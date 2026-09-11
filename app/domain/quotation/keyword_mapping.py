"""
关键词映射模块
用于对查询关键词进行转换处理。
"""
from __future__ import annotations

import re
from typing import Callable, Dict, List


# ------------------------------------------------------------
# 关键词映射配置
# ------------------------------------------------------------
# 通用模式映射规则：(正则模式, 替换模板)
KEYWORD_PATTERN_MAPPINGS: List[tuple] = [
    # 数字-degree 转换为 数字°
    (r"(\d+)-degree", r"\1°"),
]

# 机架产品映射
MACHINE_FRAME_MAPPINGS: Dict[str, str | List[str]] = {
    "common bed": "机架",
    "flat": "平",
    "flat lip": "平",
    "flatlip": "平",
    "bent lip": "翘",
    "bentlip": "翘",
    "emboss": "花",
    "single": ["", "单"],
    "double": ["两", "双"],
    "detergent": "粉",
    "detergent:no": "",
    # Common bed 材质：Painted on 前缀会在预处理里删除。
    "ss": ["SS", "碳钢"],
    "sus": ["SUS", "不锈钢"],
    "sus304": ["SUS", "不锈钢"],
    "sus 304": ["SUS", "不锈钢"],
    "sus-304": ["SUS", "不锈钢"],
    "sus / with air purge": ["SUS", "不锈钢"],
    # 国家/地区缩写
    "cn": "中",
    "china": "中",
    "chinese": "中",
    "us": "美",
    "usa": "美",
    "america": "美",
    "american": "美",
    "eu": "欧",
    "europe": "欧",
    "european": "欧",
    "jp": "日",
    "japan": "日",
    "japanese": "日",
    "kr": "韩",
    "korea": "韩",
    "korean": "韩",
    "in": "印",
    "india": "印",
    "indian": "印",
    "uk": "英",
    "gb": "英",
    "britain": "英",
    "british": "英",
    "de": "德",
    "germany": "德",
    "german": "德",
    "fr": "法",
    "france": "法",
    "french": "法",
    "it": "意",
    "italy": "意",
    "italian": "意",
    "tw": "台",
    "taiwan": "台",
    "hk": "港",
    "hongkong": "港",
    "hong kong": "港",
    "sg": "新",
    "singapore": "新",
    "au": "澳",
    "australia": "澳",
    "australian": "澳",
    "ca": "加",
    "canada": "加",
    "canadian": "加",
    "th": "泰",
    "thailand": "泰",
    "thai": "泰",
    "my": "马",
    "malaysia": "马",
    "malaysian": "马",
    "id": "印尼",
    "indonesia": "印尼",
    "indonesian": "印尼",
    "ph": "菲",
    "philippines": "菲",
    "philippine": "菲",
    "vn": "越",
    "vietnam": "越",
    "vietnamese": "越",
    "ru": "俄",
    "russia": "俄",
    "russian": "俄",
    "br": "巴",
    "brazil": "巴",
    "brazilian": "巴",
    "mx": "墨",
    "mexico": "墨",
    "mexican": "墨",
}

# 供料漏斗/供料锥映射
HOPPER_MAPPINGS: Dict[str, str | List[str]] = {
    # 形状类型
    "flat": "平",
    "flat lip": "平",
    "flatlip": "平",
    "bent lip": "翘",
    "bentlip": "翘",
    "emboss": "花",
    # 分流相关
    "single": ["", "单"],
    "divided two": ["两", "双"],
    "dividedtwo": ["两", "双"],
    "divided-two": ["两", "双"],
    "两分式": "两分式",
    "diverter": "分流器",
    "分流器": "分流器",
    "silo": "SILO",
    "octopus style": "分流槽体",
    "octopusstyle": "分流槽体",
    "octopus-style": "分流槽体",
    "分流槽体": "分流槽体",
    # 传感器
    "photo sensor": "光电传感器",
    "photosensor": "光电传感器",
    "photo-sensor": "光电传感器",
    "photo": "光电",
    "光电": "光电",
    "光电传感器": "光电传感器",
    # 型号标识
    "sn": "SN",
    "v": "V",
    "rb": "RB",
    "pasta": "PASTA",
    # 材质/特性
    "anti breakage": "防碎",
    "anti-breakage": "防碎",
    "防碎": "防碎",
    "red silicone": "红硅",
    "红硅": "红硅",
}

