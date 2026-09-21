from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .asr import Transcriber
from .audio import devices
from .config import ConfigStore, DATA, ROOT, SettingsUpdate
from .session import Session, StartRequest
from .storage import Storage
from .summary import Summarizer


def create_app(folder=DATA, transcriber=None, summarizer=None):
    store, config = Storage(folder), ConfigStore(folder)
    asr, summary = transcriber or Transcriber(), summarizer or Summarizer()
    sessions: dict[str, Session] = {}
    pending: set[asyncio.Task] = set()
    start_lock = asyncio.Lock()

    def spawn(coro):
        task = asyncio.create_task(coro)
        pending.add(task)
        def finished(t):
            pending.discard(t)
            if not t.cancelled():
                t.exception()
        task.add_done_callback(finished)
        return task

    def active():
        return next((s for s in sessions.values() if s.status in ("starting", "recording", "stopping")), None)

    def require(mid):
        meeting = store.get(mid)
        if not meeting:
            raise HTTPException(404, "会议不存在")
        return meeting

    @asynccontextmanager
    async def lifespan(app):
        yield
        if current := active():
            current.stop()
            try:
                await asyncio.wait_for(current.done.wait(), timeout=15)
            except asyncio.TimeoutError:
                pass
        for task in list(pending):
            task.cancel()
        if hasattr(asr, "close") and not active():
            await asyncio.to_thread(asr.close)

    app = FastAPI(title="MeetingAssistant", lifespan=lifespan)
    app.state.store, app.state.sessions, app.state.config = store, sessions, config
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        # Reject browser requests from unrelated websites to this local microphone service.
        if origin:
            try:
                parsed = urlparse(origin)
                allowed = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost") and parsed.port in (8766, 5179)
            except ValueError:
                allowed = False
            if not allowed:
                return JSONResponse({"detail": "不允许的请求来源"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api") else "no-cache"
        return response

    @app.get("/api/health")
    async def health():
        return {"ok": True, "asr": {"status": asr.status, "error": asr.error, "model": asr.signature[0] if asr.signature else None},
                "active_meeting": active().id if active() else None}

    @app.get("/api/settings")
    async def get_settings():
        return config.public()

    @app.put("/api/settings")
    async def save_settings(update: SettingsUpdate):
        if active() or any(s.summary_busy for s in sessions.values()) or asr.status == "loading":
            raise HTTPException(409, "请等待当前录音、摘要或模型加载完成后再修改配置")
        config.save(update)
        return config.public()

    @app.post("/api/settings/test")
    async def test_settings(update: SettingsUpdate):
        try:
            await summary.test(update, config.key if update.api_key is None else update.api_key)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return {"ok": True}

    @app.post("/api/model/prepare")
    async def prepare():
        if active():
            raise HTTPException(409, "当前正在录音")
        if asr.status != "loading":
            spawn(asr.prepare(config.settings.model_copy()))
        return {"ok": True}

    @app.get("/api/devices")
    async def list_devices():
        try:
            return await asyncio.to_thread(devices)
        except Exception as exc:
            raise HTTPException(503, f"无法读取音频设备：{type(exc).__name__}") from None

    @app.get("/api/meetings")
    async def list_meetings():
        return store.list()

    @app.post("/api/meetings")
    async def start_meeting(body: StartRequest):
        async with start_lock:
            if active():
                raise HTTPException(409, "已有会议正在录音或收尾")
            session = Session(body, store, config, asr, summary)
            sessions[session.id] = session
            session.start()
            return {"id": session.id}

    @app.get("/api/meetings/{mid}")
    async def get_meeting(mid: str):
        return require(mid)

    @app.post("/api/meetings/{mid}/stop")
    async def stop_meeting(mid: str):
        require(mid)
        if mid in sessions:
            sessions[mid].stop()
        return {"ok": True}

    @app.post("/api/meetings/{mid}/summarize")
    async def summarize_meeting(mid: str):
        meeting = require(mid)
        if not store.segments(mid):
            raise HTTPException(400, "还没有可总结的转写内容")
        session = sessions.get(mid)
        if not session:
            session = Session(StartRequest(title=meeting["title"], source=meeting["source"], language=meeting["language"]), store, config, asr, summary, meeting_id=mid)
            session.elapsed = meeting["duration"]
            sessions[mid] = session
        if not active() or active().id != mid:
            session.settings, session.key = config.settings.model_copy(), config.key
        if session.summary_busy:
            raise HTTPException(409, "摘要正在生成中")
        spawn(session.summarize())
        return {"ok": True}

    @app.get("/api/meetings/{mid}/events")
    async def events(mid: str, request: Request, after: int = 0):
        meeting = require(mid)
        async def stream():
            cursor, summary_id = max(0, after), None
            while not await request.is_disconnected():
                session = sessions.get(mid)
                state = session.snapshot() if session else {"id": mid, "status": meeting["status"], "duration": meeting["duration"], "level": 0, "backlog": 0, "summary_busy": False, "error": "", "summary_error": ""}
                new = store.segments(mid, cursor)
                if new:
                    cursor = new[-1]["id"]
                latest = store.summary(mid)
                payload = {"state": state, "segments": new}
                if latest and latest["id"] != summary_id:
                    payload["summary"] = latest
                    summary_id = latest["id"]
                yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                await asyncio.sleep(0.5)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    @app.get("/api/meetings/{mid}/export")
    async def export(mid: str):
        meeting = require(mid)
        lines = [f"# {meeting['title']}", "", f"创建时间：{meeting['created_at']}", ""]
        if meeting["summary"]:
            content = meeting["summary"]["content"]
            lines += ["## 会议摘要", "", content["overview"], ""]
            for key, label in (("key_points", "讨论要点"), ("decisions", "已确认决策"), ("action_items", "待办事项")):
                lines += [f"### {label}", ""] + [f"- {item['text']}" for item in content[key]] + [""]
        lines += ["## 完整转写", ""]
        for segment in sorted(meeting["segments"], key=lambda s: (s["start"], s["id"])):
            seconds = int(segment["start"])
            lines += [f"**{seconds // 60:02}:{seconds % 60:02} · {'麦克风' if segment['source'] == 'mic' else '系统声音'}** {segment['text']}", ""]
        return Response("\n".join(lines), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="meeting-{mid[:8]}.md"'})

    dist = ROOT / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
