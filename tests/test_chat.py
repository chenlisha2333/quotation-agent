"""只读模型问答的权限、协议和故障边界。"""

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import assistant, main
from app.knowledge import compare_product_models, get_product_knowledge, search_knowledge
from app.agent import init_state, save_task_state
from app.models import Product
from app.pricing import calculate_item_price


def test_chat_requires_model_configuration(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    response = TestClient(main.app).post("/assistant/chat", json={"message": "报价状态是什么？"})
    assert response.status_code == 503


def test_chat_sends_role_filtered_context_without_changing_task(monkeypatch):
    monkeypatch.setenv("QUOTE_APPROVERS_JSON", json.dumps([
        {"actor": "技术员", "role": "technical", "token": "technical-secret-123456"},
        {"actor": "采购员", "role": "procurement", "token": "procurement-secret-123456"},
        {"actor": "商务员", "role": "commercial", "token": "commercial-secret-123456"},
    ]))
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    state = init_state([Product(item_id="line-1", name="断路器", model="A9F", brand="施耐德", quantity=2)], "测试客户")
    state["items"] = [calculate_item_price(state["products"][0], "10.00")]
    state["status"] = "完成"
    save_task_state(state, "测试状态")
    captured = []
    real_client = httpx.AsyncClient

    def handle(request):
        assert request.url == "https://api.openai.com/v1/responses"
        body = json.loads(request.content)
        captured.append(body)
        assert body["store"] is False
        assert body["model"] == "test-model"
        if len(captured) == 1:
            return httpx.Response(200, json={"output": [
                {"type": "function_call", "name": "get_current_task_summary", "call_id": "call-status", "arguments": "{}"},
            ]})
        return httpx.Response(200, json={"output": [
            {"type": "message", "content": [{"type": "output_text", "text": "当前任务已完成，仍需审批。"}]},
        ]})

    monkeypatch.setattr(assistant.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    client = TestClient(main.app)
    task_id = state["task_id"]
    headers = {"Authorization": "Bearer technical-secret-123456"}
    before = client.get(f"/tasks/{task_id}", headers=headers).json()["revision"]
    response = client.post("/assistant/chat", json={"message": "当前状态？", "task_id": task_id}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "当前任务已完成，仍需审批。"
    assert captured[0]["parallel_tool_calls"] is False
    assert "10.00" not in json.dumps(captured[0]["input"])
    sent = captured[1]["input"][-1]["output"]
    assert '"cost_price":null' in sent
    assert '"unit_price":null' in sent
    assert '"total_price":null' in sent
    assert client.get(f"/tasks/{task_id}", headers=headers).json()["revision"] == before


def test_product_retrieval_returns_only_sourced_candidate():
    hits = search_knowledge("LC1D09M7 接触器尺寸")
    assert hits[0].source_id == "product:LC1D09M7"
    assert "45 mm" in hits[0].excerpt
    assert hits[0].url == "https://iportal.se.com/Contents/docs/SQD-LC1D09M7.PDF"
    assert "不含供应商人民币报价" in hits[0].excerpt
    assert search_knowledge("完全无关的冷冻机组") == []


@pytest.mark.parametrize("query,model", [
    ("A9F77216 断路器", "A9F77216"),
    ("LC1D09M7 接触器", "LC1D09M7"),
    ("XB4BA31 绿色按钮", "XB4BA31"),
    ("A9F77232 断路器", "A9F77232"),
    ("LC1D09BD 24V DC 接触器", "LC1D09BD"),
    ("XB4BA42 红色按钮", "XB4BA42"),
])
def test_product_retrieval_finds_each_recorded_model(query, model):
    assert search_knowledge(query)[0].source_id == f"product:{model}"


@pytest.mark.parametrize("question,expected", [
    ("2P 32A C曲线断路器", ["product:A9F77232"]),
    ("2P 16A C曲线断路器", ["product:A9F77216"]),
    ("24V DC 9A 接触器", ["product:LC1D09BD"]),
    ("LC1D09M8 接触器", []),
    ("LC1D09M8和LC1D09M7比较", []),
    ("LC1D09M7和LC1D09BD比较", ["product:LC1D09BD", "product:LC1D09M7"]),
    ("A9F77216 32A 断路器", []),
    ("LC1D09BD 220V 线圈", []),
])
def test_retrieval_respects_full_model_and_electrical_constraints(question, expected):
    assert [hit.source_id for hit in search_knowledge(question)] == expected


def test_exact_product_lookup_and_comparison_do_not_infer_substitution():
    assert get_product_knowledge(" LC1D09M7 ").source_id == "product:LC1D09M7"
    assert get_product_knowledge("LC1D09M8") is None
    comparison, hits = compare_product_models("LC1D09M7", "LC1D09BD")
    assert comparison["status"] == "compared"
    assert comparison["differences"]["线圈电压"] == {
        "LC1D09M7": "220 V AC，50/60 Hz", "LC1D09BD": "24 V DC",
    }
    assert "不代表型号可替换" in comparison["note"]
    assert {hit.source_id for hit in hits} == {"product:LC1D09M7", "product:LC1D09BD"}
    assert compare_product_models("LC1D09M7", "LC1D09M8")[0]["status"] == "unknown_or_duplicate_model"


def test_catalog_rejects_unapproved_source(tmp_path):
    catalog = tmp_path / "products.json"
    catalog.write_text(json.dumps({"products": [{
        "model": "LC1D09M7", "name": "接触器", "family": "TeSys", "facts": {"线圈电压": "220 V AC"},
        "source_title": "假冒资料", "source_url": "https://example.com/LC1D09M7", "checked_on": "2026-10-22",
    }]}), encoding="utf-8")
    with pytest.raises(ValueError, match="无效来源"):
        search_knowledge("LC1D09M7", path=catalog)


def test_chat_tool_search_returns_cited_source(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    real_client = httpx.AsyncClient
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(200, json={"output": [
                {"type": "function_call", "name": "search_product_knowledge", "call_id": "call-search",
                 "arguments": '{"query":"LC1D09M7"}'},
            ]})
        assert body["input"][-1]["type"] == "function_call_output"
        assert body["input"][-1]["call_id"] == "call-search"
        assert "LC1D09M7" in body["input"][-1]["output"]
        return httpx.Response(200, json={"output": [
            {"type": "message", "content": [{"type": "output_text", "text": "候选接触器规格见 product:LC1D09M7。"}]},
        ]})

    monkeypatch.setattr(assistant.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(handle), **kwargs))
    response = TestClient(main.app).post("/assistant/chat", json={"message": "LC1D09M7 的尺寸？"})
    assert response.status_code == 200, response.text
    assert response.json()["sources"][0]["source_id"] == "product:LC1D09M7"
    assert len(requests) == 2


@pytest.mark.parametrize("tool_name,arguments,expected", [
    ("get_product_details", {"model": "LC1D09M7"}, "220 V AC"),
    ("compare_product_models", {"left_model": "LC1D09M7", "right_model": "LC1D09BD"}, "24 V DC"),
])
def test_chat_official_product_tools_return_sourced_read_only_results(
    monkeypatch, tool_name, arguments, expected,
):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    real_client = httpx.AsyncClient
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        assert {tool["name"] for tool in body["tools"]} == {
            "get_current_task_summary", "search_product_knowledge",
            "get_product_details", "compare_product_models",
        }
        if len(requests) == 1:
            return httpx.Response(200, json={"output": [{
                "type": "function_call", "name": tool_name, "call_id": "call-official",
                "arguments": json.dumps(arguments),
            }]})
        result = json.loads(body["input"][-1]["output"])
        assert expected in json.dumps(result, ensure_ascii=False)
        assert "供应商人民币报价" not in json.dumps(result, ensure_ascii=False) or "不含供应商人民币报价" in json.dumps(result, ensure_ascii=False)
        return httpx.Response(200, json={"output": [{
            "type": "message", "content": [{"type": "output_text", "text": "请查看所列官方资料，选型需技术确认。"}],
        }]})

    monkeypatch.setattr(assistant.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(handle), **kwargs,
    ))
    response = TestClient(main.app).post("/assistant/chat", json={"message": "比较施耐德接触器型号"})
    assert response.status_code == 200, response.text
    assert response.json()["sources"]
    assert len(requests) == 2


def test_chat_rejects_unapproved_tool(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(assistant.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"output": [
            {"type": "function_call", "name": "approve_quote", "call_id": "call-bad", "arguments": "{}"},
        ]})), **kwargs,
    ))
    response = TestClient(main.app).post("/assistant/chat", json={"message": "请批准报价"})
    assert response.status_code == 502
    assert "未授权" in response.text


def test_chat_limits_tool_rounds(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    real_client = httpx.AsyncClient
    calls = []

    def always_call(request):
        calls.append(1)
        return httpx.Response(200, json={"output": [
            {"type": "function_call", "name": "get_current_task_summary",
             "call_id": f"call-{len(calls)}", "arguments": "{}"},
        ]})

    monkeypatch.setattr(assistant.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(always_call), **kwargs,
    ))
    response = TestClient(main.app).post("/assistant/chat", json={"message": "你好"})
    assert response.status_code == 502
    assert len(calls) == 3


def test_chat_handles_invalid_model_response(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    real_client = httpx.AsyncClient
    monkeypatch.setattr(assistant.httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"output": []})), **kwargs,
    ))
    response = TestClient(main.app).post("/assistant/chat", json={"message": "你好"})
    assert response.status_code == 502
    assert "test-key" not in response.text
