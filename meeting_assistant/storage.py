from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .config import DATA


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Storage:
    def __init__(self, folder: Path = DATA):
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self.lock = threading.RLock()
        self.db = sqlite3.connect(folder / "meetings.sqlite3", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS meetings (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at TEXT NOT NULL,
                status TEXT NOT NULL, source TEXT NOT NULL, language TEXT NOT NULL, duration REAL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id TEXT NOT NULL REFERENCES meetings(id),
                start REAL NOT NULL, end REAL NOT NULL, text TEXT NOT NULL, source TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS segments_meeting ON segments(meeting_id, id);
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, meeting_id TEXT NOT NULL REFERENCES meetings(id),
                through_id INTEGER NOT NULL, created_at TEXT NOT NULL, content TEXT NOT NULL
            );
        """)
        # A terminated process cannot keep recording. Preserve its recovered text/audio.
        self.db.execute("UPDATE meetings SET status='interrupted' WHERE status IN ('recording','starting','stopping')")
        self.db.commit()

    def create(self, title: str, source: str, language: str) -> str:
        mid = uuid4().hex
        with self.lock, self.db:
            self.db.execute("INSERT INTO meetings(id,title,created_at,status,source,language) VALUES(?,?,?,?,?,?)", (mid, title, now(), "starting", source, language))
        return mid

    def update(self, mid: str, status: str, duration: float):
        with self.lock, self.db:
            self.db.execute("UPDATE meetings SET status=?,duration=? WHERE id=?", (status, duration, mid))

    def update_title(self, mid: str, title: str):
        with self.lock, self.db:
            self.db.execute("UPDATE meetings SET title=? WHERE id=?", (title, mid))

    def add_segment(self, mid: str, start: float, end: float, text: str, source: str):
        with self.lock, self.db:
            cursor = self.db.execute("INSERT INTO segments(meeting_id,start,end,text,source) VALUES(?,?,?,?,?)", (mid, start, end, text, source))
            return {"id": cursor.lastrowid, "meeting_id": mid, "start": start, "end": end, "text": text, "source": source}

    def segments(self, mid: str, after: int = 0):
        with self.lock:
            return [dict(r) for r in self.db.execute("SELECT * FROM segments WHERE meeting_id=? AND id>? ORDER BY id", (mid, after))]

    def summary(self, mid: str):
        with self.lock:
            row = self.db.execute("SELECT * FROM summaries WHERE meeting_id=? ORDER BY id DESC LIMIT 1", (mid,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["content"] = json.loads(result["content"])
            return result

    def add_summary(self, mid: str, through: int, content: dict):
        with self.lock, self.db:
            self.db.execute("INSERT INTO summaries(meeting_id,through_id,created_at,content) VALUES(?,?,?,?)", (mid, through, now(), json.dumps(content, ensure_ascii=False)))
        return self.summary(mid)

    def list(self):
        with self.lock:
            rows = self.db.execute("""
                SELECT m.*, s.id AS summary_id, s.through_id AS summary_through_id,
                       s.created_at AS summary_created_at, s.content AS summary_content
                FROM meetings m
                LEFT JOIN summaries s ON s.id = (
                    SELECT id FROM summaries WHERE meeting_id=m.id ORDER BY id DESC LIMIT 1
                )
                ORDER BY m.created_at DESC LIMIT 100
            """).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            summary_id = item.pop("summary_id")
            summary_through_id = item.pop("summary_through_id")
            summary_created_at = item.pop("summary_created_at")
            summary_content = item.pop("summary_content")
            item["summary"] = None if summary_id is None else {
                "id": summary_id,
                "through_id": summary_through_id,
                "created_at": summary_created_at,
                "content": json.loads(summary_content),
            }
            result.append(item)
        return result

    def get(self, mid: str):
        with self.lock:
            row = self.db.execute("SELECT * FROM meetings WHERE id=?", (mid,)).fetchone()
        if not row:
            return None
        return {**dict(row), "segments": self.segments(mid), "summary": self.summary(mid)}