# 顶锥映射
TOP_CONE_MAPPINGS: Dict[str, str | List[str]] = {
    # 形状类型
    "flat": "平",
    "flat lip": "平",
    "flatlip": "平",
    "bent lip": "翘",
    "bentlip": "翘",
    "emboss": "花",
    # 规格类型
    "single": ["", "单"],
    "twin": ["两", "双"],
    "detergent": "粉",
    "detergent:no": [],
    # 型号标识
    "sn": "SN",
    "v": "V",
    "rb": "RB",
    "pasta": "PASTA",
    # 材质/特性
    "红硅": "红硅",
    "pim": "PIM",
    "防碎": "防碎",
    "防漏": "防漏",
}

# 振动盘映射
VIBRATING_PLATE_MAPPINGS: Dict[str, str | List[str]] = {
    # 形状类型
    "flat": "平",
    "flat lip": "平",
    "flatlip": "平",
    "bent lip": "翘",
    "bentlip": "翘",
    "emboss": "花",
    # 规格类型
    "single": ["", "单"],
    "twin": ["两", "双"],
    "detergent": ["粉", "粉体"],
    "detergent:no": [],
    # 型号标识
    "sn": "SN",
    "v": "V",
    "rb": "RB",
    "pasta": "PASTA",
    # 材质/特性
    "红": "红",
    "pim": "PIM",
    "防碎": "防碎",
    "防漏": "防漏",
}

# 供料斗映射
FEEDING_HOPPER_MAPPINGS: Dict[str, str | List[str]] = {
    # 形状类型
    "flat": "平",
    "flat lip": "平",
    "flatlip": "平",
    "bent lip": "翘",
    "bentlip": "翘",
    "emboss": "花",
    # 规格类型
    "single": "单",
    "twin": "双",
    "detergent": "粉",
    "detergent:no": [],
    "fb spring": ["弹簧", "!无弹簧"],
    "fbspring": ["弹簧", "!无弹簧"],
    "fb spring:no": ["", "无弹簧"],
    "fbspring:no": ["", "无弹簧"],
    "single door": "单",
    "singledoor": "单",
    "double door": "双",
    "doubledoor": "双",
    "z-edge": "Z型门边",
    "z edge": "Z型门边",
    "z型门边": "Z型门边",
    # 型号标识
    "sn": "SN",
    "v": "V",
    "rb": "RB",
    "pasta": "PASTA",
    # 材质/特性
    "红硅": "红硅",
    "红": "红",
    "pim": "PIM",
    "防碎": "防碎",
    "防漏": "防漏",
    "a型防漏": "A型防漏",
    "b型防漏": "B型防漏",
    "无夹具": "无夹具",
    "带夹具": "带夹具",
}

# 计量斗映射
WEIGH_BUCKET_MAPPINGS: Dict[str, str | List[str]] = {
    # 形状类型
    "flat": "平",
    "flat lip": "平",
    "flatlip": "平",
    "bent lip": "翘",
    "bentlip": "翘",
    "emboss": "花",
    # 规格类型
    "single": "单",
    "twin": "双",
    "detergent": "粉",
    "detergent:no": [],
    "wb spring": ["弹簧", "!无弹簧"],
    "wbspring": ["弹簧", "!无弹簧"],
    "wb spring:no": ["", "无弹簧"],
    "wbspring:no": ["", "无弹簧"],
    "single door": "单",
    "singledoor": "单",
    "double door": "双",
    "doubledoor": "双",
    "z-edge": "Z型门边",
    "z edge": "Z型门边",
    "z型门边": "Z型门边",
    # 型号标识
    "sn": "SN",
    "v": "V",
    "rb": "RB",
    "pasta": "PASTA",
    # 材质/特性
    "红硅": "红硅",
    "红": "红",
    "pim": "PIM",
    "防碎": "防碎",
    "防漏": "防漏",
    "a型防漏": "A型防漏",
    "b型防漏": "B型防漏",
    "a防": "A防",
    "b防": "B防",
    "无夹具": "无夹具",
    "带夹具": "带夹具",
}

