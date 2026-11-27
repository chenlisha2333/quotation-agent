import json

import pytest

from app.knowledge_evaluation import (
    EvaluationDatasetError,
    evaluate_dataset,
    load_dataset,
)


def test_synthetic_retrieval_evaluation_is_reproducible():
    first = evaluate_dataset(load_dataset())
    second = evaluate_dataset(load_dataset())

    assert first == second
    assert first["data_class"] == "synthetic"
    assert first["sample_count"] == 10
    assert first["exact_match_count"] == 10
    assert first["recall_at_k"] == 1.0
    assert first["abstention_recall"] == 1.0
    assert first["false_positive_rate_on_no_answer"] == 0.0
    assert all(case["error"] is None for case in first["cases"])


def test_evaluation_refuses_dataset_without_synthetic_marker(tmp_path):
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps({"data_class": "business", "cases": []}), encoding="utf-8")

    with pytest.raises(EvaluationDatasetError, match="synthetic"):
        load_dataset(path)


def test_evaluation_marks_wrong_source_and_false_positive(monkeypatch):
    from app import knowledge_evaluation
    from app.knowledge import KnowledgeHit

    monkeypatch.setattr(knowledge_evaluation, "search_knowledge", lambda query, limit: [
        KnowledgeHit(source_id="product:WRONG", title="wrong", excerpt="", url="https://example.test", checked_on="2026-01-01")
    ])
    dataset = {
        "data_class": "synthetic",
        "cases": [
            {"id": "wrong", "query": "some product", "expected_source_ids": ["product:RIGHT"]},
            {"id": "abstain", "query": "unknown", "expected_source_ids": []},
        ],
    }

    result = evaluate_dataset(dataset)

    assert result["cases"][0]["error"] == "wrong_source"
    assert result["cases"][1]["error"] == "false_positive_on_no_answer"
    assert result["false_positive_rate_on_no_answer"] == 1.0


def test_evaluation_rejects_non_positive_k():
    with pytest.raises(ValueError, match="大于 0"):
        evaluate_dataset({"data_class": "synthetic", "cases": [{
            "id": "one", "query": "LC1D09M7", "expected_source_ids": ["product:LC1D09M7"],
        }]}, k=0)
