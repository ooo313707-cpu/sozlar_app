"""
test_claude2_spell_typing.py  (CLAUDE-2, v1.5)
----------------------------------------------
Imlo tekshiruvi (spell_checker), v3 migratsiyasi, yozish rejimi va
Again/Hard kartalarni sessiya oxirida qayta ko'rsatish testlari.

Ishga tushirish (loyiha ildizidan):   python -m pytest tests -q
Tarmoq KERAK EMAS: Gemini soxta (mock) funksiya bilan almashtiriladi.
Paket nomi o'zi aniqlanadi: ildizdagi `database.py` + `__init__.py` bor papka.
"""

import gc
import importlib
import json
import os
import sqlite3
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent.parent


def _find_package() -> str:
    for d in sorted(ROOT.iterdir()):
        if d.is_dir() and (d / "database.py").exists() and (d / "__init__.py").exists():
            return d.name
    raise RuntimeError("database.py joylashgan paket topilmadi")


def _find_ui_subpackage(pkg: str) -> str:
    for d in sorted((ROOT / pkg).iterdir()):
        if d.is_dir() and (d / "flashcard.py").exists():
            return d.name
    raise RuntimeError("flashcard.py joylashgan quyi paket topilmadi")


PKG = _find_package()
UI = _find_ui_subpackage(PKG)
import sys
sys.path.insert(0, str(ROOT))

dbm = importlib.import_module(f"{PKG}.database")
spell = importlib.import_module(f"{PKG}.spell_checker")
flashcard = importlib.import_module(f"{PKG}.{UI}.flashcard")
settings_mod = importlib.import_module(f"{PKG}.{UI}.settings")


# ---------------------------------------------------------------------------
# Fixture'lar
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path, monkeypatch):
    """Har test uchun toza vaqtinchalik baza."""
    monkeypatch.setattr(dbm, "DB_PATH", tmp_path / "sozlar.db")
    dbm.init_db()
    return dbm


_WIDGETS: list = []        # testlarda yaratilgan oynalar - QApplication'dan OLDIN o'chiriladi


def track(widget):
    """Widget'ni ro'yxatga oladi (test oxirida deleteLater). Qt obyektlari
    QApplication'dan keyin o'chsa, Python chiqishda segfault bo'ladi."""
    _WIDGETS.append(widget)
    return widget


def _drop_widgets(app):
    while _WIDGETS:
        _WIDGETS.pop().deleteLater()
    app.processEvents()
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    gc.collect()


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    _drop_widgets(app)


@pytest.fixture(autouse=True)
def _cleanup_widgets():
    yield
    app = QApplication.instance()
    if app is not None:
        _drop_widgets(app)


class FakeAI:
    def discard_prefetch(self):
        pass

    def prefetch_after_cards(self, *a, **k):
        pass

    def start_after_cards(self, *a, **k):
        pass


class FakeMainWindow:
    def __init__(self):
        self.pages = {"ai_practice": FakeAI()}
        self.shown = []

    def show_page(self, name):
        self.shown.append(name)

    def offer_forgotten_review(self, *a, **k):
        self.shown.append("forgotten")


def add_ok(db, word, translation="ma'no", example=""):
    """So'z qo'shadi va imlo holatini 'ok' qiladi (kartalarda qatnashishi uchun)."""
    wid = db.add_word(word, translation, example)
    db.set_spell_status(wid, "ok")
    return wid


def make_page(qapp, db, words, **kw):
    ids = [add_ok(db, w, t) for w, t in words]
    mw = FakeMainWindow()
    page = track(flashcard.FlashcardPage(mw))
    page.start_session(ids, ai_after=False, **kw)
    return page, mw, ids


def ratings(db, word_id):
    conn = db.get_connection()
    rows = [r["rating"] for r in conn.execute(
        "SELECT rating FROM history WHERE word_id = ? ORDER BY id", (word_id,))]
    conn.close()
    return rows


def ai_json(**over):
    data = {"word_ok": True, "suggestion": None, "translation_ok": True,
            "example_ok": True, "note": ""}
    data.update(over)
    return lambda prompt: json.dumps(data)


# ---------------------------------------------------------------------------
# Baza: v3 migratsiyasi
# ---------------------------------------------------------------------------

