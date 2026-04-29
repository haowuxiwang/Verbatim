from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler

from core.app_paths import runtime_state_dir


LOGGER_NAME = "verbatim.app"
LOG_FILENAME = "verbatim_app.log"


def _build_fallback_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.NullHandler())
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger
    try:
        logger.setLevel(logging.INFO)
        log_dir = runtime_state_dir() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_dir / LOG_FILENAME,
            maxBytes=2_000_000,
            backupCount=5,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.propagate = False
        return logger
    except Exception:
        return _build_fallback_logger()


def log_event(code: str, message: str, *, level: str = "info", **fields) -> None:
    try:
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
    except Exception:
        return
