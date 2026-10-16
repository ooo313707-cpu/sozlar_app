"""
spell_review.py  (CLAUDE-3)
---------------------------
Imlo tekshiruvi 'shubhali' (suspect) deb topgan so'zlar bilan ishlash oynalari.

Muammo: shubhali so'z kartalarda va AI mashqda qatnashmaydi, lekin uni qutqarishning
yo'li yo'q edi (so'z qo'shishdan keyin dialog ham, ro'yxatda ham). Endi:
  * choose_action  - bitta so'z uchun dialog: [To'g'irla -> taklif] [Men shunday yozdim] [Keyinroq]
  * apply_choice   - tanlovni bazaga qo'llaydi (spell_checker.apply_suggestion / confirm_word)
  * review_suspects - barcha shubhali so'zlarni ketma-ket ko'rib chiqadi (Sozlamalar tugmasi)
Hech narsa avtomatik o'zgartirilmaydi: almashtirishni foydalanuvchi tanlaydi (D-008/Claude 2).
"""

from __future__ import annotations

import sqlite3

from PySide6.QtWidgets import QMessageBox

from .. import database as db
from .. import spell_checker

FIX = "fix"
CONFIRM = "confirm"
LATER = "later"
STOP = "stop"


def get_suspect_rows() -> list:
    """Imlo holati 'suspect' bo'lgan so'zlar (eskisi birinchi)."""
    conn = db.get_connection()
    try:
        return conn.execute("SELECT * FROM words WHERE spell_status = ? ORDER BY id",
                            (db.SPELL_SUSPECT,)).fetchall()
    finally:
        conn.close()


def choose_action(parent, word: str, result, allow_stop: bool = False) -> str:
    """Shubhali so'z uchun dialog. Qaytaradi: FIX | CONFIRM | LATER | STOP.

    FIX faqat taklif (result.suggestion) bo'lsa mumkin. Oynani yopish = LATER.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Imlo tekshiruvi")
    lines = [f"«{word}» shubhali ko'rindi."]
    lines += [f"• {i}" for i in (result.issues or [])]
    if result.suggestion:
        lines.append(f"\nTaklif: {result.suggestion}")
    lines.append("\nTekshirilmaguncha bu so'z kartalarda chiqmaydi.")
    box.setText("\n".join(lines))
    fix_btn = None
    if result.suggestion:
        fix_btn = box.addButton(f"✏️ To'g'irla → {result.suggestion}", QMessageBox.AcceptRole)
    confirm_btn = box.addButton("✅ Men shunday yozdim", QMessageBox.AcceptRole)
    later_btn = box.addButton("Keyinroq", QMessageBox.RejectRole)
    stop_btn = box.addButton("To'xtatish", QMessageBox.RejectRole) if allow_stop else None
    box.exec()
    clicked = box.clickedButton()
    if fix_btn is not None and clicked is fix_btn:
        return FIX
    if clicked is confirm_btn:
        return CONFIRM
    if stop_btn is not None and clicked is stop_btn:
        return STOP
    return LATER


def apply_choice(parent, word_id: int, result, choice: str) -> bool:
    """Tanlovni qo'llaydi. True - so'z endi kartalarda qatnasha oladi (ok/confirmed).

    FIX: so'z matni taklifga almashadi; shunday so'z bazada allaqachon bo'lsa ogohlantirish
    ko'rsatiladi va so'z shubhali holicha qoladi. CONFIRM: 'confirmed' (qayta ogohlantirilmaydi).
    """
    try:
        if choice == FIX and result.suggestion:
            spell_checker.apply_suggestion(word_id, result.suggestion)
            return True
        if choice == CONFIRM:
            spell_checker.confirm_word(word_id)
            return True
    except sqlite3.IntegrityError:
        QMessageBox.warning(parent, "Imlo tekshiruvi",
                            f"«{result.suggestion}» so'zi bazada allaqachon bor. "
                            "Bu so'zni qo'lda tahrirlang yoki «Men shunday yozdim»ni tanlang.")
    except ValueError:
        pass   # so'z shu orada o'chirilgan
    return False


def review_suspects(parent) -> dict:
    """Barcha shubhali so'zlarni ketma-ket ko'rib chiqadi (mahalliy tekshiruv taklif beradi).

    Qaytaradi: {'fixed','confirmed','later','resolved_auto','total'} sonlari.
    """
    rows = get_suspect_rows()
    counts = {"fixed": 0, "confirmed": 0, "later": 0, "resolved_auto": 0, "total": len(rows)}
    for i, row in enumerate(rows):
        result = spell_checker.check_word(row["word"], row["translation"], row["example"] or "",
                                          use_ai=False)
        if result.status == db.SPELL_OK:
            # lug'at endi tanidi (masalan, boshqa so'z orqali yoki lug'at yangilangan)
            db.set_spell_status(row["id"], db.SPELL_OK)
            counts["resolved_auto"] += 1
            continue
        choice = choose_action(parent, row["word"], result, allow_stop=True)
        if choice == STOP:
            counts["later"] += len(rows) - i
            break
        if choice == LATER:
            counts["later"] += 1
        elif apply_choice(parent, row["id"], result, choice):
            counts["fixed" if choice == FIX else "confirmed"] += 1
        else:
            counts["later"] += 1
    return counts
