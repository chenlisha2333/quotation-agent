"""上传边界：无效上传和数量不创建任务，也不保留临时文件。"""

from io import BytesIO

import pandas as pd
from fastapi.testclient import TestClient

from app import main


def test_empty_and_oversized_uploads_are_rejected_and_removed(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(main, "MAX_UPLOAD_BYTES", 4)
    client = TestClient(main.app)
    empty = client.post("/upload", files={"file": ("list.xlsx", b"", "application/octet-stream")})
    assert empty.status_code == 400
    large = client.post("/upload", files={"file": ("list.xlsx", b"12345", "application/octet-stream")})
    assert large.status_code == 413
    assert list(tmp_path.iterdir()) == []


def test_invalid_quantity_upload_does_not_create_task_or_keep_file(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "UPLOAD_DIR", tmp_path)
    calls = []
    monkeypatch.setattr(main, "run_quotation_agent", lambda *args, **kwargs: calls.append(args))

    excel = BytesIO()
    pd.DataFrame({"产品名称": ["断路器"], "数量": [2.5]}).to_excel(excel, index=False)
    client = TestClient(main.app)
    response = client.post(
        "/upload",
        files={"file": ("list.xlsx", excel.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )

    assert response.status_code == 400
    assert "第 2 行" in response.json()["detail"]
    assert calls == []
    assert list(tmp_path.iterdir()) == []
