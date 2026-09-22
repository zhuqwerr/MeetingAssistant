import asyncio

import numpy as np
import pytest

from meeting_assistant.audio import AudioJob
from meeting_assistant.config import ConfigStore
from meeting_assistant.session import Session, StartRequest
from meeting_assistant.storage import Storage


class FakeASR:
    async def prepare(self, _):
        pass

    def transcribe(self, job, *_):
        return [{"start": job.start, "end": job.start + 1, "text": "会议文字", "source": job.source}]


class RecordingSummarizer:
    def __init__(self):
        self.calls = []
        self.modes = []
        self.cited = []
        self.fail = False

    async def generate(self, settings, key, previous, segments, valid, mode="incremental", cited_segments=None):
        self.calls.append([s["id"] for s in segments])
        self.modes.append(mode)
        self.cited.append([s["id"] for s in (cited_segments or [])])
        if self.fail:
            raise ValueError("network unavailable")
        return {"summary": "讨论中", "topics": [], "key_points": [{"text": "新增信息", "sources": [segments[-1]["id"]]}], "todos": [], "suggestions": []}


async def test_incremental_summary_retries_without_advancing_cursor(tmp_path):
    store, config, llm = Storage(tmp_path), ConfigStore(tmp_path), RecordingSummarizer()
    session = Session(StartRequest(), store, config, FakeASR(), llm)
    first = store.add_segment(session.id, 0, 1, "第一次", "mic")
    await session.summarize()
    second = store.add_segment(session.id, 1, 2, "第二次", "mic")
    llm.fail = True
    await session.summarize()
    assert store.summary(session.id)["through_id"] == first["id"]
    assert session.summary_error
    llm.fail = False
    await session.summarize()
    assert llm.calls == [[first["id"]], [second["id"]], [second["id"]]]
    assert store.summary(session.id)["through_id"] == second["id"]
    assert not session.summary_error


async def test_stop_drains_last_chunk_before_final_summary(tmp_path, monkeypatch):
    class FakeCapture:
        def __init__(self, folder, source, mic, speaker, on_job, on_level, on_error):
            self.on_job = on_job
        def start(self):
            self.on_job(AudioJob(np.ones(16000), 0, "mic"))
        def stop(self):
            pass
        def join(self):
            # The final short chunk is delivered only when the recorder flushes.
            self.on_job(AudioJob(np.ones(16000), 1, "mic"))
    monkeypatch.setattr("meeting_assistant.session.Capture", FakeCapture)
    store, llm = Storage(tmp_path), RecordingSummarizer()
    session = Session(StartRequest(), store, ConfigStore(tmp_path), FakeASR(), llm)
    session.start()
    for _ in range(100):
        if len(store.segments(session.id)) == 1:
            break
        await asyncio.sleep(0.01)
    session.stop()
    await asyncio.wait_for(session.done.wait(), timeout=3)
    assert session.status == "ended", session.error
    assert len(store.segments(session.id)) == 2
    assert store.summary(session.id)["through_id"] == store.segments(session.id)[-1]["id"]


async def test_stop_during_model_load_never_opens_microphone(tmp_path, monkeypatch):
    gate = asyncio.Event()
    class SlowASR:
        async def prepare(self, _):
            await gate.wait()
    def forbidden(*args, **kwargs):
        pytest.fail("Capture must not start after cancellation")
    monkeypatch.setattr("meeting_assistant.session.Capture", forbidden)
    session = Session(StartRequest(), Storage(tmp_path), ConfigStore(tmp_path), SlowASR(), RecordingSummarizer())
    session.start()
    await asyncio.sleep(0.01)
    session.stop()
    await asyncio.wait_for(session.done.wait(), 1)
    gate.set()
    await asyncio.sleep(0.01)
    assert session.status == "ended"


def test_process_restart_recovers_interrupted_meeting(tmp_path):
    store = Storage(tmp_path)
    mid = store.create("恢复测试", "mic", "zh")
    store.add_segment(mid, 0, 1, "已保存", "mic")
    store.update(mid, "recording", 1)
    recovered = Storage(tmp_path).get(mid)
    assert recovered["status"] == "interrupted"
    assert recovered["segments"][0]["text"] == "已保存"


