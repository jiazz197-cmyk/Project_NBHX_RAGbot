"""Closing form DTOs (pure dataclass, no Pydantic coupling)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.domain.closing_form.formatting import format_closing_form_text


@dataclass
class ClosingFormCommand:
    """Command for submitting a closing form."""

    date: Optional[str] = None
    deal_time: Optional[str] = None
    customer_name: Optional[str] = None
    product_type: Optional[str] = None
    model_spec: Optional[str] = None
    quantity: Optional[int] = None
    original_price: Optional[float] = None
    production_code: Optional[str] = None
    contract_number: Optional[str] = None
    material_name: Optional[str] = None
    weighing_spec: Optional[str] = None
    speed: Optional[str] = None
    accuracy: Optional[str] = None
    packaging_machine_type: Optional[str] = None
    top_cone_type: Optional[str] = None
    linear_vibrator_type: Optional[str] = None
    layer_adjustment_ring: Optional[str] = None
    feeding_hopper: Optional[str] = None
    weigh_bucket: Optional[str] = None
    memory_bucket: Optional[str] = None
    chute_angle: Optional[str] = None
    collecting_cone_type: Optional[str] = None
    scale_config: Optional[str] = None
    image_urls: List[str] = field(default_factory=list)
    extras: Dict[str, Any] = field(default_factory=dict)

    @property
    def image_url_1(self) -> Optional[str]:
        return self.image_urls[0] if len(self.image_urls) > 0 else None

    @property
    def image_url_2(self) -> Optional[str]:
        return self.image_urls[1] if len(self.image_urls) > 1 else None

    def to_formatted_text(self) -> str:
        return format_closing_form_text(
            order_date=self.date,
            deal_time=self.deal_time,
            customer_name=self.customer_name,
            product_type=self.product_type,
            model_spec=self.model_spec,
            quantity=self.quantity,
            original_price=self.original_price,
            production_code=self.production_code,
            contract_number=self.contract_number,
            material_name=self.material_name,
            weighing_spec=self.weighing_spec,
            speed=self.speed,
            accuracy=self.accuracy,
            packaging_machine_type=self.packaging_machine_type,
            top_cone_type=self.top_cone_type,
            linear_vibrator_type=self.linear_vibrator_type,
            layer_adjustment_ring=self.layer_adjustment_ring,
            feeding_hopper=self.feeding_hopper,
            weigh_bucket=self.weigh_bucket,
            memory_bucket=self.memory_bucket,
            chute_angle=self.chute_angle,
            collecting_cone_type=self.collecting_cone_type,
            scale_config=self.scale_config,
            image_urls=self.image_urls if self.image_urls else None,
        )