# 驱动单元映射
DRIVE_UNIT_MAPPINGS: Dict[str, str | List[str]] = {
    # 法规
    "india w&m": "印",
    "europe mid": "欧",
    # 兼容常见写法
    "w&m": "印",
    "mid": "欧",
    "india": "印",
    "europe": "欧",
}

# 溜槽部/溜槽映射
CHUTE_MAPPINGS: Dict[str, str | List[str]] = {
    # 形状类型
    "flat": "平",
    "flat lip": "平",
    "flatlip": "平",
    "bent lip": "翘",
    "bentlip": "翘",
    "emboss": "花",
    # 规格类型
    "detergent": "粉",
    "detergent:no": [],
    "cc baffles": ["挡板","!无挡板"],
    "ccbaffles": ["挡板","!无挡板"],
    "cc baffles:no": ["无挡板", ""],
    "ccbaffles:no": ["无挡板", ""],
    # 材质/特性
    "红": "红",
    "pim": "PIM",
    "无夹具": "无夹具",
    "带夹具": "带夹具",
    # 常见完整短语
    "0114s溜槽双开门料斗": "0114S溜槽双开门料斗",
}

# 收集锥/集合漏斗映射
COLLECTING_CONE_MAPPINGS: Dict[str, str | List[str]] = {
    # 形状类型
    "flat": "平",
    "flat lip": "平",
    "flatlip": "平",
    "bent lip": "翘",
    "bentlip": "翘",
    "emboss": "花",
    # 规格类型
    "detergent": "粉",
    "detergent:no": [],
    "cf baffles": ["挡板","!无挡板"],
    "cfbaffles": ["挡板","!无挡板"],
    "cf baffles:no": ["无挡板", ""],
    "cfbaffles:no": ["无挡板", ""],
    "c-c": "C-C",
    # 材质/特性
    "红": "红",
    "pim": "PIM",
    "配鸭嘴式集合斗": "配鸭嘴式集合斗",
}

# 本体电气元件/电气映射
ELECTRICAL_COMPONENT_MAPPINGS: Dict[str, str | List[str]] = {
    "蔬菜秤": "蔬菜秤",
}

# 配线单元映射
WIRING_UNIT_MAPPINGS: Dict[str, str | List[str]] = {
    "8m": "标准",
    "ul": "UL",
    "ul规格": "UL",
    "蔬菜秤": "蔬菜秤",
}

# 主振动器/中心振动器映射
VIBRATOR_UNIT_MAPPINGS: Dict[str, str | List[str]] = {
    "single": ["", "单"],
    "double": "双",
    "divided two": "双",
    "dividedtwo": "双",
    "ul": "UL",
}

# 线性振动器映射
LINEAR_VIBRATOR_MAPPINGS: Dict[str, str | List[str]] = {
    "ul": "UL",
}

# 中心柱天板密封罩映射
CENTER_COLUMN_SEAL_MAPPINGS: Dict[str, str | List[str]] = {
    "single": ["", "单"],
    "double": "双",
    "divided two": "双",
    "dividedtwo": "双",
    "detergent": "粉",
    "detergent:no": [],
}

# 供料锥支架映射
TOP_CONE_BRACKET_MAPPINGS: Dict[str, str | List[str]] = {
    "single": ["无", "单"],
    "twin": ["两", "双"],
    "double": "双",
    "divided two": ["两", "双"],
    "dividedtwo": ["两", "双"],
    "divided-two": ["两", "双"],
    "diverter": "分流器",
    "silo": "筒仓式",
    "photo sensor": "光电传感器",
    "photosensor": "光电传感器",
    "photo-sensor": "光电传感器",
}

