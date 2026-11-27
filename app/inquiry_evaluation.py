"""Offline, row-level evaluation for frozen inquiry spreadsheets.

Evaluation inputs and detailed outputs are restricted to the local
``data/evaluation`` directory. Only aggregate counts should be copied into
tracked documentation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .models import Product
from .parser import InquiryParseError, parse_excel_with_rows


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data" / "evaluation"
SUPPORTED_ERROR_CATEGORIES = {"missing_required_columns", "invalid_quantity"}
PRODUCT_FIELDS = ("name", "model", "spec", "brand", "quantity", "unit", "category")
OPAQUE_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
SHA256 = re.compile(r"^[a-f0-9]{64}$")


class EvaluationManifestError(ValueError):
    """The local evaluation manifest is malformed or points outside its data root."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvaluationManifestError(message)


def _load_manifest(manifest_path: Path, allowed_root: Path) -> dict[str, Any]:
    manifest_path = manifest_path.resolve()
    allowed_root = allowed_root.resolve()
    _require(manifest_path.is_relative_to(allowed_root), "manifest must be stored under the permitted evaluation data root")
    _require(manifest_path.is_file(), "manifest file does not exist")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EvaluationManifestError("manifest cannot be read as UTF-8 JSON") from exc

    _require(isinstance(manifest, dict), "manifest root must be a JSON object")
    _require(manifest.get("schema_version") == 1, "unsupported manifest schema_version")
    _require(bool(OPAQUE_ID.fullmatch(str(manifest.get("dataset_id", "")))), "dataset_id must be an opaque identifier")
    _require(bool(OPAQUE_ID.fullmatch(str(manifest.get("dataset_version", "")))), "dataset_version must be an opaque identifier")

    classification = manifest.get("data_classification")
    _require(classification in {"synthetic", "authorized_anonymized"}, "data_classification must be synthetic or authorized_anonymized")
    if classification == "authorized_anonymized":
        _require(bool(str(manifest.get("authorization_ref", "")).strip()), "authorized_anonymized datasets require an authorization_ref")

    samples = manifest.get("samples")
    _require(isinstance(samples, list) and samples, "manifest must contain at least one sample")
    seen_ids: set[str] = set()
    seen_files: set[Path] = set()
    manifest_dir = manifest_path.parent
    for sample in samples:
        _require(isinstance(sample, dict), "each sample must be an object")
        sample_id = str(sample.get("sample_id", ""))
        _require(bool(OPAQUE_ID.fullmatch(sample_id)), "sample_id must be an opaque identifier")
        _require(sample_id not in seen_ids, "sample_id values must be unique")
        seen_ids.add(sample_id)

        format_class = sample.get("format_class")
        _require(isinstance(format_class, str) and bool(OPAQUE_ID.fullmatch(format_class)), "format_class must be a short non-sensitive label")
        _require(sample.get("split") in {"development", "acceptance"}, "sample split must be development or acceptance")
        relative_file = sample.get("file")
        _require(isinstance(relative_file, str) and relative_file, "sample file must be a relative path")
        candidate = (manifest_dir / relative_file).resolve()
        _require(candidate.is_relative_to(manifest_dir), "sample file must remain inside the manifest directory")
        _require(candidate.is_file(), "a declared sample file does not exist")
        _require(candidate not in seen_files, "each sample must reference a distinct file")
        seen_files.add(candidate)

        expected_hash = str(sample.get("sha256", ""))
        _require(bool(SHA256.fullmatch(expected_hash)), "each sample requires a lowercase SHA-256")
        _validate_expected(sample.get("expected"))

    return manifest


