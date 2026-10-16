"""
spell_checker.py  (YANGI, v1.5 - CLAUDE-2)
------------------------------------------
So'z SAQLANAYOTGANDA majburiy imlo tekshiruvi. Ikki qatlam:

  1. LOKAL (pyspellchecker) - internetsiz, HAR DOIM ishlaydi. Lug'atda yo'q
     so'z = "shubhali" (ot, atama, ibora - Wi-Fi, give up - adashishi mumkin,
     shuning uchun foydalanuvchi "men shunday yozdim" deya tasdiqlay oladi).
  2. GEMINI (kalit va internet bo'lsa) - ibora, kam uchraydigan so'z, tarjima
     va misol jumlani ham tekshiradi. Lokal natija bilan BITTA so'rovda.
     Kalit yo'q / internet yo'q / vaqt tugadi / javob buzuq bo'lsa - lokal
     natija bilan davom etiladi (ai_checked=False).

Holatlar (words.spell_status): unchecked / ok / suspect / confirmed.
`confirmed` so'z qayta tekshirilmaydi. Hech narsa avtomatik o'zgartirilmaydi:
taklif faqat `SpellResult.suggestion` da qaytadi, almashtirishni foydalanuvchi
(apply_suggestion orqali) tanlaydi.

Add-word sahifasi uchun oqim:
    Saqlash -> add_word() -> SpellCheckWorker(word_id).start()
    finished_check(SpellResult) ->
        status == ok          -> "Saqlandi"
        status == suspect     -> "ubiquitus -> ubiquitous?" + [To'g'irla] [Men shunday yozdim]
        [To'g'irla]           -> apply_suggestion(word_id, result.suggestion)
        [Men shunday yozdim]  -> confirm_word(word_id)
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QThread, Signal

from . import database as db

AI_TIMEOUT_SECONDS = 12          # shundan oshsa lokal natija bilan davom etiladi
_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


class AIUnavailable(Exception):
    """Gemini'dan foydalanib bo'lmaydi (kalit yo'q, tarmoq xatosi, ...)."""


@dataclass
class SpellResult:
    """Imlo tekshiruvi natijasi."""
    status: str                              # db.SPELL_* qiymatlaridan biri
    suggestion: str | None = None            # taklif qilingan to'g'ri yozilish
    issues: list[str] = field(default_factory=list)   # foydalanuvchiga ko'rsatiladigan izohlar
    ai_checked: bool = False                 # Gemini ham tekshirdimi
    error: str = ""                          # lokal tekshiruvni ham bajarib bo'lmagan bo'lsa


# ---------------------------------------------------------------------------
# 1-qatlam: lokal tekshiruv
# ---------------------------------------------------------------------------

_spell = None


def _get_spell():
    """pyspellchecker obyektini bir marta yaratadi (kech import qilinadi)."""
    global _spell
    if _spell is None:
        from spellchecker import SpellChecker
        _spell = SpellChecker()
    return _spell


def _match_case(original: str, replacement: str) -> str:
    """Taklifga asl so'zning bosh/kichik harf ko'rinishini beradi."""
    if len(original) > 1 and original.isupper():
        return replacement.upper()
    if original[:1].isupper():
        return replacement[:1].upper() + replacement[1:]
    return replacement


def local_check(phrase: str) -> tuple[list[str], str | None]:
    """So'z yoki iborani lug'at bo'yicha tekshiradi.
    Qaytaradi: (lug'atda yo'q so'zlar ro'yxati, to'g'rilangan ibora yoki None).
    Bir harfli bo'laklar tekshirilmaydi."""
    tokens = [t for t in _WORD_RE.findall(phrase or "") if len(t) > 1]
    if not tokens:
        return [], None
    spell = _get_spell()
    unknown = set(spell.unknown([t.lower() for t in tokens]))
    if not unknown:
        return [], None

    corrections: dict[str, str] = {}
    for low in unknown:
        fixed = spell.correction(low)
        if fixed and fixed != low:
            corrections[low] = fixed

    def _sub(m: re.Match) -> str:
        tok = m.group(0)
        fixed = corrections.get(tok.lower())
        return _match_case(tok, fixed) if fixed else tok

    suggestion = _WORD_RE.sub(_sub, phrase)
    bad = sorted(unknown)
    return bad, (suggestion if suggestion != phrase else None)


# ---------------------------------------------------------------------------
# 2-qatlam: Gemini (bitta so'rov)
# ---------------------------------------------------------------------------

def build_ai_prompt(word: str, translation: str, example: str,
                    local_unknown: list[str]) -> str:
    """Gemini'ga yuboriladigan BITTA so'rov matni (lokal natija ham ichida)."""
    return (
        "You are a strict English spelling and vocabulary-card checker.\n"
        f"Word or phrase: {word!r}\n"
        f"Uzbek translation: {translation!r}\n"
        f"Example sentence: {example!r}\n"
        f"Local dictionary did not recognise: {local_unknown or 'nothing'}\n"
        "Check (1) the English spelling (names, terms and valid phrases such as "
        "'Wi-Fi' or 'give up' are fine), (2) whether the Uzbek translation fits "
        "the word, (3) whether the example sentence is correct English and uses "
        "the word. Reply with ONLY a JSON object, no markdown:\n"
        '{"word_ok": true|false, "suggestion": "<correct spelling or null>", '
        '"translation_ok": true|false, "example_ok": true|false, '
        '"note": "<one short sentence in Uzbek, or empty>"}'
    )


def parse_ai_response(text: str) -> dict:
    """Gemini javobini (```json ... ``` bilan o'ralgan bo'lishi mumkin) lug'atga
    aylantiradi. Buzuq javobda ValueError."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Bo'sh javob")
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE)
    data = json.loads(cleaned)
    if not isinstance(data, dict) or not isinstance(data.get("word_ok"), bool):
        raise ValueError("Kutilgan JSON shakli emas")
    return data


def _default_ai_call(prompt: str) -> str:
    """Sozlamalardagi kalit va model bilan Gemini'ga so'rov yuboradi (matn qaytaradi).
    Kalit yo'q bo'lsa AIUnavailable."""
    key = (db.get_setting("gemini_api_key", "") or "").strip()
    if not key:
        raise AIUnavailable("Gemini kaliti kiritilmagan")
    model = (db.get_setting("gemini_model", "") or "").strip() or "gemini-3.1-flash-lite"
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=key)
        resp = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0),
        )
        return resp.text or ""
    except Exception as exc:                      # tarmoq, 429/503, kalit xatosi...
        raise AIUnavailable(str(exc)) from exc


def _ask_ai(prompt: str, ai_func: Callable[[str], str], timeout: float) -> dict:
    """ai_func ni vaqt chegarasi bilan chaqiradi. Muvaffaqiyatsiz bo'lsa AIUnavailable."""
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(ai_func, prompt)
    try:
        text = future.result(timeout=timeout)
    except FutureTimeout as exc:
        raise AIUnavailable("Gemini javobi kechikdi") from exc
    except AIUnavailable:
        raise
    except Exception as exc:
        raise AIUnavailable(str(exc)) from exc
    finally:
        pool.shutdown(wait=False)
    try:
        return parse_ai_response(text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise AIUnavailable(f"Javob tushunarsiz: {exc}") from exc


# ---------------------------------------------------------------------------
# Birlashtirilgan tekshiruv
# ---------------------------------------------------------------------------

def check_word(word: str, translation: str = "", example: str = "",
               use_ai: bool = True,
               ai_func: Callable[[str], str] | None = None,
               ai_timeout: float = AI_TIMEOUT_SECONDS) -> SpellResult:
    """So'zni tekshiradi (bazaga YOZMAYDI). Avval lokal, so'ng (use_ai bo'lsa)
    Gemini. `ai_func(prompt) -> str` testlarda soxta (mock) manba berish uchun.

    Qoida: AI tekshirgan bo'lsa AI hukmi hal qiladi (kam uchraydigan so'zni
    lokal lug'at bilmasligi mumkin); AI ishlamasa lokal natija qoladi."""
    word = (word or "").strip()
    if not word:
        return SpellResult(status=db.SPELL_UNCHECKED, error="So'z bo'sh")

    try:
        unknown, local_suggestion = local_check(word)
    except Exception as exc:                      # pyspellchecker o'rnatilmagan va h.k.
        return SpellResult(status=db.SPELL_UNCHECKED, error=f"Lokal tekshiruv ishlamadi: {exc}")

    result = SpellResult(
        status=db.SPELL_SUSPECT if unknown else db.SPELL_OK,
        suggestion=local_suggestion,
        issues=[f"«{t}» lug'atda topilmadi" for t in unknown],
    )
    if not use_ai:
        return result

    prompt = build_ai_prompt(word, translation, example or "", unknown)
    try:
        ai = _ask_ai(prompt, ai_func or _default_ai_call, ai_timeout)
    except AIUnavailable:
        return result                             # lokal natija, ai_checked=False

    result.ai_checked = True
    issues: list[str] = []
    suggestion = None
    bad = False

    if not ai["word_ok"]:
        bad = True
        sug = ai.get("suggestion")
        suggestion = sug.strip() if isinstance(sug, str) and sug.strip() and sug.strip() != word else local_suggestion
        issues.append("Imlo xatosi ehtimoli")
    if ai.get("translation_ok") is False:
        bad = True
        issues.append("Tarjima mos kelmasligi mumkin")
    if ai.get("example_ok") is False:
        bad = True
        issues.append("Misol jumlada xato bo'lishi mumkin")
    note = ai.get("note")
    if bad and isinstance(note, str) and note.strip():
        issues.append(note.strip())

    result.status = db.SPELL_SUSPECT if bad else db.SPELL_OK
    result.suggestion = suggestion if bad else None
    result.issues = issues
    return result


# ---------------------------------------------------------------------------
# Bazaga yozish bilan ishlaydigan yuqori darajali funksiyalar
# ---------------------------------------------------------------------------

def check_saved_word(word_id: int, use_ai: bool | None = None,
                     ai_func: Callable[[str], str] | None = None,
                     ai_timeout: float = AI_TIMEOUT_SECONDS) -> SpellResult:
    """Saqlangan so'zni tekshiradi va natijani words.spell_status ga yozadi.
    `confirmed` so'z QAYTA TEKSHIRILMAYDI. `use_ai=None` bo'lsa 'spell_check_ai'
    sozlamasi o'qiladi (standart: yoqiq)."""
    row = db.get_word(word_id)
    if row is None:
        raise ValueError(f"So'z topilmadi: id={word_id}")
    if row["spell_status"] == db.SPELL_CONFIRMED:
        return SpellResult(status=db.SPELL_CONFIRMED)
    if use_ai is None:
        use_ai = db.get_setting("spell_check_ai", "1") != "0"

    result = check_word(row["word"], row["translation"], row["example"] or "",
                        use_ai=use_ai, ai_func=ai_func, ai_timeout=ai_timeout)
    if result.status != db.SPELL_UNCHECKED:
        db.set_spell_status(word_id, result.status)
    return result


def confirm_word(word_id: int) -> None:
    """[Men shunday yozdim] - holat 'confirmed', qayta ogohlantirilmaydi."""
    db.set_spell_status(word_id, db.SPELL_CONFIRMED)


def apply_suggestion(word_id: int, suggestion: str) -> None:
    """[To'g'irla] - so'z matnini taklif bilan almashtiradi (tarjima, misol,
    bo'lim saqlanadi) va holatni 'ok' qiladi. Taklif bazada allaqachon bor
    so'z bo'lsa sqlite3.IntegrityError ko'tariladi (chaqiruvchi ushlaydi)."""
    suggestion = (suggestion or "").strip()
    if not suggestion:
        raise ValueError("Taklif bo'sh")
    row = db.get_word(word_id)
    if row is None:
        raise ValueError(f"So'z topilmadi: id={word_id}")
    db.update_word(word_id, suggestion, row["translation"], row["example"] or "",
                   row["category"])
    db.set_spell_status(word_id, db.SPELL_OK)


class SpellCheckWorker(QThread):
    """check_saved_word ni fon oqimida bajaradi (interfeys qotmaydi).
    Natija `finished_check` signali bilan SpellResult sifatida keladi."""
    finished_check = Signal(object)          # Signal(object) - dict/dataclass uchun (D-010)

    def __init__(self, word_id: int, use_ai: bool | None = None,
                 ai_func: Callable[[str], str] | None = None, parent=None):
        super().__init__(parent)
        self.word_id = word_id
        self.use_ai = use_ai
        self.ai_func = ai_func

    def run(self):
        """Tekshiradi; kutilmagan xatoda ham signal yuboradi (qotib qolmaslik uchun)."""
        try:
            result = check_saved_word(self.word_id, self.use_ai, self.ai_func)
        except Exception as exc:
            result = SpellResult(status=db.SPELL_UNCHECKED, error=str(exc))
        self.finished_check.emit(result)
