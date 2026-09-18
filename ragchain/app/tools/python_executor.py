"""沙箱 Python 计算工具（.dsh/ragchain-interfaces.md §12）。

隔离与安全边界：
- ``sys.executable -I``（isolated mode）；
- 仅保留 PATH/LANG/LC_*/TZ/SYSTEMROOT 环境变量；
- cwd 为 /tmp 下 mkdtemp 的临时目录；
- 墙钟超时 kill 进程组；
- stdout/stderr 合并后按上限截断；
- 代码长度上限；临时文件 finally 删除。
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import signal
import sys
import tempfile
import time
from dataclasses import dataclass

_ENV_WHITELIST_EXACT = frozenset({"PATH", "LANG", "TZ", "SYSTEMROOT"})


@dataclass
class ToolExecutionResult:
    ok: bool
    output: str = ""
    error: str = ""
    duration_sec: float = 0.0


def _sanitized_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for key, value in os.environ.items():
        if key in _ENV_WHITELIST_EXACT or key.startswith("LC_"):
            env[key] = value
    # 保证绝对路径解释器之外，脚本内部最常见的 PATH 查询至少有兜底。
    env.setdefault("PATH", os.defpath)
    return env


def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    with contextlib.suppress(Exception):
        if os.name == "posix":
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        else:  # pragma: no cover - 容器只跑 Linux
            proc.kill()


class PythonExecutor:
    def __init__(self, timeout_sec: float, code_max_chars: int, output_max_chars: int):
        self.timeout_sec = float(timeout_sec)
        self.code_max_chars = int(code_max_chars)
        self.output_max_chars = int(output_max_chars)

    async def execute(self, code: str) -> ToolExecutionResult:
        start = time.monotonic()
        code = code if isinstance(code, str) else str(code)

        if self.code_max_chars >= 0 and len(code) > self.code_max_chars:
            return ToolExecutionResult(
                ok=False,
                output="",
                error=f"代码长度 {len(code)} 超过上限 {self.code_max_chars}",
                duration_sec=time.monotonic() - start,
            )

        tmpdir = tempfile.mkdtemp(prefix="ragchain_exec_", dir="/tmp")
        script_path = os.path.join(tmpdir, "script.py")
        proc: asyncio.subprocess.Process | None = None
        try:
            with open(script_path, "w", encoding="utf-8") as fh:
                fh.write(code)

            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                script_path,
                cwd=tmpdir,
                env=_sanitized_env(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=(os.name == "posix"),
            )

            communicate_task = asyncio.ensure_future(proc.communicate())
            try:
                done, pending = await asyncio.wait(
                    {communicate_task}, timeout=self.timeout_sec
                )
            except asyncio.CancelledError:
                _kill_process_tree(proc)
                communicate_task.cancel()
                with contextlib.suppress(BaseException):
                    await communicate_task
                raise

            if communicate_task in pending:
                _kill_process_tree(proc)
                raw = b""
                with contextlib.suppress(BaseException):
                    raw, _ = await asyncio.wait_for(communicate_task, timeout=5.0)
                text = raw.decode("utf-8", errors="replace")
                if len(text) > self.output_max_chars:
                    text = text[: self.output_max_chars]
                return ToolExecutionResult(
                    ok=False,
                    output=text,
                    error=f"代码执行超时（>{self.timeout_sec:g}s）",
                    duration_sec=time.monotonic() - start,
                )

            stdout, _ = communicate_task.result()
            text = (stdout or b"").decode("utf-8", errors="replace")
            if len(text) > self.output_max_chars:
                text = text[: self.output_max_chars]

            if proc.returncode == 0:
                return ToolExecutionResult(
                    ok=True,
                    output=text,
                    error="",
                    duration_sec=time.monotonic() - start,
                )
            return ToolExecutionResult(
                ok=False,
                output=text,
                error=text.strip() or f"进程退出码 {proc.returncode}",
                duration_sec=time.monotonic() - start,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 启动/IO 异常统一返回失败结果
            if proc is not None:
                _kill_process_tree(proc)
            return ToolExecutionResult(
                ok=False,
                output="",
                error=f"沙箱执行失败: {exc.__class__.__name__}: {exc}",
                duration_sec=time.monotonic() - start,
            )
        finally:
            if proc is not None and proc.returncode is None:
                _kill_process_tree(proc)
                with contextlib.suppress(BaseException):
                    await asyncio.wait_for(proc.wait(), timeout=5.0)
            shutil.rmtree(tmpdir, ignore_errors=True)
