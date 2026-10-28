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


FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture_json(name: str):
    import json

    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None, headers=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else ("" if payload is None else str(payload))
        self.headers = headers or {}

    def json(self):
        if self._payload is None:
            raise ValueError("no JSON")
        return self._payload


class FakeSession:
    """Stands in for requests.Session: returns queued responses (or raises queued exceptions)."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def _next(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected request {method} {url}")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, **kwargs):
        return self._next("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._next("POST", url, **kwargs)
