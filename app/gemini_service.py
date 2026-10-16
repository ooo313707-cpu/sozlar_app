"""
gemini_service.py
------------------
Bu fayl Google Gemini AI bilan "gaplashadi". Reja hujjatining 11-bo'limida
ko'rsatilgan namunaga mos ravishda ishlaydi:

  1) Gemini so'zlardan bir nechtasini ishlatib qisqa MATN (passage) yozadi,
     so'ng o'sha so'zlar haqida SAVOLLAR beradi.
  2) Foydalanuvchi har bir savolga javob beradi.
  3) Gemini har bir javobni baholaydi -> natija FSRS'ga "virtual baho"
     sifatida yuboriladi (database.review_word orqali).

Muhim: reja hujjatidagi 6-bo'limda yozilganidek, Gemini'ning o'zi hech
narsani "eslab qolmaydi" - biz har safar unga so'zlar ro'yxatini
o'zimiz (database'dan o'qib) yuboramiz.

v1.2: generate_session(question_count=...) - savollar soni endi tanlangan
so'zlar soniga bog'liq (70% = 45% zaif + 25% yaxshi qoidasi database.py da).
"""

import json
import re
import sqlite3
import time
from google import genai
from google.genai import types

from . import database as db

# 2026-yil holatiga ko'ra barqaror (GA) va bepul tarifda mavjud model.
# DIQQAT: Google Gemini modellarini har 4-5 oyda "eskirgan" deb e'lon qilib,
# keyin butunlay o'chirib qo'yadi (masalan gemini-1.5-flash, gemini-2.0-flash
# allaqachon o'chirilgan). Shuning uchun bu qiymat KODGA emas, Sozlamalar
# orqali o'zgartiriladigan bazaga yozilgan - agar kelajakda bu model ham
# ishlamay qolsa, kodni o'zgartirish shart emas: Sozlamalar'da yangi model
# nomini yozib qo'yish kifoya (masalan ai.google.dev/gemini-api/docs/models
# sahifasidan joriy nomni olib).
DEFAULT_MODEL_NAME = "gemini-3.1-flash-lite"


def get_model_name() -> str:
    return db.get_setting("gemini_model", DEFAULT_MODEL_NAME)


def get_client() -> genai.Client | None:
    """Sozlamalarda saqlangan API kalit bilan Gemini mijozini yaratadi.
    Kalit yo'q bo'lsa None qaytaradi (chaqiruvchi shuni tekshirishi kerak)."""
    api_key = db.get_setting("gemini_api_key")
    if not api_key:
        return None
    return genai.Client(api_key=api_key)


def test_connection() -> tuple[bool, str]:
    """Sozlamalar'dagi 'Ulanishni sinash' tugmasi uchun: API kalit va model
    nomi haqiqatan ishlayaptimi - tekshiradi. (True, "OK") yoki
    (False, "aniq xato matni") qaytaradi."""
    client = get_client()
    if client is None:
        return False, "API kalit kiritilmagan."
    try:
        client.models.generate_content(
            model=get_model_name(),
            contents="Salom",
        )
        return True, "Ulanish muvaffaqiyatli ✅"
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# 1-qadam: matn (passage) + savollar yaratish
# ---------------------------------------------------------------------------

_JSON_HEAD = """
Faqat quyidagi JSON formatida javob ber, boshqa hech qanday matn qo'shma
(kirish so'zi, tushuntirish, markdown belgilari ```json ham kerak emas):

{
  "passage": "4-6 jumlali ingliz tilidagi qisqa matn",
  "questions": [
    {"word": "so'zlardan biri", "question": "shu so'z haqidagi ingliz tilidagi savol"}
  ]
}
"""

_JSON_WORD_RULE = """
MUHIM: "word" maydoniga so'zni ro'yxatda YOZILGANIDEK, aynan bir xil shaklda
yoz (o'zgartirmasdan, ko'plik/fe'l shaklini qo'shmasdan), lekin kichik harflarda.
"""