# 包装映射
PACKAGING_MAPPINGS: Dict[str, str | List[str]] = {
    "india": ["印","!非印度"],
    "china": "国内",
    "non-india": ["国外", "出口", "海外"],
    "detergent": "粉",
    "detergent:no": [],
    "蔬菜秤": "蔬菜秤",
}

# 集合斗映射
COLLECTION_BUCKET_MAPPINGS: Dict[str, str | List[str]] = {
    # 形状类型
    "flat": "平",
    "flat lip": "平",
    "flatlip": "平",
    "bent lip": "翘",
    "bentlip": "翘",
    "emboss": "花",
    # 方向/结构
    "single": ["", "单"],
    "twin": "双",
    "double": "双",
    "divided two": "双",
    "dividedtwo": "双",
    "divided-two": "双",
    "1-way": ["单", "单向"],
    "2-way": ["双", "双向"],
    "single-way": ["单", "单向"],
    "double-way": ["双", "双向"],
    # 机构/驱动
    "motor": "电机",
    "pneumatic": "气缸",
    "side stroke": ["横拉", "横"],
    "center drive": "中心驱动",
    "center double drive": "中心双驱动",
    "单横": "单横",
    "双横": "双横",
    # 规格与参数
    "3l": "3L",
    "c-c": "C-C",
    "degree": "°",
    "detergent": "粉",
    "detergent:no": [],
    # 材质/特性
    "z型门边": "Z型门边",
    "鸭嘴式": "鸭嘴式",
    "红": "红",
    "pim": "PIM",
    "a型防漏": "A型防漏",
    "b型防漏": "B型防漏",
    "无夹具": "无夹具",
    "带夹具": "带夹具",
}

# 记忆斗映射
MEMORY_BUCKET_MAPPINGS: Dict[str, str | List[str]] = {
    # 形状类型
    "flat": "平",
    "flat lip": "平",
    "flatlip": "平",
    "bent lip": "翘",
    "bentlip": "翘",
    "emboss": "花",
    # 规格类型
    "memory spring": ["弹簧", "!无弹簧"],
    "memoryspring": ["弹簧", "!无弹簧"],
    "memory spring:no": ["", "无弹簧"],
    "memoryspring:no": ["", "无弹簧"],
    "single door": "单开",
    "singledoor": "单开",
    "double door": "双开",
    "doubledoor": "双开",
    # 材质/特性
    "z型门边": "Z型门边",
    "红": "红",
    "pim": "PIM",
    "a型防漏": "A型防漏",
    "b型防漏": "B型防漏",
    "无夹具": "无夹具",
    "带夹具": "带夹具",
}

# 产品防堵防碎/防碎映射
ANTI_BLOCK_BREAK_MAPPINGS: Dict[str, str | List[str]] = {
    "fixed": "固定式",
    "固定式": "固定式",
    "bayonet": "卡口式",
    "movable": "上下驱动式",
    "上下驱动式": "上下驱动式",
}

# 料层调整圈映射
LAYER_ADJUSTMENT_RING_MAPPINGS: Dict[str, str | List[str]] = {
    "整体式": "整体式",
    "个别式": "个别式",
    "silo": "筒仓式",
    "pasta": "PASTA",
    "不锈钢挡板": "不锈钢挡板",
    "红": "红",
    "pim": "PIM",
}

# 产品类型关键词识别
PRODUCT_TYPE_KEYWORDS: Dict[str, str | List[str]] = {
    "机架": "机架",
    "common bed": "机架",
    "供料漏斗": "供料漏斗",
    "顶锥": "顶锥",
    "振动盘": "振动盘",
    "供料斗": "供料斗",
    "计量斗": "计量斗",
    "驱动单元": "驱动单元",
    "溜槽": "溜槽",
    "溜槽部": ["溜槽", "溜槽部"],
    "收集锥": "收集锥",
    "集合漏斗": "收集锥",
    "本体电气元件": "电气",
    "配线单元": "配线单元",
    "主振动器": "主振动器",
    "中心振动器": "中心振动器",
    "线性振动器": "线性振动器",
    "中心柱天板密封罩": "中心柱天板密封罩",
    "供料锥支架": "供料锥支架",
    "包装": "包装",
    "集合斗": "集合斗",
    "记忆斗": "记忆斗",
    "memory bucket": "记忆斗",
    "产品防堵防碎": "产品防堵防碎",
    "防碎": "产品防堵防碎",
    "料层调整圈": "料层调整圈",
}

