from types import SimpleNamespace

import numpy as np
import pytest

from meeting_assistant.asr import Transcriber
from meeting_assistant.audio import AudioJob, RATE
from meeting_assistant.config import Settings, preferred_asr


def test_hallucination_candidates_do_not_reach_transcript():
    # Scores and timings observed when replaying the reported microphone recording.
    candidates = [
        SimpleNamespace(text="请不吝点赞 订阅 转发 打赏支持明镜与点点栏目", start=0, end=1.26,
                        no_speech_prob=0.7455, avg_logprob=-0.388, compression_ratio=0.85),
        SimpleNamespace(text="请不吝说点赞,订阅,转发,打赏,转发,转发,转发,转发,转发,转发,转发。", start=0, end=2,
                        no_speech_prob=0.1978, avg_logprob=-0.411, compression_ratio=1.596),
        # The same topic genuinely spoken at a plausible rate must remain allowed.
        SimpleNamespace(text="请点赞订阅", start=0, end=2,
                        no_speech_prob=0.05, avg_logprob=-0.3, compression_ratio=0.8),
    ]
    engine = Transcriber()
    engine.model = SimpleNamespace(transcribe=lambda *a, **k: (iter(candidates), None))
    result = engine.transcribe(AudioJob(np.zeros(4 * RATE), 10, "mic"), "zh", "")
    assert [item["text"] for item in result] == ["请点赞订阅"]
    assert result[0]["start"] == 10


class Closable:
    def __init__(self, order, name):
        self.order, self.name = order, name

    def close(self):
        self.order.append(f"close:{self.name}")


async def test_switching_model_releases_the_loaded_one_first(monkeypatch):
    order = []
    engine = Transcriber()
    engine.model = Closable(order, "old")
    engine.signature = ("small", "cpu")

    def load(settings):
        order.append(f"load:{settings.asr_model}:{settings.asr_device}")
        return Closable(order, "new")

    monkeypatch.setattr(engine, "_load", load)
    await engine.prepare(Settings(asr_model="large-v3-turbo", asr_device="cuda"))
    assert order == ["close:old", "load:large-v3-turbo:cuda"]
    assert engine.status == "ready"
    assert engine.signature == ("large-v3-turbo", "cuda")


def test_windows_cuda_loads_outside_the_server_process(monkeypatch):
    engine = Transcriber()
    sentinel = object()
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setattr("meeting_assistant.gpu_worker.GPUModel", lambda settings: sentinel)
    assert engine._load(Settings(asr_model="large-v3-turbo", asr_device="cuda")) is sentinel


def test_preferred_model_follows_cuda_runtime(monkeypatch):
    monkeypatch.setattr("meeting_assistant.config.cuda_runtime_installed", lambda: True)
    assert preferred_asr() == ("large-v3-turbo", "cuda")
    monkeypatch.setattr("meeting_assistant.config.cuda_runtime_installed", lambda: False)
    assert preferred_asr() == ("small", "cpu")


async def test_failed_switch_does_not_keep_the_released_model(monkeypatch):
    engine = Transcriber()
    engine.model = Closable([], "old")
    engine.signature = ("small", "cpu")
    def load(_settings):
        raise RuntimeError("cuda missing")

    monkeypatch.setattr(engine, "_load", load)
    with pytest.raises(ValueError):
        await engine.prepare(Settings(asr_model="large-v3-turbo", asr_device="cuda"))
    assert engine.model is None
    assert engine.status == "error"
