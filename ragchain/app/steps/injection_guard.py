"""防注入审查步骤：sub_llm 判定为主，不可达时正则兜底。"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field

from ..clients.llm_client import LLMError
from ..prompts import render_guard_prompt

logger = logging.getLogger(__name__)

# 正则兜底：仅覆盖最明显的注入/越权表达
_SUSPICIOUS_PATTERNS = [
    r"忽略(以上|之前|前面|所有)?(的)?(指令|规则|提示|要求)",
    r"(泄露|告诉我|输出|打印|复述|展示).{0,12}(系统提示|system\s*prompt|内部指令|开发者消息)",
    r"(忽略|绕过|关闭|跳过).{0,8}(安全|限制|审查|规则)",
    r"(开发者模式|开发者权限|DAN|越狱|无限制模式|jailbreak)",
    r"(伪造|篡改|删除).{0,8}(账|凭证|发票|报表|记录|数据)",
    r"(帮助|教我|如何).{0,8}(逃税|避税|规避审计|做假账)",
    r"(其他用户|别人|他人).{0,8}(数据|信息|密码|token|记录)",
]
_SUSPICIOUS_RE = re.compile("|".join(_SUSPICIOUS_PATTERNS), re.IGNORECASE)


class GuardResult(BaseModel):
    is_malicious: bool = False
    reason: str = ""


def regex_hit(query: str) -> bool:
    return bool(_SUSPICIOUS_RE.search(query or ""))


async def check_injection(query: str, deps) -> GuardResult:
    """审查用户输入。

    - sub_llm 判定成功：直接返回其结论；
    - sub_llm 不可达 / 结构化返回 None：正则兜底；非严格模式放行，
      RAGCHAIN_GUARD_STRICT=True 且命中则拒绝。
    """
    try:
        system, user = render_guard_prompt(query)
        result = await deps.llm.structured(
            system=system,
            user=user,
            schema_cls=GuardResult,
        )
        if result is None:
            # 结构化调用静默返回 None（如 method=function_calling 且网关忽略
            # tool_choice）时绝不能当「审查通过」——显式转降级路径。
            raise LLMError("结构化审查返回 None")
        if isinstance(result, GuardResult):
            return result
        return GuardResult(
            is_malicious=bool(getattr(result, "is_malicious", False)),
            reason=str(getattr(result, "reason", "") or ""),
        )
    except Exception as exc:  # noqa: BLE001 - 单点失败降级，不阻断
        settings = getattr(deps, "settings", None)
        strict = bool(getattr(settings, "RAGCHAIN_GUARD_STRICT", False))
        hit = regex_hit(query)
        logger.warning("防注入 sub_llm 不可达，正则兜底：hit=%s strict=%s err=%s", hit, strict, exc)
        if strict and hit:
            return GuardResult(is_malicious=True, reason="安全审查服务不可用且输入命中高危特征，严格模式拒绝")
        return GuardResult(is_malicious=False, reason="安全审查服务不可用，正则兜底放行")
