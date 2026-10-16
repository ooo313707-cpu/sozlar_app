"""
bank_service.py
---------------
Savol bankini AI mashqqa ulaydigan qatlam (Qt va internetsiz - to'liq test qilinadi).

Oqim:
  1) plan_session(words)   - har so'zga tur biriktiriladi; bankda shu tur uchun faol
                             savol bo'lsa, o'sha olinadi (internet kerak emas).
  2) Qolganlari uchun Gemini so'raladi (plan.ai_payload) - bankdan topilmaganlar uchun.
  3) finish_session(plan, raw) - Gemini javobidan savollar tuziladi, bank savollari
                             bilan tartib saqlanib birlashtiriladi va YAROQLI (validate_question
                             dan o'tgan, faqat BANK_TYPES) yangi savollar bankka yoziladi.
  4) record_answer(q, correct) - javob bankka (yoki bankka tushmagan savol bo'lsa
                             umumiy statistikaga) yoziladi.
  5) mark_bad(q)           - "Yomon savol" tugmasi.

Ochiq savollar (sentence, fix, situation, passage) va translate_word (kod yasaydi)
bankka tushmaydi.
"""

import random

from . import database as db
from . import questions as qs

# Bankka tushadigan turlar: Gemini yaratadi VA javobi kompyuterda tekshiriladi.
BANKABLE = set(qs.EASY_TYPES) & set(db.BANK_TYPES)


class SessionPlan:
    """Bitta mashq sessiyasi rejasi: har so'z uchun tur va (bo'lsa) bankdagi savol."""

    def __init__(self, words, items, level):
        self.words, self.items, self.level = words, items, level

    @property
    def bank_count(self) -> int:
        """Bankdan olingan savollar soni."""
        return sum(1 for it in self.items if it.get("bank_q") is not None)

    @property
    def ai_items(self) -> list[dict]:
        """Gemini'dan so'raladigan elementlar (bankdan topilmagan AI turlari)."""
        return [it for it in self.items
                if it.get("bank_q") is None and it["type"] in qs.AI_GENERATED]

    def ai_payload(self) -> list[dict]:
        """gemini_service.generate_questions uchun ro'yxat (bo'sh bo'lsa Gemini kerak emas)."""
        leech = db.get_leech_ids([it["row"]["id"] for it in self.ai_items])
        return [{"word": it["row"]["word"], "translation": it["row"]["translation"],
                 "type": it["type"], "leech": it["row"]["id"] in leech}
                for it in self.ai_items]


def question_to_data(q: dict) -> dict:
    """Savolni bankdagi JSON ko'rinishiga o'tkazadi (to'liq tuzilma saqlanadi)."""
    if qs.is_mcq(q):
        correct_answer = q["options"][q["correct"]]
    else:
        correct_answer = q["answer"]
    return {"question": q["question"], "options": q["options"], "correct": q["correct"],
            "answer": q["answer"], "accept": list(q.get("accept") or []),
            "tip": q.get("tip"), "correct_answer": correct_answer}


def bank_to_question(bank_row: dict, word_row, rng: random.Random | None = None) -> dict:
    """Bankdagi yozuvni questions.py tuzilmasidagi savolga aylantiradi
    (variantlar har safar qayta aralashtiriladi, javob joyini yodlab olmaslik uchun)."""
    rng = rng or random.Random()
    d = bank_row["data"]
    q = qs._base(word_row, bank_row["type"])
    q["question"] = d["question"]
    q["tip"] = d.get("tip")
    q["source"] = "bank"
    q["bank_id"] = bank_row["id"]
    if d.get("options") is not None:
        q["options"] = list(d["options"])
        correct = d.get("correct")
        if not isinstance(correct, int) or not (0 <= correct < len(q["options"])):
            correct = q["options"].index(d["correct_answer"])
        q["correct"] = correct
        qs._shuffle_options(q, rng)
    else:
        q["answer"] = d.get("answer") or d["correct_answer"]
        q["accept"] = list(d.get("accept") or [])
    return q


def plan_session(words, rng: random.Random | None = None) -> SessionPlan:
    """Har so'zga tur biriktiradi va bankdan mos savollarni oladi."""
    rng = rng or random.Random()
    level = db.get_english_level()
    # D-008: imlo tekshiruvidan o'tmagan so'zlar bankdan ham, Gemini'ga ham ketmasin
    words = [w for w in words if db.is_studyable(w)]
    items = qs.build_items(words, rng)
    ids = [it["row"]["id"] for it in items if it["type"] in BANKABLE]
    by_word: dict[int, list[dict]] = {}
    if ids:
        for b in db.get_bank_questions(ids, level=level, per_word=db.BANK_MAX_ACTIVE):
            by_word.setdefault(b["word_id"], []).append(b)
    for it in items:
        it["bank_q"] = None
        if it["type"] not in BANKABLE:
            continue
        # eng kam ko'rsatilgan (get_bank_questions tartibi) mos turdagi savol
        for b in by_word.get(it["row"]["id"], []):
            if b["type"] == it["type"]:
                try:
                    it["bank_q"] = bank_to_question(b, it["row"], rng)
                except (KeyError, ValueError, TypeError):
                    continue          # buzilgan yozuv - o'tkazib yuboramiz
                break
    return SessionPlan(words, items, level)


def finish_session(plan: SessionPlan, raw, rng: random.Random | None = None):
    """Bank savollari + Gemini javobidan tuzilgan savollarni BIRLASHTIRADI (tartib saqlanadi)
    va yangi yaroqli savollarni bankka yozadi.
    Qaytaradi: (questions, fallback_soni, bankka_qoshilgan_soni)."""
    rng = rng or random.Random()
    rest = [it for it in plan.items if it.get("bank_q") is None]
    built, fallbacks = qs.build_questions(rest, raw, rng)
    built_iter = iter(built)
    out, saved = [], 0
    for it in plan.items:
        if it.get("bank_q") is not None:
            out.append(it["bank_q"])
            continue
        q = next(built_iter)
        out.append(q)
        if q.get("source") == "ai" and q["type"] in BANKABLE:
            bid = None
            try:
                bid = db.add_bank_question(q["word_id"], q["type"], plan.level,
                                           question_to_data(q))
            except Exception:
                bid = None            # bank xatosi mashqni to'xtatmasligi kerak
            if bid is not None:
                q["bank_id"] = bid
                saved += 1
    return out, fallbacks, saved


def record_answer(q: dict, correct: bool) -> None:
    """Javobni yozadi: bankdagi savol bo'lsa record_bank_answer, bo'lmasa umumiy log."""
    try:
        if q.get("bank_id"):
            db.record_bank_answer(q["bank_id"], correct)
        else:
            db.log_question_answer(q["word_id"], q["type"], correct)
    except Exception:
        pass                          # statistika xatosi mashqni to'xtatmasligi kerak


def mark_bad(q: dict) -> bool:
    """'Yomon savol' tugmasi: bankdagi savolni 'bad' qiladi. Bankda bo'lmasa False."""
    if not q.get("bank_id"):
        return False
    db.mark_bank_question_bad(q["bank_id"])
    return True
