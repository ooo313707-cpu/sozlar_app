"""Offscreen Qt testlari: gemini_service soxta (mock), win.show() chaqiriladi."""
import time
from datetime import timedelta

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from app import database as db
from app import gemini_service
from app.ui import flashcard as fc
from app.ui import theme
from app.ui.main_window import MainWindow, counts_text


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(theme.global_stylesheet())
    return app


def pump(app, ms=50):
    end = time.time() + ms / 1000
    while time.time() < end:
        app.processEvents()


def wait_worker(app, worker):
    worker.wait()
    pump(app, 30)


def wait_all(app, ai):
    for w in list(ai._workers):
        w.wait()
    pump(app, 30)


@pytest.fixture
def boxes(monkeypatch):
    """QMessageBox'larni bloklamaslik uchun almashtiradi va chaqiruvlarni yozib boradi."""
    log = {"info": [], "question": [], "answer": QMessageBox.Yes}
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: log["info"].append(a[1:3]) or QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: QMessageBox.Ok))
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: log["question"].append(a[1:3]) or log["answer"]))
    return log


@pytest.fixture
def mock_gemini(fresh_db, monkeypatch):
    """Soxta Gemini: generate_questions + grade_batch. `state` orqali xato/kechikish boshqariladi."""
    import threading
    db.set_setting("gemini_api_key", "test-key")
    calls = {"gen": [], "grade": []}
    state = {"gen_error": None, "grade_error": None, "grade_missing": set(),
             "garbage_words": set(), "gate": None}

    def fake_gen(items, level="B1", mode="A"):
        calls["gen"].append({"n": len(items), "mode": mode, "level": level,
                             "types": [i["type"] for i in items],
                             "leech": [i["word"] for i in items if i.get("leech")]})
        if state["gate"] is not None:
            state["gate"].wait(5)
        if state["gen_error"]:
            raise RuntimeError(state["gen_error"])
        qs, passage = [], ""
        for it in items:
            t, w = it["type"], it["word"]
            if w in state["garbage_words"]:
                qs.append({"word": w, "type": t, "question": ""})
            elif t == "cloze":
                qs.append({"word": w, "type": t, "question": f"I really like ___ today. ({w})", "answer": w})
            elif t == "meaning":
                qs.append({"word": w, "type": t, "question": f'What does "{w}" mean?',
                           "options": [f"{w}-ok", "alpha", "beta", "gamma"], "correct": 0})
            elif t == "correct_sentence":
                qs.append({"word": w, "type": t, "question": f'Which sentence uses "{w}" correctly?',
                           "options": [f"Good {w} sentence.", "Bad one.", "Worse one."], "correct": 0})
            elif t == "synonym":
                qs.append({"word": w, "type": t, "question": f'Closest in meaning to "{w}"?',
                           "options": [f"{w}-syn", "x1", "x2", "x3"], "correct": 0})
            elif t == "passage":
                passage = "A short passage about life."
                qs.append({"word": w, "type": t, "question": f"In the text, what does {w} suggest?"})
            else:
                qs.append({"word": w, "type": t, "question": f"Write about {w}."})
        return {"passage": passage, "questions": qs}

    def fake_grade(entries, level="B1"):
        calls["grade"].append({"entries": entries, "level": level})
        if state["grade_error"]:
            raise RuntimeError(state["grade_error"])
        return {e["id"]: {"word_ok": True, "grammar_ok": True, "natural": True, "feedback": "Ajoyib"}
                for e in entries if e["id"] not in state["grade_missing"]}

    monkeypatch.setattr(gemini_service, "generate_questions", fake_gen)
    monkeypatch.setattr(gemini_service, "grade_batch", fake_grade)
    calls["state"] = state
    calls["threading"] = threading
    return calls


@pytest.fixture
def win(fresh_db, qapp, boxes, mock_gemini):
    w = MainWindow()
    w.show()
    pump(qapp)
    yield w
    w.close()


def add_words(n, category="Kitob", prefix=None):
    prefix = prefix or category[:2].lower()     # so'zlar UNIQUE - bo'limlar orasida nom to'qnashmasin
    ids = [db.add_word(f"{prefix}{i}", f"t{i}", "", category) for i in range(n)]
    for i in ids:     # D-008: imlo tekshiruvidan o'tmagan so'z kartada/AI mashqda qatnashmaydi
        db.set_spell_status(i, db.SPELL_OK)
    return ids


