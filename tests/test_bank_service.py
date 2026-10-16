"""Savol bankini AI mashqqa ulash (CLAUDE-4): bank -> Gemini -> bankka saqlash."""
import random

import pytest

from app import bank_service as bs
from app import database as db
from app import questions as qs


def word(w="apple"):
    wid = db.add_word(w, "olma", category="Umumiy", example=f"I eat a {w} every day.")
    db.set_spell_status(wid, "ok")
    return wid


def rows(*ids):
    return db.get_words_by_ids(list(ids))


@pytest.fixture
def fixed_type(monkeypatch):
    """Savol turini aniq qilib qo'yadi (tasodifiy tanlov testni beqaror qilmasin)."""
    box = {"t": "meaning"}
    monkeypatch.setattr(qs, "pick_type", lambda row, rng, avoid=None: box["t"])
    return box


def raw_meaning(w="apple", text=None):
    return {"questions": [{"word": w, "type": "meaning",
                           "question": text or f"What does '{w}' mean in this context?",
                           "options": ["olma", "non", "suv", "sut"], "correct": 0}]}


def test_bank_empty_asks_gemini_and_saves(fresh_db, fixed_type):
    wid = word()
    plan = bs.plan_session(rows(wid), random.Random(1))
    assert plan.bank_count == 0
    assert len(plan.ai_payload()) == 1                 # Gemini kerak
    out, fb, saved = bs.finish_session(plan, raw_meaning())
    assert (len(out), fb, saved) == (1, 0, 1)
    assert out[0]["bank_id"] and out[0]["source"] == "ai"
    assert db.count_bank_questions(wid) == 1


def test_bank_has_question_gemini_not_needed(fresh_db, fixed_type):
    wid = word()
    bs.finish_session(bs.plan_session(rows(wid)), raw_meaning())
    plan = bs.plan_session(rows(wid))
    assert plan.bank_count == 1
    assert plan.ai_payload() == []                     # Gemini'ga hech narsa ketmaydi
    out, fb, saved = bs.finish_session(plan, {})
    assert (fb, saved) == (0, 0)
    q = out[0]
    assert q["source"] == "bank" and q["bank_id"]
    assert qs.grade_local(q, q["correct"])["correct"] is True
    assert qs.grade_local(q, (q["correct"] + 1) % 4)["correct"] is False


def test_gemini_failure_uses_local_and_saves_nothing(fresh_db, fixed_type):
    wid = word()
    for w in ("pear", "plum", "fig"):
        word(w)
    plan = bs.plan_session(rows(wid))
    out, fb, saved = bs.finish_session(plan, {})
    assert len(out) == 1 and fb == 1 and saved == 0
    assert out[0]["source"] == "local" and db.count_bank_questions(wid) == 0


def test_order_preserved_mixed_bank_and_gemini(fresh_db, fixed_type):
    a, b, c = word("apple"), word("pear"), word("plum")
    bs.finish_session(bs.plan_session(rows(b)), raw_meaning("pear"))   # faqat pear bankda
    plan = bs.plan_session(rows(a, b, c))
    assert plan.bank_count == 1 and len(plan.ai_payload()) == 2
    raw = {"questions": raw_meaning("apple")["questions"] + raw_meaning("plum")["questions"]}
    out, fb, saved = bs.finish_session(plan, raw)
    assert [q["word_id"] for q in out] == [a, b, c]
    assert [q["source"] for q in out] == ["ai", "bank", "ai"] and saved == 2


def test_translate_word_never_banked(fresh_db, fixed_type):
    fixed_type["t"] = "translate_word"
    wid = word()
    plan = bs.plan_session(rows(wid))
    assert plan.ai_payload() == []
    out, _, saved = bs.finish_session(plan, {})
    assert out[0]["type"] == "translate_word" and saved == 0
    assert db.count_bank_questions(wid) == 0