# Eski (v1.1) qoida - question_count berilmasa shu ishlatiladi
SESSION_JSON_INSTRUCTION = (
    _JSON_HEAD
    + """
"questions" ro'yxatida 3 tadan 4 tagacha savol bo'lsin, har biri ro'yxatdagi
so'zlardan biriga tegishli bo'lsin."""
    + _JSON_WORD_RULE
)


def _json_instruction(question_count: int | None) -> str:
    if question_count is None:
        return SESSION_JSON_INSTRUCTION
    return (
        _JSON_HEAD
        + f"""
"questions" ro'yxatida AYNAN {question_count} ta savol bo'lsin: ro'yxatdagi HAR BIR
so'z uchun bitta savol (har so'z faqat bir marta). Matn esa 4-6 jumladan iborat
bo'lib, so'zlarning faqat bir qismini ishlatsa bo'ladi; qolgan so'zlar bo'yicha
savollar matnga bog'liq bo'lmasligi mumkin (masalan "so'zni o'z jumlangizda ishlating")."""
        + _JSON_WORD_RULE
    )


MODE_A_PROMPT = """Siz ingliz tili o'qituvchisisiz. Foydalanuvchi quyidagi
so'zlarni mashq qilmoqda.

Shu so'zlardan ba'zilarini ishlatib, tasodifiy mavzuda (sayohat, ish,
oila va h.k.) 4-6 jumlali qisqa ingliz tilidagi matn yozing. Keyin o'sha
so'zlar haqida savollar bering (masalan: "so'z bu yerda qanday ma'noda
ishlatilgan?" yoki "so'zni o'z jumlangizda ishlating").
"""

MODE_B_PROMPT = """Siz foydalanuvchining shaxsiy ingliz tili
suhbatdoshisiz. Sizga foydalanuvchi hozirgacha o'rgangan so'zlar va ularning
holati keltiriladi (masalan "Yaxshi o'rganilgan" yoki "O'rtacha unutilgan").

Erkin suhbat qilmang. Buning o'rniga tasodifiy mavzuda qisqa matn/savol
mashqi tuzing, yuqoridagi so'zlarni - ayniqsa "O'rtacha unutilgan"
va "Unutilgan" toifadagilarni - tabiiy tarzda ko'proq ishlatib.
"""

# Eski nomlar (testlar va boshqa kod ishlatishi mumkin)
MODE_A_SYSTEM_PROMPT = MODE_A_PROMPT + SESSION_JSON_INSTRUCTION
MODE_B_SYSTEM_PROMPT = MODE_B_PROMPT + SESSION_JSON_INSTRUCTION


def _words_block(words, with_state: bool = False) -> str:
    lines = []
    for w in words:
        if with_state:
            lines.append(f"- {w['word']} — {w['translation']} ({db.word_label(w)})")
        else:
            lines.append(f"- {w['word']} — {w['translation']}")
    return "\n".join(lines)


def _normalize(text: str) -> str:
    """Solishtirish uchun matnni soddalashtiradi: kichik harf, faqat
    harf/raqamlar qoladi (bo'sh joy, nuqta, tinish belgilari olib tashlanadi)."""
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _match_word(raw_word: str, words: list):
    """Gemini qaytargan 'word' matnini bizning ro'yxatimizdagi so'zga
    moslashtiradi. Avval ANIQ moslikni qidiradi, topilmasa qism sifatida
    moslikni qidiradi (masalan Gemini "meticulously" deb yozsa,
    ro'yxatdagi "meticulous" bilan ham moslashtiradi). Bu - avval faqat
    aniq moslikni tekshirib, ozgina farq bo'lsa ham savolni "jimgina
    tashlab yuborgan" eski xatoni tuzatadi."""
    target = _normalize(raw_word)
    if not target:
        return None

    for w in words:
        if _normalize(w["word"]) == target:
            return w

    for w in words:
        norm_w = _normalize(w["word"])
        if norm_w and (norm_w in target or target in norm_w):
            return w

    return None


