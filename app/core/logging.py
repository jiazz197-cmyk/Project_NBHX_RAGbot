"""logging.dictConfig 与 app.* 命名 logger。"""
import logging
import logging.config
import logging.handlers
import sys
from pathlib import Path
from typing import Any, Dict

from app.core.config import settings


LOG_LEVEL = settings.LOG_LEVEL.upper()
BASE_DIR = Path(__file__).resolve().parents[2]
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


CATEGORY_FILES: Dict[str, str] = {
    "app": "app.log",
    "quotation": "quotation.log",
    "task": "task.log",
    "security": "security.log",
    "database": "database.log",
    "requests": "requests.log",
    "diag": "diag.log",
    "websocket": "websocket.log",
    "cache": "cache.log",
    "closing_form": "closing_form.log",
    "document_processing": "document_processing.log",
    "file_manager": "file_manager.log",
    "context_compression": "context_compression.log",
    "rag": "rag.log",
    "image2url": "image2url.log",
    "storage": "storage.log",
    "monitoring": "monitoring.log",
}

# key = logger 名去掉 "app." 前缀；value = category
# 子 logger（含点号）会自动冒泡到已路由的父级，无需单独登记。
LOGGER_ROUTES: Dict[str, str] = {
    "quotation": "quotation",
    "quotation_generation": "quotation",
    "quotation_dispatcher": "quotation",
    "u8_grouping": "quotation",
    "task_manager": "task",
    "task_observers": "task",
    "task_owner_registry": "task",
    "executor": "task",
    "executor_task_query": "task",
    "auth": "security",
    "security": "security",
    "database": "database",
    "diag": "diag",
    "requests": "requests",
    "websocket_notifier": "websocket",
    "websocket_task_manager": "websocket",
    "cache": "cache",
    "closing_form": "closing_form",
    "document_processing": "document_processing",
    "file_manager": "file_manager",
    "context_compression": "context_compression",
    "ragsystem": "rag",
    "image2url": "image2url",
    "storage": "storage",
    "monitoring": "monitoring",
}


def _file_handler(filename: str) -> Dict[str, Any]:
    """RotatingFileHandler so long-running processes do not fill the disk.

    Rotates at LOG_MAX_BYTES (default 10MB) keeping LOG_BACKUP_COUNT (default 5)
    backup copies. Previously this used a plain append-only FileHandler which grew
    without bound.
    """
    target = LOG_DIR / filename
    return {
        "formatter": "plain",
        "class": "logging.handlers.RotatingFileHandler",
        "filename": str(target),
        "mode": "a",
        "encoding": "utf-8",
        "maxBytes": int(getattr(settings, "LOG_MAX_BYTES", 10 * 1024 * 1024)),
        "backupCount": int(getattr(settings, "LOG_BACKUP_COUNT", 5)),
    }


def _build_logging_config() -> Dict[str, Any]:
    """构建 logging.dictConfig 所需的配置结构。"""
    config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "()": "uvicorn.logging.DefaultFormatter",
                "fmt": "%(levelprefix)s %(asctime)s | %(name)s | %(message)s",
                "use_colors": True,
            },
            "access": {
                "()": "uvicorn.logging.AccessFormatter",
                "fmt": '%(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
                "use_colors": True,
            },
            "plain": {
                "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            },
        },
        "handlers": {
            "default": {
                "formatter": "default",
                "class": "logging.StreamHandler",
                "stream": sys.stderr,
            },
            "access": {
                "formatter": "access",
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
            },
            "plain": {
                "formatter": "plain",
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
            },
        },
        "loggers": {
            "app": {
                "handlers": ["default", "app_file"],
                "level": LOG_LEVEL,
                "propagate": False,
            },
            "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.error": {"handlers": ["default"], "level": "INFO", "propagate": False},
            "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
            "pdfminer": {"handlers": ["default"], "level": "ERROR", "propagate": False},
            "pdfminer.pdffont": {"handlers": ["default"], "level": "ERROR", "propagate": False},
            "PIL": {"handlers": ["default"], "level": "ERROR", "propagate": False},
            "matplotlib": {"handlers": ["default"], "level": "ERROR", "propagate": False},
        },
    }

    for category, filename in CATEGORY_FILES.items():
        config["handlers"][f"{category}_file"] = _file_handler(filename)

    for name, category in LOGGER_ROUTES.items():
        config["loggers"][f"app.{name}"] = {
            "handlers": [f"{category}_file"],
            "level": LOG_LEVEL,
            "propagate": False,
        }

    return config


def setup_logging():
    """初始化全局日志配置。"""
    logging.config.dictConfig(_build_logging_config())


def get_logger(name: str) -> logging.Logger:
    """按照 app.xxx 规则获取命名 logger。"""
    return logging.getLogger(f"app.{name}")


request_logger = logging.getLogger("app.requests")
security_logger = logging.getLogger("app.security")