def test_open_types_never_banked(fresh_db, fixed_type):
    fixed_type["t"] = "sentence"
    wid = word()
    plan = bs.plan_session(rows(wid))
    raw = {"questions": [{"word": "apple", "type": "sentence", "question": "Write a sentence."}]}
    out, _, saved = bs.finish_session(plan, raw)
    assert out[0]["type"] == "sentence" and saved == 0
    assert db.count_bank_questions(wid) == 0


def test_cloze_roundtrip_text_answer(fresh_db, fixed_type):
    fixed_type["t"] = "cloze"
    wid = word()
    raw = {"questions": [{"word": "apple", "type": "cloze",
                          "question": "She bought a red ____ at the market.", "answer": "apple"}]}
    bs.finish_session(bs.plan_session(rows(wid)), raw)
    plan = bs.plan_session(rows(wid))
    assert plan.bank_count == 1
    q = bs.finish_session(plan, {})[0][0]
    assert q["source"] == "bank" and qs.grade_local(q, "apple")["correct"]
    assert not qs.grade_local(q, "banana")["correct"]


def test_same_question_not_duplicated(fresh_db, fixed_type):
    wid = word()
    bs.finish_session(bs.plan_session(rows(wid)), raw_meaning())
    fixed_type["t"] = "meaning"
    # bank bo'sh deb faraz qilib yana xuddi shu savol keladi -> dublikat saqlanmaydi
    plan = bs.plan_session(rows(wid))
    plan.items[0]["bank_q"] = None
    out, _, saved = bs.finish_session(plan, raw_meaning())
    assert saved == 0 and db.count_bank_questions(wid) == 1


def test_wrong_level_not_used(fresh_db, fixed_type):
    wid = word()
    db.add_bank_question(wid, "meaning", "C2", {"question": "q?", "options": ["a", "b", "c"],
                                                "correct": 0, "correct_answer": "a"})
    assert db.get_english_level() != "C2"
    assert bs.plan_session(rows(wid)).bank_count == 0


def test_unchecked_word_not_from_bank(fresh_db, fixed_type):
    wid = word()
    bs.finish_session(bs.plan_session(rows(wid)), raw_meaning())
    db.set_spell_status(wid, "unchecked")
    assert bs.plan_session(rows(wid)).bank_count == 0


def test_record_answer_bank_and_non_bank(fresh_db, fixed_type):
    wid = word()
    q = bs.finish_session(bs.plan_session(rows(wid)), raw_meaning())[0][0]
    bs.record_answer(q, True)
    conn = db.get_connection()
    assert conn.execute("SELECT shown_count, correct_count FROM question_bank").fetchone()[:] == (1, 1)
    conn.close()
    local = qs.make_translate_word(rows(wid)[0])
    bs.record_answer(local, False)
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM question_log WHERE bank_id IS NULL").fetchone()[0] == 1
    conn.close()


def test_three_correct_retires_and_bad_is_excluded(fresh_db, fixed_type):
    wid = word()
    q = bs.finish_session(bs.plan_session(rows(wid)), raw_meaning())[0][0]
    for _ in range(3):
        bs.record_answer(q, True)
    assert bs.plan_session(rows(wid)).bank_count == 0        # retired - kam chiqadi
    w2 = word("pear")
    q2 = bs.finish_session(bs.plan_session(rows(w2)), raw_meaning("pear"))[0][0]
    assert bs.mark_bad(q2) is True
    assert bs.plan_session(rows(w2)).bank_count == 0          # bad - qaytmaydi


def test_mark_bad_without_bank_id_is_noop(fresh_db):
    wid = word()
    assert bs.mark_bad(qs.make_translate_word(rows(wid)[0])) is False


def test_word_delete_removes_bank(fresh_db, fixed_type):
    wid = word()
    bs.finish_session(bs.plan_session(rows(wid)), raw_meaning())
    db.delete_word(wid)
    conn = db.get_connection()
    assert conn.execute("SELECT COUNT(*) FROM question_bank").fetchone()[0] == 0
    conn.close()
