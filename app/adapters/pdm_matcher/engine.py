"""
多路召回 + 多维打分 + 分层输出 匹配引擎（无 PARTID 版本 v2）

核心改进：
- Channel 2 也受 must_kw CHINANAME 过滤，防止 MODEL 跨部件串扰
- 自适应显示限额：HC=3, MC=7, LC=5（合计15以内）
- 输出中提示 narrow_attrs 建议，帮助用户缩小候选
"""

import re

from app.core.config import settings
from app.adapters.sqlserver.client import close_sql_client, get_sql_client
from app.adapters.pdm_matcher.type_config import (
    TYPE_CONFIG,
    ATTR_KEYWORD_MAP,
    DEPRECATION_KEYWORDS,
    COUNTRY_REGION_MAP,
    COUNTRY_APPLICABLE_TYPES,
    REGULATION_SCORING,
    DRIVE_UNIT_KG_RULES,
    LFP_TYPE_KEYWORDS,
)
from app.adapters.pdm_matcher.model_deriver import derive_models


def _pdm_client_config() -> dict:
    return {
        "backend": "pymssql",
        "server": settings.PDM_SQLSERVER_HOST,
        "port": settings.PDM_SQLSERVER_PORT,
        "database": settings.PDM_SQLSERVER_DATABASE,
        "username": settings.PDM_SQLSERVER_USER,
        "password": settings.PDM_SQLSERVER_PASSWORD,
        "encrypt": settings.PDM_SQLSERVER_ENCRYPT,
    }


def _get_conn():
    """使用主项目 Settings 配置创建 SQL Server 客户端（复用工厂 + sqlserver_tools 回退）。"""
    return get_sql_client(_pdm_client_config())


def _model_boundary_match(short_model, text):
    """Return True if short_model appears in text and is NOT a prefix of a longer model suffix.
    e.g. '0314S' matches '0314S/防水', '0314S)', '0314S某种' but NOT '0314SL', '0314SWL'.
    """
    if short_model not in text:
        return False
    pattern = re.escape(short_model) + r'(?![A-Z])'
    return bool(re.search(pattern, text))


def _query(sql, conn=None):
    """Execute SQL, returning list of dicts. Uses conn (client) if provided, else creates new."""
    if conn is not None:
        return conn.query(sql)
    client = _get_conn()
    try:
        return client.query(sql)
    finally:
        close_sql_client(client)


# ---------- Parameter normalization ----------

def normalize(spec_input: dict) -> dict:
    comp_type = spec_input.get("type", "")
    config = TYPE_CONFIG.get(comp_type)

    if not config:
        return {
            "error": f"未知部件类型: {comp_type}",
            "known_types": list(TYPE_CONFIG.keys()),
        }

    model = spec_input.get("model", "")
    derived = derive_models(model)

    attr = spec_input.get("attr", {}) or {}

    attr_keywords = {}
    for attr_name, attr_value in attr.items():
        kw_map = ATTR_KEYWORD_MAP.get(attr_name, {})
        attr_value_lower = str(attr_value).lower()

        if attr_name in ("degree", "c_c", "cable_length", "name_plate", "capacity"):
            attr_keywords[attr_name] = str(attr_value)
            continue

        matched_kw = []
        if attr_value_lower in kw_map:
            matched_kw = kw_map[attr_value_lower]
        elif isinstance(attr_value, str) and attr_value in kw_map:
            matched_kw = kw_map[attr_value]

        if matched_kw:
            attr_keywords[attr_name] = matched_kw

    # 动态调整: collating_chute 指定的类型从 exclude 移到 must
    must_kw = list(config.get("must_kw", []))
    exclude_kw = list(config.get("exclude_kw", []))

    if "collating_chute" in attr_keywords:
        for ckw in attr_keywords["collating_chute"]:
            if ckw in exclude_kw:
                exclude_kw.remove(ckw)
            if ckw not in must_kw:
                must_kw.append(ckw)

    # 计算缺少哪些 narrow_attrs
    provided_attrs = set(attr.keys())
    suggested = [a for a in config.get("narrow_attrs", []) if a not in provided_attrs]

    return {
        "type": comp_type,
        "config": {
            "must_kw": must_kw,
            "must_kw_logic": config.get("must_kw_logic", "AND"),
            "exclude_kw": exclude_kw,
            "non_subject_exclude": config.get("non_subject_exclude", []),
            "partid_prefixes": config.get("partid_prefixes", []),
            "positive_kw": config.get("positive_kw", {}),
            "lfp_type_config": config.get("lfp_type_config", False),
        },
        "model_raw": model,
        "model_derived": derived,
        "attr_raw": attr,
        "attr_keywords": attr_keywords,
        "suggested_attrs": suggested,
    }


# ---------- SQL helper: build CHINANAME must-filter for model channels ----------

def _chinaname_must_filter(must_kw: list, logic: str, alias: str = "b27") -> str:
    """构建 CHINANAME must_kw 过滤条件，用于附加到 MODEL 查询中"""
    if not must_kw:
        return ""
    if logic == "OR":
        return "(" + " OR ".join(
            [f"{alias}.CHINANAME LIKE N'%{kw}%'" for kw in must_kw]
        ) + ")"
    else:
        return "(" + " AND ".join(
            [f"{alias}.CHINANAME LIKE N'%{kw}%'" for kw in must_kw]
        ) + ")"


def _chinaname_exclude_filter(exclude_kw: list, alias: str = "b27") -> str:
    """构建 CHINANAME exclude_kw 排除条件"""
    if not exclude_kw:
        return ""
    return " AND ".join(
        [f"{alias}.CHINANAME NOT LIKE N'%{ekw}%'" for ekw in exclude_kw]
    )


# ---------- Channel 1: CHINANAME text ----------

def _channel1_chinaname(norm: dict, conn=None) -> list:
    config = norm["config"]
    must_kw = config["must_kw"]
    exclude_kw = config["exclude_kw"]
    must_logic = config["must_kw_logic"]

    if must_logic == "OR":
        must_cond = " OR ".join([f"b27.CHINANAME LIKE N'%{kw}%'" for kw in must_kw])
    else:
        must_cond = " AND ".join([f"b27.CHINANAME LIKE N'%{kw}%'" for kw in must_kw])

    where_parts = [f"({must_cond})"]
    for ekw in exclude_kw:
        where_parts.append(f"b27.CHINANAME NOT LIKE N'%{ekw}%'")

    where_sql = " AND ".join(where_parts)

    sql = f"""
        SELECT b27.PARTID, b27.PARTVAR, b27.CHINANAME, b27.MODEL, b27.SPEC,
               b54.BOMSTATE, b54.BOMNAME
        FROM BOM_027 b27
        LEFT JOIN BOM_054 b54 ON b27.PARTID = b54.PARTID
        WHERE {where_sql}
        ORDER BY b27.PARTID
    """
    rows = _query(sql, conn)
    for r in rows:
        r["_channel"] = "ch1"
        r["_channel_score"] = 5
    return rows


# ---------- Channel 2: MODEL + CHINANAME must ----------

