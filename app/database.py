"""
database.py  (v2 — 1-bosqich: asos va mantiq)
---------------------------------------------
Ilovaning "xotirasi": SQLite bazasiga so'zlarni saqlaydi, FSRS yordamida
"qachon qayta ko'rsatish kerak"ni hisoblaydi.

v2 da nima o'zgardi:
  * "O'quv kuni": mahalliy vaqt bo'yicha, yangi kun tun 04:00 da boshlanadi.
  * Bo'lim -> bo'limcha tuzilmasi (words.category = bo'lim, words.pack =
    bo'limcha raqami, har bir bo'limchada eng ko'pi 20 ta so'z).
  * 🔵🟢🔴 sonlar (get_sections).
  * PRAGMA user_version migratsiyalari + avtomatik zaxira (backups/).
  * AI savollari uchun so'z tanlash (plan_counts, select_practice_words).
"""

import json
import math
import random
import sqlite3
import sys
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

from fsrs import Scheduler, Card, Rating, State


def _app_base_dir() -> Path:
    """Ilova skript sifatida yoki PyInstaller .exe sifatida ishlayotganidan
    qat'i nazar, bazani doim .exe/skript joylashgan papkada saqlaydi."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


DB_PATH = _app_base_dir() / "sozlar.db"

PACK_SIZE = 20            # bitta bo'limchadagi eng ko'p so'z soni
DAY_START_HOUR = 4        # yangi o'quv kuni tun 04:00 da boshlanadi
SCHEMA_VERSION = 5        # PRAGMA user_version ning joriy qiymati
BACKUP_KEEP = 5           # oxirgi nechta zaxira nusxa saqlanadi
MAX_DURATION_MS = 60_000  # javob vaqti 60 soniyadan oshsa, 60 soniya deb olinadi
DEFAULT_SECTION = "Umumiy"

_scheduler = Scheduler()


# ---------------------------------------------------------------------------
# Vaqt: mahalliy vaqt + 04:00 "o'quv kuni"
# ---------------------------------------------------------------------------

def _now_utc() -> datetime:
    """Hozirgi vaqt (UTC). Testlarda shu funksiya almashtiriladi."""
    return datetime.now(timezone.utc)


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def study_day_of(dt: datetime):
    """Berilgan vaqt qaysi "o'quv kuni"ga tegishli ekanini qaytaradi.
    Mahalliy vaqtdan 4 soat ayiriladi: 03:59 hali kechagi kun, 04:00 - yangi kun."""
    local = dt.astimezone()  # tizimning mahalliy vaqt mintaqasi (TZ ni hisobga oladi)
    return (local - timedelta(hours=DAY_START_HOUR)).date()


def today_study_day():
    return study_day_of(_now_utc())


def study_day_start_utc(day) -> datetime:
    """O'quv kunining boshlanishi (mahalliy 04:00) - UTC ko'rinishida."""
    naive_local = datetime.combine(day, dtime(DAY_START_HOUR, 0))
    return naive_local.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Ulanish va zaxira
# ---------------------------------------------------------------------------

def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL: o'qish va yozish bir-biriga xalaqit bermaydi ("database is locked"
    # xatosiga qarshi standart yechim).
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def backups_dir() -> Path:
    return DB_PATH.parent / "backups"


def backup_db(tag: str = "daily") -> Path | None:
    """sqlite3'ning rasmiy backup API'si bilan bazaning to'liq nusxasini
    backups/ papkasiga saqlaydi, so'ng eng yangi BACKUP_KEEP tadan boshqasini
    o'chiradi. Baza fayli hali yo'q bo'lsa None qaytaradi."""
    if not DB_PATH.exists():
        return None
    folder = backups_dir()
    folder.mkdir(parents=True, exist_ok=True)

    stamp = today_study_day().strftime("%Y-%m-%d") + _now_utc().astimezone().strftime("_%H%M%S")
    target = folder / f"sozlar_{stamp}_{tag}.db"
    n = 1
    while target.exists():
        target = folder / f"sozlar_{stamp}_{tag}_{n}.db"
        n += 1

    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    _prune_backups()
    return target


def _prune_backups():
    files = sorted(backups_dir().glob("sozlar_*.db"), key=lambda p: p.name)
    for old in files[:-BACKUP_KEEP]:
        try:
            old.unlink()
        except OSError:
            pass


def auto_backup() -> Path | None:
    """Kuniga BIR marta (o'quv kuni bo'yicha) zaxira oladi. Bugun allaqachon
    olingan bo'lsa, hech narsa qilmaydi."""
    folder = backups_dir()
    today = today_study_day().strftime("%Y-%m-%d")
    if folder.exists():
        for p in folder.glob(f"sozlar_{today}_*_daily*.db"):
            return None
    return backup_db("daily")


# ---------------------------------------------------------------------------
# Bazani sozlash va migratsiyalar
# ---------------------------------------------------------------------------

def _columns(conn, table: str) -> list[str]:
    return [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]