def generate_session(words: list, mode: str = "A", question_count: int | None = None) -> dict:
    """`words` - database'dan olingan so'z qatorlari ro'yxati.
    `mode` - 'A' (kartalardan keyin / bo'lim / bo'limcha) yoki 'B' (erkin mashq).
    `question_count` - nechta savol so'rash (odatda len(words)); None bo'lsa 3-4 ta.

    Natija: {"passage": str, "questions": [{"word_id":.., "word":..,
    "translation":.., "question":..}, ...]}
    """
    client = get_client()
    if client is None:
        raise RuntimeError("Gemini API kalit kiritilmagan. Sozlamalarga o'ting.")

    if mode == "B":
        base_prompt = MODE_B_PROMPT
        words_text = _words_block(words, with_state=True)
    else:
        base_prompt = MODE_A_PROMPT
        words_text = _words_block(words, with_state=False)
    system_prompt = base_prompt + _json_instruction(question_count)

    response = client.models.generate_content(
        model=get_model_name(),
        contents=f"So'zlar ro'yxati:\n{words_text}",
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
        ),
    )

    data = json.loads(response.text)
    passage = data.get("passage", "")
    raw_questions = data.get("questions", [])

    questions = []
    used_ids = set()
    for q in raw_questions:
        src = _match_word(str(q.get("word", "")), words)
        if src is None:
            continue
        if question_count is not None and src["id"] in used_ids:
            continue            # har so'zdan bittadan ortiq savol kerak emas
        used_ids.add(src["id"])
        questions.append({
            "word_id": src["id"],
            "word": src["word"],
            "translation": src["translation"],
            "question": q.get("question", ""),
        })

    return {"passage": passage, "questions": questions}


# ---------------------------------------------------------------------------
# 2-qadam: foydalanuvchi javobini baholash
# ---------------------------------------------------------------------------

GRADE_SYSTEM_PROMPT = """Sen ingliz tili o'qituvchisan. Foydalanuvchiga
matn va shu matn asosida savol berilgan edi, u so'zni bilishini tekshirish
uchun. Endi uning javobini baholaysan.

Faqat quyidagi JSON formatida javob ber (boshqa matn yo'q, ```json ham kerak emas):

{
  "correct": true yoki false,
  "rating": "again" yoki "hard" yoki "good" yoki "easy",
  "feedback": "Foydalanuvchiga o'zbek tilida qisqa, iliq izoh (1-2 jumla)"
}

Baholash mezoni:
- "again": so'zni umuman tushunmagan yoki noto'g'ri ishlatgan
- "hard": to'g'ri, lekin ancha ikkilanib yoki xato bilan aralashtirib javob bergan
- "good": to'g'ri va tushunarli javob bergan
- "easy": to'g'ri, ishonchli va tabiiy ravon javob bergan
"""


def grade_answer(word: str, translation: str, passage: str, question: str,
                  user_answer: str) -> dict:
    client = get_client()
    if client is None:
        raise RuntimeError("Gemini API kalit kiritilmagan. Sozlamalarga o'ting.")

    prompt = (
        f"Matn: {passage}\n\n"
        f"So'z: {word} ({translation})\n"
        f"Savol: {question}\n"
        f"Foydalanuvchi javobi: {user_answer}"
    )

    response = client.models.generate_content(
        model=get_model_name(),
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=GRADE_SYSTEM_PROMPT,
            response_mime_type="application/json",
        ),
    )

    data = json.loads(response.text)
    if data.get("rating") not in ("again", "hard", "good", "easy"):
        data["rating"] = "good" if data.get("correct") else "again"
    return data


# ===========================================================================
# v1.3 (2-bosqich): aralash savol turlari, ommaviy baholash, qayta urinish
# ===========================================================================

RETRY_DELAYS = (2, 5, 10)      # soniya: 429/503 da shuncha kutib qayta uriniladi
_sleep = time.sleep            # testlarda almashtiriladi


