import random
from datetime import datetime, timedelta, timezone

import pytest

from app import database as db
from app import questions as qs


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def fixed_now(monkeypatch, dt):
    monkeypatch.setattr(db, "_now_utc", lambda: dt)


def mkword(word="meticulous", tr="sinchkov", ex="", cat="K"):
    wid = db.add_word(word, tr, ex, cat)
    return db.get_word(wid)


# ---------------- matn yordamchilari ----------------

def test_norm_and_edit_distance():
    assert qs.norm("  Look-Up!! ") == "look up"
    assert qs.within_one_edit("meticulous", "meticulus")      # 1 harf tushgan
    assert qs.within_one_edit("abcde", "abXde")               # 1 almashgan
    assert not qs.within_one_edit("abcde", "abXYe")
    assert not qs.within_one_edit("abc", "abcde")


def test_check_text_answer():
    assert qs.check_text_answer("Meticulous.", "meticulous") == "exact"
    assert qs.check_text_answer("meticulus", "meticulous") == "typo"
    assert qs.check_text_answer("cat", "cut") == "wrong"          # qisqa so'z - imlo yumshatilmaydi
    assert qs.check_text_answer("", "x") == "wrong"
    assert qs.check_text_answer("ran", "run", accept=["ran"]) == "exact"


# ---------------- tur tanlash ----------------

def test_pick_type_easy_vs_hard(fresh_db, monkeypatch):
    base = utc(2026, 10, 5, 10)
    fixed_now(monkeypatch, base)
    new = mkword("a1", "t")
    learning = mkword("a2", "t")
    strong = mkword("a3", "t")
    db.review_word(learning["id"], "again")                 # Learning
    db.review_word(strong["id"], "easy")                    # Review, ehtimol ~1.0
    rng = random.Random(0)
    easy_pool = set(qs.EASY_TYPES) | {"translate_word"}
    hard_pool = set(qs.HARD_TYPES) | {"translate_word"}
    for _ in range(40):
        assert qs.pick_type(db.get_word(new["id"]), rng) in easy_pool
        assert qs.pick_type(db.get_word(learning["id"]), rng) in easy_pool
        assert qs.pick_type(db.get_word(strong["id"]), rng) in hard_pool
    types = {qs.pick_type(db.get_word(strong["id"]), rng) for _ in range(80)}
    assert types == hard_pool


def test_forgotten_review_word_is_easy(fresh_db, monkeypatch):
    base = utc(2026, 10, 5, 10)
    fixed_now(monkeypatch, base)
    w = mkword("a1", "t")
    db.review_word(w["id"], "easy")
    fixed_now(monkeypatch, base + timedelta(days=2000))      # ehtimol past -> yengil turlar
    assert not qs.is_hard_word(db.get_word(w["id"]))


def test_build_items_avoids_repeats(fresh_db):
    rows = [mkword(f"w{i}", "t") for i in range(12)]
    items = qs.build_items(rows, random.Random(1))
    assert [it["row"]["id"] for it in items] == [r["id"] for r in rows]
    assert all(items[i]["type"] != items[i + 1]["type"] for i in range(len(items) - 1))


# ---------------- validatsiya ----------------

@pytest.fixture
def row(fresh_db):
    return mkword()


def test_validate_mcq_types(row):
    raw = {"question": "What does X mean?", "options": ["a", "b", "c", "d"], "correct": 2}
    for t in ("meaning", "synonym"):
        q = qs.validate_question(raw, row, t, random.Random(3))
        assert q["checker"] == "local" and len(q["options"]) == 4
        assert q["options"][q["correct"]] == "c"             # aralashtirishdan keyin ham to'g'ri
    q = qs.validate_question({"question": "Q", "options": ["s1", "s2", "s3"], "correct": 0},
                             row, "correct_sentence")
    assert q["options"][q["correct"]] == "s1"


@pytest.mark.parametrize("bad", [
    {"question": "Q", "options": ["a", "b"], "correct": 0},                 # kam variant
    {"question": "Q", "options": ["a", "a", "b", "c"], "correct": 0},       # takror
    {"question": "Q", "options": ["a", "b", "c", "d"], "correct": 4},       # indeks tashqarida
    {"question": "Q", "options": ["a", "b", "c", "d"], "correct": True},    # bool
    {"question": "Q", "options": ["a", "", "c", "d"], "correct": 0},        # bo'sh variant
    {"question": "", "options": ["a", "b", "c", "d"], "correct": 0},        # savol yo'q
    {"question": "Q", "options": "abcd", "correct": 0},
])
def test_validate_mcq_rejects_bad(row, bad):
    assert qs.validate_question(bad, row, "meaning") is None


def test_validate_cloze(row):
    q = qs.validate_question({"question": "She is very _____ today.", "answer": "meticulous",
                              "accept": ["Meticulous"]}, row, "cloze")
    assert q["question"] == "She is very ___ today." and q["answer"] == "meticulous"
    assert qs.validate_question({"question": "No blank here."}, row, "cloze") is None
    q2 = qs.validate_question({"question": "A ___ b"}, row, "cloze")       # javob yo'q -> so'zning o'zi
    assert q2["answer"] == "meticulous"


def test_validate_open_types_and_tip(row):
    for t in ("sentence", "fix", "situation", "passage"):
        q = qs.validate_question({"question": "Write.", "tip": "Eslab qol", "examples": ["e1", "e2", "e3"]}, row, t)
        assert q["checker"] == "ai" and not qs.is_mcq(q)
        assert q["tip"] == "Eslab qol\n• e1\n• e2"
    assert qs.validate_question({"question": "Write."}, row, "translate_word") is None   # AI yaratmaydi
    assert qs.validate_question("matn", row, "sentence") is None


