import os
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from app import database as db


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def set_now(monkeypatch, dt):
    monkeypatch.setattr(db, "_now_utc", lambda: dt)


@pytest.fixture
def tz(monkeypatch):
    """TZ muhit o'zgaruvchisini vaqtincha almashtiradi."""
    old = os.environ.get("TZ")

    def _set(name):
        os.environ["TZ"] = name
        time.tzset()
    yield _set
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time.tzset()


# ---------------- 04:00 o'quv kuni ----------------

def test_study_day_boundary_0400_tashkent(tz):
    tz("Asia/Tashkent")  # UTC+5
    # 03:59 mahalliy (22:59 UTC) -> hali kechagi kun
    assert db.study_day_of(utc(2026, 10, 4, 22, 59)).isoformat() == "2026-10-04"
    # 04:00 mahalliy (23:00 UTC) -> yangi kun
    assert db.study_day_of(utc(2026, 10, 4, 23, 0)).isoformat() == "2026-10-05"


def test_study_day_boundary_0400_cairo(tz):
    tz("Africa/Cairo")  # oktyabrda UTC+3 (yoki +2)
    local_0359 = datetime(2026, 10, 5, 3, 59).astimezone()
    local_0400 = datetime(2026, 10, 5, 4, 0).astimezone()
    assert db.study_day_of(local_0359).isoformat() == "2026-10-04"
    assert db.study_day_of(local_0400).isoformat() == "2026-10-05"


def test_daily_new_limit_resets_at_0400(fresh_db, monkeypatch, tz):
    tz("Asia/Tashkent")
    db.set_setting("daily_new_limit", "2")
    for i in range(5):
        wid = db.add_word(f"a{i}", "t")
        db.set_spell_status(wid, db.SPELL_OK)         # D-008: tekshirilmagan so'z kartada qatnashmaydi
    set_now(monkeypatch, utc(2026, 10, 4, 22, 0))     # 03:00 mahalliy (kechagi kun)
    ws = db.get_study_words("Umumiy")
    assert len(ws) == 2
    for w in ws:
        db.review_word(w["id"], "good")
    assert db.new_slots_left_today() == 0
    set_now(monkeypatch, utc(2026, 10, 4, 22, 59))    # 03:59 - hali shu kun
    assert db.new_slots_left_today() == 0
    set_now(monkeypatch, utc(2026, 10, 4, 23, 1))     # 04:01 - yangi kun
    assert db.new_slots_left_today() == 2


def test_streak_and_reviews_per_day_use_study_day(fresh_db, monkeypatch, tz):
    tz("Asia/Tashkent")
    wid = db.add_word("x", "y")
    # 02:00 mahalliy (21:00 UTC oldingi kun) -> kechagi o'quv kuniga tegishli
    set_now(monkeypatch, utc(2026, 10, 4, 21, 0))
    db.review_word(wid, "good")
    set_now(monkeypatch, utc(2026, 10, 5, 0, 0))      # 05:00 mahalliy, 5-oktyabr kuni
    db.review_word(wid, "good")
    per_day = dict(db.get_reviews_per_day(3))
    assert per_day["04.10"] == 1 and per_day["05.10"] == 1
    assert db.get_current_streak() == 2


# ---------------- migratsiya ----------------

def test_migration_old_db_splits_packs_and_keeps_names(old_db):
    db.init_db()
    conn = db.get_connection()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert conn.execute("SELECT COUNT(*) FROM words").fetchone()[0] == 48
    cats = {r[0] for r in conn.execute("SELECT DISTINCT category FROM words")}
    assert cats == {"Umumiy", "IELTS"}
    sizes = [r[0] for r in conn.execute(
        "SELECT COUNT(*) FROM words WHERE category='Umumiy' GROUP BY pack ORDER BY pack")]
    assert sizes == [20, 20, 5]
    # tartib: eng eski so'zlar 1-bo'limchada
    first = conn.execute("SELECT word FROM words WHERE category='Umumiy' AND pack=1 ORDER BY id").fetchall()
    assert first[0]["word"] == "w00" and first[-1]["word"] == "w19"
    assert "duration_ms" in db._columns(conn, "history")
    conn.close()