PRODUCT_TYPE_KEYWORD_ITEMS = sorted(
    PRODUCT_TYPE_KEYWORDS.items(),
    key=lambda item: len(item[0]),
    reverse=True,
)


# ------------------------------------------------------------
# 产品类型属性保留配置（白名单）
# ------------------------------------------------------------
# 白名单 = 必要参数（含 model）
# 只传递白名单中的属性，其他属性忽略
# 如果产品类型不在配置中，则保留所有属性
PRODUCT_TYPE_ATTR_WHITELIST: Dict[str, List[str]] = {
    # 机架: 必要参数 = Model + Common bed
    "机架": [
        "model",
        "common bed", "commonbed",
    ],
    # 供料漏斗: 必要参数 = Model
    "供料漏斗": [
        "model",
    ],
    # 顶锥: 必要参数 = Model
    "顶锥": [
        "model",
    ],
    # 振动盘: 必要参数 = Model + Linear feeder pan
    "振动盘": [
        "model",
        "linear feeder pan", "linearfeederpan",
    ],
    # 供料斗: 必要参数 = Model + FB spring + Surface + Remarks
    "供料斗": [
        "model",
        "fb spring", "fbspring", "fb弹簧",
        "surface",
        "remarks",
    ],
    # 计量斗: 必要参数 = Model + WB spring + Surface + Remarks
    "计量斗": [
        "model",
        "wb spring", "wbspring", "wb弹簧",
        "surface",
        "remarks",
    ],
    # 溜槽: 必要参数 = Model
    "溜槽": [
        "model",
    ],
    "溜槽部": [
        "model",
    ],
    # 收集锥: 必要参数 = Model + Collating funnel Degree
    "收集锥": [
        "model",
        "collating funnel degree", "collatingfunneldegree",
    ],
    "集合漏斗": [
        "model",
        "collating funnel degree", "collatingfunneldegree",
    ],
    # 电气: 必要参数 = Model
    "电气": [
        "model",
    ],
    "本体电气元件": [
        "model",
    ],
    # 配线单元: 必要参数 = Model
    "配线单元": [
        "model",
    ],
    # 主振动器 / 中心振动器: 必要参数 = Model
    "主振动器": [
        "model",
    ],
    "中心振动器": [
        "model",
    ],
    # 线性振动器: 必要参数 = Model
    "线性振动器": [
        "model",
    ],
    # 中心柱天板密封罩: 必要参数 = Model + Detergent
    "中心柱天板密封罩": [
        "model",
        "detergent",
    ],
    # 包装: 必要参数 = Model（Detergent=Yes 时额外加"粉"）
    "包装": [
        "model",
    ],
    # 集合斗: 必要参数 = Model + Collection bucket容量
    "集合斗": [
        "model",
        "collection bucket", "collectionbucket", "collection bucket容量",
    ],
}


def get_attr_whitelist(product_type: str) -> List[str] | None:
    """
    获取产品类型的属性白名单（即必要参数）。

    Args:
        product_type: 产品类型名称

    Returns:
        白名单列表，返回 None 表示保留所有属性
    """
    return PRODUCT_TYPE_ATTR_WHITELIST.get(product_type)


