"""技术、采购、商务依次批准后冻结客户报价版本。"""

from datetime import datetime, timezone
from decimal import Decimal
import os

from . import agent
from .auth import Approver
from .models import QuotationStatus
from .product_policy import product_issue

APPROVAL_ORDER = ("technical", "procurement", "commercial")


def approve_quote(task_id: str, stage: str, approver: Approver, reason: str):
    if stage not in APPROVAL_ORDER or approver.role != stage:
        raise ValueError("审批阶段与角色不匹配")
    reason = reason.strip()
    if not reason:
        raise ValueError("必须填写审批意见")
    state = agent.get_task_state(task_id)
    if state is None:
        return None
    if state["status"] != QuotationStatus.DONE.value or state["pending_products"]:
        raise ValueError("报价草稿未完成，不能审批")
    if not state["items"] or any(
        product_issue(item.product) or item.cost_price is None or item.cost_price <= 0
        or item.currency != "CNY" for item in state["items"]
    ):
        raise ValueError("报价明细不符合施耐德人民币审批条件")
    approvals = state["approvals"]
    previous = approvals.get(stage)
    if previous:
        if previous["actor"] == approver.actor and previous["reason"] == reason:
            return state  # 同一审批请求重试，保留原版本和时间。
        raise ValueError("该阶段已审批，不能覆盖记录")
    position = APPROVAL_ORDER.index(stage)
    if any(required not in approvals for required in APPROVAL_ORDER[:position]):
        raise ValueError("须按技术、采购、商务顺序审批")
    if any(entry["actor"] == approver.actor for entry in approvals.values()):
        raise ValueError("同一人员不能兼任多个审批阶段")
    if stage == "commercial":
        approved_rule = os.getenv("QUOTE_APPROVED_RULE_ID", "").strip()
        if not approved_rule or any(item.pricing_rule_id != approved_rule for item in state["items"]):
            raise ValueError("定价规则尚未由企业确认，不能发布客户报价")

    state["approvals"] = {
        **approvals,
        stage: {
            "actor": approver.actor, "role": stage, "reason": reason,
            "approved_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    published_quote = None
    if stage == "commercial":
        state["published_revision"] = 1
        # 冻结含成本和规则依据的完整快照；客户导出只读取白名单字段。
        snapshot = agent.TaskSnapshot.model_validate(state).model_dump(mode="json")
        snapshot["total_price"] = str(sum((i.total_price for i in state["items"]), Decimal("0.00")))
        published_quote = (1, snapshot)
    agent.save_task_state(
        state, "报价审批" if published_quote is None else "报价版本发布",
        {"stage": stage, "actor": approver.actor, "reason": reason},
        published_quote,
    )
    return state