def _validate_expected(expected: Any) -> None:
    _require(isinstance(expected, dict), "sample expected value must be an object")
    outcome = expected.get("status")
    _require(outcome in {"parsed", "rejected"}, "expected.status must be parsed or rejected")
    if outcome == "parsed":
        rows = expected.get("rows")
        _require(isinstance(rows, list) and rows, "parsed samples require expected rows")
        seen_rows: set[int] = set()
        for row in rows:
            _require(isinstance(row, dict), "each expected row must be an object")
            row_number = row.get("excel_row")
            _require(isinstance(row_number, int) and not isinstance(row_number, bool) and row_number >= 2, "excel_row must be a physical worksheet row number")
            _require(row_number not in seen_rows, "expected excel_row values must be unique")
            seen_rows.add(row_number)
            product = row.get("product")
            _require(isinstance(product, dict), "each expected row requires a product object")
            _require(set(product) == set(PRODUCT_FIELDS), f"expected products must define exactly: {', '.join(PRODUCT_FIELDS)}")
            _require(isinstance(product["name"], str) and bool(product["name"].strip()), "expected product name must be non-empty")
            _require(isinstance(product["quantity"], int) and not isinstance(product["quantity"], bool) and product["quantity"] > 0, "expected quantity must be a positive integer")
            _require(product["category"] in {"元器件", "柜体", "辅材", "未知"}, "expected category is not supported")
            for field in ("model", "spec", "brand", "unit"):
                _require(product[field] is None or isinstance(product[field], str), f"expected {field} must be a string or null")
    else:
        _require(expected.get("error_category") in SUPPORTED_ERROR_CATEGORIES, "rejected samples require a supported error_category")
        row_number = expected.get("error_row")
        _require(row_number is None or (isinstance(row_number, int) and not isinstance(row_number, bool) and row_number >= 2), "error_row must be a worksheet row number or null")


def _product_values(product: Product) -> dict[str, Any]:
    values = product.model_dump()
    values["category"] = product.category.value
    return {field: values.get(field) for field in PRODUCT_FIELDS}


def _sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with file_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _evaluate_sample(manifest_dir: Path, sample: dict[str, Any]) -> tuple[dict[str, Any], int, int, int, int]:
    sample_id = sample["sample_id"]
    expected = sample["expected"]
    file_path = (manifest_dir / sample["file"]).resolve()
    actual_hash = _sha256_file(file_path)
    if actual_hash != sample["sha256"]:
        return ({
            "sample_id": sample_id,
            "format_class": sample["format_class"],
            "split": sample["split"],
            "passed": False,
            "reason": "input_hash_mismatch",
            "expected_sha256": sample["sha256"],
            "actual_sha256": actual_hash,
        }, 0, 0, 0, 0)

    try:
        parsed_rows = parse_excel_with_rows(file_path)
    except InquiryParseError as exc:
        actual = {"status": "rejected", "error_category": exc.category, "error_row": exc.row_number}
    except Exception as exc:  # Keep exception content out of reports; it may include local paths or cell data.
        actual = {"status": "rejected", "error_category": "parser_exception", "exception_type": type(exc).__name__}
    else:
        actual = {
            "status": "parsed",
            "rows": [{"excel_row": row_number, "product": _product_values(product)} for row_number, product in parsed_rows],
        }

    row_count = field_count = matched_rows = matched_fields = 0
    if expected["status"] == "rejected":
        passed = actual["status"] == "rejected" and actual.get("error_category") == expected["error_category"]
        if "error_row" in expected:
            passed = passed and actual.get("error_row") == expected["error_row"]
        result = {
            "sample_id": sample_id,
            "format_class": sample["format_class"],
            "split": sample["split"],
            "passed": bool(passed),
            "expected": expected,
            "actual": actual,
        }
        return result, row_count, matched_rows, field_count, matched_fields

    expected_rows = {row["excel_row"]: row["product"] for row in expected["rows"]}
    actual_rows = {row["excel_row"]: row["product"] for row in actual.get("rows", [])}
    row_details = []
    for row_number in sorted(set(expected_rows) | set(actual_rows)):
        expected_product = expected_rows.get(row_number)
        actual_product = actual_rows.get(row_number)
        differences = {}
        for field in PRODUCT_FIELDS:
            field_count += int(expected_product is not None or actual_product is not None)
            if expected_product is not None and actual_product is not None:
                expected_value = expected_product[field]
                actual_value = actual_product.get(field)
                if expected_value == actual_value:
                    matched_fields += 1
                else:
                    differences[field] = {"expected": expected_value, "actual": actual_value}
        row_match = expected_product is not None and actual_product is not None and not differences
        row_count += int(expected_product is not None or actual_product is not None)
        matched_rows += int(row_match)
        row_details.append({
            "excel_row": row_number,
            "matched": bool(row_match),
            "differences": differences,
            "missing_from_output": expected_product is not None and actual_product is None,
            "unexpected_in_output": expected_product is None and actual_product is not None,
        })
    passed = actual["status"] == "parsed" and len(expected_rows) == len(actual_rows) and matched_rows == len(expected_rows)
    return ({
        "sample_id": sample_id,
        "format_class": sample["format_class"],
        "split": sample["split"],
        "passed": bool(passed),
        "expected_status": "parsed",
        "actual_status": actual["status"],
        "rows": row_details,
    }, row_count, matched_rows, field_count, matched_fields)


