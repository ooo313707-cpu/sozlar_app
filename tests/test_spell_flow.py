"""Yangi so'zning imlo tekshiruvi oqimi (CLAUDE-3): so'z qo'shish -> tekshiruv -> kartalar."""
import sqlite3
import time

import pytest

pytest.importorskip("PySide6")
pytest.importorskip("spellchecker")
from PySide6.QtWidgets import QApplication, QMessageBox

from app import database as db
from app import spell_checker
from app.background import ThreadWorker
from app.ui import spell_review


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _Home:
    refreshed = 0
    def refresh(self): _Home.refreshed += 1


class _MW:
    pages = {"home": _Home()}
    def refresh_user_name(self): pass


def _pump(qapp, task, timeout=10000):
    task.wait(timeout)
    qapp.processEvents()


def _fill(page, word, translation="tarjima"):
    page.word_input.setText(word)
    page.translation_input.setText(translation)
    page.save_word()


@pytest.fixture
def add_page(qapp, fresh_db, monkeypatch):
    from app.ui.add_word import AddWordPage
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: None)
    p = AddWordPage(_MW())
    p.refresh()
    return p


def _status(word):
    conn = db.get_connection()
    try:
        r = conn.execute("SELECT spell_status FROM words WHERE word = ?", (word,)).fetchone()
        return r["spell_status"] if r else None
    finally:
        conn.close()


# --- asosiy xato: yangi so'z endi tekshiriladi --------------------------------------

def test_new_word_is_checked_automatically_and_becomes_studyable(add_page, qapp):
    _fill(add_page, "apple", "olma")
    assert _status("apple") == "unchecked"            # saqlangan zahoti
    assert add_page._spell_tasks, "tekshiruv boshlanmadi (asosiy xato)"
    for t in list(add_page._spell_tasks):
        _pump(qapp, t)
    assert _status("apple") == "ok"
    assert [w["word"] for w in db.get_study_words("Umumiy")] == ["apple"]   # kartada chiqadi
    assert "to'g'ri" in add_page.spell_label.text()
    assert _Home.refreshed >= 1


@pytest.mark.parametrize("choice", [spell_review.FIX, spell_review.CONFIRM, spell_review.LATER])
def test_suspect_word_dialog_choices(add_page, qapp, monkeypatch, choice):
    suggestion = spell_checker.local_check("aple")[1]       # lug'at nimani taklif qilsa, shu
    assert suggestion and suggestion != "aple"
    shown = {}

    def fake_choose(parent, word, result, allow_stop=False):
        shown["word"], shown["suggestion"] = word, result.suggestion
        return choice

    monkeypatch.setattr(spell_review, "choose_action", fake_choose)
    _fill(add_page, "aple", "xato yozilgan")
    for t in list(add_page._spell_tasks):
        _pump(qapp, t)
    assert shown == {"word": "aple", "suggestion": suggestion}
    studyable = [w["word"] for w in db.get_study_words("Umumiy")]
    if choice == spell_review.FIX:
        assert _status("aple") is None and _status(suggestion) == "ok" and studyable == [suggestion]
    elif choice == spell_review.CONFIRM:
        assert _status("aple") == "confirmed" and studyable == ["aple"]
    else:
        assert _status("aple") == "suspect" and studyable == []


def test_fix_to_existing_word_warns_and_stays_suspect(add_page, qapp, monkeypatch):
    suggestion = spell_checker.local_check("aple")[1]
    db.add_word(suggestion, "bor")                           # taklif qilinadigan so'z allaqachon bazada
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warned.append(a[2]))
    monkeypatch.setattr(spell_review, "choose_action", lambda *a, **k: spell_review.FIX)
    _fill(add_page, "aple", "x")
    for t in list(add_page._spell_tasks):
        _pump(qapp, t)
    assert warned and "allaqachon bor" in warned[0]
    assert _status("aple") == "suspect"
    assert "Shubhali so'zlarni ko'rib chiqish" in add_page.spell_label.text()


def test_spell_engine_failure_leaves_word_for_later(add_page, qapp, monkeypatch):
    def boom(*a, **k): raise RuntimeError("lug'at yo'q")
    monkeypatch.setattr(spell_checker, "check_saved_word", boom)
    _fill(add_page, "apple", "olma")
    for t in list(add_page._spell_tasks):
        _pump(qapp, t)
    assert _status("apple") == "unchecked"
    assert "tekshirilmadi" in add_page.spell_label.text()


# --- kartalar tanlovi: tekshirilmagan so'z limitni "yemasligi" kerak --------------------

