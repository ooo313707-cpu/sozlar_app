"""
add_word.py
-----------
Yangi so'z qo'shish uchun oddiy forma: ingliz so'zi, o'zbekcha tarjima,
ixtiyoriy misol jumla va ixtiyoriy BO'LIM nomi (bo'sh qolsa "Umumiy").
So'z bo'limning oxirgi bo'limchasiga (har birida eng ko'pi 20 ta) joylanadi.

v1.6 (CLAUDE-3): saqlangandan keyin imlo tekshiruvi AVTOMATIK boshlanadi (avval hech kim
boshlamas edi - yangi so'z 'unchecked' bo'lib qolib, kartalarda umuman chiqmasdi).
Natija: ok -> kartalarga qo'shiladi; suspect -> [To'g'irla] / [Men shunday yozdim] dialogi.
"""

from functools import partial

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QLineEdit, QTextEdit,
    QPushButton, QMessageBox, QComboBox,
)
from PySide6.QtCore import Signal
from PySide6.QtGui import QFont

from .. import database as db
from .. import spell_checker
from ..background import ThreadWorker
from . import spell_review, theme
from .widgets import CenteredColumn


class SpellTask(ThreadWorker):
    """Saqlangan so'zni fon oqimida tekshiradi (interfeys qotmaydi).
    Natija result_ready signali bilan SpellResult sifatida keladi."""

    result_ready = Signal(object)

    def __init__(self, word_id: int, parent=None):
        """word_id - tekshiriladigan so'zning id'si."""
        super().__init__(parent)
        self.word_id = word_id

    def run(self):
        """spell_checker.check_saved_word ni bajaradi; xato bo'lsa ham signal chiqaradi."""
        try:
            result = spell_checker.check_saved_word(self.word_id)
        except Exception as exc:
            result = spell_checker.SpellResult(status=db.SPELL_UNCHECKED, error=str(exc))
        self._safe_emit(self.result_ready, result)


class AddWordPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self._spell_tasks = []    # ishlayotgan imlo tekshiruvlari (GC o'chirib yubormasin)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 36, 40, 30)

        column = CenteredColumn(max_width=560)
        outer.addWidget(column)
        layout = column.layout_for_content()
        layout.setSpacing(10)

        title = QLabel("Yangi so'z qo'shish")
        title.setFont(QFont(theme.FONT_FAMILY, 22, QFont.Bold))
        layout.addWidget(title)
        layout.addSpacing(8)

        layout.addWidget(self._field_label("Ingliz so'zi"))
        self.word_input = QLineEdit()
        self.word_input.setPlaceholderText("masalan: apple")
        layout.addWidget(self.word_input)

        layout.addWidget(self._field_label("O'zbekcha tarjimasi"))
        self.translation_input = QLineEdit()
        self.translation_input.setPlaceholderText("masalan: olma")
        layout.addWidget(self.translation_input)

        layout.addWidget(self._field_label("Misol jumla (ixtiyoriy)"))
        self.example_input = QTextEdit()
        self.example_input.setPlaceholderText("masalan: I eat an apple every day.")
        self.example_input.setMaximumHeight(80)
        layout.addWidget(self.example_input)

        layout.addWidget(self._field_label(
            "Bo'lim (ixtiyoriy - masalan Kitob, IELTS. Bo'sh qolsa «Umumiy»)"))
        self.category_input = QComboBox()
        self.category_input.setEditable(True)
        self.category_input.lineEdit().setPlaceholderText("Umumiy")
        layout.addWidget(self.category_input)

        layout.addSpacing(6)
        save_btn = QPushButton("💾  Saqlash")
        save_btn.setMinimumHeight(48)
        save_btn.setStyleSheet(theme.accent_button())
        save_btn.clicked.connect(self.save_word)
        layout.addWidget(save_btn)

        self.spell_label = QLabel("")
        self.spell_label.setWordWrap(True)
        self.spell_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        layout.addWidget(self.spell_label)

        outer.addStretch()

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px; margin-top: 6px;")
        return lbl

    def refresh(self):
        self.word_input.clear()
        self.translation_input.clear()
        self.example_input.clear()

        existing = db.get_categories()
        current_text = self.category_input.currentText()
        self.category_input.clear()
        categories = ["Umumiy"] + [c for c in existing if c != "Umumiy"]
        self.category_input.addItems(categories)
        # Oxirgi ishlatilgan bo'lim eslab qolinadi; birinchi marta - bo'sh (= Umumiy)
        self.category_input.setCurrentText(current_text)

        self.word_input.setFocus()

    def save_word(self):
        word = self.word_input.text().strip()
        translation = self.translation_input.text().strip()
        example = self.example_input.toPlainText().strip()
        category = self.category_input.currentText().strip() or db.DEFAULT_SECTION

        if not word or not translation:
            QMessageBox.warning(self, "Xato", "So'z va tarjima bo'sh bo'lmasligi kerak.")
            return

        try:
            new_id = db.add_word(word, translation, example, category)
        except Exception as e:
            QMessageBox.warning(
                self, "Xato",
                f"Saqlashda xatolik: {e}\n\n"
                f"(Ehtimol, bu so'z avval qo'shilgan bo'lishi mumkin.)"
            )
            return

        pack = db.get_word(new_id)["pack"]
        QMessageBox.information(
            self, "Saqlandi",
            f"'{word}' so'zi qo'shildi ✅\n{category} · {pack}-bo'limcha")
        self.word_input.clear()
        self.translation_input.clear()
        self.example_input.clear()
        self.word_input.setFocus()
        self._start_spell_check(new_id, word)

    # ------------------------------------------------------------------
    # Imlo tekshiruvi (CLAUDE-3)
    def _start_spell_check(self, word_id: int, word: str):
        """Saqlangan so'zning imlo tekshiruvini fonda boshlaydi."""
        self.spell_label.setText(f"⏳ «{word}» imlosi tekshirilmoqda...")
        task = SpellTask(word_id, parent=self)
        task.result_ready.connect(partial(self._on_spell_result, word_id, word))
        task.finished.connect(lambda t=task: self._spell_tasks.remove(t) if t in self._spell_tasks else None)
        self._spell_tasks.append(task)
        task.start()

    def _on_spell_result(self, word_id: int, word: str, result):
        """Tekshiruv natijasi: ok -> kartalarga qo'shiladi; suspect -> dialog; aks holda keyinroq."""
        status = result.status
        if status in db.SPELL_STUDY_OK:
            self.spell_label.setText(f"✅ «{word}» imlosi to'g'ri — kartalarda chiqadi.")
        elif status == db.SPELL_SUSPECT:
            choice = spell_review.choose_action(self, word, result)
            if spell_review.apply_choice(self, word_id, result, choice):
                self.spell_label.setText(f"✅ «{word}» — saqlandi, kartalarda chiqadi.")
            else:
                self.spell_label.setText(
                    f"⚠ «{word}» shubhali bo'lib qoldi — kartalarda chiqmaydi. "
                    "Sozlamalar → «Shubhali so'zlarni ko'rib chiqish» orqali hal qiling.")
        else:
            why = f" ({result.error})" if result.error else ""
            self.spell_label.setText(
                f"⚠ «{word}» imlosi tekshirilmadi{why}. Ilova qayta ochilganda yoki "
                "Sozlamalar → «Tekshirilmagan so'zlarni hozir tekshirish» orqali tekshiriladi.")
        home = getattr(self.main_window, "pages", {}).get("home")
        if home is not None:
            home.refresh()
