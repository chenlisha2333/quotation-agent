"""离线评估只读 Tool Calling 后端白名单；不调用模型或业务写接口。"""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from .assistant import AIRequestError, _run_tool

DEFAULT_DATASET = (
    Path(__file__).resolve().parent.parent
    / "tests"
    / "fixtures"
    / "synthetic_tool_call_eval.json"
)


class ToolEvaluationDatasetError(ValueError):
    """合成工具调用数据集无效。"""


def load_dataset(path: Path = DEFAULT_DATASET) -> dict[str, Any]:
    try:
        dataset = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ToolEvaluationDatasetError("工具调用评测集无法读取或不是有效 JSON") from exc
    if not isinstance(dataset, dict) or dataset.get("data_class") != "synthetic":
        raise ToolEvaluationDatasetError("当前评测器只接受 data_class=synthetic 的测试集")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ToolEvaluationDatasetError("评测集必须包含非空 cases")
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ToolEvaluationDatasetError("工具调用案例必须是对象")
        case_id = case.get("id")
        if not isinstance(case_id, str) or not case_id.strip() or case_id in seen:
            raise ToolEvaluationDatasetError("案例 id 必须非空且唯一")
        seen.add(case_id)
        if case.get("expected_outcome") not in {"allow", "deny"}:
            raise ToolEvaluationDatasetError(f"{case_id} 的 expected_outcome 必须是 allow 或 deny")
        if not isinstance(case.get("call"), dict):
            raise ToolEvaluationDatasetError(f"{case_id} 缺少 call 对象")
        if not isinstance(case.get("expected_source_ids", []), list):
            raise ToolEvaluationDatasetError(f"{case_id} 的 expected_source_ids 必须是列表")
    return dataset


async def _evaluate(dataset: dict[str, Any]) -> dict[str, Any]:
    context = {"task_id": "synthetic-task", "revision": 3, "status": "人工确认"}
    results = []
    allow_total = deny_total = allowed = denied = exact = 0
    for case in dataset["cases"]:
        expected = case["expected_outcome"]
        allow_total += expected == "allow"
        deny_total += expected == "deny"
        try:
            case_context = case["context"] if "context" in case else context
            output, hits = await _run_tool(case["call"], case_context)
            outcome = "allow"
            response = json.loads(output["output"])
            source_ids = sorted(hit.source_id for hit in hits)
            expected_contains = case.get("expected_output_contains")
            output_json = json.dumps(response, ensure_ascii=False)
            result_match = (
                set(source_ids) == set(case.get("expected_source_ids", []))
                and (expected_contains is None or expected_contains in output_json)
            )
        except AIRequestError:
            outcome = "deny"
            source_ids = []
            result_match = expected == "deny"
        allowed += outcome == "allow" and expected == "allow"
        denied += outcome == "deny" and expected == "deny"
        passed = outcome == expected and result_match
        exact += passed
        results.append({
            "id": case["id"],
            "expected_outcome": expected,
            "actual_outcome": outcome,
            "expected_source_ids": sorted(case.get("expected_source_ids", [])),
            "actual_source_ids": source_ids,
            "passed": passed,
        })

    total = len(results)
    return {
        "dataset_id": dataset.get("dataset_id", "unnamed"),
        "data_class": "synthetic",
        "evaluator": "app.assistant._run_tool",
        "sample_count": total,
        "expected_allow_count": allow_total,
        "correct_allow_count": allowed,
        "expected_deny_count": deny_total,
        "correct_deny_count": denied,
        "exact_case_count": exact,
        "exact_case_rate": exact / total,
        "cases": results,
        "limitations": [
            "仅评估后端工具白名单、参数拒绝和固定上下文结果；未评估模型是否选对工具。",
            "只使用合成数据，不构成真实模型安全或企业权限验收。",
            "调用 _run_tool 的合成任务摘要，不访问 SQLite，也不验证 API 层身份权限。",
        ],
    }


def evaluate_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    if dataset.get("data_class") != "synthetic" or not dataset.get("cases"):
        raise ToolEvaluationDatasetError("只接受非空合成评测集")
    return asyncio.run(_evaluate(dataset))


def main() -> None:
    parser = argparse.ArgumentParser(description="评估只读工具白名单与参数边界（合成数据）")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()
    print(json.dumps(evaluate_dataset(load_dataset(args.dataset)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
