"""人民币草稿计价：默认成本价乘 1.2，税等商务条款仍待确认。"""

from decimal import Decimal, InvalidOperation
from typing import Mapping, TypeAlias

from .config import (
    DEFAULT_DISCOUNT,
    DEFAULT_PROFIT_RATE,
    MONEY_QUANTUM,
    MONEY_ROUNDING,
    PRICING_RULE_ID,
)
from .models import Product, QuotationItem

DecimalInput: TypeAlias = Decimal | str | int | float


def as_decimal(value: DecimalInput, field_name: str) -> Decimal:
    """在系统边界把输入转为有限的十进制数；不对 float 做算术。"""
    if isinstance(value, bool):
        raise ValueError(f"{field_name} 不能是布尔值")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} 不是有效数字") from exc
    if not amount.is_finite():
        raise ValueError(f"{field_name} 必须是有限数字")
    return amount


def calculate_item_price(
    product: Product,
    cost_price: DecimalInput | None,
    profit_rate: DecimalInput = DEFAULT_PROFIT_RATE,
    discount: DecimalInput = DEFAULT_DISCOUNT,
) -> QuotationItem:
    """先将单价舍入到分，再乘数量；输出保留原始成本及规则标识。"""
    rate = as_decimal(profit_rate, "加价率")
    factor = as_decimal(discount, "折扣")
    if rate < 0:
        raise ValueError("加价率不能为负数")
    if not Decimal("0") <= factor <= Decimal("1"):
        raise ValueError("折扣必须在 0 到 1 之间")
    rule_id = (
        PRICING_RULE_ID if rate == DEFAULT_PROFIT_RATE and factor == DEFAULT_DISCOUNT
        else f"draft-custom-rate-{rate}-discount-{factor}"
    )

    cost = None if cost_price is None else as_decimal(cost_price, "成本价")
    if cost is not None and cost < 0:
        raise ValueError("成本价不能为负数")
    if cost is None or cost == 0:
        return QuotationItem(
            item_id=product.item_id or "",
            product=product,
            cost_price=None,
            profit_rate=rate,
            discount=factor,
            remark="未查到价格，等待人工确认",
            pricing_rule_id=rule_id,
            rounding_mode=MONEY_ROUNDING,
        )

    unit_price = (cost * (Decimal("1") + rate) * factor).quantize(
        MONEY_QUANTUM, rounding=MONEY_ROUNDING
    )
    return QuotationItem(
        item_id=product.item_id or "",
        product=product,
        cost_price=cost,
        profit_rate=rate,
        discount=factor,
        unit_price=unit_price,
        total_price=unit_price * product.quantity,
        pricing_rule_id=rule_id,
        rounding_mode=MONEY_ROUNDING,
    )


def calculate_quotation(
    products: list[Product],
    price_lookup: Mapping[str, DecimalInput],
    profit_rate: DecimalInput = DEFAULT_PROFIT_RATE,
    discount: DecimalInput = DEFAULT_DISCOUNT,
) -> list[QuotationItem]:
    """兼容现有的批量辅助函数；按名称查价的局限留待匹配模块处理。"""
    return [
        calculate_item_price(product, price_lookup.get(product.name), profit_rate, discount)
        for product in products
    ]
