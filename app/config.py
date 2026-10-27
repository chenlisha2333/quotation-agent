import logging
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
# 延迟创建目录，避免导入时权限问题
# DATA_DIR.mkdir(exist_ok=True)

DATABASE_URL = f"sqlite:///{DATA_DIR / 'quotation.db'}"
UPLOAD_DIR = BASE_DIR / "data" / "uploads"

# 延迟创建目录的辅助函数
def ensure_dirs():
    """确保必要的目录存在"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 用户指定成本价乘 1.2；税与采购成本凭证仍待企业确认。
DEFAULT_PROFIT_RATE = Decimal("0.20")
DEFAULT_DISCOUNT = Decimal("1")
MONEY_QUANTUM = Decimal("0.01")
MONEY_ROUNDING = ROUND_HALF_UP
PRICING_RULE_ID = "draft-cost-times-1.2-v1"
QUOTE_CURRENCY = "CNY"
REQUIRED_BRAND = "施耐德"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("quotation-agent")
