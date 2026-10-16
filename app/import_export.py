"""
import_export.py  (CLAUDE-3, 3.5-bosqich, D-012)
------------------------------------------------
So'zlarni CSV/TSV ga chiqarish (eksport) va ulardan olish (import).

Eksport turlari:
  A  "faqat so'zlar":  word, translation, example_sentence, category
  B  "to'liq zaxira":  A + spell_status, audio_source, FSRS holati
                       (fsrs_state, fsrs_difficulty, fsrs_stability, due_date
                        + tiklash uchun fsrs_card_id, fsrs_step, last_review)

Import xavfsizligi: faqat .csv/.tsv, eng ko'pi 5 MB va 10 000 qator,
eval()/exec() HECH QACHON ishlatilmaydi, buzilgan fayl ilovani yiqitmaydi
(ImportResult.error da xabar qaytadi). Butun import BITTA tranzaksiyada:
xato bo'lsa bazaga hech narsa yozilmaydi.

Import qilingan so'zlar spell_status='unchecked' bilan kiradi (to'liq zaxira
faylida 'ok'/'confirmed' bo'lsa shu saqlanadi, aks holda tiklangan so'zlar
tekshiruv tugamaguncha kartalardan chiqib ketardi) va fon navbatiga
(import_queue jadvali) qo'yiladi: imlo tekshiruvi (50 tadan bitta so'rov) va
audio. Navbat bazada saqlanadi - ilova yopilsa, keyingi ochilishda davom etadi.
"""

from __future__ import annotations

import csv
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from . import database as db

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 10_000
SPELL_BATCH = 50
ALLOWED_EXTENSIONS = (".csv", ".tsv")

MAX_WORD_LEN = 100
MAX_TRANSLATION_LEN = 500
MAX_EXAMPLE_LEN = 1000
MAX_CATEGORY_LEN = 100

EXPORT_A_COLUMNS = ["word", "translation", "example_sentence", "category"]
EXPORT_B_COLUMNS = EXPORT_A_COLUMNS + [
    "spell_status", "audio_source", "fsrs_state", "fsrs_difficulty", "fsrs_stability",
    "due_date",
    # Spetsifikatsiyadan tashqari, lekin FSRS kartasini TO'LIQ tiklash uchun kerak:
    "fsrs_card_id", "fsrs_step", "last_review",
]
MODE_WORDS = "words"      # eksport A
MODE_BACKUP = "backup"    # eksport B

TASK_SPELL = "spell"
TASK_AUDIO = "audio"

_HEADER_ALIASES = {"example": "example_sentence", "due": "due_date",
                   "difficulty": "fsrs_difficulty", "stability": "fsrs_stability"}


# ---------------------------------------------------------------------------
# Umumiy yordamchilar
# ---------------------------------------------------------------------------

@dataclass
class ImportResult:
    """Importning yakuniy natijasi.

    added: qo'shilgan so'zlar soni; duplicates: allaqachon bor/faylda takrorlangan;
    invalid: yaroqsiz qatorlar soni; invalid_rows: (qator raqami, sabab) ro'yxati
    (eng ko'pi 20 tasi); error: butun fayl rad etilgan bo'lsa xabar, aks holda None;
    added_ids: qo'shilgan so'zlar id'lari; queued: navbatga qo'yilgan vazifalar soni.
    """
    added: int = 0
    duplicates: int = 0
    invalid: int = 0
    invalid_rows: list = field(default_factory=list)
    error: str | None = None
    added_ids: list = field(default_factory=list)
    queued: int = 0


def _delimiter_for(path: Path) -> str:
    """Kengaytmaga qarab ajratuvchi: .tsv -> tab, .csv -> vergul."""
    return "\t" if Path(path).suffix.lower() == ".tsv" else ","


def _check_extension(path: Path) -> str | None:
    """Kengaytma ruxsat etilganmi? Yo'q bo'lsa xabar qaytaradi."""
    if Path(path).suffix.lower() not in ALLOWED_EXTENSIONS:
        return "Faqat .csv yoki .tsv fayllar qabul qilinadi."
    return None


def _parse_dt(value: str) -> str | None:
    """ISO sanani UTC (tz bilan) ISO satrga aylantiradi; yaroqsiz bo'lsa None."""
    value = (value or "").strip()
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _to_float(value: str) -> float | None:
    """Matnni float ga o'giradi; bo'sh/yaroqsiz bo'lsa None."""
    try:
        return float((value or "").strip())
    except ValueError:
        return None


