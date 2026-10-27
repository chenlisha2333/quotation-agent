from pydantic import BaseModel, Field
from typing import List, Literal, Optional
from enum import Enum
from decimal import Decimal

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


class PriceRecord(BaseModel):
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
