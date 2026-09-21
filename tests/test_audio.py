import numpy as np

from meeting_assistant.audio import RATE, Segmenter


def test_uninterrupted_speech_is_bounded_and_stop_flushes_tail():
    segmenter = Segmenter("mic")
    jobs = []
    # 9.4 seconds of continuous signal must not wait indefinitely for silence.
    frame = np.ones(3200, dtype=np.float32) * 0.1
    for _ in range(47):
        jobs.extend(segmenter.feed(frame))
    jobs.append(segmenter.flush())
    assert len(jobs) == 3
    assert [round(j.start, 1) for j in jobs] == [0, 4, 8]
    assert sum(len(j.samples) for j in jobs) == 47 * len(frame)
    assert all(len(j.samples) <= RATE * 4 for j in jobs)


def test_silence_does_not_enqueue_hallucination_prone_segments():
    segmenter = Segmenter("system")
    for _ in range(100):
        assert segmenter.feed(np.zeros(3200, dtype=np.float32)) == []
    assert segmenter.flush() is None


def test_pause_emits_speech_with_correct_offset():
    segmenter = Segmenter("mic", offset=2)
    jobs = []
    for _ in range(3):
        jobs += segmenter.feed(np.full(3200, 0.1, dtype=np.float32))
    for _ in range(3):
        jobs += segmenter.feed(np.zeros(3200, dtype=np.float32))
    assert len(jobs) == 1
    assert jobs[0].start == 2
    assert len(jobs[0].samples) == 19200
