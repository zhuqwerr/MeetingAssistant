from __future__ import annotations

import asyncio
import time
from typing import Literal

from pydantic import BaseModel, Field

from .asr import Transcriber
from .audio import Capture
from .config import ConfigStore
from .storage import Storage
from .summary import RECONCILE_SECONDS, TRIGGER_DELAY, mentions_key_event, reconcile_windows, Summarizer


class StartRequest(BaseModel):
    title: str = Field(default="未命名会议", min_length=1, max_length=120)
    source: Literal["mic", "system", "mixed"] = "mic"
    language: Literal["zh", "en", "auto"] = "zh"
    microphone: str | None = None
    speaker: str | None = None


class Session:
    def __init__(self, request: StartRequest, store: Storage, config: ConfigStore, asr: Transcriber, summarizer: Summarizer, meeting_id: str | None = None):
        self.request, self.store, self.config = request, store, config
        self.settings = config.settings.model_copy()
        self.key = config.key
        self.asr, self.summarizer = asr, summarizer
        self.id = meeting_id or store.create(request.title.strip() or "未命名会议", request.source, request.language)
        self.status = "ended" if meeting_id else "starting"
        self.error = ""
        self.summary_error = ""
        self.summary_busy = False
        self.level = 0.0
        self.elapsed = 0.0
        self.started: float | None = None
        self.jobs: asyncio.Queue = asyncio.Queue(maxsize=60)
        self.stop_requested = asyncio.Event()
        self.summary_lock = asyncio.Lock()
        self._early_task: asyncio.Task | None = None
        self.capture: Capture | None = None
        self.done = asyncio.Event()
        self.run_task: asyncio.Task | None = None

    def snapshot(self):
        return {"id": self.id, "status": self.status, "error": self.error, "summary_error": self.summary_error,
                "summary_busy": self.summary_busy, "level": self.level,
                "duration": time.monotonic() - self.started if self.started and self.status == "recording" else self.elapsed,
                "backlog": self.jobs.qsize()}

    def start(self):
        self.run_task = asyncio.create_task(self.run())

    def offer(self, job):
        if self.status not in ("recording", "stopping"):
            return
        try:
            self.jobs.put_nowait(job)
        except asyncio.QueueFull:
            self.fail("本机识别速度跟不上录音，已停止采集。原始音频已保存在 data/recordings，可换较小模型后重新处理。")

    def fail(self, message):
        if not self.error:
            self.error = message
        self.stop()

    def stop(self):
        if self.status in ("starting", "recording"):
            if self.started:
                self.elapsed = time.monotonic() - self.started
            self.status = "stopping"
            self.store.update(self.id, self.status, self.elapsed)
            self.stop_requested.set()
            if self.capture:
                self.capture.stop()

    async def run(self):
        worker = ticker = None
        loop = asyncio.get_running_loop()
        try:
            # Model download/loading happens before capture, so startup cannot lose speech.
            prepare = asyncio.create_task(self.asr.prepare(self.settings))
            stopped = asyncio.create_task(self.stop_requested.wait())
            finished, _ = await asyncio.wait([prepare, stopped], return_when=asyncio.FIRST_COMPLETED)
            if stopped in finished and not prepare.done():
                # Loading in a thread cannot be interrupted safely. Consume its exception.
                prepare.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
                return
            stopped.cancel()
            await prepare
            if self.stop_requested.is_set():
                return
            self.status, self.started = "recording", time.monotonic()
            self.store.update(self.id, self.status, 0)
            self.capture = Capture(
                self.store.folder / "recordings" / self.id, self.request.source, self.request.microphone, self.request.speaker,
                lambda job: loop.call_soon_threadsafe(self.offer, job),
                lambda level: loop.call_soon_threadsafe(self.set_level, level),
                lambda message: loop.call_soon_threadsafe(self.fail, message),
            )
            worker = asyncio.create_task(self.consume())
            ticker = asyncio.create_task(self.summary_loop())
            self.capture.start()
            await self.stop_requested.wait()
            await asyncio.to_thread(self.capture.join)
            # All capture threads have delivered their last partial chunks.
            await asyncio.sleep(0)
            await self.jobs.put(None)
            await worker
            if ticker:
                await ticker
            await self.summarize()
        except Exception as exc:
            self.error = self.error or str(exc)
        finally:
            self.stop_requested.set()
            if self.capture:
                self.capture.stop()
            for task in (worker, ticker, self._early_task):
                if task and not task.done():
                    task.cancel()
            self.level = 0
            self.status = "error" if self.error else "ended"
            self.store.update(self.id, self.status, self.elapsed)
            self.done.set()

    def set_level(self, value):
        self.level = value

    async def consume(self):
        while True:
            job = await self.jobs.get()
            try:
                if job is None:
                    return
                results = await asyncio.to_thread(self.asr.transcribe, job, self.request.language, self.settings.vocabulary)
                for result in results:
                    saved = self.store.add_segment(self.id, **result)
                    if mentions_key_event(saved["text"]):
                        self.arm_early_summary()
            except Exception as exc:
                self.fail(f"转写失败：{type(exc).__name__}。音频已保留，请检查模型和运行环境。")
            finally:
                self.jobs.task_done()

    def arm_early_summary(self):
        if self._early_task and not self._early_task.done():
            return
        self._early_task = asyncio.create_task(self._early_summary())

    async def _early_summary(self):
        try:
            await asyncio.wait_for(self.stop_requested.wait(), TRIGGER_DELAY)
        except asyncio.TimeoutError:
            await self.summarize()

    async def summary_loop(self):
        next_reconcile = time.monotonic() + RECONCILE_SECONDS
        while not self.stop_requested.is_set():
            timeout = min(self.settings.summary_interval, max(0.05, next_reconcile - time.monotonic()))
            try:
                await asyncio.wait_for(self.stop_requested.wait(), timeout)
            except asyncio.TimeoutError:
                if time.monotonic() >= next_reconcile - 0.01:
                    await self.summarize(mode="reconcile")
                    next_reconcile = time.monotonic() + RECONCILE_SECONDS
                else:
                    await self.summarize()

    async def summarize(self, mode: str = "incremental"):
        async with self.summary_lock:
            previous = self.store.summary(self.id)
            # Freeze the input frontier. Audio arriving during an LLM request must
            # remain pending until a later call, never be skipped by its cursor.
            everything = self.store.segments(self.id)
            through = previous["through_id"] if previous else 0
            remaining = [segment for segment in everything if segment["id"] > through]
            if not everything or (mode == "incremental" and not remaining):
                return
            self.summary_busy, self.summary_error = True, ""
            try:
                valid_ids = {segment["id"] for segment in everything}
                # Recover the full backlog before windowed reconciliation. Each
                # successful batch is durable, so a failure retries from that point.
                previous = await self._update_incrementally(previous, remaining, valid_ids)
                if mode == "reconcile":
                    recent, cited = reconcile_windows(everything, previous["content"] if previous else None)
                    content = await self.summarizer.generate(
                        self.settings, self.key, previous["content"] if previous else None,
                        recent, valid_ids, mode="reconcile", cited_segments=cited,
                    )
                    self.store.add_summary(self.id, everything[-1]["id"], content)
            except Exception as exc:
                self.summary_error = str(exc)
            finally:
                self.summary_busy = False

    async def _update_incrementally(self, previous, remaining, valid_ids):
        while remaining:
            batch, chars = [], 0
            for segment in remaining:
                if batch and chars + len(segment["text"]) > 6500:
                    break
                batch.append(segment)
                chars += len(segment["text"])
            content = await self.summarizer.generate(
                self.settings, self.key, previous["content"] if previous else None, batch, valid_ids,
            )
            previous = self.store.add_summary(self.id, batch[-1]["id"], content)
            remaining = remaining[len(batch):]
        return previous
