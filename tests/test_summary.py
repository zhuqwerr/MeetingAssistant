import json

import httpx
import pytest

from meeting_assistant.config import Settings
from meeting_assistant.summary import Summarizer


@pytest.mark.parametrize("provider", ["ollama", "compatible"])
async def test_real_http_contract_and_invalid_source_removal(provider):
    seen = []
    content = {"overview": "讨论上线时间", "key_points": [], "decisions": [{"text": "周五上线", "sources": [1, 999]}], "action_items": []}
    def handler(request):
        payload = json.loads(request.content)
        seen.append((request, payload))
        text = json.dumps(content, ensure_ascii=False)
        return httpx.Response(200, json={"message": {"content": text}} if provider == "ollama" else {"choices": [{"message": {"content": text}}]})
    client = Summarizer(httpx.MockTransport(handler))
    result = await client.generate(Settings(summary_provider=provider, summary_url="http://localhost:1234"), "test-secret", None, [{"id": 1, "text": "决定周五上线"}], {1})
    assert result["decisions"][0]["sources"] == [1]
    request, payload = seen[0]
    assert request.headers["authorization"] == "Bearer test-secret"
    assert payload["stream"] is False
    assert "new_segments" in payload["messages"][1]["content"]
    assert request.url.path == ("/api/chat" if provider == "ollama" else "/chat/completions")


@pytest.mark.parametrize("output", ["not JSON", "{}"])
async def test_malformed_model_output_is_not_a_fake_summary(output):
    client = Summarizer(httpx.MockTransport(lambda _: httpx.Response(200, json={"message": {"content": output}})))
    with pytest.raises(ValueError, match="结构化摘要"):
        await client.generate(Settings(), "", None, [{"id": 1, "text": "你好"}], {1})


async def test_provider_errors_do_not_leak_keys_or_response_body():
    client = Summarizer(httpx.MockTransport(lambda _: httpx.Response(401, text="private body")))
    with pytest.raises(ValueError, match="HTTP 401") as caught:
        await client.test(Settings(), "secret")
    assert "secret" not in str(caught.value) and "private body" not in str(caught.value)
