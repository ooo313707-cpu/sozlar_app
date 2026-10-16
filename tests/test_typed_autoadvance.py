"""Yozish rejimi: javob -> talaffuz -> 6.5 s -> keyingi karta; Enter darrov o'tkazmaydi."""
import time

import pytest

pytest.importorskip("PySide6")
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import audio_service as au
from app import database as db
from app.ui import flashcard as fc


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _AI:
    def discard_prefetch(self): pass
    def prefetch_after_cards(self, *a, **k): pass


class _MW:
    pages = {"ai_practice": _AI()}
    def show_page(self, *_): pass
    def offer_forgotten_review(self, *_): pass


@pytest.fixture
def page(qapp, fresh_db, monkeypatch, tmp_path):
    db.set_setting("typing_mode", "1")
    mp3 = tmp_path / "a.mp3"; mp3.write_bytes(b"AUDIO")
    for w in ("apple", "book", "cat"):
        wid = db.add_word(w, "t", f"Example {w}.", "Kitob")
        db.set_spell_status(wid, "ok")
    p = fc.FlashcardPage(_MW())
    played = []
    monkeypatch.setattr(p.audio_player, "play_file", lambda path: played.append(str(path)))
    monkeypatch.setattr(au, "get_word_audio", lambda wid: ("tts", str(mp3)))
    p._played = played
    p.start_session(db.get_pack_words("Kitob", 1), "Kitob", 1)
    p.show()
    qapp.processEvents()
    return p


def _answer(page, qapp, text):
    page.typing_input.setText(text)
    QTest.keyClick(page.typing_input, Qt.Key_Return)   # haqiqiy Enter bosilishi
    qapp.processEvents()


def test_enter_submits_but_does_not_skip(page, qapp):
    first = page.current_id
    _answer(page, qapp, "apple")
    assert page.answer_shown is True
    assert page.current_id == first, "Enter darrov keyingi kartaga o'tkazdi (xato)"
    assert page._played, "javob ko'rsatilgach talaffuz chalinmadi"
    assert page._auto_timer.isActive() and page._auto_timer.interval() == fc.TYPED_AUTO_ADVANCE_MS
    assert "To'g'ri" in page.typing_feedback.text()


def test_auto_advance_after_timeout(page, qapp):
    first = page.current_id
    _answer(page, qapp, "apple")
    page._on_auto_advance()            # 6.5 s o'tdi deb faraz
    qapp.processEvents()
    assert page.current_id != first
    fsrs = db.get_word(first)
    assert fsrs["last_review"] is not None and fsrs["state"] is not None   # baho yozildi


def test_timer_does_not_skip_if_user_already_moved(page, qapp):
    first = page.current_id
    _answer(page, qapp, "apple")
    time.sleep(0.35)
    QTest.keyClick(page, Qt.Key_Space)  # foydalanuvchi qo'lda o'tdi
    qapp.processEvents()
    second = page.current_id
    assert second != first
    page._on_auto_advance()             # eski taymer: yangi kartani o'tkazmasin
    assert page.current_id == second


def test_manual_enter_after_guard_advances(page, qapp):
    first = page.current_id
    _answer(page, qapp, "apple")
    time.sleep(fc.ENTER_GUARD_SECONDS + 0.1)
    QTest.keyClick(page, Qt.Key_Return)
    qapp.processEvents()
    assert page.current_id != first and not page._auto_timer.isActive()


def test_normal_mode_plays_on_show_not_on_reveal(qapp, fresh_db, monkeypatch, tmp_path):
    mp3 = tmp_path / "a.mp3"; mp3.write_bytes(b"AUDIO")
    db.set_setting("typing_mode", "0")
    db.set_setting("auto_play", "1")
    wid = db.add_word("apple", "olma", "x", "Kitob"); db.set_spell_status(wid, "ok")
    p = fc.FlashcardPage(_MW())
    played = []
    monkeypatch.setattr(p.audio_player, "play_file", lambda path: played.append(1))
    monkeypatch.setattr(au, "get_word_audio", lambda w: ("tts", str(mp3)))
    p.start_session(db.get_pack_words("Kitob", 1), "Kitob", 1)
    assert played == [1]              # so'z ko'rinishi bilan bir marta
    p.reveal_answer()
    assert played == [1]              # "Javobni ko'rsatish"da qayta chalinmaydi
