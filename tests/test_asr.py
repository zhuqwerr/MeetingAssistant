from types import SimpleNamespace

import numpy as np

from meeting_assistant.asr import Transcriber
from meeting_assistant.audio import AudioJob, RATE
from meeting_assistant.config import Settings


def test_hallucination_candidates_do_not_reach_transcript():
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


def test_high_no_speech_score_does_not_discard_confident_speech():
    candidate = SimpleNamespace(
        text="因为这个主导技术还没有完全定型", start=0, end=3.4,
        no_speech_prob=0.837, avg_logprob=-0.208, compression_ratio=1.053,
    )
    engine = Transcriber()
    engine.model = SimpleNamespace(transcribe=lambda *a, **k: (iter([candidate]), None))

    result = engine.transcribe(AudioJob(np.zeros(4 * RATE), 104, "system"), "zh", "")

    assert result == [{"start": 104, "end": 107.4, "text": candidate.text, "source": "system"}]


def test_adjacent_decoder_fragments_are_returned_as_one_utterance():
    candidates = [
        SimpleNamespace(text="这位师长叫刘元璋", start=0, end=3,
                        no_speech_prob=0.05, avg_logprob=-0.3, compression_ratio=0.8),
        SimpleNamespace(text="是个", start=3, end=4,
                        no_speech_prob=0.05, avg_logprob=-0.3, compression_ratio=0.8),
    ]
    engine = Transcriber()
    engine.model = SimpleNamespace(transcribe=lambda *a, **k: (iter(candidates), None))

    result = engine.transcribe(AudioJob(np.zeros(4 * RATE), 10, "system"), "zh", "")

    assert result == [{"start": 10, "end": 14, "text": "这位师长叫刘元璋是个", "source": "system"}]


def test_complete_decoder_sentence_starts_a_new_utterance():
    candidates = [
        SimpleNamespace(text="第一句说完了。", start=0, end=2,
                        no_speech_prob=0.05, avg_logprob=-0.3, compression_ratio=0.8),
        SimpleNamespace(text="第二句开始。", start=2.1, end=4,
                        no_speech_prob=0.05, avg_logprob=-0.3, compression_ratio=0.8),
    ]
    engine = Transcriber()
    engine.model = SimpleNamespace(transcribe=lambda *a, **k: (iter(candidates), None))

    result = engine.transcribe(AudioJob(np.zeros(4 * RATE), 0, "system"), "zh", "")

    assert [item["text"] for item in result] == ["第一句说完了。", "第二句开始。"]


class Closable:
    def __init__(self, order, name):
        self.order, self.name = order, name

    def close(self):
        self.order.append(f"close:{self.name}")


async def test_prepare_replaces_loaded_engine_with_fixed_medium_cpu(monkeypatch):
    order = []
    engine = Transcriber()
    engine.model = Closable(order, "old")
    engine.signature = ("whisper", "small")

    def load(name, model_path):
        order.append("load:whisper-medium")
        assert name == "medium"
        assert model_path == engine.model_dir
        return Closable(order, "medium")

    monkeypatch.setattr(engine, "_load_whisper", load)
    monkeypatch.setattr(engine, "_resolve_model_dir", lambda name="medium": engine.model_dir)
    await engine.prepare()

    assert order == ["close:old", "load:whisper-medium"]
    assert engine.status == "ready"
    assert engine.signature[0:2] == ("whisper", "medium")


async def test_model_load_failure_reports_incomplete_model_files(monkeypatch):
    engine = Transcriber()

    def load(_name, _model_path):
        raise FileNotFoundError("model.bin")

    monkeypatch.setattr(engine, "_load_whisper", load)
    monkeypatch.setattr(engine, "_resolve_model_dir", lambda name="medium": engine.model_dir)
    try:
        await engine.prepare()
    except ValueError as exc:
        assert "模型文件完整" in str(exc)
    else:
        raise AssertionError("prepare must report the missing local model")
    assert engine.status == "error"


async def test_download_uses_a_pinned_medium_snapshot_and_reports_installed(monkeypatch):
    import meeting_assistant.asr as asr_module

    engine = Transcriber()
    engine.status = "missing"
    calls = []
    downloaded = False

    def fake_download(name):
        nonlocal downloaded
        assert name == "medium"
        calls.append("download")
        downloaded = True

    monkeypatch.setattr(engine, "_resolve_model_dir", lambda name="medium": engine._model_path(name) if downloaded else None)
    monkeypatch.setattr(engine, "_complete_model", lambda _path, name="medium": True)
    monkeypatch.setattr(engine, "_download_model_files", fake_download)

    await engine.download()

    assert calls == ["download"]
    assert engine.status == "installed"
    assert engine.model_info()["installed"] is True
    assert engine.model_info()["progress"] == 100
    assert asr_module.MODEL_REVISION == "08e178d48790749d25932bbc082711ddcfdfbc4f"


async def test_base_and_small_models_can_be_selected_and_reported_independently(monkeypatch):
    engine = Transcriber()
    paths = {name: engine._model_path(name) for name in ("base", "small", "medium")}
    loaded = []
    monkeypatch.setattr(engine, "_resolve_model_dir", lambda name="medium": paths[name])
    monkeypatch.setattr(engine, "_load_whisper", lambda name, path: loaded.append((name, path)) or Closable([], name))

    for name in ("base", "small"):
        await engine.prepare(Settings(asr_model=name))
        assert engine.signature == ("whisper", name, str(paths[name]))
        assert loaded[-1] == (name, paths[name])
        assert engine.model_info(name)["installed"] is True
    assert {model["id"] for model in engine.model_info("small")["models"]} == {"base", "small", "medium"}


async def test_prepare_explains_that_model_must_be_downloaded(monkeypatch):
    engine = Transcriber()
    engine.status = "missing"
    monkeypatch.setattr(engine, "_resolve_model_dir", lambda name="medium": None)

    try:
        await engine.prepare()
    except ValueError as exc:
        assert "设置中下载 Whisper Medium" in str(exc)
    else:
        raise AssertionError("prepare must not download the model implicitly")
