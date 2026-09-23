from __future__ import annotations

import asyncio
import io
import os
from pathlib import Path
import re
import threading

from .audio import AudioJob, RATE
from .config import MODEL_DIR, ROOT, Settings


SENTENCE_END = re.compile(r"[。！？!?…][\"'”’）】》]*$")
CJK = re.compile(r"[\u3400-\u9fff]")
MODEL_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.txt")
MODEL_CATALOG = {
    "base": {
        "name": "Whisper Base", "repo": "Systran/faster-whisper-base",
        "revision": "a80717a3a48b1b28aa687bca146cb7301feae1b1",
        "files": {"config.json": 2_309, "model.bin": 145_217_532, "tokenizer.json": 2_203_239, "vocabulary.txt": 459_861},
        "total_bytes": 147_882_941, "size_label": "约 148 MB",
    },
    "small": {
        "name": "Whisper Small", "repo": "Systran/faster-whisper-small",
        "revision": "536b0662742c02347bc0e980a01041f333bce120",
        "files": {"config.json": 2_370, "model.bin": 483_546_902, "tokenizer.json": 2_203_239, "vocabulary.txt": 459_861},
        "total_bytes": 486_212_372, "size_label": "约 486 MB",
    },
    "medium": {
        "name": "Whisper Medium", "repo": "Systran/faster-whisper-medium",
        "revision": "08e178d48790749d25932bbc082711ddcfdfbc4f",
        "files": {"config.json": 2_257, "model.bin": 1_527_906_378, "tokenizer.json": 2_203_239, "vocabulary.txt": 459_861},
        "total_bytes": 1_530_571_735, "size_label": "约 1.53 GB",
    },
}
MODEL_NAMES = tuple(MODEL_CATALOG)
MODEL_REVISION = MODEL_CATALOG["medium"]["revision"]
MODEL_TOTAL_BYTES = MODEL_CATALOG["medium"]["total_bytes"]


def _join_fragment(left: str, right: str) -> str:
    """Join adjacent decoder fragments without inserting spaces into Chinese."""
    if not left:
        return right
    if CJK.match(left[-1]) or CJK.match(right[0]) or right[0] in "，。！？；：、,.!?;:）】》”’":
        return left + right
    return left + " " + right


def _coalesce_fragments(segments: list[dict]) -> list[dict]:
    """Keep adjacent Whisper timing fragments together as readable utterances."""
    utterances: list[dict] = []
    for segment in segments:
        previous = utterances[-1] if utterances else None
        if (previous is not None
                and previous["source"] == segment["source"]
                and segment["start"] - previous["end"] <= 0.5
                and not SENTENCE_END.search(previous["text"])):
            previous["end"] = segment["end"]
            previous["text"] = _join_fragment(previous["text"], segment["text"])
        else:
            utterances.append(segment.copy())
    return utterances


