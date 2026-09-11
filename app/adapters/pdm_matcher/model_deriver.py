"""
Model 派生：完整型号 → 多层级派生
"""

import re


def derive_models(raw_model: str) -> dict:
    """
    从规格书 OCR 识别的完整型号，派生出多层次查询关键词。

    返回:
        {
            "full": "ADW-A-0314S",           # 原始完整型号
            "alt_prefix": "ADW-0314S",        # 去掉一个A-的写法变体
            "short": "0314S",                 # 短型号（去掉ADW-A-前缀）
            "base": "0314",                   # 无后缀基号
            "base_with_suffix": "0314S",      # 基号+单后缀
            "series": "03系列",               # 系列号
            "series_xx": "03XX",              # 系列通配形式
            "split": ["ADW-A-0314S"],         # 拆解多型号后的列表
            "suffix": "S",                     # 后缀字母
        }

    不修改数据库。
    """
    if not raw_model or not raw_model.strip():
        return _empty_result()

    model = raw_model.strip()

    # ---- 1. 多型号拆解 ----
    split_models = _split_multi_models(model)

    # 取第一个型号做派生（多型号各自独立派生）
    primary = split_models[0]

    # ---- 2. 识别前缀类型 ----
    prefix = ""
    body = primary

    # ADW-A- 系列
    m = re.match(r'(ADW-[A-Z]?-?)', primary)
    if m:
        prefix = m.group(1)
        body = primary[len(prefix):]

    # FNL- 系列
    m = re.match(r'(FNL-)', primary)
    if m:
        prefix = m.group(1)
        body = primary[len(prefix):]

    # 其他前缀
    m = re.match(r'(RCU-|EF-|UL\d+)', primary)
    if m:
        prefix = m.group(1)
        body = primary[len(prefix):]

    # ---- 3. 提取短型号 ----
    # body 就是去掉前缀后的部分，如 "0314S"
    short = body if body else primary

    # ---- 4. 提取无后缀基号 ----
    base_match = re.match(r'(\d{2,4})', short)
    base = base_match.group(1) if base_match else short

    # ---- 5. 提取后缀 ----
    suffix_match = re.search(r'(\d{2,4})([A-Z]+)', short)
    suffix = suffix_match.group(2) if suffix_match else ""

    # ---- 6. 构造变体 ----
    # alt_prefix: 处理 ADW-A- → ADW- 的变体
    alt_prefix = ""
    if prefix.startswith("ADW-A-"):
        alt_prefix = "ADW-"

    # ---- 7. 系列号 ----
    series = f"{base[:2]}系列" if len(base) >= 2 else ""

    # ---- 8. 系列通配 ----
    series_xx = ""
    if len(base) >= 2:
        series_xx = base[:2] + "XX"

    # ---- 9. 处理所有拆解的型号 ----
    all_derived = []
    for sm in split_models:
        all_derived.append(_derive_single(sm))

    # ---- 10. 弱别名（legacy model code → current naming） ----
    # DB evidence: 中心柱(520/0320), MODEL "ADW-514A ADW-520A ADW-A-0314S ADW-A-0320S"
    # 520A/520 is legacy Japanese product code for 0320S/0320.
    # 514A/514 is legacy code for 0314S/0314.
    # These are WEAK hints — only used when type + spec params already match.
    weak_aliases = _derive_weak_aliases(short, base)

    return {
        "full": model,
        "primary": primary,
        "prefix": prefix,
        "body": body,
        "short": short,
        "base": base,
        "base_with_suffix": base + suffix if suffix else short,
        "suffix": suffix,
        "alt_prefix": alt_prefix,
        "alt_model": alt_prefix + short if alt_prefix else "",
        "series": series,
        "series_xx": series_xx,
        "split_models": split_models,
        "derived": all_derived,
        "is_multi": len(split_models) > 1,
        "weak_aliases": weak_aliases,
    }


def _derive_single(model: str) -> dict:
    """对单个型号做完整派生"""
    m = re.match(r'(ADW-[A-Z]?-?)', model)
    prefix = m.group(1) if m else ""
    body = model[len(prefix):] if prefix else model
    short = body if body else model
    base_match = re.match(r'(\d{2,4})', short)
    base = base_match.group(1) if base_match else short
    suffix_match = re.search(r'(\d{2,4})([A-Z]+)', short)
    suffix = suffix_match.group(2) if suffix_match else ""
    alt_prefix = "ADW-" if prefix.startswith("ADW-A-") else ""
    series = f"{base[:2]}系列" if len(base) >= 2 else ""
    return {
        "full": model,
        "short": short,
        "base": base,
        "suffix": suffix,
        "alt_prefix": alt_prefix,
        "series": series,
    }


def _split_multi_models(model: str) -> list:
    """拆解多型号并列，如 'ADW-514A/ADW-A-0314S' → ['ADW-514A', 'ADW-A-0314S']"""
    # 各种分隔符
    if re.search(r'[/、+,&]', model):
        parts = re.split(r'\s*[/、+,&]\s*', model)
        # 过滤掉太短的（可能是误拆）
        parts = [p.strip() for p in parts if len(p.strip()) >= 3]
        if len(parts) > 1:
            return parts
    return [model]


def _derive_weak_aliases(short: str, base: str) -> list:
    """Derive legacy product-code aliases for weak fallback matching.

    DB evidence:
    - 中心柱(520/0320) → 520 = 0320, 520A = 0320S
    - MODEL "ADW-514A ADW-A-0314S" → 514A = 0314S
    - These are legacy Japanese product codes used in CHINANAME only (MODEL
      field is often empty for these records)

    Only >=4 character aliases are active for matching (recall + scoring).
    3-digit numeric aliases (520, 514, etc.) are too generic and only
    serve as documentation of the equivalence.
    """
    raw_mapping = {
        "0320": ["520", "520A"],
        "0320S": ["520A"],
        "0314": ["514", "514A"],
        "0314S": ["514A"],
        "0316": ["516", "516A"],
        "0316S": ["516A"],
        "0310": ["510", "510A"],
        "0310S": ["510A"],
    }
    raw_aliases = []
    if short in raw_mapping:
        raw_aliases.extend(raw_mapping[short])
    if base != short and base in raw_mapping:
        raw_aliases.extend(raw_mapping[base])
    raw_aliases = list(dict.fromkeys(raw_aliases))
    # Only >=4-char aliases are active (3-digit ones are too generic)
    return [a for a in raw_aliases if len(a) >= 4]


def _empty_result() -> dict:
    return {
        "full": "",
        "primary": "",
        "prefix": "",
        "body": "",
        "short": "",
        "base": "",
        "base_with_suffix": "",
        "suffix": "",
        "alt_prefix": "",
        "alt_model": "",
        "series": "",
        "series_xx": "",
        "split_models": [],
        "derived": [],
        "is_multi": False,
        "weak_aliases": [],
    }
