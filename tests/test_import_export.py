"""import_export.py sinovlari (CLAUDE-3): eksport, import, xavfsizlik, navbat."""
from datetime import datetime, timedelta, timezone

import pytest

from app import database as db
from app import import_export as ie


def _write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


def _all_words():
    conn = db.get_connection()
    try:
        return conn.execute("SELECT * FROM words ORDER BY id").fetchall()
    finally:
        conn.close()


# --- eksport A / B ---------------------------------------------------------

def test_export_a_columns(fresh_db, tmp_path):
    db.add_word("apple", "olma", "I eat an apple.", "Food")
    db.add_word("book", "kitob")
    out = tmp_path / "a.csv"
    assert ie.export_words(out, ie.MODE_WORDS) == 2
    first = out.read_text(encoding="utf-8-sig").splitlines()[0]
    assert first == "word,translation,example_sentence,category"


def test_export_b_columns_and_tsv(fresh_db, tmp_path):
    db.add_word("apple", "olma")
    out = tmp_path / "b.tsv"
    ie.export_words(out, ie.MODE_BACKUP)
    header = out.read_text(encoding="utf-8-sig").splitlines()[0].split("\t")
    assert header == ie.EXPORT_B_COLUMNS


def test_export_rejects_bad_extension_and_mode(fresh_db, tmp_path):
    with pytest.raises(ValueError):
        ie.export_words(tmp_path / "x.txt")
    with pytest.raises(ValueError):
        ie.export_words(tmp_path / "x.csv", "nimadir")


# --- aylanma yo'l (export -> import bir xil) --------------------------------

def test_roundtrip_words_only(fresh_db, tmp_path):
    db.add_word("apple", "olma", "I eat an apple.", "Food")
    db.add_word("o'zbek, tili", "uzbek \"language\"", "", "Umumiy")   # vergul va qo'shtirnoq
    before = [(r["word"], r["translation"], r["example"] or "", r["category"]) for r in _all_words()]
    out = tmp_path / "a.csv"
    ie.export_words(out)
    conn = db.get_connection(); conn.execute("DELETE FROM words"); conn.commit(); conn.close()
    res = ie.import_words(out)
    assert res.error is None and res.added == 2
    after = [(r["word"], r["translation"], r["example"] or "", r["category"]) for r in _all_words()]
    assert sorted(before) == sorted(after)


def test_roundtrip_backup_restores_fsrs_and_spell(fresh_db, tmp_path):
    wid = db.add_word("apple", "olma")
    db.review_word(wid, "good")
    db.set_spell_status(wid, db.SPELL_OK)
    orig = db.get_word(wid)
    out = tmp_path / "b.csv"
    ie.export_words(out, ie.MODE_BACKUP)
    conn = db.get_connection(); conn.execute("DELETE FROM words"); conn.commit(); conn.close()
    res = ie.import_words(out)
    assert res.added == 1
    new = _all_words()[0]
    for col in ("state", "step", "stability", "difficulty", "card_id"):
        assert new[col] == orig[col]
    assert datetime.fromisoformat(new["due"]) == datetime.fromisoformat(orig["due"])
    assert new["spell_status"] == db.SPELL_OK          # tiklangan so'z kartalardan chiqib ketmaydi
    assert db._row_to_card(new) is not None
    assert ie.pending_count(ie.TASK_SPELL) == 0         # tekshirish kerak emas
    assert ie.pending_count(ie.TASK_AUDIO) == 1


# --- import qoidalari -------------------------------------------------------

def test_import_sets_unchecked_and_queues(fresh_db, tmp_path):
    p = _write(tmp_path, "w.csv", "word,translation\napple,olma\nbook,kitob\n")
    res = ie.import_words(p)
    assert (res.added, res.duplicates, res.invalid, res.error) == (2, 0, 0, None)
    assert all(r["spell_status"] == "unchecked" and r["audio_source"] == "none" for r in _all_words())
    assert ie.pending_count(ie.TASK_SPELL) == 2 and ie.pending_count(ie.TASK_AUDIO) == 2
    assert res.queued == 4