def test_migration_is_idempotent(old_db):
    db.init_db()
    db.init_db()
    db.init_db()
    conn = db.get_connection()
    sizes = [r[0] for r in conn.execute(
        "SELECT COUNT(*) FROM words WHERE category='Umumiy' GROUP BY pack ORDER BY pack")]
    assert sizes == [20, 20, 5]
    conn.close()


def test_migration_makes_premigrate_backup(old_db):
    db.init_db()
    files = list(db.backups_dir().glob("sozlar_*_premigrate*.db"))
    assert len(files) == 1
    # zaxira ESKI sxemada - ya'ni migratsiyadan oldingi holat
    c = sqlite3.connect(files[0])
    assert c.execute("PRAGMA user_version").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM words").fetchone()[0] == 48
    c.close()


def test_fresh_db_created_without_backup(fresh_db):
    assert not db.backups_dir().exists() or not list(db.backups_dir().glob("*.db"))


# ---------------- bo'limcha ----------------

def test_21st_word_goes_to_new_pack(fresh_db):
    for i in range(20):
        db.add_word(f"w{i}", "t", category="Kitob")
    assert {w["pack"] for w in db.get_category_words("Kitob")} == {1}
    db.add_word("w20", "t", category="Kitob")
    assert db.get_pack_words("Kitob", 2)[0]["word"] == "w20"
    assert len(db.get_pack_words("Kitob", 1)) == 20


def test_empty_category_is_umumiy(fresh_db):
    wid = db.add_word("a", "b", category="   ")
    assert db.get_word(wid)["category"] == "Umumiy"
    wid2 = db.add_word("c", "d", category=None)
    assert db.get_word(wid2)["category"] == "Umumiy"


def test_deleted_slot_not_refilled_in_old_packs(fresh_db):
    ids = [db.add_word(f"w{i}", "t") for i in range(25)]   # 20 + 5
    db.delete_word(ids[0])                                  # 1-bo'limchadan o'chdi
    nid = db.add_word("new", "t")
    assert db.get_word(nid)["pack"] == 2                    # 1-bo'limcha to'ldirilmaydi


def test_update_word_moves_to_last_pack_of_new_section(fresh_db):
    for i in range(20):
        db.add_word(f"k{i}", "t", category="Kitob")
    db.add_word("k20", "t", category="Kitob")               # Kitob: 20 + 1
    wid = db.add_word("mover", "t", category="IELTS")
    db.update_word(wid, "mover", "t", "", "Kitob")
    w = db.get_word(wid)
    assert w["category"] == "Kitob" and w["pack"] == 2
    # bo'lim o'zgarmasa - bo'limcha ham o'zgarmaydi
    db.update_word(wid, "mover2", "t2", "", "Kitob")
    assert db.get_word(wid)["pack"] == 2


# ---------------- get_sections ----------------

def test_get_sections_counts_sum_to_total(fresh_db, monkeypatch):
    base = utc(2026, 10, 5, 10, 0)
    set_now(monkeypatch, base)
    ids = [db.add_word(f"w{i}", "t", category="Kitob") for i in range(30)]
    ids += [db.add_word(f"u{i}", "t") for i in range(4)]
    # 10 ta ko'riladi: 'good' -> Learning/Review, 'again' -> Learning
    for wid in ids[:6]:
        db.review_word(wid, "easy")
    for wid in ids[6:10]:
        db.review_word(wid, "again")
    # vaqtni 400 kun oldinga suramiz -> Review so'zlarning muddati o'tadi (qizil)
    set_now(monkeypatch, base + timedelta(days=400))
    secs = db.get_sections()
    names = [s["category"] for s in secs]
    assert names[0] == "Umumiy"
    for s in secs:
        assert s["blue"] + s["green"] + s["red"] == s["total"]
        assert sum(p["total"] for p in s["packs"]) == s["total"]
        for p in s["packs"]:
            assert p["blue"] + p["green"] + p["red"] == p["total"]
            assert p["total"] <= 20
        for k in ("blue", "green", "red"):
            assert s[k] == sum(p[k] for p in s["packs"])
    kitob = next(s for s in secs if s["category"] == "Kitob")
    assert kitob["total"] == 30 and [p["total"] for p in kitob["packs"]] == [20, 10]
    assert kitob["blue"] == 20 and kitob["red"] > 0


