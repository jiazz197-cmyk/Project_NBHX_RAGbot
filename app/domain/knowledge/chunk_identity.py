"""chunk 内容身份（纯逻辑，无 IO）——issue #17 的内容级去重判据。

「两个 chunk 是否相同」在本仓分三层，别混为一谈：

- **行身份**：``node_id`` / ``metadata['chunk_id']``——写入时随机生成（uuid4），
  只回答「是不是同一行」，对「内容是否相同」零帮助；
- **内容身份**（本模块）：对**真正落进 ``data_<collection>.text`` 列的那串字符**
  求指纹。同一内容再次入库时指纹必然相同，写入端据此跳过；
- **近似身份**：simhash / minhash / pg_trgm——容忍空白、标点、OCR 抖动，
  本期不做（见 issue #17 方案 D8）。

算法选 md5 而不是 sha256，是因为 PostgreSQL 的 ``md5(text)`` 能算出**同一个
值**（已实测：库 UTF-8，中文/空串/ASCII 三例与 Python 逐字节一致），于是
「库里已有哪些内容」不需要回填历史数据即可现算对账——见
``app/adapters/knowledge/chunk_fingerprint.py``。

指纹必须算在**清洗后**（``clean_text_for_postgres`` 去 NUL）的文本上：那才是
写进 ``text`` 列的字符串。算法或归一化规则一旦变化，必须升 ``FINGERPRINT_PREFIX``
的版本号——旧指纹与新指纹永不相等，避免把「算法不同」误判成「同一内容」。
"""

from __future__ import annotations

import hashlib
from typing import Optional

#: 指纹前缀，形如 ``md5:v1:<32 位小写 hex>``；算法/归一化变更时递增版本号。
FINGERPRINT_PREFIX = "md5:v1:"

#: md5 hex 长度（本模块只认这一种形态）
_DIGEST_LEN = 32
_HEX_CHARS = frozenset("0123456789abcdef")


def content_fingerprint(text: str) -> str:
    """返回落库文本的内容指纹（UTF-8 编码，与 PG ``md5(text)`` 逐字节一致）。

    ``None`` / 空串按空串处理（``md5('')`` 是固定值）；空块不该走到这里——
    写入端在生成 node 前已把空/纯空白块丢弃。
    """
    digest = hashlib.md5((text or "").encode("utf-8")).hexdigest()
    return f"{FINGERPRINT_PREFIX}{digest}"


def fingerprint_digest(fingerprint: object) -> Optional[str]:
    """指纹 → 裸 32 位 hex（供 SQL 与 PG ``md5(text)`` 直接比对）。

    形态非法（非字符串、前缀不符、长度不对、含非 hex 字符、大写）一律返回
    ``None``：历史/脏 metadata 宁可当作「没有指纹」，也不能拿来当判重依据。
    """
    if not isinstance(fingerprint, str) or not fingerprint.startswith(FINGERPRINT_PREFIX):
        return None
    digest = fingerprint[len(FINGERPRINT_PREFIX) :]
    if len(digest) != _DIGEST_LEN:
        return None
    if any(char not in _HEX_CHARS for char in digest):
        return None
    return digest
