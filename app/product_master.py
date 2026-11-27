"""施耐德电气中国区公开产品资料的校验与显式导入。"""

import argparse
import json
from pathlib import Path
from urllib.parse import urlsplit

from . import db
from .models import ProductMasterVersion
from .product_policy import normalized_brand

DEFAULT_CHINA_CATALOG = (
    Path(__file__).resolve().parent.parent
    / "data_sources"
    / "schneider_cn_product_master.json"
)


class ProductCatalogError(ValueError):
    """产品目录结构、市场范围、证据域名或批准状态不符合导入边界。"""


def load_china_product_catalog(path: Path = DEFAULT_CHINA_CATALOG) -> list[ProductMasterVersion]:
    """读取中国区目录，并强制全部保持 draft；不会把公开页面当企业批准。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProductCatalogError("中国区产品目录无法读取或不是有效 JSON") from exc
    if payload.get("market") != "CN" or not payload.get("catalog_version"):
        raise ProductCatalogError("目录必须声明 market=CN 和 catalog_version")

    raw_products = payload.get("products")
    if not isinstance(raw_products, list) or not raw_products:
        raise ProductCatalogError("产品目录必须包含非空 products 列表")
    try:
        products = [ProductMasterVersion.model_validate(record) for record in raw_products]
    except Exception as exc:
        raise ProductCatalogError("产品目录记录未通过 ProductMasterVersion 校验") from exc

    ids = [product.product_id for product in products]
    if len(ids) != len(set(ids)):
        raise ProductCatalogError("产品目录包含重复 product_id")
    known_ids = set(ids)
    for product in products:
        if normalized_brand(product.brand) != "施耐德":
            raise ProductCatalogError(f"{product.product_id} 不是施耐德品牌记录")
        if product.approval_status != "draft":
            raise ProductCatalogError("中国官网公开资料只能作为待审核 draft 导入")
        if product.replacement_product_id and product.replacement_product_id not in known_ids:
            raise ProductCatalogError(
                f"{product.product_id} 的替代产品 {product.replacement_product_id} 不在目录中"
            )
        for evidence in product.evidence:
            hostname = (urlsplit(str(evidence.url)).hostname or "").casefold()
            if hostname != "schneider-electric.cn" and not hostname.endswith(".schneider-electric.cn"):
                raise ProductCatalogError(
                    f"{product.product_id} 的来源不是施耐德中国官网域名"
                )
    return products


def main() -> None:
    parser = argparse.ArgumentParser(description="校验/显式导入施耐德电气中国区待审核产品资料")
    parser.add_argument(
        "--import-drafts", action="store_true",
        help="将目录中的 draft 原子导入本机 SQLite；不批准产品，也不会导入报价或价格",
    )
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CHINA_CATALOG)
    args = parser.parse_args()
    products = load_china_product_catalog(args.catalog)
    if args.import_drafts:
        inserted, skipped = db.import_product_master_versions(products)
        print(f"已导入待审核产品版本：新增 {inserted}，相同记录跳过 {skipped}；未批准任何产品。")
    else:
        print(f"目录校验通过：{len(products)} 条中国区待审核产品记录；没有写入数据库。")


if __name__ == "__main__":
    main()