def _is_retryable(e: Exception) -> bool:
    """Google serveri band (503) yoki so'rovlar limiti (429) - bu vaqtinchalik xato."""
    code = getattr(e, "code", None) or getattr(e, "status_code", None)
    if code in (429, 503):
        return True
    msg = str(e).upper()
    return any(k in msg for k in ("429", "503", "UNAVAILABLE", "RESOURCE_EXHAUSTED", "OVERLOADED"))


def _generate_json(system_prompt: str, contents: str) -> dict:
    """Gemini'dan JSON oladi; vaqtinchalik xatoda (429/503) avtomatik qayta urinadi."""
    client = get_client()
    if client is None:
        raise RuntimeError("Gemini API kalit kiritilmagan. Sozlamalarga o'ting.")
    for attempt in range(len(RETRY_DELAYS) + 1):
        try:
            response = client.models.generate_content(
                model=get_model_name(),
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=system_prompt,
                    response_mime_type="application/json",
                ),
            )
            return json.loads(response.text)
        except Exception as e:
            if attempt == len(RETRY_DELAYS) or not _is_retryable(e):
                raise
            _sleep(RETRY_DELAYS[attempt])


QUESTIONS_PROMPT = """Siz ingliz tili o'qituvchisisiz. Foydalanuvchining ingliz tili
darajasi: {level} (CEFR). Matn, jumlalar va variantlar shu darajaga mos, aniq va
tabiiy bo'lsin.

Quyida so'zlar ro'yxati keladi; har bir so'z uchun SAVOL TURI berilgan. Har so'z
uchun AYNAN bitta savol tuzing, aynan berilgan turda. "word" maydoniga so'zni
ro'yxatda yozilganidek yozing.

Turlar va maydonlar:
- cloze: "question" = ingliz tilidagi jumla, so'z o'rniga ___ (uch pastki chiziq);
  "answer" = bo'sh joyga tushadigan aniq shakl; "accept" = boshqa to'g'ri shakllar (ixtiyoriy).
- meaning: "question" = What does "X" mean?; "options" = 4 ta ingliz tilidagi variant
  (bittasi to'g'ri); "correct" = to'g'ri variant indeksi (0 dan boshlanadi).
- correct_sentence: "question" = Which sentence uses "X" correctly?; "options" = 3 ta
  jumla (bittasida so'z to'g'ri ishlatilgan); "correct" = indeks.
- synonym: "question" = Which word is closest in meaning to "X"? (yoki antonim);
  "options" = 4 ta so'z; "correct" = indeks.
- sentence: "question" = so'z bilan o'z gapini yozishni so'rang (ixtiyoriy kontekst bilan).
- fix: "question" = "Fix the mistake:" va ichida X noto'g'ri ishlatilgan ingliz jumla.
- situation: "question" = shu so'z qaysi vaziyatda ishlatilishini tasvirlashni so'rang.
- passage: yuqori darajadagi "passage" = 4-6 jumlali matn (shu tipdagi barcha savollar
  uchun bitta umumiy matn); "question" = shu matn va so'z haqida savol.
Qiyin so'zlar (belgisi: leech) uchun qo'shimcha: "tip" = o'zbek tilida qisqa eslab
qolish usuli (mnemonika), "examples" = 2 ta ingliz tilidagi misol jumla.

Faqat JSON qaytaring (markdown belgilarisiz):
{{"passage": "faqat passage turi bo'lsa, aks holda bo'sh qator",
  "questions": [{{"word": "...", "type": "...", "question": "...", "options": [...],
                 "correct": 0, "answer": "...", "accept": [], "tip": "...", "examples": []}}]}}
Keraksiz maydonlarni tushirib qoldirish mumkin.
"""


