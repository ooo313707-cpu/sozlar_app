"""
questions.py
------------
AI mashqidagi savollar mantig'i (Qt va internetsiz - to'liq test qilinadi).

Savol turlari:
  Mahalliy tekshiriladigan (tez, internetsiz):
    meaning          - ma'no tanlash (4 variant)
    cloze            - bo'sh joyni to'ldirish (yoziladi)
    correct_sentence - qaysi gap to'g'ri (3 gap)
    synonym          - sinonim/antonim (4 variant)
    translate_word   - o'zbekcha -> inglizcha so'z (yoziladi); AI'siz, kod yasaydi
  AI tekshiradigan (ochiq javob, sessiya oxirida BITTA so'rovda baholanadi):
    sentence         - so'z bilan o'z gapingni yoz
    fix              - xatoni tuzat
    situation        - vaziyat tasvirla
    passage          - qisqa matn + savol

Qiyinlik: yangi/o'rganilayotgan/unutilgan so'zlarga yengil turlar,
yaxshi o'rganilgan (Review va ehtimol >= 0.90) so'zlarga qiyin turlar.

Baho kodda hisoblanadi (AI faqat tuzilgan natija qaytaradi):
  so'z xato -> Again;  so'z to'g'ri, grammatika xato -> Hard;
  to'g'ri -> Good;  to'g'ri va tabiiy (faqat o'zi yozadigan turlarda) -> Easy.
  Mahalliy turlar: to'g'ri -> Good, imlo xatosi (1 harf) -> Hard, xato -> Again.
"""

import random
import re
import unicodedata

from fsrs import State

from . import database as db

EASY_TYPES = ["cloze", "meaning", "correct_sentence", "synonym"]
HARD_TYPES = ["sentence", "fix", "situation", "passage"]
LOCAL_TYPES = {"meaning", "cloze", "correct_sentence", "synonym", "translate_word"}
OPEN_TYPES = {"sentence", "fix", "situation", "passage"}
SELF_WRITTEN = {"sentence", "situation"}      # "Easy" faqat shu turlarda berilishi mumkin
AI_GENERATED = set(EASY_TYPES) | set(HARD_TYPES)   # Gemini yaratadi (translate_word - kod)

TYPE_TITLES = {
    "meaning": "Ma'no tanlang",
    "cloze": "Bo'sh joyni to'ldiring",
    "correct_sentence": "Qaysi gap to'g'ri?",
    "synonym": "Sinonim / antonim",
    "translate_word": "Tarjima",
    "sentence": "O'z gapingizni yozing",
    "fix": "Xatoni tuzating",
    "situation": "Vaziyat",
    "passage": "Matn va savol",
}


# ---------------------------------------------------------------------------
# Matn yordamchilari
# ---------------------------------------------------------------------------

