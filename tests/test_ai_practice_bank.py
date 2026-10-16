"""AI mashq sahifasi + savol banki (CLAUDE-4): bank -> Gemini -> bankka saqlash, 'Yomon savol'."""
import time

import pytest
from PySide6.QtWidgets import QApplication

from app import database as db
from app import gemini_service
from app import questions as qs

app = QApplication.instance() or QApplication([])


class FakeMain:
    """MainWindow o'rniga: faqat sahifa chaqiradigan metodlar."""
    def show_page(self, name): pass
    def offer_forgotten_review(self, *a, **k): pass


@pytest.fixture
def page(fresh_db, monkeypatch):
    from app.ui.ai_practice import AIPracticePage
    monkeypatch.setattr(qs, "pick_type", lambda row, rng, avoid=None: "meaning")
    return AIPracticePage(FakeMain())


def mk(w="apple"):
    wid = db.add_word(w, "olma", category="Umumiy")
    db.set_spell_status(wid, "ok")
    return wid


def wait_for(cond, timeout=5):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    assert cond()


RAW = {"questions": [{"word": "apple", "type": "meaning", "question": "What is an apple here?",
                      "options": ["olma", "non", "suv", "sut"], "correct": 0}]}


def test_bank_empty_calls_gemini_then_saves(page, monkeypatch):
    wid = mk()
    db.set_setting("gemini_api_key", "x")
    calls = []
    monkeypatch.setattr(gemini_service, "generate_questions",
                        lambda payload, level, mode: calls.append(payload) or RAW)
    page._start(db.get_words_by_ids([wid]), "A", None, None)
    wait_for(lambda: len(page.questions) == 1)
    assert len(calls) == 1
    assert page.questions[0]["bank_id"] and db.count_bank_questions(wid) == 1


def test_bank_full_no_gemini_and_answer_recorded(page, monkeypatch):
    wid = mk()
    db.set_setting("gemini_api_key", "x")
    db.add_bank_question(wid, "meaning", db.get_english_level(),
                         {"question": "Pick it", "options": ["olma", "non", "suv"],
                          "correct": 0, "correct_answer": "olma"})
    monkeypatch.setattr(gemini_service, "generate_questions",
                        lambda *a, **k: pytest.fail("Gemini chaqirilmasligi kerak"))
    page._start(db.get_words_by_ids([wid]), "A", None, None)
    assert len(page.questions) == 1 and page.questions[0]["source"] == "bank"
    q = page.questions[0]
    page._answer(q["correct"])
    conn = db.get_connection()
    assert conn.execute("SELECT shown_count, correct_count FROM question_bank").fetchone()[:] == (1, 1)
    conn.close()
    assert not page.bad_btn.isHidden()


def test_bad_button_marks_question_bad(page):
    wid = mk()
    db.add_bank_question(wid, "meaning", db.get_english_level(),
                         {"question": "Pick it", "options": ["olma", "non", "suv"],
                          "correct": 0, "correct_answer": "olma"})
    page._start(db.get_words_by_ids([wid]), "A", None, None)
    page._answer(page.questions[0]["correct"])
    page._mark_bad()
    assert not page.bad_btn.isEnabled()
    assert db.get_bank_questions([wid], include_retired=True) == []


def test_no_api_key_local_fallback_works(page):
    wid = mk()
    for w in ("pear", "plum", "fig"):
        mk(w)
    page._start(db.get_words_by_ids([wid]), "A", None, None)
    assert len(page.questions) == 1 and page.questions[0]["source"] == "local"
    page._answer(0 if qs.is_mcq(page.questions[0]) else "x")
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM question_log").fetchone()[0] == 1
    conn.close()
    assert page.bad_btn.isHidden()