def _channel2_model(norm: dict, conn=None) -> list:
    """MODEL 匹配，附加 CHINANAME must_kw 过滤防止跨部件串扰"""
    derived = norm["model_derived"]
    if not derived["full"]:
        return []

    config = norm["config"]
    must_kw = config["must_kw"]
    must_logic = config["must_kw_logic"]
    exclude_kw = config["exclude_kw"]

    conditions = []
    full = derived["full"].replace("'", "''")
    conditions.append(f"b27.MODEL = N'{full}'")

    short = derived["short"].replace("'", "''")
    if len(short) >= 3:
        conditions.append(f"b27.MODEL LIKE N'%{short}%'")

    if derived["alt_model"]:
        alt = derived["alt_model"].replace("'", "''")
        conditions.append(f"b27.MODEL = N'{alt}'")

    if derived["series_xx"]:
        sxx = derived["series_xx"].replace("'", "''")
        conditions.append(f"b27.MODEL LIKE N'%{sxx}%'")

    if derived["series"]:
        ser = derived["series"].replace("'", "''")
        conditions.append(f"b27.MODEL LIKE N'%{ser}%'")

    for sm in derived["split_models"]:
        sms = sm.replace("'", "''")
        if sms != full:
            conditions.append(f"b27.MODEL LIKE N'%{sms}%'")

    model_where = " OR ".join(conditions)

    # 关键: MODEL 匹配也必须通过 CHINANAME must_kw 过滤
    cn_filter = _chinaname_must_filter(must_kw, must_logic)
    # 加上 exclude_kw 过滤，防止跨部件串扰
    ex_filter = _chinaname_exclude_filter(exclude_kw)
    full_filter = cn_filter
    if ex_filter:
        full_filter += " AND " + ex_filter

    sql = f"""
        SELECT b27.PARTID, b27.PARTVAR, b27.CHINANAME, b27.MODEL, b27.SPEC,
               b54.BOMSTATE, b54.BOMNAME
        FROM BOM_027 b27
        LEFT JOIN BOM_054 b54 ON b27.PARTID = b54.PARTID
        WHERE b27.MODEL IS NOT NULL AND b27.MODEL <> ''
          AND ({model_where})
          AND {full_filter}
        ORDER BY b27.PARTID
    """
    rows = _query(sql, conn)
    for r in rows:
        r["_channel"] = "ch2"
        r["_channel_score"] = 8
    return rows


# ---------- Channel 3: CHINANAME 型号模糊 ----------

def _channel3_chinaname_fuzzy(norm: dict, conn=None) -> list:
    derived = norm["model_derived"]
    short = derived.get("short", "")
    if not short or len(short) < 3:
        return []

    config = norm["config"]
    must_kw = config["must_kw"]
    must_logic = config["must_kw_logic"]
    exclude_kw = config["exclude_kw"]
    esc_short = short.replace("'", "''")
    weak_aliases = derived.get("weak_aliases", [])

    cn_filter = _chinaname_must_filter(must_kw, must_logic)
    ex_filter = _chinaname_exclude_filter(exclude_kw)
    full_filter = cn_filter
    if ex_filter:
        full_filter += " AND " + ex_filter

    model_conditions = [f"b27.CHINANAME LIKE N'%{esc_short}%'"]
    for alias in weak_aliases:
        # Only use aliases >=4 chars for recall (avoids "514","520"
        # flooding results). Short numeric aliases only score in _score_all.
        if len(alias) >= 4:
            esc_alias = alias.replace("'", "''")
            model_conditions.append(f"b27.CHINANAME LIKE N'%{esc_alias}%'")

    model_where = " OR ".join(model_conditions)

    sql = f"""
        SELECT b27.PARTID, b27.PARTVAR, b27.CHINANAME, b27.MODEL, b27.SPEC,
               b54.BOMSTATE, b54.BOMNAME
        FROM BOM_027 b27
        LEFT JOIN BOM_054 b54 ON b27.PARTID = b54.PARTID
        WHERE ({model_where})
          AND {full_filter}
        ORDER BY b27.PARTID
    """
    rows = _query(sql, conn)
    filtered = []
    for r in rows:
        cn = r.get("CHINANAME") or ""
        # Model suffix boundary: reject records where short_model is a prefix
        # of a longer suffix (e.g. query 0314S matching CN 0314SL)
        short_in_cn = _model_boundary_match(esc_short, cn)
        alias_hit = any(a in cn for a in weak_aliases)
        # Skip if short_model appears as suffix extension only (not a true match)
        if esc_short in cn and not short_in_cn:
            continue
        r["_channel"] = "ch3"
        # Alias-only hits (no direct model boundary match in CN) get reduced score
        if alias_hit and not short_in_cn:
            r["_channel_score"] = 1
            r["_alias_hit"] = True
        else:
            r["_channel_score"] = 2
        filtered.append(r)
    return filtered


# ---------- Channel 4: BOM_016 层级反查 ----------

def _channel4_bom_parent(norm: dict, ch1_partids: list, conn=None) -> list:
    if not ch1_partids:
        return []

    config = norm["config"]
    must_kw = config["must_kw"]
    must_logic = config["must_kw_logic"]
    partids = ch1_partids[:200]
    placeholders = ",".join([f"N'{p}'" for p in partids])

    cn_filter = _chinaname_must_filter(must_kw, must_logic, alias="b27")

    sql = f"""
        SELECT DISTINCT b27.PARTID, b27.PARTVAR, b27.CHINANAME, b27.MODEL, b27.SPEC,
               b54.BOMSTATE, b54.BOMNAME,
               MIN(b16.ASSEMBLELEVEL) AS min_asm_level
        FROM BOM_016 b16
        JOIN BOM_027 b27 ON b16.PARENTID = b27.PARTID
        LEFT JOIN BOM_054 b54 ON b27.PARTID = b54.PARTID
        WHERE b16.PARTID IN ({placeholders})
          AND b16.ASSEMBLELEVEL <= 2
          AND {cn_filter}
        GROUP BY b27.PARTID, b27.PARTVAR, b27.CHINANAME, b27.MODEL, b27.SPEC,
                 b54.BOMSTATE, b54.BOMNAME
    """
    rows = _query(sql, conn)
    for r in rows:
        r["_channel"] = "ch4"
        r["_channel_score"] = 3
    return rows


# ---------- BOM enrichment ----------

def _enrich_bom_info(candidates: list, conn=None) -> list:
    """Set BOM metadata defaults without querying BOM_016.

    Previously this queried BOM_016 for ASSEMBLELEVEL and PARENTID
    which caused timeouts for spec series with large candidate pools
    (e.g. 0114S). The scoring impact of these fields is minimal
    (+2/+1/-1) and not worth the DB risk.

    Channel 4 (_channel4_bom_parent) still queries BOM_016 for
    work_no → child PARTID lookup, which is the primary BOM signal.
    """
    for c in candidates:
        c["_bom_min_level"] = None
        c["_bom_is_parent"] = False
    return candidates


# ---------- Subject word recognition ----------

def _subject_penalty(cn: str, must_kw: list, non_subject_exclude: list) -> tuple:
    """检测查询关键词在 CHINANAME 中是主体词还是定语/修饰语。
    返回 (penalty, reason_string)"""
    if not non_subject_exclude:
        return 0, ""

    # 提取括号外的正文（主体描述）
    main_text = re.sub(r'[（(][^)）]*[)）]', '', cn)
    # 提取括号内的文本（用途/参数说明）
    paren_text = ' '.join(re.findall(r'[（(]([^)）]*)[)）]', cn))

    # 检查1: must_kw 仅出现在括号内 → 定语用法
    kw_in_main = any(kw in main_text for kw in must_kw)
    kw_in_paren = any(kw in paren_text for kw in must_kw)

    if kw_in_paren and not kw_in_main:
        return -20, "subject:only_in_parens"

    # 检查2: 正文中非主体词作为核心名词，must_kw 作为修饰语前置
    # 中文复合名词中核心名词在后，如 "溜槽防碎装置" 核心是 "防碎装置"
    for nsw in non_subject_exclude:
        if nsw in main_text:
            nsw_pos = main_text.rfind(nsw)
            for kw in must_kw:
                kw_pos = main_text.find(kw)
                if kw_pos >= 0 and kw_pos < nsw_pos:
                    return -20, f"subject:{nsw}_is_head"

    return 0, ""


# ---------- PARTID prefix scoring ----------

def _partid_prefix_score(partid: str, prefixes: list) -> int:
    """检查 PARTID 是否匹配预期的前缀列表，匹配则加分"""
    if not prefixes:
        return 0
    for pfx in prefixes:
        if partid.startswith(pfx):
            return 4
    return 0


# ---------- Scoring ----------

