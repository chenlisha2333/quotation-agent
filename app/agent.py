"""确定性报价工作流；人工补价按任务内明细 ID 完整提交。"""

import uuid
from decimal import Decimal
from typing import Dict, List, Literal, Optional, TypedDict

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from . import db
from .config import DEFAULT_PROFIT_RATE, QUOTE_CURRENCY
from .models import PriceValidationResult, Product, QuotationItem, QuotationStatus
from .pricing import as_decimal, calculate_item_price
from .product_policy import eligible_price, normalized_brand, product_issue
from .tools import search_local_price_tool, validate_price_tool


class GraphState(TypedDict):
    task_id: str
    customer_name: str
    currency: Literal["CNY"]
    profit_rate: Decimal
    products: List[Product]
    items: List[QuotationItem]
    status: str
    messages: List[str]
    pending_products: List[Product]
    review_reasons: Dict[str, str]
    manual_prices: Dict[str, Decimal]
    validation_results: Dict[str, PriceValidationResult]
    revision: int
    approvals: Dict[str, dict]
    published_revision: Optional[int]


class TaskSnapshot(BaseModel):
    """把 LangGraph 状态中的 Decimal 和 Pydantic 对象可靠地写入 JSON。"""

    task_id: str
    customer_name: str
    currency: Literal["CNY"]
    profit_rate: Decimal
    products: List[Product]
    items: List[QuotationItem]
    status: str
    messages: List[str]
    pending_products: List[Product]
    review_reasons: Dict[str, str]
    manual_prices: Dict[str, Decimal]
    validation_results: Dict[str, PriceValidationResult]
    revision: int = 0
    approvals: Dict[str, dict] = Field(default_factory=dict)
    published_revision: Optional[int] = None


def get_task_state(task_id: str) -> Optional[GraphState]:
    stored = db.load_task_snapshot(task_id)
    if stored is None:
        return None
    version, payload = stored
    snapshot = TaskSnapshot.model_validate(payload)
    state = snapshot.__dict__.copy()  # model_dump 会把嵌套 Product 转成 dict。
    state["revision"] = version
    return state


def save_task_state(
    state: GraphState, event_type: str = "状态更新", details: dict | None = None,
    published_quote: tuple[int, dict] | None = None,
) -> None:
    snapshot = TaskSnapshot.model_validate(state)
    version = db.save_task_snapshot(
        state["task_id"], state["status"], snapshot.model_dump(mode="json"),
        state["revision"], event_type, details, published_quote,
    )
    state["revision"] = version


def init_state(
    products: List[Product], customer_name: str = "", profit_rate: Decimal = DEFAULT_PROFIT_RATE
) -> GraphState:
    # 即使两行名称、型号都相同，行号仍唯一；原始清单内容不被悄悄替换。
    identified = [
        product.model_copy(update={"item_id": f"line-{index}", "brand": normalized_brand(product.brand)})
        for index, product in enumerate(products, start=1)
    ]
    return {
        "task_id": str(uuid.uuid4()),
        "customer_name": customer_name,
        "currency": QUOTE_CURRENCY,
        "profit_rate": profit_rate,
        "products": identified,
        "items": [],
        "status": QuotationStatus.PENDING.value,
        "messages": ["开始人民币报价流程"],
        "pending_products": [],
        "review_reasons": {},
        "manual_prices": {},
        "validation_results": {},
        "revision": 0,
        "approvals": {},
        "published_revision": None,
    }


def parse_step(state: GraphState) -> GraphState:
    state["status"] = QuotationStatus.PARSING.value
    state["messages"].append(f"已解析 {len(state['products'])} 个产品")
    return state


def price_lookup_step(state: GraphState) -> GraphState:
    state["status"] = QuotationStatus.PRICING.value
    items: List[QuotationItem] = []
    pending: List[Product] = []
    reasons: Dict[str, str] = {}

    for product in state["products"]:
        issue = product_issue(product)
        record = search_local_price_tool(product) if issue is None else None
        if record is not None and eligible_price(product, record):
            item = calculate_item_price(product, record.price, state["profit_rate"])
            if item.unit_price > 0:
                item.price_source = record.source
                item.price_precision = record.price_precision
                items.append(item)
                continue
            issue = "单价舍入后为零；请核对价格来源并人工录入有效成本价"

        reason = issue or "缺少已核准且有效的施耐德人民币价格；请按明细 ID 人工录入成本价"
        item = calculate_item_price(product, None, state["profit_rate"])
        item.remark = reason
        items.append(item)
        pending.append(product)
        reasons[item.item_id] = reason

    state["items"] = items
    state["pending_products"] = pending
    state["review_reasons"] = reasons
    state["messages"].append(f"价格核对完成：{len(items) - len(pending)} 行有可用价，{len(pending)} 行待处理")
    save_task_state(state, "价格查询", {"待处理行": list(reasons)})
    return state