def test_bucket_rules(fresh_db, monkeypatch):
    base = utc(2026, 10, 5, 10, 0)
    set_now(monkeypatch, base)
    a = db.add_word("new", "t")
    assert db.word_bucket(db.get_word(a)) == "blue"
    db.review_word(a, "again")                           # Learning -> yashil
    assert db.word_bucket(db.get_word(a)) == "green"
    assert db.word_label(db.get_word(a)) == "O'rganilmoqda"


# ---------------- zaxira ----------------

def test_backups_keep_at_most_5(fresh_db, monkeypatch):
    db.add_word("a", "b")
    day = utc(2026, 10, 1, 10, 0)
    for i in range(9):
        set_now(monkeypatch, day + timedelta(days=i))
        assert db.auto_backup() is not None
        assert db.auto_backup() is None            # shu kuni ikkinchi marta olinmaydi
    files = list(db.backups_dir().glob("sozlar_*.db"))
    assert len(files) == 5
    names = sorted(f.name for f in files)
    assert names[-1].startswith("sozlar_" + db.today_study_day().strftime("%Y-%m-%d"))


def test_backup_is_valid_database(fresh_db):
    db.add_word("a", "b")
    p = db.backup_db("daily")
    c = sqlite3.connect(p)
    assert c.execute("SELECT COUNT(*) FROM words").fetchone()[0] == 1
    c.close()


# ---------------- plan_counts va tanlash ----------------

@pytest.mark.parametrize("n,limit,expected", [
    (20, 20, (14, 9, 5)),
    (400, 20, (20, 13, 7)),
    (1, 20, (1, 1, 0)),
    (2, 20, (2, 1, 1)),
    (3, 20, (3, 2, 1)),
    (0, 20, (0, 0, 0)),
    (10, 20, (7, 5, 2)),
])
def test_plan_counts(n, limit, expected):
    assert db.plan_counts(n, limit) == expected


def _make_seen_words(monkeypatch, count, forgotten):
    """count ta so'z: dastlabki `forgotten` tasi Again (zaif), qolgani Easy (yaxshi)."""
    base = utc(2026, 10, 5, 10, 0)
    set_now(monkeypatch, base)
    ids = [db.add_word(f"p{i}", "t") for i in range(count)]
    for wid in ids[:forgotten]:
        db.review_word(wid, "again")
    for wid in ids[forgotten:]:
        db.review_word(wid, "easy")
    return ids


def test_select_practice_words_20_words(fresh_db, monkeypatch):
    ids = _make_seen_words(monkeypatch, 20, 9)
    words = [db.get_word(i) for i in ids]
    sel = db.select_practice_words(words, set(), limit=20, rng=random.Random(1))
    assert len(sel) == 14
    assert len({w["id"] for w in sel}) == 14


def test_good_words_never_come_from_weak_pool(fresh_db, monkeypatch):
    ids = _make_seen_words(monkeypatch, 20, 9)
    weak_ids = set(ids[:9])
    for seed in range(30):
        sel = db.select_practice_words([db.get_word(i) for i in ids], set(),
                                       limit=20, rng=random.Random(seed))
        sel_ids = {w["id"] for w in sel}
        assert weak_ids <= sel_ids                  # barcha 9 zaif so'z kirgan
        good = sel_ids - weak_ids
        assert len(good) == 5 and good <= set(ids[9:])


def test_struggled_come_first(fresh_db, monkeypatch):
    ids = _make_seen_words(monkeypatch, 20, 0)      # hammasi Easy
    struggled = set(ids[:9])
    sel = db.select_practice_words([db.get_word(i) for i in ids], struggled,
                                   limit=20, rng=random.Random(3))
    assert struggled <= {w["id"] for w in sel}