def _score_all(candidates: list, norm: dict) -> list:
    derived = norm["model_derived"]
    config = norm["config"]
    attr_keywords = norm["attr_keywords"]
    must_kw = config["must_kw"]
    exclude_kw = config["exclude_kw"]
    must_logic = config["must_kw_logic"]
    non_subject_exclude = config.get("non_subject_exclude", [])
    partid_prefixes = config.get("partid_prefixes", [])

    scored = []
    for c in candidates:
        score = 0
        penalty = 0
        reasons = []

        cn = c.get("CHINANAME") or ""
        model = c.get("MODEL") or ""
        bomstate = c.get("BOMSTATE") or ""

        # ---- 通道分 ----
        ch_score = c.get("_channel_score", 0)
        ch_name = c.get("_channel", "")
        score += ch_score
        reasons.append(f"ch={ch_name}:+{ch_score}")

        # ---- MUST_KW 命中 ----
        must_hits = [kw for kw in must_kw if kw in cn]

        if must_logic == "OR":
            if must_hits:
                score += 15
                reasons.append(f"must_kw(OR):{must_hits}:+15")
            else:
                penalty -= 15
                reasons.append(f"must_kw(OR)_miss:-15")
        else:
            if len(must_hits) == len(must_kw):
                score += 15
                reasons.append(f"must_kw(AND):+15")
            elif must_hits:
                missed = [kw for kw in must_kw if kw not in cn]
                penalty -= 10
                reasons.append(f"must_kw(AND)_partial/miss:{missed}:-10")
            else:
                penalty -= 15
                reasons.append(f"must_kw(AND)_miss:-15")

        # ---- 排除词惩罚 ----
        exclude_hits = [ekw for ekw in exclude_kw if ekw in cn]
        if exclude_hits:
            penalty -= 10 * len(exclude_hits)
            reasons.append(f"exclude:{exclude_hits}:-{10*len(exclude_hits)}")

        # ---- 主体词检查 ----
        subj_pen, subj_reason = _subject_penalty(cn, must_kw, non_subject_exclude)
        if subj_pen:
            penalty += subj_pen
            reasons.append(subj_reason + f":{subj_pen}")

        # ---- MODEL 匹配 ----
        full_model = derived.get("full", "")
        short_model = derived.get("short", "")
        series = derived.get("series", "")
        alt_model = derived.get("alt_model", "")
        split_models = derived.get("split_models", [])

        model_bonus = 0
        if full_model and model == full_model:
            model_bonus += 10
            reasons.append("model_exact:+10")
        elif full_model and full_model in model:
            model_bonus += 7
            reasons.append("model_contains:+7")

        base_model = derived.get("base", "")
        # Base model (strip suffix letters) in MODEL field — for types where
        # CN stores model without suffix (e.g. 收集锥 0314 not 0314S)
        if model_bonus == 0 and base_model and len(base_model) >= 3:
            if base_model in model:
                model_bonus += 5
                reasons.append(f"model_has_base:{base_model}:+5")

        # ---- Old model name equivalence ----
        # 514/314 are legacy model names for 0314/0114 series.
        # 514=0314, 514A=0314S, 514ACC≈0314S
        # 314=0114, 314A=0114S, 314ACC≈0114S
        # When the spec uses new naming but candidates use old naming (or vice
        # versa), treat them as equivalent model matches.
        if model_bonus == 0 and base_model and model:
            OLD_MODEL_MAP = {
                "0314": ["514", "514A", "514ACC", "03XX"],
                "0114": ["314", "314A", "314ACC"],
            }
            equivalents = OLD_MODEL_MAP.get(base_model, [])
            for eq in equivalents:
                if eq in model:
                    model_bonus += 10
                    reasons.append(f"model_old_eq:{eq}→{base_model}:+10")
                    break

        cn_model_bonus = 0
        if short_model and len(short_model) >= 3:
            # Suffix boundary: 0314S should NOT match 0314SL/SW/SWL in CN
            bracket_pattern = re.compile(
                r'[（(][^）)]*' + re.escape(short_model) + r'(?![A-Z])[^）)]*[）)]'
            )
            # CN start: short model at beginning, not followed by uppercase letter
            starts_with_short = bool(re.match(
                r'^' + re.escape(short_model) + r'(?=[^A-Z]|\Z)', cn
            ))
            if bracket_pattern.search(cn):
                cn_model_bonus = 12
                reasons.append(f"short_in_bracket:{short_model}:+12")
            elif starts_with_short:
                cn_model_bonus = 10
                reasons.append(f"short_at_cn_start:{short_model}:+10")
            elif _model_boundary_match(short_model, cn):
                cn_model_bonus = 4
                reasons.append(f"short_in_cn:{short_model}:+4")

        # Old model name in CN: 314ACC/314A in CN ≈ 0114S in CN
        # When the spec uses new naming but CN still uses old naming.
        if cn_model_bonus == 0 and base_model:
            OLD_CN_MAP = {
                "0314": ["514", "514A", "514ACC"],
                "0114": ["314", "314A", "314ACC"],
                "03系列": ["03XX"],  # generic 03-series part reference
            }
            old_names = OLD_CN_MAP.get(base_model, [])
            for old in old_names:
                if old in cn:
                    cn_model_bonus = 10
                    reasons.append(f"old_name_in_cn:{old}→{base_model}:+10")
                    break

        # Base model (without suffix) CN matching — for types where CN
        # stores model without trailing letters (e.g. "0314" not "0314S")
        if cn_model_bonus == 0 and base_model and len(base_model) >= 3 \
                and base_model != short_model:
            # Cascade: try near-match with progressively shorter suffix
            # e.g. for "0314SL" → try "0314S" → +10, then "0314" → +9
            near_model = short_model
            while near_model and len(near_model) > len(base_model):
                near_model = near_model[:-1]
                if len(near_model) < len(base_model) + 1:
                    break  # at least base+1 char
                near_bracket = re.compile(
                    r'[（(][^）)]*' + re.escape(near_model) + r'(?![A-Z])[^）)]*[）)]'
                )
                if near_bracket.search(cn):
                    cn_model_bonus = 10
                    reasons.append(f"near_in_bracket:{near_model}:+10")
                    break

            if cn_model_bonus == 0:
                base_bracket = re.compile(
                    r'[（(][^）)]*' + re.escape(base_model) + r'[^）)]*[）)]'
                )
                base_at_start = bool(re.match(
                    r'^' + re.escape(base_model) + r'(?=[\sA-Za-z0-9（(]|\Z)', cn
                ))
                if base_bracket.search(cn):
                    cn_model_bonus = 9
                    reasons.append(f"base_in_bracket:{base_model}:+9")
                elif base_at_start:
                    cn_model_bonus = 7
                    reasons.append(f"base_at_cn_start:{base_model}:+7")
                elif base_model in cn:
                    cn_model_bonus = 3
                    reasons.append(f"base_in_cn:{base_model}:+3")

        # Series-compatible cross-reference: when CN uses "03XXS"/"03XX" pattern
        # to indicate compatibility with the entire 03-series family, give a
        # bonus for queries in the same series. e.g. "ADW-A-03XXS" + query "0314".
        if cn_model_bonus == 0 and base_model and len(base_model) >= 3:
            series_prefix = base_model[:2]  # "03" from "0314"
            if re.search(r'\b' + re.escape(series_prefix) + r'XX', cn):
                cn_model_bonus = 5
                reasons.append(f"series_compat:{series_prefix}XX:+5")

        # MODEL-empty records get compensation since CN model match
        # is the only signal available (MODEL coverage is only 33.5%)
        if not model and cn_model_bonus > 0:
            cn_model_bonus += 5
            reasons.append("model_less_comp:+5")

        # CN explicitly mentioning the query model gets priority over
        # candidates that only match via the MODEL field (which is sparsely
        # populated). Model-in-CN is stronger evidence of relevance.
        if cn_model_bonus > 0:
            cn_model_bonus += 3
            reasons.append("cn_has_model:+3")

        # Cap total model-related bonus at 19 to reduce gap vs MODEL-empty records
        model_total = model_bonus + cn_model_bonus
        if model_total > 19:
            cn_model_bonus = max(0, 19 - model_bonus)
            model_total = model_bonus + cn_model_bonus
        score += model_total

        if alt_model and model == alt_model:
            score += 5
            reasons.append("alt_model:+5")

        for sm in split_models:
            if sm and sm != full_model and sm in model:
                score += 3
                reasons.append(f"split_model:{sm}:+3")
                break

        # ---- Weak alias bonus ----
        # Legacy product codes (e.g. 520A for 0320S) found in CN.
        # Only applies when type + spec params already match to prevent
        # random alias matches from polluting results.
        # Short aliases (3-digit: 520, 514) are too generic — skip.
        weak_aliases = derived.get("weak_aliases", [])
        if weak_aliases:
            for alias in weak_aliases:
                if len(alias) >= 4 and (alias in cn or alias in model):
                    alias_bonus = 3
                    score += alias_bonus
                    reasons.append(f"weak_alias:{alias}:+{alias_bonus}")
                    break

        # ---- 规格参数 ----
        # Capacity is often a series default (e.g. 3L for 03 series).
        # Lower weight prevents generic items without capacity mention
        # from being pushed out by items that happen to mention the default.
        _LOW_WEIGHT_ATTRS = {"capacity"}
        attr_score = 0
        attr_match_count = 0
        for attr_name, keywords in attr_keywords.items():
            attr_weight = 2 if attr_name in _LOW_WEIGHT_ATTRS else 4
            if isinstance(keywords, list):
                for kw in keywords:
                    if kw in cn:
                        attr_score += attr_weight
                        attr_match_count += 1
                        reasons.append(f"attr:{attr_name}={kw}:+{attr_weight}")
                        break
            elif isinstance(keywords, str):
                if keywords in cn:
                    attr_score += attr_weight
                    attr_match_count += 1
                    reasons.append(f"attr:{attr_name}={keywords}:+{attr_weight}")

        attr_score = min(attr_score, 20)
        score += attr_score

        # Multi-attr bonus: matching 2+ spec params signals strong relevance
        if attr_match_count >= 2:
            multi_bonus = min(attr_match_count - 1, 3)
            score += multi_bonus
            reasons.append(f"multi_attr:{attr_match_count}:+{multi_bonus}")

        series_bonus = 0
        if series:
            series_bracket = re.compile(
                r'[（(][^）)]*' + re.escape(series) + r'[^）)]*[）)]'
            )
            if series_bracket.search(cn):
                series_bonus = 10
                reasons.append(f"series_in_bracket:{series}:+10")
                # Generic/universal series part: CN references the series
                # but no specific model number → compatible with all machines
                # in this series family. Extra credit for cross-model reuse.
                if not re.search(r'(?:0[0-9]\d{2}[A-Z]*)', cn):
                    series_bonus += 3
                    reasons.append("generic_series:+3")
            elif series in cn or series in model:
                series_bonus = 2
                reasons.append(f"series:{series}:+2")
        score += series_bonus

        # ---- FB/WB gate double priority (供料斗 / 计量斗) ----
        # When spec specifies double gate, candidates with 双开/双开门 in CN
        # get a small supplementary bonus on top of the standard attr match.
        comp_type = norm.get("type", "")
        if comp_type in ("供料斗", "计量斗"):
            raw_attrs = norm.get("attr_raw", {})
            for gate_key, gate_val in [("fb_gate", raw_attrs.get("fb_gate", "")),
                                        ("wb_gate", raw_attrs.get("wb_gate", ""))]:
                if gate_val == "double":
                    if "双开门" in cn or "双开" in cn:
                        score += 3
                        reasons.append(f"gate_double_priority:+3")
                        break

        # ---- Supplemental bonus ----
        # Supplementary params (PIM, leak_proof, duck_mouth, detergent) from
        # Other/Remarks are strong discriminators. These are necessary params
        # when specified — give higher weight to matching items.
        supp_attrs = ["pim", "leak_proof", "duck_mouth", "detergent", "silo"]
        supp_count = 0
        for sa in supp_attrs:
            if sa in attr_keywords:
                kw_list = attr_keywords[sa]
                if isinstance(kw_list, list):
                    for kw in kw_list:
                        if kw in cn:
                            supp_count += 1
                            break
                elif isinstance(kw_list, str) and kw_list in cn:
                    supp_count += 1
        if supp_count > 0:
            supp_bonus = supp_count * 4
            score += supp_bonus
            reasons.append(f"supplemental:x{supp_count}:+{supp_bonus}")

        # ---- 主振动器 YP-3NA / GB01602 boost ----
        # When model family is 0314 and the candidate's CN references
        # the specific part number or model, boost priority.
        if comp_type == "主振动器":
            derived = norm.get("model_derived", {})
            if derived.get("base", "") == "0314":
                if "YP-3NA" in cn or "GB01602G0458" in cn:
                    score += 20
                    reasons.append("main_vib:YP-3NA:+20")

        # ---- Silo / PIM keyword boost for 供料锥支架 ----
        # When spec specifies silo/PIM (from Infeed funnel Other column),
        # candidates with matching keywords should be strongly preferred.
        # These are sparse attrs — few candidates match, so the bonus must
        # be high enough to overcome the model-matching gap.
        if comp_type == "供料锥支架":
            attr_raw = norm.get("attr_raw", {})
            if attr_raw.get("silo") == "yes" and ("silo" in cn.lower() or "SILO" in cn):
                score += 10
                reasons.append("silo_match:+10")

        # ---- 电气原理图 demotion ----
        # "原理图" = schematic diagram (drawing), NOT a real part.
        # Real electrical components (电气元件/电气部品) should rank higher.
        if comp_type == "电气" and "原理图" in cn:
            penalty -= 8
            reasons.append("electrical_schematic:-8")

        # ---- Sprout/vegetable scale bonus ----
        # When spec mentions "sprout" (bean sprout / もやし application),
        # candidates with "蔬菜" in CN are specifically designed for vegetable scales.
        spec_application = norm.get("attr_raw", {}).get("application", "")
        if spec_application == "sprout" and "蔬菜" in cn:
            score += 5
            reasons.append("app:sprout→蔬菜:+5")

        # ---- Work number BOM bonus ----
        # If the spec has a work number, boost candidates that are known
        # children of that work number in BOM_016 (design reuse).
        work_children = norm.get("work_no_children", set())
        if work_children and c.get("PARTID", "") in work_children:
            score += 15
            reasons.append(f"bom_work_no:{norm.get('work_no','')}:+15")

        # ---- Leak-proof = no penalty ----
        # When spec explicitly says no leak-proof, items with leak-proof
        # keywords in CN are over-specified and should not be top priority.
        attr_raw = norm.get("attr_raw", {})
        if attr_raw.get("leak_proof") == "no":
            leak_kws = ["防漏", "B防", "A防", "A型防漏", "B型防漏",
                        "B type", "A type", "Btype", "Atype"]
            for lkw in leak_kws:
                if lkw in cn:
                    penalty -= 15
                    reasons.append(f"leak_proof_overspec:{lkw}:-15")
                    break

        # ---- Detergent = no penalty ----
        # When spec explicitly says no detergent (detergent=no), candidates
        # with "粉"/"粉体" keywords in CN are designed for powder/detergent
        # applications — over-specified and should be deprioritized.
        # Exclude false positives: "粉尘" (dust), "粉末" (powder metallurgy).
        if attr_raw.get("detergent") == "no":
            if ("粉体" in cn or
                re.search(r'[/（(]粉[/）)\s]|[/（(]粉$|[/（(]粉体', cn)):
                if not re.search(r'粉尘|粉末', cn):
                    penalty -= 15
                    reasons.append("detergent_overspec:粉:-15")

        # ---- Degree conflict ----
        # When spec says degree=X, candidates with a different degree
        # (e.g. 50°/60°) in CN are hard-excluded from clean results.
        # They go to needs_review regardless of other scores.
        if "degree" in attr_keywords:
            spec_degree = attr_keywords["degree"]
            cn_degrees = re.findall(r'(?<!\d)(\d{2})\s*[°度/)）]', cn)
            for d in cn_degrees:
                if d != spec_degree and d in ("45", "50", "55", "60"):
                    penalty -= 25
                    reasons.append(f"degree_conflict:{d}°:-25")
                    c["_degree_conflict"] = True
                    break

        # ---- Foreign model penalty ----
        # When the query is for a specific model (e.g. 0310S), penalize
        # candidates whose CN explicitly mentions a DIFFERENT known model
        # (e.g. 0314S, 0114, 0320).  This prevents cross-model leakage
        # where a candidate matches the component type but belongs to a
        # different scale model series.
        # If the CN ALSO contains the query base, the part is multi-model
        # compatible — use a reduced penalty.
        KNOWN_BASES = {"0110","0114","0120","0124","0310","0314","0316","0320",
                       "0510","0514"}
        query_base = derived.get("base", "")
        if query_base:
            # Find 4-digit model bases in CN (e.g. "0314" in "0314S")
            cn_bases = set()
            for m in re.finditer(r'\b(\d{4})[A-Z]*\b', cn):
                if m.group(1) in KNOWN_BASES:
                    cn_bases.add(m.group(1))
            foreign = cn_bases - {query_base}
            has_query_base = query_base in cn_bases
            if foreign:
                if has_query_base:
                    # Multi-model compatible: the CN includes the query model
                    # AND other models. No penalty — multi-model parts are normal.
                    c["_has_query_base"] = True
                else:
                    # True cross-model: CN only has foreign models.
                    penalty -= 25
                    reasons.append(f"foreign_model:{','.join(sorted(foreign))}:-25")
                    c["_foreign_model"] = ",".join(sorted(foreign))

        # ---- Material conflict ----
        # When spec says material=carbon_steel, penalize SUS/不锈钢 candidates.
        # When spec says material=sus, penalize 碳钢/Q235 candidates.
        # Use attr_raw to get the original spec value (not expanded keywords).
        attr_raw = norm.get("attr_raw", {})
        if "material" in attr_raw:
            spec_mat = str(attr_raw["material"]).lower()
            if spec_mat == "carbon_steel":
                sus_kws = ["SUS", "不锈钢"]
                for mkw in sus_kws:
                    if mkw in cn:
                        penalty -= 15
                        reasons.append(f"material_conflict:{mkw}:-15")
                        c["_material_conflict"] = True
                        break
            elif spec_mat == "sus":
                cs_kws = ["碳钢", "Q235"]
                for mkw in cs_kws:
                    if mkw in cn:
                        penalty -= 15
                        reasons.append(f"material_conflict:{mkw}:-15")
                        c["_material_conflict"] = True
                        break

        # ---- Surface conflict: dimple/emboss vs flat ----
        # When spec surface is dimple (embossed/patterned), penalize candidates
        # that have flat surface keywords ("平") but no emboss pattern keywords.
        if "surface" in attr_raw:
            spec_surf = str(attr_raw["surface"]).lower()
            surface_map = ATTR_KEYWORD_MAP.get("surface", {})
            dimple_kws = surface_map.get("dimple", [])
            flat_kws = ["平板", "平/", "(平)", "(平/", "平"]
            has_dimple = any(dk in cn for dk in dimple_kws)
            has_flat = any(fk in cn for fk in flat_kws)
            if spec_surf == "dimple":
                if has_flat and not has_dimple:
                    penalty -= 8
                    reasons.append("surface_conflict:dimple_vs_flat:-8")
            elif spec_surf == "flat":
                # Mirror: spec says flat but candidate has 花纹/花/纹路.
                # "平" may appear in both flat and dimple contexts
                # (e.g. "花纹板/平/"), so check for dimple keywords explicitly.
                if has_dimple and not has_flat:
                    penalty -= 8
                    reasons.append("surface_conflict:flat_vs_dimple:-8")

        # ---- Country/region + regulation keyword scoring ----
        # Supplementary only: does not add recall channels, only adjusts
        # scores on already-retrieved candidates.
        #
        # Type-specific priority:
        #   驱动单元 → regulation (strong) + end_user_country (weak)
        #   包装     → end_user_country (medium)
        #   机架     → end_user_country (weak, positive only)
        #   铭牌/电气/配线单元 → end_user_country (moderate)
        comp_type = norm.get("type", "")
        if comp_type in COUNTRY_APPLICABLE_TYPES:
            attr_raw = norm.get("attr_raw", {})
            end_user_country = attr_raw.get("end_user_country", "")
            regulation = attr_raw.get("regulation", "")

            if comp_type == "驱动单元":
                # --- Regulation is the PRIMARY signal for drive units ---
                # Tiered scoring: exact match > partial > country-only
                if regulation:
                    if regulation == "india_wm":
                        # Tier 1: "印度W&M" exact → +12
                        if "印度W&M" in cn or "印度 W&M" in cn:
                            score += 12
                            reasons.append("reg_india_wm_exact:+12")
                        # Tier 2: "W&M" without "印度" → +8
                        elif "W&M" in cn:
                            score += 8
                            reasons.append("reg_wm_only:+8")
                        # Tier 3: "印度" without "W&M" → +4
                        elif "印度" in cn:
                            score += 4
                            reasons.append("reg_india_only:+4")
                        # European MID → heavy demote
                        if "欧洲MID" in cn:
                            score -= 10
                            reasons.append("reg_eu_demote:-10")
                    elif regulation == "ce":
                        if "欧洲MID" in cn:
                            score += 12
                            reasons.append("reg_eu_mid:+12")
                        if "印度W&M" in cn or "印度 W&M" in cn:
                            score -= 10
                            reasons.append("reg_india_demote:-10")
                    elif regulation in REGULATION_SCORING:
                        rc = REGULATION_SCORING[regulation]
                        for neg_kw in rc.get("negative", []):
                            if neg_kw in cn:
                                score += rc["neg_score"]
                                reasons.append(
                                    f"reg_neg:{regulation}:{neg_kw}:{rc['neg_score']}")
                                break
                        for pos_kw in rc.get("positive", []):
                            if pos_kw in cn:
                                score += rc["pos_score"]
                                reasons.append(
                                    f"reg_pos:{regulation}:{pos_kw}:+{rc['pos_score']}")
                                break
                # --- India/Europe/MID penalty when regulation is unspecified ---
                # When spec does NOT mention any regulation, candidates with
                # India W&M / European MID are over-specified and should rank
                # below clean candidates that don't carry irrelevant certs.
                if not regulation:
                    if "印度W&M" in cn or "欧洲MID" in cn:
                        penalty -= 5
                        reasons.append("reg_unwanted:-5")
                # --- India/Europe demotion guard ---
                # Only demote when regulation is explicitly specified and NOT india_wm/ce
                elif regulation and regulation not in ("india_wm", "ce"):
                    india_europe_kws = [
                        "印度W&M规格秤用", "印度W&M规格", "印度规格秤用",
                        "欧洲MID秤用", "欧洲MID规格秤用", "欧洲规格秤用",
                        "印度、欧洲规格秤用", "印度、欧洲规格",
                        "印度W&M", "欧洲MID",
                    ]
                    for iekw in india_europe_kws:
                        if iekw in cn:
                            score -= 8
                            reasons.append(f"reg_demote:{iekw}:-8")
                            break
                # ---- Drive unit kg scoring (from DB-verified BOM links) ----
                # Only applied when regulation + model family has a known kg mapping.
                # Does NOT introduce new recall channels; scoring-only adjustment.
                derived = norm.get("model_derived", {})
                short_model = derived.get("short", "")
                # Try multiple model forms: exact short, then strip trailing
                # letters (0310SW→0310S), then base only (0310)
                kg_hint = None
                for try_model in {short_model, re.sub(r'[A-Z]$', '', short_model),
                                  derived.get("base", "")}:
                    if not try_model:
                        continue
                    kg_hint = DRIVE_UNIT_KG_RULES.get((try_model, regulation))
                    if kg_hint:
                        break
                if kg_hint:
                    target_kg = kg_hint["kg"]
                    reliability = kg_hint["reliability"]
                    bonus = 5 if reliability == "high" else (3 if reliability == "medium" else 2)
                    if target_kg in cn:
                        score += bonus
                        reasons.append(f"kg_match:{target_kg}:{reliability}:+{bonus}")
                    else:
                        # Penalize records with a different kg value
                        for other_kg in ["2kg", "4kg", "8kg"]:
                            if other_kg != target_kg and other_kg in cn:
                                penalty_val = -3 if reliability == "high" else -2
                                penalty += penalty_val
                                reasons.append(
                                    f"kg_conflict:spec={target_kg},cn={other_kg}:{penalty_val}")
                                break
                # --- End-user country is WEAK for drive units ---
                if end_user_country and end_user_country in COUNTRY_REGION_MAP:
                    rc = COUNTRY_REGION_MAP[end_user_country]
                    for pos_kw in rc.get("positive", []):
                        if pos_kw in cn:
                            if pos_kw == "印度" and "非印度" in cn:
                                continue
                            score += 1
                            reasons.append(
                                f"country_weak:{end_user_country}:{pos_kw}:+1")
                            break

            elif comp_type == "包装":
                # --- End-user country is the PRIMARY signal for packaging ---
                if end_user_country and end_user_country in COUNTRY_REGION_MAP:
                    rc = COUNTRY_REGION_MAP[end_user_country]
                    # Negative check first
                    for neg_kw in rc.get("negative", []):
                        if neg_kw in cn:
                            # Guard: "印度" in "非印度" is NOT a real India match
                            if neg_kw == "印度" and "非印度" in cn:
                                continue
                            score += rc["neg_score"]
                            reasons.append(
                                f"country_neg:{end_user_country}:{neg_kw}:{rc['neg_score']}")
                            break
                    # Positive check
                    for pos_kw in rc.get("positive", []):
                        if pos_kw in cn:
                            if pos_kw == "印度" and "非印度" in cn:
                                continue
                            score += rc["pos_score"]
                            reasons.append(
                                f"country_pos:{end_user_country}:{pos_kw}:+{rc['pos_score']}")
                            break

            elif comp_type == "机架":
                # --- Weak positive-only signal for racks ---
                # DB verified: only 1.8% of rack records have country keywords.
                # Use weak bonus, no penalty (country is not a defining feature).
                if end_user_country and end_user_country in COUNTRY_REGION_MAP:
                    rc = COUNTRY_REGION_MAP[end_user_country]
                    for pos_kw in rc.get("positive", []):
                        if pos_kw in cn:
                            if pos_kw == "印度" and "非印度" in cn:
                                continue
                            score += 3
                            reasons.append(
                                f"country_weak:{end_user_country}:{pos_kw}:+3")
                            break

            else:
                # --- Other types (铭牌, 电气, 配线单元, 线性振动器): moderate signal ---
                # 线性振动器: full REGULATION_SCORING (pos + neg) like 驱动单元.
                # Model suffix matters for this type — regulation is a key differentiator.
                # When regulation is specified, candidates without the regulation keyword
                # are penalized (regulation is a necessary parameter for this type).
                if comp_type == "线性振动器" and regulation and regulation in REGULATION_SCORING:
                    rc = REGULATION_SCORING[regulation]
                    for neg_kw in rc.get("negative", []):
                        if neg_kw in cn:
                            score += rc["neg_score"]
                            reasons.append(
                                f"reg_neg:{regulation}:{neg_kw}:{rc['neg_score']}")
                            break
                    pos_found = False
                    for pos_kw in rc.get("positive", []):
                        if pos_kw in cn:
                            score += rc["pos_score"]
                            reasons.append(
                                f"reg_pos:{regulation}:{pos_kw}:+{rc['pos_score']}")
                            pos_found = True
                            break
                    # Penalize candidates missing the required regulation keyword
                    if not pos_found:
                        score -= 4
                        reasons.append(f"reg_missing:{regulation}:-4")
                # 配线单元: weak regulation reference only
                if comp_type == "配线单元" and regulation:
                    if regulation in REGULATION_SCORING:
                        rc = REGULATION_SCORING[regulation]
                        for pos_kw in rc.get("positive", []):
                            if pos_kw in cn:
                                score += 2
                                reasons.append(
                                    f"reg_weak:{regulation}:{pos_kw}:+2")
                                break
                # Country scoring (moderate, positive only)
                if end_user_country and end_user_country in COUNTRY_REGION_MAP:
                    rc = COUNTRY_REGION_MAP[end_user_country]
                    for pos_kw in rc.get("positive", []):
                        if pos_kw in cn:
                            if pos_kw == "印度" and "非印度" in cn:
                                continue
                            score += 4
                            reasons.append(
                                f"country_moderate:{end_user_country}:{pos_kw}:+4")
                            break

        # ---- Capacity mismatch / overspec penalty ----
        comp_type = norm.get("type", "")
        capacity_regex = r'(?<!\d)(\d+\.?\d*)L'
        capacity_types = {"供料斗", "计量斗", "集合斗"}
        if comp_type in capacity_types:
            cn_cap = re.search(capacity_regex, cn)
            if "capacity" in attr_keywords:
                # Spec has capacity — penalize CN with a different capacity
                spec_cap = attr_keywords["capacity"]
                if cn_cap and cn_cap.group() != spec_cap:
                    penalty -= 5
                    reasons.append(f"capacity_mismatch:spec={spec_cap},cn={cn_cap.group()}:-5")
            else:
                # Spec doesn't specify capacity — penalize CN that does (overspec)
                if cn_cap:
                    penalty -= 5
                    reasons.append(f"overspec:capacity:{cn_cap.group()}:-5")
                    c["_overspec"] = True

        # ---- LFP type dynamic scoring (振动盘 / 顶锥) ----
        # Replaces hardcoded positive_kw with dynamic LFP type keyword matching.
        # Mutual exclusion: only the matching LFP type gets the bonus.
        lfp_type_config = config.get("lfp_type_config", False)
        if lfp_type_config:
            raw_attrs = norm.get("attr_raw", {})
            spec_lfp_type = raw_attrs.get("lfp_type", "")
            if spec_lfp_type and spec_lfp_type in LFP_TYPE_KEYWORDS:
                lfp_cfg = LFP_TYPE_KEYWORDS[spec_lfp_type]
                pos_kws = lfp_cfg.get("positive", [])
                # Positive: matching LFP type keyword in CN
                for lfp_kw in pos_kws:
                    if lfp_kw in cn:
                        score += lfp_cfg["score"]
                        reasons.append(f"lfp_type:{spec_lfp_type}={lfp_kw}:+{lfp_cfg['score']}")
                        break
                # Negative: other LFP types in CN → mutual exclusion penalty
                all_lfp_kws = set()
                for ltype, lcfg in LFP_TYPE_KEYWORDS.items():
                    if ltype in ("No", spec_lfp_type):
                        continue
                    for kw in lcfg.get("positive", []):
                        all_lfp_kws.add(kw)
                for other_kw in all_lfp_kws:
                    if other_kw in cn:
                        # Only penalize if no matching keyword was found
                        matched = any(pk in cn for pk in pos_kws)
                        if not matched:
                            penalty -= 5
                            reasons.append(f"lfp_mismatch:{other_kw}:-5")
                            break

        # ---- 停用标记 ----
        deprecation_hit = None
        for dtype, kws in DEPRECATION_KEYWORDS.items():
            for kw in kws:
                if kw in cn:
                    deprecation_hit = dtype
                    penalty -= 15
                    reasons.append(f"deprecation:{dtype}:-15")
                    break
            if deprecation_hit:
                break

        # MODEL 含多型号 — only flag if multiple slash-separated segments
        # contain digits (real model numbers), not attribute suffixes like /UL.
        if model and re.search(r'[/、+,&]', model):
            segments = [s.strip() for s in re.split(r'[/、+,&]', model) if s.strip()]
            digit_segments = [s for s in segments if re.search(r'\d', s)]
            if len(digit_segments) >= 2:
                query_short = derived.get("short", "")
                query_base = derived.get("base", "")
                # Build near_models by progressively stripping suffix chars
                near_models = []
                nm = query_short
                while nm and query_base and len(nm) > len(query_base):
                    nm = nm[:-1]
                    if len(nm) >= len(query_base) + 1:
                        near_models.append(nm)
                check_models = [query_short] + near_models
                if query_base and query_base not in check_models:
                    check_models.append(query_base)
                if not any(m in model or _model_boundary_match(m, cn) for m in check_models if m):
                    penalty -= 2
                    reasons.append("multi_model:-2")

        # Bracket multi-model (e.g. "0314、514" in brackets)
        bracket_match = re.search(r'[（(]([^）)]*)[）)]', cn)
        if bracket_match and re.search(r'[、,]', bracket_match.group(1)):
            penalty -= 2
            reasons.append("bracket_multi_model:-2")

        # 客户代号
        if cn and re.match(r'^(WG|SWG|YG|YH|洽洽)', cn.strip()):
            penalty -= 3
            reasons.append("customer:-3")

        score += penalty

        # ---- PARTID 前缀加分 ----
        partid = c.get("PARTID") or ""
        pfx_bonus = _partid_prefix_score(partid, partid_prefixes)
        if pfx_bonus:
            score += pfx_bonus
            reasons.append(f"partid_prefix:+{pfx_bonus}")

        # ---- BOM 层级 ----
        bom_bonus = 0
        if bomstate == "frozen":
            bom_bonus += 3
            reasons.append("frozen:+3")
        elif bomstate == "design":
            bom_bonus -= 1
            reasons.append("design:-1")

        min_level = c.get("_bom_min_level")
        if min_level is not None:
            if min_level <= 1:
                bom_bonus += 2
                reasons.append(f"bom_lv{min_level}:+2")
            elif min_level > 2:
                bom_bonus -= 1
                reasons.append(f"bom_lv{min_level}:-1")

        if c.get("_bom_is_parent"):
            bom_bonus += 1
            reasons.append("is_parent:+1")

        score += bom_bonus

        c["_score"] = score
        c["_reasons"] = reasons
        c["_deprecation"] = deprecation_hit
        c["_cn_has_exclude"] = bool(exclude_hits)
        c["_attr_match_count"] = attr_match_count
        c.setdefault("_degree_conflict", False)
        c.setdefault("_foreign_model", False)
        c.setdefault("_has_query_base", False)
        c.setdefault("_material_conflict", False)
        c.setdefault("_overspec", False)
        scored.append(c)

    return scored


