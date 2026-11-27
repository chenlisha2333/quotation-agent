"""人民币、施耐德产品匹配和逐行人工确认。"""

from decimal import Decimal
from io import BytesIO
from pathlib import Path

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app import agent, db, main
from app.models import PriceRecord, Product
from app.parser import parse_excel
from app.pricing import calculate_item_price
from app.product_policy import eligible_price, product_issue


def product(model: str = "A1", spec: str = "32A") -> Product:
    return Product(name="断路器", model=model, spec=spec, brand="施耐德", quantity=2, unit="个")


def approved_price(**changes) -> PriceRecord:
    values = dict(
        product_name="断路器", model="A1", spec="32A", brand="Schneider Electric",
        unit="个", currency="CNY", source="供应商报价", source_reference="QUOTE-001",
        valid_until="2099-12-31", verified=True, price=Decimal("12.34"),
        price_precision="decimal_text",
    )
    values.update(changes)
    return PriceRecord(**values)


def test_schneider_identity_and_currency_are_required():
    wanted = product()
    good = approved_price()
    assert eligible_price(wanted, good)
    assert not eligible_price(wanted, approved_price(brand="正泰"))
    assert not eligible_price(wanted, approved_price(currency=None))
    assert not eligible_price(wanted, approved_price(model="A2"))
    assert not eligible_price(wanted, approved_price(spec="16A"))
    assert not eligible_price(wanted, approved_price(unit="台"))
    assert not eligible_price(wanted, approved_price(valid_until="2020-01-01"))
    assert not eligible_price(wanted, approved_price(verified=False))
    assert not eligible_price(wanted, approved_price(source_reference=None))
    assert not eligible_price(wanted, approved_price(price_precision="legacy_real"))
    assert product_issue(Product(name="断路器", model="A1", brand="正泰"))


def test_database_only_returns_approved_exact_cny_price(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db()
    db.insert_price(approved_price(price_precision="unknown"))
    db.insert_price(approved_price(model="A2", price=Decimal("99.99")))
    db.insert_price(approved_price(brand="正泰", price=Decimal("88.88")))
    db.insert_price(approved_price(verified=False, price=Decimal("77.77")))
    matches = db.search_local_price(product())
    assert len(matches) == 1
    assert matches[0].price == Decimal("12.34")
    assert matches[0].brand == "施耐德"
    assert matches[0].currency == "CNY"
    assert matches[0].id is not None
    assert db.search_local_price(product(model="A3")) == []


def test_quote_snapshot_keeps_price_record_provenance_after_price_record_changes(monkeypatch):
    db.init_db()
    db.insert_price(approved_price())
    frozen_record = db.search_local_price(product())[0]
    monkeypatch.setattr(agent, "search_local_price_tool", lambda _: frozen_record)

    state = agent.run_quotation_agent([product()])
    saved = agent.get_task_state(state["task_id"])
    item = saved["items"][0]
    assert item.price_record_id == frozen_record.id
    assert item.price_source_reference == "QUOTE-001"
    assert item.price_valid_until == "2099-12-31"
    assert item.price_evidence_status == "verified"

    with db.get_conn() as conn:
        conn.execute("UPDATE price_records SET source_reference = 'QUOTE-CHANGED' WHERE id = ?", (frozen_record.id,))
    reloaded = agent.get_task_state(state["task_id"])["items"][0]
    assert reloaded.price_source_reference == "QUOTE-001"
    assert reloaded.price_record_id == frozen_record.id


def test_duplicate_names_are_confirmed_by_item_id_and_all_prices_required(monkeypatch):
    monkeypatch.setattr(agent, "search_local_price_tool", lambda p: None)
    state = agent.run_quotation_agent([product("A1"), product("A2")], profit_rate=Decimal("0"))
    assert state["status"] == "人工确认"
    assert [item.item_id for item in state["items"]] == ["line-1", "line-2"]
    assert state["currency"] == "CNY"
    with pytest.raises(ValueError, match="全部待补价行"):
        agent.apply_manual_prices(state["task_id"], {"line-1": Decimal("10")})
    assert all(item.cost_price is None for item in state["items"])
    done = agent.apply_manual_prices(state["task_id"], {
        "line-1": Decimal("10"), "line-2": Decimal("20"),
    })
    assert done["status"] == "完成"
    assert [item.cost_price for item in done["items"]] == [Decimal("10"), Decimal("20")]
    assert all(item.price_evidence_status == "pending_procurement_evidence" for item in done["items"])
    assert all(item.price_source_reference is None and item.price_record_id is None for item in done["items"])
    assert sum(item.total_price for item in done["items"]) == Decimal("60.00")
    with pytest.raises(ValueError, match="不处于人工确认状态"):
        agent.apply_manual_prices(state["task_id"], {"line-1": Decimal("11")})


def test_other_brand_cannot_be_fixed_by_only_entering_price(monkeypatch):
    monkeypatch.setattr(agent, "search_local_price_tool", lambda p: pytest.fail("其他品牌不应查价"))
    state = agent.run_quotation_agent([Product(name="断路器", model="X1", brand="正泰")])
    assert state["status"] == "人工确认"
    assert "施耐德" in state["review_reasons"]["line-1"]
    response = main._task_response(state)
    assert response["pending_products"][0]["can_enter_price"] is False
    assert response["currency"] == "CNY"
    with pytest.raises(ValueError, match="修改清单"):
        agent.apply_manual_prices(state["task_id"], {"line-1": Decimal("10")})


def test_existing_mixed_brand_sample_stays_pending(monkeypatch):
    monkeypatch.setattr(agent, "search_local_price_tool", lambda p: None)
    sample = Path(__file__).resolve().parent.parent / "sample_products.xlsx"
    state = agent.run_quotation_agent(parse_excel(sample))
    assert state["currency"] == "CNY"
    assert state["status"] == "人工确认"
    assert len(state["pending_products"]) == 5
    assert state["pending_products"][0].brand == "正泰"
    assert "施耐德" in state["review_reasons"]["line-1"]


def test_export_uses_server_task_and_rejects_incomplete_quotes(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    client = TestClient(main.app)
    assert client.post("/export", json={"task_id": "missing"}).status_code == 404

    other = Product(name="断路器", model="X1", brand="正泰")
    invalid = agent.init_state([other], customer_name="测试客户")
    invalid["task_id"] = "invalid"
    invalid["status"] = "完成"
    invalid["items"] = [calculate_item_price(invalid["products"][0], "10")]
    agent.save_task_state(invalid, "测试非法导出")
    assert client.post("/export", json={"task_id": "invalid"}).status_code == 409

    ready = agent.init_state([product()], customer_name="测试客户")
    ready["task_id"] = "ready"
    ready["status"] = "完成"
    ready["items"] = [calculate_item_price(ready["products"][0], "12.34")]
    agent.save_task_state(ready, "测试合法导出")
    response = client.post("/export", json={"task_id": "ready"})
    assert response.status_code == 200
    workbook = openpyxl.load_workbook(BytesIO(response.content), data_only=True)
    assert "人民币 CNY" in workbook["报价单"]["A1"].value
    assert "测试客户" in workbook["报价单"]["A2"].value
    assert workbook["计算依据-内部"]["Q2"].value == "CNY"
