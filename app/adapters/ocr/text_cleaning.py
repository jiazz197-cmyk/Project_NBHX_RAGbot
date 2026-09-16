"""OCR 文本清洗与判定工具函数（纯函数，无 IO）。"""

def pdftotext_has_key_fields(text: str) -> bool:
    """检查 pdftotext 输出是否包含匹配所需的关键字段。"""
    text_upper = text.upper()
    required_hints = ["MODEL", "SURFACE", "WORK NO"]
    found = sum(1 for h in required_hints if h in text_upper)
    return found >= 2