def _to_int(value: str) -> int | None:
    """Matnni int ga o'giradi; bo'sh/yaroqsiz bo'lsa None."""
    try:
        return int((value or "").strip())
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Eksport
# ---------------------------------------------------------------------------

def export_words(path: Path | str, mode: str = MODE_WORDS) -> int:
    """So'zlarni faylga yozadi (A yoki B tur). Yozilgan so'zlar sonini qaytaradi.

    Fayl UTF-8 (BOM bilan: Excel o'zbekcha harflarni to'g'ri ochadi).
    Ruxsat etilmagan kengaytma yoki noma'lum mode - ValueError.
    """
    path = Path(path)
    problem = _check_extension(path)
    if problem:
        raise ValueError(problem)
    if mode not in (MODE_WORDS, MODE_BACKUP):
        raise ValueError(f"Noma'lum eksport turi: {mode!r}")
    columns = EXPORT_A_COLUMNS if mode == MODE_WORDS else EXPORT_B_COLUMNS

    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT * FROM words ORDER BY category, pack, id").fetchall()
    finally:
        conn.close()

    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh, delimiter=_delimiter_for(path))
        writer.writerow(columns)
        for r in rows:
            values = {
                "word": r["word"], "translation": r["translation"],
                "example_sentence": r["example"] or "", "category": r["category"] or "",
            }
            if mode == MODE_BACKUP:
                values.update({
                    "spell_status": r["spell_status"] or db.SPELL_UNCHECKED,
                    "audio_source": r["audio_source"] or "none",
                    "fsrs_card_id": _blank(r["card_id"]),
                    "fsrs_state": _blank(r["state"]),
                    "fsrs_step": _blank(r["step"]),
                    "fsrs_difficulty": _blank(r["difficulty"]),
                    "fsrs_stability": _blank(r["stability"]),
                    "due_date": r["due"] or "",
                    "last_review": r["last_review"] or "",
                })
            writer.writerow([values[c] for c in columns])
    return len(rows)


def _blank(value) -> str:
    """None -> '', aks holda satr."""
    return "" if value is None else str(value)


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------

def _read_rows(path: Path):
    """Faylni xavfsiz o'qiydi. (header, rows, xato_xabari) qaytaradi.

    Tekshiradi: kengaytma, hajm (5 MB), kodlash (UTF-8), qator soni (10 000),
    majburiy ustunlar (word, translation). Hech qanday kod bajarilmaydi.
    """
    problem = _check_extension(path)
    if problem:
        return None, None, problem
    try:
        size = os.path.getsize(path)
    except OSError:
        return None, None, "Faylni ochib bo'lmadi."
    if size > MAX_FILE_BYTES:
        return None, None, f"Fayl juda katta ({size / 1024 / 1024:.1f} MB). Eng ko'pi 5 MB."
    if size == 0:
        return None, None, "Fayl bo'sh."
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return None, None, "Fayl UTF-8 kodlashda emas. Excel'da «CSV UTF-8» sifatida saqlang."
    except OSError:
        return None, None, "Faylni o'qib bo'lmadi."
    if "\x00" in text:
        return None, None, "Fayl buzilgan (ikkilik ma'lumot)."

    try:
        reader = csv.reader(text.splitlines(), delimiter=_delimiter_for(path))
        header_row = next(reader, None)
        if not header_row:
            return None, None, "Faylda sarlavha qatori yo'q."
        header = [_HEADER_ALIASES.get(h.strip().lower(), h.strip().lower()) for h in header_row]
        if "word" not in header or "translation" not in header:
            return None, None, "Sarlavhada «word» va «translation» ustunlari bo'lishi shart."
        rows = []
        for raw in reader:
            if not any(cell.strip() for cell in raw):
                continue   # bo'sh qator
            rows.append(raw)
            if len(rows) > MAX_ROWS:
                return None, None, f"Qatorlar soni {MAX_ROWS} dan oshdi. Faylni bo'lib import qiling."
    except csv.Error as exc:
        return None, None, f"CSV buzilgan: {exc}"
    return header, rows, None