# ---------- Result layering ----------

def _layer_results(scored: list, norm: dict) -> dict:
    """分层输出，去重，自适应限额"""
    best = {}
    for c in scored:
        pid = c["PARTID"]
        if pid not in best:
            best[pid] = c
        else:
            # 同PARTID多版本时，优先取非停用版本
            curr_dep = bool(best[pid].get("_deprecation"))
            new_dep = bool(c.get("_deprecation"))
            if curr_dep and not new_dep:
                best[pid] = c  # 新版正常，替换停用版本
            elif not curr_dep and new_dep:
                pass  # 当前非停用，保留
            elif c["_score"] > best[pid]["_score"]:
                best[pid] = c  # 同状态取高分
            elif c["_score"] == best[pid]["_score"]:
                # 同分同状态，优先取新版本 (PARTVAR更大)
                curr_var = best[pid].get("PARTVAR") or ""
                new_var = c.get("PARTVAR") or ""
                if new_var > curr_var:
                    best[pid] = c

    # ---- Multi-version PARTVAR handling ----
    # When a PARTID's best record is deprecated and references another
    # version (e.g. "使用C版"), include the referenced version as well
    existing_objs = set(id(c) for c in best.values())
    multi_ver_extras = []
    for pid, c in list(best.items()):
        cn = c.get("CHINANAME") or ""
        if c.get("_deprecation"):
            ver_ref = re.search(r'使用([A-E])版', cn)
            if ver_ref:
                ref_ver = ver_ref.group(1)
                for sc in scored:
                    if (sc["PARTID"] == pid and
                            (sc.get("PARTVAR") or "") == ref_ver and
                            id(sc) not in existing_objs):
                        multi_ver_extras.append(sc)
                        c["_alt_version"] = sc["PARTID"] + "/" + ref_ver
                        break

    unique = list(best.values()) + multi_ver_extras
    # Stable sort: score → attr_match_count → PARTID as deterministic tiebreaker.
    # Without PARTID, candidates with equal score+attr_match_count depend on
    # DB query order, which can differ between parallel threads.
    unique.sort(key=lambda x: (x["_score"], x.get("_attr_match_count", 0), x["PARTID"]), reverse=True)

    # 获取查询短型号用于智能判断
    query_short = norm.get("model_derived", {}).get("short", "")
    query_base = norm.get("model_derived", {}).get("base", "")

    # 分离需审核的
    review = []
    clean = []
    for c in unique:
        cn = c.get("CHINANAME") or ""
        model = c.get("MODEL") or ""

        # 多型号并列: 如果查询型号在并列型号或CHINANAME中，不触发 review
        multi_model = bool(re.search(r'[/、+,&]', model))
        if multi_model and query_short:
            # Build near_models by progressively stripping suffix chars
            near_models = []
            nm = query_short
            while nm and query_base and len(nm) > len(query_base):
                nm = nm[:-1]
                if len(nm) >= len(query_base) + 1:
                    near_models.append(nm)
            check_models = [query_short] + near_models
            if query_base and query_base not in check_models:
                check_models.append(query_base)
            if any(m in model or _model_boundary_match(m, cn) for m in check_models if m):
                multi_model = False  # 查询型号在其中或其CHINANAME中，属正常匹配

        needs_review = any([
            c.get("_deprecation"),
            c.get("_degree_conflict"),       # spec says X° but CN has different Y°
            c.get("_foreign_model") and not c.get("_has_query_base"),  # foreign model only if query model absent
            bool(re.search(r'(?<!\d)XX', model)),  # standalone XX wildcard (not 03XX)
            bool(re.search(r'(?<!\d)XX', model)),  # standalone XX wildcard (not 03XX)
            bool(re.search(r'(?<!\d)系列', model)),  # 系列 without digit prefix
            multi_model,
            bool(cn and re.match(r'^(WG|SWG|YG|YH|洽洽)', cn.strip())),
            c.get("_cn_has_exclude"),
        ])

        if needs_review:
            review.append(c)
        else:
            clean.append(c)

    # Degree-conflict and foreign-model records always go to the top of review
    # so the user can see which candidates were excluded and why.
    priority_review = [c for c in review if c.get("_degree_conflict") or c.get("_foreign_model")]
    other_review = [c for c in review if not c.get("_degree_conflict") and not c.get("_foreign_model")]
    review = priority_review + other_review

    # PARTID 前缀后过滤: 候选过多时大幅削减非前缀匹配的记录
    # 这是基于部件类型的领域知识，不是用户输入的 PARTID 参数
    partid_prefixes = norm.get("config", {}).get("partid_prefixes", [])
    if partid_prefixes and len(clean) > 50:
        prefix_clean = [c for c in clean
                        if any(c["PARTID"].startswith(p) for p in partid_prefixes)]
        non_prefix = [c for c in clean if c not in prefix_clean]
        non_prefix.sort(key=lambda x: (x["_score"], x["PARTID"]), reverse=True)
        # 保留所有前缀匹配的 + 前10条非前缀匹配（防止跨前缀漏检）
        if len(prefix_clean) >= 10:
            clean = prefix_clean + non_prefix[:10]

    # 根据总候选数自适应限额
    total = len(clean)
    if total <= 20:
        hc_limit, mc_limit, lc_limit = 5, 10, 5
    elif total <= 50:
        hc_limit, mc_limit, lc_limit = 3, 7, 5
    elif total <= 100:
        hc_limit, mc_limit, lc_limit = 3, 7, 5
    else:
        hc_limit, mc_limit, lc_limit = 3, 10, 5

    layer1 = [c for c in clean if c["_score"] >= 25][:hc_limit]
    hc_pids = set(c["PARTID"] for c in layer1)
    layer2 = [c for c in clean if c["PARTID"] not in hc_pids and c["_score"] >= 15][:mc_limit]
    seen_pids = hc_pids | set(c["PARTID"] for c in layer2)
    layer3 = [c for c in clean if c["PARTID"] not in seen_pids][:lc_limit]

    # ---- 顶锥 protected candidate ----
    # BOM-less 顶锥 records that pass all parameter checks but fall out of
    # visible layers due to missing BOM bonus are promoted to MC.
    comp_type = norm.get("type", "")
    if comp_type == "顶锥":
        attr_raw = norm.get("attr_raw", {})
        spec_surface = attr_raw.get("surface", "")
        spec_lfp = attr_raw.get("lfp_type", "")
        derived = norm.get("model_derived", {})
        short_model = derived.get("short", "")
        base_model = derived.get("base", "")
        partid_prefixes = norm.get("config", {}).get("partid_prefixes", [])

        seen_all = hc_pids | set(c["PARTID"] for c in layer2) | set(c["PARTID"] for c in layer3)
        for c in scored:
            pid = c["PARTID"] or ""
            # Skip if already in visible layers
            if pid in seen_all:
                continue
            cn = c.get("CHINANAME") or ""
            # Criteria check
            # 1. Not deprecated
            if c.get("_deprecation"):
                continue
            # 2. PARTID prefix matches
            if partid_prefixes and not any(pid.startswith(p) for p in partid_prefixes):
                continue
            # 3. Must_kw hit (主体词命中)
            must_kw = norm.get("config", {}).get("must_kw", [])
            if not all(kw in cn for kw in must_kw):
                continue
            # 4. Model/basemodel hit
            model_hit = False
            if short_model and short_model in cn:
                model_hit = True
            elif base_model and base_model in cn:
                model_hit = True
            if not model_hit:
                continue
            # 5. Surface match
            if spec_surface:
                surface_map = ATTR_KEYWORD_MAP.get("surface", {})
                surface_kws = surface_map.get(spec_surface, [])
                if surface_kws and not any(sk in cn for sk in surface_kws):
                    continue
            # 6. LFP type match (if spec has lfp_type)
            if spec_lfp and spec_lfp != "No":
                lfp_cfg = LFP_TYPE_KEYWORDS.get(spec_lfp, {})
                lfp_kws = lfp_cfg.get("positive", [])
                if lfp_kws and not any(lk in cn for lk in lfp_kws):
                    continue
            # Passed all criteria — promote to MC
            c["_protected_candidate"] = True
            c["_reasons"].append("protected:参数高度匹配，但缺少BOM加分，作为protected candidate保留")
            layer2.append(c)
            break  # Only promote at most one protected candidate

    return {
        "high_confidence": layer1,
        "medium_confidence": layer2,
        "low_confidence": layer3,
        "needs_review": review[:15],
        "total_candidates": len(unique),
        "total_clean": total,
        "score_distribution": {
            "max": max((c["_score"] for c in unique), default=0),
            "min": min((c["_score"] for c in unique), default=0),
            "avg": round(sum(c["_score"] for c in unique) / len(unique), 1) if unique else 0,
        },
    }


