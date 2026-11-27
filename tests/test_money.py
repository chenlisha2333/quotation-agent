"""金额从输入、计算、价格库到文件输出的回归测试。"""

import sqlite3
from decimal import Decimal

import openpyxl
import pytest
from fastapi.testclient import TestClient

from app import agent, db, main
from app.exporter import export_quotation
from app.models import PriceRecord, Product, PriceValidationResult
from app.pricing import calculate_item_price
from app.validator import validate_price_basic


def test_unit_rounds_before_line_total():
    item = calculate_item_price(Product(name="端子", quantity=3), "0.333", "0", "1")
    assert item.cost_price == Decimal("0.333")
    assert item.unit_price == Decimal("0.33")
    assert item.total_price == Decimal("0.99")
    assert item.total_price == item.unit_price * item.product.quantity
    assert item.pricing_rule_id == "draft-custom-rate-0-discount-1"
    assert item.rounding_mode == "ROUND_HALF_UP"


def test_decimal_input_and_round_half_up():
    item = calculate_item_price(Product(name="端子"), "0.005", "0", "1")
    assert item.unit_price == Decimal("0.01")
    assert calculate_item_price(Product(name="端子"), "0.1", "0", "1").unit_price == Decimal("0.10")


def test_default_price_uses_cost_times_1_point_2():
    item = calculate_item_price(Product(name="断路器", quantity=2), "10.01")
    assert item.profit_rate == Decimal("0.20")
    assert item.unit_price == Decimal("12.01")
    assert item.total_price == Decimal("24.02")
    assert item.pricing_rule_id == "draft-cost-times-1.2-v1"


@pytest.mark.parametrize("cost,rate,discount", [
    ("-1", "0", "1"),
    ("1", "-0.1", "1"),
    ("1", "0", "1.1"),
    ("NaN", "0", "1"),
])
def test_invalid_money_inputs_are_rejected(cost, rate, discount):
    with pytest.raises(ValueError):
        calculate_item_price(Product(name="端子"), cost, rate, discount)


def test_zero_price_needs_review_and_negative_validation_is_reported():
    item = calculate_item_price(Product(name="端子"), "0")
    assert item.cost_price is None
    assert item.total_price == Decimal("0.00")
    result = validate_price_basic(Product(name="端子"), Decimal("-1"))
    assert result.is_valid is False
    assert result.original_price == Decimal("-1")


def test_new_prices_are_exact_and_legacy_prices_are_marked(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    conn = sqlite3.connect(tmp_path / "quotation.db")
    conn.execute("CREATE TABLE price_records (id INTEGER PRIMARY KEY, product_name TEXT NOT NULL, model TEXT, category TEXT, source TEXT NOT NULL, price REAL NOT NULL, confidence REAL, fetched_at TEXT, created_at TEXT)")
    conn.execute("INSERT INTO price_records (product_name, source, price, fetched_at) VALUES ('旧端子', '旧库', 0.333, '2025-01-01')")
    conn.commit()
    conn.close()

    db.init_db()
    db.insert_price(PriceRecord(product_name="新端子", model=None, source="测试", price=Decimal("0.335")))
    conn = db.get_conn()
    new = db._price_record_from_row(conn.execute("SELECT * FROM price_records WHERE product_name = '新端子'").fetchone())
    old = db._price_record_from_row(conn.execute("SELECT * FROM price_records WHERE product_name = '旧端子'").fetchone())
    conn.close()
    assert new.price == Decimal("0.335")
    assert new.price_precision == "decimal_text"
    assert old.price_precision == "legacy_real"
    assert old.price == Decimal("0.333")
    assert db.search_local_price(Product(name="旧端子", brand="施耐德", model="A")) == []


def test_api_returns_money_as_strings(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path)
    product = Product(name="端子", quantity=3)
    item = calculate_item_price(product, "0.333", "0", "1")
    monkeypatch.setattr(main, "parse_excel", lambda path: [product])
    monkeypatch.setattr(main, "run_quotation_agent", lambda products, customer, rate, **kwargs: {
        "task_id": "demo", "revision": 1, "status": "完成", "currency": "CNY", "items": [item],
        "messages": [], "pending_products": [], "review_reasons": {},
    })
    response = TestClient(main.app).post(
        "/upload", data={"customer_name": "测试", "profit_rate": "0.20"},
        files={"file": ("demo.xlsx", b"demo", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["unit_price"] == "0.33"
    assert body["items"][0]["total_price"] == "0.99"
    assert body["total_price"] == "0.99"


def test_upload_rejects_old_markup_before_saving_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path)
    response = TestClient(main.app).post(
        "/upload", data={"profit_rate": "0.15"},
        files={"file": ("demo.xlsx", b"demo")},
    )
    assert response.status_code == 422
    assert list(tmp_path.iterdir()) == []


def test_graph_uses_exact_price_and_keeps_source(monkeypatch):
    record = PriceRecord(
        product_name="端子", model="A1", brand="施耐德", unit="个", currency="CNY",
        source="测试价格表", source_reference="DOC-1", valid_until="2099-12-31",
        verified=True, price=Decimal("0.335"), price_precision="decimal_text",
    )
    monkeypatch.setattr(agent, "search_local_price_tool", lambda product: record)
    monkeypatch.setattr(agent, "validate_price_tool", lambda product, price: PriceValidationResult(
        product_name=product.name, original_price=price, validated_price=price,
    ))
    state = agent.run_quotation_agent([Product(name="端子", model="A1", brand="施耐德", quantity=3)], profit_rate=Decimal("0"))
    assert state["status"] == "完成"
    assert state["items"][0].unit_price == Decimal("0.34")
    assert state["items"][0].total_price == Decimal("1.02")
    assert state["items"][0].price_source == "测试价格表"
    assert state["items"][0].price_precision == "decimal_text"


def test_manual_price_api_keeps_decimal_input(monkeypatch):
    product = Product(name="端子", model="A1", brand="施耐德", quantity=3)
    state = agent.init_state([product], profit_rate=Decimal("0"))
    identified = state["products"][0]
    state["items"] = [calculate_item_price(identified, None, "0")]
    state["pending_products"] = [identified]
    state["review_reasons"] = {"line-1": "待补价"}
    state["status"] = "人工确认"
    state["task_id"] = "manual-test"
    agent.save_task_state(state, "测试待补价")
    client = TestClient(main.app)
    rejected = client.post("/tasks/manual-test/confirm", json={"line-1": "-1"})
    assert rejected.status_code == 422
    response = client.post("/tasks/manual-test/confirm", json={"line-1": "0.335"})
    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["cost_price"] == "0.335"
    assert body["items"][0]["unit_price"] == "0.34"
    assert body["items"][0]["total_price"] == "1.02"
    assert body["items"][0]["price_source"] == "人工录价"


def test_export_keeps_decimal_audit_values(tmp_path):
    item = calculate_item_price(Product(name="端子", quantity=3), "0.333", "0", "1")
    item.price_source = "测试来源"
    path = export_quotation([item], output_path=tmp_path / "quote.xlsx")
    workbook = openpyxl.load_workbook(path, data_only=True)
    assert workbook["报价单"]["M6"].value == 0.99
    assert workbook["报价单"]["J5"].value == "加价率"
    audit = workbook["计算依据-内部"]
    assert audit["D2"].value == "0.333"
    assert audit["G2"].value == "0.33"
    assert audit["H2"].value == "0.99"
    assert audit["I2"].value == "测试来源"