def _fsrs_fields(cell: dict, fallback_id: int) -> dict | None:
    """Zaxira faylidagi FSRS ustunlarini tekshirib, bazaga yoziladigan maydonlarga
    aylantiradi. Yaroqsiz/yetarli emas bo'lsa None (so'z 'yangi' sifatida kiradi)."""
    state = _to_int(cell.get("fsrs_state", ""))
    stability = _to_float(cell.get("fsrs_stability", ""))
    difficulty = _to_float(cell.get("fsrs_difficulty", ""))
    due = _parse_dt(cell.get("due_date", ""))
    if state not in (1, 2, 3) or stability is None or difficulty is None or due is None:
        return None
    if stability <= 0 or not (0 < difficulty <= 10):
        return None
    step = _to_int(cell.get("fsrs_step", ""))
    card_id = _to_int(cell.get("fsrs_card_id", ""))
    return {
        "card_id": card_id if card_id is not None else fallback_id,
        "state": state, "step": step, "stability": stability,
        "difficulty": difficulty, "due": due,
        "last_review": _parse_dt(cell.get("last_review", "")),
    }


def import_words(path: Path | str) -> ImportResult:
    """Fayldan so'zlarni bazaga qo'shadi va ularni fon navbatiga qo'yadi.

    Dublikat (katta-kichik harfga e'tiborsiz, bazada yoki faylning o'zida) o'tkazib
    yuboriladi. Butun import bitta tranzaksiyada. Natija: ImportResult.
    """
    path = Path(path)
    result = ImportResult()
    header, rows, error = _read_rows(path)
    if error:
        result.error = error
        return result

    conn = db.get_connection()
    try:
        seen = {r["word"].strip().lower() for r in conn.execute("SELECT word FROM words")}
        now = db._now_utc().isoformat()
        id_seed = int(time.time() * 1000)
        conn.execute("BEGIN")
        for line_no, raw in enumerate(rows, start=2):
            cell = {h: (raw[i].strip() if i < len(raw) else "") for i, h in enumerate(header)}
            word, translation = cell.get("word", ""), cell.get("translation", "")
            example = cell.get("example_sentence", "")
            category = db._clean_category(cell.get("category", ""))

            reason = None
            if not word or not translation:
                reason = "«word» yoki «translation» bo'sh"
            elif (len(word) > MAX_WORD_LEN or len(translation) > MAX_TRANSLATION_LEN
                  or len(example) > MAX_EXAMPLE_LEN or len(category) > MAX_CATEGORY_LEN):
                reason = "maydon juda uzun"
            if reason:
                result.invalid += 1
                if len(result.invalid_rows) < 20:
                    result.invalid_rows.append((line_no, reason))
                continue

            key = word.lower()
            if key in seen:
                result.duplicates += 1
                continue
            seen.add(key)

            status = cell.get("spell_status", "").lower()
            if status not in (db.SPELL_OK, db.SPELL_CONFIRMED):
                status = db.SPELL_UNCHECKED
            fsrs = _fsrs_fields(cell, id_seed + len(result.added_ids)) if "fsrs_state" in header else None
            fsrs = fsrs or {"card_id": None, "state": None, "step": None, "stability": None,
                            "difficulty": None, "due": None, "last_review": None}

            pack = db._next_pack(conn, category)
            cur = conn.execute(
                """INSERT INTO words (word, translation, example, category, pack, added_date,
                                      spell_status, audio_source, audio_path,
                                      card_id, state, step, stability, difficulty, due, last_review)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'none', NULL, ?, ?, ?, ?, ?, ?, ?)""",
                (word, translation, example, category, pack, now, status,
                 fsrs["card_id"], fsrs["state"], fsrs["step"], fsrs["stability"],
                 fsrs["difficulty"], fsrs["due"], fsrs["last_review"]))
            wid = cur.lastrowid
            result.added += 1
            result.added_ids.append(wid)
            if status == db.SPELL_UNCHECKED:
                conn.execute("INSERT INTO import_queue (word_id, task) VALUES (?, ?)", (wid, TASK_SPELL))
                result.queued += 1
            conn.execute("INSERT INTO import_queue (word_id, task) VALUES (?, ?)", (wid, TASK_AUDIO))
            result.queued += 1
        conn.commit()
    except Exception as exc:   # tranzaksiya bekor: bazaga hech narsa yozilmaydi
        conn.rollback()
        return ImportResult(error=f"Import bajarilmadi: {exc}")
    finally:
        conn.close()
    return result


# ---------------------------------------------------------------------------
# Fon navbati (import_queue jadvali - bazada saqlanadi)
# ---------------------------------------------------------------------------

