import subprocess
import sys

import pytest

from market_monitor.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "market_monitor" in capsys.readouterr().out


def test_module_entry_point():
    result = subprocess.run(
        [sys.executable, "-m", "market_monitor", "--version"], capture_output=True, text=True
    )
    assert result.returncode == 0
