"""从真实 Excel 上传到客户版导出的最小完整接口旅程。"""

import json
from io import BytesIO

import openpyxl
from docx import Document
from fastapi.testclient import TestClient

from app import db, main


def test_excel_to_approved_customer_quote(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    db.init_db()
    credentials = [
        {"actor": "技术员", "role": "technical", "token": "technical-secret-123456"},
        {"actor": "采购员", "role": "procurement", "token": "procurement-secret-123456"},
        {"actor": "商务经理", "role": "commercial", "token": "commercial-secret-123456"},
    ]
    monkeypatch.setenv("QUOTE_APPROVERS_JSON", json.dumps(credentials))
    monkeypatch.setenv("QUOTE_APPROVED_RULE_ID", "draft-cost-times-1.2-v1")
    auth = {
        record["role"]: {"Authorization": f"Bearer {record['token']}"}
        for record in credentials
    }

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["产品名称", "型号", "品牌", "数量", "单位"])
    sheet.append(["断路器", "待核实型号", "施耐德", 2, "个"])
    file_bytes = BytesIO()
    workbook.save(file_bytes)

    client = TestClient(main.app)
    uploaded = client.post(
        "/upload", data={"customer_name": "测试客户", "profit_rate": "0.20"},
        files={"file": ("request.xlsx", file_bytes.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        headers=auth["technical"],
    )
    assert uploaded.status_code == 200, uploaded.text
    task_id = uploaded.json()["task_id"]
    assert uploaded.json()["status"] == "人工确认"
    assert uploaded.json()["pending_products"][0]["item_id"] == "line-1"
    assert client.post(
        f"/tasks/{task_id}/confirm", json={"line-1": "10.00"},
        headers=auth["technical"],
    ).status_code == 403

    confirmed = client.post(
        f"/tasks/{task_id}/confirm", json={"line-1": "10.00"},
        headers=auth["procurement"],
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "完成"
    assert confirmed.json()["items"][0]["cost_price"] == "10.00"
    internal_word = client.post(
        "/export", json={"task_id": task_id, "format": "internal", "file_type": "docx"},
        headers=auth["procurement"],
    )
    assert internal_word.status_code == 200
    internal_doc = Document(BytesIO(internal_word.content))
    assert "成本价" in internal_doc.tables[0].rows[0].cells[4].text
    assert "¥10.00" in internal_doc.tables[0].rows[1].cells[4].text
    assert client.post(
        "/export", json={"task_id": task_id, "format": "customer", "file_type": "docx"},
        headers=auth["commercial"],
    ).status_code == 409
    for stage in ("technical", "procurement", "commercial"):
        response = client.post(
            f"/tasks/{task_id}/approve/{stage}", json={"reason": "已核对"},
            headers=auth[stage],
        )
        assert response.status_code == 200, response.text
    customer = client.post(
        "/export", json={"task_id": task_id, "format": "customer"},
        headers=auth["commercial"],
    )
    assert customer.status_code == 200
    quote = openpyxl.load_workbook(BytesIO(customer.content), data_only=True)
    assert quote.active["I6"].value == 24
    word = client.post(
        "/export", json={"task_id": task_id, "format": "customer", "file_type": "docx"},
        headers=auth["commercial"],
    )
    assert word.status_code == 200
    customer_doc = Document(BytesIO(word.content))
    text = "\n".join([p.text for p in customer_doc.paragraphs] + [
        cell.text for table in customer_doc.tables for row in table.rows for cell in row.cells
    ])
    assert "¥24.00" in text
    assert "成本" not in text
    assert "1.2" not in text
    assert client.post(
        "/export", json={"task_id": task_id, "format": "customer", "file_type": "docx"},
        headers=auth["technical"],
    ).status_code == 403
    assert client.get(f"/tasks/{task_id}", headers=auth["technical"]).json()["items"][0]["cost_price"] is None


def test_manual_source_is_selected_and_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(main, "DATA_DIR", tmp_path)
    db.init_db()
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["产品名称", "型号", "品牌", "数量", "单位"])
    sheet.append(["断路器", "待核实型号", "施耐德", 1, "个"])
    file_bytes = BytesIO()
    workbook.save(file_bytes)

    response = TestClient(main.app).post(
        "/upload", data={"price_source": "manual"},
        files={"file": ("request.xlsx", file_bytes.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    assert response.json()["price_source_selection"] == "manual"
    assert "指定人工录价" in response.json()["pending_products"][0]["reason"]
    restored = TestClient(main.app).get(f"/tasks/{task_id}")
    assert restored.json()["price_source_selection"] == "manual"


def test_unconfigured_price_source_fails_before_storing_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    workbook = openpyxl.Workbook()
    file_bytes = BytesIO()
    workbook.save(file_bytes)
    response = TestClient(main.app).post(
        "/upload", data={"price_source": "web"},
        files={"file": ("request.xlsx", file_bytes.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 501
    assert "网页报价来源尚未配置适配器" in response.json()["detail"]
    assert not (tmp_path / "uploads").exists()
