"""ragchain 统一错误类型（.dsh/ragchain-interfaces.md §2）。"""

from __future__ import annotations

from typing import Any


class ChainError(Exception):
    """核心链统一异常基类。"""

    code: str = "INTERNAL_ERROR"
    message: str = "内部错误"
    status: int = 500

    def __init__(self, code: str, message: str, status: int = 500) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)

    def to_error_body(self) -> dict[str, Any]:
        """转成前端契约的统一 JSON 错误体。"""
        return {"message": self.message, "error_code": self.code}


class AuthError(ChainError):
    """JWT 缺失/无效/过期。"""

    def __init__(
        self,
        message: str = "无效或已过期的身份凭证",
        code: str = "AUTHENTICATION_ERROR",
        status: int = 401,
    ) -> None:
        super().__init__(code, message, status)


class ValidationError(ChainError):
    """业务参数校验失败。"""

    def __init__(
        self,
        message: str = "参数校验失败",
        code: str = "VALIDATION_ERROR",
        status: int = 422,
    ) -> None:
        super().__init__(code, message, status)


class BackendError(ChainError):
    """外部后端（主应用/检索/重排/搜索）错误或不可用。

    兼容冻结契约的 ``BackendError(code, message, status)`` 调用方式。
    """

    def __init__(
        self,
        code: str = "BACKEND_ERROR",
        message: str = "后端服务请求失败",
        status: int = 500,
    ) -> None:
        super().__init__(code, message, status)


class LLMUnavailableError(ChainError):
    """主/辅 LLM 不可用。"""

    def __init__(
        self,
        message: str = "模型服务不可用",
        code: str = "LLM_UNAVAILABLE",
        status: int = 503,
    ) -> None:
        super().__init__(code, message, status)
