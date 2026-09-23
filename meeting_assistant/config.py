from __future__ import annotations

import base64
import ctypes
import json
import os
import sys
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

ROOT = Path(os.environ.get("MEETING_ASSISTANT_ROOT", Path(__file__).resolve().parent.parent))
DATA = Path(os.environ.get("MEETING_ASSISTANT_DATA", ROOT / "data"))
MODEL_DIR = Path(os.environ.get("MEETING_ASSISTANT_MODEL_DIR", ROOT / "models"))
FRONTEND_DIST = Path(os.environ.get("MEETING_ASSISTANT_FRONTEND", ROOT / "frontend" / "dist"))


class Settings(BaseModel):
    asr_model: Literal["base", "small", "medium"] = "medium"
    summary_url: str = Field(default="", max_length=500)
    summary_model: str = Field(default="", max_length=160)
    # Stored as seconds for the scheduler, exposed in the UI as whole minutes.
    summary_interval: int = Field(default=120, ge=60, le=3600, multiple_of=60)
    vocabulary: str = Field(default="", max_length=1500)

    @field_validator("summary_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return ""
        parsed = urlparse(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("请输入不含密钥、查询参数的 HTTP(S) 服务地址")
        if parsed.scheme == "http" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("远程 API 请使用 HTTPS；HTTP 仅限本机服务")
        return value.rstrip("/")


class SettingsUpdate(Settings):
    api_key: str | None = Field(default=None, max_length=2048)


def protect(value: bytes, decrypt: bool = False) -> bytes:
    """Windows DPAPI binds stored credentials to this user's Windows account."""
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = (ctypes.c_ubyte * len(value)).from_buffer_copy(value)
    source, target = Blob(len(value), buffer), Blob()
    fn = ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OSError("Windows 无法保护或读取 API 密钥")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


class ConfigStore:
    def __init__(self, folder: Path = DATA):
        folder.mkdir(parents=True, exist_ok=True)
        self.path = folder / "settings.json"
        self.settings = Settings()
        self.key = os.environ.get("MEETING_ASSISTANT_API_KEY", "")
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            raw = dict(saved.get("settings", {}))
            # Keep the user's valid Whisper size choice and migrate legacy engine
            # selectors. Unsupported historic model names fall back to Medium.
            if saved.get("schema_version", 0) < 2 and raw.get("summary_provider", "ollama") == "ollama":
                raw["summary_url"] = ""
                raw["summary_model"] = ""
            if raw.get("asr_model") not in ("base", "small", "medium"):
                raw["asr_model"] = "medium"
            for key in ("asr_engine", "asr_device", "summary_provider"):
                raw.pop(key, None)
            if saved.get("schema_version", 0) < 4 and "summary_interval" in raw:
                # Old releases stored second-based options. Map the old 30-second
                # default to the new 2-minute default; round custom values to the
                # nearest whole minute supported by the settings UI.
                try:
                    legacy_interval = int(raw["summary_interval"])
                except (TypeError, ValueError):
                    legacy_interval = 30
                if legacy_interval == 30:
                    raw["summary_interval"] = 120
                else:
                    rounded_interval = ((legacy_interval + 30) // 60) * 60
                    raw["summary_interval"] = min(3600, max(60, rounded_interval))
            self.settings = Settings.model_validate(raw)
            if saved.get("protected_key") and sys.platform == "win32":
                self.key = protect(base64.b64decode(saved["protected_key"]), decrypt=True).decode()

    def public(self):
        return {**self.settings.model_dump(), "has_api_key": bool(self.key),
                "summary_configured": bool(self.settings.summary_url and self.settings.summary_model)}

    def save(self, update: SettingsUpdate):
        settings = Settings.model_validate(update.model_dump())
        key = self.key if update.api_key is None else update.api_key.strip()
        encrypted = base64.b64encode(protect(key.encode())).decode() if key and sys.platform == "win32" else None
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"schema_version": 4, "settings": settings.model_dump(), "protected_key": encrypted}, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)
        self.settings, self.key = settings, key
