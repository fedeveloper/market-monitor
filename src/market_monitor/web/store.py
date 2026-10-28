"""Validated, atomic saving of the three YAML files edited from the web interface.

Each save is validated with the same code as ``check-config`` (the candidate file
next to the other two current files, in a temporary directory), the previous file
is copied to ``<name>.yaml.bak`` and the new one replaces it atomically. The
monitor picks it up on its next cycle (hot reload). Comments are not preserved.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from market_monitor.config import CONFIG_FILES, ConfigError, load_config

EDITABLE = {Path(name).stem: name for name in CONFIG_FILES}  # config, watchlist, alerts

HEADER = (
    "# Salvato dall'interfaccia web il {when}.\n"
    "# I commenti non vengono conservati: per la spiegazione dei campi vedi config/{example}.\n"
)


class ConflictError(Exception):
    """The file changed on disk after the interface read it."""


class UnknownFileError(Exception):
    pass


@dataclass
class SaveResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    version: str | None = None


def file_version(path: Path) -> str | None:
    """Opaque version of a file (mtime and size), None if it does not exist."""
    try:
        st = path.stat()
    except FileNotFoundError:
        return None
    return f"{st.st_mtime_ns}-{st.st_size}"


def dump_yaml(data: Any, example: str, now: datetime) -> str:
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False, width=100)
    return HEADER.format(when=now.strftime("%d/%m/%Y %H:%M"), example=example) + body


def write_text_atomic(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


class ConfigStore:
    def __init__(self, config_dir: Path, base_dir: Path | None = None):
        self.config_dir = Path(config_dir)
        self.base_dir = Path(base_dir) if base_dir is not None else self.config_dir.resolve().parent
        self._lock = threading.Lock()

    def path(self, name: str) -> Path:
        if name not in EDITABLE:
            raise UnknownFileError(name)
        return self.config_dir / EDITABLE[name]

    def read(self, name: str) -> tuple[Any, str | None, str | None]:
        """(data, version, error). ``error`` is set when the file is missing or not valid YAML."""
        path = self.path(name)
        version = file_version(path)
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None, None, f"{path.name}: file mancante"
        except yaml.YAMLError as exc:
            return None, version, f"{path.name}: YAML non valido: {exc}"
        return data, version, None

    def validate(self, name: str, data: Any) -> SaveResult:
        """Validate ``data`` as the new content of ``name`` together with the other current files."""
        target = self.path(name)
        with tempfile.TemporaryDirectory(prefix="mm-validate-") as tmp:
            tmp_dir = Path(tmp)
            for other in EDITABLE.values():
                source = self.config_dir / other
                if other != target.name and source.exists():
                    shutil.copy(source, tmp_dir / other)
            (tmp_dir / target.name).write_text(dump_yaml(data, "x", datetime.now()), encoding="utf-8")
            try:
                config = load_config(tmp_dir, self.base_dir)
            except ConfigError as exc:
                return SaveResult(False, errors=exc.errors)
        return SaveResult(True, warnings=list(config.warnings))

    def save(self, name: str, data: Any, expected_version: str | None, now: datetime | None = None) -> SaveResult:
        path = self.path(name)
        with self._lock:
            if file_version(path) != expected_version:
                raise ConflictError(name)
            result = self.validate(name, data)
            if not result.ok:
                return result
            if path.exists():
                shutil.copy2(path, path.with_name(path.name + ".bak"))
            example = path.name.replace(".yaml", ".example.yaml")
            write_text_atomic(path, dump_yaml(data, example, now or datetime.now()))
            result.version = file_version(path)
            return result
