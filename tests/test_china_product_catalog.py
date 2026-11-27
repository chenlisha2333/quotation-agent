import json

import pytest

from app import db
from app.product_master import DEFAULT_CHINA_CATALOG, ProductCatalogError, load_china_product_catalog


def test_china_catalog_contains_official_china_sources_and_unapproved_records():
    products = load_china_product_catalog()
    by_model = {product.model: product for product in products}
    assert {"A9F18216", "A9F18232", "LC1D09M7", "XB4BA31", "LC1D099M7"} <= set(by_model)
    assert all(product.approval_status == "draft" for product in products)
    assert all(
        "schneider-electric.cn" in str(source.url)
        for product in products
        for source in product.evidence
    )
    assert by_model["A9F18216"].attributes["额定电流"] == "16 A"
    assert by_model["LC1D099M7"].lifecycle_status == "discontinued"
    assert by_model["LC1D099M7"].replacement_product_id == by_model["LC1D09M7"].product_id


def test_china_draft_import_is_atomic_idempotent_and_not_eligible_for_approved_search(tmp_path, monkeypatch):
    products = load_china_product_catalog()
    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "catalog")
    assert db.import_product_master_versions(products) == (5, 0)
    assert db.import_product_master_versions(products) == (0, 5)
    assert db.search_approved_product_candidates("施耐德", "A9F18216") == []

    monkeypatch.setattr(db, "DATA_DIR", tmp_path / "atomic")
    invalid_version = products[0].model_copy(update={
        "product_id": "SE-CN-INVALID",
        "version": 2,
    })
    with pytest.raises(db.ProductMasterConflictError, match="下一版本应为 1"):
        db.import_product_master_versions([products[0], invalid_version])
    with db.get_conn() as connection:
        count = connection.execute("SELECT COUNT(*) FROM product_master_versions").fetchone()[0]
    assert count == 0


@pytest.mark.parametrize("change", ["approved", "external_source"])
def test_china_catalog_rejects_approved_rows_and_non_china_sources(tmp_path, change):
    payload = json.loads(DEFAULT_CHINA_CATALOG.read_text("utf-8"))
    if change == "approved":
        payload["products"][0]["approval_status"] = "approved"
        payload["products"][0]["approved_by"] = "fake"
        payload["products"][0]["approved_at"] = "2026-11-01T00:00:00Z"
        payload["products"][0]["permission_reference"] = "fake"
    else:
        payload["products"][0]["evidence"][0]["url"] = "https://example.com/fake"
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ProductCatalogError):
        load_china_product_catalog(path)
