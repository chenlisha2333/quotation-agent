"""上传边界：过大和空文件不保留在本地。"""

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
