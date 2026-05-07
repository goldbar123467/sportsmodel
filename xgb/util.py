"""Shared small utilities for SportsBotv2 XGBoost scripts."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
LOG_DIR = SCRIPT_DIR / "logs"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def validate_date(date_str: str | None, label: str = "date") -> str | None:
    if date_str is None:
        return None
    if not DATE_RE.match(str(date_str)):
        raise ValueError(f"Invalid {label}: expected YYYY-MM-DD, got {date_str!r}")
    try:
        parsed = datetime.strptime(str(date_str), "%Y-%m-%d")
    except ValueError as exc:
        raise ValueError(f"Invalid {label}: expected YYYY-MM-DD, got {date_str!r}") from exc
    return parsed.strftime("%Y-%m-%d")


class JsonLineHandler(logging.Handler):
    def __init__(self, path: Path):
        super().__init__()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, record):
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        with self.path.open("a") as f:
            f.write(json.dumps(payload) + "\n")


def setup_file_logger(name: str, date_str: str | None = None) -> logging.Logger:
    date_str = validate_date(date_str or datetime.now().strftime("%Y-%m-%d"))
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    path = LOG_DIR / f"{date_str}.jsonl"
    if not any(isinstance(h, JsonLineHandler) and h.path == path for h in logger.handlers):
        logger.addHandler(JsonLineHandler(path))
    return logger