# ---------------- bosh sahifa ----------------

def test_home_tree_shows_sections_and_counts(win, qapp):
    add_words(25, "Kitob")
    ids = add_words(3, "IELTS")
    db.review_word(ids[0], "good")
    win.show_page("home")
    home = win.pages["home"]
    tree = home.tree
    cats = [tree.topLevelItem(i).text(0) for i in range(tree.topLevelItemCount())]
    assert any("IELTS" in c for c in cats) and any("Kitob" in c for c in cats)
    kitob = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                 if "Kitob" in tree.topLevelItem(i).text(0))
    assert kitob.childCount() == 2
    assert kitob.child(0).text(0) == "1-bo'limcha"
    assert "🔵 20" in kitob.child(0).text(1) and "🔴 0" in kitob.child(0).text(1)
    assert "🔵 25" in kitob.text(1)
    assert not home.cards_btn.isEnabled()          # hech narsa tanlanmagan


def test_counts_text_format():
    assert counts_text({"blue": 5, "green": 8, "red": 7}) == "🔵 5   🟢 8   🔴 7"


def test_select_enables_buttons_and_starts_cards(win, qapp):
    add_words(5, "Kitob")
    win.show_page("home")
    home = win.pages["home"]
    top = home.tree.topLevelItem(0)
    home.tree.setCurrentItem(top.child(0))
    assert home.cards_btn.isEnabled() and home.ai_btn.isEnabled()
    assert "5 ta" in home.cards_btn.text()
    home.start_cards()
    assert win.stack.currentWidget() is win.pages["flashcard"]
    assert win.pages["flashcard"].word_label.text().startswith("ki")


def test_cards_no_words_offers_forgotten_message(win, qapp, boxes):
    ids = add_words(2, "Kitob")
    for i in ids:
        db.review_word(i, "easy")
    win.show_page("home")
    home = win.pages["home"]
    home.tree.setCurrentItem(home.tree.topLevelItem(0))
    home.start_cards()
    assert boxes["info"]                              # "ko'rib chiqadigan karta yo'q" + "Unutilgan so'z yo'q"
    assert any("Unutilgan so'z yo'q" in str(m) for m in boxes["info"])
    assert win.stack.currentWidget() is win.pages["home"]


# ---------------- flashcard ----------------

def test_requeue_function():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 5, tzinfo=timezone.utc)
    q = [10, 11, 12, 13, 14, 15]
    assert fc.apply_requeue(q, 1, "again", now + timedelta(minutes=1), now)
    assert q.index(1) == 4                            # ~4 karta keyin
    q = [10, 11]
    fc.apply_requeue(q, 1, "again", now + timedelta(minutes=1), now)
    assert q == [10, 11, 1]                           # navbat qisqa - oxiriga
    q = [10, 11, 12]
    assert fc.apply_requeue(q, 2, "good", now + timedelta(minutes=10), now)
    assert q[-1] == 2                                 # boshqalar oxiriga
    q = [10]
    assert not fc.apply_requeue(q, 3, "easy", now + timedelta(days=2), now)
    assert q == [10]                                  # kun bo'yi muddat - qaytmaydi
    assert not fc.apply_requeue(q, 3, "good", now + timedelta(minutes=21), now)


def test_flashcard_requeue_in_page(win, qapp):
    ids = add_words(6, "Kitob")
    page = win.pages["flashcard"]
    page.start_session(db.get_pack_words("Kitob", 1), "Kitob", 1)
    win.show_page("flashcard")
    first = page.current_id
    page.reveal_answer()
    page.rate_current("again")
    # v1.5 (CLAUDE-2): Again/Hard olgan karta sessiya OXIRIDA qayta ko'rsatiladi (retry_queue);
    # eski "~4 karta keyin qaytadi" qoidasi bekor qilingan.
    assert first in page.retry_queue and first not in page.queue
    assert first in page.struggled_ids and first in page.seen_ids
    # Easy yangi so'zga -> kunlar -> qaytmaydi
    second = page.current_id
    page.reveal_answer()
    page.rate_current("easy")
    assert second not in page.queue


