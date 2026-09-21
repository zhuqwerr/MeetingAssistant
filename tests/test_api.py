import sys

from fastapi.testclient import TestClient

from meeting_assistant.app import create_app


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
    store.add_segment(mid, 62, 66, "决定周五上线", "mic")
    store.update(mid, "ended", 66)
    with TestClient(create_app(tmp_path)) as client:
        meetings = client.get("/api/meetings").json()
        assert meetings[0]["title"] == "第一场会议"
        assert client.get(f"/api/meetings/{mid}").json()["segments"][0]["text"] == "决定周五上线"
        exported = client.get(f"/api/meetings/{mid}/export")
        assert "01:02" in exported.text and "决定周五上线" in exported.text
        assert "attachment" in exported.headers["content-disposition"]
        assert client.get("/api/meetings/missing").status_code == 404
