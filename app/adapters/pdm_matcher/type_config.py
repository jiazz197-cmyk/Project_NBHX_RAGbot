"""
部件类型 → 查询约束配置（无 PARTID 版本 v2）

关键改进：
- must_kw 更精确，避免子串误匹配
- exclude_kw 覆盖更多误匹配场景
- narrow_attrs 提示用户提供哪些参数可大幅缩小候选
"""

TYPE_CONFIG = {
    "机架": {
        "must_kw": ["机架"],
        "exclude_kw": ["底座", "支撑脚", "追加工图", "机架底座", "机架支撑脚",
                       "机架部品", "机架运输"],
        "relevant_attrs": ["model", "degree", "surface", "material", "end_user_country", "detergent"],
        "narrow_attrs": ["surface", "degree", "detergent"],
        "partid_prefixes": ["50GB002"],
        "non_subject_exclude": ["底座", "支撑脚", "追加工图", "门板", "焊接件"],
    },
    "中心柱天板密封罩": {
        "must_kw": ["密封罩"],
        "must_kw_logic": "OR",
        "exclude_kw": [],
        "relevant_attrs": ["model", "detergent"],
        "narrow_attrs": ["model", "detergent"],
        "partid_prefixes": ["50GB001"],
        "non_subject_exclude": [],
    },
    "供料漏斗": {
        "must_kw": ["供料漏斗", "供料锥"],
        "must_kw_logic": "OR",
        "exclude_kw": ["支架", "供料漏斗支架", "供料锥支架", "底座"],
        "relevant_attrs": ["model", "surface"],
        "narrow_attrs": ["surface"],
        "partid_prefixes": ["50GB003"],
        "non_subject_exclude": ["支架"],
    },
    "供料锥支架": {
        "must_kw": ["供料锥支架", "供料漏斗支架"],
        "must_kw_logic": "OR",
        "exclude_kw": [],
        "relevant_attrs": ["model"],
        "narrow_attrs": [],
        "partid_prefixes": ["50GB004"],
        "non_subject_exclude": [],
    },
    "顶锥": {
        "must_kw": ["顶锥"],
        "exclude_kw": ["收集锥", "收集漏斗", "斜振盘用", "振动盘用"],
        "relevant_attrs": ["model", "degree", "surface", "lfp_type"],
        "narrow_attrs": ["degree", "surface"],
        "partid_prefixes": ["50GB006"],
        "non_subject_exclude": ["安装板"],
        # LFP type scoring is dynamic — see LFP_TYPE_KEYWORDS and engine.py
        "lfp_type_config": True,
    },
    "振动盘": {
        # 只用"振动盘"，不用"振盘"（太短，会匹配到"斜振盘用"等内容）
        "must_kw": ["振动盘"],
        "exclude_kw": ["焊接件", "振动盘焊接", "斜振盘", "振动盘用", "顶锥"],
        "relevant_attrs": ["model", "lfp_lip", "surface", "lfp_type", "leak_proof", "pim", "detergent"],
        "narrow_attrs": ["surface", "lfp_lip", "leak_proof", "pim", "detergent"],
        "partid_prefixes": ["50GB007"],
        "non_subject_exclude": ["料层调整环", "料层调整圈", "安装板", "顶锥"],
        # LFP type scoring is dynamic — see LFP_TYPE_KEYWORDS and engine.py
        "lfp_type_config": True,
    },
    "供料斗": {
        "must_kw": ["供料斗"],
        "exclude_kw": ["挂钩", "供料斗挂钩", "供料漏斗", "供料锥", "防碎装置", "防碎"],
        "relevant_attrs": ["model", "fb_spring", "fb_gate", "surface", "capacity", "pim", "leak_proof", "detergent"],
        "narrow_attrs": ["capacity", "fb_gate", "fb_spring", "surface", "pim", "leak_proof", "detergent"],
        "partid_prefixes": ["50GB010"],
        "non_subject_exclude": ["挂钩", "防碎"],
    },
    "计量斗": {
        "must_kw": ["计量斗", "称重斗"],
        "must_kw_logic": "OR",
        "exclude_kw": ["防碎", "防碎装置", "计量斗防碎", "供料斗", "驱动单元"],
        "relevant_attrs": ["model", "wb_spring", "wb_gate", "surface", "capacity", "pim", "leak_proof", "detergent"],
        "narrow_attrs": ["capacity", "wb_gate", "wb_spring", "surface", "pim", "leak_proof", "detergent"],
        "partid_prefixes": ["50GB011"],
        "non_subject_exclude": ["防碎", "驱动单元", "密封圈"],
    },
    "溜槽": {
        "must_kw": ["溜槽"],
        "must_kw_logic": "OR",
        "exclude_kw": ["盖板", "侧板", "防碎", "溜槽盖板", "溜槽侧板", "焊接件"],
        "relevant_attrs": ["model", "collating_chute", "degree", "surface", "baffle", "leak_proof", "detergent"],
        "narrow_attrs": ["degree", "surface", "collating_chute", "baffle", "detergent"],
        "partid_prefixes": ["50GB013", "50AA1302"],
        "non_subject_exclude": ["防碎装置", "防碎", "盖板", "侧板", "框架", "焊接件"],
    },
    "收集锥": {
        "must_kw": ["收集锥", "收集漏斗"],
        "must_kw_logic": "OR",
        "exclude_kw": ["顶锥", "防碎", "机架"],
        "relevant_attrs": ["model", "degree", "surface", "c_c", "cf_baffles", "detergent"],
        "narrow_attrs": ["degree", "surface", "detergent"],
        "partid_prefixes": ["50GB014", "50GB615"],
        "non_subject_exclude": ["防碎", "托架", "支架"],
    },
    "集合斗": {
        "must_kw": ["集合斗"],
        "exclude_kw": ["电缆", "支架", "防碎", "集合斗电缆", "集合斗支架", "集合斗防碎",
                       "电气原理图"],
        "relevant_attrs": ["model", "capacity", "degree", "c_c", "surface", "collection_bucket",
                          "collection_direction", "pim", "leak_proof", "duck_mouth", "detergent"],
        "narrow_attrs": ["capacity", "degree", "surface", "pim", "leak_proof", "detergent"],
        "partid_prefixes": ["50GB015", "50GB601", "50GB610", "50GB613", "50GB614", "50GB615", "50GB616"],
        "non_subject_exclude": ["防碎", "电缆", "支架", "电气原理图", "托架", "框架", "收集锥", "总装"],
    },
    "主振动器": {
        "must_kw": ["主振动器", "中心振动器", "中心振動器", "主振器"],
        "must_kw_logic": "OR",
        "exclude_kw": ["线性振动器", "板簧", "主振动器包装"],
        "relevant_attrs": ["model"],
        "narrow_attrs": [],
        "partid_prefixes": ["50GB008"],
        "non_subject_exclude": ["线", "连接线"],
    },
    "线性振动器": {
        "must_kw": ["线性振动器"],
        "exclude_kw": ["主振动器"],
        "relevant_attrs": ["model", "regulation"],
        "narrow_attrs": [],
        "partid_prefixes": ["50GB009"],
        "non_subject_exclude": ["线", "连接线"],
    },
    "驱动单元": {
        "must_kw": ["驱动单元"],
        "exclude_kw": ["运输衬垫"],
        "relevant_attrs": ["model", "regulation"],
        "narrow_attrs": ["regulation"],
        "partid_prefixes": ["50GB012"],
        "non_subject_exclude": ["运输衬垫", "螺栓", "拨杆", "密封圈"],
    },
    "配线单元": {
        "must_kw": ["配线单元"],
        "exclude_kw": [],
        "relevant_attrs": ["model", "cable_length", "cable_option", "regulation"],
        "narrow_attrs": ["cable_length"],
        "partid_prefixes": ["50GB024", "50GB034", "50CB0620"],
        "non_subject_exclude": [],
    },
    "包装": {
        "must_kw": ["包装", "木箱", "包装箱"],
        "must_kw_logic": "OR",
        "exclude_kw": ["包装箱内", "包装材料", "包装明细"],
        "relevant_attrs": ["model", "end_user_country", "detergent"],
        "narrow_attrs": ["end_user_country", "detergent"],
        "partid_prefixes": [],
        "non_subject_exclude": [],
    },
    "铭牌": {
        "must_kw": ["铭牌"],
        "exclude_kw": ["铭牌孔", "铭牌安装"],
        "relevant_attrs": ["name_plate", "model", "detergent"],
        "narrow_attrs": ["name_plate", "detergent"],
        "partid_prefixes": ["50GB098", "50JC0987"],
        "non_subject_exclude": ["门板", "铭牌孔"],
    },
    "防碎": {
        "must_kw": ["防碎装置", "防碎"],
        "must_kw_logic": "OR",
        "exclude_kw": [],
        "relevant_attrs": ["model"],
        "narrow_attrs": [],
        "partid_prefixes": ["50GB018"],
        "non_subject_exclude": [],
    },
    "料层调整圈": {
        "must_kw": ["料层调整圈", "料层调整环"],
        "must_kw_logic": "OR",
        "exclude_kw": [],
        "relevant_attrs": ["model", "pim"],
        "narrow_attrs": ["pim"],
        "partid_prefixes": ["50GB019"],
        "non_subject_exclude": [],
    },
    "记忆斗": {
        "must_kw": ["记忆斗"],
        "exclude_kw": ["防碎"],
        "relevant_attrs": ["model", "surface"],
        "narrow_attrs": ["surface"],
        "partid_prefixes": ["50GB017", "50GB030"],
        "non_subject_exclude": ["防碎", "密封圈", "拨杆", "木箱"],
    },
    "电气": {
        "must_kw": ["电气原理图", "电气布局", "电气部品", "电气元件"],
        "must_kw_logic": "OR",
        "exclude_kw": ["安装板", "安装架", "电气安装"],
        "relevant_attrs": ["model"],
        "narrow_attrs": [],
        "partid_prefixes": ["50GB023", "50GB025"],
        "non_subject_exclude": ["安装板", "安装架"],
    },
    "光电料位计": {
        "must_kw": ["光电料位计", "料位计部", "光电料位计部"],
        "must_kw_logic": "OR",
        "exclude_kw": [],
        "relevant_attrs": ["model", "sensor_type", "photoelectric_model", "detergent"],
        "narrow_attrs": ["sensor_type", "photoelectric_model", "detergent"],
        "partid_prefixes": ["50GB005"],
        "non_subject_exclude": [],
    },
}


