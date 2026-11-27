import json

import pytest

from app.tool_call_evaluation import (
    ToolEvaluationDatasetError,
    evaluate_dataset,
    load_dataset,
)


def test_synthetic_tool_gate_evaluation_is_reproducible():
    result = evaluate_dataset(load_dataset())

    assert result["sample_count"] == 10
    assert result["correct_allow_count"] == result["expected_allow_count"] == 5
    assert result["correct_deny_count"] == result["expected_deny_count"] == 5
    assert result["exact_case_count"] == 10
    assert result["exact_case_rate"] == 1.0
    assert all(case["passed"] for case in result["cases"])


def test_tool_evaluator_rejects_dataset_without_synthetic_marker(tmp_path):
    path = tmp_path / "calls.json"
    path.write_text(json.dumps({"data_class": "business", "cases": []}), encoding="utf-8")

    with pytest.raises(ToolEvaluationDatasetError, match="synthetic"):
        load_dataset(path)


def test_tool_evaluator_requires_unique_case_ids(tmp_path):
    dataset = {"data_class": "synthetic", "cases": [
        {"id": "same", "expected_outcome": "deny", "call": {}},
        {"id": "same", "expected_outcome": "deny", "call": {}},
    ]}
    path = tmp_path / "calls.json"
    path.write_text(json.dumps(dataset), encoding="utf-8")

    with pytest.raises(ToolEvaluationDatasetError, match="唯一"):
        load_dataset(path)


def test_evaluator_carries_no_context_when_case_requests_it(monkeypatch):
    from app import tool_call_evaluation

    async def inspect_call(call, context):
        assert context is None
        return ({"output": "{\"error\":\"未关联任务\"}"}, [])

    monkeypatch.setattr(tool_call_evaluation, "_run_tool", inspect_call)
    dataset = {"data_class": "synthetic", "cases": [{
        "id": "no-context", "expected_outcome": "allow",
        "call": {"name": "get_current_task_summary", "call_id": "id", "arguments": "{}"},
        "context": None, "expected_output_contains": "未关联任务",
    }]}

    # This evaluator fixture path is tested separately from the standard dataset.
    import asyncio
    from app.tool_call_evaluation import _evaluate

    result = asyncio.run(_evaluate(dataset))
    assert result["exact_case_count"] == 1
