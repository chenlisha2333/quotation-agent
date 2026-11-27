import json
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.cad2d import (
    CadArtifactManifest,
    LayoutError,
    LayoutProposal,
    PanelDesignSpec,
    dxf_entity_types,
    build_artifact_manifest,
    generate_layout,
    load_spec,
    render_dxf,
    resolve_manifest_path,
    verify_artifact_manifest,
    validate_layout,
    validate_dxf_matches_layout,
    write_artifact_manifest,
)


def test_synthetic_layout_is_deterministic_and_matches_bom():
    spec = load_spec(Path("tests/fixtures/synthetic_panel_spec.json"))

    first = generate_layout(spec)
    second = generate_layout(spec)

    assert first.model_dump() == second.model_dump()
    assert len(first.placements) == 5
    assert validate_layout(spec, first) == []
    assert first.status == "draft_for_review"


def test_dxf_contains_board_keepout_components_and_labels():
    spec = load_spec(Path("tests/fixtures/synthetic_panel_spec.json"))
    document = render_dxf(spec, generate_layout(spec))

    types = dxf_entity_types(document)
    assert types.count("LINE") == 4 + 4 + 5 * 4
    assert types.count("TEXT") == 5
    assert document.endswith("0\nEOF\n")
    assert "$INSUNITS\n70\n4" in document
    assert validate_dxf_matches_layout(spec, generate_layout(spec), document) == []


def test_dxf_geometry_validation_detects_coordinate_tampering():
    spec = load_spec(Path("tests/fixtures/synthetic_panel_spec.json"))
    proposal = generate_layout(spec)
    document = render_dxf(spec, proposal)
    tampered = document.replace("11\n600\n21\n0", "11\n599\n21\n0", 1)

    assert validate_dxf_matches_layout(spec, proposal, tampered) == [
        "DXF 线段坐标/图层与已校验布局不一致",
    ]


def test_artifact_manifest_tracks_hashes_and_detects_changed_files(tmp_path):
    spec_path = Path("tests/fixtures/synthetic_panel_spec.json")
    spec = load_spec(spec_path)
    proposal = generate_layout(spec)
    document = render_dxf(spec, proposal)
    dxf_path = tmp_path / "panel.dxf"
    manifest_path = tmp_path / "panel.manifest.json"
    dxf_path.write_text(document, encoding="ascii")

    manifest = build_artifact_manifest(spec_path, dxf_path, spec, proposal, document)
    write_artifact_manifest(manifest, manifest_path)

    loaded = CadArtifactManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    assert loaded.artifact_id == manifest.artifact_id
    assert loaded.engineering_use_approved is False
    assert loaded.review_status == "not_reviewed"
    assert verify_artifact_manifest(manifest_path, spec_path, dxf_path) == []

    dxf_path.write_text(document + "\n", encoding="ascii")
    assert verify_artifact_manifest(manifest_path, spec_path, dxf_path) == [
        "DXF 文件名或 SHA-256 与产物清单不一致",
    ]


def test_manifest_cannot_mark_unreviewed_artifact_engineering_approved():
    with pytest.raises(ValidationError):
        CadArtifactManifest.model_validate({
            "artifact_id": "a" * 24,
            "data_class": "synthetic",
            "design_spec_id": "SYNTH-PANEL-001",
            "design_spec_filename": "spec.json",
            "design_spec_sha256": "b" * 64,
            "dxf_filename": "panel.dxf",
            "dxf_sha256": "c" * 64,
            "generated_at": "2026-11-22T00:00:00Z",
            "generator_id": "deterministic-shelf-v1",
            "layout_validation": "passed",
            "dxf_format_validation": "passed",
            "dxf_geometry_validation": "passed",
            "engineering_use_approved": True,
        })


def test_artifact_paths_cannot_overwrite_input_or_each_other(tmp_path):
    spec_path = tmp_path / "spec.json"
    dxf_path = tmp_path / "panel.dxf"
    spec_path.write_text("{}", encoding="utf-8")

    with pytest.raises(ValueError, match="必须使用不同"):
        resolve_manifest_path(spec_path, dxf_path, spec_path)
    with pytest.raises(ValueError, match="必须使用不同"):
        resolve_manifest_path(spec_path, dxf_path, dxf_path)


def test_validator_rejects_missing_bom_instance():
    spec = load_spec(Path("tests/fixtures/synthetic_panel_spec.json"))
    proposal = generate_layout(spec)
    incomplete = LayoutProposal(
        design_spec_id=proposal.design_spec_id,
        placements=proposal.placements[:-1],
    )

    assert any("数量不一致" in issue for issue in validate_layout(spec, incomplete))
    with pytest.raises(LayoutError, match="拒绝导出"):
        render_dxf(spec, incomplete)


def test_validator_rejects_out_of_bounds_overlap_and_keepout_conflict():
    spec = load_spec(Path("tests/fixtures/synthetic_panel_spec.json"))
    proposal = generate_layout(spec)
    first = proposal.placements[0].model_copy(update={"x": Decimal("5"), "y": Decimal("5")})
    second = proposal.placements[1].model_copy(update={"x": first.x, "y": first.y})
    proposal.placements[0] = first
    proposal.placements[1] = second

    issues = validate_layout(spec, proposal)

    assert any("超出安装板有效边界" in issue for issue in issues)
    assert any("禁布区" in issue for issue in issues)
    assert any("碰撞或间距不足" in issue for issue in issues)


def test_spec_rejects_non_synthetic_data_and_overlarge_instance_count():
    path = Path("tests/fixtures/synthetic_panel_spec.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["data_class"] = "business"
    with pytest.raises(ValidationError):
        PanelDesignSpec.model_validate(raw)

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["components"][0]["quantity"] = 51
    with pytest.raises(ValidationError):
        PanelDesignSpec.model_validate(raw)


def test_generator_fails_when_board_cannot_fit_bom():
    spec = load_spec(Path("tests/fixtures/synthetic_panel_spec.json"))
    crowded = spec.model_copy(update={"board_width": Decimal("120"), "board_height": Decimal("120")})

    with pytest.raises(LayoutError, match="无法放入"):
        generate_layout(crowded)


def test_dxf_parser_rejects_unpaired_or_unsupported_entities():
    with pytest.raises(ValueError, match="未成对"):
        dxf_entity_types("0\nEOF\nextra")
    spec = load_spec(Path("tests/fixtures/synthetic_panel_spec.json"))
    document = render_dxf(spec, generate_layout(spec))
    malformed = document.replace("0\nLINE\n", "0\nCIRCLE\n", 1)
    with pytest.raises(ValueError, match="不支持"):
        dxf_entity_types(malformed)