# attr → CHINANAME 关键词映射
ATTR_KEYWORD_MAP = {
    "surface": {
        "flat": ["平板", "平/", "(平)", "(平/", "平板", "平"],
        "dimple": ["花纹板", "花纹", "花/", "(花/", "花纹", "水花", "米粒", "米花", "米粒花纹", "米粒型花纹"],
        "water_dimple": ["水花"],
        "rice_dimple": ["米粒", "米花", "米粒花纹", "米粒型花纹"],
        "pim": ["PIM"],
        "teflon": ["特氟龙"],
    },
    "fb_spring": {
        "yes": ["有弹簧"],
        "no": ["无弹簧"],
    },
    "fb_gate": {
        "single": ["单开门", "单开"],
        "double": ["双开门", "双开"],
    },
    "wb_spring": {
        "yes": ["有弹簧"],
        "no": ["无弹簧"],
    },
    "wb_gate": {
        "single": ["单开门", "单开"],
        "double": ["双开门", "双开"],
        "full_open": ["全开"],
    },
    "lfp_lip": {
        "flat_lip": ["平唇", "flat lip", "LIPS", "平板"],
        "lips": ["LIPS", "带LIPS"],
    },
    "lfp_type": {
        "SN": ["SN"],
        "RB": ["RB"],
        "V-shape": ["V型", "V-shape", "V shape", "V形", "V"],
        "U-shape": ["U型", "U-shape", "U shape", "U形", "U"],
        "Pasta": ["Pasta", "pasta", "意面"],
    },
    "regulation": {
        "india_wm": ["印度W&M", "W&M"],
        "tna": ["TNA"],
        "ce": ["欧洲MID"],
        "ul": ["UL"],
    },
    "detergent": {
        "yes": ["粉", "粉体", "含洗涤剂", "洗净"],
    },
    "collating_chute": {
        "fork": ["分叉溜槽"],
        "collection": ["集合溜槽"],
    },
    "material": {
        "sus": ["SUS", "不锈钢"],
        # IMPORTANT: "SS" in 机架 CHINANAME = carbon steel with painted surface
        # (e.g. "碳钢机架（0314S/SS/50°）"). "Painted on SS" → material=carbon_steel.
        "carbon_steel": ["碳钢", "Q235", "SS"],
    },
    "end_user_country": {
        "domestic": ["国内"],
        "export": ["出口"],
        "india": ["印度"],
        "korea": ["韩国", "Korea"],
    },
    "pim": {
        "yes": ["PIM"],
    },
    "leak_proof": {
        "B": ["B防", "B型防漏", "B type"],
        "A": ["A防", "A型防漏", "A type"],
        "yes": ["防漏"],
    },
    "duck_mouth": {
        "yes": ["鸭嘴"],
    },
    "silo": {
        "yes": ["silo", "Silo", "SILO"],
    },
    "baffle": {
        "yes": ["带隔板", "有挡板", "带挡板"],
        "no": ["无挡板", "无隔板"],
    },
    "collection_direction": {
        "single": ["单横", "单", "单方向", "1-way"],
        "double": ["双横", "双", "双方向", "2-way"],
    },
    "sensor_type": {
        "reflective": ["反射式"],
    },
    "photoelectric_model": {
        "WT24-2B220": ["WT24-2B220"],
    },
}


