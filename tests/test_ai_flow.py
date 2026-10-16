"""AI mashq oqimi (2-bosqich): mahalliy tekshiruv, ommaviy baholash, xatolar, prefetch, skip."""
import threading

import pytest

from app import database as db
from app import questions as qs
from tests.test_ui import (qapp, boxes, mock_gemini, win, pump, wait_all,  # noqa: F401
                           add_words)


def ai_rows(source="ai"):
    with db.get_connection() as c:
        return c.execute("SELECT * FROM history WHERE source=? ORDER BY id", (source,)).fetchall()


def drive(ai, qapp, text="my own answer", wrong=False):
    """Barcha savollarga javob beradi (MCQ - to'g'ri/noto'g'ri variant, matn - to'g'ri javob yoki `text`)."""
    guard = 0
    while ai.current_index < len(ai.questions) and guard < 100:
        guard += 1
        q = ai.questions[ai.current_index]
        if qs.is_mcq(q):
            idx = q["correct"]
            if wrong:
                idx = (idx + 1) % len(q["options"])
            ai._option_buttons[idx].click()
        else:
            ans = text if (q["checker"] == "ai" or wrong) else (q["answer"] or q["word"])
            if wrong and q["checker"] == "local":
                ans = "zzzzzz"
            ai.answer_input.setPlainText(ans)
            ai.submit_btn.click()
        ai.submit_btn.click()          # "Keyingi savol" / "Yakunlash"
        pump(qapp, 10)


def make_seen(n, category="Kitob", rating="good"):
    ids = add_words(n, category)
    for i in ids:
        db.review_word(i, rating)
    return ids


def start_pack(win, qapp, category="Kitob", pack=1):
    ai = win.pages["ai_practice"]
    assert ai.start_for_pack(category, pack)
    wait_all(qapp, ai)
    return ai


def test_easy_flow_local_only_no_batch(win, qapp, mock_gemini):
    make_seen(5)
    ai = start_pack(win, qapp)
    assert len(mock_gemini["gen"]) == 1 and mock_gemini["gen"][0]["level"] == "B1"
    drive(ai, qapp)
    wait_all(qapp, ai)
    assert mock_gemini["grade"] == []
    rows = ai_rows()
    assert len(rows) == len(ai.questions) >= 3
    assert all(r["rating"].lower() in ("good", "easy") for r in rows)
    assert not ai.finish_btn.isHidden()


def test_wrong_answers_give_again(win, qapp, mock_gemini):
    make_seen(4)
    ai = start_pack(win, qapp)
    drive(ai, qapp, wrong=True)
    wait_all(qapp, ai)
    n = len(ai.questions)
    assert n >= 2 and [r["rating"].lower() for r in ai_rows()] == ["again"] * n


def test_hard_words_use_open_types_single_batch(win, qapp, mock_gemini):
    make_seen(6, rating="easy")
    ai = start_pack(win, qapp)
    types = mock_gemini["gen"][0]["types"]
    assert all(t in qs.HARD_TYPES or t == "translate_word" for t in types)
    drive(ai, qapp)
    wait_all(qapp, ai)
    open_n = sum(1 for q in ai.questions if q["checker"] == "ai")
    if open_n:
        assert len(mock_gemini["grade"]) == 1
        assert len(mock_gemini["grade"][0]["entries"]) == open_n
    assert len(ai_rows()) == len(ai.questions) >= 3


def test_level_setting_passed(win, qapp, mock_gemini):
    db.set_setting("english_level", "C1")
    make_seen(3)
    start_pack(win, qapp)
    assert mock_gemini["gen"][0]["level"] == "C1"


def _open_flow(win, qapp, mock_gemini):
    """Ochiq javobli savollar bo'lishi uchun: o'zi yozadigan turlar majburlanadi."""
    make_seen(3, rating="easy")
    ai = win.pages["ai_practice"]
    real = qs.pick_type
    qs.pick_type = lambda row, rng, avoid=None: "sentence"
    try:
        assert ai.start_for_pack("Kitob", 1)
        wait_all(qapp, ai)
    finally:
        qs.pick_type = real
    return ai


def test_batch_failure_then_retry_no_double_review(win, qapp, mock_gemini):
    ai = _open_flow(win, qapp, mock_gemini)
    mock_gemini["state"]["grade_error"] = "503 overloaded"
    drive(ai, qapp)
    wait_all(qapp, ai)
    assert not ai.retry_btn.isHidden()
    n = len(ai.questions)
    assert len(ai.pending) == n
    before = len(ai_rows())
    mock_gemini["state"]["grade_error"] = None
    ai.retry_btn.click()
    wait_all(qapp, ai)
    assert ai.pending == []
    rows = ai_rows()
    assert len(rows) == before + n
    assert all(r["rating"].lower() == "easy" for r in rows[-n:])      # sentence + natural -> easy
    assert len(db.get_connection().execute("SELECT id FROM ai_sessions").fetchall()) == 1


