from typing import List
from decimal import Decimal
from .models import Product, PriceRecord, PriceValidationResult
from .db import search_local_price
from .config import DEFAULT_PROFIT_RATE


def search_local_price_tool(product: Product) -> PriceRecord:
    """查询本地价格库"""
    records = search_local_price(product)
    if records:
        return records[0]
    return None


def search_web_price_tool(product: Product) -> PriceRecord:
    """外部网站查价（接入 Playwright）"""
    try:
        from .web_scraper import search_web_price_sync
        return search_web_price_sync(product)
    except Exception as e:
        from .config import logger
        logger.warning(f"网页查价失败: {e}")
        return None


def calculate_price_tool(product: Product, cost_price: Decimal, profit_rate: Decimal = DEFAULT_PROFIT_RATE, discount: Decimal = Decimal("1")):
    """计算单个产品报价"""
    from .pricing import calculate_item_price
    return calculate_item_price(product, cost_price, profit_rate, discount)


def validate_price_tool(product: Product, price: Decimal) -> PriceValidationResult:
    """
    价格核查工具：检查价格是否合理

    这是 Agent 的"质检员"，防止错误价格流入报价单。
    """
    from .validator import validate_price
    return validate_price(product, price)
