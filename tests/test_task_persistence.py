"""任务重启恢复、幂等补价、并发版本冲突与审计事件。"""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app import agent, db, main
from app.models import Product


def _product() -> Product:
    return Product(name="断路器", model="经核实型号", brand="施耐德", quantity=2)


def test_pending_task_can_be_loaded_and_completed_after_reload(monkeypatch):
    monkeypatch.setattr(agent, "search_local_price_tool", lambda product: None)
    created = agent.run_quotation_agent([_product()], customer_name="甲公司", profit_rate=Decimal("0"))
    task_id = created["task_id"]
    loaded = agent.get_task_state(task_id)
    assert loaded is not created
    assert loaded["status"] == "人工确认"
    assert loaded["customer_name"] == "甲公司"
    assert isinstance(loaded["pending_products"][0], Product)
    assert isinstance(loaded["profit_rate"], Decimal)

    client = TestClient(main.app)
    assert client.get(f"/tasks/{task_id}").json()["revision"] == loaded["revision"]
    response = client.post(f"/tasks/{task_id}/confirm", json={"line-1": "12.34"})
    assert response.status_code == 200
    reloaded = agent.get_task_state(task_id)
    assert reloaded["status"] == "完成"
    assert reloaded["items"][0].cost_price == Decimal("12.34")
    assert reloaded["items"][0].total_price == Decimal("24.68")
    events = db.list_task_events(task_id)
    assert events[-1]["event_type"] == "人工补价并完成草稿"
    assert events[-1]["details"]["价格_CNY"] == {"line-1": "12.34"}


def test_identical_confirmation_is_idempotent_but_changed_price_is_rejected(monkeypatch):
    monkeypatch.setattr(agent, "search_local_price_tool", lambda product: None)
    state = agent.run_quotation_agent([_product()], profit_rate=Decimal("0"))
    task_id = state["task_id"]
    first = agent.apply_manual_prices(task_id, {"line-1": Decimal("10")})
    events_before = db.list_task_events(task_id)
    second = agent.apply_manual_prices(task_id, {"line-1": Decimal("10.00")})
    assert second["revision"] == first["revision"]
    assert db.list_task_events(task_id) == events_before
    with pytest.raises(ValueError, match="不处于人工确认状态"):
        agent.apply_manual_prices(task_id, {"line-1": Decimal("11")})


def test_stale_task_version_cannot_overwrite_newer_state(monkeypatch):
    monkeypatch.setattr(agent, "search_local_price_tool", lambda product: None)
    state = agent.run_quotation_agent([_product()])
    stale = agent.get_task_state(state["task_id"])
    fresh = agent.get_task_state(state["task_id"])
    fresh["messages"].append("首次修改")
    agent.save_task_state(fresh, "首次修改")
    stale["messages"].append("旧状态修改")
    with pytest.raises(db.TaskConflictError):
        agent.save_task_state(stale, "旧状态修改")
    loaded = agent.get_task_state(state["task_id"])
    assert "首次修改" in loaded["messages"]
    assert "旧状态修改" not in loaded["messages"]
    assert db.list_task_events(state["task_id"])[-1]["event_type"] == "首次修改"


def test_old_task_table_is_migrated_without_losing_rows(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    with sqlite3.connect(tmp_path / "quotation.db") as conn:
        conn.execute(
            "CREATE TABLE quotation_tasks (task_id TEXT PRIMARY KEY, status TEXT NOT NULL, "
            "input_file TEXT, result TEXT, created_at TEXT, updated_at TEXT)"
        )
        conn.execute(
            "INSERT INTO quotation_tasks(task_id, status) VALUES ('old', '完成')"
        )
    db.init_db()
    with db.get_conn() as conn:
        row = conn.execute("SELECT status, version FROM quotation_tasks WHERE task_id='old'").fetchone()
        assert (row["status"], row["version"]) == ("完成", 0)
