"""
settings.py
-----------
Gemini API kalitini, model nomini, kunlik yangi so'z limitini va AI
mashqidagi savollar sonini (eng ko'pi) kiritish/saqlash.

v1.6 (CLAUDE-3): talaffuzni avtomatik chalish (auto_play) va "Import / Eksport"
bo'limi (CSV/TSV: eksport A/B, import, fon navbati jarayoni). "Ulanishni sinash"
tugmasi orqali API kalit va model nomi haqiqatan ishlayaptimi - flashcard
sessiyasigacha kutmasdan darhol bilib olish mumkin.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton,
    QSpinBox, QMessageBox, QComboBox, QCheckBox, QFileDialog,
)
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QFont, QGuiApplication

from .. import audio_service
from .. import database as db
from .. import import_export
from . import spell_review
from .. import gemini_service
from . import theme
from .widgets import CenteredColumn, wrap_scrollable


class ConnectionTestWorker(QThread):
    """Gemini'ga sinov so'rovini fon oqimida yuboradi (interfeys qotib
    qolmasligi uchun)."""
    finished_ok = Signal(bool, str)

    def run(self):
        ok, message = gemini_service.test_connection()
        self.finished_ok.emit(ok, message)


class SettingsPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.test_worker = None
        self.queue_worker = None   # import navbatini ishlovchi fon oqimi

        # Sozlamalar ko'payib ketgani uchun sahifa skroll qilinadi (kichik oynada ham hammasi yetadi)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(40, 36, 40, 30)
        root.addWidget(wrap_scrollable(content))

        column = CenteredColumn(max_width=560)
        outer.addWidget(column)
        layout = column.layout_for_content()
        layout.setSpacing(10)

        title = QLabel("Sozlamalar")
        title.setFont(QFont(theme.FONT_FAMILY, 22, QFont.Bold))
        layout.addWidget(title)
        layout.addSpacing(8)

        layout.addWidget(self._field_label("Ismingiz (bosh sahifa va yon panelda ko'rinadi)"))
        self.name_input = QLineEdit()
        self.name_input.setPlaceholderText("Masalan: Zubayr")
        layout.addWidget(self.name_input)

        layout.addWidget(self._field_label("Rang mavzusi (ilova qayta ochilganda kuchga kiradi)"))
        self.palette_input = QComboBox()
        self.palette_input.addItems(list(theme.PALETTES.keys()))
        layout.addWidget(self.palette_input)

        layout.addWidget(self._field_label("Gemini API kaliti"))
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.api_key_input.setPlaceholderText("AIza... (ai.google.dev saytidan olinadi)")
        layout.addWidget(self.api_key_input)

        key_help = QLabel(
            "<b>Kalitni qanday olaman?</b> (3 qadam)<br>"
            "1. <a href=\"https://aistudio.google.com/apikey\" "
            f"style=\"color:{theme.ACCENT_HOVER}\">aistudio.google.com/apikey</a> ga shaxsiy Gmail bilan kiring.<br>"
            "2. «Create API key» tugmasini bosing (shartlar so'ralsa, qabul qiling).<br>"
            "3. Kalitni nusxalab, yuqoridagi maydonga qo'ying va «Saqlash»ni bosing.<br>"
            "<i>Kalitsiz ham ilova ishlaydi: kartalar va oddiy savollar AI'siz ham bor.</i>")
        key_help.setOpenExternalLinks(True)
        key_help.setWordWrap(True)
        key_help.setTextFormat(Qt.RichText)
        key_help.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px; margin: 2px 0 6px 0;")
        layout.addWidget(key_help)

        show_key_btn = QPushButton("👁  Kalitni ko'rsatish/yashirish")
        show_key_btn.setStyleSheet(theme.secondary_button())
        show_key_btn.clicked.connect(self.toggle_key_visibility)
        layout.addWidget(show_key_btn)

        layout.addWidget(self._field_label("Gemini model nomi"))
        self.model_input = QLineEdit()
        self.model_input.setPlaceholderText(gemini_service.DEFAULT_MODEL_NAME)
        layout.addWidget(self.model_input)

        model_hint = QLabel(
            "Google modellarni vaqti-vaqti bilan eskirtiradi. AI ishlamay qolsa, "
            "ai.google.dev/gemini-api/docs/models sahifasidan joriy nomni oling."
        )
        model_hint.setWordWrap(True)
        model_hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px; margin-bottom: 4px;")
        layout.addWidget(model_hint)

        self.test_btn = QPushButton("🔌  Ulanishni sinash")
        self.test_btn.setStyleSheet(theme.secondary_button())
        self.test_btn.clicked.connect(self.test_connection)
        layout.addWidget(self.test_btn)

        self.test_result_label = QLabel("")
        self.test_result_label.setWordWrap(True)
        layout.addWidget(self.test_result_label)

        layout.addWidget(self._field_label("Kunlik yangi so'z limiti (yangi kun tun 04:00 da boshlanadi)"))
        self.limit_input = QSpinBox()
        self.limit_input.setRange(1, 500)
        self.limit_input.setValue(20)
        layout.addWidget(self.limit_input)

        layout.addWidget(self._field_label("AI mashqidagi savollar soni (eng ko'pi)"))
        self.max_questions_input = QSpinBox()
        self.max_questions_input.setRange(1, 100)
        self.max_questions_input.setValue(20)
        layout.addWidget(self.max_questions_input)

        layout.addWidget(self._field_label(
            "Ingliz tili darajangiz (AI savollari va baholash shunga moslashadi)"))
        self.level_input = QComboBox()
        self.level_input.addItems(db.ENGLISH_LEVELS)
        self.level_input.setCurrentText("B1")
        layout.addWidget(self.level_input)

        layout.addWidget(self._field_label(
            "Flashcard'da yozish rejimi: o'zbekcha ma'no ko'rsatiladi, inglizcha so'zni "
            "siz yozasiz (eslab qolganingizni isbotlash). Standart: o'chiq."))
        self.typing_mode_input = QCheckBox("✍️  Yozish rejimini yoqish")
        layout.addWidget(self.typing_mode_input)

        layout.addWidget(self._field_label(
            "Talaffuz: karta ochilganda inglizcha so'z o'z-o'zidan chalinadi "
            "(👤 inson ovozi, 🤖 sintez). Standart: yoqiq."))
        self.auto_play_input = QCheckBox("🔊  Talaffuzni avtomatik chalish")
        layout.addWidget(self.auto_play_input)

        layout.addSpacing(6)
        save_btn = QPushButton("💾  Saqlash")
        save_btn.setMinimumHeight(48)
        save_btn.setStyleSheet(theme.accent_button())
        save_btn.clicked.connect(self.save_settings)
        layout.addWidget(save_btn)

        # --- Import / Eksport (CSV/TSV) ---
        layout.addSpacing(14)
        layout.addWidget(self._field_label(
            "Import / Eksport (CSV yoki TSV, UTF-8). Eksport A — faqat so'zlar; "
            "B — to'liq zaxira (FSRS holati bilan). Import: fayl 5 MB gacha, 10 000 qatorgacha; "
            "dublikatlar o'tkazib yuboriladi."))
        export_a_btn = QPushButton("📤  Eksport A: faqat so'zlar")
        export_a_btn.setStyleSheet(theme.secondary_button())
        export_a_btn.clicked.connect(lambda: self.export_file(import_export.MODE_WORDS))
        layout.addWidget(export_a_btn)
        export_b_btn = QPushButton("💾  Eksport B: to'liq zaxira")
        export_b_btn.setStyleSheet(theme.secondary_button())
        export_b_btn.clicked.connect(lambda: self.export_file(import_export.MODE_BACKUP))
        layout.addWidget(export_b_btn)
        self.import_btn = QPushButton("📥  Import (CSV/TSV)")
        self.import_btn.setStyleSheet(theme.secondary_button())
        self.import_btn.clicked.connect(self.import_file)
        layout.addWidget(self.import_btn)
        self.check_unchecked_btn = QPushButton("🔍  Tekshirilmagan so'zlarni hozir tekshirish")
        self.check_unchecked_btn.setStyleSheet(theme.secondary_button())
        self.check_unchecked_btn.clicked.connect(self.check_unchecked_now)
        layout.addWidget(self.check_unchecked_btn)
        self.review_suspects_btn = QPushButton("🧐  Shubhali so'zlarni ko'rib chiqish")
        self.review_suspects_btn.setStyleSheet(theme.secondary_button())
        self.review_suspects_btn.clicked.connect(self.review_suspect_words)
        layout.addWidget(self.review_suspects_btn)
        self.queue_label = QLabel("")
        self.queue_label.setWordWrap(True)
        self.queue_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        layout.addWidget(self.queue_label)

        outer.addStretch()

    def _field_label(self, text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px; margin-top: 6px;")
        return lbl

    def toggle_key_visibility(self):
        if self.api_key_input.echoMode() == QLineEdit.Password:
            self.api_key_input.setEchoMode(QLineEdit.Normal)
        else:
            self.api_key_input.setEchoMode(QLineEdit.Password)

    def refresh(self):
        self.name_input.setText(db.get_user_name())
        self.palette_input.setCurrentText(db.get_setting("accent_color", theme.DEFAULT_PALETTE))
        self.api_key_input.setText(db.get_setting("gemini_api_key", ""))
        self.model_input.setText(db.get_setting("gemini_model", gemini_service.DEFAULT_MODEL_NAME))
        self.limit_input.setValue(db.get_daily_new_limit())
        self.max_questions_input.setValue(db.get_max_questions())
        self.level_input.setCurrentText(db.get_english_level())
        self.typing_mode_input.setChecked(db.get_setting("typing_mode", "0") == "1")
        self.auto_play_input.setChecked(audio_service.is_auto_play())
        self.test_result_label.setText("")

    def save_settings(self):
        db.set_setting("user_name", self.name_input.text().strip())
        old_palette = db.get_setting("accent_color", theme.DEFAULT_PALETTE)
        db.set_setting("accent_color", self.palette_input.currentText())
        db.set_setting("gemini_api_key", self.api_key_input.text().strip())
        model = self.model_input.text().strip() or gemini_service.DEFAULT_MODEL_NAME
        db.set_setting("gemini_model", model)
        db.set_setting("daily_new_limit", str(self.limit_input.value()))
        db.set_setting("max_questions", str(self.max_questions_input.value()))
        db.set_setting("english_level", self.level_input.currentText())
        db.set_setting("typing_mode", "1" if self.typing_mode_input.isChecked() else "0")
        db.set_setting("auto_play", "1" if self.auto_play_input.isChecked() else "0")
        msg = "Sozlamalar saqlandi ✅"
        if old_palette != self.palette_input.currentText():
            msg += "\nYangi rang ilovani qayta ochganingizda ko'rinadi."
        QMessageBox.information(self, "Saqlandi", msg)
        self.main_window.refresh_user_name()

    def test_connection(self):
        api_key = self.api_key_input.text().strip()
        if not api_key:
            self.test_result_label.setStyleSheet(f"color: {theme.DANGER};")
            self.test_result_label.setText("❌ Avval API kalitni kiriting.")
            return

        db.set_setting("gemini_api_key", api_key)
        model = self.model_input.text().strip() or gemini_service.DEFAULT_MODEL_NAME
        db.set_setting("gemini_model", model)

        self.test_btn.setEnabled(False)
        self.test_result_label.setStyleSheet(f"color: {theme.WARNING};")
        self.test_result_label.setText(f"⏳ '{model}' modeli bilan tekshirilmoqda...")

        self.test_worker = ConnectionTestWorker()
        self.test_worker.finished_ok.connect(self._on_test_result)
        self.test_worker.start()

    def _on_test_result(self, ok: bool, message: str):
        self.test_btn.setEnabled(True)
        if ok:
            self.test_result_label.setStyleSheet(f"color: {theme.SUCCESS}; font-weight: bold;")
            self.test_result_label.setText(f"✅ Ishlayapti! Model: {self.model_input.text().strip()}")
        else:
            self.test_result_label.setStyleSheet(f"color: {theme.DANGER}; font-weight: bold;")
            self.test_result_label.setText(f"❌ Xato: {message}")

    # ------------------------------------------------------------------
    # Import / Eksport (CLAUDE-3)
    def export_file(self, mode: str):
        """Fayl tanlash oynasi orqali so'zlarni CSV/TSV ga chiqaradi (A yoki B tur)."""
        default = "sozlar_zaxira.csv" if mode == import_export.MODE_BACKUP else "sozlar.csv"
        path, _ = QFileDialog.getSaveFileName(
            self, "Eksport", default, "CSV (*.csv);;TSV (*.tsv)")
        if not path:
            return
        if not path.lower().endswith(import_export.ALLOWED_EXTENSIONS):
            path += ".csv"
        try:
            count = import_export.export_words(path, mode)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Eksport", f"Eksport bajarilmadi: {exc}")
            return
        QMessageBox.information(self, "Eksport", f"{count} ta so'z saqlandi ✅\n{path}")

    def import_file(self):
        """Fayl tanlab so'zlarni import qiladi, natijani ko'rsatadi va fon navbatini boshlaydi."""
        path, _ = QFileDialog.getOpenFileName(
            self, "Import", "", "CSV/TSV (*.csv *.tsv)")
        if not path:
            return
        QGuiApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = import_export.import_words(path)
        finally:
            QGuiApplication.restoreOverrideCursor()
        if result.error:
            QMessageBox.warning(self, "Import", f"❌ {result.error}")
            return
        text = (f"Qo'shildi: {result.added}\nDublikat (o'tkazib yuborildi): {result.duplicates}\n"
                f"Yaroqsiz qator: {result.invalid}")
        if result.invalid_rows:
            shown = "; ".join(f"{n}-qator: {why}" for n, why in result.invalid_rows[:5])
            text += f"\n({shown})"
        if result.added:
            text += "\n\nImlo tekshiruvi va audio fonda davom etadi. Tekshirilmaguncha yangi so'zlar kartalarda chiqmaydi."
        QMessageBox.information(self, "Import", text)
        self.start_import_queue()
        self._refresh_home()

    def review_suspect_words(self):
        """Tugma: imlo tekshiruvi 'shubhali' deb topgan so'zlarni birma-bir ko'rib chiqadi
        (To'g'irla / Men shunday yozdim / Keyinroq)."""
        counts = spell_review.review_suspects(self)
        if counts["total"] == 0:
            QMessageBox.information(self, "Shubhali so'zlar", "Shubhali so'z yo'q ✅")
        else:
            QMessageBox.information(
                self, "Shubhali so'zlar",
                f"Jami: {counts['total']}\nTo'g'irlandi: {counts['fixed']}\n"
                f"Tasdiqlandi: {counts['confirmed']}\nO'zi hal bo'ldi: {counts['resolved_auto']}\n"
                f"Keyinroqqa qoldi: {counts['later']}")
        self._refresh_home()

    def start_import_queue(self):
        """Navbatda vazifa bo'lsa, fon oqimini ishga tushiradi (allaqachon ishlayotgan bo'lsa - yo'q)."""
        if self.queue_worker is not None and self.queue_worker.isRunning():
            return
        if import_export.pending_count() == 0:
            return
        worker = import_export.ImportQueueWorker(
            spell_fn=import_export.spell_batch,
            audio_fn=audio_service.ensure_word_audio, parent=self)
        worker.progress.connect(self._on_queue_progress)
        worker.queue_finished.connect(self._on_queue_finished)
        self.queue_worker = worker
        self.queue_label.setText(import_export.progress_text(0, import_export.pending_count()))
        worker.start()

    def resume_import_queue(self):
        """Ilova ochilganda: avvalgi importdan qolgan vazifalarni va imlo tekshiruvi
        o'tkazilmay qolgan ('unchecked') so'zlarni fonda davom ettiradi."""
        import_export.enqueue_unchecked()
        self.start_import_queue()

    def check_unchecked_now(self):
        """Tugma: tekshirilmay qolgan so'zlarni hozir imlo navbatiga qo'yib, tekshiradi."""
        import_export.enqueue_unchecked()
        if import_export.pending_count() == 0:
            QMessageBox.information(self, "Imlo tekshiruvi", "Tekshirilmagan so'z yo'q ✅")
            return
        self.start_import_queue()

    def _on_queue_progress(self, done: int, total: int):
        """Navbat jarayoni: 'Tekshirilmoqda: 45/200'."""
        self.queue_label.setText(import_export.progress_text(done, total))

    def _on_queue_finished(self, report):
        """Navbat tugadi yoki to'xtadi: holatni ko'rsatadi va bosh sahifani yangilaydi."""
        if report.remaining:
            detail = f" — {report.errors[-1]}" if report.errors else ""
            self.queue_label.setText(
                f"Navbat to'xtadi: {report.remaining} ta vazifa qoldi{detail}\n"
                "Ilova keyingi ochilganda yoki tugmani qayta bosganda davom etadi.")
        else:
            self.queue_label.setText("✅ Imlo tekshiruvi va audio tugadi.")
        self._refresh_home()

    def _refresh_home(self):
        """Bosh sahifadagi sonlarni yangilaydi (agar sahifa mavjud bo'lsa)."""
        home = getattr(self.main_window, "pages", {}).get("home")
        if home is not None:
            home.refresh()
