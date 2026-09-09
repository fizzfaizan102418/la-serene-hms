from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


DEFAULT_LOG_DIR = Path(__file__).resolve().parents[3] / "logs"


def configure_production_logging(log_dir: Path | None = None) -> Path:
    directory = log_dir or DEFAULT_LOG_DIR
    directory.mkdir(parents=True, exist_ok=True)
    log_file = directory / "api.log"

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    )
    handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=10 * 1024 * 1024,
        backupCount=14,
        encoding="utf-8",
    )
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(item, logging.handlers.RotatingFileHandler) and Path(item.baseFilename) == log_file for item in root.handlers):
        root.addHandler(handler)

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine"):
        logging.getLogger(logger_name).setLevel(logging.INFO)

    return log_file