def pending_count(task: str | None = None) -> int:
    """Navbatda kutayotgan vazifalar soni (task berilsa faqat shu tur)."""
    conn = db.get_connection()
    try:
        if task:
            return conn.execute("SELECT COUNT(*) FROM import_queue WHERE task = ?", (task,)).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM import_queue").fetchone()[0]
    finally:
        conn.close()


def _pending(task: str, limit: int) -> list:
    """Navbatdagi eng eski `limit` ta vazifa (id, word_id)."""
    conn = db.get_connection()
    try:
        return conn.execute("SELECT id, word_id FROM import_queue WHERE task = ? ORDER BY id LIMIT ?",
                            (task, limit)).fetchall()
    finally:
        conn.close()


def _remove(queue_ids: list) -> None:
    """Bajarilgan vazifalarni navbatdan o'chiradi."""
    if not queue_ids:
        return
    conn = db.get_connection()
    try:
        marks = ",".join("?" * len(queue_ids))
        conn.execute(f"DELETE FROM import_queue WHERE id IN ({marks})", queue_ids)
        conn.commit()
    finally:
        conn.close()


def enqueue_unchecked() -> int:
    """Imlo holati 'unchecked' bo'lib qolgan, lekin navbatda YO'Q so'zlarni imlo
    navbatiga qo'shadi (masalan, so'z qo'shish sahifasi tekshiruvni boshlamagan
    bo'lsa). Qayta chaqirish xavfsiz: navbatdagi so'z ikki marta qo'shilmaydi.
    Qo'shilgan vazifalar sonini qaytaradi."""
    conn = db.get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO import_queue (word_id, task)
               SELECT id, ? FROM words
               WHERE spell_status = ?
                 AND id NOT IN (SELECT word_id FROM import_queue WHERE task = ?)""",
            (TASK_SPELL, db.SPELL_UNCHECKED, TASK_SPELL))
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


@dataclass
class QueueReport:
    """Navbat ishlovi natijasi: done - bajarilgan, total - boshlanishdagi jami,
    remaining - qolgani, stopped - to'xtatildi, errors - xabarlar."""
    done: int = 0
    total: int = 0
    remaining: int = 0
    stopped: bool = False
    errors: list = field(default_factory=list)


def progress_text(done: int, total: int) -> str:
    """Interfeys uchun matn: 'Tekshirilmoqda: 45/200'."""
    return f"Tekshirilmoqda: {done}/{total}"


def process_queue(spell_fn: Callable | None, audio_fn: Callable | None,
                  progress_cb: Callable[[int, int], None] | None = None,
                  should_stop: Callable[[], bool] | None = None,
                  batch_size: int = SPELL_BATCH) -> QueueReport:
    """Navbatni ketma-ket ishlaydi: avval imlo (batch_size tadan bitta so'rov), keyin audio.

    spell_fn(rows) -> {word_id: 'ok'|'suspect'}: so'zlar partiyasi uchun hukm.
    audio_fn(word_id): bitta so'z uchun audio (masalan audio_service.ensure_word_audio).
    Provayder xato bersa (internet yo'q) o'sha tur to'xtaydi, vazifalar navbatda
    QOLADI - keyingi ishga tushirishda davom etadi. progress_cb(done, total).
    """
    report = QueueReport(total=pending_count())
    stop = should_stop or (lambda: False)

    def tick(n: int = 1):
        report.done += n
        if progress_cb:
            progress_cb(report.done, report.total)

    if progress_cb and report.total:
        progress_cb(0, report.total)

    if spell_fn is not None:
        while not stop():
            batch = _pending(TASK_SPELL, batch_size)
            if not batch:
                break
            rows = db.get_words_by_ids([b["word_id"] for b in batch])
            try:
                verdicts = spell_fn(rows) or {}
            except Exception as exc:
                report.errors.append(f"Imlo tekshiruvi to'xtadi: {type(exc).__name__}: {exc}")
                break
            for r in rows:
                status = verdicts.get(r["id"])
                if status in (db.SPELL_OK, db.SPELL_SUSPECT):
                    db.set_spell_status(r["id"], status)
            # Hukm olmagan so'zlar ham navbatdan chiqadi (qayta-qayta urinib qotib qolmaslik
            # uchun); ular 'unchecked' bo'lib qoladi va kartalarda qatnashmaydi.
            _remove([b["id"] for b in batch])
            tick(len(batch))

    if audio_fn is not None:
        while not stop():
            batch = _pending(TASK_AUDIO, 1)
            if not batch:
                break
            try:
                audio_fn(batch[0]["word_id"])
            except Exception as exc:
                report.errors.append(f"Audio olish to'xtadi: {type(exc).__name__}: {exc}")
                break
            _remove([batch[0]["id"]])
            tick()

    report.stopped = stop()
    report.remaining = pending_count()
    return report