# ---------------------------------------------------------------------------
# Linear feeder pan (LFP) type → CHINANAME keyword scoring map.
#
# Dynamic: the spec sheet's Linear feeder pan value is standardized and
# matched against these keywords. Only the matching type gets the bonus;
# all other type keywords are excluded (mutual exclusion).
#
# Positive score values are per-keyword-hit; only applied when candidate
# CHINANAME contains the keyword AND the spec LFP type matches.
# ---------------------------------------------------------------------------
LFP_TYPE_KEYWORDS = {
    "SN": {"positive": ["SN"], "score": 5},
    "RB": {"positive": ["RB"], "score": 5},
    "V-shape": {"positive": ["V型", "V-shape", "V shape", "V形", "V"], "score": 5},
    "V": {"positive": ["V型", "V-shape", "V shape", "V形", "V"], "score": 5},  # OCR shorthand
    "U-shape": {"positive": ["U型", "U-shape", "U shape", "U形", "U"], "score": 5},
    "U": {"positive": ["U型", "U-shape", "U shape", "U形", "U"], "score": 5},  # OCR shorthand
    "Pasta": {"positive": ["Pasta", "pasta", "意面"], "score": 5},
    "No": {"positive": [], "score": 0},
}


# ---------------------------------------------------------------------------
# Country/region → CHINANAME keyword scoring map
#
# Purpose: adjust candidate scores based on country/region keywords found in
# CHINANAME, so that e.g. France (export) specs don't rank India-packaging
# candidates first. Operates on already-cached CHINANAME strings — no new DB
# queries. Applied only to COUNTRY_APPLICABLE_TYPES.
#
# positive:  keywords that boost score for this region
# negative:  keywords that penalize score for this region (wrong country)
# ---------------------------------------------------------------------------
COUNTRY_REGION_MAP = {
    "india": {
        "positive": ["印度"],
        "negative": ["海外", "出口", "国内", "非印度", "欧洲", "MID"],
        "pos_score": 8,
        "neg_score": -6,
    },
    "domestic": {
        "positive": ["国内"],
        "negative": ["海外", "出口", "印度", "非印度", "欧洲", "MID"],
        "pos_score": 5,
        "neg_score": -5,
    },
    "europe": {
        "positive": ["欧洲", "MID"],
        "negative": ["印度", "国内", "W&M"],
        "pos_score": 5,
        "neg_score": -5,
    },
    "korea": {
        "positive": ["韩国", "Korea"],
        "negative": ["印度", "国内", "欧洲", "MID", "W&M"],
        "pos_score": 5,
        "neg_score": -5,
    },
    "export": {
        "positive": ["海外", "出口"],
        "negative": ["国内", "印度", "非印度"],
        "pos_score": 5,
        "neg_score": -5,
    },
}