def test_study_words_exclude_unchecked_and_do_not_eat_daily_slots(fresh_db):
    db.set_setting("daily_new_limit", "2")
    unchecked = db.add_word("aaa", "t")                 # eng birinchi, lekin tekshirilmagan
    suspect = db.add_word("bbb", "t"); db.set_spell_status(suspect, "suspect")
    ok1 = db.add_word("ccc", "t"); db.set_spell_status(ok1, "ok")
    ok2 = db.add_word("ddd", "t"); db.set_spell_status(ok2, "confirmed")
    ok3 = db.add_word("eee", "t"); db.set_spell_status(ok3, "ok")
    words = [w["word"] for w in db.get_study_words("Umumiy")]
    assert words == ["ccc", "ddd"]                       # limit=2 to'liq ishlatiladi
    assert unchecked and suspect


def test_due_review_words_also_filtered(fresh_db):
    wid = db.add_word("old", "t"); db.set_spell_status(wid, "ok")
    db.review_word(wid, "again")
    db.set_spell_status(wid, "suspect")
    conn = db.get_connection(); conn.execute("UPDATE words SET due = '2000-01-01T00:00:00+00:00'"); conn.commit(); conn.close()
    assert db.get_study_words("Umumiy") == []
    db.set_spell_status(wid, "confirmed")
    assert len(db.get_study_words("Umumiy")) == 1


def test_ai_practice_select_skips_unstudyable(fresh_db):
    from app.ui.ai_practice import AIPracticePage
    good = db.add_word("good", "t"); db.set_spell_status(good, "ok"); db.review_word(good, "good")
    bad = db.add_word("bad", "t"); db.review_word(bad, "good")          # tekshirilmagan
    rows = db.get_category_words("Umumiy")
    chosen = AIPracticePage._select(None, rows)
    assert [w["word"] for w in chosen] == ["good"]


# --- shubhali so'zlarni ko'rib chiqish ---------------------------------------------------

def test_review_suspects_batch(fresh_db, qapp, monkeypatch):
    d = db.add_word("apple", "4"); db.set_spell_status(d, "suspect")   # lug'at endi taniydi -> o'zi hal bo'ladi
    a = db.add_word("aple", "1"); db.set_spell_status(a, "suspect")
    b = db.add_word("bannana", "2"); db.set_spell_status(b, "suspect")
    c = db.add_word("zzqx", "3"); db.set_spell_status(c, "suspect")
    fixed_to = spell_checker.local_check("aple")[1]
    answers = iter([spell_review.FIX, spell_review.CONFIRM, spell_review.STOP])
    seen = []
    monkeypatch.setattr(spell_review, "choose_action",
                        lambda parent, word, result, allow_stop=False: (seen.append((word, allow_stop)), next(answers))[1])
    counts = spell_review.review_suspects(None)
    assert counts == {"fixed": 1, "confirmed": 1, "later": 1, "resolved_auto": 1, "total": 4}
    assert [x[0] for x in seen] == ["aple", "bannana", "zzqx"] and all(x[1] for x in seen)
    assert _status("apple") == "ok" and _status("bannana") == "confirmed"
    assert _status("aple") is None and _status(fixed_to) == "ok"
    assert _status("zzqx") == "suspect"                       # "To'xtatish": tegilmagan


def test_settings_review_button(qapp, fresh_db, monkeypatch):
    from app.ui.settings import SettingsPage
    infos = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: infos.append(a[2]))
    page = SettingsPage(_MW())
    page.review_suspect_words()
    assert infos[-1] == "Shubhali so'z yo'q ✅"
    w = db.add_word("aple", "x"); db.set_spell_status(w, "suspect")
    monkeypatch.setattr(spell_review, "choose_action", lambda *a, **k: spell_review.CONFIRM)
    page.review_suspect_words()
    assert "Tasdiqlandi: 1" in infos[-1] and _status("aple") == "confirmed"


# --- ThreadWorker ------------------------------------------------------------------------

def test_thread_worker_lifecycle(qapp):
    from PySide6.QtCore import Signal

    class W(ThreadWorker):
        done = Signal(int)
        def run(self):
            time.sleep(0.05)
            self._safe_emit(self.done, 7)

    got, fin = [], []
    w = W()
    w.done.connect(got.append)
    w.finished.connect(lambda: fin.append(1))
    assert not w.isRunning()
    w.start()
    assert w.isRunning()
    assert w.wait(5000) and not w.isRunning()
    qapp.processEvents()
    assert got == [7] and fin == [1]


def test_thread_worker_survives_deleted_owner(qapp):
    from PySide6.QtCore import QObject, Signal
    import shiboken6

    class W(ThreadWorker):
        done = Signal()
        def run(self):
            time.sleep(0.1)
            self._safe_emit(self.done)

    owner = QObject()
    w = W(owner)
    w.start()
    shiboken6.delete(owner)          # sahifa/oyna ish tugamasdan o'chdi: istisno ko'tarilmasligi kerak
    time.sleep(0.3)
    qapp.processEvents()