# ---------- Main entry ----------

def query_candidate_parts(spec_input: dict) -> dict:
    norm = normalize(spec_input)
    if "error" in norm:
        return norm

    work_no = spec_input.get("work_no", "")
    work_no_children = set()

    conn = _get_conn()
    try:
        ch1 = _channel1_chinaname(norm, conn)
        ch2 = _channel2_model(norm, conn)

        ch1_pids = set(c["PARTID"] for c in ch1)
        ch2_pids = set(c["PARTID"] for c in ch2)
        total_ch12 = len(ch1_pids | ch2_pids)

        ch3 = _channel3_chinaname_fuzzy(norm, conn) if total_ch12 < 5 else []
        ch4 = _channel4_bom_parent(norm, [c["PARTID"] for c in ch1], conn)

        # Work number BOM lookup: find all child PARTIDs for this work number
        if work_no:
            rows = conn.query("""
                SELECT DISTINCT PARTID FROM BOM_016
                WHERE PARENTID = %s AND ASSEMBLELEVEL <= 3
            """, (work_no,))
            work_no_children = set(r["PARTID"] for r in rows)

        all_candidates = ch1 + ch2 + ch3 + ch4
        all_candidates = _enrich_bom_info(all_candidates, conn)
    finally:
        close_sql_client(conn)

    norm["work_no"] = work_no
    norm["work_no_children"] = work_no_children

    scored = _score_all(all_candidates, norm)
    layers = _layer_results(scored, norm)

    return {
        "normalized": {
            "type": norm["type"],
            "model_raw": norm["model_raw"],
            "model_derived": {
                "full": norm["model_derived"]["full"],
                "short": norm["model_derived"]["short"],
                "base": norm["model_derived"]["base"],
                "suffix": norm["model_derived"]["suffix"],
                "series": norm["model_derived"]["series"],
                "series_xx": norm["model_derived"]["series_xx"],
                "split_models": norm["model_derived"]["split_models"],
                "is_multi": norm["model_derived"]["is_multi"],
                "weak_aliases": norm["model_derived"].get("weak_aliases", []),
            },
            "attr_keywords": norm["attr_keywords"],
            "config": {
                "must_kw": norm["config"]["must_kw"],
                "exclude_kw": norm["config"]["exclude_kw"],
            },
            "suggested_attrs": norm["suggested_attrs"],
        },
        "candidates": layers,
    }


