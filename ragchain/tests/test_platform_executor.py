"""PythonExecutor 沙箱测试：正常执行 / 超时 / 脱敏 / 截断 / 代码超长。"""

from __future__ import annotations

import os

from app.tools.python_executor import PythonExecutor, ToolExecutionResult


async def test_normal_execution_output_and_duration():
    result = await PythonExecutor(timeout_sec=10, code_max_chars=8000, output_max_chars=4000).execute(
        "print('你好，计算结果:', 2 + 3)"
    )
    assert isinstance(result, ToolExecutionResult)
    assert result.ok is True
    assert "你好，计算结果: 5" in result.output
    assert result.error == ""
    assert result.duration_sec >= 0


async def test_nonzero_exit_is_error_with_merged_stderr():
    result = await PythonExecutor(10, 8000, 4000).execute("raise SystemExit('bad')")
    assert result.ok is False
    assert "bad" in result.output
    assert result.error


async def test_timeout_kills_process_and_returns_failure():
    executor = PythonExecutor(timeout_sec=0.4, code_max_chars=8000, output_max_chars=4000)
    result = await executor.execute("import time; time.sleep(10); print('late')")
    assert result.ok is False
    assert "超时" in result.error
    assert result.duration_sec < 5


async def test_env_is_sanitized(monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "super-secret-value")
    monkeypatch.setenv("MAIN_LLM_API_KEY", "llm-key-value")
    monkeypatch.setenv("LC_ALL", "C")
    result = await PythonExecutor(10, 8000, 4000).execute(
        "import os; print('SECRET_KEY' in os.environ);"
        "print(os.environ.get('MAIN_LLM_API_KEY'));"
        "print(os.environ.get('LC_ALL'))"
    )
    assert result.ok is True
    assert "super-secret-value" not in result.output
    assert "llm-key-value" not in result.output
    lines = result.output.strip().splitlines()
    assert lines[0] == "False"
    assert lines[1] == "None"
    assert lines[2] == "C"


async def test_cwd_is_temp_dir_and_cleaned_up():
    result = await PythonExecutor(10, 8000, 4000).execute("import os; print(os.getcwd())")
    assert result.ok is True
    cwd = result.output.strip()
    assert cwd.startswith("/tmp/ragchain_exec_")
    assert not os.path.exists(cwd)


async def test_output_is_truncated_to_limit():
    result = await PythonExecutor(10, 8000, output_max_chars=10).execute(
        "print('abcdefghijklmnopqrstuvwxyz')"
    )
    assert result.ok is True
    assert result.output == "abcdefghij"


async def test_overlong_code_rejected_without_execution():
    result = await PythonExecutor(10, code_max_chars=20, output_max_chars=4000).execute(
        "x = 1\n" * 10
    )
    assert result.ok is False
    assert "长度" in result.error
    assert result.output == ""


async def test_isolated_mode_and_pandas_available():
    result = await PythonExecutor(30, 8000, 4000).execute(
        "import sys, pandas, numpy;"
        "print(int(getattr(sys.flags, 'isolated', 0)));"
        "print(pandas.__version__, numpy.__version__)"
    )
    assert result.ok is True
    lines = result.output.strip().splitlines()
    assert lines[0] == "1"
    assert len(lines[1].split()) == 2
