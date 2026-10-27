"""示例询价表必须与项目上传解析器兼容，且不伪造采购价。"""

from pathlib import Path

import openpyxl
from fastapi.testclient import TestClient

from app import main
from app.parser import parse_excel


TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "施耐德询价表示例.xlsx"


def test_template_contains_three_identified_schneider_products():
    products = parse_excel(TEMPLATE)
    assert [p.model for p in products] == ["A9F77216", "LC1D09M7", "XB4BA31"]
    assert [p.quantity for p in products] == [2, 1, 2]
    assert {p.brand for p in products} == {"施耐德"}


def test_supplier_prices_are_blank_and_template_is_downloadable():
    workbook = openpyxl.load_workbook(TEMPLATE, data_only=True, read_only=True)
    assert workbook.sheetnames == ["询价清单", "供应商报价回填", "尺寸与资料"]
    assert all(workbook["供应商报价回填"][f"E{row}"].value is None for row in range(2, 5))
    assert "rsonline.cn" in workbook["供应商报价回填"]["C11"].value
    response = TestClient(main.app).get("/templates/inquiry")
    assert response.status_code == 200
    assert response.content[:2] == b"PK"


def test_uploading_template_requires_real_cost_quotes(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path / "uploads")
    response = TestClient(main.app).post(
        "/upload", data={"customer_name": "演示客户"},
        files={"file": (TEMPLATE.name, TEMPLATE.read_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "人工确认"
    assert body["pending_count"] == 3
    assert all(item["cost_price"] is None for item in body["items"])
    assert {item["pricing_rule_id"] for item in body["items"]} == {"draft-cost-times-1.2-v1"}