# ---------------------------------------------------------------------------
# Imlo tekshiruvi (mahalliy + Gemini batch)
# ---------------------------------------------------------------------------

def local_spell_batch(rows) -> dict:
    """Mahalliy imlo tekshiruvi: so'z lug'atda bo'lsa 'ok', aks holda 'suspect'.

    Mantiq CLAUDE-2 ning spell_checker.local_check funksiyasidan olinadi (so'z qo'shish
    sahifasi bilan BIR XIL qoida - takrorlanmaydi). Internet va API kalit kerak emas.
    pyspellchecker o'rnatilmagan bo'lsa istisno ko'tariladi (navbat saqlanib qoladi).
    """
    from . import spell_checker   # kech import (PySide6 va pyspellchecker kerak)
    verdicts = {}
    for r in rows:
        unknown, _ = spell_checker.local_check(r["word"])
        verdicts[r["id"]] = db.SPELL_SUSPECT if unknown else db.SPELL_OK
    return verdicts


def spell_batch(rows) -> dict:
    """Import navbati uchun standart imlo tekshiruvi: mahalliy + (kalit va internet bo'lsa)
    Gemini, hammasi BITTA so'rovda (partiya 50 tagacha).

    Qoida CLAUDE-2 nikidek: AI tekshirgan bo'lsa uning hukmi hal qiladi (kam uchraydigan
    so'zni lug'at bilmasligi mumkin); AI ishlamasa (kalit yo'q, 'spell_check_ai'=0, internet
    yo'q, javob buzuq) mahalliy natija bilan davom etiladi.
    """
    from . import spell_checker
    verdicts = local_spell_batch(rows)
    if db.get_setting("spell_check_ai", "1") == "0":
        return verdicts
    if not (db.get_setting("gemini_api_key", "") or "").strip():
        return verdicts
    entries = []
    for r in rows:
        unknown, _ = spell_checker.local_check(r["word"])
        entries.append({"id": r["id"], "word": r["word"], "translation": r["translation"],
                        "example": r["example"] or "", "local_unknown": unknown})
    try:
        from . import gemini_service
        ai = gemini_service.check_spelling_batch(entries)
    except Exception:
        return verdicts
    for wid, res in ai.items():
        if wid not in verdicts or not isinstance(res.get("word_ok"), bool):
            continue
        bad = (res["word_ok"] is False or res.get("translation_ok") is False
               or res.get("example_ok") is False)
        verdicts[wid] = db.SPELL_SUSPECT if bad else db.SPELL_OK
    return verdicts


# ---------------------------------------------------------------------------
# Qt qismi: fon oqimi
# ---------------------------------------------------------------------------

try:
    from PySide6.QtCore import Signal
    from .background import ThreadWorker
except ImportError:   # PySide6 yo'q muhit (faqat mantiq sinovlari)
    ThreadWorker = None


if ThreadWorker is not None:

    class ImportQueueWorker(ThreadWorker):
        """Navbatni fon oqimida ishlaydi va jarayonni signal bilan xabar qiladi.
        (QThread emas, daemon oqim: ilovadan chiqishda xato bermaydi - background.ThreadWorker.)"""

        progress = Signal(int, int)          # (bajarildi, jami)
        queue_finished = Signal(object)      # QueueReport (D-010: obyekt uchun object)

        def __init__(self, spell_fn=None, audio_fn=None, parent=None):
            """spell_fn va audio_fn - process_queue dagi kabi (None bo'lsa o'sha tur o'tkaziladi)."""
            super().__init__(parent)
            self.spell_fn = spell_fn
            self.audio_fn = audio_fn
            self._stop = False

        def request_stop(self):
            """Ishni xavfsiz to'xtatishni so'raydi (joriy vazifa tugagach)."""
            self._stop = True

        def run(self):
            """Navbatni ishlaydi; kutilmagan xato bo'lsa ham signal chiqaradi."""
            try:
                report = process_queue(self.spell_fn, self.audio_fn,
                                       progress_cb=lambda d, t: self._safe_emit(self.progress, d, t),
                                       should_stop=lambda: self._stop)
            except Exception as exc:
                report = QueueReport(errors=[str(exc)], remaining=pending_count())
            self._safe_emit(self.queue_finished, report)
