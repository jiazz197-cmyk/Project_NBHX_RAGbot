"""JWT / schemas / SSE 基础契约测试。"""

from __future__ import annotations

import time

import jwt
import pytest
from pydantic import ValidationError as PydanticValidationError

from app.errors import AuthError
from app.schemas import (
    SEARCH_MODE_BOTH,
    SEARCH_MODE_LOCAL,
    SEARCH_MODE_WEB,
    VALID_SEARCH_MODES,
    ChatMessageRequest,
    ErrorResponse,
)
from app.security import verify_bearer
from app.sse import sse_frame


def test_valid_bearer(make_token):
    token = make_token(sub="user-1", role="user")
    ctx = verify_bearer(f"Bearer {token}")
    assert ctx.user_id == "user-1"
    assert ctx.token == token
    assert ctx.claims["role"] == "user"
    assert ctx.claims["sub"] == "user-1"


def test_valid_lowercase_bearer(make_token):
    token = make_token()
    ctx = verify_bearer(f"bearer {token}")
    assert ctx.user_id


@pytest.mark.parametrize(
    "header",
    [None, "", "Token abc", "Bearer", "Bearer   ", "abc.def.ghi"],
)
def test_invalid_authorization_header(header):
    with pytest.raises(AuthError) as excinfo:
        verify_bearer(header)
    assert excinfo.value.status == 401
    assert excinfo.value.code == "AUTHENTICATION_ERROR"


def test_expired_token(make_token):
    token = make_token(expires_in=-10)
    with pytest.raises(AuthError):
        verify_bearer(f"Bearer {token}")


def test_wrong_signature(make_token):
    token = make_token(secret="another-secret")
    with pytest.raises(AuthError):
        verify_bearer(f"Bearer {token}")


def test_missing_sub(make_token):
    token = make_token(sub=None, expires_in=600)
    with pytest.raises(AuthError):
        verify_bearer(f"Bearer {token}")


def test_missing_exp(make_token):
    token = make_token(expires_in=None)
    with pytest.raises(AuthError):
        verify_bearer(f"Bearer {token}")


def test_already_expired_jwt_is_rejected(jwt_secret):
    token = jwt.encode({"sub": "u", "exp": int(time.time()) - 5}, jwt_secret, algorithm="HS256")
    with pytest.raises(AuthError):
        verify_bearer(f"Bearer {token}")


def test_sse_frame_format_and_chinese():
    frame = sse_frame("message", {"event": "message", "content": "去年售后费用"})
    assert frame == (
        'event: message\ndata: {"event": "message", "content": "去年售后费用"}\n\n'
    )
    assert "\\u" not in frame
    assert frame.endswith("\n\n")


def test_search_mode_constants():
    assert SEARCH_MODE_WEB == "联网搜索"
    assert SEARCH_MODE_LOCAL == "本地检索"
    assert SEARCH_MODE_BOTH == "本地&网络"
    assert VALID_SEARCH_MODES == {"联网搜索", "本地检索", "本地&网络"}


def test_chat_message_request_defaults_and_validation():
    req = ChatMessageRequest(query="你好")
    assert req.search_mode == SEARCH_MODE_BOTH
    assert req.conversation_id is None
    assert req.inputs == {}
    assert req.response_mode == "streaming"

    with pytest.raises(PydanticValidationError):
        ChatMessageRequest(query="")

    req2 = ChatMessageRequest(
        query="去年售后费用",
        conversation_id="c-1",
        search_mode=SEARCH_MODE_LOCAL,
        inputs={"background": ""},
    )
    assert req2.query == "去年售后费用"
    assert req2.conversation_id == "c-1"


def test_error_response_shape():
    body = ErrorResponse(message="无权访问", error_code="PERMISSION_DENIED")
    assert body.model_dump() == {"message": "无权访问", "error_code": "PERMISSION_DENIED"}