def test_new_words_excluded_from_practice(fresh_db, monkeypatch):
    ids = _make_seen_words(monkeypatch, 5, 0)
    fresh = [db.add_word(f"n{i}", "t") for i in range(10)]
    sel = db.select_practice_words([db.get_word(i) for i in ids + fresh], set(), limit=20)
    assert {w["id"] for w in sel} <= set(ids)
    assert db.select_practice_words([db.get_word(i) for i in fresh], set(), limit=20) == []


def test_limit_applies_for_big_pool(fresh_db, monkeypatch):
    # 30 ta so'z haqiqatan UNUTILGAN (Relearning), 30 tasi yaxshi eslab qolingan
    base = utc(2026, 10, 5, 10, 0)
    set_now(monkeypatch, base)
    ids = [db.add_word(f"p{i}", "t") for i in range(60)]
    for wid in ids[:30]:
        db.review_word(wid, "easy")
    set_now(monkeypatch, base + timedelta(days=30))
    for wid in ids[:30]:
        db.review_word(wid, "again")          # Review -> Relearning
    for wid in ids[30:]:
        db.review_word(wid, "easy")           # bugun ko'rildi, ehtimol ~100%
    weak_ids = set(ids[:30])
    for seed in range(10):
        sel = db.select_practice_words([db.get_word(i) for i in ids], set(), limit=20,
                                       rng=random.Random(seed))
        assert len(sel) == 20
        assert len([w for w in sel if w["id"] in weak_ids]) == 13
        assert len([w for w in sel if w["id"] not in weak_ids]) == 7


def test_selection_rereads_fresh_rows(fresh_db, monkeypatch):
    ids = _make_seen_words(monkeypatch, 4, 0)
    stale = [db.get_word(i) for i in ids]
    # eskirgan qatorlarda holat Easy; bazada esa 'again' bilan Relearning'ga o'tkazamiz
    set_now(monkeypatch, utc(2026, 10, 20, 10, 0))
    db.review_word(ids[0], "again")
    db.review_word(ids[0], "again")
    sel = db.select_practice_words(stale, set(), limit=20, rng=random.Random(0))
    assert ids[0] in {w["id"] for w in sel}
    got = next(w for w in sel if w["id"] == ids[0])
    assert got["last_review"] == db.get_word(ids[0])["last_review"]


# ---------------- boshqa funksiyalar ----------------

def test_retrievability_and_preview(fresh_db, monkeypatch):
    set_now(monkeypatch, utc(2026, 10, 5, 10, 0))
    wid = db.add_word("a", "b")
    assert db.get_retrievability(db.get_word(wid)) is None
    prev_new = db.preview_intervals(db.get_word(wid))
    assert set(prev_new) == {"again", "hard", "good", "easy"}
    assert prev_new["again"] == "<1 daq"
    before = dict(db.get_word(wid))
    db.preview_intervals(db.get_word(wid))
    assert dict(db.get_word(wid)) == before             # bazaga tegilmadi
    db.review_word(wid, "good")
    r = db.get_retrievability(db.get_word(wid))
    assert 0.0 < r <= 1.0
    assert db.retrievability_percent(db.get_word(wid)) in range(0, 101)


def test_format_interval():
    f = db.format_interval
    assert f(timedelta(seconds=30)) == "<1 daq"
    assert f(timedelta(minutes=10)) == "10 daq"
    assert f(timedelta(hours=3)) == "3 soat"
    assert f(timedelta(days=2)) == "2 kun"
    assert f(timedelta(days=45)).endswith("oy")
    assert f(timedelta(days=800)).endswith("yil")


def test_duration_ms_clamped(fresh_db):
    wid = db.add_word("a", "b")
    db.review_word(wid, "good", duration_ms=500_000)
    db.review_word(wid, "good", duration_ms=1234)
    conn = db.get_connection()
    vals = [r[0] for r in conn.execute("SELECT duration_ms FROM history ORDER BY id")]
    conn.close()
    assert vals == [60000, 1234]


