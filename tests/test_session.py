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
        self.fail = False

    async def generate(self, settings, key, previous, segments, valid):
        self.calls.append([s["id"] for s in segments])
        if self.fail:
            raise ValueError("network unavailable")
        return {"overview": "讨论中", "key_points": [{"text": "新增信息", "sources": [segments[-1]["id"]]}], "decisions": [], "action_items": []}


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
