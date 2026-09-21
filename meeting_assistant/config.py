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

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("MEETING_ASSISTANT_DATA", ROOT / "data"))


def cuda_runtime_installed() -> bool:
    """True when the Windows CUDA wheels are installed. Does not load native libraries."""
    if sys.platform != "win32":
        return False
    for root in sys.path:
        vendor = Path(root) / "nvidia"
        if not vendor.is_dir():
            continue
        names = {path.parent.name.lower() for path in vendor.glob("*/bin")}
        if any(name.startswith("cublas") for name in names) and any("cudnn" in name for name in names):
            return True
    return False


def preferred_asr() -> tuple[str, str]:
    # large-v3-turbo on CPU is slower than realtime, so it is only the default with CUDA.
    if cuda_runtime_installed():
        return "large-v3-turbo", "cuda"
    return "small", "cpu"


class Settings(BaseModel):
    asr_model: Literal["tiny", "base", "small", "medium", "large-v3-turbo"] = "small"
    asr_device: Literal["cpu", "cuda"] = "cpu"
    summary_provider: Literal["ollama", "compatible"] = "ollama"
    summary_url: str = "http://127.0.0.1:11434"
    summary_model: str = Field(default="qwen3:4b", min_length=1, max_length=160)
    summary_interval: int = Field(default=30, ge=10, le=300)
    vocabulary: str = Field(default="", max_length=1500)

    @field_validator("summary_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
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
        model, device = preferred_asr()
        self.settings = Settings(asr_model=model, asr_device=device)
        self.key = os.environ.get("MEETING_ASSISTANT_API_KEY", "")
        if self.path.exists():
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            self.settings = Settings.model_validate(saved["settings"])
            if saved.get("protected_key") and sys.platform == "win32":
                self.key = protect(base64.b64decode(saved["protected_key"]), decrypt=True).decode()

    def public(self):
        return {**self.settings.model_dump(), "has_api_key": bool(self.key)}

    def save(self, update: SettingsUpdate):
        settings = Settings.model_validate(update.model_dump())
        key = self.key if update.api_key is None else update.api_key.strip()
        encrypted = base64.b64encode(protect(key.encode())).decode() if key and sys.platform == "win32" else None
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps({"settings": settings.model_dump(), "protected_key": encrypted}, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)
        self.settings, self.key = settings, key
