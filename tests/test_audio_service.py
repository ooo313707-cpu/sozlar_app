"""audio_service.py sinovlari (CLAUDE-3): zanjir, baza, auto_play, migratsiya v4."""
from pathlib import Path

import pytest

from app import audio_service as au
from app import database as db


def _writer(name, calls, *, ok=True, raises=False, write=True):
    """Soxta provayder: chaqirilganini yozib qo'yadi, istasa fayl yaratadi."""
    def func(word, dest):
        calls.append(name)
        if raises:
            raise RuntimeError("tarmoq yo'q")
        if write:
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            Path(dest).write_bytes(b"AUDIO")
        return ok
    return func


def _chain(calls, **kw):
    return [
        ("dictionary", au.SOURCE_HUMAN, _writer("dictionary", calls, **kw.get("dictionary", {}))),
        ("edge", au.SOURCE_TTS, _writer("edge", calls, **kw.get("edge", {}))),
        ("gtts", au.SOURCE_TTS, _writer("gtts", calls, **kw.get("gtts", {}))),
    ]


def test_local_file_wins_and_no_network(tmp_path):
    (tmp_path / "apple.mp3").write_bytes(b"x")
    calls = []
    r = au.resolve_audio("Apple", audio_dir=tmp_path, providers=_chain(calls))
    assert (r.source, r.provider, r.path) == ("human", "local", tmp_path / "apple.mp3")
    assert calls == []


def test_dictionary_is_human(tmp_path):
    calls = []
    r = au.resolve_audio("apple", audio_dir=tmp_path, providers=_chain(calls))
    assert (r.source, r.provider) == ("human", "dictionary")
    assert calls == ["dictionary"]


def test_falls_back_to_edge_then_gtts(tmp_path):
    calls = []
    r = au.resolve_audio("apple", audio_dir=tmp_path,
                         providers=_chain(calls, dictionary={"ok": False, "write": False}))
    assert (r.source, r.provider) == ("tts", "edge")
    calls.clear()
    r = au.resolve_audio("pear", audio_dir=tmp_path,
                         providers=_chain(calls, dictionary={"raises": True}, edge={"raises": True}))
    assert (r.source, r.provider) == ("tts", "gtts")
    assert calls == ["dictionary", "edge", "gtts"]


def test_all_fail_uses_qt_tts(tmp_path):
    calls = []
    r = au.resolve_audio("apple", audio_dir=tmp_path, providers=_chain(
        calls, dictionary={"raises": True}, edge={"ok": False, "write": False}, gtts={"raises": True}))
    assert r.use_qt_tts and r.path is None and r.source == "none" and r.provider == "qt"
    assert calls == ["dictionary", "edge", "gtts"]


def test_provider_true_but_no_file_is_failure(tmp_path):
    calls = []
    r = au.resolve_audio("apple", audio_dir=tmp_path, providers=_chain(
        calls, dictionary={"ok": True, "write": False}))
    assert r.provider == "edge"


def test_safe_filename_and_marker():
    assert au.safe_filename("Look up!") == "look_up"
    assert au.safe_filename("../../etc") == "etc"
    assert au.safe_filename("???") == "word"
    assert au.audio_marker("human") == "👤"
    assert au.audio_marker("tts") == "🤖"
    assert au.audio_marker("none") == "" and au.audio_marker(None) == ""


def test_ensure_word_audio_saves_to_db(fresh_db, tmp_path):
    wid = db.add_word("apple", "olma")
    calls = []
    r = au.ensure_word_audio(wid, audio_dir=tmp_path / "a", providers=_chain(calls))
    assert r.source == "human"
    source, path = au.get_word_audio(wid)
    assert source == "human" and Path(path).exists()


def test_ensure_word_audio_reuses_stored_file(fresh_db, tmp_path):
    wid = db.add_word("apple", "olma")
    f = tmp_path / "apple.mp3"
    f.write_bytes(b"x")
    au.save_word_audio(wid, "tts", f)
    calls = []
    r = au.ensure_word_audio(wid, audio_dir=tmp_path, providers=_chain(calls))
    assert (r.source, r.provider) == ("tts", "local")   # 🤖 belgisi o'zgarmaydi
    assert calls == []


def test_ensure_word_audio_qt_does_not_write_db(fresh_db, tmp_path):
    wid = db.add_word("apple", "olma")
    calls = []
    r = au.ensure_word_audio(wid, audio_dir=tmp_path, providers=_chain(
        calls, dictionary={"raises": True}, edge={"raises": True}, gtts={"raises": True}))
    assert r.use_qt_tts
    assert au.get_word_audio(wid) == ("none", None)


def test_ensure_word_audio_missing_word(fresh_db):
    r = au.ensure_word_audio(99999, providers=[])
    assert r.source == "none" and r.path is None


def test_save_word_audio_rejects_bad_source(fresh_db):
    wid = db.add_word("apple", "olma")
    with pytest.raises(ValueError):
        au.save_word_audio(wid, "robot", None)


def test_auto_play_default_on_and_toggle(fresh_db):
    assert au.is_auto_play() is True
    db.set_setting("auto_play", "0")
    assert au.is_auto_play() is False
    db.set_setting("auto_play", "1")
    assert au.is_auto_play() is True


def test_migration_v4_on_old_db(old_db):
    db.init_db()
    conn = db.get_connection()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 4
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(words)")]
        assert "audio_source" in cols and "audio_path" in cols
        assert conn.execute("SELECT COUNT(*) FROM import_queue").fetchone()[0] == 0
        row = conn.execute("SELECT audio_source, audio_path FROM words LIMIT 1").fetchone()
        assert row["audio_source"] in ("none", None) and row["audio_path"] is None
        # eski so'zlar spell_status=confirmed bo'lib qoladi (Claude 2 migratsiyasi buzilmagan)
        assert conn.execute("SELECT COUNT(*) FROM words WHERE spell_status != 'confirmed'").fetchone()[0] == 0
    finally:
        conn.close()
    db.init_db()   # qayta chaqirish xavfsiz