def test_forgotten_words(fresh_db, monkeypatch):
    base = utc(2026, 10, 5, 10, 0)
    set_now(monkeypatch, base)
    a = db.add_word("a", "t", category="K")
    b = db.add_word("b", "t", category="K")
    c = db.add_word("c", "t", category="Z")
    n = db.add_word("n", "t", category="K")                 # Yangi - kirmaydi
    for wid in (a, b, c):
        db.review_word(wid, "easy")
    assert db.get_forgotten_words() == []
    set_now(monkeypatch, base + timedelta(days=3000))       # hammasi unutildi
    f = db.get_forgotten_words("K")
    assert {w["id"] for w in f} == {a, b}
    assert n not in {w["id"] for w in f}
    assert {w["id"] for w in db.get_forgotten_words()} == {a, b, c}
    rets = [db.get_retrievability(w) for w in db.get_forgotten_words()]
    assert rets == sorted(rets)


def test_max_questions_setting(fresh_db):
    assert db.get_max_questions() == 20
    db.set_setting("max_questions", "35")
    assert db.get_max_questions() == 35


# ---------------- tezlik (4000 so'z) ----------------

def test_speed_4000_words(fresh_db, monkeypatch):
    conn = db.get_connection()
    base = utc(2026, 10, 5, 10, 0)
    set_now(monkeypatch, base)
    from fsrs import Card, Rating, Scheduler
    s = Scheduler()
    rows = []
    for i in range(4000):
        cat = f"Bo'lim{i % 5}"
        card, _ = s.review_card(Card(), Rating.Good if i % 3 else Rating.Again, review_datetime=base)
        d = card.to_dict()
        seen = i % 4 != 0
        rows.append((f"w{i}", "t", cat, (i // 5) // 20 + 1, base.isoformat(),
                     d["card_id"] if seen else None, d["state"] if seen else None,
                     d["step"] if seen else None, d["stability"] if seen else None,
                     d["difficulty"] if seen else None, d["due"] if seen else None,
                     d["last_review"] if seen else None))
    conn.executemany(
        "INSERT INTO words (word, translation, category, pack, added_date, card_id, state, step,"
        " stability, difficulty, due, last_review) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()
    set_now(monkeypatch, base + timedelta(days=5))

    t0 = time.perf_counter()
    secs = db.get_sections()
    t_sections = time.perf_counter() - t0
    assert sum(s["total"] for s in secs) == 4000

    allw = db.get_all_words()
    t0 = time.perf_counter()
    sel = db.select_practice_words(allw, set(), limit=20)
    t_select = time.perf_counter() - t0
    assert len(sel) == 20
    print(f"\nget_sections: {t_sections*1000:.0f} ms, select: {t_select*1000:.0f} ms")
    assert t_sections < 1.0
    assert t_select < 3.0


def test_migration_preserves_fsrs_data_and_history(tmp_path, monkeypatch):
    """Eski (v1.1) bazada FSRS holati va tarix migratsiyadan keyin aynan saqlanadi."""
    from tests.conftest import OLD_SCHEMA
    path = tmp_path / "sozlar.db"
    c = sqlite3.connect(path)
    c.executescript(OLD_SCHEMA)
    c.execute("INSERT INTO words (word, translation, category, added_date, card_id, state, step,"
              " stability, difficulty, due, last_review) VALUES ('old','eski','Kitob',"
              "'2026-01-01T00:00:00+00:00', 123, 2, NULL, 12.5, 4.2,"
              " '2026-12-01T00:00:00+00:00', '2026-11-01T00:00:00+00:00')")
    c.execute("INSERT INTO history (word_id, timestamp, rating, source) VALUES (1,'2026-11-01T00:00:00+00:00','Good','flashcard')")
    c.execute("INSERT INTO settings VALUES ('gemini_api_key','AIzaTEST')")
    c.commit()
    before = dict(c.execute("SELECT * FROM words").fetchone() and
                  zip([d[0] for d in c.execute("SELECT * FROM words").description],
                      c.execute("SELECT * FROM words").fetchone()))
    c.close()
    monkeypatch.setattr(db, "DB_PATH", path)
    db.init_db()
    row = dict(db.get_word(1))
    for k, v in before.items():
        assert row[k] == v, k
    assert row["pack"] == 1
    conn = db.get_connection()
    h = conn.execute("SELECT * FROM history").fetchall()
    conn.close()
    assert len(h) == 1 and h[0]["duration_ms"] is None
    assert db.get_setting("gemini_api_key") == "AIzaTEST"
