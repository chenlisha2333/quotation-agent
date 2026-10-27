"""
外部网站价格抓取模块
支持多网站适配器模式，目前实现：
- 震坤行 (zhenkunhang.com)
- 阿里巴巴 (1688.com)
- 京东工业品

使用 Playwright 进行浏览器自动化操作
"""

import asyncio
import datetime as _dt
import re
from abc import ABC, abstractmethod
from typing import List, Optional, Dict
from dataclasses import dataclass

try:
    from playwright.async_api import async_playwright, Browser, Page
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False
    # 占位符类型
    Browser = type('Browser', (), {})
    Page = type('Page', (), {})

from .models import Product, PriceRecord
from .config import logger


@dataclass
class ScrapedPrice:
    """抓取到的价格信息"""
    product_name: str
    model: Optional[str]
    price: float
    source: str
    url: str
    confidence: float = 0.5


class BaseScraper(ABC):
    """价格抓取器基类"""

    def __init__(self, name: str, base_url: str):
        self.name = name
        self.base_url = base_url

    @abstractmethod
    async def search(self, page: Page, product: Product) -> Optional[ScrapedPrice]:
        """搜索单个产品价格"""
        pass

    @abstractmethod
    def can_handle(self, product: Product) -> bool:
        """判断是否能处理该产品"""
        pass


class ZKHScraper(BaseScraper):
    """震坤行价格抓取器"""

    def __init__(self):
        super().__init__("震坤行", "https://www.zkh.com")

    def can_handle(self, product: Product) -> bool:
        # 震坤行主要覆盖工业元器件、劳保用品等
        keywords = ["断路器", "接触器", "继电器", "开关", "按钮", "熔断器",
                   "互感器", "变压器", "电缆", "电线", "插座", "插头"]
        return any(k in product.name for k in keywords)

    async def search(self, page: Page, product: Product) -> Optional[ScrapedPrice]:
        query = f"{product.name} {product.model or ''}".strip()
        search_url = f"https://www.zkh.com/search?keyword={query}"

        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)  # 等待页面加载

            # 尝试提取价格（震坤行页面结构可能变化，这里是示例）
            price_selectors = [
                ".price",
                ".current-price",
                ".product-price",
                "[class*='price']",
            ]

            for selector in price_selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    if elements:
                        for elem in elements:
                            text = await elem.inner_text()
                            price = self._extract_price(text)
                            if price and price > 0:
                                return ScrapedPrice(
                                    product_name=product.name,
                                    model=product.model,
                                    price=price,
                                    source="震坤行",
                                    url=search_url,
                                    confidence=0.7
                                )
                except Exception:
                    continue

        except Exception as e:
            logger.warning(f"震坤行查价失败: {e}")

        return None

    def _extract_price(self, text: str) -> Optional[float]:
        """从文本中提取价格"""
        # 匹配 ¥123.45 或 123.45元 或 ￥123.45
        patterns = [
            r'[¥￥]\s*(\d+\.?\d*)',
            r'(\d+\.?\d*)\s*元',
            r'(\d+\.?\d{1,2})',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    price = float(match.group(1))
                    if price > 0:
                        return price
                except ValueError:
                    continue
        return None


class Alibaba1688Scraper(BaseScraper):
    """阿里巴巴1688价格抓取器"""

    def __init__(self):
        super().__init__("阿里巴巴1688", "https://www.1688.com")

    def can_handle(self, product: Product) -> bool:
        # 1688 覆盖几乎所有电气产品
        return True

    async def search(self, page: Page, product: Product) -> Optional[ScrapedPrice]:
        query = f"{product.name} {product.model or ''}".strip()
        search_url = f"https://s.1688.com/selloffer/offer_search.htm?keywords={query}"

        try:
            await page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(2)

            # 1688 价格选择器
            price_selectors = [
                ".price",
                ".price-num",
                ".offer-price",
                "[class*='price']",
            ]

            for selector in price_selectors:
                try:
                    elements = await page.query_selector_all(selector)
                    if elements:
                        for elem in elements:
                            text = await elem.inner_text()
                            price = self._extract_price(text)
                            if price and price > 0:
                                return ScrapedPrice(
                                    product_name=product.name,
                                    model=product.model,
                                    price=price,
                                    source="阿里巴巴1688",
                                    url=search_url,
                                    confidence=0.6
                                )
                except Exception:
                    continue

        except Exception as e:
            logger.warning(f"1688查价失败: {e}")

        return None

    def _extract_price(self, text: str) -> Optional[float]:
        """从文本中提取价格"""
        patterns = [
            r'[¥￥]\s*(\d+\.?\d*)',
            r'(\d+\.?\d*)\s*元',
            r'(\d+\.?\d{1,2})',
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                try:
                    price = float(match.group(1))
                    if price > 0:
                        return price
                except ValueError:
                    continue
        return None


class WebPriceScraper:
    """网页价格抓取器主类"""

    def __init__(self):
        self.scrapers: List[BaseScraper] = [
            ZKHScraper(),
            Alibaba1688Scraper(),
        ]
        self.browser: Optional[Browser] = None
        self._playwright = None

    async def __aenter__(self):
        if not PLAYWRIGHT_AVAILABLE:
            logger.warning("Playwright 未安装，网页抓取功能不可用")
            return self
        self._playwright = await async_playwright().start()
        self.browser = await self._playwright.chromium.launch(headless=True)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.browser:
            await self.browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def search_price(self, product: Product) -> Optional[PriceRecord]:
        """
        搜索单个产品的价格
        按优先级尝试各个抓取器
        """
        if not PLAYWRIGHT_AVAILABLE or not self.browser:
            logger.info("Playwright 不可用，跳过网页查价")
            return None

        for scraper in self.scrapers:
            if not scraper.can_handle(product):
                continue

            page = await self.browser.new_page()
            try:
                result = await scraper.search(page, product)
                if result:
                    return PriceRecord(
                        product_name=result.product_name,
                        model=result.model,
                        source=result.source,
                        price=result.price,
                        confidence=result.confidence,
                        fetched_at=_dt.datetime.now().isoformat(),
                        price_precision="external_float",
                    )
            finally:
                await page.close()

        return None

    async def search_prices(self, products: List[Product]) -> Dict[str, Optional[PriceRecord]]:
        """批量搜索多个产品价格"""
        results = {}
        for product in products:
            record = await self.search_price(product)
            results[product.name] = record
        return results


# 同步包装函数，方便非异步代码调用
async def search_web_price_async(product: Product) -> Optional[PriceRecord]:
    """异步搜索单个产品价格"""
    async with WebPriceScraper() as scraper:
        return await scraper.search_price(product)


def search_web_price_sync(product: Product) -> Optional[PriceRecord]:
    """
    同步包装：搜索单个产品价格

    使用重试机制，失败后自动重试最多 3 次。
    真实业务里网络请求不稳定，必须有重试。
    """
    from .retry import retry_with_fallback

    @retry_with_fallback(fallback_value=None, max_retries=3, delay=1.0, backoff=2.0)
    def _search():
        return asyncio.run(search_web_price_async(product))

    return _search()
