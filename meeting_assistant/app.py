from __future__ import annotations

import asyncio
import json
import os
import secrets
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .asr import Transcriber
from .audio import devices
from .config import ConfigStore, DATA, FRONTEND_DIST, SettingsUpdate
from .session import Session, StartRequest
from .storage import Storage
from .summary import Summarizer


def render_markdown(meeting: dict) -> str:
    lines = [f"# {meeting['title']}", "", f"创建时间：{meeting['created_at']}", ""]
    content = meeting["summary"]["content"] if meeting.get("summary") else None
    if isinstance(content, dict) and "summary" in content and "topics" in content:
        lines += ["## 即时摘要", "", content.get("summary") or "（尚无概括）", "", "### 决策结论", ""]
        decisions = [f"- {item['text']}" for item in content.get("key_points") or []]
        lines += decisions or ["（尚无明确结论）"]
        lines += ["", "## 关键要点", ""]
        topics = content.get("topics") or []
        if topics:
            for index, topic in enumerate(topics, 1):
                lines += [f"{index}. {topic['title']}"]
                lines += [f"   - {point['text']}" for point in topic.get("points") or []]
                lines.append("")
        else:
            lines += ["（尚无关键要点）", ""]
        lines += ["", "## 待办事项", ""]
        todos = content.get("todos") or []
        lines += [f"- {item['content']}（负责人：{item.get('owner') or '未明确'}，截止：{item.get('deadline') or '未明确'}）" for item in todos] or ["（尚无）"]
        lines += ["", "## AI 建议", ""]
        suggestions = content.get("suggestions") or []
        if suggestions:
            for item in suggestions:
                lines += [f"- {item['title']}", f"  原话：{item.get('quote') or '（无）'}", f"  判断：{item.get('detail') or '（无）'}"]
        else:
            lines.append("（尚无）")
        lines.append("")
    elif isinstance(content, dict) and "overview" in content:
        lines += ["## 会议摘要", "", content["overview"], ""]
        for key, label in (("decisions", "决策结论"), ("key_points", "关键要点"), ("action_items", "待办事项")):
            lines += [f"### {label}", ""] + [f"- {item['text']}" for item in content.get(key) or []] + [""]
    lines += ["## 完整转写", ""]
    for segment in sorted(meeting["segments"], key=lambda item: (item["start"], item["id"])):
        seconds = int(segment["start"])
        source = "麦克风" if segment["source"] == "mic" else "系统声音"
        lines += [f"**{seconds // 60:02}:{seconds % 60:02} · {source}** {segment['text']}", ""]
    return "\n".join(lines)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class ModelDownloadRequest(BaseModel):
    model: Literal["base", "small", "medium"] | None = None


def create_app(folder=DATA, transcriber=None, summarizer=None):
    store, config = Storage(folder), ConfigStore(folder)
    asr, summary = transcriber or Transcriber(), summarizer or Summarizer()
    sessions: dict[str, Session] = {}
    pending: set[asyncio.Task] = set()
    start_lock = asyncio.Lock()
    desktop = os.environ.get("MEETING_ASSISTANT_DESKTOP") == "1"
    desktop_token = os.environ.get("MEETING_ASSISTANT_DESKTOP_TOKEN", "")
    desktop_port = int(os.environ.get("MEETING_ASSISTANT_PORT", "0") or 0)
    try:
        dev_frontend_port = int(os.environ.get("MEETING_ASSISTANT_DEV_FRONTEND_PORT", "0") or 0) if desktop else 0
        if not 1 <= dev_frontend_port <= 65535:
            dev_frontend_port = 0
    except ValueError:
        dev_frontend_port = 0

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
    app.state.desktop = desktop
    if desktop and not desktop_token:
        raise RuntimeError("Electron desktop mode requires a per-launch access token")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        if desktop and request.url.path.startswith("/api"):
            supplied = request.cookies.get("ma_session", "")
            if not secrets.compare_digest(supplied, desktop_token):
                return JSONResponse({"detail": "桌面会话已失效，请重新打开 MeetingAssistant"}, status_code=401)
        # Reject browser requests from unrelated websites to this local microphone service.
        if origin:
            try:
                parsed = urlparse(origin)
                allowed_ports = {desktop_port, dev_frontend_port} if desktop else {8766, 5179}
                allowed = parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost") and parsed.port in allowed_ports
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
        signature = getattr(asr, "signature", None)
        return {"ok": True, "asr": {"status": asr.status, "error": asr.error, "model": signature[1] if signature else None},
                "model": asr.model_info(config.settings.asr_model),
                "active_meeting": active().id if active() else None}

    @app.post("/api/model/download")
    async def download_model(request: ModelDownloadRequest | None = None):
        model = request.model if request and request.model else config.settings.asr_model
        if active() or asr.status == "loading":
            raise HTTPException(409, "请在会议结束后下载或准备语音模型")
        info = asr.model_info(model)
        if info["installed"]:
            return {"ok": True}
        if asr.status == "downloading":
            if getattr(asr, "operation_model", model) != model:
                raise HTTPException(409, "另一个语音模型正在下载，请等待完成后再试")
            return {"ok": True}
        spawn(asr.download(model))
        return {"ok": True}

    @app.get("/api/settings")
    async def get_settings():
        return config.public()

    @app.put("/api/settings")
    async def save_settings(update: SettingsUpdate):
        if active() or any(s.summary_busy for s in sessions.values()) or asr.status in ("loading", "downloading"):
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
            if asr.status in ("downloading", "loading"):
                raise HTTPException(409, "请等待语音模型完成下载或加载后再开始会议")
            model = asr.model_info(config.settings.asr_model)
            if not model["installed"]:
                raise HTTPException(409, f"请先在设置中下载 {model['name']} 模型（{model['size_label']}）")
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

    @app.post("/api/meetings/{mid}/ask")
    async def ask(mid: str, body: AskRequest):
        meeting = require(mid)
        if not meeting["segments"]:
            raise HTTPException(400, "还没有可询问的转写内容")
        state = meeting["summary"]["content"] if meeting["summary"] else None
        try:
            return await summary.answer(config.settings, config.key, state, meeting["segments"], body.question)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.get("/api/meetings/{mid}/events")
    async def events(mid: str, request: Request, after: int = 0):
        meeting = require(mid)
        async def stream():
            cursor, summary_id = max(0, after), None
            while not await request.is_disconnected():
                session = sessions.get(mid)
                state = session.snapshot() if session else {"id": mid, "title": meeting["title"], "status": meeting["status"], "duration": meeting["duration"], "level": 0, "backlog": 0, "summary_busy": False, "error": "", "summary_error": ""}
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
        return Response(render_markdown(meeting), media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="meeting-{mid[:8]}.md"'})

    @app.post("/api/desktop/shutdown")
    async def desktop_shutdown():
        if not desktop:
            raise HTTPException(404, "桌面退出接口仅在 Electron 模式开放")
        current = active()
        if current:
            current.stop()
            try:
                await asyncio.wait_for(asyncio.shield(current.done.wait()), timeout=45)
            except asyncio.TimeoutError:
                # Captured WAV files are already flushed by the capture threads;
                # exit anyway rather than leaving an orphan process indefinitely.
                pass
        in_flight = [task for task in pending if task is not asyncio.current_task()]
        if in_flight:
            await asyncio.wait(in_flight, timeout=20)
        callback = getattr(app.state, "request_shutdown", None)
        if callback:
            asyncio.get_running_loop().call_later(0.25, callback)
        return {"ok": True, "graceful": current is None or current.done.is_set()}

    dist = FRONTEND_DIST
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app
