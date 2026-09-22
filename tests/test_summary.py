import json

import httpx
import pytest

from meeting_assistant.config import Settings
from meeting_assistant.summary import Summarizer, reconcile_windows, relevant_segments


@pytest.mark.parametrize("provider", ["ollama", "compatible"])
async def test_real_http_contract_and_invalid_source_removal(provider):
    seen = []
    content = {
        "summary": "讨论上线时间",
        "topics": [{"title": "上线", "points": [{"text": "仍在讨论", "sources": [1, 999]}]}],
        "key_points": [],
        "todos": [{"content": "周五上线", "owner": "", "deadline": "未知", "sources": [1, 999]}],
        "suggestions": [],
    }
    def handler(request):
        payload = json.loads(request.content)
        seen.append((request, payload))
        text = json.dumps(content, ensure_ascii=False)
        return httpx.Response(200, json={"message": {"content": text}} if provider == "ollama" else {"choices": [{"message": {"content": text}}]})
    client = Summarizer(httpx.MockTransport(handler))
    result = await client.generate(Settings(summary_provider=provider, summary_url="http://localhost:1234"), "test-secret", None, [{"id": 1, "text": "决定周五上线"}], {1})
    assert result["todos"][0]["sources"] == [1]
    assert result["todos"][0]["owner"] == "未明确"
    assert result["todos"][0]["deadline"] == "未明确"
    assert result["topics"][0]["points"][0]["sources"] == [1]
    request, payload = seen[0]
    assert request.headers["authorization"] == "Bearer test-secret"
    assert payload["stream"] is False
    assert "new_segments" in payload["messages"][1]["content"]
    assert request.url.path == ("/api/chat" if provider == "ollama" else "/chat/completions")
    if provider == "ollama":
        assert payload["options"]["num_predict"] == 1400
    else:
        assert "max_tokens" not in payload


@pytest.mark.parametrize("output", ["not JSON", "{}", '{"summary":"只有概括"}', '{"summary":"概括","topics":[],"key_points":[],"todos":[]}'])
async def test_malformed_model_output_is_not_a_fake_summary(output):
    client = Summarizer(httpx.MockTransport(lambda _: httpx.Response(200, json={"message": {"content": output}})))
    with pytest.raises(ValueError, match="结构化摘要"):
        await client.generate(Settings(), "", None, [{"id": 1, "text": "你好"}], {1})


async def test_excess_model_items_are_bounded_instead_of_rejecting_the_summary():
    content = {
        "title": "上线安排",
        "summary": "讨论上线",
        "topics": [],
        "key_points": [],
        "todos": [],
        "suggestions": [
            {"kind": "missing_info", "title": f"待确认 {index}", "quote": "", "detail": "", "sources": [1]}
            for index in range(9)
        ],
    }
    client = Summarizer(httpx.MockTransport(lambda _: httpx.Response(200, json={"message": {"content": json.dumps(content, ensure_ascii=False)}})))

    result = await client.generate(Settings(), "", None, [{"id": 1, "text": "讨论上线"}], {1})

    assert len(result["suggestions"]) == 4


async def test_provider_errors_do_not_leak_keys_or_response_body():
    client = Summarizer(httpx.MockTransport(lambda _: httpx.Response(401, text="private body")))
    with pytest.raises(ValueError, match="HTTP 401") as caught:
        await client.test(Settings(), "secret")
    assert "secret" not in str(caught.value) and "private body" not in str(caught.value)


def test_reconcile_window_keeps_recent_audio_and_older_citations():
    segments = [
        {"id": 1, "end": 10, "text": "京东账号由管理员分配"},
        {"id": 2, "end": 400, "text": "中间讨论"},
        {"id": 3, "end": 1000, "text": "顺丰改到下周"},
    ]
    state = {"todos": [{"sources": [1]}], "key_points": [{"sources": [3]}]}
    recent, cited = reconcile_windows(segments, state)
    assert [item["id"] for item in recent] == [2, 3]
    assert [item["id"] for item in cited] == [1]


async def test_summary_can_propose_a_meeting_title():
    content = {"title": "支付系统上线安排", "summary": "讨论上线", "topics": [], "key_points": [], "todos": [], "suggestions": []}
    client = Summarizer(httpx.MockTransport(lambda _: httpx.Response(200, json={"message": {"content": json.dumps(content, ensure_ascii=False)}})))
    result = await client.generate(Settings(), "", None, [{"id": 1, "text": "讨论支付系统上线"}], {1})
    assert result["title"] == "支付系统上线安排"


def test_question_retrieval_prefers_overlapping_and_cited_lines():
    segments = [
        {"id": 1, "end": 1, "text": "京东企业账号由张明申请"},
        {"id": 2, "end": 2, "text": "今天天气不错"},
        {"id": 3, "end": 3, "text": "顺丰接口本周联调"},
    ]
    chosen = relevant_segments("京东账号谁申请", segments, {"todos": [{"sources": [3]}]})
    assert [item["id"] for item in chosen] == [1]


def test_unrelated_citations_cannot_displace_question_evidence():
    segments = [{"id": i, "end": i, "text": "无关的旧讨论"} for i in range(1, 13)]
    segments.append({"id": 13, "end": 13, "text": "预算十万元"})
    state = {"key_points": [{"sources": list(range(1, 13))}]}
    assert [s["id"] for s in relevant_segments("预算？", segments, state)] == [13]


def test_question_retrieval_handles_english_case_and_latest_correction():
    segments = [{"id": i, "end": i, "text": "EMS 接口讨论"} for i in range(1, 15)]
    assert [s["id"] for s in relevant_segments("ems", segments, None, limit=2)] == [13, 14]
