"""OCR 文本清洗与判定工具函数。

从 run_test.py 提取的纯函数，供 OcrPlainTextAdapter 复用。
"""

import re


def pdftotext_has_key_fields(text: str) -> bool:
    """检查 pdftotext 输出是否包含匹配所需的关键字段。"""
    text_upper = text.upper()
    required_hints = ["MODEL", "SURFACE", "WORK NO"]
    found = sum(1 for h in required_hints if h in text_upper)
    return found >= 2


def clean_dotsocr_text(text: str) -> str:
    """将 DotsOCR 返回的 HTML 转换为 pdftotext 兼容的纯文本格式。

    清洗步骤:
    1. <tr> 行转为 tab 分隔
    2. <br> 和块元素转为换行
    3. 去除剩余 HTML 标签
    4. 压缩多余空白
    """
    # 步骤 1: <tr> 行转为 tab 分隔
    def _join_row(m):
        row_content = m.group(1)
        cells = re.findall(r'<(?:td|th)[^>]*>(.*?)</(?:td|th)>', row_content)
        cells = [re.sub(r'<[^>]+>', ' ', c).strip() for c in cells]
        cells = [re.sub(r'\s+', ' ', c).strip() for c in cells if c]
        return '\t'.join(cells) + '\n'

    text = re.sub(r'<tr[^>]*>(.*?)</tr>', _join_row, text, flags=re.DOTALL)

    # 步骤 2: <br> 和块元素转为换行
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'</?p[^>]*>', '\n', text)
    text = re.sub(r'</?div[^>]*>', '\n', text)

    # 步骤 3: 去除剩余 HTML 标签
    text = re.sub(r'<[^>]+>', '', text)

    # 步骤 4: 压缩多余空白
    text = re.sub(r'[ \t]{3,}', '  ', text)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text.strip()