def validate_prices_step(state: GraphState) -> GraphState:
    state["status"] = QuotationStatus.VALIDATING.value
    results: Dict[str, PriceValidationResult] = {}
    warnings = 0
    for item in state["items"]:
        if item.cost_price is None:
            continue
        result = validate_price_tool(item.product, item.cost_price)
        results[item.item_id] = result
        if result.warning:
            warnings += 1
            item.remark = f"{item.remark}; {result.warning}".strip("; ")
    state["validation_results"] = results
    state["messages"].append(f"价格核查发现 {warnings} 个警告" if warnings else "价格核查通过")
    save_task_state(state, "价格核查", {"警告数量": warnings})
    return state


def human_review_decision(state: GraphState) -> str:
    if state["pending_products"]:
        state["status"] = QuotationStatus.HUMAN_REVIEW.value
        return "human_review"
    return "calculate"


def calculate_step(state: GraphState, *, persist: bool = True) -> GraphState:
    if state["pending_products"]:
        raise ValueError("仍有待处理明细，不能完成报价计算")
    state["status"] = QuotationStatus.CALCULATING.value
    total = sum((item.total_price for item in state["items"]), Decimal("0.00"))
    state["status"] = QuotationStatus.DONE.value
    state["messages"].append(f"报价草稿计算完成，总价：CNY {total:.2f}")
    if persist:
        save_task_state(state, "草稿计算完成", {"总价_CNY": str(total)})
    return state


def human_review_step(state: GraphState) -> GraphState:
    state["status"] = QuotationStatus.HUMAN_REVIEW.value
    state["messages"].append("等待修正产品信息或按明细 ID 录入成本价")
    save_task_state(state, "等待人工处理", {"待处理行": list(state["review_reasons"])})
    return state


def apply_manual_prices(task_id: str, prices: Dict[str, Decimal]) -> Optional[GraphState]:
    """一次性确认全部可补价明细；任何校验失败都不修改已有任务。"""
    state = get_task_state(task_id)
    if state is None:
        return None
    checked_input = {key: as_decimal(value, "人工成本价") for key, value in prices.items()}
    if state["status"] == QuotationStatus.DONE.value and state["manual_prices"] == checked_input:
        return state  # 同一请求重试返回原结果，不再生成事件或版本。
    if state["status"] != QuotationStatus.HUMAN_REVIEW.value:
        raise ValueError("当前任务不处于人工确认状态")

    for product in state["pending_products"]:
        issue = product_issue(product)
        if issue:
            raise ValueError(f"{product.item_id}：{issue}")

    required = {product.item_id for product in state["pending_products"]}
    if set(prices) != required:
        raise ValueError(f"须按明细 ID 一次提交全部待补价行：{', '.join(sorted(required))}")

    replacements: Dict[str, QuotationItem] = {}
    checked_prices: Dict[str, Decimal] = {}
    for product in state["pending_products"]:
        price = as_decimal(prices[product.item_id], "人工成本价")
        if price <= 0:
            raise ValueError(f"{product.item_id}：人工成本价必须大于零")
        item = calculate_item_price(product, price, state["profit_rate"])
        if item.unit_price <= 0:
            raise ValueError(f"{product.item_id}：舍入后单价为零，请核对成本价")
        item.price_source = "人工录价"
        item.price_precision = "manual"
        item.remark = "人工录价，报价草稿待审批"
        replacements[item.item_id] = item
        checked_prices[item.item_id] = price

    state["items"] = [replacements.get(item.item_id, item) for item in state["items"]]
    state["manual_prices"] = checked_prices
    state["pending_products"] = []
    state["review_reasons"] = {}
    state["messages"].append(f"收到 {len(checked_prices)} 行人工成本价")
    calculate_step(state, persist=False)
    save_task_state(
        state, "人工补价并完成草稿",
        {"价格_CNY": {key: str(value) for key, value in checked_prices.items()}},
    )
    return state


def build_agent():
    workflow = StateGraph(GraphState)
    workflow.add_node("parse", parse_step)
    workflow.add_node("price_lookup", price_lookup_step)
    workflow.add_node("validate_prices", validate_prices_step)
    workflow.add_node("calculate", calculate_step)
    workflow.add_node("human_review", human_review_step)
    workflow.set_entry_point("parse")
    workflow.add_edge("parse", "price_lookup")
    workflow.add_edge("price_lookup", "validate_prices")
    workflow.add_conditional_edges("validate_prices", human_review_decision, {
        "human_review": "human_review", "calculate": "calculate",
    })
    workflow.add_edge("human_review", END)
    workflow.add_edge("calculate", END)
    return workflow.compile()


def run_quotation_agent(
    products: List[Product], customer_name: str = "", profit_rate: Decimal = DEFAULT_PROFIT_RATE
) -> GraphState:
    return build_agent().invoke(init_state(products, customer_name, profit_rate))