def _format_candidate(c: dict) -> dict:
    return {
        "PARTID": c["PARTID"],
        "PARTVAR": c["PARTVAR"],
        "CHINANAME": c["CHINANAME"],
        "MODEL": c.get("MODEL") or "",
        "SPEC": c.get("SPEC") or "",
        "BOMSTATE": c.get("BOMSTATE") or "",
        "score": c["_score"],
        "reasons": c["_reasons"],
        "deprecation": c.get("_deprecation"),
        "alt_version": c.get("_alt_version", ""),
        "is_parent": c.get("_bom_is_parent", False),
    }


def query_all_parallel(specs: list, max_workers: int = 6) -> list:
    """Query multiple spec components in parallel using ThreadPoolExecutor.
    Returns list of results in the same order as input specs.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = [None] * len(specs)
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_to_idx = {ex.submit(query_and_format, spec): i
                         for i, spec in enumerate(specs)}
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = {"error": str(e), "type": specs[idx].get("type", "?")}
    return results


def query_and_format(spec_input: dict) -> dict:
    result = query_candidate_parts(spec_input)
    if "error" in result:
        return result

    return {
        "normalized": result["normalized"],
        "high_confidence": [_format_candidate(c) for c in result["candidates"]["high_confidence"]],
        "medium_confidence": [_format_candidate(c) for c in result["candidates"]["medium_confidence"]],
        "low_confidence": [_format_candidate(c) for c in result["candidates"]["low_confidence"]],
        "needs_review": [_format_candidate(c) for c in result["candidates"]["needs_review"]],
        "stats": {
            "total_candidates": result["candidates"]["total_candidates"],
            "total_clean": result["candidates"]["total_clean"],
            "score_distribution": result["candidates"]["score_distribution"],
        },
    }