def _make_v2_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE words (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT NOT NULL UNIQUE,
            translation TEXT NOT NULL, example TEXT, category TEXT DEFAULT 'Umumiy',
            added_date TEXT NOT NULL, card_id INTEGER, state INTEGER, step INTEGER,
            stability REAL, difficulty REAL, due TEXT, last_review TEXT, pack INTEGER);
        CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, word_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL, rating TEXT NOT NULL, source TEXT NOT NULL, duration_ms INTEGER);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        INSERT INTO words (word, translation, added_date, pack) VALUES ('apple','olma','2026-01-01',1);
        INSERT INTO words (word, translation, added_date, pack) VALUES ('pear','nok','2026-01-02',1);
        PRAGMA user_version = 2;
    """)
    conn.commit()
    conn.close()


def test_migration_v3_existing_words_confirmed(tmp_path, monkeypatch):
    path = tmp_path / "sozlar.db"
    _make_v2_db(path)
    monkeypatch.setattr(dbm, "DB_PATH", path)
    dbm.init_db()
    conn = dbm.get_connection()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == dbm.SCHEMA_VERSION >= 3   # v4 (CLAUDE-3) qo'shilgan
    statuses = {r["word"]: r["spell_status"] for r in conn.execute("SELECT * FROM words")}
    conn.close()
    assert statuses == {"apple": "confirmed", "pear": "confirmed"}
    assert list((tmp_path / "backups").glob("*premigrate*.db")), "migratsiyadan oldin zaxira olinmadi"


def test_migration_v3_runs_once_new_word_unchecked(tmp_path, monkeypatch):
    path = tmp_path / "sozlar.db"
    _make_v2_db(path)
    monkeypatch.setattr(dbm, "DB_PATH", path)
    dbm.init_db()
    new_id = dbm.add_word("ubiquitous", "hamma joyda")
    dbm.init_db()                                  # qayta ishga tushirish: hech narsa o'zgarmasin
    assert dbm.get_word(new_id)["spell_status"] == "unchecked"
    assert dbm.get_word(1)["spell_status"] == "confirmed"


def test_set_spell_status_validates(db):
    wid = db.add_word("apple", "olma")
    with pytest.raises(ValueError):
        db.set_spell_status(wid, "weird")
    db.set_spell_status(wid, "suspect")
    assert db.get_word(wid)["spell_status"] == "suspect"


@pytest.mark.parametrize("status,expected", [
    ("unchecked", False), ("suspect", False), ("ok", True), ("confirmed", True)])
def test_is_studyable(db, status, expected):
    wid = db.add_word("apple", "olma")
    db.set_spell_status(wid, status)
    assert db.is_studyable(db.get_word(wid)) is expected


# ---------------------------------------------------------------------------
# Imlo tekshiruvi
# ---------------------------------------------------------------------------

def test_correct_word_is_ok(db):
    r = spell.check_word("ubiquitous", "hamma joyda", use_ai=False)
    assert r.status == "ok" and r.suggestion is None and not r.ai_checked


def test_misspelled_word_is_suspect_with_suggestion(db):
    r = spell.check_word("ubiquitus", "hamma joyda", use_ai=False)
    assert r.status == "suspect"
    assert r.suggestion == "ubiquitous"
    assert any("ubiquitus" in i for i in r.issues)


def test_phrase_checked_per_word_and_case_kept(db):
    assert spell.check_word("give up", "taslim bo'lmoq", use_ai=False).status == "ok"
    r = spell.check_word("Recieve", "olmoq", use_ai=False)
    assert r.status == "suspect" and r.suggestion == "Receive"


def test_checked_word_saved_to_db(db):
    wid = db.add_word("apple", "olma")
    r = spell.check_saved_word(wid, use_ai=False)
    assert r.status == "ok"
    assert db.get_word(wid)["spell_status"] == "ok"
    bad = db.add_word("ubiquitus", "x")
    assert spell.check_saved_word(bad, use_ai=False).status == "suspect"
    assert db.get_word(bad)["spell_status"] == "suspect"


def test_confirmed_word_is_not_rechecked(db):
    wid = db.add_word("Wi-Fi", "simsiz tarmoq")
    spell.confirm_word(wid)
    calls = []
    r = spell.check_saved_word(wid, use_ai=True, ai_func=lambda p: calls.append(p) or "{}")
    assert r.status == "confirmed" and calls == []
    assert db.get_word(wid)["spell_status"] == "confirmed"


def test_apply_suggestion_updates_word_and_status(db):
    wid = db.add_word("ubiquitus", "hamma joyda", "It is ubiquitus.", "IELTS")
    spell.check_saved_word(wid, use_ai=False)
    spell.apply_suggestion(wid, "ubiquitous")
    row = db.get_word(wid)
    assert (row["word"], row["translation"], row["category"]) == ("ubiquitous", "hamma joyda", "IELTS")
    assert row["spell_status"] == "ok"
    with pytest.raises(ValueError):
        spell.apply_suggestion(wid, "  ")


def test_nothing_changes_automatically(db):
    wid = db.add_word("ubiquitus", "hamma joyda")
    spell.check_saved_word(wid, use_ai=False)
    assert db.get_word(wid)["word"] == "ubiquitus"        # faqat taklif, almashtirish yo'q


# --- Gemini qatlami (mock) ---

def test_ai_clears_rare_word_local_dictionary_does_not_know(db):
    r = spell.check_word("zugzwang", "majburiy yurish", use_ai=True, ai_func=ai_json())
    assert r.status == "ok" and r.ai_checked


def test_ai_flags_spelling_and_gives_suggestion(db):
    r = spell.check_word("ubiquitus", "hamma joyda", use_ai=True,
                         ai_func=ai_json(word_ok=False, suggestion="ubiquitous",
                                         note="Imlo xatosi."))
    assert r.status == "suspect" and r.suggestion == "ubiquitous" and r.ai_checked
    assert "Imlo xatosi." in r.issues


def test_ai_flags_translation_or_example(db):
    r = spell.check_word("apple", "nok", "I ate a apple.", use_ai=True,
                         ai_func=ai_json(translation_ok=False, example_ok=False))
    assert r.status == "suspect" and r.suggestion is None
    assert len(r.issues) == 2


def test_ai_unavailable_falls_back_to_local(db):
    def boom(prompt):
        raise spell.AIUnavailable("tarmoq yo'q")
    ok = spell.check_word("apple", "olma", use_ai=True, ai_func=boom)
    assert ok.status == "ok" and not ok.ai_checked
    bad = spell.check_word("ubiquitus", "x", use_ai=True, ai_func=boom)
    assert bad.status == "suspect" and bad.suggestion == "ubiquitous" and not bad.ai_checked


def test_ai_garbage_response_falls_back_to_local(db):
    r = spell.check_word("apple", "olma", use_ai=True, ai_func=lambda p: "bu JSON emas")
    assert r.status == "ok" and not r.ai_checked


def test_ai_json_in_markdown_fence_is_parsed(db):
    fenced = '```json\n{"word_ok": true, "translation_ok": true, "example_ok": true}\n```'
    r = spell.check_word("zugzwang", "x", use_ai=True, ai_func=lambda p: fenced)
    assert r.status == "ok" and r.ai_checked


def test_ai_timeout_falls_back_to_local(db):
    def slow(prompt):
        time.sleep(2)
        return "{}"
    t0 = time.monotonic()
    r = spell.check_word("ubiquitus", "x", use_ai=True, ai_func=slow, ai_timeout=0.2)
    assert time.monotonic() - t0 < 1.5
    assert r.status == "suspect" and not r.ai_checked


def test_no_api_key_means_local_only(db):
    r = spell.check_word("apple", "olma", use_ai=True)      # standart ai_func, kalit yo'q
    assert r.status == "ok" and not r.ai_checked


def test_ai_setting_off_skips_ai(db):
    db.set_setting("spell_check_ai", "0")
    wid = db.add_word("apple", "olma")
    calls = []
    spell.check_saved_word(wid, ai_func=lambda p: calls.append(p) or "{}")
    assert calls == []


def test_worker_emits_result(db, qapp):
    wid = db.add_word("ubiquitus", "x")
    got = []
    w = spell.SpellCheckWorker(wid, use_ai=False)
    w.finished_check.connect(got.append)
    w.run()                                   # to'g'ridan-to'g'ri (oqim kerak emas)
    assert got and got[0].status == "suspect"


def test_worker_emits_even_on_error(db, qapp):
    got = []
    w = spell.SpellCheckWorker(99999, use_ai=False)       # mavjud bo'lmagan so'z
    w.finished_check.connect(got.append)
    w.run()
    assert got and got[0].status == "unchecked" and got[0].error


# ---------------------------------------------------------------------------
# Yozish rejimi: baholash
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("typed,target,expected", [
    ("apple", "apple", "good"),
    ("  APPLE ", "apple", "good"),            # katta-kichik harf va bo'shliq farqsiz
    ("Give  Up", "give up", "good"),
    ("aple", "apple", "hard"),                # tushib qolgan harf (5 harfli)
    ("applle", "apple", "hard"),              # ortiqcha harf
    ("appla", "apple", "hard"),               # almashgan harf
    ("aplle", "apple", "hard"),               # 1 ta harf almashgan (l -> p)
    ("aplee", "apple", "again"),              # 2 ta farq
    ("banana", "apple", "again"),
    ("ca", "cat", "again"),                   # 3 harfli so'zda typo kechirilmaydi
    ("cot", "cat", "again"),
    ("", "apple", None),
    ("   ", "apple", None),
])
def test_grade_typed_answer(typed, target, expected):
    assert flashcard.grade_typed_answer(typed, target) == expected


# ---------------------------------------------------------------------------
# Yozish rejimi: ekran
# ---------------------------------------------------------------------------

def test_typing_mode_off_hides_input_and_keeps_old_view(db, qapp):
    page, mw, ids = make_page(qapp, db, [("apple", "olma")])
    assert not page.typing_active
    assert not page.typing_input.isVisibleTo(page)
    assert page.word_label.text() == "apple"
    assert page.show_answer_btn.isVisibleTo(page)


def test_typing_mode_on_shows_translation_and_input(db, qapp):
    db.set_setting("typing_mode", "1")
    page, mw, ids = make_page(qapp, db, [("apple", "olma")])
    assert page.typing_active
    assert page.typing_input.isVisibleTo(page)
    assert page.word_label.text() == "olma"
    assert not page.show_answer_btn.isVisibleTo(page)


def _typing_round(db, qapp, typed, target="apple"):
    db.set_setting("typing_mode", "1")
    page, mw, ids = make_page(qapp, db, [(target, "ma'no")])
    page.typing_input.setText(typed)
    page.submit_typed()
    return page, ids[0]


def test_typing_correct_gives_good(db, qapp):
    page, wid = _typing_round(db, qapp, "Apple")
    assert ratings(db, wid) == []                 # Enter baho yozmaydi, davom etish bosiladi
    page.advance()
    assert ratings(db, wid) == ["Good"]


def test_typing_one_letter_typo_gives_hard(db, qapp):
    page, wid = _typing_round(db, qapp, "aple")
    page.advance()
    assert ratings(db, wid) == ["Hard"]


def test_typing_wrong_gives_again(db, qapp):
    page, wid = _typing_round(db, qapp, "banana")
    page.advance()
    assert ratings(db, wid) == ["Again"]


def test_typing_easy_only_after_correct_answer(db, qapp):
    page, wid = _typing_round(db, qapp, "apple")
    assert page.rating_buttons["easy"].isVisibleTo(page)
    assert not page.rating_buttons["good"].isVisibleTo(page)
    page.rate_current("easy")
    assert ratings(db, wid) == ["Easy"]


def test_typing_easy_ignored_when_wrong_or_typo(db, qapp):
    page, wid = _typing_round(db, qapp, "banana")
    assert not page.rating_buttons["easy"].isVisibleTo(page)
    page.rate_current("easy")
    page.rate_current("good")
    assert ratings(db, wid) == []                 # hech narsa yozilmadi
    page.advance()
    assert ratings(db, wid) == ["Again"]


def test_typing_empty_field_warns_and_does_not_grade(db, qapp):
    page, wid = _typing_round(db, qapp, "   ")
    assert page.typing_feedback.text() != ""
    assert not page.answer_shown and ratings(db, wid) == []
    assert page.typing_input.isEnabled()


def test_typing_mode_off_after_on_restores_old_view(db, qapp):
    db.set_setting("typing_mode", "1")
    db.set_setting("typing_mode", "0")
    page, mw, ids = make_page(qapp, db, [("apple", "olma")])
    assert not page.typing_active and not page.typing_input.isVisibleTo(page)


def test_settings_page_saves_typing_mode(db, qapp):
    page = track(settings_mod.SettingsPage(FakeMainWindow()))
    page.refresh()
    assert page.typing_mode_input.isChecked() is False        # standart: o'chiq
    page.typing_mode_input.setChecked(True)
    # QMessageBox/refresh_user_name ni chetlab o'tamiz: faqat sozlamani yozish qismi
    db.set_setting("typing_mode", "1" if page.typing_mode_input.isChecked() else "0")
    page.refresh()
    assert page.typing_mode_input.isChecked() is True


# ---------------------------------------------------------------------------
# Again/Hard kartalar sessiya oxirida qaytadi
# ---------------------------------------------------------------------------

def test_again_and_hard_cards_return_at_end_fsrs_once(db, qapp):
    page, mw, ids = make_page(qapp, db, [("one", "1"), ("two", "2"), ("three", "3")])
    w1, w2, w3 = ids
    for rating in ("again", "good", "hard"):
        page.reveal_answer()
        page.rate_current(rating)
    # asosiy navbat tugadi -> faqat Again/Hard olganlar qaytadi (w1, w3), w2 qaytmaydi
    assert page.in_retry
    shown = []
    while page.current_id is not None:
        shown.append(page.current_id)
        page.reveal_answer()
        page.rate_current("good")                 # qayta ko'rishda baho YO'Q: e'tiborsiz
        page.advance()
    assert shown == [w1, w3]
    assert mw.shown == ["home"]
    # FSRS har so'zga aynan 1 marta yangilangan
    assert ratings(db, w1) == ["Again"]
    assert ratings(db, w2) == ["Good"]
    assert ratings(db, w3) == ["Hard"]


def test_retry_phase_shows_next_button_not_ratings(db, qapp):
    page, mw, ids = make_page(qapp, db, [("one", "1")])
    page.reveal_answer()
    page.rate_current("again")
    assert page.in_retry and page.current_id == ids[0]
    page.reveal_answer()
    assert page.next_btn.isVisibleTo(page)
    assert not any(b.isVisibleTo(page) for b in page.rating_buttons.values())


def test_all_good_has_no_retry_phase(db, qapp):
    page, mw, ids = make_page(qapp, db, [("one", "1"), ("two", "2")])
    for _ in ids:
        page.reveal_answer()
        page.rate_current("good")
    assert not page.in_retry
    assert mw.shown == ["home"]


def test_struggled_ids_still_tracked_for_ai_practice(db, qapp):
    page, mw, ids = make_page(qapp, db, [("one", "1"), ("two", "2")])
    page.reveal_answer(); page.rate_current("again")
    page.reveal_answer(); page.rate_current("easy")
    assert page.struggled_ids == {ids[0]}
    assert page.seen_ids == ids


def test_typing_card_in_retry_phase_is_plain_review(db, qapp):
    db.set_setting("typing_mode", "1")
    page, mw, ids = make_page(qapp, db, [("apple", "olma")])
    page.typing_input.setText("banana")
    page.submit_typed()
    page.advance()                                # Again -> qayta ko'rish
    assert page.in_retry and not page.typing_active
    assert page.word_label.text() == "apple"
    assert not page.typing_input.isVisibleTo(page)
    page.reveal_answer(); page.advance()
    assert ratings(db, ids[0]) == ["Again"]       # qayta ko'rishda yangi baho yozilmadi


# ---------------------------------------------------------------------------
# Klaviatura (#3) va tugmalar ustidagi muddat (#2) - avvaldan bor, buzilmaganini tekshiramiz
# ---------------------------------------------------------------------------

def test_keyboard_space_reveals_then_digits_rate(db, qapp):
    page, mw, ids = make_page(qapp, db, [("one", "1"), ("two", "2")])
    QTest.keyClick(page, Qt.Key_3)                # javobdan oldin 1-4 ishlamaydi
    assert ratings(db, ids[0]) == []
    QTest.keyClick(page, Qt.Key_Space)
    assert page.answer_shown
    QTest.keyClick(page, Qt.Key_3)
    assert ratings(db, ids[0]) == ["Good"]


def test_rating_buttons_show_real_intervals(db, qapp):
    page, mw, ids = make_page(qapp, db, [("one", "1")])
    page.reveal_answer()
    texts = {k: b.text() for k, b in page.rating_buttons.items()}
    assert all("\n" in t and t.split("\n")[1] for t in texts.values())


# ---------------------------------------------------------------------------
# Imlo holati kartalarga ta'sir qiladi
# ---------------------------------------------------------------------------

def test_unchecked_and_suspect_words_excluded_from_session(db, qapp):
    good = add_ok(db, "apple", "olma")
    unchecked = db.add_word("pear", "nok")                      # standart: unchecked
    suspect = db.add_word("ubiquitus", "x")
    db.set_spell_status(suspect, "suspect")
    confirmed = db.add_word("Wi-Fi", "tarmoq")
    db.set_spell_status(confirmed, "confirmed")

    page = track(flashcard.FlashcardPage(FakeMainWindow()))
    page.start_session([good, unchecked, suspect, confirmed], ai_after=False)
    assert sorted(page.queue + [page.current_id]) == sorted([good, confirmed])
    assert "2" in page.scope_label.text()                       # 2 ta so'z kutmoqda


def test_session_with_only_unchecked_words_finishes_immediately(db, qapp):
    wid = db.add_word("pear", "nok")
    mw = FakeMainWindow()
    page = track(flashcard.FlashcardPage(mw))
    page.start_session([wid], ai_after=False)
    assert page.current_id is None and mw.shown == ["home"]


def test_apply_requeue_still_available_for_old_tests():
    from datetime import datetime, timedelta
    now = datetime(2026, 1, 1)
    q = [2, 3, 4, 5, 6]
    assert flashcard.apply_requeue(q, 1, "again", now + timedelta(minutes=1), now)
    assert q.index(1) == 4
