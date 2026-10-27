"""角色审批、冻结版本与客户版字段隔离。"""

import json
from decimal import Decimal
from io import BytesIO

import openpyxl
from fastapi.testclient import TestClient

from app import agent, db, main
from app.models import Product
from app.pricing import calculate_item_price


TOKENS = {
    "technical": "technical-secret-123456",
    "procurement": "procurement-secret-123456",
    "commercial": "commercial-secret-123456",
}


def headers(role: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKENS[role]}"}


def configured_task(monkeypatch) -> str:
    monkeypatch.setenv("QUOTE_APPROVED_RULE_ID", "draft-cost-times-1.2-v1")
    monkeypatch.setenv("QUOTE_APPROVERS_JSON", json.dumps([
        {"actor": "技术员", "role": "technical", "token": TOKENS["technical"]},
        {"actor": "采购员", "role": "procurement", "token": TOKENS["procurement"]},
        {"actor": "商务经理", "role": "commercial", "token": TOKENS["commercial"]},
    ]))
    state = agent.init_state(
        [Product(name="断路器", model="型号A", brand="施耐德", quantity=2)],
        customer_name="测试客户", profit_rate=Decimal("0.20"),
    )
    item = calculate_item_price(state["products"][0], "10.00", state["profit_rate"])
    item.price_source = "人工录价"
    state["items"] = [item]
    state["status"] = "完成"
    agent.save_task_state(state, "测试草稿")
    return state["task_id"]


def test_role_order_release_and_customer_workbook(monkeypatch, tmp_path):
    task_id = configured_task(monkeypatch)
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    client = TestClient(main.app)
    customer_request = {"task_id": task_id, "format": "customer"}
    assert client.get(f"/tasks/{task_id}").status_code == 401
    technical_view = client.get(f"/tasks/{task_id}", headers=headers("technical")).json()
    assert technical_view["items"][0]["cost_price"] is None
    assert technical_view["total_price"] is None
    procurement_view = client.get(f"/tasks/{task_id}", headers=headers("procurement")).json()
    assert procurement_view["items"][0]["cost_price"] == "10.00"
    assert procurement_view["items"][0]["unit_price"] is None
    assert client.post("/export", json={"task_id": task_id}, headers=headers("technical")).status_code == 403
    assert client.post("/export", json=customer_request, headers=headers("commercial")).status_code == 409
    assert client.post(f"/tasks/{task_id}/approve/commercial", json={"reason": "同意"}, headers=headers("commercial")).status_code == 409
    assert client.post(f"/tasks/{task_id}/approve/technical", json={"reason": "同意"}, headers=headers("procurement")).status_code == 403

    for stage, reason in [
        ("technical", "型号已核对"),
        ("procurement", "成本已核对"),
        ("commercial", "条款已核对"),
    ]:
        response = client.post(
            f"/tasks/{task_id}/approve/{stage}", json={"reason": reason}, headers=headers(stage),
        )
        assert response.status_code == 200, response.text
    assert response.json()["published_revision"] == 1
    frozen = db.load_quote_revision(task_id, 1)
    assert frozen["items"][0]["cost_price"] == "10.00"
    assert set(frozen["approvals"]) == {"technical", "procurement", "commercial"}

    customer = client.post("/export", json=customer_request, headers=headers("commercial"))
    assert customer.status_code == 200
    workbook = openpyxl.load_workbook(BytesIO(customer.content), data_only=True)
    assert workbook.sheetnames == ["客户报价单"]
    sheet = workbook.active
    assert sheet["B6"].value == "断路器"
    assert sheet["H6"].value == 12
    assert sheet["I6"].value == 24
    texts = [str(cell.value) for row in sheet for cell in row if cell.value is not None]
    assert not any("成本" in text or "加价" in text or "人工录价" in text for text in texts)
    assert client.post("/export", json=customer_request, headers=headers("technical")).status_code == 403

    version = agent.get_task_state(task_id)["revision"]
    retry = client.post(
        f"/tasks/{task_id}/approve/commercial",
        json={"reason": "条款已核对"}, headers=headers("commercial"),
    )
    assert retry.status_code == 200
    assert retry.json()["revision"] == version
    assert db.load_quote_revision(task_id, 1) == frozen


def test_approval_requires_configuration_and_reason(monkeypatch):
    state = agent.init_state([Product(name="断路器", model="A", brand="施耐德")])
    state["items"] = [calculate_item_price(state["products"][0], "10")]
    state["status"] = "完成"
    agent.save_task_state(state, "测试草稿")
    client = TestClient(main.app)
    response = client.post(
        f"/tasks/{state['task_id']}/approve/technical", json={"reason": "同意"}
    )
    assert response.status_code == 503
    configured_task(monkeypatch)
    response = client.post(
        f"/tasks/{state['task_id']}/approve/technical", json={"reason": "  "},
        headers=headers("technical"),
    )
    assert response.status_code == 409


def test_customer_release_blocks_unapproved_pricing_rule(monkeypatch):
    task_id = configured_task(monkeypatch)
    client = TestClient(main.app)
    for stage in ("technical", "procurement"):
        assert client.post(
            f"/tasks/{task_id}/approve/{stage}", json={"reason": "已核对"},
            headers=headers(stage),
        ).status_code == 200
    monkeypatch.delenv("QUOTE_APPROVED_RULE_ID")
    response = client.post(
        f"/tasks/{task_id}/approve/commercial", json={"reason": "同意"},
        headers=headers("commercial"),
    )
    assert response.status_code == 409
    assert agent.get_task_state(task_id)["published_revision"] is None
    assert db.load_quote_revision(task_id, 1) is None
