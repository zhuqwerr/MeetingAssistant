from __future__ import annotations

import ctypes
from contextlib import contextmanager, ExitStack
from math import gcd
import sys
import threading
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from scipy.signal import resample_poly

RATE = 16000


@dataclass
class AudioJob:
    samples: np.ndarray
    start: float
    source: str


class Segmenter:
    """End on a pause, but cap continuous speech at four seconds.

    The low energy gate only bounds chunks; Whisper's Silero VAD performs speech
    filtering before inference. No dependency on an end-of-utterance pause.
    """
    def __init__(self, source: str, offset: float = 0, max_seconds: float = 4):
        self.source, self.offset = source, offset
        self.max_samples = int(max_seconds * RATE)
        self.position = 0
        self.frames: list[np.ndarray] = []
        self.size = 0
        self.silent = 0
        self.start = 0
        self.has_voice = False

    def feed(self, samples: np.ndarray) -> list[AudioJob]:
        if not self.frames:
            self.start = self.position
        self.position += len(samples)
        self.frames.append(samples)
        self.size += len(samples)
        voice = float(np.sqrt(np.mean(samples * samples))) > 0.002
        self.has_voice |= voice
        self.silent = 0 if voice else self.silent + len(samples)
        if self.size >= self.max_samples or (self.silent >= int(0.6 * RATE) and self.size >= RATE):
            job = self.flush()
            return [job] if job is not None else []
        return []

    def flush(self) -> AudioJob | None:
        job = None
        if self.frames and self.has_voice and self.size >= int(0.2 * RATE):
            job = AudioJob(np.concatenate(self.frames), self.offset + self.start / RATE, self.source)
        self.frames, self.size, self.silent, self.has_voice = [], 0, 0, False
        return job


def devices():
    import sounddevice as sd
    result = {"microphones": [], "speakers": [], "system_supported": sys.platform == "win32"}
    for index, device in enumerate(sd.query_devices()):
        if device["max_input_channels"] > 0:
            host = sd.query_hostapis(device["hostapi"])["name"]
            result["microphones"].append({"id": str(index), "name": f"{device['name']} · {host}"})
    if sys.platform == "win32":
        with soundcard_context() as sc:
            result["speakers"] = [{"id": s.id, "name": s.name} for s in sc.all_speakers()]
    return result


@contextmanager
def soundcard_context():
    # SoundCard initializes COM only in its importing thread. Enumeration and
    # recording run on other threads and each needs its own COM apartment.
    import soundcard as sc
    ole32 = ctypes.WinDLL("ole32")
    hr = ole32.CoInitializeEx(None, 0)
    if hr not in (0, 1, -2147417850):  # RPC_E_CHANGED_MODE means already initialized as STA.
        raise OSError(f"Windows COM initialization failed: {hr}")
    owned = hr in (0, 1)
    try:
        yield sc
    finally:
        if owned:
            ole32.CoUninitialize()


class Capture:
    def __init__(self, folder: Path, source: str, microphone: str | None, speaker: str | None,
                 on_job: Callable, on_level: Callable, on_error: Callable):
        self.folder, self.source = folder, source
        self.microphone, self.speaker = microphone, speaker
        self.on_job, self.on_level, self.on_error = on_job, on_level, on_error
        self.stopped = threading.Event()
        self.threads: list[threading.Thread] = []
        self.started = time.monotonic()

    def start(self):
        self.folder.mkdir(parents=True, exist_ok=True)
        kinds = ("mic", "system") if self.source == "mixed" else (self.source,)
        for kind in kinds:
            thread = threading.Thread(target=self._read, args=(kind,), daemon=True, name=f"capture-{kind}")
            self.threads.append(thread)
            thread.start()

    def _read(self, kind: str):
        segmenter = Segmenter(kind, time.monotonic() - self.started)
        try:
            with wave.open(str(self.folder / f"{kind}.wav"), "wb") as recording:
                recording.setnchannels(1)
                recording.setsampwidth(2)
                recording.setframerate(RATE)

                def consume(native, native_rate):
                    if native is None or len(native) == 0:
                        return
                    mono = native.mean(axis=1) if native.ndim == 2 else native
                    divisor = gcd(native_rate, RATE)
                    samples = resample_poly(mono, RATE // divisor, native_rate // divisor).astype(np.float32)
                    recording.writeframes((np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes())
                    self.on_level(float(np.sqrt(np.mean(samples * samples))))
                    for job in segmenter.feed(samples):
                        self.on_job(job)

                if kind == "mic":
                    import sounddevice as sd
                    device = int(self.microphone) if self.microphone else None
                    native_rate = int(sd.query_devices(device, "input")["default_samplerate"])
                    block_size = int(native_rate * 0.2)
                    with sd.InputStream(device=device, samplerate=native_rate, channels=1, dtype="float32", blocksize=block_size) as stream:
                        while not self.stopped.is_set():
                            block, overflow = stream.read(block_size)
                            if overflow:
                                raise RuntimeError("麦克风采集发生溢出，已停止以避免无提示丢音；请关闭高负载程序后重试")
                            consume(block, native_rate)
                else:
                    if sys.platform != "win32":
                        raise RuntimeError("第一版系统声音采集仅支持 Windows")
                    with soundcard_context() as sc, ExitStack() as stack:
                        selected = self.speaker or sc.default_speaker().id
                        mic = sc.get_microphone(selected, include_loopback=True)
                        stream, last_error = None, None
                        # Bluetooth/USB endpoints can reject a forced 48 kHz rate.
                        for native_rate in (48000, 44100, 32000, 16000):
                            try:
                                stream = stack.enter_context(mic.recorder(samplerate=native_rate, blocksize=int(native_rate * 0.2)))
                                break
                            except Exception as exc:
                                last_error = exc
                        if stream is None:
                            raise RuntimeError(f"设备不支持可用采样率：{last_error}")
                        # All native channels: WASAPI single-channel capture can be corrupt.
                        while not self.stopped.is_set():
                            consume(stream.record(numframes=int(native_rate * 0.2)), native_rate)
                        consume(stream.flush(), native_rate)
        except Exception as exc:
            self.on_error(f"{'麦克风' if kind == 'mic' else '系统声音'}采集失败：{exc}")
        finally:
            remainder = segmenter.flush()
            if remainder is not None:
                self.on_job(remainder)

    def stop(self):
        self.stopped.set()

    def join(self):
        for thread in self.threads:
            thread.join(timeout=8)
        if any(thread.is_alive() for thread in self.threads):
            raise RuntimeError("音频设备未及时释放，请重启应用后再开始会议")