PRODUCT_TYPE_MAPPING_REGISTRY: List[tuple[tuple[str, ...], Dict[str, str | List[str]]]] = [
    (("机架",), MACHINE_FRAME_MAPPINGS),
    (("供料漏斗", "供料锥"), HOPPER_MAPPINGS),
    (("顶锥",), TOP_CONE_MAPPINGS),
    (("振动盘",), VIBRATING_PLATE_MAPPINGS),
    (("供料斗",), FEEDING_HOPPER_MAPPINGS),
    (("计量斗",), WEIGH_BUCKET_MAPPINGS),
    (("驱动单元",), DRIVE_UNIT_MAPPINGS),
    (("溜槽", "溜槽部"), CHUTE_MAPPINGS),
    (("收集锥", "集合漏斗"), COLLECTING_CONE_MAPPINGS),
    (("电气", "本体电气元件"), ELECTRICAL_COMPONENT_MAPPINGS),
    (("配线单元",), WIRING_UNIT_MAPPINGS),
    (("主振动器", "中心振动器"), VIBRATOR_UNIT_MAPPINGS),
    (("线性振动器",), LINEAR_VIBRATOR_MAPPINGS),
    (("中心柱天板密封罩",), CENTER_COLUMN_SEAL_MAPPINGS),
    (("供料锥支架",), TOP_CONE_BRACKET_MAPPINGS),
    (("包装",), PACKAGING_MAPPINGS),
    (("集合斗",), COLLECTION_BUCKET_MAPPINGS),
    (("记忆斗",), MEMORY_BUCKET_MAPPINGS),
    (("产品防堵防碎",), ANTI_BLOCK_BREAK_MAPPINGS),
    (("料层调整圈",), LAYER_ADJUSTMENT_RING_MAPPINGS),
]

ADW_A_PREFIX_STRIP_TYPES = (
    "机架",
    "供料漏斗",
    "供料锥",
    "顶锥",
    "振动盘",
    "供料斗",
    "计量斗",
    "驱动单元",
    "溜槽",
    "溜槽部",
    "收集锥",
    "集合漏斗",
    "电气",
    "本体电气元件",
    "配线单元",
    "主振动器",
    "中心振动器",
    "线性振动器",
    "中心柱天板密封罩",
    "供料锥支架",
    "包装",
    "集合斗",
    "记忆斗",
    "产品防堵防碎",
    "料层调整圈",
)