class Transcriber:
    """Whisper Base, Small, or Medium running locally on CPU with CTranslate2 int8."""

    def __init__(self):
        self.model = None
        self.signature = None
        self.model_root = Path(MODEL_DIR)
        # Keep the original attribute for existing callers and the Medium cache.
        self.model_dir = self.model_root / "whisper-medium"
        self.selected_model = "medium"
        self.model_states = {
            name: {"status": "missing", "error": "", "downloaded_bytes": 0}
            for name in MODEL_NAMES
        }
        self.operation_model: str | None = None
        self.status = "installed" if self._resolve_model_dir("medium") else "missing"
        if self.status == "installed":
            self.model_states["medium"].update(status="installed", downloaded_bytes=MODEL_TOTAL_BYTES)
        self.error = ""
        self.lock = asyncio.Lock()
        self.download_lock = asyncio.Lock()
        self.decode_lock = threading.Lock()

    def _model_path(self, name: str) -> Path:
        return self.model_dir if name == "medium" else self.model_root / f"whisper-{name}"

    @staticmethod
    def _complete_model(path: Path, name: str = "medium") -> bool:
        return all((path / file_name).is_file()
                   and (path / file_name).stat().st_size == MODEL_CATALOG[name]["files"][file_name]
                   for file_name in MODEL_FILES)

    def _resolve_model_dir(self, name: str = "medium") -> Path | None:
        path = self._model_path(name)
        if self._complete_model(path, name):
            return path
        # Reuse the cache from the earlier browser-only Medium release.
        if name == "medium":
            legacy = ROOT / "models" / "models--Systran--faster-whisper-medium" / "snapshots" / MODEL_REVISION
            return legacy if self._complete_model(legacy, name) else None
        return None

    def _model_state(self, name: str) -> dict:
        spec = MODEL_CATALOG[name]
        state = self.model_states[name]
        model_dir = self._resolve_model_dir(name)
        installed = model_dir is not None
        status = state["status"]
        if installed and status not in ("loading", "ready", "downloading"):
            status = "installed"
        elif not installed and status in ("installed", "ready"):
            status = "missing"
        downloaded_bytes = spec["total_bytes"] if installed else state["downloaded_bytes"]
        return {
            "id": name,
            "name": spec["name"],
            "revision": spec["revision"],
            "status": status,
            "installed": installed,
            "progress": min(100, round(downloaded_bytes * 100 / spec["total_bytes"])) if downloaded_bytes else 0,
            "downloaded_bytes": downloaded_bytes,
            "total_bytes": spec["total_bytes"],
            "size_label": spec["size_label"],
            "error": state["error"],
        }

    def model_info(self, selected: str | None = None):
        selected = selected if selected in MODEL_CATALOG else self.selected_model
        models = [self._model_state(name) for name in MODEL_NAMES]
        chosen = next(model for model in models if model["id"] == selected)
        return {**chosen, "selected": selected, "models": models}

    def _set_model_state(self, name: str, status: str, error: str = "", downloaded_bytes: int | None = None):
        state = self.model_states[name]
        state["status"] = status
        state["error"] = error
        if downloaded_bytes is not None:
            state["downloaded_bytes"] = min(MODEL_CATALOG[name]["total_bytes"], downloaded_bytes)
        if name == self.selected_model:
            self.status, self.error = status, error

    def _set_download_progress(self, name: str, downloaded_bytes: int):
        state = self.model_states[name]
        state["downloaded_bytes"] = min(MODEL_CATALOG[name]["total_bytes"], downloaded_bytes)

    async def download(self, name: str = "medium"):
        if name not in MODEL_CATALOG:
            raise ValueError("请选择 Whisper Base、Small 或 Medium 模型")
        async with self.download_lock:
            if self._resolve_model_dir(name):
                self._set_model_state(name, "installed", downloaded_bytes=MODEL_CATALOG[name]["total_bytes"])
                if self.status == "downloading":
                    self.status = "ready" if self.model is not None else "installed"
                return
            if self.status == "loading":
                return
            previous_status = self.status
            self.operation_model = name
            self._set_model_state(name, "downloading", downloaded_bytes=0)
            self.status = "downloading"
            self.error = ""
            state = self.model_states[name]
            state["downloaded_bytes"] = sum(
                (self._model_path(name) / file_name).stat().st_size
                for file_name in MODEL_FILES
                if (self._model_path(name) / file_name).is_file()
                and (self._model_path(name) / file_name).stat().st_size == MODEL_CATALOG[name]["files"][file_name]
            )
            try:
                await asyncio.to_thread(self._download_model_files, name)
                model_dir = self._model_path(name)
                if not self._complete_model(model_dir, name):
                    raise FileNotFoundError("下载结束后模型文件仍不完整")
                self._set_model_state(name, "installed", downloaded_bytes=MODEL_CATALOG[name]["total_bytes"])
            except Exception as exc:
                message = f"{MODEL_CATALOG[name]['name']} 下载失败，可检查网络后重试。({type(exc).__name__})"
                self._set_model_state(name, "download_error", message)
            finally:
                self.operation_model = None
                if name != self.selected_model or previous_status not in ("downloading", "missing"):
                    self.status = previous_status
                    self.error = ""

    def _download_model_files(self, name: str):
        from huggingface_hub import hf_hub_download
        from tqdm import tqdm

        spec = MODEL_CATALOG[name]
        model_dir = self._model_path(name)
        model_dir.mkdir(parents=True, exist_ok=True)
        completed_bytes = 0
        for file_name in MODEL_FILES:
            destination = model_dir / file_name
            expected_size = spec["files"][file_name]
            if destination.is_file() and destination.stat().st_size == expected_size:
                completed_bytes += destination.stat().st_size
                self._set_download_progress(name, completed_bytes)
                continue
            if destination.exists():
                destination.unlink()

            owner = self

            class ProgressBar(tqdm):
                def __init__(self, *args, **kwargs):
                    kwargs.update(file=io.StringIO(), disable=False, leave=False)
                    super().__init__(*args, **kwargs)

                def update(self, n=1):
                    result = super().update(n)
                    owner._set_download_progress(name, completed_bytes + int(self.n))
                    return result

            hf_hub_download(
                repo_id=spec["repo"],
                filename=file_name,
                revision=spec["revision"],
                local_dir=str(model_dir),
                tqdm_class=ProgressBar,
            )
            if not destination.is_file() or destination.stat().st_size != expected_size:
                raise ValueError(f"模型文件大小校验失败：{file_name}")
            completed_bytes += destination.stat().st_size
            self._set_download_progress(name, completed_bytes)

    async def prepare(self, settings: Settings | None = None):
        name = getattr(settings, "asr_model", "medium") if settings else "medium"
        if name not in MODEL_CATALOG:
            name = "medium"
        self.selected_model = name
        model_dir = self._resolve_model_dir(name)
        if model_dir is None:
            self._set_model_state(name, "missing")
            raise ValueError(f"请先在设置中下载 {MODEL_CATALOG[name]['name']} 模型（{MODEL_CATALOG[name]['size_label']}），然后再开始会议。")
        signature = ("whisper", name, str(model_dir))
        async with self.lock:
            if self.model is not None and self.signature == signature:
                return
            self._set_model_state(name, "loading", error="")
            previous = self.model
            self.model, self.signature = None, None
            try:
                if hasattr(previous, "close"):
                    await asyncio.to_thread(previous.close)
                model = await asyncio.to_thread(self._load_whisper, name, model_dir)
                self.model, self.signature = model, signature
                self._set_model_state(name, "ready")
            except Exception as exc:
                message = f"{MODEL_CATALOG[name]['name']} 加载失败，请确认模型文件完整。({type(exc).__name__})"
                self._set_model_state(name, "error", message)
                raise ValueError(message) from exc

    def _load_whisper(self, name: str, model_path: Path):
        from faster_whisper import WhisperModel

        if not self._complete_model(model_path, name):
            raise FileNotFoundError(f"Whisper {name} model is incomplete: {model_path}")
        return WhisperModel(
            str(model_path), device="cpu", compute_type="int8",
            cpu_threads=min(8, max(2, (os.cpu_count() or 4) // 2)),
            local_files_only=True,
        )

    def close(self):
        if hasattr(self.model, "close"):
            self.model.close()
        self.model = None
        self.signature = None
        self.status = "installed" if self._resolve_model_dir(self.selected_model) else "missing"

    def transcribe(self, job: AudioJob, language: str, vocabulary: str):
        with self.decode_lock:
            segments, _ = self.model.transcribe(
                job.samples, language=None if language == "auto" else language,
                beam_size=3, temperature=0.0, vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300},
                condition_on_previous_text=False, initial_prompt=vocabulary or None,
            )
            result = []
            for item in segments:
                text = item.text.strip()
                end = min(item.end, len(job.samples) / RATE)
                duration = end - item.start
                # A high no-speech score alone can discard clear speech. Use it
                # only together with an unlikely decode.
                likely_silence = item.no_speech_prob >= 0.6 and item.avg_logprob < -1.0
                if (not text or duration <= 0 or likely_silence
                        or item.avg_logprob < -1.5 or item.compression_ratio > 2.4):
                    continue
                han_count = len(re.findall(r"[\u3400-\u9fff]", text))
                if han_count >= 12 and han_count / duration > 12:
                    continue
                result.append({"start": job.start + item.start, "end": job.start + end,
                               "text": text, "source": job.source})
            return _coalesce_fragments(result)
