"""issue #17：chunk 内容指纹（domain 纯逻辑）单测。"""

from __future__ import annotations

import inspect

import pytest

from app.domain.knowledge.chunk_identity import (
    FINGERPRINT_PREFIX,
    content_fingerprint,
    fingerprint_digest,
)


def test_fingerprint_matches_known_md5_values():
    """与 PG ``md5(text)`` 对账用的常量必须逐字节一致（实测过这三个值）。"""
    assert content_fingerprint("abc") == "md5:v1:900150983cd24fb0d6963f7d28e17f72"
    assert content_fingerprint("") == "md5:v1:d41d8cd98f00b204e9800998ecf8427e"
    assert (
        content_fingerprint("绩效考核制度")
        == "md5:v1:c4f4e548f3500c27cf772df2102cb18b"
    )


def test_fingerprint_is_utf8_encoded_and_prefixed():
    fingerprint = content_fingerprint("宁波华翔 NBHX")
    assert fingerprint.startswith(FINGERPRINT_PREFIX)
    assert fingerprint_digest(fingerprint) == fingerprint[len(FINGERPRINT_PREFIX) :]


def test_fingerprint_is_stable_and_content_sensitive():
    assert content_fingerprint("同一段文字") == content_fingerprint("同一段文字")
    assert content_fingerprint("同一段文字") != content_fingerprint("同一段文字。")
    # 空白差异在 v1 里算「不同内容」（不做归一化，见模块 docstring）
    assert content_fingerprint("a b") != content_fingerprint("a  b")


def test_none_text_is_treated_as_empty():
    assert content_fingerprint(None) == content_fingerprint("")


@pytest.mark.parametrize(
    "value",
    [
        None,
        123,
        "",
        "900150983cd24fb0d6963f7d28e17f72",  # 缺前缀
        "sha1:v1:900150983cd24fb0d6963f7d28e17f72",  # 别的算法
        "md5:v2:900150983cd24fb0d6963f7d28e17f72",  # 版本不符
        "md5:v1:900150983cd24fb0d6963f7d28e17f7",  # 短一位
        "md5:v1:900150983CD24FB0D6963F7D28E17F72",  # 大写
        "md5:v1:zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",  # 非 hex
    ],
)
def test_fingerprint_digest_rejects_malformed_values(value):
    assert fingerprint_digest(value) is None


def test_domain_module_has_no_outer_layer_imports():
    """domain 层禁 import 外层（守卫规则 3）：本模块只许 stdlib。"""
    source = inspect.getsource(
        __import__(
            "app.domain.knowledge.chunk_identity", fromlist=["chunk_identity"]
        )
    )
    assert "app.core" not in source
    assert "app.adapters" not in source
