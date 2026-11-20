r"""Isolate the test suite from the live tracker data directory.

The live app reads/writes ``%LOCALAPPDATA%\Vintendo COD Tracker``
(``engine.DATA``, set from ``VINTENDO_DATA_DIR`` at import).
Tests must never touch it: point the env var at a temp dir here,
before any test module imports ``engine``/``app``.
"""
import os
import tempfile


def _isolated_data_dir():
    existing = os.environ.get("VINTENDO_DATA_DIR", "")
    if existing and os.environ.get("VINTENDO_TEST_ISOLATED") == "1":
        return existing
    folder = os.path.join(tempfile.gettempdir(), "vintendo-test-data-isolated")
    os.makedirs(folder, exist_ok=True)
    os.environ["VINTENDO_DATA_DIR"] = folder
    os.environ["VINTENDO_TEST_ISOLATED"] = "1"
    return folder


_isolated_data_dir()
