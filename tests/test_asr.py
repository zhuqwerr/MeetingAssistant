from types import SimpleNamespace

import numpy as np

from meeting_assistant.asr import Transcriber
from meeting_assistant.audio import AudioJob, RATE


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
