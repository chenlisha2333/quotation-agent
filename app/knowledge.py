"""从人工核对的施耐德官方资料中检索产品事实；不读取供应商价格。"""

import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from .config import BASE_DIR

CATALOG_PATH = BASE_DIR / "data_sources" / "schneider_products.json"
_SOURCE_HOSTS = {"www.se.com", "eshop.se.com", "iportal.se.com"}


class KnowledgeHit(BaseModel):
    source_id: str
    title: str
    excerpt: str
    url: str
    checked_on: str


class ProductFact(BaseModel):
    model: str = Field(pattern=r"^[A-Z0-9]{4,24}$")
    name: str
    family: str
    facts: dict[str, str]
    source_title: str
    source_url: str
    checked_on: str

    def as_hit(self) -> KnowledgeHit:
        detail = "；".join(f"{key}：{value}" for key, value in self.facts.items())
        return KnowledgeHit(
            source_id=f"product:{self.model}",
            title=f"{self.name} {self.model}（{self.family}）",
            excerpt=f"{detail}。资料日期：{self.checked_on}。需技术确认；不含供应商人民币报价或柜体安装结论。",
            url=self.source_url,
            checked_on=self.checked_on,
        )


def _approved_source(url: str, model: str) -> bool:
    parts = urlsplit(url)
    return (
        parts.scheme == "https"
        and parts.hostname in _SOURCE_HOSTS
        and not parts.username and not parts.password
        and not parts.query and not parts.fragment
        and model.lower() in parts.path.lower()
    )


def _products(path: Path = CATALOG_PATH) -> list[ProductFact]:
    """校验本地事实清单，阻止错误来源混入模型上下文。"""
    payload = json.loads(path.read_text(encoding="utf-8"))
    products = [ProductFact.model_validate(item) for item in payload["products"]]
    models = [item.model for item in products]
    if len(models) != len(set(models)) or any(
        not item.facts or not _approved_source(item.source_url, item.model)
        for item in products
    ):
        raise ValueError("施耐德产品资料包含重复型号或无效来源")
    return products


def _terms(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).lower()
    # 询价常写 32A、24V，而厂家资料会写 32 A、24 V。
    normalized = re.sub(r"(?<=\d)\s+(?=[a-z])", "", normalized)
    terms = set(re.findall(r"[a-z0-9]{2,}", normalized))
    for segment in re.findall(r"[\u4e00-\u9fff]+", normalized):
        terms.update(segment[index:index + 2] for index in range(len(segment) - 1))
    return terms


def _exact_model_in_query(model: str, query: str) -> bool:
    return bool(re.search(rf"(?<![a-z0-9]){re.escape(model.lower())}(?![a-z0-9])", query))


def _electrical_tokens(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKC", value).lower()
    normalized = re.sub(r"(?<=\d)\s+(?=[avp])", "", normalized)
    return set(re.findall(r"(?<![a-z0-9])\d+(?:\.\d+)?[avp](?![a-z0-9])", normalized))


def search_knowledge(query: str, *, limit: int = 3, path: Path = CATALOG_PATH) -> list[KnowledgeHit]:
    """词项检索并优先匹配完整型号；不进行相似型号自动替代。"""
    normalized = unicodedata.normalize("NFKC", query[:160]).lower()
    query_terms = _terms(normalized)
    if not query_terms or limit < 1:
        return []
    products = _products(path)
    known_models = {product.model for product in products}
    model_tokens = {
        token for token in re.findall(
            r"(?<![A-Z0-9])([A-Z][A-Z0-9]{5,23})(?![A-Z0-9])", query.upper()
        ) if any(char.isdigit() for char in token)
    }
    # 问题明确给出未收录型号时不能用相近型号冒充答案。
    if model_tokens - known_models:
        return []
    requested_specs = _electrical_tokens(query)
    ranked = []
    for product in products:
        if model_tokens and product.model not in model_tokens:
            continue
        if requested_specs:
            document_specs = _electrical_tokens(" ".join(product.facts.values()))
            if not requested_specs <= document_specs:
                continue
        hit = product.as_hit()
        searchable = f"施耐德 {hit.title} {hit.excerpt.split('资料日期：', 1)[0]}"
        overlap = len(query_terms & _terms(searchable))
        exact = _exact_model_in_query(product.model, normalized)
        if exact or overlap >= 2:
            ranked.append((100 if exact else overlap, overlap, product.model, hit))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [hit for _, _, _, hit in ranked[:limit]]


def get_product_knowledge(model: str, *, path: Path = CATALOG_PATH) -> KnowledgeHit | None:
    """只按完整型号查询；未知型号不做近似猜测。"""
    normalized = model.strip().upper()
    if not re.fullmatch(r"[A-Z0-9]{4,24}", normalized):
        return None
    return next((item.as_hit() for item in _products(path) if item.model == normalized), None)


def compare_product_models(
    left_model: str, right_model: str, *, path: Path = CATALOG_PATH,
) -> tuple[dict, list[KnowledgeHit]]:
    """确定性列出两个型号的已收录事实差异，不判断替代或适用性。"""
    by_model = {item.model: item for item in _products(path)}
    left = by_model.get(left_model.strip().upper())
    right = by_model.get(right_model.strip().upper())
    if left is None or right is None or left.model == right.model:
        return {"status": "unknown_or_duplicate_model", "note": "需提供两个已收录且不同的完整型号。"}, []
    keys = sorted(set(left.facts) | set(right.facts))
    differences = {
        key: {left.model: left.facts.get(key), right.model: right.facts.get(key)}
        for key in keys if left.facts.get(key) != right.facts.get(key)
    }
    shared = {
        key: left.facts[key]
        for key in keys if key in left.facts and key in right.facts and left.facts[key] == right.facts[key]
    }
    return {
        "status": "compared", "models": [left.model, right.model],
        "same_family": left.family == right.family,
        "shared": shared, "differences": differences,
        "note": "只比较已收录的官方资料；不代表型号可替换、已批准选型或供应商报价。",
    }, [left.as_hit(), right.as_hit()]
