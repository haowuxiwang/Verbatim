from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def get_logger() -> logging.Logger:
    logger = logging.getLogger("verbatim.app")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        log_dir / "verbatim_app.log",
        maxBytes=2_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def log_event(code: str, message: str, *, level: str = "info", **fields) -> None:
    logger = get_logger()
    payload = {"code": code, "message": message, **fields}
    line = json.dumps(payload, ensure_ascii=False)
    lvl = (level or "info").lower()
    if lvl == "error":
        logger.error(line)
    elif lvl == "warning":
        logger.warning(line)
    else:
        logger.info(line)