def generate_questions(items: list, level: str = "B1", mode: str = "A") -> dict:
    """items: [{"word","translation","type","leech": bool}]. Bitta so'rovda hamma
    savollarni xom JSON ko'rinishida qaytaradi (tekshiruv questions.py da)."""
    lines = []
    for it in items:
        tag = " | leech" if it.get("leech") else ""
        lines.append(f"- {it['word']} — {it['translation']} | type={it['type']}{tag}")
    system_prompt = QUESTIONS_PROMPT.format(level=level)
    if mode == "B":
        system_prompt += "\nBu umumiy takrorlash mashqi: mavzular xilma-xil bo'lsin.\n"
    return _generate_json(system_prompt, "So'zlar:\n" + "\n".join(lines))


GRADE_BATCH_PROMPT = """Sen ingliz tili o'qituvchisan. Foydalanuvchining ingliz tili
darajasi: {level} (CEFR). Quyida foydalanuvchining bir nechta javobi bor (har biri "id" bilan).
Har bir javobni alohida baho.

Faqat JSON qaytar (markdown belgilarisiz):
{{"results": [{{"id": 1, "word_ok": true, "grammar_ok": true, "natural": false,
               "feedback": "o'zbek tilida 1-2 jumlali iliq izoh"}}]}}

- word_ok: so'z to'g'ri ma'noda va to'g'ri ishlatilganmi. Kichik imlo xatosi uchun
  word_ok ni false qilma.
- grammar_ok: javobda grammatika yoki imlo xatosi yo'qmi.
- natural: ona tili egasi shunday aytadimi (tabiiy, ravon).
"fix" turida: foydalanuvchi jumlani to'g'ri tuzatdimi - word_ok shuni bildiradi.
Har bir "id" uchun bitta natija qaytar.
"""


def grade_batch(entries: list, level: str = "B1") -> dict:
    """entries: [{"id","word","translation","type","question","passage"?,"answer"}].
    Hammasini BITTA so'rovda baholaydi. Qaytaradi: {id: {"word_ok","grammar_ok",
    "natural","feedback"}} (faqat qaytgan, yaroqli id'lar uchun)."""
    data = _generate_json(GRADE_BATCH_PROMPT.format(level=level),
                          json.dumps(entries, ensure_ascii=False))
    out = {}
    results = data.get("results") if isinstance(data, dict) else None
    for r in results if isinstance(results, list) else []:
        try:
            out[int(r.get("id"))] = r
        except (TypeError, ValueError, AttributeError):
            continue
    return out


SPELL_BATCH_PROMPT = """You are a strict English spelling and vocabulary-card checker.
Below is a JSON list of cards, each with an "id", an English "word" (or phrase), its
Uzbek "translation", an English "example" sentence (may be empty) and "local_unknown"
(words the local dictionary did not recognise). Check EACH card:
(1) the English spelling (names, terms and valid phrases such as "Wi-Fi" or "give up"
are fine), (2) whether the Uzbek translation fits the word, (3) whether the example
is correct English and uses the word.

Reply with ONLY a JSON object, no markdown:
{"results": [{"id": 1, "word_ok": true, "translation_ok": true, "example_ok": true}]}
Return exactly one result per "id"."""


def check_spelling_batch(entries: list) -> dict:
    """entries: [{"id","word","translation","example","local_unknown"}] (bir so'rovda
    50 tagacha, import navbati uchun). Qaytaradi: {id: {"word_ok","translation_ok",
    "example_ok"}} - faqat qaytgan, yaroqli id'lar uchun. 429/503 da _generate_json
    avtomatik qayta urinadi; baribir xato bo'lsa istisno ko'tariladi (chaqiruvchi
    mahalliy natija bilan davom etadi)."""
    data = _generate_json(SPELL_BATCH_PROMPT, json.dumps(entries, ensure_ascii=False))
    out = {}
    results = data.get("results") if isinstance(data, dict) else None
    for r in results if isinstance(results, list) else []:
        try:
            out[int(r.get("id"))] = r
        except (TypeError, ValueError, AttributeError):
            continue
    return out