def norm(text) -> str:
    """Solishtirish uchun: kichik harf, tinish belgilarsiz, bitta bo'sh joy."""
    s = unicodedata.normalize("NFKD", str(text or "")).lower()
    s = s.replace("-", " ").replace("’", "'")
    s = re.sub(r"[^a-z0-9' ]+", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _squash(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def match_row(raw_word, rows):
    """Gemini qaytargan so'zni bizning qatorlarga moslashtiradi (aniq, keyin qisman)."""
    target = _squash(raw_word)
    if not target:
        return None
    for r in rows:
        if _squash(r["word"]) == target:
            return r
    for r in rows:
        w = _squash(r["word"])
        if w and (w in target or target in w):
            return r
    return None


def within_one_edit(a: str, b: str) -> bool:
    """Ikki satr orasida eng ko'pi 1 ta harf almashgan/qo'shilgan/tushgan bo'lsa True."""
    if a == b:
        return True
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(x != y for x, y in zip(a, b)) <= 1
    if la > lb:
        a, b = b, a
    i = j = 0
    skipped = False
    while i < len(a) and j < len(b):
        if a[i] == b[j]:
            i += 1
            j += 1
        else:
            if skipped:
                return False
            skipped = True
            j += 1
    return True


def check_text_answer(user_answer, answer, accept=()) -> str:
    """'exact' | 'typo' (1 harflik imlo xatosi) | 'wrong'."""
    n = norm(user_answer)
    if not n:
        return "wrong"
    forms = [norm(answer)] + [norm(a) for a in (accept or [])]
    forms = [f for f in forms if f]
    if n in forms:
        return "exact"
    for f in forms:
        if len(f) >= 5 and within_one_edit(n, f):
            return "typo"
    return "wrong"


# ---------------------------------------------------------------------------
# Tur tanlash
# ---------------------------------------------------------------------------

def is_hard_word(row, now=None) -> bool:
    """Yaxshi o'rganilgan (Review va eslab qolish ehtimoli >= 0.90) so'z - qiyin turlar uchun."""
    if row["last_review"] is None or row["state"] != State.Review.value:
        return False
    ret = db.get_retrievability(row, now)
    return ret is not None and ret >= 0.90


def pick_type(row, rng: random.Random, avoid: str | None = None) -> str:
    pool = (HARD_TYPES if is_hard_word(row) else EASY_TYPES) + ["translate_word"]
    if avoid and avoid in pool and len(pool) > 1:
        pool = [t for t in pool if t != avoid]
    return rng.choice(pool)


def build_items(rows, rng: random.Random | None = None) -> list[dict]:
    """Har bir so'zga savol turini biriktiradi (ketma-ket bir xil tur kam uchrasin)."""
    rng = rng or random.Random()
    items, prev = [], None
    for r in rows:
        t = pick_type(r, rng, avoid=prev)
        items.append({"row": r, "type": t})
        prev = t
    return items


# ---------------------------------------------------------------------------
# Savol tuzilmasi
# ---------------------------------------------------------------------------

def _base(row, qtype: str) -> dict:
    return {
        "word_id": row["id"], "word": row["word"], "translation": row["translation"],
        "type": qtype, "question": "", "options": None, "correct": None,
        "answer": None, "accept": [], "passage": None, "tip": None,
        "checker": "local" if qtype in LOCAL_TYPES else "ai",
        "source": "local",     # "ai" - Gemini yaratgan va validate_question'dan o'tgan
        "bank_id": None,       # savol bankdagi yozuvi (bo'lsa)
    }


def is_mcq(q: dict) -> bool:
    return q.get("options") is not None


def _shuffle_options(q: dict, rng: random.Random):
    correct_text = q["options"][q["correct"]]
    rng.shuffle(q["options"])
    q["correct"] = q["options"].index(correct_text)


def _clean_str(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def _tip_of(raw: dict):
    tip = _clean_str(raw.get("tip"))
    ex = [e.strip() for e in (raw.get("examples") or []) if isinstance(e, str) and e.strip()][:2]
    if not tip and not ex:
        return None
    text = tip
    if ex:
        text += ("\n" if text else "") + "\n".join(f"• {e}" for e in ex)
    return text


def validate_question(raw, row, qtype: str, rng: random.Random | None = None):
    """Gemini qaytargan xom savolni tekshiradi va tuzilmaga o'tkazadi.
    Yaroqsiz bo'lsa None (chaqiruvchi mahalliy savol bilan almashtiradi).
    Savol turi BIZ bergan tur (Gemini qaytargani emas)."""
    rng = rng or random.Random()
    if not isinstance(raw, dict) or qtype not in AI_GENERATED:
        return None
    question = _clean_str(raw.get("question"))
    if not question:
        return None
    q = _base(row, qtype)
    q["source"] = "ai"
    q["question"] = question
    q["tip"] = _tip_of(raw)

    if qtype in ("meaning", "synonym", "correct_sentence"):
        opts = raw.get("options")
        if not isinstance(opts, list):
            return None
        opts = [_clean_str(o) for o in opts]
        lo, hi = (2, 4) if qtype == "correct_sentence" else (3, 5)
        if not (lo <= len(opts) <= hi) or any(not o for o in opts):
            return None
        if len({o.lower() for o in opts}) != len(opts):
            return None
        c = raw.get("correct")
        if isinstance(c, bool) or not isinstance(c, int) or not (0 <= c < len(opts)):
            return None
        q["options"], q["correct"] = opts, c
        _shuffle_options(q, rng)
    elif qtype == "cloze":
        if not re.search(r"_{2,}", question):
            return None
        q["question"] = re.sub(r"_{2,}", "___", question)
        q["answer"] = _clean_str(raw.get("answer")) or row["word"]
        q["accept"] = [a.strip() for a in (raw.get("accept") or []) if isinstance(a, str) and a.strip()]
        if row["word"] not in q["accept"]:
            q["accept"].append(row["word"])
    # sentence / fix / situation / passage: faqat savol matni kerak (AI baholaydi)
    return q


def build_questions(items: list[dict], raw, rng: random.Random | None = None):
    """items (build_items natijasi) va Gemini'ning xom javobidan savollar ro'yxatini
    tuzadi. Har so'z uchun savol kafolatlanadi: yaroqsiz/yetishmagan bo'lsa mahalliy
    savol qo'yiladi. Qaytaradi: (questions, fallback_soni)."""
    rng = rng or random.Random()
    raw_qs = raw.get("questions") if isinstance(raw, dict) else None
    raw_qs = raw_qs if isinstance(raw_qs, list) else []
    passage = _clean_str(raw.get("passage")) if isinstance(raw, dict) else ""
    rows = [it["row"] for it in items]

    by_id = {}
    for rq in raw_qs:
        if not isinstance(rq, dict):
            continue
        r = match_row(rq.get("word", ""), rows)
        if r is not None and r["id"] not in by_id:
            by_id[r["id"]] = rq

    out, fallbacks = [], 0
    for it in items:
        row, qtype = it["row"], it["type"]
        q = None
        if qtype == "translate_word":
            q = make_translate_word(row)
        elif row["id"] in by_id:
            q = validate_question(by_id[row["id"]], row, qtype, rng)
            if q is not None and qtype == "passage":
                if not passage:
                    q = None
                else:
                    q["passage"] = passage
        if q is None:
            q = make_local_question(row, rng)
            fallbacks += 1
        out.append(q)
    return out, fallbacks


# ---------------------------------------------------------------------------
# Mahalliy savollar (AI'siz) - internet bo'lmasa ham mashq ishlayveradi
# ---------------------------------------------------------------------------

def make_translate_word(row) -> dict:
    q = _base(row, "translate_word")
    q["question"] = f"Inglizcha so'zni yozing: «{row['translation']}»"
    q["answer"] = row["word"]
    return q


def make_meaning_local(row, rng: random.Random):
    wrong = db.get_distractors(row["id"], "translation", 3)
    if len(wrong) < 2:
        return None
    q = _base(row, "meaning")
    q["question"] = f"«{row['word']}» so'zining ma'nosi qaysi?"
    q["options"] = [row["translation"]] + wrong
    q["correct"] = 0
    _shuffle_options(q, rng)
    return q


def make_cloze_local(row):
    example = (row["example"] or "").strip()
    if not example:
        return None
    pattern = re.compile(r"\b" + re.escape(row["word"]) + r"\b", re.IGNORECASE)
    if not pattern.search(example):
        return None
    q = _base(row, "cloze")
    q["question"] = "Bo'sh joyni to'ldiring: " + pattern.sub("___", example, count=1)
    q["answer"] = row["word"]
    return q


def make_local_question(row, rng: random.Random | None = None) -> dict:
    """AI'siz savol: tarjima (har doim mumkin), ma'no tanlash (boshqa so'zlar
    yetarli bo'lsa), bo'sh joy (misol jumla bo'lsa)."""
    rng = rng or random.Random()
    cands = [make_translate_word(row)]
    for q in (make_meaning_local(row, rng), make_cloze_local(row)):
        if q is not None:
            cands.append(q)
    return rng.choice(cands)


# ---------------------------------------------------------------------------
# Tekshirish va baho qoidalari
# ---------------------------------------------------------------------------

def grade_local(q: dict, user_answer) -> dict:
    """Mahalliy tur javobini tekshiradi.
    user_answer: MCQ uchun variant indeksi (int), yoziladigan turlar uchun matn.
    Qaytaradi: {"rating", "correct", "feedback"}."""
    if q["checker"] != "local":
        raise ValueError("Bu savol AI tomonidan baholanadi")
    if is_mcq(q):
        if user_answer == q["correct"]:
            return {"rating": "good", "correct": True, "feedback": "To'g'ri!"}
        return {"rating": "again", "correct": False,
                "feedback": f"To'g'ri javob: {q['options'][q['correct']]}"}
    verdict = check_text_answer(user_answer, q["answer"], q.get("accept", []))
    if verdict == "exact":
        return {"rating": "good", "correct": True, "feedback": "To'g'ri!"}
    if verdict == "typo":
        return {"rating": "hard", "correct": True,
                "feedback": f"Deyarli to'g'ri - imlo xatosi bor. To'g'risi: {q['answer']}"}
    return {"rating": "again", "correct": False, "feedback": f"To'g'ri javob: {q['answer']}"}


def rating_from_ai(qtype: str, result) -> str | None:
    """AI qaytargan tuzilgan natijadan baho (kodda hisoblanadi).
    Natija yaroqsiz bo'lsa None (keyinroq qayta uriniladi)."""
    if not isinstance(result, dict):
        return None
    word_ok, grammar_ok = result.get("word_ok"), result.get("grammar_ok")
    if not isinstance(word_ok, bool) or not isinstance(grammar_ok, bool):
        return None
    if not word_ok:
        return "again"
    if not grammar_ok:
        return "hard"
    if qtype in SELF_WRITTEN and result.get("natural") is True:
        return "easy"
    return "good"
