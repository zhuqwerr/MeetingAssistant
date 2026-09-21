from __future__ import annotations

import asyncio
import os
import threading
import re
from pathlib import Path
import sys

from .audio import AudioJob, RATE
from .config import ROOT, Settings


class Transcriber:
    def __init__(self):
        self.model = None
        self.signature = None
        self.status = "idle"
        self.error = ""
        self.lock = asyncio.Lock()
        self.decode_lock = threading.Lock()
        self.dll_handles = []

    async def prepare(self, settings: Settings):
        signature = (settings.asr_model, settings.asr_device)
        async with self.lock:
            if self.model is not None and self.signature == signature:
                return
            self.status, self.error = "loading", ""
            try:
                model = await asyncio.to_thread(self._load, settings)
                if hasattr(self.model, "close"):
                    await asyncio.to_thread(self.model.close)
                self.model, self.signature, self.status = model, signature, "ready"
            except Exception as exc:
                self.status = "error"
                self.error = f"语音模型加载失败，请检查网络或本地模型缓存。{type(exc).__name__}"
                raise ValueError(self.error) from exc

    def _load(self, settings):
        if settings.asr_device == "cuda" and sys.platform == "win32":
            from .gpu_worker import GPUModel
            return GPUModel(settings)
        return self._load_native(settings)

    def close(self):
        if hasattr(self.model, "close"):
            self.model.close()
        self.model = None
        self.signature = None
        self.status = "idle"

    def _load_native(self, settings):
        if settings.asr_device == "cuda" and sys.platform == "win32":
            # NVIDIA's optional pip wheels keep DLLs outside the system PATH.
            # Keep handles alive for the model's entire lifetime.
            for root in sys.path:
                vendor = Path(root) / "nvidia"
                if vendor.is_dir():
                    for folder in vendor.glob("*/bin"):
                        path = str(folder.resolve())
                        self.dll_handles.append(os.add_dll_directory(path))
                        # CTranslate2 uses LoadLibrary, which also needs the
                        # process PATH (this does not change Windows settings).
                        if path not in os.environ.get("PATH", "").split(os.pathsep):
                            os.environ["PATH"] = path + os.pathsep + os.environ.get("PATH", "")
        from faster_whisper import WhisperModel
        from faster_whisper.utils import download_model
        from huggingface_hub.errors import LocalEntryNotFoundError
        model_path = settings.asr_model
        try:
            cached = Path(download_model(settings.asr_model, cache_dir=str(ROOT / "models"), local_files_only=True))
            if all((cached / name).is_file() for name in ("model.bin", "config.json", "tokenizer.json")):
                model_path = str(cached)
        except LocalEntryNotFoundError:
            pass
        return WhisperModel(model_path, device=settings.asr_device,
                            compute_type="int8" if settings.asr_device == "cpu" else "float16",
                            cpu_threads=min(8, max(2, (os.cpu_count() or 4) // 2)),
                            download_root=str(ROOT / "models"))

    def transcribe(self, job: AudioJob, language: str, vocabulary: str):
        with self.decode_lock:
            segments, _ = self.model.transcribe(
                job.samples, language=None if language == "auto" else language,
                beam_size=3, temperature=0.0, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
                condition_on_previous_text=False,
                initial_prompt=vocabulary or None,
            )
            result = []
            for item in segments:
                text = item.text.strip()
                end = min(item.end, len(job.samples) / RATE)
                duration = end - item.start
                # Decoder fallback thresholds are not acceptance checks: Whisper can
                # emit confident boilerplate even with a high no-speech probability.
                if (not text or duration <= 0 or item.no_speech_prob >= 0.6
                        or item.avg_logprob < -1.5 or item.compression_ratio > 2.4):
                    continue
                # Chinese characters approximate syllables. An implausibly dense
                # caption is suspect regardless of its topic; never ban phrases.
                han_count = len(re.findall(r"[\u3400-\u9fff]", text))
                if han_count >= 12 and han_count / duration > 12:
                    continue
                result.append({"start": job.start + item.start, "end": job.start + end, "text": text, "source": job.source})
            return result
