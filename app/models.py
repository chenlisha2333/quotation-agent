from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator
from typing import Dict, List, Literal, Optional
from enum import Enum
from decimal import Decimal
from datetime import date, datetime, timezone

from .config import DEFAULT_PROFIT_RATE, PRICING_RULE_ID


class ProductCategory(str, Enum):
    COMPONENT = "元器件"
    CABINET = "柜体"
    AUXILIARY = "辅材"
    UNKNOWN = "未知"


class Product(BaseModel):
    item_id: Optional[str] = Field(default=None, description="报价任务内的明细行标识")
    name: str = Field(description="产品名称")
    model: Optional[str] = Field(default=None, description="型号")
    spec: Optional[str] = Field(default=None, description="规格参数")
    brand: Optional[str] = Field(default=None, description="品牌")
    category: ProductCategory = Field(default=ProductCategory.UNKNOWN, description="产品类别")
    quantity: int = Field(default=1, ge=1, description="数量")
    unit: Optional[str] = Field(default="个", description="单位")


class ProductEvidence(BaseModel):
    """一条产品事实的外部依据；来源事实不等于企业选型批准。"""

    title: str = Field(min_length=1, max_length=240)
    url: HttpUrl
    checked_on: date
    note: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("url")
    @classmethod
    def require_https_source(cls, value: HttpUrl) -> HttpUrl:
        if value.scheme != "https":
            raise ValueError("产品证据链接必须使用 HTTPS")
        return value

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("来源标题不能为空")
        return value


class ProductMasterVersion(BaseModel):
    """不可变的产品主数据版本，区分公开证据与企业技术批准。"""

    product_id: str = Field(min_length=1, max_length=100)
    version: int = Field(ge=1)
    brand: str = Field(min_length=1, max_length=120)
    model: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=240)
    aliases: List[str] = Field(default_factory=list)
    unit: Optional[str] = Field(default=None, max_length=40)
    attributes: Dict[str, str] = Field(default_factory=dict)
    lifecycle_status: Literal["active", "discontinued", "unknown"] = "unknown"
    replacement_product_id: Optional[str] = Field(default=None, max_length=100)
    evidence: List[ProductEvidence] = Field(min_length=1)
    permission_reference: Optional[str] = Field(default=None, max_length=500)
    approval_status: Literal["draft", "approved", "rejected"] = "draft"
    approved_by: Optional[str] = Field(default=None, max_length=120)
    approved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("product_id", "brand", "model", "name")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("产品主数据必填文本不能只有空格")
        return value

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: List[str]) -> List[str]:
        cleaned = [value.strip() for value in values]
        if any(not value for value in cleaned):
            raise ValueError("产品别名不能是空字符串")
        keys = [value.casefold() for value in cleaned]
        if len(keys) != len(set(keys)):
            raise ValueError("同一产品版本不能重复登记别名")
        return cleaned

    @field_validator("attributes")
    @classmethod
    def normalize_attributes(cls, values: Dict[str, str]) -> Dict[str, str]:
        cleaned = {key.strip(): value.strip() for key, value in values.items()}
        if any(not key or not value for key, value in cleaned.items()):
            raise ValueError("产品属性名称和值均不能为空")
        return cleaned

    @model_validator(mode="after")
    def approved_records_need_authority(self):
        if self.approval_status == "approved":
            if not self.approved_by or not self.approved_by.strip() or self.approved_at is None:
                raise ValueError("已批准产品版本必须记录技术审核人和时间")
            if self.approved_at.tzinfo is None or self.approved_at.utcoffset() is None:
                raise ValueError("技术审核时间必须包含时区，统一使用 UTC")
            if not self.permission_reference:
                raise ValueError("已批准产品版本必须记录数据使用依据")
            if not self.permission_reference.strip():
                raise ValueError("已批准产品版本必须记录数据使用依据")
        if self.replacement_product_id == self.product_id:
            raise ValueError("替代产品不能引用自身")
        return self


class PriceRecord(BaseModel):
    id: Optional[int] = None
    product_name: str
    model: Optional[str]
    spec: Optional[str] = None
    brand: Optional[str] = None
    unit: Optional[str] = None
    currency: Optional[Literal["CNY"]] = None  # 旧记录币种未知，不能默认成人民币
    source_reference: Optional[str] = None
    valid_until: Optional[str] = None
    verified: bool = False
    source: str
    price: Decimal = Field(ge=0)
    confidence: float = Field(default=0.5, ge=0, le=1)
    fetched_at: Optional[str] = None
    price_precision: str = "unknown"


class QuotationStatus(str, Enum):
    PENDING = "待处理"
    PARSING = "解析中"
    PRICING = "查价中"
    VALIDATING = "价格核查中"
    CALCULATING = "计算中"
    HUMAN_REVIEW = "人工确认"
    DONE = "完成"
    ERROR = "错误"


class QuotationItem(BaseModel):
    item_id: str = ""
    product: Product
    currency: Literal["CNY"] = "CNY"
    cost_price: Optional[Decimal] = None
    profit_rate: Decimal = DEFAULT_PROFIT_RATE  # 兼容旧接口字段名；当前公式按加价率解释
    discount: Decimal = Decimal("1")
    unit_price: Decimal = Decimal("0.00")
    total_price: Decimal = Decimal("0.00")
    pricing_rule_id: str = PRICING_RULE_ID
    rounding_mode: str = "ROUND_HALF_UP"
    remark: str = ""
    price_source: Optional[str] = None  # 价格来源（本地/网页/人工）
    price_precision: Optional[str] = None  # decimal_text / legacy_real / external_float / manual
    price_record_id: Optional[int] = None  # 采用的本地价格记录主键，随报价快照冻结
    price_source_reference: Optional[str] = None  # 供应商凭证标识，不自动公开给客户
    price_valid_until: Optional[str] = None
    price_evidence_status: Literal["verified", "pending_procurement_evidence", "not_applicable"] = "not_applicable"
    validation_warning: Optional[str] = None  # 价格核查警告


class Quotation(BaseModel):
    """完整报价单"""
    task_id: str
    customer_name: str
    currency: Literal["CNY"] = "CNY"
    items: List[QuotationItem]
    total_price: Decimal = Decimal("0.00")
    created_at: str = ""  # 报价时间
    valid_until: str = ""  # 有效期至
    status: QuotationStatus = QuotationStatus.DONE


class QuotationRequest(BaseModel):
    customer_name: str = ""
    profit_rate: Decimal = DEFAULT_PROFIT_RATE
    items: List[Product]


class PriceValidationResult(BaseModel):
    """价格核查结果"""
    product_name: str
    model: Optional[str] = None
    original_price: Decimal = Field(description="原始查到的价格")
    validated_price: Decimal = Field(ge=0, description="核查后的价格（可能调整）")
    is_valid: bool = Field(default=True, description="价格是否有效")
    warning: Optional[str] = Field(default=None, description="警告信息（如价格异常）")
    validation_method: str = Field(default="basic", description="核查方法：basic/range/historical")


class AgentState(BaseModel):
    task_id: str
    status: QuotationStatus = QuotationStatus.PENDING
    input_file: Optional[str] = None
    items: List[QuotationItem] = []
    messages: List[str] = []
    error: Optional[str] = None
