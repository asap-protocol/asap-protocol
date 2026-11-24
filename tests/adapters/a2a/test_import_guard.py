"""Import guard: importing asap.adapters.a2a never imports a2a-sdk."""

from __future__ import annotations

import subprocess
import sys

# A fresh interpreter: the interop tests import a2a-sdk into the pytest worker.
_PROBE = """
import importlib.util
import sys

if importlib.util.find_spec("a2a") is None:
    raise SystemExit("a2a-sdk is not installed, so this guard would prove nothing")
import asap.adapters.a2a  # noqa: F401
leaked = sorted(name for name in sys.modules if name == "a2a" or name.startswith("a2a."))
raise SystemExit(f"asap.adapters.a2a imported {leaked}" if leaked else 0)
"""


def test_importing_the_adapter_does_not_import_a2a_sdk() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, check=False, timeout=120
    )

    assert result.returncode == 0, result.stderr