# ---------------- build_questions ----------------

def test_build_questions_fallbacks(fresh_db):
    rows = [mkword(f"w{i}", f"tarjima{i}", f"I use w{i} often.") for i in range(6)]
    items = [{"row": rows[0], "type": "meaning"}, {"row": rows[1], "type": "cloze"},
             {"row": rows[2], "type": "sentence"}, {"row": rows[3], "type": "passage"},
             {"row": rows[4], "type": "translate_word"}, {"row": rows[5], "type": "synonym"}]
    raw = {"passage": "",   # passage yo'q -> passage savoli mahalliyga almashadi
           "questions": [
               {"word": "W0", "question": "What?", "options": ["a", "b", "c", "d"], "correct": 1},
               {"word": "w1", "question": "no blank"},                       # yaroqsiz cloze
               {"word": "w2", "question": "Write about w2."},
               {"word": "w3", "question": "About the text?"},
               "axlat",
           ]}                                                                # w5 uchun umuman yo'q
    out, fb = qs.build_questions(items, raw, random.Random(0))
    assert len(out) == 6 and [q["word_id"] for q in out] == [r["id"] for r in rows]
    assert out[0]["type"] == "meaning" and out[0]["options"][out[0]["correct"]] == "b"
    assert out[2]["type"] == "sentence" and out[2]["checker"] == "ai"
    assert out[4]["type"] == "translate_word"
    assert fb == 3                                   # cloze, passage, synonym mahalliyga almashdi
    for i in (1, 3, 5):
        assert out[i]["checker"] == "local"


def test_build_questions_passage_attached(fresh_db):
    r = mkword("w", "t")
    out, fb = qs.build_questions([{"row": r, "type": "passage"}],
                                 {"passage": "Text.", "questions": [{"word": "w", "question": "Q?"}]})
    assert fb == 0 and out[0]["passage"] == "Text." and out[0]["checker"] == "ai"


def test_build_questions_garbage_input(fresh_db):
    r = mkword("w", "t")
    for raw in (None, "x", {}, {"questions": "bad"}):
        out, fb = qs.build_questions([{"row": r, "type": "sentence"}], raw)
        assert len(out) == 1 and fb == 1 and out[0]["checker"] == "local"


# ---------------- mahalliy savollar ----------------

def test_make_local_question_variants(fresh_db):
    rows = [mkword(f"w{i}", f"tarjima{i}", f"I like w{i} very much.") for i in range(6)]
    seen = set()
    for seed in range(60):
        q = qs.make_local_question(db.get_word(rows[0]["id"]), random.Random(seed))
        assert q["checker"] == "local" and q["word_id"] == rows[0]["id"]
        seen.add(q["type"])
        if q["type"] == "meaning":
            assert q["options"][q["correct"]] == "tarjima0" and len(set(q["options"])) == len(q["options"])
        if q["type"] == "cloze":
            assert "___" in q["question"] and q["answer"] == "w0"
    assert seen == {"translate_word", "meaning", "cloze"}


def test_local_question_single_word_only_translate(fresh_db):
    r = mkword("solo", "yolg'iz")
    for seed in range(10):
        assert qs.make_local_question(r, random.Random(seed))["type"] == "translate_word"


# ---------------- tekshirish va baho ----------------

def test_grade_local_mcq_and_typed(fresh_db):
    r = mkword("meticulous", "sinchkov")
    mcq = qs.validate_question({"question": "Q", "options": ["a", "b", "c", "d"], "correct": 0},
                               r, "meaning", random.Random(1))
    assert qs.grade_local(mcq, mcq["correct"])["rating"] == "good"
    wrong = qs.grade_local(mcq, (mcq["correct"] + 1) % 4)
    assert wrong["rating"] == "again" and not wrong["correct"] and "To'g'ri javob" in wrong["feedback"]
    tw = qs.make_translate_word(r)
    assert qs.grade_local(tw, "Meticulous")["rating"] == "good"
    typo = qs.grade_local(tw, "meticulus")
    assert typo["rating"] == "hard" and typo["correct"]
    assert qs.grade_local(tw, "careful")["rating"] == "again"
    with pytest.raises(ValueError):
        qs.grade_local({"checker": "ai"}, "x")


@pytest.mark.parametrize("qtype,res,expected", [
    ("sentence", {"word_ok": False, "grammar_ok": True, "natural": True}, "again"),
    ("sentence", {"word_ok": True, "grammar_ok": False, "natural": True}, "hard"),
    ("sentence", {"word_ok": True, "grammar_ok": True, "natural": False}, "good"),
    ("sentence", {"word_ok": True, "grammar_ok": True, "natural": True}, "easy"),
    ("situation", {"word_ok": True, "grammar_ok": True, "natural": True}, "easy"),
    ("fix", {"word_ok": True, "grammar_ok": True, "natural": True}, "good"),       # Easy faqat o'zi yozadigan turlarda
    ("passage", {"word_ok": True, "grammar_ok": True, "natural": True}, "good"),
    ("sentence", {"word_ok": True, "grammar_ok": True}, "good"),                   # natural yo'q
    ("sentence", {"word_ok": "yes", "grammar_ok": True}, None),                    # yaroqsiz
    ("sentence", {"grammar_ok": True}, None),
    ("sentence", None, None),
])
def test_rating_from_ai(qtype, res, expected):
    assert qs.rating_from_ai(qtype, res) == expected
