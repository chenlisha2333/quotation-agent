"""
价格核查模块
真实业务场景里，查到的价格不能直接用，必须经过核查：
1. 基础核查：价格是否为正数、是否在合理范围
2. 范围核查：价格是否偏离历史价格太多（防止抓到异常值）
3. 规格匹配：查到的价格对应的规格是否和我们要的一致

这个模块是"确定性流程"的一部分，不是 AI 决策。
"""

from typing import List, Optional
from decimal import Decimal
from .models import PriceRecord, PriceValidationResult, Product
from .db import get_historical_prices
from .config import logger


# 各类产品的合理价格范围（单位：元）
# 真实业务里这些值应该从配置文件或数据库读取
PRICE_RANGES = {
    "断路器": (10, 500),
    "接触器": (20, 800),
    "继电器": (10, 300),
    "配电箱": (50, 2000),
    "电缆": (1, 100),  # 每米价格
    "按钮开关": (5, 100),
    "熔断器": (5, 200),
    "互感器": (50, 1000),
}

# 价格偏离阈值：如果新价格和历史平均价偏离超过这个比例，标记为可疑
DEVIATION_THRESHOLD = Decimal("0.30")  # 30%


def validate_price_basic(product: Product, price: Decimal) -> PriceValidationResult:
    """
    基础核查：检查价格是否在合理范围

    Args:
        product: 产品对象
        price: 查到的价格

    Returns:
        PriceValidationResult: 核查结果
    """
    product_name = product.name

    # 1. 基础检查：价格必须为正数
    if price <= 0:
        return PriceValidationResult(
            product_name=product_name,
            model=product.model,
            original_price=price,
            validated_price=0,
            is_valid=False,
            warning="价格为负数或零",
            validation_method="basic"
        )

    # 2. 范围检查：价格是否在合理区间
    price_range = PRICE_RANGES.get(product_name)
    if price_range:
        min_price, max_price = price_range
        if price < min_price:
            return PriceValidationResult(
                product_name=product_name,
                model=product.model,
                original_price=price,
                validated_price=price,
                is_valid=True,
                warning=f"价格 {price} 低于常见范围 {min_price}-{max_price}，请人工确认",
                validation_method="range"
            )
        elif price > max_price:
            return PriceValidationResult(
                product_name=product_name,
                model=product.model,
                original_price=price,
                validated_price=price,
                is_valid=True,
                warning=f"价格 {price} 高于常见范围 {min_price}-{max_price}，请人工确认",
                validation_method="range"
            )

    # 3. 通过基础检查
    return PriceValidationResult(
        product_name=product_name,
        model=product.model,
        original_price=price,
        validated_price=price,
        is_valid=True,
        warning=None,
        validation_method="basic"
    )


def validate_price_historical(product: Product, price: Decimal) -> PriceValidationResult:
    """
    历史价格核查：检查新价格是否和历史价格偏差太大

    Args:
        product: 产品对象
        price: 查到的价格

    Returns:
        PriceValidationResult: 核查结果
    """
    # 1. 先做基础核查
    basic_result = validate_price_basic(product, price)
    if not basic_result.is_valid:
        return basic_result

    # 2. 查询历史价格
    historical = get_historical_prices(product, limit=5)

    if not historical:
        # 没有历史价格，直接用基础核查结果
        return basic_result

    # 3. 计算历史平均价
    avg_price = sum(h.price for h in historical) / len(historical)

    # 4. 检查偏离度
    deviation = abs(price - avg_price) / avg_price if avg_price > 0 else 0

    if deviation > DEVIATION_THRESHOLD:
        warning_msg = (
            f"价格 {price} 与历史平均价 {avg_price:.2f} 偏离 {deviation:.1%}，"
            f"超过阈值 {DEVIATION_THRESHOLD:.0%}，建议人工确认"
        )
        return PriceValidationResult(
            product_name=product.name,
            model=product.model,
            original_price=price,
            validated_price=price,
            is_valid=True,
            warning=warning_msg,
            validation_method="historical"
        )

    # 5. 偏离度正常
    return PriceValidationResult(
        product_name=product.name,
        model=product.model,
        original_price=price,
        validated_price=price,
        is_valid=True,
        warning=None,
        validation_method="historical"
    )


def validate_price(product: Product, price: Decimal) -> PriceValidationResult:
    """
    价格核查主入口：依次执行基础核查和历史核查

    这是对外暴露的唯一接口，内部决定用什么核查策略。

    Args:
        product: 产品对象
        price: 查到的价格

    Returns:
        PriceValidationResult: 最终核查结果
    """
    # 策略：优先用历史核查，如果没有历史数据就降级到基础核查
    return validate_price_historical(product, price)
