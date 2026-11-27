from datetime import datetime, timezone

import pytest

from app import db
from app.models import ProductEvidence, ProductMasterVersion


def record(*, version=1, product_id="schneider-breaker-a1", status="approved", **changes):
    values = {
        "product_id": product_id,
        "version": version,
        "brand": "施耐德",
        "model": "A1-32",
        "name": "小型断路器",
        "aliases": ["A1_32"],
        "unit": "个",
        "attributes": {"额定电流": "32 A", "极数": "2P"},
        "lifecycle_status": "active",
        "evidence": [ProductEvidence(
            title="合成测试来源",
            url="https://example.test/products/a1-32",
            checked_on="2026-11-01",
        )],
        "permission_reference": "合成测试授权记录",
        "approval_status": status,
        "approved_by": "测试技术审核人" if status == "approved" else None,
        "approved_at": datetime(2026, 11, 1, tzinfo=timezone.utc) if status == "approved" else None,
    }
    values.update(changes)
    return ProductMasterVersion(**values)


def test_product_master_versions_are_append_only_and_search_exact_approved_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    draft = record(status="draft")
    db.save_product_master_version(draft)
    assert db.search_approved_product_candidates("施耐德", "A1-32") == []

    approved = record(version=2)
    db.save_product_master_version(approved)
    matches = db.search_approved_product_candidates("施耐德", "A1_32")
    assert len(matches) == 1
    assert matches[0].product_id == approved.product_id
    assert db.search_approved_product_candidates("施耐德", "A1-3") == []
    assert db.search_approved_product_candidates("正泰", "A1-32") == []

    db.save_product_master_version(record(version=3, status="draft", name="待审核新名称"))
    still_approved = db.search_approved_product_candidates("施耐德", "A1-32")
    assert len(still_approved) == 1
    assert still_approved[0].version == 2

    superseding = record(version=5, name="更新后的产品名称")
    with pytest.raises(db.ProductMasterConflictError, match="下一版本应为 4"):
        db.save_product_master_version(superseding)
    new_version = record(version=4, name="更新后的产品名称", attributes={"额定电流": "32 A"})
    db.save_product_master_version(new_version)
    current = db.search_approved_product_candidates("施耐德", "A1-32")
    assert len(current) == 1
    assert current[0].version == 4
    assert current[0].name == "更新后的产品名称"


def test_product_master_approval_requires_technical_and_permission_evidence():
    with pytest.raises(ValueError, match="技术审核人和时间"):
        record(approved_by=None, approved_at=None)
    with pytest.raises(ValueError, match="数据使用依据"):
        record(permission_reference=None)
    with pytest.raises(ValueError, match="HTTPS"):
        ProductEvidence(title="错误来源", url="http://example.test/product", checked_on="2026-11-01")
    with pytest.raises(ValueError, match="重复登记别名"):
        record(aliases=["A1-32", "a1-32"])
    with pytest.raises(ValueError, match="包含时区"):
        record(approved_at=datetime(2026, 11, 1))


def test_ambiguous_exact_alias_returns_all_candidates_for_human_review(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", tmp_path)
    db.save_product_master_version(record())
    db.save_product_master_version(record(
        product_id="schneider-breaker-a2",
        model="A2-32",
        aliases=["SHARED-ALIAS"],
    ))
    db.save_product_master_version(record(
        product_id="schneider-breaker-a3",
        model="A3-32",
        aliases=["SHARED-ALIAS"],
    ))

    matches = db.search_approved_product_candidates("施耐德", "SHARED-ALIAS")
    assert [candidate.product_id for candidate in matches] == [
        "schneider-breaker-a2", "schneider-breaker-a3",
    ]
