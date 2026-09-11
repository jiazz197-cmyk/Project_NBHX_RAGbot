"""
关键词归一化模块。

负责将结构化 keywords 请求体转换为可执行查询关键词组。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence

from app.domain.quotation.keyword_mapping import get_attr_whitelist


YES_VALUES = {"yes", "true", "y", "1", "是"}
NO_VALUES = {"no", "false", "n", "0", "否", "无"}

SEMANTIC_FIELD_TOKENS: Dict[str, str] = {
    "detergent": "detergent",
    "fbspring": "fb spring",
    "fb弹簧": "fb spring",
    "wbspring": "wb spring",
    "wb弹簧": "wb spring",
    "ccbaffles": "cc baffles",
    "cc挡板": "cc baffles",
    "挡板": "cc baffles",
    "cfbaffles": "cf baffles",
    "cf挡板": "cf baffles",
    "memoryspring": "memory spring",
    "记忆弹簧": "memory spring",
}

LFP_LIP_KEYS = {"lfplip", "lfp唇口", "lfp唇"}
CF_L_BRACKET_KEYS = {"cflshapedbracket", "cfl型支架", "l型支架"}
REMARKS_KEYS = {"remarks", "othersremarks", "others/remarks", "otherremarks", "备注", "其他备注"}
FEED_BUCKET_REMARKS_SECTION_RE = re.compile(
    r"feed\s*bucket\s*[,，、/]\s*weigh\s*bucket\s*[:：]\s*([^;\n\r；]*)",
    re.IGNORECASE,
)
CAPACITY_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])(\d+(?:\.\d+)?)\s*[lL]\b")
C_C_VALUE_RE = re.compile(
    r"(?i)(?:c\s*-\s*c\s*=?\s*)?(\d+(?:\.\d+)?)"
)


def _normalize_key_name(key_text: str) -> str:
    return re.sub(r"[\s_\-]", "", key_text.lower())


def _to_tristate(value: Any) -> str:
    """将输入值归一化为 yes/no/other 三态。"""
    if isinstance(value, bool):
        return "yes" if value else "no"

    text = str(value).strip().lower()
    if text in YES_VALUES:
        return "yes"
    if text in NO_VALUES:
        return "no"
    return "other"


def _extract_feed_bucket_remark_capacities(text: str) -> List[str]:
    """Extract only xL capacity tokens from `Feed bucket, Weigh bucket:` remarks."""
    capacities: List[str] = []
    for section_match in FEED_BUCKET_REMARKS_SECTION_RE.finditer(text):
        section = section_match.group(1)
        for capacity_match in CAPACITY_TOKEN_RE.finditer(section):
            capacities.append(f"{capacity_match.group(1)}L")
    return list(dict.fromkeys(capacities))


def _extract_c_c_value(text: str) -> str:
    """Extract numeric C-C value; empty/non-numeric values are not query params."""
    match = C_C_VALUE_RE.search(text)
    return match.group(1) if match else ""


def normalize_pdm_keywords(value: Any) -> List[List[str]]:
    """
    将 keywords 统一转换为多组原始关键词。

    仅支持以下输入格式:
    1) {"type": "机架", "attr": {"surface": "flat", "detergent": True}}
    2) [{"type": "机架", "attr": {"surface": "flat", "detergent": True}}]
    3) [{"type": "机架", "attr": {"surface": "flat", "detergent": True}}, {"type": "供料斗", "attr": {"LFP唇口": "No"}}]
    """

    def filter_attr_by_whitelist(item: Dict[str, Any]) -> Dict[str, Any]:
        """根据白名单过滤 attr，返回过滤后的 item"""
        type_name = str(item.get("type") or "").strip()
        attr = item.get("attr")

        if not isinstance(attr, dict):
            return item

        whitelist = get_attr_whitelist(type_name)

        # 如果没有配置白名单（返回 None），保留所有属性
        if whitelist is None:
            return item

        # 白名单为空列表，过滤掉所有 attr
        if not whitelist:
            return {"type": item.get("type")}

        # 根据白名单过滤 attr
        whitelist_normalized = {_normalize_key_name(k) for k in whitelist}
        filtered_attr = {}
        for key, val in attr.items():
            key_lower = _normalize_key_name(str(key).strip())
            if key_lower in whitelist_normalized:
                filtered_attr[key] = val

        return {"type": item.get("type"), "attr": filtered_attr} if filtered_attr else {"type": item.get("type")}

    def normalize_structured_item(item: Dict[str, Any]) -> List[str]:
        def append_value_with_key_semantics(container: List[str], key_text: str, value: Any) -> None:
            if value is None:
                return

            normalized_key = _normalize_key_name(key_text)
            semantic_token = SEMANTIC_FIELD_TOKENS.get(normalized_key)
            is_lfp_lip = normalized_key in LFP_LIP_KEYS
            is_cf_l_bracket = normalized_key in CF_L_BRACKET_KEYS
            is_c_c = normalized_key in {"cc", "c-c", "c到c"}
            is_collection_bucket = normalized_key in {"collectionbucket", "集合斗参数", "集合斗"}
            is_collating_funnel = normalized_key in {"collatingfunnel", "供料漏斗", "供料锥"}
            is_center_vibrator_key = normalized_key in {"centervibrator", "中心振动器"}
            is_end_user_country = normalized_key in {"endusercountry", "country", "终端国家"}
            is_vibrating = "振动盘" in type_name
            is_feeding_hopper = "供料斗" in type_name or "feed bucket" in type_name.lower()
            is_weigh_bucket = "计量斗" in type_name or "weigh bucket" in type_name.lower()
            is_collecting_cone = "收集锥" in type_name or "集料漏斗" in type_name
            is_packaging = "包装" in type_name

            if is_c_c:
                text_value = str(value).strip()
                if not text_value:
                    return
                lower_text_value = text_value.lower()
                if lower_text_value in NO_VALUES:
                    return
                c_c_value = _extract_c_c_value(text_value)
                if not c_c_value:
                    return
                # Mapping expands this to C-C=数值 / C-C数值 alternatives.
                container.append(f"C-C={c_c_value}")
                return

            if is_collection_bucket:
                text_value = str(value).strip().lower()
                if not text_value:
                    return
                before_count = len(container)
                if "3l" in text_value:
                    container.append("3L")
                if "1-way" in text_value or "1 way" in text_value:
                    container.append("1-way")
                if "2-way" in text_value or "2 way" in text_value:
                    container.append("2-way")
                if "motor" in text_value:
                    container.append("motor")
                if "pneumatic" in text_value:
                    container.append("pneumatic")
                if "side stroke" in text_value:
                    container.append("side stroke")
                if "center" in text_value and "drive" in text_value and "double" in text_value:
                    container.append("center double drive")
                elif "center" in text_value and "drive" in text_value:
                    container.append("center drive")
                if "单横" in text_value:
                    container.append("单横")
                if "双横" in text_value:
                    container.append("双横")
                if len(container) > before_count:
                    return

            value_state = _to_tristate(value)
            if value_state in {"yes", "no"}:
                if value_state == "yes":
                    if semantic_token:
                        container.append(semantic_token)
                    elif key_text:
                        container.append(key_text)
                else:
                    if semantic_token:
                        container.append(f"{semantic_token}:no")
                    elif is_cf_l_bracket and is_collecting_cone:
                        # 收集锥规则: No 对应无L型支架，不作为参数加入。
                        return
                    elif is_lfp_lip:
                        if is_vibrating:
                            container.append("无")
                        # 供料斗和计量斗的 LFP lip = No 不加入关键词。
                        elif is_feeding_hopper or is_weigh_bucket:
                            return
                return

            text = str(value).strip()
            if not text:
                return

            if (is_feeding_hopper or is_weigh_bucket) and normalized_key in REMARKS_KEYS:
                for capacity in _extract_feed_bucket_remark_capacities(text):
                    container.append(capacity)
                return

            if is_end_user_country and is_packaging:
                lower_country = text.lower()
                if lower_country in {"india", "indian", "印度"}:
                    container.append("india")
                    container.append("!非印度")
                    return
                if lower_country in {"china", "chinese", "中国", "国内"}:
                    container.append("china")
                    return
                # 非印度国家: 主检国外类关键词，同时排除印度/国内语义干扰。
                container.append("non-india")
                container.append("!印")
                container.append("!国内")
                container.append("!非印度")
                return

            lower_text = text.lower()

            # 针对枚举值的通用归一化。
            if is_center_vibrator_key or is_collating_funnel:
                if lower_text in {"single"}:
                    container.append("single")
                    return
                if lower_text in {"double", "twin", "divided two", "dividedtwo", "divided-two"}:
                    container.append("double" if is_center_vibrator_key else "twin")
                    return

            container.append(text)

        group: List[str] = []

        type_name = str(item.get("type") or "").strip()
        if type_name:
            group.append(type_name)

        attr = item.get("attr")

        if isinstance(attr, dict):
            for key, raw_value in attr.items():
                key_text = str(key).strip()

                if isinstance(raw_value, Sequence) and not isinstance(raw_value, (str, bytes, bytearray, dict)):
                    for sub_value in raw_value:
                        append_value_with_key_semantics(group, key_text, sub_value)
                    continue

                append_value_with_key_semantics(group, key_text, raw_value)

        # 去重并保持顺序
        return list(dict.fromkeys(group))

    # 单个结构化对象：{"type": "机架", "attr": {...}}
    if isinstance(value, dict):
        value = [value]

    if not isinstance(value, list) or not value:
        return []

    # 仅接受结构化形式: [{"type": "机架", "attr": {...}}, ...]
    if not all(isinstance(item, dict) for item in value):
        return []

    # 先根据白名单过滤每个 item 的 attr
    filtered_items = [filter_attr_by_whitelist(item) for item in value]

    groups: List[List[str]] = []
    for item in filtered_items:
        group = normalize_structured_item(item)
        if group:
            groups.append(group)
    return groups
