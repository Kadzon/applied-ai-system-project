"""Logging setup: everything goes to logs/pawpal_ai.log, warnings and errors also to the console."""
from __future__ import annotations

import logging
from pathlib import Path

LOG_FILE = Path(__file__).parent / "logs" / "pawpal_ai.log"


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(getattr(h, "_pawpal", False) for h in root.handlers):
        return  # already configured
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")

    LOG_FILE.parent.mkdir(exist_ok=True)
    file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(fmt)
    file_handler._pawpal = True  # type: ignore[attr-defined]

    console = logging.StreamHandler()
    console.setLevel(logging.WARNING)
    console.setFormatter(fmt)
    console._pawpal = True  # type: ignore[attr-defined]

    root.addHandler(file_handler)
    root.addHandler(console)
