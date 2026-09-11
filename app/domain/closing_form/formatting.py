"""Closing form text formatting (domain logic)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def _now_utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def format_closing_form_text(
    *,
    order_date: Optional[str] = None,
    deal_time: Optional[str] = None,
    customer_name: str = "",
    product_type: str = "",
    model_spec: str = "",
    quantity: int = 0,
    original_price: float = 0.0,
    production_code: str = "",
    contract_number: str = "",
    material_name: str = "",
    weighing_spec: str = "",
    speed: str = "",
    accuracy: str = "",
    packaging_machine_type: str = "",
    top_cone_type: str = "",
    linear_vibrator_type: str = "",
    layer_adjustment_ring: str = "",
    feeding_hopper: str = "",
    weigh_bucket: str = "",
    memory_bucket: str = "",
    chute_angle: str = "",
    collecting_cone_type: str = "",
    scale_config: str = "",
    image_urls: Optional[list[str]] = None,
) -> str:
    """Format closing form fields into a comma-separated Chinese description.

    This matches the ORIGINAL format used by the frontend:
    - Chinese colons (：) between key and value
    - English comma + space (", ") between fields
    - ALL fields always included (even if empty)
    - Date falls back to now() if not provided
    """
    date_str = order_date or _now_utc_naive().strftime("%Y-%m-%d %H:%M:%S")
    parts = [
        f"日期：{date_str}",
        f"成交时间：{deal_time or ''}",
        f"客户名称：{customer_name or ''}",
        f"产品类型：{product_type or ''}",
        f"型号规格：{model_spec or ''}",
        f"数量：{quantity or 0}",
        f"原价不含税：{original_price or 0}",
        f"生产制造编号：{production_code or ''}",
        f"合同编号：{contract_number or ''}",
        f"物料名称：{material_name or ''}",
        f"称重规格：{weighing_spec or ''}",
        f"速度：{speed or ''}",
        f"精度：{accuracy or ''}",
        f"包装机类型：{packaging_machine_type or ''}",
        f"顶锥形式：{top_cone_type or ''}",
        f"线振形式：{linear_vibrator_type or ''}",
        f"料层调整圈：{layer_adjustment_ring or ''}",
        f"供料斗：{feeding_hopper or ''}",
        f"计量斗：{weigh_bucket or ''}",
        f"记忆斗：{memory_bucket or ''}",
        f"溜槽角度：{chute_angle or ''}",
        f"集合斗形式：{collecting_cone_type or ''}",
        f"单双秤/混料/外挂/特殊：{scale_config or ''}",
    ]
    if image_urls:
        for url in image_urls:
            parts.append(f"图片url：{url}")
    return ", ".join(parts)
