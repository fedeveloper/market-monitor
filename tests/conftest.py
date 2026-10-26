import shutil
from pathlib import Path

import pytest
import yaml

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config"


class ConfigDir:
    """A temporary config/ directory initialised from the *.example.yaml files."""

    def __init__(self, path: Path):
        self.path = path
        path.mkdir()
        for name in ("config", "watchlist", "alerts"):
            shutil.copy(REPO_CONFIG / f"{name}.example.yaml", path / f"{name}.yaml")

    def load(self, name: str):
        return yaml.safe_load((self.path / f"{name}.yaml").read_text(encoding="utf-8"))

    def save(self, name: str, data) -> None:
        (self.path / f"{name}.yaml").write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def write_text(self, name: str, text: str) -> None:
        (self.path / f"{name}.yaml").write_text(text, encoding="utf-8")


@pytest.fixture
def config_dir(tmp_path) -> ConfigDir:
    return ConfigDir(tmp_path / "config")