async def test_key_events_within_the_delay_make_one_summary(tmp_path, monkeypatch):
    monkeypatch.setattr("meeting_assistant.session.TRIGGER_DELAY", 0.05)
    store, llm = Storage(tmp_path), RecordingSummarizer()
    session = Session(StartRequest(), store, ConfigStore(tmp_path), FakeASR(), llm)
    segment = store.add_segment(session.id, 0, 1, "先听着", "mic")
    session.arm_early_summary()
    session.arm_early_summary()
    await asyncio.sleep(0.2)
    assert llm.calls == [[segment["id"]]]
    later = store.add_segment(session.id, 1, 2, "再改一次", "mic")
    session.arm_early_summary()
    await asyncio.sleep(0.2)
    assert llm.calls == [[segment["id"]], [later["id"]]]


async def test_reconcile_sends_recent_window_and_cited_history(tmp_path):
    store, llm = Storage(tmp_path), RecordingSummarizer()
    session = Session(StartRequest(), store, ConfigStore(tmp_path), FakeASR(), llm)
    first = store.add_segment(session.id, 0, 10, "京东由管理员分配", "mic")
    store.add_segment(session.id, 20, 400, "中间过程", "mic")
    latest = store.add_segment(session.id, 900, 1000, "顺丰改到下周", "mic")
    store.add_summary(session.id, latest["id"], {"summary": "旧状态", "topics": [], "key_points": [], "todos": [{"content": "确认京东", "owner": "未明确", "deadline": "未明确", "sources": [first["id"]]}], "suggestions": []})
    await session.summarize(mode="reconcile")
    assert llm.modes == ["reconcile"]
    assert llm.calls == [[store.segments(session.id)[1]["id"], latest["id"]]]
    assert llm.cited == [[first["id"]]]
    assert store.summary(session.id)["through_id"] == latest["id"]


async def test_reconcile_catches_up_unsummarized_old_audio_before_advancing(tmp_path):
    store, llm = Storage(tmp_path), RecordingSummarizer()
    session = Session(StartRequest(), store, ConfigStore(tmp_path), FakeASR(), llm)
    first = store.add_segment(session.id, 0, 10, "早期决定", "mic")
    last = store.add_segment(session.id, 1800, 1810, "服务恢复后的内容", "mic")
    await session.summarize(mode="reconcile")
    assert llm.modes == ["incremental", "reconcile"]
    assert llm.calls[0] == [first["id"], last["id"]]
    assert store.summary(session.id)["through_id"] == last["id"]


async def test_failed_reconcile_catchup_keeps_cursor_for_retry(tmp_path):
    store, llm = Storage(tmp_path), RecordingSummarizer()
    session = Session(StartRequest(), store, ConfigStore(tmp_path), FakeASR(), llm)
    first = store.add_segment(session.id, 0, 10, "早期决定", "mic")
    store.add_segment(session.id, 1800, 1810, "较晚内容", "mic")
    llm.fail = True
    await session.summarize(mode="reconcile")
    assert store.summary(session.id) is None
    assert llm.modes == ["incremental"]
    llm.fail = False
    await session.summarize()
    assert first["id"] in llm.calls[-1]


async def test_partial_model_state_preserves_saved_summary(tmp_path):
    import httpx
    from meeting_assistant.summary import Summarizer
    llm = Summarizer(httpx.MockTransport(lambda _: httpx.Response(200, json={"message": {"content": '{"summary":"不完整"}'}})))
    store = Storage(tmp_path)
    config = ConfigStore(tmp_path)
    config.settings.summary_provider = "ollama"
    session = Session(StartRequest(), store, config, FakeASR(), llm)
    first = store.add_segment(session.id, 0, 10, "任务", "mic")
    old = store.add_summary(session.id, first["id"], {"summary": "已有内容", "topics": [], "key_points": [], "todos": [{"content": "不能丢失", "sources": [first["id"]]}], "suggestions": []})
    store.add_segment(session.id, 11, 12, "新内容", "mic")
    await session.summarize()
    assert store.summary(session.id) == old
    assert session.summary_error