def test_import_duplicates_db_and_file_case_insensitive(fresh_db, tmp_path):
    db.add_word("Apple", "olma")
    p = _write(tmp_path, "w.csv", "word,translation\napple,olma\nbook,kitob\nBOOK,kitob\n")
    res = ie.import_words(p)
    assert (res.added, res.duplicates) == (1, 2)


def test_import_invalid_rows_reported(fresh_db, tmp_path):
    p = _write(tmp_path, "w.csv", "word,translation\n,olma\nbook,\nok,yaxshi\n")
    res = ie.import_words(p)
    assert res.added == 1 and res.invalid == 2
    assert [n for n, _ in res.invalid_rows] == [2, 3]


def test_import_header_aliases_and_default_category(fresh_db, tmp_path):
    p = _write(tmp_path, "w.csv", "Word, Translation ,Example\nsun,quyosh,The sun.\n")
    res = ie.import_words(p)
    row = _all_words()[0]
    assert res.added == 1 and row["example"] == "The sun." and row["category"] == "Umumiy"


def test_import_assigns_packs_of_20(fresh_db, tmp_path):
    lines = "word,translation,category\n" + "\n".join(f"w{i},t{i},Test" for i in range(45))
    ie.import_words(_write(tmp_path, "w.csv", lines))
    packs = [r["pack"] for r in _all_words()]
    assert packs.count(1) == 20 and packs.count(2) == 20 and packs.count(3) == 5


def test_import_tsv(fresh_db, tmp_path):
    p = _write(tmp_path, "w.tsv", "word\ttranslation\nsun\tquyosh, nur\n")
    res = ie.import_words(p)
    assert res.added == 1 and _all_words()[0]["translation"] == "quyosh, nur"


def test_import_bad_fsrs_values_become_new_word(fresh_db, tmp_path):
    p = _write(tmp_path, "b.csv",
               "word,translation,fsrs_state,fsrs_stability,fsrs_difficulty,due_date\n"
               "a,b,9,-1,99,not-a-date\n")
    res = ie.import_words(p)
    row = _all_words()[0]
    assert res.added == 1 and row["card_id"] is None and row["state"] is None


# --- xavfsizlik / buzilgan fayllar -----------------------------------------

def test_rejects_wrong_extension(fresh_db, tmp_path):
    p = _write(tmp_path, "w.txt", "word,translation\na,b\n")
    assert "csv" in ie.import_words(p).error.lower()
    assert _all_words() == []


def test_rejects_file_over_5mb(fresh_db, tmp_path):
    p = tmp_path / "big.csv"
    p.write_text("word,translation\n" + "a,b\n" * 1_800_000, encoding="utf-8")
    assert p.stat().st_size > ie.MAX_FILE_BYTES
    res = ie.import_words(p)
    assert res.error and "5 MB" in res.error and _all_words() == []


def test_rejects_more_than_10000_rows(fresh_db, tmp_path):
    p = _write(tmp_path, "many.csv",
               "word,translation\n" + "\n".join(f"w{i},t" for i in range(ie.MAX_ROWS + 1)))
    res = ie.import_words(p)
    assert res.error and "10000" in res.error and _all_words() == []


def test_accepts_exactly_10000_rows(fresh_db, tmp_path):
    p = _write(tmp_path, "ok.csv", "word,translation\n" + "\n".join(f"w{i},t" for i in range(ie.MAX_ROWS)))
    res = ie.import_words(p)
    assert res.error is None and res.added == ie.MAX_ROWS


