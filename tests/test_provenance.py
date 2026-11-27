"""报价凭证来源与上传原件关联测试，全部使用合成数据。"""

import hashlib
from decimal import Decimal
from io import BytesIO

import openpyxl
from fastapi.testclient import TestClient

from app import agent, main
from app.auth import Approver
from app.models import Product, QuotationItem
from app.pricing import calculate_item_price


def test_upload_persists_file_id_and_hash_without_original_filename(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    monkeypatch.setattr(main, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(agent, "search_local_price_tool", lambda _: None)
    workbook = openpyxl.Workbook()
    workbook.active.append(["产品名称", "型号", "品牌", "数量"])
    workbook.active.append(["断路器", "A1", "施耐德", 1])
    content = BytesIO()
    workbook.save(content)
    payload = content.getvalue()

    with TestClient(main.app) as client:
        response = client.post(
            "/upload",
            files={"file": ("sensitive-original-name.xlsx", payload, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )

    assert response.status_code == 200, response.text
    state = agent.get_task_state(response.json()["task_id"])
    file_id = state["input_file_id"]
    assert len(file_id) == 32
    assert state["input_file_extension"] == ".xlsx"
    assert state["input_file_sha256"] == hashlib.sha256(payload).hexdigest()
    stored_file = upload_dir / f"{file_id}{state['input_file_extension']}"
    assert stored_file.read_bytes() == payload
    assert "input_file" not in response.json()
    assert "sensitive-original-name" not in stored_file.name


def test_old_task_snapshot_and_quotation_items_load_without_provenance_fields():
    old_item = {
        "item_id": "line-1",
        "product": {"name": "断路器", "model": "A1", "brand": "施耐德", "quantity": 1},
        "currency": "CNY", "cost_price": "10.00", "profit_rate": "0.20", "discount": "1",
        "unit_price": "12.00", "total_price": "12.00", "pricing_rule_id": "old-rule",
        "rounding_mode": "ROUND_HALF_UP", "remark": "旧快照",
    }
    restored_item = QuotationItem.model_validate(old_item)
    assert restored_item.price_record_id is None
    assert restored_item.price_evidence_status == "not_applicable"
    old_snapshot = {
        "task_id": "old-task", "customer_name": "", "currency": "CNY", "profit_rate": "0.20",
        "products": [old_item["product"]], "items": [old_item], "status": "完成", "messages": [],
        "pending_products": [], "review_reasons": {}, "manual_prices": {}, "validation_results": {},
    }
    restored_snapshot = agent.TaskSnapshot.model_validate(old_snapshot)
    assert restored_snapshot.input_file_id is None
    assert restored_snapshot.input_file_extension is None
    assert restored_snapshot.input_file_sha256 is None


def test_technical_response_hides_supplier_provenance():
    state = agent.init_state([Product(name="断路器", model="A1", brand="施耐德")])
    item = calculate_item_price(state["products"][0], Decimal("10"))
    item.price_source = "供应商报价"
    item.price_record_id = 42
    item.price_source_reference = "SUPPLIER-QUOTE-SECRET"
    item.price_valid_until = "2099-12-31"
    item.price_evidence_status = "verified"
    state["items"] = [item]

    response = main._task_response(state, Approver(actor="工程师", role="technical", token="local-test-token"))

    for field in ("price_record_id", "price_source_reference", "price_valid_until", "price_evidence_status"):
        assert response["items"][0][field] is None
