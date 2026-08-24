"""Structural fence for the whole unit suite (audit 2026-08-24, D-5/C-5).

Runs before ANY test module (and so before any smartbar import freezes a
path constant). Two real incidents motivated it: an unfenced test once
published a junk presence beacon to the REAL origin and blanked the live
device badges, and the warmup run_once tests wrote 65 fixture lines into
the user's real ~/.cache/ai-smartbar/warmup.log.

- SMARTBAR_PRESENCE=off: no test can write to the real remote by accident.
  Tests that exercise enabled() pop or overwrite the variable themselves.
- SMARTBAR_CACHE_DIR / SMARTBAR_CONFIG_DIR: a per-run temp sandbox, so
  module-level constants (LOG_FILE, STATE_FILE, …) frozen at import time
  point somewhere disposable. Explicitly exported values win — a developer
  aiming a test run at a specific dir keeps that.
"""
from __future__ import annotations

import atexit
import os
import shutil
import tempfile

os.environ.setdefault("SMARTBAR_PRESENCE", "off")

_SANDBOX = None
if not os.environ.get("SMARTBAR_CACHE_DIR") \
        or not os.environ.get("SMARTBAR_CONFIG_DIR"):
    _SANDBOX = tempfile.mkdtemp(prefix="smartbar-tests-")
    os.environ.setdefault("SMARTBAR_CACHE_DIR",
                          os.path.join(_SANDBOX, "cache"))
    os.environ.setdefault("SMARTBAR_CONFIG_DIR",
                          os.path.join(_SANDBOX, "config"))
    atexit.register(shutil.rmtree, _SANDBOX, ignore_errors=True)
