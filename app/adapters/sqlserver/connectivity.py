"""Health check: U8/PDM SQL Server SELECT 1."""

from __future__ import annotations

from time import perf_counter
from typing import Any, Dict

from app.core.config import settings

from app.adapters.sqlserver.client import get_sql_client


def test_sqlserver_connectivity() -> Dict[str, Dict[str, Any]]:
    """
    Test U8/PDM SQLServer connectivity with `SELECT 1`.

    Returns:
        {
            "u8": {"ok": bool, "latency_ms": float | None, "error": str | None},
            "pdm": {"ok": bool, "latency_ms": float | None, "error": str | None},
        }
    """

    checks = {
        "u8": {
            "backend": "pymssql",
            "server": settings.U8_SQLSERVER_HOST,
            "port": settings.U8_SQLSERVER_PORT,
            "database": settings.U8_SQLSERVER_DATABASE,
            "username": settings.U8_SQLSERVER_USER,
            "password": settings.U8_SQLSERVER_PASSWORD,
            "encrypt": settings.U8_SQLSERVER_ENCRYPT,
        },
        "pdm": {
            "backend": "pymssql",
            "server": settings.PDM_SQLSERVER_HOST,
            "port": settings.PDM_SQLSERVER_PORT,
            "database": settings.PDM_SQLSERVER_DATABASE,
            "username": settings.PDM_SQLSERVER_USER,
            "password": settings.PDM_SQLSERVER_PASSWORD,
            "encrypt": settings.PDM_SQLSERVER_ENCRYPT,
        },
    }

    results: Dict[str, Dict[str, Any]] = {}
    for name, conf in checks.items():
        start = perf_counter()
        try:
            client = get_sql_client(conf)
            client.query("SELECT 1 AS ok")
            latency_ms = (perf_counter() - start) * 1000
            results[name] = {
                "ok": True,
                "latency_ms": round(latency_ms, 2),
                "error": None,
            }
        except Exception as exc:
            results[name] = {
                "ok": False,
                "latency_ms": None,
                "error": str(exc),
            }

    return results