@pytest.mark.parametrize("content", [
    b"",                                  # bo'sh
    b"\xff\xfe\x00bad",                   # UTF-8 emas / ikkilik
    b"word,translation\n\x00\x00\x00\n",  # NUL baytlar
    b"faqat,boshqa,ustunlar\n1,2,3\n",    # majburiy ustun yo'q
    b"\n\n\n",                            # sarlavhasiz
])
def test_broken_files_do_not_crash(fresh_db, tmp_path, content):
    p = tmp_path / "bad.csv"
    p.write_bytes(content)
    res = ie.import_words(p)
    assert res.error and res.added == 0 and _all_words() == []


def test_missing_file_does_not_crash(fresh_db, tmp_path):
    assert ie.import_words(tmp_path / "yoq.csv").error


def test_formula_like_text_stored_as_plain_text(fresh_db, tmp_path):
    # eval/exec yo'q: "=1+1" va "__import__('os')" oddiy matn bo'lib qoladi
    p = _write(tmp_path, "w.csv", "word,translation\nx,=1+1\ny,__import__('os').system('echo')\n")
    ie.import_words(p)
    rows = _all_words()
    assert rows[0]["translation"] == "=1+1" and rows[1]["translation"].startswith("__import__")


def test_failed_import_rolls_back(fresh_db, tmp_path, monkeypatch):
    p = _write(tmp_path, "w.csv", "word,translation\na,b\nc,d\n")
    real = db._next_pack
    calls = {"n": 0}

    def boom(conn, category, exclude_id=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("kutilmagan xato")
        return real(conn, category, exclude_id)

    monkeypatch.setattr(db, "_next_pack", boom)
    res = ie.import_words(p)
    assert res.error and _all_words() == [] and ie.pending_count() == 0


# --- fon navbati -------------------------------------------------------------

def _import3(tmp_path):
    ie.import_words(_write(tmp_path, "w.csv", "word,translation\na,1\nb,2\nc,3\n"))


def test_queue_processes_spell_in_batches_and_audio(fresh_db, tmp_path):
    _import3(tmp_path)
    batches, audio, progress = [], [], []

    def spell(rows):
        batches.append(len(rows))
        return {r["id"]: ("suspect" if r["word"] == "b" else "ok") for r in rows}

    rep = ie.process_queue(spell, audio.append, progress_cb=lambda d, t: progress.append((d, t)),
                           batch_size=2)
    assert batches == [2, 1]
    assert len(audio) == 3 and rep.remaining == 0 and rep.done == rep.total == 6
    assert progress[0] == (0, 6) and progress[-1] == (6, 6)
    status = {r["word"]: r["spell_status"] for r in _all_words()}
    assert status == {"a": "ok", "b": "suspect", "c": "ok"}
    assert ie.progress_text(45, 200) == "Tekshirilmoqda: 45/200"


def test_queue_persists_when_provider_fails_and_resumes(fresh_db, tmp_path):
    _import3(tmp_path)

    def broken(rows):
        raise RuntimeError("internet yo'q")

    rep = ie.process_queue(broken, None)
    assert rep.errors and rep.remaining == 6            # hech narsa yo'qolmadi
    rep2 = ie.process_queue(lambda rows: {r["id"]: "ok" for r in rows}, lambda wid: None)
    assert rep2.remaining == 0                            # keyingi ishga tushirishda davom etdi


def test_queue_survives_restart_data_in_db(fresh_db, tmp_path):
    _import3(tmp_path)
    assert ie.pending_count() == 6                        # alohida ulanish ochilganda ham bor


def test_queue_stop_request(fresh_db, tmp_path):
    _import3(tmp_path)
    rep = ie.process_queue(lambda rows: {r["id"]: "ok" for r in rows}, None,
                           should_stop=lambda: True)
    assert rep.stopped and rep.remaining == 6


def test_queue_deleted_word_cascades(fresh_db, tmp_path):
    _import3(tmp_path)
    wid = _all_words()[0]["id"]
    db.delete_word(wid)
    assert ie.pending_count() == 4


# --- mahalliy imlo tekshiruvi --------------------------------------------------

def test_local_spell_batch(fresh_db, tmp_path):
    pytest.importorskip("spellchecker")
    ie.import_words(_write(tmp_path, "w.csv",
        "word,translation\napple,olma\naple,xato\nlook up,qidirmoq\nwell-known,mashhur\n123,raqam\n"))
    rows = _all_words()
    verdict = ie.local_spell_batch(rows)
    by_word = {r["word"]: verdict[r["id"]] for r in rows}
    assert by_word == {"apple": "ok", "aple": "suspect", "look up": "ok",
                       "well-known": "ok", "123": "ok"}   # CLAUDE-2 qoidasi: raqamda inglizcha so'z yo'q


def test_default_queue_with_local_spell_makes_words_studyable(fresh_db, tmp_path):
    pytest.importorskip("spellchecker")
    ie.import_words(_write(tmp_path, "w.csv", "word,translation\napple,olma\nbook,kitob\n"))
    assert [db.is_studyable(r) for r in _all_words()] == [False, False]   # tekshirilguncha chiqarib tashlangan
    ie.process_queue(ie.local_spell_batch, None)
    assert [db.is_studyable(r) for r in _all_words()] == [True, True]


# --- AI batch bilan imlo tekshiruvi (spell_batch) ----------------------------------

@pytest.fixture
def ai_words(fresh_db, tmp_path):
    pytest.importorskip("spellchecker")
    pytest.importorskip("google.genai")
    ie.import_words(_write(tmp_path, "w.csv",
                           "word,translation,example_sentence\napple,olma,I eat an apple.\n"
                           "aple,xato,\nWi-Fi,simsiz tarmoq,\n"))
    return _all_words()


def _by_word(rows, verdict):
    return {r["word"]: verdict[r["id"]] for r in rows}


def test_spell_batch_without_key_is_local_only(ai_words, monkeypatch):
    from app import gemini_service
    monkeypatch.setattr(gemini_service, "check_spelling_batch",
                        lambda e: pytest.fail("kalit yo'q: AI chaqirilmasligi kerak"))
    v = _by_word(ai_words, ie.spell_batch(ai_words))
    assert v["apple"] == "ok" and v["aple"] == "suspect"


def test_spell_batch_ai_overrides_local(ai_words, monkeypatch):
    from app import gemini_service
    db.set_setting("gemini_api_key", "KEY")
    seen = {}

    def fake(entries):
        seen["n"] = len(entries)
        seen["unknown"] = {e["word"]: e["local_unknown"] for e in entries}
        ids = {e["word"]: e["id"] for e in entries}
        return {ids["apple"]: {"word_ok": True, "translation_ok": False, "example_ok": True},   # tarjima mos emas
                ids["Wi-Fi"]: {"word_ok": True, "translation_ok": True, "example_ok": True},     # lug'at bilmaydi, AI biladi
                ids["aple"]: {"word_ok": False}}

    monkeypatch.setattr(gemini_service, "check_spelling_batch", fake)
    v = _by_word(ai_words, ie.spell_batch(ai_words))
    assert seen["n"] == 3                                   # hammasi BITTA so'rovda
    assert seen["unknown"]["aple"] == ["aple"]
    assert v == {"apple": "suspect", "aple": "suspect", "Wi-Fi": "ok"}


def test_spell_batch_ai_failure_falls_back_to_local(ai_words, monkeypatch):
    from app import gemini_service
    db.set_setting("gemini_api_key", "KEY")

    def boom(entries):
        raise RuntimeError("503")

    monkeypatch.setattr(gemini_service, "check_spelling_batch", boom)
    v = _by_word(ai_words, ie.spell_batch(ai_words))
    assert v["apple"] == "ok" and v["aple"] == "suspect"


def test_spell_batch_ai_disabled_setting(ai_words, monkeypatch):
    from app import gemini_service
    db.set_setting("gemini_api_key", "KEY")
    db.set_setting("spell_check_ai", "0")
    monkeypatch.setattr(gemini_service, "check_spelling_batch",
                        lambda e: pytest.fail("spell_check_ai=0: AI chaqirilmasligi kerak"))
    ie.spell_batch(ai_words)


def test_spell_batch_ignores_garbage_ai_entries(ai_words, monkeypatch):
    from app import gemini_service
    db.set_setting("gemini_api_key", "KEY")
    monkeypatch.setattr(gemini_service, "check_spelling_batch",
                        lambda e: {99999: {"word_ok": False}, e[0]["id"]: {"word_ok": "yes"}})
    v = _by_word(ai_words, ie.spell_batch(ai_words))
    assert v["apple"] == "ok" and v["aple"] == "suspect"    # yaroqsiz javoblar e'tiborga olinmadi


def test_queue_with_spell_batch_marks_studyable(ai_words):
    ie.process_queue(ie.spell_batch, None)
    st = {r["word"]: r["spell_status"] for r in _all_words()}
    assert st["apple"] == "ok" and st["aple"] == "suspect"


# --- gemini_service.check_spelling_batch ------------------------------------------

def test_check_spelling_batch_parsing(fresh_db, monkeypatch):
    pytest.importorskip("google.genai")
    from app import gemini_service
    captured = {}

    def fake_generate(system_prompt, contents):
        captured["prompt"], captured["contents"] = system_prompt, contents
        return {"results": [{"id": 1, "word_ok": True}, {"id": "2", "word_ok": False},
                            {"id": "x", "word_ok": True}, "junk", {"word_ok": True}]}

    monkeypatch.setattr(gemini_service, "_generate_json", fake_generate)
    out = gemini_service.check_spelling_batch([{"id": 1, "word": "a", "translation": "b",
                                                "example": "", "local_unknown": []}])
    assert set(out) == {1, 2} and out[2]["word_ok"] is False
    assert "spelling" in captured["prompt"] and '"id": 1' in captured["contents"]


def test_check_spelling_batch_bad_shapes(fresh_db, monkeypatch):
    pytest.importorskip("google.genai")
    from app import gemini_service
    for bad in ({}, [], {"results": "x"}, None):
        monkeypatch.setattr(gemini_service, "_generate_json", lambda s, c, b=bad: b)
        assert gemini_service.check_spelling_batch([]) == {}


# --- tekshirilmay qolgan so'zlar (so'z qo'shish sahifasidan) ----------------------

def test_enqueue_unchecked_adds_only_unchecked_and_is_idempotent(fresh_db):
    a = db.add_word("apple", "olma")            # unchecked (so'z qo'shish sahifasi tekshirmagan)
    b = db.add_word("book", "kitob")
    db.set_spell_status(b, db.SPELL_OK)
    assert ie.enqueue_unchecked() == 1
    assert ie.enqueue_unchecked() == 0          # ikki marta qo'shilmaydi
    conn = db.get_connection()
    ids = [r["word_id"] for r in conn.execute("SELECT word_id FROM import_queue WHERE task='spell'")]
    conn.close()
    assert ids == [a]


def test_enqueue_unchecked_skips_words_already_in_import_queue(fresh_db, tmp_path):
    ie.import_words(_write(tmp_path, "w.csv", "word,translation\napple,olma\n"))
    assert ie.pending_count(ie.TASK_SPELL) == 1
    assert ie.enqueue_unchecked() == 0


def test_stuck_unchecked_word_becomes_studyable_after_queue(fresh_db):
    pytest.importorskip("spellchecker")
    wid = db.add_word("apple", "olma")
    assert db.is_studyable(db.get_word(wid)) is False     # xatoning o'zi: kartada ko'rinmaydi
    ie.enqueue_unchecked()
    ie.process_queue(ie.local_spell_batch, None)
    assert db.is_studyable(db.get_word(wid)) is True
