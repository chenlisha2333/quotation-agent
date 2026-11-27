import hashlib
import json

import pandas as pd
import pytest

from app.inquiry_evaluation import EvaluationManifestError, evaluate_manifest


def _write_manifest(tmp_path, frame, expected, *, sample_id="sample-001", format_class="xlsx_basic", split="acceptance", classification="synthetic", authorization_ref=None):
    sample_path = tmp_path / f"{sample_id}.xlsx"
    frame.to_excel(sample_path, index=False)
    manifest = {
        "schema_version": 1,
        "dataset_id": "T05-synthetic",
        "dataset_version": "v1",
        "data_classification": classification,
        "samples": [{
            "sample_id": sample_id,
            "format_class": format_class,
            "split": split,
            "file": sample_path.name,
            "sha256": hashlib.sha256(sample_path.read_bytes()).hexdigest(),
            "expected": expected,
        }],
    }
    if authorization_ref is not None:
        manifest["authorization_ref"] = authorization_ref
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def _expected_product(*, quantity=2, brand=None):
    return {
        "name": "断路器",
        "model": None,
        "spec": None,
        "brand": brand,
        "quantity": quantity,
        "unit": "个",
        "category": "元器件",
    }


def test_evaluate_manifest_compares_rows_and_writes_detailed_local_report(tmp_path):
    path = _write_manifest(
        tmp_path,
        pd.DataFrame({"产品名称": [None, "断路器"], "数量": [None, 2]}),
        {"status": "parsed", "rows": [{"excel_row": 3, "product": _expected_product()}]},
    )

    report = evaluate_manifest(path, allowed_root=tmp_path)

    assert report["summary"]["sample_passed"] == 1
    assert report["summary"]["exact_row_match_count"] == 1
    assert report["summary"]["field_accuracy"] == 1.0
    assert report["summary"]["meets_five_format_class_minimum"] is False
    assert report["summary"]["meets_five_acceptance_format_class_minimum"] is False
    assert report["summary"]["business_acceptance_ready"] is False
    assert report["samples"][0]["rows"] == [{
        "excel_row": 3,
        "matched": True,
        "differences": {},
        "missing_from_output": False,
        "unexpected_in_output": False,
    }]
    assert (tmp_path / "evaluation-results.json").is_file()


def test_evaluate_manifest_reports_field_mismatch_without_failing_to_align_rows(tmp_path):
    path = _write_manifest(
        tmp_path,
        pd.DataFrame({"产品名称": ["断路器"], "数量": [2], "品牌": ["施耐德"]}),
        {"status": "parsed", "rows": [{"excel_row": 2, "product": _expected_product(brand="正泰")} ]},
    )

    report = evaluate_manifest(path, allowed_root=tmp_path)

    assert report["summary"]["sample_failed"] == 1
    assert report["samples"][0]["rows"][0]["differences"]["brand"] == {
        "expected": "正泰",
        "actual": "施耐德",
    }


def test_evaluate_manifest_counts_unexpected_rows_as_mismatches(tmp_path):
    path = _write_manifest(
        tmp_path,
        pd.DataFrame({"产品名称": ["断路器", "接触器"], "数量": [2, 1]}),
        {"status": "parsed", "rows": [{"excel_row": 2, "product": _expected_product()}]},
    )

    report = evaluate_manifest(path, allowed_root=tmp_path)

    assert report["summary"]["sample_failed"] == 1
    assert report["summary"]["compared_row_count"] == 2
    assert report["summary"]["row_exact_match_rate"] == 0.5
    assert report["summary"]["field_accuracy"] == 0.5
    assert report["samples"][0]["rows"][1]["unexpected_in_output"] is True


def test_evaluate_manifest_matches_expected_rejection_and_physical_row(tmp_path):
    path = _write_manifest(
        tmp_path,
        pd.DataFrame({"产品名称": ["断路器"], "数量": [2.5]}),
        {"status": "rejected", "error_category": "invalid_quantity", "error_row": 2},
    )

    report = evaluate_manifest(path, allowed_root=tmp_path)

    assert report["summary"]["sample_passed"] == 1
    assert report["samples"][0]["actual"] == {
        "status": "rejected",
        "error_category": "invalid_quantity",
        "error_row": 2,
    }


def test_evaluate_manifest_stops_before_parsing_when_file_hash_changes(tmp_path):
    path = _write_manifest(
        tmp_path,
        pd.DataFrame({"产品名称": ["断路器"], "数量": [2]}),
        {"status": "parsed", "rows": [{"excel_row": 2, "product": _expected_product()}]},
    )
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["samples"][0]["sha256"] = "0" * 64
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    report = evaluate_manifest(path, allowed_root=tmp_path)

    assert report["summary"]["sample_failed"] == 1
    assert report["samples"][0]["reason"] == "input_hash_mismatch"
    assert "expected" not in report["samples"][0]


def test_evaluate_manifest_rejects_files_outside_manifest_directory(tmp_path):
    outside = tmp_path / "outside.xlsx"
    pd.DataFrame({"产品名称": ["断路器"], "数量": [2]}).to_excel(outside, index=False)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    manifest = {
        "schema_version": 1,
        "dataset_id": "T05-synthetic",
        "dataset_version": "v1",
        "data_classification": "synthetic",
        "samples": [{"sample_id": "s1", "format_class": "xlsx", "split": "acceptance", "file": "../outside.xlsx", "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(), "expected": {"status": "rejected", "error_category": "invalid_quantity"}}],
    }
    manifest_path = data_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(EvaluationManifestError, match="inside the manifest directory"):
        evaluate_manifest(manifest_path, allowed_root=tmp_path)


def test_authorized_dataset_requires_a_recorded_permission_reference(tmp_path):
    path = _write_manifest(
        tmp_path,
        pd.DataFrame({"产品名称": ["断路器"], "数量": [2]}),
        {"status": "parsed", "rows": [{"excel_row": 2, "product": _expected_product()}]},
        classification="authorized_anonymized",
    )

    with pytest.raises(EvaluationManifestError, match="authorization_ref"):
        evaluate_manifest(path, allowed_root=tmp_path)


def test_evaluator_reports_development_and_acceptance_splits(tmp_path):
    path = _write_manifest(
        tmp_path,
        pd.DataFrame({"产品名称": ["断路器"], "数量": [2]}),
        {"status": "parsed", "rows": [{"excel_row": 2, "product": _expected_product()}]},
        split="development",
    )

    report = evaluate_manifest(path, allowed_root=tmp_path)

    assert report["samples"][0]["split"] == "development"
    assert report["summary"]["split_summary"]["development"]["sample_passed"] == 1
    assert "acceptance" not in report["summary"]["split_summary"]
