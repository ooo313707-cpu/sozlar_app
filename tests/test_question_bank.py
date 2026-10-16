"""Savol banki va statistika (CLAUDE-4, migratsiya v5)."""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app import database as db


def Q(text="I like ___ a lot.", answer="apple", **extra):
    return {"question": text, "options": [answer, "x", "y", "z"], "correct_answer": answer, **extra}


DISTINCT = [
    "The weather yesterday was surprisingly ___ for October.",
    "Which option best completes: she was ___ about the results?",
    "Choose the word closest in meaning to the highlighted one.",
    "My brother always forgets where he left his ___ keys.",
    "Translate the following expression into Uzbek, please.",
    "Nobody expected the committee to ___ such a decision.",
    "After a long meeting everyone felt completely ___ and quiet.",
    "Read the sentence carefully and fix the grammar mistake.",
    "Their new apartment has a wonderful view of the ___ river.",
    "What does the author imply in the second paragraph?",
    "Pick the sentence in which the word is used correctly here.",
]


def studyable_word(word="apple", category="Umumiy"):
    wid = db.add_word(word, "t", category=category)
    db.set_spell_status(wid, "ok")
    return wid


# ---------------- migratsiya ----------------

def test_schema_version_is_5(fresh_db):
    conn = db.get_connection()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION == 5
    assert {"question_bank", "question_log", "import_queue"} <= {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()


def _make_v4_db(path):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE words (id INTEGER PRIMARY KEY AUTOINCREMENT, word TEXT NOT NULL UNIQUE,
            translation TEXT NOT NULL, example TEXT, category TEXT DEFAULT 'Umumiy',
            added_date TEXT NOT NULL, card_id INTEGER, state INTEGER, step INTEGER,
            stability REAL, difficulty REAL, due TEXT, last_review TEXT, pack INTEGER,
            spell_status TEXT DEFAULT 'unchecked', audio_source TEXT DEFAULT 'none', audio_path TEXT);
        CREATE TABLE history (id INTEGER PRIMARY KEY AUTOINCREMENT, word_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL, rating TEXT NOT NULL, source TEXT NOT NULL, duration_ms INTEGER);
        CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE import_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, word_id INTEGER NOT NULL,
            task TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE);
        INSERT INTO words (word, translation, added_date, pack, spell_status)
            VALUES ('apple','olma','2026-01-01',1,'ok');
        INSERT INTO import_queue (word_id, task) VALUES (1, 'spell');
        PRAGMA user_version = 4;
    """)
    conn.commit()
    conn.close()


def test_migration_4_to_5_keeps_data_and_backs_up(tmp_path, monkeypatch):
    path = tmp_path / "sozlar.db"
    _make_v4_db(path)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    conn = db.get_connection()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 5
    assert conn.execute("SELECT word, spell_status FROM words").fetchone()[:] == ("apple", "ok")
    assert conn.execute("SELECT COUNT(*) FROM import_queue").fetchone()[0] == 1   # Claude 3 navbati saqlandi
    assert conn.execute("SELECT COUNT(*) FROM question_bank").fetchone()[0] == 0
    conn.close()
    assert list((tmp_path / "backups").glob("*premigrate*.db"))


def test_migration_5_runs_once(tmp_path, monkeypatch):
    path = tmp_path / "sozlar.db"
    _make_v4_db(path)
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    qid = db.add_bank_question(1, "cloze", "B1", Q())
    db.init_db()
    db.init_db()
    assert db.count_bank_questions(1) == 1 and qid is not None


# ---------------- qo'shish, tekshiruv ----------------

def test_add_and_get_roundtrip(fresh_db):
    wid = studyable_word()
    qid = db.add_bank_question(wid, "cloze", "B1", Q())
    got = db.get_bank_questions([wid], level="B1")
    assert [g["id"] for g in got] == [qid]
    assert got[0]["data"]["correct_answer"] == "apple" and got[0]["status"] == "active"


@pytest.mark.parametrize("qtype,level,data", [
    ("essay", "B1", Q()),                 # bankda saqlanmaydigan (ochiq) tur
    ("cloze", "D9", Q()),                 # noma'lum daraja
    ("cloze", "B1", {"question": "x"}),   # javob yo'q
    ("cloze", "B1", {"question": " ", "correct_answer": "a"}),
    ("cloze", "B1", "matn"),
])
def test_invalid_questions_rejected(fresh_db, qtype, level, data):
    wid = studyable_word()
    with pytest.raises(ValueError):
        db.add_bank_question(wid, qtype, level, data)
    assert db.count_bank_questions(wid) == 0


def test_unknown_word_rejected(fresh_db):
    with pytest.raises(ValueError):
        db.add_bank_question(999, "cloze", "B1", Q())


# ---------------- dublikat ----------------

def test_duplicate_not_saved(fresh_db):
    wid = studyable_word()
    assert db.add_bank_question(wid, "cloze", "B1", Q("I like ___ a lot.")) is not None
    assert db.add_bank_question(wid, "cloze", "B1", Q("I like ___ a lot.")) is None       # aynan bir xil
    assert db.add_bank_question(wid, "cloze", "C1", Q("  i LIKE ___ a  lot. ")) is None   # katta-kichik/bo'shliq
    assert db.count_bank_questions(wid) == 1


def test_same_text_other_type_or_word_is_not_duplicate(fresh_db):
    a, b = studyable_word("apple"), studyable_word("pear")
    assert db.add_bank_question(a, "cloze", "B1", Q("Fill ___ in.")) is not None
    assert db.add_bank_question(a, "synonym", "B1", Q("Fill ___ in.")) is not None
    assert db.add_bank_question(b, "cloze", "B1", Q("Fill ___ in.")) is not None


def test_bad_question_cannot_come_back(fresh_db):
    wid = studyable_word()
    qid = db.add_bank_question(wid, "cloze", "B1", Q("Bad one ___ here."))
    db.mark_bank_question_bad(qid)
    assert db.get_bank_questions([wid]) == []
    assert db.add_bank_question(wid, "cloze", "B1", Q("Bad one ___ here.")) is None


# ---------------- retired ----------------

def test_three_correct_in_a_row_retires(fresh_db):
    wid = studyable_word()
    qid = db.add_bank_question(wid, "cloze", "B1", Q())
    assert db.record_bank_answer(qid, True) == "active"
    assert db.record_bank_answer(qid, True) == "active"
    assert db.record_bank_answer(qid, True) == "retired"
    assert db.get_bank_questions([wid]) == []                           # kam chiqadi: oddiy tanlovda yo'q
    assert [g["id"] for g in db.get_bank_questions([wid], include_retired=True)] == [qid]
    conn = db.get_connection()                                          # o'chirilmaydi
    assert conn.execute("SELECT COUNT(*) FROM question_bank").fetchone()[0] == 1
    conn.close()


def test_wrong_answer_resets_streak(fresh_db):
    wid = studyable_word()
    qid = db.add_bank_question(wid, "cloze", "B1", Q())
    db.record_bank_answer(qid, True)
    db.record_bank_answer(qid, True)
    db.record_bank_answer(qid, False)                                   # seriya uzildi
    assert db.record_bank_answer(qid, True) == "active"
    assert db.record_bank_answer(qid, True) == "active"
    assert db.record_bank_answer(qid, True) == "retired"


def test_record_updates_counters_and_log(fresh_db):
    wid = studyable_word()
    qid = db.add_bank_question(wid, "meaning", "B1", Q("What is X?"))
    db.record_bank_answer(qid, True)
    db.record_bank_answer(qid, False)
    row = db.get_bank_questions([wid])[0]
    assert (row["shown_count"], row["correct_count"]) == (2, 1) and row["last_shown"]
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM question_log").fetchone()[0] == 2
    conn.close()


def test_record_unknown_question_raises(fresh_db):
    with pytest.raises(ValueError):
        db.record_bank_answer(12345, True)


# ---------------- har so'zga 10 ta ----------------

def test_max_10_active_replaces_most_answered_correctly(fresh_db):
    wid = studyable_word()
    ids = [db.add_bank_question(wid, "cloze", "B1", Q(DISTINCT[i])) for i in range(10)]
    assert db.count_bank_questions(wid) == 10
    db.record_bank_answer(ids[3], True)                                 # eng ko'p to'g'ri javob berilgani
    new = db.add_bank_question(wid, "cloze", "B1", Q(DISTINCT[10]))
    assert new is not None and db.count_bank_questions(wid) == 10
    conn = db.get_connection()
    left = {r[0] for r in conn.execute("SELECT id FROM question_bank WHERE word_id = ?", (wid,))}
    conn.close()
    assert ids[3] not in left and new in left


def test_retired_do_not_count_toward_limit(fresh_db):
    wid = studyable_word()
    qid = db.add_bank_question(wid, "cloze", "B1", Q(DISTINCT[10]))
    for _ in range(3):
        db.record_bank_answer(qid, True)
    for i in range(10):
        assert db.add_bank_question(wid, "cloze", "B1", Q(DISTINCT[i])) is not None
    assert db.count_bank_questions(wid) == 10 and db.count_bank_questions(wid, "retired") == 1


# ---------------- tanlash: imlo, daraja, kam ko'rsatilgan ----------------

def test_unchecked_and_suspect_words_excluded(fresh_db):
    ok = studyable_word("apple")
    unchecked = db.add_word("pear", "t")                                # standart: unchecked
    suspect = db.add_word("plum", "t")
    db.set_spell_status(suspect, "suspect")
    for w in (ok, unchecked, suspect):
        db.add_bank_question(w, "cloze", "B1", Q(f"Sentence for {w} with ___ blank."))
    got = db.get_bank_questions([ok, unchecked, suspect])
    assert [g["word_id"] for g in got] == [ok]


def test_level_filter(fresh_db):
    wid = studyable_word()
    db.add_bank_question(wid, "cloze", "A2", Q("Easy ___ one."))
    b1 = db.add_bank_question(wid, "synonym", "B1", Q("Medium synonym ___ q."))
    assert [g["id"] for g in db.get_bank_questions([wid], level="B1")] == [b1]
    assert len(db.get_bank_questions([wid])) == 1                       # per_word=1
    assert len(db.get_bank_questions([wid], per_word=5)) == 2


def test_least_shown_first(fresh_db):
    wid = studyable_word()
    a = db.add_bank_question(wid, "cloze", "B1", Q("First kind ___ of text."))
    b = db.add_bank_question(wid, "synonym", "B1", Q("Second very different one?"))
    db.record_bank_answer(a, False)
    assert db.get_bank_questions([wid])[0]["id"] == b


def test_empty_bank_returns_empty_so_caller_asks_gemini(fresh_db):
    wid = studyable_word()
    assert db.get_bank_questions([wid]) == [] and db.get_bank_questions([]) == []


# ---------------- CASCADE ----------------

def test_deleting_word_deletes_questions_and_log(fresh_db):
    wid = studyable_word()
    other = studyable_word("pear")
    q1 = db.add_bank_question(wid, "cloze", "B1", Q())
    q2 = db.add_bank_question(other, "cloze", "B1", Q("Other ___ sentence here."))
    db.record_bank_answer(q1, False)
    db.delete_word(wid)
    conn = db.get_connection()
    assert [r[0] for r in conn.execute("SELECT id FROM question_bank")] == [q2]
    assert conn.execute("SELECT COUNT(*) FROM question_log").fetchone()[0] == 0
    conn.close()


# ---------------- statistika ----------------

def test_log_question_answer_validates(fresh_db):
    wid = studyable_word()
    db.log_question_answer(wid, "cloze", True)
    with pytest.raises(ValueError):
        db.log_question_answer(wid, "essay", True)
    with pytest.raises(ValueError):
        db.log_question_answer(999, "cloze", True)


def test_question_stats(fresh_db):
    apple = studyable_word("apple", "Kitob")
    pear = studyable_word("pear", "IELTS")
    for _ in range(3):
        db.log_question_answer(apple, "cloze", False)
    db.log_question_answer(apple, "cloze", True)
    db.log_question_answer(pear, "synonym", True)
    db.log_question_answer(pear, "synonym", True)
    s = db.get_question_stats(weeks=3)
    assert [(t["type"], t["percent"]) for t in s["by_type"]] == [("cloze", 25), ("synonym", 100)]   # zaifi birinchi
    assert s["worst_words"] == [{"word_id": apple, "word": "apple", "wrong": 3, "total": 4}]
    assert {c["category"]: c["percent"] for c in s["by_category"]} == {"Kitob": 25, "IELTS": 100}
    assert len(s["weekly"]) == 3 and s["weekly"][-1]["total"] == 6 and s["weekly"][-1]["percent"] == 50
    assert s["weekly"][0]["total"] == 0


def test_question_stats_empty(fresh_db):
    s = db.get_question_stats()
    assert s["by_type"] == [] and s["worst_words"] == [] and s["by_category"] == []
    assert len(s["weekly"]) == 8 and all(w["total"] == 0 for w in s["weekly"])


def test_weekly_growth_uses_weeks(fresh_db, monkeypatch):
    wid = studyable_word()
    base = datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc)           # chorshanba
    monkeypatch.setattr(db, "_now_utc", lambda: base - timedelta(days=14))
    db.log_question_answer(wid, "cloze", False)
    monkeypatch.setattr(db, "_now_utc", lambda: base)
    db.log_question_answer(wid, "cloze", True)
    w = db.get_question_stats(weeks=3)["weekly"]
    assert [x["percent"] for x in w] == [0, 0, 100] and [x["total"] for x in w] == [1, 0, 1]
