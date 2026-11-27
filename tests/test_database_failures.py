"""价格库故障必须与正常的无合格价格区分。"""

import sqlite3
from decimal import Decimal
from io import BytesIO

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import agent, db, main
from app.models import PriceRecord, Product


def product() -> Product:
    return Product(
        name="断路器", model="A1", spec="32A", brand="施耐德", quantity=1, unit="个"
    )


def approved_price() -> PriceRecord:
    return PriceRecord(
        product_name="断路器", model="A1", spec="32A", brand="施耐德", unit="个",
        currency="CNY", source="合成测试凭证", source_reference="SYNTHETIC-1",
        valid_until="2099-12-31", verified=True, price=Decimal("12.34"),
        price_precision="decimal_text",
    )


def test_price_repository_raises_safe_error_when_sqlite_read_fails(monkeypatch, caplog):
    def fail_connection():
        raise sqlite3.OperationalError("sensitive/path/quotation.db is locked")

    monkeypatch.setattr(db, "get_conn", fail_connection)

    with pytest.raises(db.PriceLookupError) as error:
        db.search_local_price(product())

    assert error.value.category == "database"
    assert error.value.public_message == "本地价格库暂不可用，请稍后重试"
    assert "sensitive/path" not in caplog.text


def test_invalid_price_record_is_not_silently_treated_as_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.init_db()
    with sqlite3.connect(tmp_path / "quotation.db") as conn:
        conn.execute(
            "INSERT INTO price_records "
            "(product_name, model, spec, brand, unit, currency, source, source_reference, "
            "valid_until, verified, price, price_decimal, confidence, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("断路器", "A1", "32A", "施耐德", "个", "CNY", "合成来源", "SYNTHETIC-1",
             "2099-12-31", 1, 12.34, "not-a-decimal", 1.0, "2099-01-01"),
        )

    with pytest.raises(db.PriceLookupError) as error:
        db.search_local_price(product())

    assert error.value.category == "invalid_record"


def test_database_failure_during_upload_returns_503_without_task_or_file(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")

    def fail_lookup(_product):
        raise db.PriceLookupError("database", "本地价格库暂不可用，请稍后重试")

    monkeypatch.setattr(agent, "search_local_price_tool", fail_lookup)
    excel = BytesIO()
    pd.DataFrame({
        "产品名称": ["断路器"], "型号": ["A1"], "规格": ["32A"],
        "品牌": ["施耐德"], "数量": [1], "单位": ["个"],
    }).to_excel(excel, index=False)

    with TestClient(main.app) as client:
        response = client.post(
            "/upload",
            files={"file": ("list.xlsx", excel.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )

    assert response.status_code == 503
    assert response.json()["detail"] == "本地价格库暂不可用，请稍后重试"
    assert "task_id=" in caplog.text
    assert "item_id=line-1" in caplog.text
    assert list((tmp_path / "uploads").glob("*")) == []
    with sqlite3.connect(db.DATA_DIR / "quotation.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM quotation_tasks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0] == 0


def test_historical_lookup_failure_does_not_leave_partial_quote_snapshot(monkeypatch):
    db.init_db()
    monkeypatch.setattr(agent, "search_local_price_tool", lambda _product: approved_price())

    def fail_historical_lookup(_product):
        raise db.PriceLookupError("database", "本地价格库暂不可用，请稍后重试")

    monkeypatch.setattr(db, "search_local_price", fail_historical_lookup)

    with pytest.raises(db.PriceLookupError):
        agent.run_quotation_agent([product()])

    with sqlite3.connect(db.DATA_DIR / "quotation.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM quotation_tasks").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0] == 0


def test_healthy_empty_price_lookup_remains_a_human_review_case(monkeypatch):
    monkeypatch.setattr(agent, "search_local_price_tool", lambda _product: None)

    state = agent.run_quotation_agent([product()])

    assert state["status"] == "人工确认"
    assert "缺少已核准且有效" in state["review_reasons"]["line-1"]
    assert db.load_task_snapshot(state["task_id"]) is not None
