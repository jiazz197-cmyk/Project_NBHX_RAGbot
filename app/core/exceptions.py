"""统一结构的业务异常类型。"""
from __future__ import annotations

from typing import Any, Dict, Optional


class APIException(Exception):
    """API 异常基类，提供统一的错误结构。"""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        error_code: str = "API_ERROR",
        details: Optional[Dict[str, Any]] = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.error_code = error_code
        self.details = details

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "message": self.message,
            "error_code": self.error_code,
        }
        if self.details:
            payload["details"] = self.details
        return payload


class ValidationError(APIException):
    """参数或数据校验失败。"""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, status_code=422, error_code="VALIDATION_ERROR", details=details)


class NotFoundError(APIException):
    """资源不存在。"""

    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, status_code=404, error_code="NOT_FOUND")


class PermissionDeniedError(APIException):
    """无权访问资源或执行操作。"""

    def __init__(self, message: str = "Permission denied"):
        super().__init__(message, status_code=403, error_code="PERMISSION_DENIED")


class ExternalServiceError(APIException):
    """外部服务调用失败。"""

    def __init__(self, service_name: str, message: str = "External service error"):
        super().__init__(
            message=f"{service_name}: {message}",
            status_code=502,
            error_code="EXTERNAL_SERVICE_ERROR",
            details={"service": service_name},
        )


class AuthenticationError(APIException):
    """身份认证失败（用户名、密码错误等）。"""

    def __init__(self, message: str = "Authentication failed"):
        super().__init__(message, status_code=401, error_code="AUTHENTICATION_ERROR")
