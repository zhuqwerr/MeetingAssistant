import json
import sys

import pytest
from fastapi.testclient import TestClient

from meeting_assistant.app import create_app
from meeting_assistant.config import ConfigStore, Settings, SettingsUpdate


def test_config_persistence_secret_redaction_and_origin_guard(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/health").status_code == 200
        settings = client.get("/api/settings").json()
        settings["api_key"] = "test-api-secret"
        response = client.put("/api/settings", json=settings)
        assert response.status_code == 200
        assert response.json()["has_api_key"] is True
        assert "test-api-secret" not in response.text
        assert "test-api-secret" not in (tmp_path / "settings.json").read_text()
        assert client.put("/api/settings", json=settings, headers={"Origin": "https://evil.example"}).status_code == 403
        assert client.post("/api/meetings", json={}, headers={"Origin": "http://localhost:bad"}).status_code == 403
        assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
        settings["summary_url"] = "http://remote.example/v1"
        assert client.put("/api/settings", json=settings).status_code == 422
    if sys.platform == "win32":
        from meeting_assistant.config import ConfigStore
        assert ConfigStore(tmp_path).key == "test-api-secret"


def test_history_and_export_survive_new_app_instance(tmp_path):
    app = create_app(tmp_path)
    store = app.state.store
    mid = store.create("第一场会议", "mic", "zh")
    segment = store.add_segment(mid, 62, 66, "决定周五上线", "mic")
    store.add_summary(mid, segment["id"], {"overview": "团队决定周五上线", "key_points": [], "decisions": [], "action_items": []})
    store.update(mid, "ended", 66)
    with TestClient(create_app(tmp_path)) as client:
        meetings = client.get("/api/meetings").json()
        assert meetings[0]["title"] == "第一场会议"
        assert meetings[0]["summary"]["content"]["overview"] == "团队决定周五上线"
        assert client.get(f"/api/meetings/{mid}").json()["segments"][0]["text"] == "决定周五上线"
        exported = client.get(f"/api/meetings/{mid}/export")
        assert "01:02" in exported.text and "决定周五上线" in exported.text
        assert "attachment" in exported.headers["content-disposition"]
        assert client.get("/api/meetings/missing").status_code == 404


def test_export_renders_new_state_and_keeps_legacy_minutes(tmp_path):
    app = create_app(tmp_path)
    store = app.state.store
    fresh = store.create("新会议", "mic", "zh")
    store.add_segment(fresh, 12, 16, "张明本周五完成顺丰联调", "mic")
    store.add_summary(fresh, 1, {
        "summary": "讨论顺丰联调",
        "topics": [{"title": "顺丰接口", "points": [{"text": "本周完成联调", "sources": [1]}]}],
        "key_points": [{"text": "张明负责顺丰接口", "sources": [1]}],
        "todos": [{"content": "完成顺丰接口联调", "owner": "张明", "deadline": "本周五", "sources": [1]}],
        "suggestions": [{"kind": "missing_info", "title": "待办尚未明确负责人", "quote": "下周测试 EMS", "detail": "没有听到负责人", "sources": [1]}],
    })
    legacy = store.create("旧会议", "mic", "zh")
    store.add_segment(legacy, 62, 66, "决定周五上线", "mic")
    store.add_summary(legacy, 1, {"overview": "讨论上线", "key_points": [{"text": "上线窗口", "sources": [1]}], "decisions": [], "action_items": []})
    with TestClient(app) as client:
        exported = client.get(f"/api/meetings/{fresh}/export").text
        assert "即时摘要" in exported and "讨论顺丰联调" in exported and "决策结论" in exported and "关键要点" in exported and "负责人：张明" in exported and "判断：没有听到负责人" in exported
        assert exported.index("决策结论") < exported.index("关键要点")
        old = client.get(f"/api/meetings/{legacy}/export").text
        assert "会议摘要" in old and "讨论上线" in old and "决策结论" in old and "关键要点" in old
        assert old.index("决策结论") < old.index("关键要点")


def test_ask_answers_from_the_current_meeting(tmp_path):
    class Answering:
        async def answer(self, settings, key, state, segments, question):
            assert question == "谁负责顺丰"
            assert state["summary"] == "讨论顺丰"
            return {"answer": "由张明负责", "sources": [segments[0]["id"]]}

    app = create_app(tmp_path, summarizer=Answering())
    store = app.state.store
    mid = store.create("问答", "mic", "zh")
    store.add_segment(mid, 1, 2, "顺丰由张明负责", "mic")
    store.add_summary(mid, 1, {"summary": "讨论顺丰", "topics": [], "key_points": [], "todos": [], "suggestions": []})
    with TestClient(app) as client:
        response = client.post(f"/api/meetings/{mid}/ask", json={"question": "谁负责顺丰"})
        assert response.status_code == 200
        assert response.json()["answer"] == "由张明负责"
        assert client.post(f"/api/meetings/{mid}/ask", json={"question": ""}).status_code == 422


def test_fresh_config_uses_fixed_local_medium_and_allows_missing_summary_api(tmp_path):
    settings = ConfigStore(tmp_path).settings
    assert settings.summary_url == ""
    assert settings.summary_model == ""
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/settings").json()["summary_configured"] is False


def test_summary_interval_defaults_to_two_minutes_and_requires_whole_minutes(tmp_path):
    assert Settings().summary_interval == 120
    assert Settings(summary_interval=60).summary_interval == 60
    assert Settings(summary_interval=3600).summary_interval == 3600
    with pytest.raises(ValueError):
        Settings(summary_interval=90)
    with pytest.raises(ValueError):
        Settings(summary_interval=3660)


@pytest.mark.parametrize("legacy_interval, expected", [(30, 120), (60, 60), (90, 120), (300, 300), (5000, 3600)])
def test_legacy_summary_interval_migrates_to_whole_minutes(tmp_path, legacy_interval, expected):
    folder = tmp_path / f"legacy-{legacy_interval}"
    folder.mkdir()
    raw = {"summary_interval": legacy_interval}
    (folder / "settings.json").write_text(json.dumps({"schema_version": 3, "settings": raw}), encoding="utf-8")

    assert ConfigStore(folder).settings.summary_interval == expected


def test_api_rejects_summary_interval_that_is_not_a_whole_minute(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.put("/api/settings", json={"summary_interval": 90})
        assert response.status_code == 422


def test_legacy_settings_preserve_supported_whisper_model_and_compatible_api(tmp_path):
    folder = tmp_path / "legacy-compatible"
    folder.mkdir()
    legacy = {"asr_engine": "whisper", "asr_model": "small", "summary_provider": "compatible",
              "summary_url": "https://api.example.com/v1", "summary_model": "meeting-model",
              "summary_interval": 60, "vocabulary": "项目甲"}
    (folder / "settings.json").write_text(json.dumps({"settings": legacy, "protected_key": None}), encoding="utf-8")
    store = ConfigStore(folder)
    assert store.settings.summary_url == "https://api.example.com/v1"
    assert store.settings.summary_model == "meeting-model"
    assert store.settings.vocabulary == "项目甲"
    assert store.settings.asr_model == "small"
    assert not hasattr(store.settings, "asr_engine")


def test_legacy_ollama_settings_do_not_become_an_api_endpoint(tmp_path):
    folder = tmp_path / "legacy"
    folder.mkdir()
    raw = {"asr_engine": "funasr", "asr_model": "paraformer-zh-streaming", "summary_provider": "ollama",
           "summary_url": "http://127.0.0.1:11434", "summary_model": "qwen3:4b", "vocabulary": ""}
    (folder / "settings.json").write_text(json.dumps({"settings": raw, "protected_key": None}), encoding="utf-8")
    settings = ConfigStore(folder).settings
    assert settings.summary_url == ""
    assert settings.summary_model == ""


def test_new_api_settings_survive_reloading_after_legacy_migration(tmp_path):
    store = ConfigStore(tmp_path)
    update = SettingsUpdate(summary_url="https://api.example.com/v1", summary_model="meeting-model")
    store.save(update)
    reloaded = ConfigStore(tmp_path)
    assert reloaded.settings.summary_url == update.summary_url
    assert reloaded.settings.summary_model == update.summary_model
    saved = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert saved["schema_version"] == 4


def test_whisper_model_selection_is_saved_and_reported(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        response = client.put("/api/settings", json={"asr_model": "small"})
        assert response.status_code == 200
        assert response.json()["asr_model"] == "small"
        health = client.get("/api/health").json()["model"]
        assert health["selected"] == "small"
        assert health["id"] == "small"
        assert {item["id"] for item in health["models"]} == {"base", "small", "medium"}


def test_desktop_api_requires_the_per_launch_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETING_ASSISTANT_DESKTOP", "1")
    monkeypatch.setenv("MEETING_ASSISTANT_DESKTOP_TOKEN", "test-session-token")
    monkeypatch.setenv("MEETING_ASSISTANT_PORT", "43210")
    monkeypatch.delenv("MEETING_ASSISTANT_DEV_FRONTEND_PORT", raising=False)
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/api/health").status_code == 401
        client.cookies.set("ma_session", "wrong-token")
        assert client.get("/api/health").status_code == 401
        client.cookies.set("ma_session", "test-session-token")
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/health", headers={"Origin": "http://127.0.0.1:43211"}).status_code == 403
        assert client.get("/api/health", headers={"Origin": "http://127.0.0.1:5179"}).status_code == 403


def test_desktop_settings_test_allows_vite_dev_server_origin(tmp_path, monkeypatch):
    monkeypatch.setenv("MEETING_ASSISTANT_DESKTOP", "1")
    monkeypatch.setenv("MEETING_ASSISTANT_DESKTOP_TOKEN", "test-session-token")
    monkeypatch.setenv("MEETING_ASSISTANT_PORT", "43210")
    monkeypatch.setenv("MEETING_ASSISTANT_DEV_FRONTEND_PORT", "5179")

    class TestSummarizer:
        async def test(self, settings, key):
            assert settings.summary_url == "https://api.example.com/v1"
            assert key == "test-api-key"

    with TestClient(create_app(tmp_path, summarizer=TestSummarizer())) as client:
        client.cookies.set("ma_session", "test-session-token")
        same_origin = client.post(
            "/api/settings/test",
            headers={"Origin": "http://127.0.0.1:43210"},
            json={"summary_url": "https://api.example.com/v1", "summary_model": "test-model", "api_key": "test-api-key"},
        )
        assert same_origin.status_code == 200, same_origin.text
        response = client.post(
            "/api/settings/test",
            headers={"Origin": "http://127.0.0.1:5179"},
            json={"summary_url": "https://api.example.com/v1", "summary_model": "test-model", "api_key": "test-api-key"},
        )
    assert response.status_code == 200, response.text


def test_model_download_can_be_started_and_reports_progress(tmp_path, monkeypatch):
    class ModelManager:
        status = "missing"
        error = ""
        operation_model = None
        installed = set()

        def model_info(self, selected="medium"):
            models = [{"id": name, "name": f"Whisper {name.title()}", "revision": "fixed",
                       "status": "installed" if name in self.installed else self.status if name == self.operation_model else "missing",
                       "installed": name in self.installed, "progress": 100 if name in self.installed else 0,
                       "downloaded_bytes": 0, "total_bytes": 1, "size_label": "test", "error": self.error}
                      for name in ("base", "small", "medium")]
            chosen = next(model for model in models if model["id"] == selected)
            return {**chosen, "selected": selected, "models": models}

        async def download(self, model="medium"):
            self.installed.add(model)
            self.status = "installed"

    manager = ModelManager()
    app = create_app(tmp_path, transcriber=manager)
    with TestClient(app) as client:
        assert client.get("/api/health").json()["model"]["installed"] is False
        assert client.post("/api/meetings", json={}).status_code == 409
        assert client.post("/api/model/download", json={"model": "base"}).status_code == 200
        for _ in range(30):
            state = client.get("/api/health").json()["model"]["models"]
            if next(model for model in state if model["id"] == "base")["installed"]:
                break
            import time
            time.sleep(0.01)
        assert next(model for model in state if model["id"] == "base")["installed"] is True
        assert client.post("/api/model/download").status_code == 200