# Component types where country/region scoring is applied.
# Only these types have country-specific keywords in CHINANAME (verified via DB).
# - 驱动单元: uses REGULATION_SCORING primarily (regulation param)
# - 包装: uses COUNTRY_REGION_MAP primarily (end_user_country param)
# - 机架/铭牌/电气/配线单元: weak reference only
COUNTRY_APPLICABLE_TYPES = {"包装", "驱动单元", "机架", "铭牌", "电气", "配线单元", "线性振动器"}

# Regulation keyword scoring — applies ONLY to 驱动单元.
# Regulation is a spec parameter that describes certification requirements
# (India W&M, European MID, TNA). These keywords only appear in 驱动单元
# and BOM spec-header records (50GB028xxx), not in packaging/racks.
REGULATION_SCORING = {
    "india_wm": {
        "positive": ["印度W&M", "W&M"],
        "negative": ["欧洲MID", " MID"],  # " MID" with leading space to avoid matching "...W&M/MID"
        "pos_score": 8,
        "neg_score": -6,
    },
    "ce": {  # Generic CE marking — NOT equivalent to European MID
        "positive": ["欧洲MID"],
        "negative": ["印度W&M", "W&M"],
        "pos_score": 6,
        "neg_score": -8,
    },
    "tna": {
        "positive": ["TNA"],
        "negative": [],
        "pos_score": 5,
        "neg_score": 0,
    },
    "ul": {  # UL certification (North America)
        "positive": ["UL"],
        # DB-verified: UL spec package (50GB0282005) has zero BOM links to any
        # drive unit. Records with W&M/MID/印度/欧洲 are NOT UL-compatible and
        # should be penalized. Without this, UL specs match Europe/India records.
        "negative": ["印度", "W&M", "欧洲", "MID"],
        "pos_score": 4,
        "neg_score": -5,
    },
}

