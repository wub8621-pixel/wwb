"""日志：按天切分的 logger 文件 + 每次运行的 JSON 报告。"""

from __future__ import annotations

import json
import logging
import logging.handlers
from datetime import datetime
from pathlib import Path

from config.loader import project_path

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def setup_logging(cfg) -> tuple[logging.Logger, Path]:
    log_dir = project_path(cfg.get("runtime.log_dir"))
    log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("ebook")
    logger.setLevel(getattr(logging, cfg.get("runtime.log_level")))
    logger.propagate = False

    # 重复调用时替换旧 handler，保证每次都按当前配置写
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)

    handler = logging.handlers.TimedRotatingFileHandler(
        log_dir / "ebook.log",
        when="midnight",
        backupCount=cfg.get("runtime.log_retention_days"),
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    logger.addHandler(handler)
    return logger, log_dir


def write_report(log_dir: Path, payload: dict) -> Path:
    reports = log_dir / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"run-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
