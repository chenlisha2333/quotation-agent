"""
重试机制模块
真实业务场景里，网络查价可能失败（超时、反爬、服务不可用），需要重试。
这个模块提供通用的重试装饰器和工具函数。
"""

import time
import functools
from typing import Callable, Any, Optional, Tuple, Type
from .config import logger


def retry(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable] = None
):
    """
    重试装饰器

    Args:
        max_retries: 最大重试次数
        delay: 初始延迟时间（秒）
        backoff: 退避系数（每次重试延迟时间乘以这个数）
        exceptions: 需要捕获并重试的异常类型
        on_retry: 重试时的回调函数 (attempt, exception) -> None

    Returns:
        装饰后的函数

    Example:
        @retry(max_retries=3, delay=1.0, backoff=2.0)
        def fetch_price():
            # 可能失败的网络请求
            pass
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay
            last_exception = None

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt == max_retries:
                        # 最后一次重试也失败了
                        logger.error(f"{func.__name__} 在 {max_retries} 次重试后仍然失败: {e}")
                        raise

                    # 还有重试机会
                    logger.warning(f"{func.__name__} 第 {attempt + 1} 次尝试失败: {e}，{current_delay:.1f}s 后重试")

                    if on_retry:
                        on_retry(attempt + 1, e)

                    time.sleep(current_delay)
                    current_delay *= backoff

            # 理论上不会走到这里
            raise last_exception

        return wrapper
    return decorator


def retry_with_fallback(
    fallback_value: Any = None,
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,)
):
    """
    带兜底值的重试装饰器：失败后不抛异常，而是返回兜底值

    Args:
        fallback_value: 失败后返回的值
        max_retries: 最大重试次数
        delay: 初始延迟时间（秒）
        backoff: 退避系数
        exceptions: 需要捕获并重试的异常类型

    Example:
        @retry_with_fallback(fallback_value=None, max_retries=3)
        def fetch_price():
            # 可能失败的网络请求
            pass
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            current_delay = delay

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_retries:
                        logger.warning(f"{func.__name__} 在 {max_retries} 次重试后仍然失败，返回兜底值: {e}")
                        return fallback_value

                    logger.warning(f"{func.__name__} 第 {attempt + 1} 次尝试失败: {e}，{current_delay:.1f}s 后重试")
                    time.sleep(current_delay)
                    current_delay *= backoff

            # 理论上不会走到这里
            return fallback_value

        return wrapper
    return decorator