# ---------------------------------------------------------------------------
# Drive unit kg inference rules — DB-verified from 50GB028→50GB012 BOM links.
#
# Each entry maps (short_model, regulation) → {kg, reliability, evidence}.
# These are SCORING hints only (never hard filters, never new recall channels).
#
# Reliability:
#   high   — ≥3 independent 50GB028→50GB012 BOM links or ≥5 CHINANAME records
#   medium — ≥1 BOM link confirmed
#   low    — inferred from similar models, less direct evidence
#
# NO rules for:
#   - 0314S: both 4kg and 8kg exist for BOTH IndiaW&M and EuropeMID
#   - UL: zero BOM links from UL spec package; no UL-specific drive units exist
# ---------------------------------------------------------------------------
DRIVE_UNIT_KG_RULES = {
    # 0310S → always 4kg (3 spec packages: IndiaW&M x1, EuropeMID x2)
    ("0310S", "india_wm"): {"kg": "4kg", "reliability": "high",
                            "evidence": "50GB0282018→50GB0122002"},
    ("0310S", "ce"):       {"kg": "4kg", "reliability": "high",
                            "evidence": "50GB0282024/25→50GB0122002/33"},
    # 0320S → 4kg (1 spec package, plus CHINANAME record 50GB0122057)
    ("0320S", "india_wm"): {"kg": "4kg", "reliability": "medium",
                            "evidence": "50GB0282016→50GB0122002"},
    ("0320S", "ce"):       {"kg": "4kg", "reliability": "medium",
                            "evidence": "50GB0282016→50GB0122002"},
    # 0114S → 2kg (inferred from 50GB0122058 CHINANAME, not from BOM link)
    ("0114S", "india_wm"): {"kg": "2kg", "reliability": "low",
                            "evidence": "50GB0122058 CN: 驱动单元（0114S/2kg/印度、欧洲规格秤用）"},
    ("0114S", "ce"):       {"kg": "2kg", "reliability": "low",
                            "evidence": "50GB0122058 CN: 驱动单元（0114S/2kg/印度、欧洲规格秤用）"},
    # 0110S → 2kg (inferred from 50GB0122066 CHINANAME)
    ("0110S", "india_wm"): {"kg": "2kg", "reliability": "low",
                            "evidence": "50GB0122066 CN: 驱动单元（0110S/2kg/印度、欧洲规格秤用）"},
    ("0110S", "ce"):       {"kg": "2kg", "reliability": "low",
                            "evidence": "50GB0122066 CN: 驱动单元（0110S/2kg/印度、欧洲规格秤用）"},
}


DEPRECATION_KEYWORDS = {
    "replaced": ["暂停使用", "使用C版", "使用D版", "改用"],
    "disabled": ["禁用", "禁：", "禁用：", "停用", "停用：", "废止"],
    "expired": ["不再使用", "废弃"],
}
