import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from app import database as db


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    """Har bir test uchun bo'sh, vaqtinchalik baza."""
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "sozlar.db")
    db.init_db()
    return tmp_path


OLD_SCHEMA = """
CREATE TABLE words (
    id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT NOT NULL UNIQUE,
    translation TEXT NOT NULL, example TEXT, category TEXT DEFAULT 'Umumiy',
    added_date TEXT NOT NULL, card_id INTEGER, state INTEGER, step INTEGER,
    stability REAL, difficulty REAL, due TEXT, last_review TEXT);
CREATE TABLE history (
    id INTEGER PRIMARY KEY AUTOINCREMENT, word_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL, rating TEXT NOT NULL, source TEXT NOT NULL,
    FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE);
CREATE TABLE ai_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, mode TEXT, transcript TEXT,
    word_ids TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
"""


@pytest.fixture
def old_db(tmp_path, monkeypatch):
    """v1.1 sxemali ESKI baza (pack/duration_ms yo'q): 45 ta 'Umumiy', 3 ta 'IELTS'."""
    import sqlite3
    path = tmp_path / "sozlar.db"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    for i in range(45):
        conn.execute("INSERT INTO words (word, translation, category, added_date) VALUES (?,?,?,?)",
                     (f"w{i:02d}", f"t{i}", "Umumiy", f"2026-01-01T00:{i:02d}:00+00:00"))
    for i in range(3):
        conn.execute("INSERT INTO words (word, translation, category, added_date) VALUES (?,?,?,?)",
                     (f"ielts{i}", f"t{i}", "IELTS", f"2026-02-01T00:0{i}:00+00:00"))
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    return path


@pytest.fixture(autouse=True)
def _isolate_audio(tmp_path, monkeypatch):
    """Testlarda audio zanjiri tarmoqqa chiqmasin (dictionaryapi/edge-tts/gTTS) va
    haqiqiy audio/ papkaga fayl yozmasin. Zanjir tekshiruvlari providers= bilan beriladi."""
    from app import audio_service
    monkeypatch.setattr(audio_service, "DEFAULT_PROVIDERS", [])
    monkeypatch.setattr(audio_service, "AUDIO_DIR", tmp_path / "audio")
