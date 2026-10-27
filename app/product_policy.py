"""施耐德品牌与人民币价格约束；不猜测型号。"""

from datetime import date

from .config import REQUIRED_BRAND
from .models import PriceRecord, Product

_SCHNEIDER_NAMES = {"施耐德", "施耐德电气", "schneider", "schneider electric"}


def normalized_brand(brand: str | None) -> str | None:
    if not brand or not brand.strip():
        return None
    return REQUIRED_BRAND if brand.strip().casefold() in _SCHNEIDER_NAMES else brand.strip()


def product_issue(product: Product) -> str | None:
    """返回阻止该行自动报价的问题；有问题时应修正清单后重传。"""
    if normalized_brand(product.brand) != REQUIRED_BRAND:
        return "品牌须明确为施耐德；请核对产品并修改清单后重新上传"
    if not product.model or not product.model.strip():
        return "缺少施耐德型号；请核对型号并修改清单后重新上传"
    return None


def _same_text(left: str | None, right: str | None) -> bool:
    return (left or "").strip().casefold() == (right or "").strip().casefold()


def eligible_price(product: Product, record: PriceRecord) -> bool:
    """自动采用价格前，逐项验证身份、来源、日期和币种。"""
    if product_issue(product):
        return False
    try:
        valid_date = record.valid_until is not None and date.fromisoformat(record.valid_until) >= date.today()
    except ValueError:
        return False
    return bool(
        valid_date
        and record.verified
        and record.currency == "CNY"
        and record.price_precision == "decimal_text"
        and record.price > 0
        and record.source.strip()
        and record.source_reference and record.source_reference.strip()
        and normalized_brand(record.brand) == REQUIRED_BRAND
        and _same_text(record.product_name, product.name)
        and _same_text(record.model, product.model)
        and _same_text(record.spec, product.spec)
        and _same_text(record.unit, product.unit)
    )