def _contains_any_keyword(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def detect_product_type(keywords: List[str]) -> List[str]:
    """
    从关键词列表中检测产品类型。

    Args:
        keywords: 关键词列表

    Returns:
        检测到的产品类型列表（去重），未检测到则返回空列表
    """
    detected_types: List[str] = []
    for keyword in keywords:
        keyword_str = str(keyword).strip()
        keyword_str_lower = keyword_str.lower()
        for type_keyword, product_type in PRODUCT_TYPE_KEYWORD_ITEMS:
            if type_keyword in keyword_str or type_keyword.lower() in keyword_str_lower:
                if isinstance(product_type, list):
                    detected_types.extend([str(item).strip() for item in product_type if str(item).strip()])
                else:
                    detected_types.append(str(product_type).strip())
                break  # 匹配到一个关键词后跳出内层循环
    # 去重并保持顺序
    return list(dict.fromkeys(detected_types))


def _get_exact_mappings(product_type: str | List[str]) -> Dict[str, str | List[str]]:
    merged_mappings: Dict[str, str | List[str]] = {}

    # 支持传入 str 或 List[str]
    product_type_str = " ".join(product_type) if isinstance(product_type, list) else product_type

    for product_type_keys, product_mappings in PRODUCT_TYPE_MAPPING_REGISTRY:
        if _contains_any_keyword(product_type_str, product_type_keys):
            merged_mappings.update(product_mappings)
            return merged_mappings

    merged_mappings.update(MACHINE_FRAME_MAPPINGS)
    merged_mappings.update(HOPPER_MAPPINGS)
    return merged_mappings


def _apply_pattern_mappings(keyword: str) -> str:
    result = str(keyword).strip()
    for pattern, replacement in KEYWORD_PATTERN_MAPPINGS:
        result = re.sub(pattern, replacement, result)
    return result


def _expand_model_series_keyword(keyword: str) -> List[str]:
    """Model 扩展：03/5 系列关键词扩展。"""
    text = str(keyword).strip()
    if not text:
        return []

    upper_text = text.upper()
    candidates: List[str] = []

    if upper_text.startswith("03"):
        candidates.extend([text, "03"])
    elif upper_text.startswith("5"):
        candidates.extend([text, "5"])

    return list(dict.fromkeys([item for item in candidates if item]))


def _expand_drive_unit_model_keyword(keyword: str) -> List[str]:
    """驱动单元 Model 扩展：01/03/5 系列关键词扩展（去 14S 后再扩展）。"""
    text = str(keyword).strip()
    if not text:
        return []

    # 例如 0314S -> 03, 0114S -> 01, 514 -> 5
    base = re.sub(r"(?i)14s?$", "", text)
    candidates: List[str] = [text]
    if base and base != text:
        candidates.append(base)

    upper_base = base.upper()
    if upper_base.startswith("01"):
        candidates.extend(["01"])
    elif upper_base.startswith("03"):
        candidates.extend(["03"])
    elif upper_base.startswith("5"):
        candidates.extend(["5"])

    return list(dict.fromkeys([item for item in candidates if item]))


def _expand_collection_bucket_model_keyword(keyword: str) -> List[str]:
    """集合斗 Model 扩展：提取数字并按 03/01/5 系列扩展。"""
    text = str(keyword).strip()
    if not text:
        return []

    # 删除末尾字母后提取数字，如 0314S -> 0314
    numeric_text = re.sub(r"(?i)[a-z]+$", "", text)
    numeric_text = re.sub(r"[^0-9]", "", numeric_text)
    if not numeric_text:
        return [text]

    candidates: List[str] = [numeric_text]
    if numeric_text.startswith("03"):
        candidates.extend(["03", "03系列"])
    elif numeric_text.startswith("01"):
        candidates.extend(["01", "01系列"])
    elif numeric_text.startswith("5"):
        candidates.extend(["5", "5系列"])

    return list(dict.fromkeys([item for item in candidates if item]))


def _expand_memory_bucket_model_keyword(keyword: str) -> List[str]:
    """记忆斗 Model 扩展：删除前缀/后缀后，03/01/5 规则扩展。"""
    text = str(keyword).strip()
    if not text:
        return []

    numeric_text = re.sub(r"(?i)[a-z]+$", "", text)
    numeric_text = re.sub(r"[^0-9]", "", numeric_text)
    if not numeric_text:
        return [text]

    candidates: List[str] = [numeric_text]
    if numeric_text.startswith("03"):
        candidates.extend(["03", "03系列"])
    elif numeric_text.startswith("01"):
        candidates.extend(["01", "01系列"])
    elif numeric_text.startswith("5"):
        candidates.extend(["5", "5系列"])

    return list(dict.fromkeys([item for item in candidates if item]))


def _expand_anti_block_break_model_keyword(keyword: str) -> List[str]:
    """产品防堵防碎 Model 扩展：03 开头时补充系列。"""
    text = str(keyword).strip()
    if not text:
        return []

    candidates: List[str] = [text]
    base = re.sub(r"(?i)14s$", "", text)
    if base and base != text:
        candidates.append(base)
    if str(base).startswith("03"):
        candidates.extend(["03", "03系列"])

    return list(dict.fromkeys([item for item in candidates if item]))


def _expand_collecting_cone_c_c_keyword(keyword: str) -> List[str]:
    """收集锥 C-C 参数：兼容 C-C=330 与 C-C330 两种写法。"""
    text = str(keyword).strip()
    match = re.search(r"(?i)c\s*-\s*c\s*=?\s*(\d+(?:\.\d+)?)", text)
    if not match:
        return []

    value = match.group(1)
    return [f"C-C={value}", f"C-C{value}"]


def _expand_vibrator_model_keyword(keyword: str) -> List[str]:
    """主振动器/中心振动器 Model 特殊规则。"""
    text = str(keyword).strip()
    if not text:
        return []

    upper_text = text.upper()
    if "0314S" in upper_text:
        return ["YP-3NA", "GB01602G0458", "YP-3NA GB01602G0458"]

    return [text]


def _normalize_keyword_for_product_type(keyword: str, product_type_str: str) -> str:
    """按产品类型做关键词预处理。"""
    text = str(keyword).strip()

    if _contains_any_keyword(product_type_str, ADW_A_PREFIX_STRIP_TYPES):
        # Model: ADW-A-0314S -> 0314S
        text = re.sub(r"(?i)^adw-a-", "", text)

    if "收集锥" in product_type_str or "集合漏斗" in product_type_str:
        # Model: 去掉后缀字母 S（如 0314S -> 0314）
        text = re.sub(r"(?i)s$", "", text)

    if "供料斗" in product_type_str or "计量斗" in product_type_str:
        # Model: ADW-514 -> 514
        text = re.sub(r"(?i)^adw-", "", text)

    if "机架" in product_type_str:
        # Common bed: Painted on SS/SUS -> SS/SUS
        text = re.sub(r"(?i)^painted\s*on\s*", "", text)

    if "机架" in product_type_str or "顶锥" in product_type_str:
        # 删除不确定标记，如 SUS(?) / SUS（？）
        text = re.sub(r"[（(]\s*[?？]\s*[)）]", "", text)
        text = text.replace("?", "").replace("？", "")

    return text.strip()


MODEL_EXPANDER_REGISTRY: List[tuple[tuple[str, ...], Callable[[str], List[str]]]] = [
    (("供料斗", "计量斗"), _expand_model_series_keyword),
    (("收集锥", "集合漏斗"), _expand_collecting_cone_c_c_keyword),
    (("驱动单元",), _expand_drive_unit_model_keyword),
    (("主振动器", "中心振动器"), _expand_vibrator_model_keyword),
    (("集合斗",), _expand_collection_bucket_model_keyword),
    (("记忆斗",), _expand_memory_bucket_model_keyword),
    (("产品防堵防碎",), _expand_anti_block_break_model_keyword),
]


def expand_keyword_mapping(keyword: str, product_type: str | List[str] = "") -> List[str]:
    """
    对单个关键词应用映射规则，并返回所有可查询候选词。

    当一个输入词需要映射成多个同义词时，返回多个结果，
    调用方可据此发起多次查询后再汇总去重。
    """
    raw_keyword = str(keyword).strip()
    if not raw_keyword:
        return []

    product_type_str = " ".join(product_type) if isinstance(product_type, list) else str(product_type)
    normalized_keyword = _normalize_keyword_for_product_type(raw_keyword, product_type_str)

    result_lower = normalized_keyword.lower()
    exact_mappings = _get_exact_mappings(product_type)

    if result_lower in exact_mappings:
        mapped_value = exact_mappings[result_lower]
        if isinstance(mapped_value, list):
            candidates = [str(item).strip() for item in mapped_value if str(item).strip()]
            return list(dict.fromkeys(candidates))
        mapped_text = str(mapped_value).strip()
        return [mapped_text] if mapped_text else []

    for product_type_keys, expander in MODEL_EXPANDER_REGISTRY:
        if _contains_any_keyword(product_type_str, product_type_keys):
            model_candidates = expander(normalized_keyword)
            if model_candidates:
                return model_candidates

    return [_apply_pattern_mappings(normalized_keyword)]


def apply_keyword_mapping(keyword: str, product_type: str | List[str] = "") -> str:
    """
    对单个关键词应用映射规则。

    规则：
    1. 根据产品类型选择对应的映射字典
    2. 完全匹配：如果关键词完全匹配映射字典中的键，则替换
    3. 模式匹配：如果关键词匹配 KEYWORD_PATTERN_MAPPINGS 中的正则模式，则替换

    Args:
        keyword: 待映射的关键词
        product_type: 产品类型（str 或 List[str]），如 "机架"、"供料漏斗"、["溜槽", "溜槽部"] 等

    Returns:
        映射后的关键词

    Examples:
        >>> apply_keyword_mapping("50-degree", "机架")
        '50°'
        >>> apply_keyword_mapping("single", "机架")
        '单'
        >>> apply_keyword_mapping("flat", "供料漏斗")
        '平板'
    """
    candidates = expand_keyword_mapping(keyword, product_type=product_type)
    return candidates[-1] if candidates else str(keyword).strip()