def _get_user_version(conn) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def init_db():
    """Ilova ishga tushganda: zaxira -> jadvallar -> migratsiyalar.
    Idempotent: istalgan marta chaqirish xavfsiz, eski bazada ham ishlaydi."""
    existed = DB_PATH.exists() and DB_PATH.stat().st_size > 0

    if existed:
        probe = sqlite3.connect(DB_PATH)
        try:
            pending = probe.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION
        finally:
            probe.close()
        if pending:
            backup_db("premigrate")   # migratsiyadan oldin alohida nusxa
        auto_backup()                  # kunlik nusxa (bugun hali olinmagan bo'lsa)

    conn = get_connection()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word TEXT NOT NULL UNIQUE,
            translation TEXT NOT NULL,
            example TEXT,
            category TEXT DEFAULT 'Umumiy',
            added_date TEXT NOT NULL,
            card_id INTEGER,
            state INTEGER,
            step INTEGER,
            stability REAL,
            difficulty REAL,
            due TEXT,
            last_review TEXT
        );

        CREATE TABLE IF NOT EXISTS history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            word_id INTEGER NOT NULL,
            timestamp TEXT NOT NULL,
            rating TEXT NOT NULL,
            source TEXT NOT NULL,
            FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS ai_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mode TEXT,
            transcript TEXT,
            word_ids TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """
    )
    _migrate(conn)
    conn.close()


def _migrate(conn):
    version = _get_user_version(conn)

    if version < 1:
        # Eng eski bazalarda category ustuni bo'lmasligi mumkin.
        if "category" not in _columns(conn, "words"):
            conn.execute("ALTER TABLE words ADD COLUMN category TEXT DEFAULT 'Umumiy'")
        conn.execute("UPDATE words SET category = ? WHERE category IS NULL OR TRIM(category) = ''",
                     (DEFAULT_SECTION,))
        conn.commit()
        conn.execute("PRAGMA user_version = 1")

    if version < 2:
        if "pack" not in _columns(conn, "words"):
            conn.execute("ALTER TABLE words ADD COLUMN pack INTEGER")
        if "duration_ms" not in _columns(conn, "history"):
            conn.execute("ALTER TABLE history ADD COLUMN duration_ms INTEGER")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_word ON history(word_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_history_ts ON history(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_words_cat_pack ON words(category, pack)")
        conn.commit()
        conn.execute("PRAGMA user_version = 2")

    if version < 3:
        # v3 (CLAUDE-2): imlo holati. Yangi so'z 'unchecked' bilan boshlanadi.
        # Migratsiya paytida MAVJUD so'zlar 'confirmed' qilinadi - aks holda
        # hammasi kartalardan chiqib ketardi. Bir martalik: ustun allaqachon
        # bo'lsa (qayta ishga tushirish) hech narsa o'zgarmaydi.
        if "spell_status" not in _columns(conn, "words"):
            conn.execute("ALTER TABLE words ADD COLUMN spell_status TEXT DEFAULT 'unchecked'")
            conn.execute("UPDATE words SET spell_status = 'confirmed'")
        conn.commit()
        conn.execute("PRAGMA user_version = 3")

    if version < 4:
        # v4 (CLAUDE-3): audio va import navbati.
        # audio_source: 'human' (audio/ papkadagi yoki internetdagi inson ovozi),
        # 'tts' (sintez), 'none' (hali yo'q). audio_path - fayl yo'li (bo'lmasa NULL).
        cols = _columns(conn, "words")
        if "audio_source" not in cols:
            conn.execute("ALTER TABLE words ADD COLUMN audio_source TEXT DEFAULT 'none'")
        if "audio_path" not in cols:
            conn.execute("ALTER TABLE words ADD COLUMN audio_path TEXT")
        # Import qilingan so'zlarni fonda tekshirish/audio olish navbati.
        # Ilova yopilsa ham navbat saqlanadi (keyingi ochilishda davom etadi).
        conn.execute(
            """CREATE TABLE IF NOT EXISTS import_queue (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   word_id INTEGER NOT NULL,
                   task TEXT NOT NULL,
                   created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                   FOREIGN KEY (word_id) REFERENCES words(id) ON DELETE CASCADE)""")
        conn.commit()
        conn.execute("PRAGMA user_version = 4")

    if version < 5:
        # v5 (CLAUDE-4): savol banki va javoblar tarixi (statistika uchun).
        # question_bank: bir marta Gemini'dan olingan, javobi kompyuterda tekshiriladigan
        # savollar. `streak` - ketma-ket to'g'ri javoblar (3 ta -> 'retired').
        # So'z o'chsa savollari ham o'chadi (ON DELETE CASCADE).
        conn.execute(
            """CREATE TABLE IF NOT EXISTS question_bank (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   word_id INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
                   type TEXT NOT NULL,
                   level TEXT NOT NULL,
                   data TEXT NOT NULL,
                   status TEXT NOT NULL DEFAULT 'active',
                   shown_count INTEGER NOT NULL DEFAULT 0,
                   correct_count INTEGER NOT NULL DEFAULT 0,
                   streak INTEGER NOT NULL DEFAULT 0,
                   last_shown TEXT,
                   created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_qbank_word ON question_bank(word_id, status)")
        # question_log: har javob - savol turi, so'z, to'g'ri/xato, vaqt.
        conn.execute(
            """CREATE TABLE IF NOT EXISTS question_log (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   word_id INTEGER NOT NULL REFERENCES words(id) ON DELETE CASCADE,
                   bank_id INTEGER,
                   type TEXT NOT NULL,
                   correct INTEGER NOT NULL,
                   answered_at TEXT NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_qlog_word ON question_log(word_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_qlog_ts ON question_log(answered_at)")
        conn.commit()
        conn.execute("PRAGMA user_version = 5")

    # Har ishga tushganda: pack'i yo'q so'zlar (masalan eski versiya qo'shgan)
    # bo'limchaga joylanadi. Migratsiyada hamma so'zning pack'i NULL bo'ladi.
    _assign_missing_packs(conn)
    conn.commit()


def _assign_missing_packs(conn):
    """Pack'i bo'lmagan so'zlarni added_date tartibida bo'limchalarga joylaydi.
    Bo'lim nomlari O'ZGARMAYDI. Misol: 45 so'zli bo'lim -> 20 + 20 + 5."""
    cats = [r["category"] for r in conn.execute(
        "SELECT DISTINCT category FROM words WHERE pack IS NULL")]
    for cat in cats:
        row = conn.execute(
            "SELECT MAX(pack) AS m FROM words WHERE category = ? AND pack IS NOT NULL", (cat,)
        ).fetchone()
        pack = row["m"] or 1
        count = conn.execute(
            "SELECT COUNT(*) FROM words WHERE category = ? AND pack = ?", (cat, pack)
        ).fetchone()[0] if row["m"] else 0

        todo = conn.execute(
            "SELECT id FROM words WHERE category = ? AND pack IS NULL "
            "ORDER BY added_date ASC, id ASC", (cat,)
        ).fetchall()
        for r in todo:
            if count >= PACK_SIZE:
                pack += 1
                count = 0
            conn.execute("UPDATE words SET pack = ? WHERE id = ?", (pack, r["id"]))
            count += 1


# ---------------------------------------------------------------------------
# Sozlamalar
# ---------------------------------------------------------------------------

def get_setting(key: str, default=None):
    conn = get_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key: str, value):
    conn = get_connection()
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
    conn.commit()
    conn.close()


def get_daily_new_limit() -> int:
    return int(get_setting("daily_new_limit", 20))


def get_max_questions() -> int:
    """AI mashqidagi savollar soni (eng ko'pi). Standart: 20."""
    try:
        return max(1, int(get_setting("max_questions", 20)))
    except (TypeError, ValueError):
        return 20


ENGLISH_LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]


def get_english_level() -> str:
    """Foydalanuvchi Sozlamalarda tanlagan ingliz tili darajasi (standart B1)."""
    v = get_setting("english_level", "B1")
    return v if v in ENGLISH_LEVELS else "B1"


LEECH_THRESHOLD = 4   # shuncha va undan ko'p marta "Again" olgan so'z - "qiyin" (leech)


def get_leech_ids(word_ids=None) -> set[int]:
    """4 va undan ko'p marta Again olgan ("qiyin") so'zlar id'lari.
    `word_ids` berilsa, faqat shular orasidan; berilmasa - hammasidan."""
    conn = get_connection()
    try:
        if word_ids is None:
            rows = conn.execute(
                "SELECT word_id FROM history WHERE rating = 'Again' "
                "GROUP BY word_id HAVING COUNT(*) >= ?", (LEECH_THRESHOLD,)).fetchall()
            return {r["word_id"] for r in rows}
        ids = list(dict.fromkeys(word_ids))
        result: set[int] = set()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            marks = ",".join("?" * len(chunk))
            rows = conn.execute(
                f"SELECT word_id FROM history WHERE rating = 'Again' AND word_id IN ({marks}) "
                f"GROUP BY word_id HAVING COUNT(*) >= ?", chunk + [LEECH_THRESHOLD]).fetchall()
            result |= {r["word_id"] for r in rows}
        return result
    finally:
        conn.close()


def get_distractors(word_id: int, field: str = "translation", n: int = 3) -> list[str]:
    """Test savollari uchun noto'g'ri variantlar: boshqa so'zlarning `field`
    ('translation' yoki 'word') qiymatlaridan tasodifiy n tasi (to'g'ri javobdan farqli)."""
    if field not in ("translation", "word"):
        raise ValueError(field)
    conn = get_connection()
    try:
        row = conn.execute(f"SELECT {field} AS v FROM words WHERE id = ?", (word_id,)).fetchone()
        correct = (row["v"] if row else "") or ""
        rows = conn.execute(
            f"SELECT DISTINCT {field} AS v FROM words WHERE id != ? AND TRIM({field}) != '' "
            f"AND LOWER({field}) != LOWER(?) ORDER BY RANDOM() LIMIT ?",
            (word_id, correct, n)).fetchall()
        return [r["v"] for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# FSRS kartasi <-> baza qatori
# ---------------------------------------------------------------------------

def _row_to_card(row) -> Card | None:
    if row["card_id"] is None:
        return None
    return Card.from_dict({
        "card_id": row["card_id"],
        "state": row["state"],
        "step": row["step"],
        "stability": row["stability"],
        "difficulty": row["difficulty"],
        "due": row["due"],
        "last_review": row["last_review"],
    })


def _card_to_fields(card: Card) -> dict:
    d = card.to_dict()
    return {k: d[k] for k in ("card_id", "state", "step", "stability",
                              "difficulty", "due", "last_review")}


# ---------------------------------------------------------------------------
# So'zlar bilan ishlash
# ---------------------------------------------------------------------------

def _clean_category(category) -> str:
    return (category or DEFAULT_SECTION).strip() or DEFAULT_SECTION


def _next_pack(conn, category: str, exclude_id: int | None = None) -> int:
    """Bo'limning OXIRGI bo'limchasida joy bo'lsa (< 20) o'sha raqamni,
    to'la bo'lsa keyingisini (raqam + 1) qaytaradi. O'chirilgan so'zlar
    o'rni oldingi bo'limchalarda to'ldirilmaydi."""
    row = conn.execute(
        "SELECT MAX(pack) AS m FROM words WHERE category = ? AND pack IS NOT NULL", (category,)
    ).fetchone()
    last = row["m"]
    if last is None:
        return 1
    q = "SELECT COUNT(*) FROM words WHERE category = ? AND pack = ?"
    params = [category, last]
    if exclude_id is not None:
        q += " AND id != ?"
        params.append(exclude_id)
    count = conn.execute(q, params).fetchone()[0]
    return last if count < PACK_SIZE else last + 1


def add_word(word: str, translation: str, example: str = "", category: str = DEFAULT_SECTION) -> int:
    """Yangi so'z qo'shadi va uni bo'limning oxirgi (yoki yangi) bo'limchasiga joylaydi."""
    category = _clean_category(category)
    conn = get_connection()
    try:
        pack = _next_pack(conn, category)
        cur = conn.execute(
            """INSERT INTO words (word, translation, example, category, pack, added_date)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (word.strip(), translation.strip(), (example or "").strip(), category, pack,
             _now_utc().isoformat()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_categories() -> list[str]:
    conn = get_connection()
    rows = conn.execute("SELECT DISTINCT category FROM words ORDER BY category ASC").fetchall()
    conn.close()
    return [r["category"] for r in rows if r["category"]]


def delete_word(word_id: int):
    conn = get_connection()
    conn.execute("DELETE FROM words WHERE id = ?", (word_id,))
    conn.commit()
    conn.close()


def get_word(word_id: int):
    conn = get_connection()
    row = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
    conn.close()
    return row


def get_words_by_ids(ids) -> list[sqlite3.Row]:
    """So'zlarni id bo'yicha YANGIDAN o'qiydi (mashq boshida olingan qatorlar
    eskirgan bo'lishi mumkin). Tartib `ids` tartibiga mos."""
    ids = list(dict.fromkeys(ids))
    if not ids:
        return []
    conn = get_connection()
    found = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        marks = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT * FROM words WHERE id IN ({marks})", chunk):
            found[r["id"]] = r
    conn.close()
    return [found[i] for i in ids if i in found]


def update_word(word_id: int, word: str, translation: str, example: str = "",
                category: str = DEFAULT_SECTION):
    """So'zni tahrirlaydi. FSRS holatiga tegmaydi. Bo'lim o'zgarsa, so'z yangi
    bo'limning oxirgi (yoki yangi) bo'limchasiga tushadi."""
    category = _clean_category(category)
    conn = get_connection()
    try:
        old = conn.execute("SELECT category, pack FROM words WHERE id = ?", (word_id,)).fetchone()
        if old is None:
            return
        pack = old["pack"]
        if old["category"] != category or pack is None:
            pack = _next_pack(conn, category, exclude_id=word_id)
        conn.execute(
            """UPDATE words SET word = ?, translation = ?, example = ?, category = ?, pack = ?
               WHERE id = ?""",
            (word.strip(), translation.strip(), (example or "").strip(), category, pack, word_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_words(search: str = "", category: str = "") -> list[sqlite3.Row]:
    conn = get_connection()
    conditions, params = [], []
    if search:
        conditions.append("(word LIKE ? OR translation LIKE ?)")
        params += [f"%{search}%", f"%{search}%"]
    if category and category != "Barchasi":
        conditions.append("category = ?")
        params.append(category)
    query = "SELECT * FROM words"
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY added_date DESC, id DESC"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return rows


def get_category_words(category: str) -> list[sqlite3.Row]:
    """Butun bo'limdagi HAMMA so'zlar (qo'shilgan tartibda)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM words WHERE category = ? ORDER BY pack ASC, added_date ASC, id ASC",
        (category,),
    ).fetchall()
    conn.close()
    return rows


def get_pack_words(category: str, pack: int) -> list[sqlite3.Row]:
    """Bitta bo'limchadagi so'zlar (eng ko'pi 20 ta)."""
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM words WHERE category = ? AND pack = ? ORDER BY added_date ASC, id ASC",
        (category, pack),
    ).fetchall()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# So'z holati: yorliq, rang-guruh, eslab qolish ehtimoli
# ---------------------------------------------------------------------------

def word_label(row, now: datetime | None = None) -> str:
    """FSRS holatidan foydalanuvchiga tushunarli yorliq."""
    if row["last_review"] is None:
        return "Yangi"
    state = row["state"]
    if state == State.Learning.value:
        return "O'rganilmoqda"
    if state == State.Relearning.value:
        return "Unutilgan"
    now = now or _now_utc()
    if _parse_ts(row["due"]) <= now:
        return "O'rtacha unutilgan"
    return "Yaxshi o'rganilgan"


_BUCKET_OF_LABEL = {
    "Yangi": "blue",
    "O'rganilmoqda": "green",
    "Yaxshi o'rganilgan": "green",
    "O'rtacha unutilgan": "red",
    "Unutilgan": "red",
}


def word_bucket(row, now: datetime | None = None) -> str:
    """🔵 blue = Yangi; 🟢 green = Yaxshi o'rganilgan + O'rganilmoqda;
    🔴 red = Unutilgan + O'rtacha unutilgan."""
    return _BUCKET_OF_LABEL[word_label(row, now)]


def get_retrievability(row, now: datetime | None = None) -> float | None:
    """Eslab qolish ehtimoli (0..1). Hali ko'rilmagan (Yangi) so'z uchun None."""
    if row["last_review"] is None:
        return None
    card = _row_to_card(row)
    if card is None:
        return None
    return float(_scheduler.get_card_retrievability(card, current_datetime=now or _now_utc()))


def retrievability_percent(row, now: datetime | None = None) -> int | None:
    r = get_retrievability(row, now)
    return None if r is None else int(round(r * 100))


# ---------------------------------------------------------------------------
# Bo'limlar daraxti: 🔵🟢🔴 sonlar
# ---------------------------------------------------------------------------

def get_sections() -> list[dict]:
    """Bo'lim -> bo'limchalar ro'yxati. Har birida so'zlar soni va 🔵🟢🔴 sonlar
    (blue + green + red = total).

    [{"category": "Kitob", "total": 45, "blue": .., "green": .., "red": ..,
      "packs": [{"pack": 1, "total": 20, "blue": .., "green": .., "red": ..}, ...]}, ...]
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT category, pack, state, due, last_review FROM words"
    ).fetchall()
    conn.close()

    now = _now_utc()
    cats: dict[str, dict] = {}
    for r in rows:
        cat = r["category"] or DEFAULT_SECTION
        pack = r["pack"] or 1
        bucket = word_bucket(r, now)
        c = cats.setdefault(cat, {"category": cat, "total": 0, "blue": 0, "green": 0,
                                  "red": 0, "packs": {}})
        p = c["packs"].setdefault(pack, {"pack": pack, "total": 0, "blue": 0, "green": 0, "red": 0})
        for d in (c, p):
            d["total"] += 1
            d[bucket] += 1

    result = []
    for name in sorted(cats, key=lambda s: (s != DEFAULT_SECTION, s.lower())):
        c = cats[name]
        c["packs"] = [c["packs"][k] for k in sorted(c["packs"])]
        result.append(c)
    return result


# ---------------------------------------------------------------------------
# Mashq uchun so'zlar: kartalar va unutilganlar
# ---------------------------------------------------------------------------

def _new_words_started_today() -> int:
    """Bugungi o'quv kunida birinchi marta baholangan (ya'ni "yangi" bo'lib
    ko'rilgan) so'zlar soni - tarixdan hisoblanadi, shuning uchun sessiya
    yarim yo'lda yopilsa ham noto'g'ri sanalmaydi."""
    start = study_day_start_utc(today_study_day()).isoformat()
    conn = get_connection()
    n = conn.execute(
        "SELECT COUNT(*) FROM (SELECT word_id FROM history GROUP BY word_id "
        "HAVING MIN(timestamp) >= ?)", (start,)
    ).fetchone()[0]
    conn.close()
    return n


def new_slots_left_today() -> int:
    return max(0, get_daily_new_limit() - _new_words_started_today())


def get_study_words(category: str | None = None, pack: int | None = None) -> list[sqlite3.Row]:
    """Tanlangan doiradagi (bo'lim yoki bo'limcha) mashq uchun so'zlar:
    1) muddati kelgan takroriy so'zlar (limitsiz),
    2) yangi so'zlar - kunlik yangi so'z limiti (barcha bo'limlar bo'yicha umumiy) bilan."""
    scope, params = "", []
    if category is not None:
        scope += " AND category = ?"
        params.append(category)
        if pack is not None:
            scope += " AND pack = ?"
            params.append(pack)

    # Imlo tekshiruvidan o'tmagan ('unchecked'/'suspect') so'zlar kartada qatnashmaydi (D-008).
    # Filtr SQL'da: aks holda ular kunlik yangi so'z limitini "yeb qo'yib", keyin kartadan
    # chiqarib tashlanardi va bosh sahifadagi son haqiqiy kartalar sonidan katta ko'rinardi.
    studyable = f" AND spell_status IN ({','.join('?' * len(SPELL_STUDY_OK))})"
    studyable_params = list(SPELL_STUDY_OK)

    conn = get_connection()
    now_iso = _now_utc().isoformat()
    due = conn.execute(
        f"SELECT * FROM words WHERE last_review IS NOT NULL AND due <= ?{scope}{studyable} ORDER BY due ASC",
        [now_iso] + params + studyable_params,
    ).fetchall()
    conn.close()

    slots = new_slots_left_today()
    new_words = []
    if slots > 0:
        conn = get_connection()
        new_words = conn.execute(
            f"SELECT * FROM words WHERE last_review IS NULL{scope}{studyable} "
            f"ORDER BY pack ASC, added_date ASC, id ASC LIMIT ?",
            params + studyable_params + [slots],
        ).fetchall()
        conn.close()
    return list(due) + list(new_words)


def get_review_session_words(max_new: int | None = None) -> list[sqlite3.Row]:
    """Eski nom (v1.1) - hamma bo'limlar bo'yicha o'quv so'zlari. Yangi kodda
    get_study_words(category, pack) ishlatiladi."""
    return get_study_words()


def get_forgotten_words(category: str | None = None, pack: int | None = None) -> list[sqlite3.Row]:
    """Unutilgan so'zlar: holati Relearning YOKI eslab qolish ehtimoli < 0.90.
    Eng past ehtimoldan boshlab tartiblangan. Hali ko'rilmagan so'zlar kirmaydi."""
    scope, params = "", []
    if category is not None:
        scope += " AND category = ?"
        params.append(category)
        if pack is not None:
            scope += " AND pack = ?"
            params.append(pack)
    conn = get_connection()
    rows = conn.execute(
        f"SELECT * FROM words WHERE last_review IS NOT NULL{scope}", params
    ).fetchall()
    conn.close()

    now = _now_utc()
    scored = []
    for r in rows:
        ret = get_retrievability(r, now)
        if r["state"] == State.Relearning.value or (ret is not None and ret < 0.90):
            scored.append((ret if ret is not None else 0.0, r))
    scored.sort(key=lambda t: t[0])
    return [r for _, r in scored]


# ---------------------------------------------------------------------------
# Baholash
# ---------------------------------------------------------------------------

RATING_MAP = {
    "again": Rating.Again,
    "hard": Rating.Hard,
    "good": Rating.Good,
    "easy": Rating.Easy,
}


def review_word(word_id: int, rating: str, source: str = "flashcard",
                duration_ms: int | None = None) -> datetime:
    """So'zni baholaydi va FSRS orqali keyingi muddatni qayta hisoblaydi.
    Yangi muddatni (UTC) qaytaradi. `duration_ms` - javob vaqti (60 soniyadan
    oshsa 60 soniya deb olinadi)."""
    rating_enum = RATING_MAP[rating.lower()]
    if duration_ms is not None:
        duration_ms = int(max(0, min(duration_ms, MAX_DURATION_MS)))

    conn = get_connection()
    row = conn.execute("SELECT * FROM words WHERE id = ?", (word_id,)).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"So'z topilmadi: id={word_id}")

    now = _now_utc()
    card = _row_to_card(row) or Card()
    new_card, _log = _scheduler.review_card(card, rating_enum, review_datetime=now)
    f = _card_to_fields(new_card)

    conn.execute(
        """UPDATE words SET card_id = ?, state = ?, step = ?, stability = ?,
                            difficulty = ?, due = ?, last_review = ? WHERE id = ?""",
        (f["card_id"], f["state"], f["step"], f["stability"], f["difficulty"],
         f["due"], f["last_review"], word_id),
    )
    conn.execute(
        "INSERT INTO history (word_id, timestamp, rating, source, duration_ms) VALUES (?, ?, ?, ?, ?)",
        (word_id, now.isoformat(), rating_enum.name, source, duration_ms),
    )
    conn.commit()
    conn.close()
    return _parse_ts(f["due"])


def format_interval(delta: timedelta) -> str:
    secs = delta.total_seconds()
    if secs <= 60:          # 1 daqiqalik qadam ham "<1 daq" deb ko'rsatiladi
        return "<1 daq"
    if secs < 3600:
        return f"{int(round(secs / 60))} daq"
    if secs < 86400:
        return f"{int(round(secs / 3600))} soat"
    days = secs / 86400
    if days < 30:
        return f"{int(round(days))} kun"
    if days < 365:
        months = days / 30.44
        return f"{months:.1f} oy".replace(".0 ", " ")
    years = days / 365.25
    return f"{years:.1f} yil".replace(".0 ", " ")


def preview_intervals(row) -> dict[str, str]:
    """Har bir baho uchun keyingi muddat ("<1 daq", "10 daq", "2 kun"...).
    Hisob kartaning NUSXASIDA bajariladi - bazaga hech narsa yozilmaydi."""
    now = _now_utc()
    base = _row_to_card(row) or Card()
    result = {}
    for key, rating in RATING_MAP.items():
        copy = Card.from_dict(base.to_dict())
        new_card, _ = _scheduler.review_card(copy, rating, review_datetime=now)
        result[key] = format_interval(new_card.due - now)
    return result


def save_ai_session(mode: str, transcript: str, word_ids: list[int]):
    conn = get_connection()
    conn.execute(
        "INSERT INTO ai_sessions (mode, transcript, word_ids) VALUES (?, ?, ?)",
        (mode, transcript, json.dumps(word_ids)),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# AI savollari uchun so'z tanlash: 70% = 45% zaif + 25% yaxshi eslab qolingan
# ---------------------------------------------------------------------------

def plan_counts(n: int, limit: int) -> tuple[int, int, int]:
    """To'plamda n ta (ko'rilgan) so'z bo'lsa: (asked, weak, good).
      asked = min(ceil(0.70*n), limit);  3 tadan kam so'zda hammasidan so'raladi
      weak  = round(asked * 45/70)   (0.5 yuqoriga yaxlitlanadi)
      good  = asked - weak
    Misol: n=20 -> (14, 9, 5);  n=400, limit=20 -> (20, 13, 7)."""
    if n <= 0 or limit <= 0:
        return (0, 0, 0)
    if n < 3:
        asked = min(n, limit)
    else:
        asked = min(math.ceil(0.70 * n), limit)
    weak = (asked * 90 + 70) // 140          # = floor(asked*45/70 + 0.5), butun sonlarda
    return (asked, weak, asked - weak)


def select_practice_words(words, struggled_ids=None, limit: int | None = None,
                          rng: random.Random | None = None) -> list[sqlite3.Row]:
    """AI mashqi uchun so'zlarni tanlaydi.

    * `words` - faqat IDlari ishlatiladi: so'zlar bazadan QAYTA o'qiladi
      (mashq boshidagi qatorlar eskirgan bo'lishi mumkin).
    * Faqat kamida bir marta ko'rilgan so'zlar hisoblanadi ("Yangi" kirmaydi).
    * Zaif tartibi: (1) struggled_ids (shu mashqda Again/Hard olganlar),
      (2) "Unutilgan" (Relearning), (3) eslab qolish ehtimoli eng past.
    * Yaxshi so'zlar zaif tanlanganidan KEYIN QOLGAN so'zlar orasidan tasodifiy
      olinadi (avval ehtimoli >= 0.90 va unutilmaganlar orasidan).
    * Natija aralashtiriladi."""
    rng = rng or random.Random()
    limit = get_max_questions() if limit is None else limit
    struggled = set(struggled_ids or [])

    fresh = get_words_by_ids([w["id"] for w in words])
    seen = [r for r in fresh if r["last_review"] is not None]
    n = len(seen)
    asked, weak_n, good_n = plan_counts(n, limit)
    if asked == 0:
        return []
    if n < 3:
        out = list(seen)
        rng.shuffle(out)
        return out[:asked]

    now = _now_utc()
    info = []
    for r in seen:
        ret = get_retrievability(r, now)
        ret = 1.0 if ret is None else ret
        if r["id"] in struggled:
            group = 0
        elif r["state"] == State.Relearning.value:
            group = 1
        else:
            group = 2
        info.append((group, ret, r))
    info.sort(key=lambda t: (t[0], t[1]))

    weak = [t for t in info[:weak_n]]
    weak_ids = {t[2]["id"] for t in weak}
    rest = [t for t in info if t[2]["id"] not in weak_ids]

    healthy = [t for t in rest if t[0] == 2 and t[1] >= 0.90]
    picked = rng.sample(healthy, min(good_n, len(healthy)))
    if len(picked) < good_n:
        # Yetarli "sog'lom" so'z yo'q: qolganlardan eng yaxshi eslab qolinganlarini to'ldiramiz.
        taken = {t[2]["id"] for t in picked}
        extra = sorted((t for t in rest if t[2]["id"] not in taken),
                       key=lambda t: -t[1])
        picked += extra[: good_n - len(picked)]

    result = [t[2] for t in weak] + [t[2] for t in picked]
    rng.shuffle(result)
    return result


# ---------------------------------------------------------------------------
# Statistika (umumiy)
# ---------------------------------------------------------------------------

def get_stats() -> dict:
    conn = get_connection()
    rows = conn.execute("SELECT state, due, last_review FROM words").fetchall()
    conn.close()
    now = _now_utc()
    stats = {"Yangi": 0, "O'rganilmoqda": 0, "Yaxshi o'rganilgan": 0,
             "O'rtacha unutilgan": 0, "Unutilgan": 0}
    for r in rows:
        stats[word_label(r, now)] += 1
    stats["jami"] = len(rows)
    return stats


def _history_days(extra_days: int) -> set:
    """Tarixdagi barcha o'quv kunlari (streak uchun)."""
    conn = get_connection()
    rows = conn.execute("SELECT timestamp FROM history").fetchall()
    conn.close()
    return {study_day_of(_parse_ts(r["timestamp"])) for r in rows}


def get_reviews_per_day(days: int = 14) -> list[tuple[str, int]]:
    """Oxirgi `days` o'quv kunida har kuni nechta baholash qilingani:
    [("01.10", 3), ...] - eskidan yangiga. Kun chegarasi mahalliy 04:00."""
    today = today_study_day()
    first = today - timedelta(days=days - 1)
    lower = (study_day_start_utc(first) - timedelta(days=1)).isoformat()

    conn = get_connection()
    rows = conn.execute("SELECT timestamp FROM history WHERE timestamp >= ?", (lower,)).fetchall()
    conn.close()

    counts: dict = {}
    for r in rows:
        d = study_day_of(_parse_ts(r["timestamp"]))
        counts[d] = counts.get(d, 0) + 1

    result = []
    for i in range(days - 1, -1, -1):
        d = today - timedelta(days=i)
        result.append((d.strftime("%d.%m"), counts.get(d, 0)))
    return result


def get_current_streak() -> int:
    """Necha o'quv kuni ketma-ket kamida bitta so'z baholangani. Bugun hali
    hech narsa qilinmagan bo'lsa, kechadan hisoblanadi (kun hali tugamagan)."""
    active = _history_days(0)
    if not active:
        return 0
    cursor = today_study_day()
    if cursor not in active:
        cursor -= timedelta(days=1)
    streak = 0
    while cursor in active:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def get_category_counts() -> list[tuple[str, int]]:
    conn = get_connection()
    rows = conn.execute(
        "SELECT category, COUNT(*) as cnt FROM words GROUP BY category ORDER BY cnt DESC"
    ).fetchall()
    conn.close()
    return [(r["category"] or DEFAULT_SECTION, r["cnt"]) for r in rows]


# ---------------------------------------------------------------------------
# 3-bosqich: "Bugungi statistika" va faollik xaritasi (heatmap)
# ---------------------------------------------------------------------------

def get_today_stats() -> dict:
    """Bugungi o'quv kuni (mahalliy 04:00 dan) bo'yicha:
    studied - nechta baholash; minutes - umumiy vaqt (daqiqa); pace - o'rtacha
    soniya/baho; retention - 'Again' bo'lmagan javoblar ulushi (% yoki None)."""
    start = study_day_start_utc(today_study_day()).isoformat()
    conn = get_connection()
    rows = conn.execute(
        "SELECT rating, duration_ms FROM history WHERE timestamp >= ?", (start,)).fetchall()
    conn.close()
    n = len(rows)
    durs = [r["duration_ms"] for r in rows if r["duration_ms"] is not None]
    total_ms = sum(durs)
    retention = None
    if n:
        retention = int(round(100 * sum(1 for r in rows if r["rating"] != "Again") / n))
    return {
        "studied": n,
        "minutes": round(total_ms / 60000, 1),
        "pace": round(total_ms / 1000 / len(durs), 1) if durs else 0.0,
        "retention": retention,
    }


def get_activity_counts(days: int = 371) -> dict:
    """{o'quv kuni (date): baholashlar soni} - oxirgi `days` kun uchun (heatmap)."""
    first = today_study_day() - timedelta(days=days)
    lower = (study_day_start_utc(first) - timedelta(days=1)).isoformat()
    conn = get_connection()
    rows = conn.execute("SELECT timestamp FROM history WHERE timestamp >= ?", (lower,)).fetchall()
    conn.close()
    counts: dict = {}
    for r in rows:
        d = study_day_of(_parse_ts(r["timestamp"]))
        counts[d] = counts.get(d, 0) + 1
    return counts


def get_longest_streak() -> int:
    """Hamma vaqtdagi eng uzun ketma-ket o'quv kunlari."""
    days = sorted(_history_days(0))
    best = run = 0
    prev = None
    for d in days:
        run = run + 1 if prev is not None and (d - prev).days == 1 else 1
        best = max(best, run)
        prev = d
    return best


def get_user_name() -> str:
    return (get_setting("user_name", "") or "").strip()


# ---------------------------------------------------------------------------
# Imlo holati (v3, CLAUDE-2)
# ---------------------------------------------------------------------------

SPELL_UNCHECKED = "unchecked"   # hali tekshirilmagan
SPELL_OK = "ok"                 # tekshirildi, xato yo'q
SPELL_SUSPECT = "suspect"       # shubhali (lug'atda yo'q / AI xato topdi)
SPELL_CONFIRMED = "confirmed"   # foydalanuvchi "men shunday yozdim, to'g'ri" dedi
SPELL_STATUSES = (SPELL_UNCHECKED, SPELL_OK, SPELL_SUSPECT, SPELL_CONFIRMED)
SPELL_STUDY_OK = (SPELL_OK, SPELL_CONFIRMED)   # faqat shular kartalarda qatnashadi


def set_spell_status(word_id: int, status: str) -> None:
    """So'zning imlo holatini yangilaydi (unchecked/ok/suspect/confirmed).
    Noto'g'ri qiymat berilsa ValueError."""
    if status not in SPELL_STATUSES:
        raise ValueError(f"Noto'g'ri imlo holati: {status!r}")
    conn = get_connection()
    try:
        conn.execute("UPDATE words SET spell_status = ? WHERE id = ?", (status, word_id))
        conn.commit()
    finally:
        conn.close()


def is_studyable(row) -> bool:
    """So'z kartalarda / AI mashqida qatnashishi mumkinmi? Faqat imlo holati
    'ok' yoki 'confirmed' bo'lsa True. Eski qator (ustun yo'q) - True."""
    try:
        status = row["spell_status"]
    except (IndexError, KeyError):
        return True
    return status in SPELL_STUDY_OK


# ---------------------------------------------------------------------------
# Savol banki va statistika (v5, CLAUDE-4)
# ---------------------------------------------------------------------------

BANK_TYPES = ("cloze", "meaning", "correct_sentence", "synonym", "translate_word")
BANK_STATUSES = ("active", "retired", "bad")
BANK_MAX_ACTIVE = 10        # har so'zga eng ko'pi bilan shuncha FAOL savol
BANK_RETIRE_STREAK = 3      # ketma-ket shuncha to'g'ri javob -> 'retired'
BANK_DUP_RATIO = 0.90       # shundan yuqori o'xshash matn - dublikat


def _norm_text(text) -> str:
    """Dublikatni aniqlash uchun matnni tekislaydi (kichik harf, ortiqcha bo'shliqsiz)."""
    return " ".join(str(text or "").casefold().split())


def _bank_row_to_dict(row) -> dict:
    """question_bank qatorini {id, word_id, type, level, status, ..., data: {...}} ga aylantiradi."""
    d = dict(row)
    d["data"] = json.loads(d["data"])
    return d


def add_bank_question(word_id: int, qtype: str, level: str, data: dict) -> int | None:
    """Savolni bankka qo'shadi va id'sini qaytaradi. Dublikat bo'lsa (bir so'z + tur +
    deyarli bir xil matn; 'bad'/'retired' savollar ham hisobga olinadi) - None.
    Tur, daraja yoki `data` (question va correct_answer kerak) noto'g'ri bo'lsa ValueError.
    So'zda faol savollar BANK_MAX_ACTIVE ga yetgan bo'lsa, eng ko'p to'g'ri javob
    berilgani (teng bo'lsa eng eskisi) o'chirilib, o'rniga yangisi qo'yiladi."""
    import difflib
    if qtype not in BANK_TYPES:
        raise ValueError(f"Noto'g'ri savol turi: {qtype!r}")
    if level not in ENGLISH_LEVELS:
        raise ValueError(f"Noto'g'ri daraja: {level!r}")
    if (not isinstance(data, dict) or not str(data.get("question", "")).strip()
            or not str(data.get("correct_answer", "")).strip()):
        raise ValueError("data da 'question' va 'correct_answer' bo'lishi kerak")

    question = _norm_text(data["question"])
    conn = get_connection()
    try:
        if conn.execute("SELECT 1 FROM words WHERE id = ?", (word_id,)).fetchone() is None:
            raise ValueError(f"So'z topilmadi: id={word_id}")
        for r in conn.execute("SELECT data FROM question_bank WHERE word_id = ? AND type = ?",
                              (word_id, qtype)):
            old = _norm_text(json.loads(r["data"]).get("question"))
            if difflib.SequenceMatcher(None, old, question).ratio() >= BANK_DUP_RATIO:
                return None
        active = conn.execute(
            "SELECT id FROM question_bank WHERE word_id = ? AND status = 'active' "
            "ORDER BY correct_count DESC, created_at ASC, id ASC", (word_id,)).fetchall()
        if len(active) >= BANK_MAX_ACTIVE:
            conn.execute("DELETE FROM question_bank WHERE id = ?", (active[0]["id"],))
        cur = conn.execute(
            "INSERT INTO question_bank (word_id, type, level, data) VALUES (?, ?, ?, ?)",
            (word_id, qtype, level, json.dumps(data, ensure_ascii=False)))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_bank_questions(word_ids, level: str | None = None, per_word: int = 1,
                       include_retired: bool = False) -> list[dict]:
    """Bankdan savollar oladi (internet kerak emas). Faqat imlo tekshiruvidan o'tgan
    (db.is_studyable) so'zlarning faol savollari; `level` berilsa faqat shu daraja.
    Har so'zdan eng kam ko'rsatilgani birinchi (`per_word` tagacha). `include_retired`
    bo'lsa faol savol yetmagan so'zga 'retired' savollar ham to'ldiriladi ('bad' - hech qachon)."""
    ids = list(dict.fromkeys(word_ids))
    if not ids:
        return []
    studyable = {r["id"] for r in get_words_by_ids(ids) if is_studyable(r)}
    statuses = ("active", "retired") if include_retired else ("active",)
    conn = get_connection()
    try:
        out: list[dict] = []
        for wid in ids:
            if wid not in studyable:
                continue
            q = ("SELECT * FROM question_bank WHERE word_id = ? AND status IN (%s)"
                 % ",".join("?" * len(statuses)))
            params: list = [wid, *statuses]
            if level is not None:
                q += " AND level = ?"
                params.append(level)
            q += (" ORDER BY (status != 'active') ASC, shown_count ASC, "
                  "COALESCE(last_shown, '') ASC, id ASC LIMIT ?")
            params.append(per_word)
            out += [_bank_row_to_dict(r) for r in conn.execute(q, params)]
        return out
    finally:
        conn.close()


def count_bank_questions(word_id: int, status: str = "active") -> int:
    """So'zning shu holatdagi (standart: faol) savollari soni."""
    conn = get_connection()
    try:
        return conn.execute("SELECT COUNT(*) FROM question_bank WHERE word_id = ? AND status = ?",
                            (word_id, status)).fetchone()[0]
    finally:
        conn.close()


def record_bank_answer(bank_id: int, correct: bool) -> str:
    """Bankdagi savolga berilgan javobni yozadi: shown/correct/streak/last_shown yangilanadi
    va question_log ga tushadi. BANK_RETIRE_STREAK marta ketma-ket to'g'ri bo'lsa savol
    'retired' bo'ladi (o'chirilmaydi); xato javob seriyani 0 qiladi. Yangi holatni qaytaradi."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM question_bank WHERE id = ?", (bank_id,)).fetchone()
        if row is None:
            raise ValueError(f"Savol topilmadi: id={bank_id}")
        streak = row["streak"] + 1 if correct else 0
        status = row["status"]
        if status == "active" and streak >= BANK_RETIRE_STREAK:
            status = "retired"
        now = _now_utc().isoformat()
        conn.execute(
            """UPDATE question_bank SET shown_count = shown_count + 1,
                   correct_count = correct_count + ?, streak = ?, status = ?, last_shown = ?
               WHERE id = ?""", (1 if correct else 0, streak, status, now, bank_id))
        conn.execute(
            "INSERT INTO question_log (word_id, bank_id, type, correct, answered_at) "
            "VALUES (?, ?, ?, ?, ?)", (row["word_id"], bank_id, row["type"], 1 if correct else 0, now))
        conn.commit()
        return status
    finally:
        conn.close()


def mark_bank_question_bad(bank_id: int) -> None:
    """'Yomon savol' tugmasi: savol 'bad' bo'ladi va boshqa chiqmaydi (dublikat tekshiruvida
    esa hisobga olinadi, shuning uchun Gemini xuddi shuni qayta qo'sha olmaydi)."""
    conn = get_connection()
    try:
        conn.execute("UPDATE question_bank SET status = 'bad' WHERE id = ?", (bank_id,))
        conn.commit()
    finally:
        conn.close()


def log_question_answer(word_id: int, qtype: str, correct: bool) -> None:
    """Bankka tushmaydigan (masalan, Gemini'dan bevosita olingan) savol javobini statistika
    uchun yozadi. Mavjud bo'lmagan so'z yoki noma'lum tur bo'lsa ValueError."""
    if qtype not in BANK_TYPES:
        raise ValueError(f"Noto'g'ri savol turi: {qtype!r}")
    conn = get_connection()
    try:
        if conn.execute("SELECT 1 FROM words WHERE id = ?", (word_id,)).fetchone() is None:
            raise ValueError(f"So'z topilmadi: id={word_id}")
        conn.execute("INSERT INTO question_log (word_id, bank_id, type, correct, answered_at) "
                     "VALUES (?, NULL, ?, ?, ?)",
                     (word_id, qtype, 1 if correct else 0, _now_utc().isoformat()))
        conn.commit()
    finally:
        conn.close()


def get_question_stats(weeks: int = 8, worst_limit: int = 10,
                       category: str | None = None, pack: int | None = None) -> dict:
    """Savollar statistikasi (question_log asosida):
      by_type  - [{"type", "total", "correct", "percent"}] eng zaifi birinchi,
      worst_words - eng ko'p xato qilingan so'zlar [{"word_id", "word", "wrong", "total"}],
      by_category - bo'lim bo'yicha [{"category", "total", "correct", "percent"}],
      weekly - oxirgi `weeks` o'quv haftasi [{"week_start": date, "total", "correct", "percent"}]
               (eskidan yangiga; haftaning boshi - dushanba),
      by_pack  - bo'limchalar bo'yicha [{"pack", "total", "correct", "percent"}] (pack tartibida).
    `category` / `pack` berilsa faqat shu bo'lim / bo'limcha so'zlari hisoblanadi.
    Faqat imlo tekshiruvidan o'tgan (is_studyable) so'zlar hisobga olinadi."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT l.word_id, l.type, l.correct, l.answered_at, w.word, w.category,
                      w.pack, w.spell_status
               FROM question_log l JOIN words w ON w.id = l.word_id""").fetchall()
    finally:
        conn.close()
    # D-008: imlo tekshiruvidan o'tmagan so'zlar statistikani buzmasin
    rows = [r for r in rows if is_studyable(r) and _in_scope(r, category, pack)]

    def pct(c, t):
        return int(round(100 * c / t)) if t else 0

    types, cats, words, weekly, packs = {}, {}, {}, {}, {}
    for r in rows:
        for bucket, key in ((types, r["type"]), (cats, r["category"] or DEFAULT_SECTION),
                            (packs, r["pack"] or 1)):
            b = bucket.setdefault(key, [0, 0])
            b[0] += 1
            b[1] += r["correct"]
        w = words.setdefault(r["word_id"], [r["word"], 0, 0])
        w[1] += 1 - r["correct"]
        w[2] += 1
        day = study_day_of(_parse_ts(r["answered_at"]))
        monday = day - timedelta(days=day.weekday())
        wk = weekly.setdefault(monday, [0, 0])
        wk[0] += 1
        wk[1] += r["correct"]

    by_type = sorted(({"type": k, "total": t, "correct": c, "percent": pct(c, t)}
                      for k, (t, c) in types.items()), key=lambda d: (d["percent"], d["type"]))
    by_category = sorted(({"category": k, "total": t, "correct": c, "percent": pct(c, t)}
                          for k, (t, c) in cats.items()), key=lambda d: (d["percent"], d["category"]))
    by_pack = [{"pack": k, "total": t, "correct": c, "percent": pct(c, t)}
               for k, (t, c) in sorted(packs.items())]
    worst = sorted(({"word_id": k, "word": v[0], "wrong": v[1], "total": v[2]}
                    for k, v in words.items() if v[1] > 0),
                   key=lambda d: (-d["wrong"], d["word"]))[:worst_limit]
    this_monday = today_study_day() - timedelta(days=today_study_day().weekday())
    weekly_out = []
    for i in range(weeks - 1, -1, -1):
        m = this_monday - timedelta(weeks=i)
        t, c = weekly.get(m, [0, 0])
        weekly_out.append({"week_start": m, "total": t, "correct": c, "percent": pct(c, t)})
    return {"by_type": by_type, "worst_words": worst, "by_category": by_category,
            "weekly": weekly_out, "by_pack": by_pack}


# ---------------------------------------------------------------------------
# Kartalar holati (doira diagramma) va bo'lim/bo'limcha filtri (CLAUDE-4)
# ---------------------------------------------------------------------------

CARD_STATE_ORDER = ("Yangi", "O'rganilmoqda", "Unutilgan", "O'rtacha unutilgan",
                    "Yaxshi o'rganilgan")
UNCHECKED_LABEL = "Tekshirilmagan"      # imlo tekshiruvidan o'tmagan (Anki'dagi "Excluded" kabi)


def _in_scope(row, category: str | None, pack: int | None) -> bool:
    """So'z tanlangan bo'lim (deck) va bo'limcha (pack) filtriga tushadimi?
    None - "hammasi". Bo'limi bo'sh so'z DEFAULT_SECTION, pack'i bo'sh so'z 1-bo'limcha hisoblanadi."""
    if category is not None and (row["category"] or DEFAULT_SECTION) != category:
        return False
    if pack is not None and (row["pack"] or 1) != pack:
        return False
    return True


def get_card_state_counts(category: str | None = None, pack: int | None = None,
                          separate_unchecked: bool = True) -> dict:
    """Kartalar holati bo'yicha sonlar (doira diagramma uchun).
    Qaytaradi: {"counts": {holat: soni}, "total": jami}. Holatlar tartibi: CARD_STATE_ORDER,
    oxirida UNCHECKED_LABEL. `separate_unchecked` bo'lsa imlo tekshiruvidan o'tmagan so'zlar
    alohida "Tekshirilmagan" bo'lagiga ajratiladi, aks holda o'z holati bo'yicha sanaladi.
    `category` / `pack` - bo'lim / bo'limcha filtri (None - hammasi)."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT category, pack, state, due, last_review, spell_status FROM words").fetchall()
    finally:
        conn.close()
    now = _now_utc()
    counts = {label: 0 for label in CARD_STATE_ORDER}
    counts[UNCHECKED_LABEL] = 0
    total = 0
    for r in rows:
        if not _in_scope(r, category, pack):
            continue
        total += 1
        if separate_unchecked and not is_studyable(r):
            counts[UNCHECKED_LABEL] += 1
        else:
            counts[word_label(r, now)] += 1
    return {"counts": counts, "total": total}