def evaluate_manifest(
    manifest_path: Path,
    *,
    output_path: Path | None = None,
    allowed_root: Path | None = None,
) -> dict[str, Any]:
    """Run a frozen manifest and write private row-level results beside it."""
    manifest_path = Path(manifest_path).resolve()
    permitted_root = Path(allowed_root).resolve() if allowed_root else DEFAULT_DATA_ROOT.resolve()
    manifest = _load_manifest(manifest_path, permitted_root)
    manifest_dir = manifest_path.parent
    output_path = Path(output_path).resolve() if output_path else manifest_dir / "evaluation-results.json"
    _require(output_path.is_relative_to(manifest_dir), "evaluation output must stay beside the manifest")
    _require(output_path != manifest_path, "evaluation output cannot overwrite the manifest")

    details = []
    total_rows = matched_rows = total_fields = matched_fields = 0
    for sample in manifest["samples"]:
        detail, rows, matched, fields, field_matches = _evaluate_sample(manifest_dir, sample)
        details.append(detail)
        total_rows += rows
        matched_rows += matched
        total_fields += fields
        matched_fields += field_matches

    format_classes = sorted({sample["format_class"] for sample in manifest["samples"]})
    acceptance_samples = [sample for sample in manifest["samples"] if sample["split"] == "acceptance"]
    acceptance_details = [detail for detail in details if detail["split"] == "acceptance"]
    acceptance_format_classes = sorted({sample["format_class"] for sample in acceptance_samples})
    split_summary = {}
    for split in ("development", "acceptance"):
        split_details = [detail for detail in details if detail["split"] == split]
        if split_details:
            split_summary[split] = {
                "sample_count": len(split_details),
                "sample_passed": sum(detail["passed"] for detail in split_details),
                "sample_failed": sum(not detail["passed"] for detail in split_details),
                "format_class_count": len({detail["format_class"] for detail in split_details}),
            }
    summary = {
        "dataset_id": manifest["dataset_id"],
        "dataset_version": manifest["dataset_version"],
        "data_classification": manifest["data_classification"],
        "sample_count": len(details),
        "sample_passed": sum(detail["passed"] for detail in details),
        "sample_failed": sum(not detail["passed"] for detail in details),
        "format_class_count": len(format_classes),
        "format_classes": format_classes,
        "meets_five_format_class_minimum": len(format_classes) >= 5,
        "split_summary": split_summary,
        "acceptance_format_class_count": len(acceptance_format_classes),
        "meets_five_acceptance_format_class_minimum": len(acceptance_format_classes) >= 5,
        "compared_row_count": total_rows,
        "exact_row_match_count": matched_rows,
        "row_exact_match_rate": matched_rows / total_rows if total_rows else None,
        "compared_field_count": total_fields,
        "field_match_count": matched_fields,
        "field_accuracy": matched_fields / total_fields if total_fields else None,
        "business_acceptance_ready": manifest["data_classification"] == "authorized_anonymized" and len(acceptance_format_classes) >= 5 and bool(acceptance_details) and all(detail["passed"] for detail in details),
    }
    report = {"summary": summary, "samples": details}
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate a frozen, local inquiry spreadsheet set.")
    parser.add_argument("manifest", type=Path, help="manifest path under data/evaluation/")
    parser.add_argument("--output", type=Path, help="private JSON report path (defaults beside the manifest)")
    args = parser.parse_args()
    try:
        report = evaluate_manifest(args.manifest, output_path=args.output)
    except EvaluationManifestError as exc:
        parser.error(str(exc))
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 1 if report["summary"]["sample_failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
