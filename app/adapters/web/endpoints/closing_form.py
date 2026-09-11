"""
智能组合秤订单填表 Schema
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.config import settings


class ClosingFormBase(BaseModel):
    """填表公共字段与校验（提交 / 修改共享）。"""

    date: Optional[str] = Field(None, description="日期，格式：YYYY-MM-DD HH:mm:ss")
    closing_date: Optional[str] = Field(None, description="成交时间，格式：YYYY-MM-DD")
    customer_name: str = Field(..., description="客户名称")
    product_type: str = Field(..., description="产品类型")
    model_spec: str = Field(..., description="型号规格")
    quantity: int = Field(..., ge=1, description="数量")
    price_excluding_tax: float = Field(..., ge=0, description="原价不含税")
    production_number: str = Field(..., description="生产制造编号")
    contract_number: str = Field(default="", description="合同编号")
    material_name: str = Field(..., description="物料名称")
    weighing_spec: str = Field(..., description="称重规格")
    speed: int = Field(..., ge=0, description="速度")
    precision: str = Field(..., description="精度")
    packaging_machine_type: str = Field(default="", description="包装机类型")
    top_cone_type: str = Field(..., description="顶锥形式")
    linear_vibration_type: str = Field(..., description="线振形式")
    material_layer_ring: str = Field(..., description="料层调整圈")
    feed_hopper: str = Field(..., description="供料斗")
    metering_hopper: str = Field(..., description="计量斗")
    memory_hopper: str = Field(..., description="记忆斗")
    chute_angle: str = Field(..., description="溜槽角度")
    collection_hopper_type: str = Field(..., description="集合斗形式")
    scale_type: str = Field(..., description="单双秤/混料/外挂/特殊")
    image_url_1: Optional[str] = Field(None, max_length=512, description="规格书图片")
    image_url_2: Optional[str] = Field(None, max_length=512, description="原价书图片")

    @field_validator("image_url_1", "image_url_2")
    @classmethod
    def validate_image_url_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not v.startswith(f"{settings.CLOSING_FORM_IMAGE_PREFIX}/"):
            raise ValueError("非法的图片路径")
        return v

    @model_validator(mode="after")
    def validate_required_images(self):
        if not self.image_url_1 or not self.image_url_2:
            raise ValueError("请上传规格书和原价书两张图片")
        return self

    def to_formatted_text(self) -> str:
        from app.domain.closing_form.formatting import format_closing_form_text

        return format_closing_form_text(
            order_date=self.date,
            deal_time=self.closing_date,
            customer_name=self.customer_name,
            product_type=self.product_type,
            model_spec=self.model_spec,
            quantity=self.quantity,
            original_price=self.price_excluding_tax,
            production_code=self.production_number,
            contract_number=self.contract_number,
            material_name=self.material_name,
            weighing_spec=self.weighing_spec,
            speed=str(self.speed) if self.speed is not None else None,
            accuracy=self.precision,
            packaging_machine_type=self.packaging_machine_type,
            top_cone_type=self.top_cone_type,
            linear_vibrator_type=self.linear_vibration_type,
            layer_adjustment_ring=self.material_layer_ring,
            feeding_hopper=self.feed_hopper,
            weigh_bucket=self.metering_hopper,
            memory_bucket=self.memory_hopper,
            chute_angle=self.chute_angle,
            collecting_cone_type=self.collection_hopper_type,
            scale_config=self.scale_type,
            image_urls=[self.image_url_1, self.image_url_2],
        )


class ClosingFormSubmit(ClosingFormBase):
    """填表提交数据"""


class ClosingFormSubmitResponse(BaseModel):
    """填表提交响应"""

    success: bool = True
    message: str = "提交成功"
    form_text: Optional[str] = None
    image_url_1: Optional[str] = None
    image_url_2: Optional[str] = None


class ClosingFormRecord(BaseModel):
    """单条已提交表单记录"""

    id: str
    text: str
    upload_time: Optional[str] = None
    uploader: str = ""
    status: str = "pending"
    image_url_1: Optional[str] = None
    image_url_2: Optional[str] = None


class ClosingFormRejectResponse(BaseModel):
    """拒绝审批响应"""

    success: bool = True
    message: str = "审批不通过，已退回待修改"


class ClosingFormRevise(ClosingFormBase):
    """修改表单提交数据"""


class ClosingFormReviseResponse(BaseModel):
    """修改表单响应"""

    success: bool = True
    message: str = "修改已提交，等待审批"


class ClosingFormListResponse(BaseModel):
    """用户已提交表单列表响应"""

    success: bool = True
    total: int = 0
    records: list[ClosingFormRecord] = []


class ClosingFormApproveResponse(BaseModel):
    """审批响应"""

    success: bool = True
    message: str = "审批通过"


class ClosingFormDeleteResponse(BaseModel):
    """删除响应"""

    success: bool = True
    message: str = "删除成功"
    deleted_id: str


class ImageUploadResponse(BaseModel):
    """图片上传响应"""

    success: bool = True
    object_name: str


class Collection2Record(BaseModel):
    """data_doc_collection_2 单条记录"""

    id: str
    text: str
    file_name: Optional[str] = None
    upload_time: Optional[str] = None
    uploader: str = ""
    status: str = "approved"


class Collection2ListResponse(BaseModel):
    """data_doc_collection_2 列表响应"""

    success: bool = True
    total: int = 0
    records: list[Collection2Record] = []
