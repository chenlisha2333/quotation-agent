from pathlib import Path

import pytest

from app.cad2d import LayoutError, generate_layout, load_spec
from app.cad_agent_adapter import drawing_spec_to_layout


SYNTHETIC_SPEC = Path("tests/fixtures/synthetic_panel_spec.json")


def cad_agent_payload(spec, proposal):
    return {
        "units": "mm",
        "entities": [
            {
                "kind": "rectangle",
                "id": f"placement:{placement.bom_item_id}:{placement.instance_index}",
                "corner": [float(placement.x), float(placement.y)],
                "width": float(placement.width),
                "height": float(placement.height),
            }
            for placement in proposal.placements
        ],
    }


def test_candidate_drawing_spec_rectangles_convert_and_pass_independent_validator():
    spec = load_spec(SYNTHETIC_SPEC)
    expected = generate_layout(spec)
    payload = cad_agent_payload(spec, expected)

    actual = drawing_spec_to_layout(spec, payload)

    assert actual.model_dump() == expected.model_dump()


def test_candidate_adapter_rejects_non_mm_and_missing_bom_instance():
    spec = load_spec(SYNTHETIC_SPEC)
    payload = cad_agent_payload(spec, generate_layout(spec))

    with pytest.raises(LayoutError, match="单位"):
        drawing_spec_to_layout(spec, {**payload, "units": "in"})
    with pytest.raises(LayoutError, match="数量不一致"):
        drawing_spec_to_layout(spec, {**payload, "entities": payload["entities"][:-1]})


def test_candidate_adapter_rejects_unknown_or_duplicate_component_ids():
    spec = load_spec(SYNTHETIC_SPEC)
    payload = cad_agent_payload(spec, generate_layout(spec))
    first = payload["entities"][0]

    with pytest.raises(LayoutError, match="未登记"):
        drawing_spec_to_layout(spec, {
            **payload,
            "entities": [{**first, "id": "placement:UNKNOWN:1"}, *payload["entities"][1:]],
        })
    with pytest.raises(LayoutError, match="重复"):
        drawing_spec_to_layout(spec, {**payload, "entities": [first, *payload["entities"]]})


def test_candidate_adapter_rejects_modified_footprint_and_keepout_violation():
    spec = load_spec(SYNTHETIC_SPEC)
    payload = cad_agent_payload(spec, generate_layout(spec))
    entities = [dict(entity) for entity in payload["entities"]]

    entities[0]["width"] = entities[0]["width"] + 1
    with pytest.raises(LayoutError, match="型号或外形"):
        drawing_spec_to_layout(spec, {**payload, "entities": entities})

    entities = [dict(entity) for entity in payload["entities"]]
    entities[0]["corner"] = [20, 20]
    with pytest.raises(LayoutError, match="冲突或间距不足"):
        drawing_spec_to_layout(spec, {**payload, "entities": entities})


def test_candidate_adapter_rejects_invalid_or_excessive_geometry():
    spec = load_spec(SYNTHETIC_SPEC)
    payload = cad_agent_payload(spec, generate_layout(spec))
    entities = [dict(entity) for entity in payload["entities"]]
    entities[0]["corner"] = ["NaN", 0]

    with pytest.raises(LayoutError, match="几何无效"):
        drawing_spec_to_layout(spec, {**payload, "entities": entities})
    with pytest.raises(LayoutError, match="上限"):
        drawing_spec_to_layout(spec, {**payload, "entities": [{}] * 501})