def test_missing_results_stay_pending(win, qapp, mock_gemini):
    ai = _open_flow(win, qapp, mock_gemini)
    mock_gemini["state"]["grade_missing"] = {2}
    drive(ai, qapp)
    wait_all(qapp, ai)
    assert len(ai.pending) == 1 and not ai.retry_btn.isHidden()
    mock_gemini["state"]["grade_missing"] = set()
    ai.retry_btn.click()
    wait_all(qapp, ai)
    assert ai.pending == [] and len(ai_rows()) == len(ai.questions)


def test_generation_failure_falls_back_local(win, qapp, mock_gemini):
    make_seen(4)
    mock_gemini["state"]["gen_error"] = "boom"
    ai = start_pack(win, qapp)
    assert "Mahalliy" in ai.status_label.text()
    n = len(ai.questions)
    assert n >= 2 and all(q["checker"] == "local" for q in ai.questions)
    drive(ai, qapp)
    assert len(ai_rows()) == n


def test_no_api_key_no_gen_call(win, qapp, mock_gemini):
    db.set_setting("gemini_api_key", "")
    make_seen(3)
    ai = start_pack(win, qapp)
    assert mock_gemini["gen"] == []
    assert len(ai.questions) >= 2


def test_garbage_question_replaced_locally(win, qapp, mock_gemini):
    ids = make_seen(4)
    mock_gemini["state"]["garbage_words"] = {r["word"] for r in db.get_words_by_ids(ids)}
    ai = start_pack(win, qapp)
    assert len(ai.questions) >= 2 and all(q["checker"] == "local" for q in ai.questions)
    assert all(q["question"] for q in ai.questions)


def test_leech_flagged_in_payload(win, qapp, mock_gemini):
    ids = make_seen(3)
    for i in ids:
        for _ in range(4):
            db.review_word(i, "again")
    start_pack(win, qapp)
    assert len(mock_gemini["gen"][0]["leech"]) >= 2


def test_limits_section_and_free_mode(win, qapp, mock_gemini):
    make_seen(30, "Kitob")
    make_seen(10, "IELTS")
    ai = win.pages["ai_practice"]
    assert ai.start_for_section("Kitob")
    wait_all(qapp, ai)
    assert len(ai.questions) == 20
    db.set_setting("max_questions", "8")
    assert ai.start_free_chat_mode()
    wait_all(qapp, ai)
    assert len(ai.questions) == 8 and mock_gemini["gen"][-1]["mode"] == "B"


def test_skip_during_generation(win, qapp, mock_gemini):
    make_seen(3)
    gate = threading.Event()
    mock_gemini["state"]["gate"] = gate
    ai = win.pages["ai_practice"]
    assert ai.start_for_pack("Kitob", 1)
    pump(qapp, 50)
    assert not ai.skip_btn.isHidden() and ai.questions == []
    ai.skip_btn.click()
    gate.set()
    wait_all(qapp, ai)
    assert ai.questions == [] and ai_rows() == []
    assert win.stack.currentWidget() is win.pages["home"]


def test_prefetch_during_cards_and_flow(win, qapp, boxes, mock_gemini):
    add_words(3, "Kitob")
    fcp = win.pages["flashcard"]
    fcp.start_session(db.get_pack_words("Kitob", 1), "Kitob", 1)
    win.show_page("flashcard")
    guard = 0
    while fcp.current_id is not None and guard < 30:
        guard += 1
        fcp.reveal_answer()
        fcp.rate_current("good")
        pump(qapp, 30)
    ai = win.pages["ai_practice"]
    wait_all(qapp, ai)
    pump(qapp, 100)
    assert len(mock_gemini["gen"]) == 1          # bitta so'rov (prefetch), tugaganda yangisi yo'q
    assert win.stack.currentWidget() is ai and len(ai.questions) == 3


def test_small_helpers_level_leech_distractors(fresh_db):
    assert db.get_english_level() == "B1"
    db.set_setting("english_level", "C2")
    assert db.get_english_level() == "C2"
    ids = add_words(6, "Kitob")
    assert db.get_leech_ids() == set()
    for _ in range(db.LEECH_THRESHOLD):
        db.review_word(ids[0], "again")
    assert db.get_leech_ids() == {ids[0]}
    d = db.get_distractors(ids[0], "translation", 3)
    assert len(d) == 3 and "t0" not in d
