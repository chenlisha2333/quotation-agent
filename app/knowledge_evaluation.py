"""对冻结的合成问题集评估只读产品资料检索；不调用模型或修改业务数据。"""

import argparse
import json
from pathlib import Path
from typing import Any

from .knowledge import search_knowledge

DEFAULT_DATASET = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "synthetic_knowledge_eval.json"
)


class EvaluationDatasetError(ValueError):
    """冻结评测集格式或安全标记无效。"""


def load_dataset(path: Path = DEFAULT_DATASET) -> dict[str, Any]:
    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationDatasetError("评测集无法读取或不是有效 JSON") from exc
    if not isinstance(dataset, dict) or dataset.get("data_class") != "synthetic":
        raise EvaluationDatasetError("当前工具只接受明确标记为 synthetic 的合成评测集")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise EvaluationDatasetError("评测集必须包含非空 cases")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise EvaluationDatasetError("评测案例必须是对象")
        case_id, query, expected = case.get("id"), case.get("query"), case.get("expected_source_ids")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise EvaluationDatasetError("评测案例 id 必须非空且唯一")
        seen.add(case_id)
        if not isinstance(query, str) or not query.strip() or len(query) > 160:
            raise EvaluationDatasetError(f"{case_id} 的 query 必须为 1–160 字符")
        if not isinstance(expected, list) or any(not isinstance(item, str) for item in expected):
            raise EvaluationDatasetError(f"{case_id} 的 expected_source_ids 必须是字符串列表")
        if len(expected) != len(set(expected)):
            raise EvaluationDatasetError(f"{case_id} 的预期来源不能重复")
    return dataset


def evaluate_dataset(dataset: dict[str, Any], *, k: int = 3) -> dict[str, Any]:
    """逐例运行词项检索，保留命中清单和简单可复现的集合指标。"""
    if k < 1:
        raise ValueError("k 必须大于 0")
    if dataset.get("data_class") != "synthetic" or not dataset.get("cases"):
        raise EvaluationDatasetError("只接受非空的合成评测集")

    results = []
    expected_relevant = no_answer = hit = abstained = exact = 0
    for case in dataset["cases"]:
        expected = case["expected_source_ids"]
        actual = [item.source_id for item in search_knowledge(case["query"], limit=k)]
        if expected:
            expected_relevant += len(set(expected))
            hit += len(set(expected) & set(actual[:k]))
        else:
            no_answer += 1
            abstained += not actual
        exact += set(expected) == set(actual)
        results.append({
            "id": case["id"],
            "expected_source_ids": expected,
            "actual_source_ids": actual,
            "passed": set(expected) == set(actual),
            "error": _error_category(expected, actual),
        })

    total = len(results)
    return {
        "dataset_id": dataset.get("dataset_id", "unnamed"),
        "data_class": "synthetic",
        "retriever": "app.knowledge.search_knowledge",
        "k": k,
        "sample_count": total,
        "exact_match_count": exact,
        "exact_match_rate": exact / total,
        "recall_at_k": hit / expected_relevant if expected_relevant else None,
        "abstention_recall": abstained / no_answer if no_answer else None,
        "false_positive_rate_on_no_answer": (no_answer - abstained) / no_answer if no_answer else None,
        "cases": results,
        "limitations": [
            "合成固定案例仅用于代码回归，不代表真实业务检索准确率。",
            "只评估检索来源 ID；不评估模型回答、引用完整性、工具选择或业务选型正确性。",
        ],
    }


def _error_category(expected: list[str], actual: list[str]) -> str | None:
    if set(expected) == set(actual):
        return None
    if not expected and actual:
        return "false_positive_on_no_answer"
    if expected and not actual:
        return "missed_expected_source"
    if expected and not set(expected).intersection(actual):
        return "wrong_source"
    return "unexpected_or_missing_source"


def main() -> None:
    parser = argparse.ArgumentParser(description="评估只读产品检索（仅接受合成数据）")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--k", type=int, default=3)
    args = parser.parse_args()
    result = evaluate_dataset(load_dataset(args.dataset), k=args.k)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