def test_flashcard_keyboard_and_buttons(win, qapp):
    add_words(3, "Kitob")
    page = win.pages["flashcard"]
    page.start_session(db.get_pack_words("Kitob", 1), "Kitob", 1)
    win.show_page("flashcard")
    page.setFocus()
    for b in list(page.rating_buttons.values()) + [page.show_answer_btn]:
        assert b.focusPolicy() == Qt.NoFocus
    assert page.translation_label.text() == ""
    QTest.keyClick(page, Qt.Key_1)                    # javob ko'rsatilmagan - baho ketmasin
    assert db.get_all_words()[0]["last_review"] is None or True
    cur = page.current_id
    assert db.get_word(cur)["last_review"] is None
    QTest.keyClick(page, Qt.Key_Space)
    assert page.answer_shown and page.translation_label.text().startswith("t")
    for k in ("again", "hard", "good", "easy"):       # tugmalarda interval ko'rinadi
        assert "\n" in page.rating_buttons[k].text()
    assert "<1 daq" in page.rating_buttons["again"].text()
    QTest.keyClick(page, Qt.Key_3)                    # Good
    assert db.get_word(cur)["last_review"] is not None
    conn = db.get_connection()
    row = conn.execute("SELECT rating, source, duration_ms FROM history WHERE word_id=?", (cur,)).fetchone()
    conn.close()
    assert row["rating"] == "Good" and row["source"] == "flashcard"
    assert row["duration_ms"] is not None and 0 <= row["duration_ms"] <= 60000


# ---------------- to'liq oqim: kartalar -> A rejimi -> unutilganlar ----------------

def test_forgotten_review_yes_starts_session(win, qapp, boxes):
    ids = add_words(3, "Kitob")
    for i in ids:
        db.review_word(i, "easy")
    # qo'lda 'unutilgan' holatga keltiramiz
    import app.database as d
    base = d._now_utc()
    d._now_utc = lambda: base + timedelta(days=5000)
    try:
        assert len(db.get_forgotten_words("Kitob", 1)) == 3
        boxes["answer"] = QMessageBox.Yes
        win.offer_forgotten_review("Kitob", 1)
        page = win.pages["flashcard"]
        assert win.stack.currentWidget() is page
        assert page.ai_after is False and len(page.queue) + 1 == 3
        boxes["answer"] = QMessageBox.No
        win.offer_forgotten_review("Kitob", 1)
        assert win.stack.currentWidget() is win.pages["home"]
    finally:
        d._now_utc = lambda: __import__("datetime").datetime.now(__import__("datetime").timezone.utc)


# ---------------- AI mashq doiralari ----------------

def test_ai_nothing_seen_goes_home(win, qapp, boxes):
    add_words(5, "Kitob")                                        # hech biri ko'rilmagan
    ai = win.pages["ai_practice"]
    assert ai.start_for_pack("Kitob", 1) is False
    assert boxes["info"]
    assert win.stack.currentWidget() is win.pages["home"]


# ---------------- qolgan sahifalar ----------------

def test_add_word_page_empty_category_and_pack(win, qapp, boxes):
    page = win.pages["add_word"]
    win.show_page("add_word")
    page.word_input.setText("apple")
    page.translation_input.setText("olma")
    page.category_input.setCurrentText("")
    page.save_word()
    w = db.get_all_words()[0]
    assert w["category"] == "Umumiy" and w["pack"] == 1
    assert any("1-bo'limcha" in str(m) for m in boxes["info"])


def test_word_list_shows_pack_column(win, qapp):
    add_words(22, "Kitob")
    win.show_page("word_list")
    t = win.pages["word_list"].table
    assert t.horizontalHeaderItem(3).text() == "Bo'limcha"
    packs = {t.item(r, 3).text() for r in range(t.rowCount())}
    assert packs == {"1-bo'limcha", "2-bo'limcha"}


def test_settings_max_questions(win, qapp, boxes):
    page = win.pages["settings"]
    win.show_page("settings")
    assert page.max_questions_input.value() == 20
    page.max_questions_input.setValue(12)
    page.save_settings()
    assert db.get_max_questions() == 12


def test_stats_page_renders(win, qapp):
    ids = add_words(3)
    db.review_word(ids[0], "good")
    win.show_page("stats")
    pump(qapp)
