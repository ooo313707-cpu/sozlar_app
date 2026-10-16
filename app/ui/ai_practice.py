"""
ai_practice.py
---------------
AI mashq sahifasi (v1.3). Kirish nuqtalari (so'zlar 70% = 45% zaif + 25% yaxshi
qoidasi bilan tanlanadi, savollar soni Sozlamalardagi limit bilan cheklanadi):

  (a) start_for_pack(category, pack)   - bo'limcha
  (b) start_for_section(category)      - butun bo'lim
  (c) start_free_chat_mode()           - B rejimi: hamma o'rganilgan so'zlar
  (d) start_after_cards(...)           - A rejimi: kartalar tugagach

Oqim:
  1) Har so'zga savol turi biriktiriladi (questions.build_items).
  2) Gemini BITTA so'rovda hamma savollarni JSON qilib beradi (fon oqimida).
     Kalit/internet bo'lmasa yoki savol yaroqsiz bo'lsa - mahalliy (AI'siz) savol
     qo'yiladi, shuning uchun mashq hech qachon to'xtab qolmaydi.
  3) Mahalliy turlar (variant, bo'sh joy, tarjima) darhol tekshiriladi va FSRS'ga
     yuboriladi; ochiq turlar (gap, xato tuzatish, vaziyat, matn) oxirida BITTA
     so'rovda baholanadi. Baho kodda hisoblanadi (questions.rating_from_ai).
  4) Kartalar tugashidan oldin AI savollari fonda oldindan tayyorlanadi (prefetch).
"""

import random
import time

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QFrame, QTextEdit, QMessageBox,
)
from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QFont

from .. import database as db
from .. import bank_service
from .. import gemini_service
from .. import questions as qs
from . import theme
from .widgets import CenteredColumn


class GenerateWorker(QThread):
    """Gemini'dan hamma savollarni so'raydi (fon oqimida, interfeys qotmasligi uchun)."""
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, payload, level, mode):
        super().__init__()
        self.payload, self.level, self.mode = payload, level, mode

    def run(self):
        try:
            self.finished_ok.emit(gemini_service.generate_questions(
                self.payload, level=self.level, mode=self.mode))
        except Exception as e:
            self.failed.emit(str(e))


class GradeBatchWorker(QThread):
    """Ochiq javoblarning hammasini BITTA so'rovda baholaydi (fon oqimida)."""
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, entries, level):
        super().__init__()
        self.entries, self.level = entries, level

    def run(self):
        try:
            self.finished_ok.emit(gemini_service.grade_batch(self.entries, level=self.level))
        except Exception as e:
            self.failed.emit(str(e))


class Generation:
    """Bitta savollar yaratish so'rovi holati (oldindan tayyorlash uchun ham)."""

    def __init__(self, words, items, mode, level, worker, plan=None):
        self.words, self.items, self.mode, self.level = words, items, mode, level
        self.plan = plan          # bank_service.SessionPlan (bankdagi savollar bilan)
        self.worker = worker
        self.active = False       # sahifa bu so'rovni hozir kutayaptimi
        self.done = False
        self.result = None
        self.error = None


class AIPracticePage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        self.mode = "A"
        self.category: str | None = None
        self.pack: int | None = None
        self.offer_forgotten_at_end = False
        self.questions: list[dict] = []
        self.current_index = 0
        self.pending: list[dict] = []        # AI baholashini kutayotgan ochiq javoblar
        self.summary: list[str] = []
        self.transcript_lines: list[str] = []
        self.word_ids: list[int] = []
        self.awaiting_next = False
        self.answered = 0
        self.correct_count = 0
        self._question_shown_at = 0.0
        self._gen: Generation | None = None
        self._workers: list[QThread] = []    # ishlayotgan oqimlar havolasi
        self.grade_worker = None
        self._option_buttons: list[QPushButton] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 30, 40, 30)

        column = CenteredColumn(max_width=700)
        outer.addWidget(column, stretch=1)
        layout = column.layout_for_content()
        layout.setSpacing(12)

        self.title_label = QLabel("AI bilan mashq")
        self.title_label.setFont(QFont(theme.FONT_FAMILY, 20, QFont.Bold))
        layout.addWidget(self.title_label)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.skip_btn = QPushButton("⏭  AI'ni o'tkazib yuborish")
        self.skip_btn.setStyleSheet(theme.secondary_button())
        self.skip_btn.clicked.connect(self.skip_ai)
        layout.addWidget(self.skip_btn)
        self.skip_btn.setVisible(False)

        self.passage_frame = QFrame()
        self.passage_frame.setStyleSheet(theme.card_frame_style(radius=16))
        passage_layout = QVBoxLayout(self.passage_frame)
        passage_layout.setContentsMargins(22, 18, 22, 18)
        self.passage_label = QLabel("")
        self.passage_label.setWordWrap(True)
        self.passage_label.setFont(QFont(theme.FONT_FAMILY, 13))
        passage_layout.addWidget(self.passage_label)
        layout.addWidget(self.passage_frame)
        self.passage_frame.setVisible(False)

        self.progress_label = QLabel("")
        self.progress_label.setStyleSheet(f"color: {theme.ACCENT}; font-weight: 600;")
        layout.addWidget(self.progress_label)

        self.question_label = QLabel("")
        self.question_label.setWordWrap(True)
        self.question_label.setFont(QFont(theme.FONT_FAMILY, 15, QFont.Bold))
        layout.addWidget(self.question_label)

        self.options_box = QWidget()
        self.options_layout = QVBoxLayout(self.options_box)
        self.options_layout.setContentsMargins(0, 0, 0, 0)
        self.options_layout.setSpacing(8)
        layout.addWidget(self.options_box)

        self.answer_input = QTextEdit()
        self.answer_input.setPlaceholderText("Javobingizni shu yerga yozing...")
        self.answer_input.setMaximumHeight(90)
        layout.addWidget(self.answer_input)

        self.submit_btn = QPushButton("Javobni yuborish")
        self.submit_btn.setMinimumHeight(48)
        self.submit_btn.setStyleSheet(theme.accent_button())
        self.submit_btn.clicked.connect(self._on_submit_btn_clicked)
        layout.addWidget(self.submit_btn)

        self.feedback_label = QLabel("")
        self.feedback_label.setWordWrap(True)
        self.feedback_label.setStyleSheet("font-size: 13px; margin-top: 4px;")
        layout.addWidget(self.feedback_label)

        self.bad_btn = QPushButton("👎  Yomon savol")
        self.bad_btn.setStyleSheet(theme.secondary_button())
        self.bad_btn.clicked.connect(self._mark_bad)
        layout.addWidget(self.bad_btn)
        self.bad_btn.setVisible(False)

        self.tip_label = QLabel("")
        self.tip_label.setWordWrap(True)
        self.tip_label.setStyleSheet(f"color: {theme.WARNING}; font-size: 13px;")
        layout.addWidget(self.tip_label)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 13px;")
        layout.addWidget(self.summary_label)

        self.retry_btn = QPushButton("🔄  Baholashni qayta urinish")
        self.retry_btn.setStyleSheet(theme.accent_button())
        self.retry_btn.clicked.connect(self._grade_pending)
        layout.addWidget(self.retry_btn)
        self.retry_btn.setVisible(False)

        self.forgotten_btn = QPushButton("🔁  Unutilgan so'zlarni takrorlash")
        self.forgotten_btn.setStyleSheet(theme.secondary_button())
        self.forgotten_btn.clicked.connect(
            lambda: main_window.offer_forgotten_review(self.category, self.pack))
        layout.addWidget(self.forgotten_btn)
        self.forgotten_btn.setVisible(False)

        self.finish_btn = QPushButton("🏠  Bosh sahifaga qaytish")
        self.finish_btn.setStyleSheet(theme.secondary_button())
        self.finish_btn.clicked.connect(lambda: main_window.show_page("home"))
        layout.addWidget(self.finish_btn)
        self.finish_btn.setVisible(False)

        outer.addStretch()
        self._set_question_visible(False)

    def refresh(self):
        pass

    # ------------------------------------------------------------------
    # Ko'rinish yordamchilari
    # ------------------------------------------------------------------
    def _set_question_visible(self, visible: bool):
        self.progress_label.setVisible(visible)
        self.question_label.setVisible(visible)
        self.submit_btn.setVisible(visible)
        if not visible:
            self.answer_input.setVisible(False)
            self.options_box.setVisible(False)

    def _clear_options(self):
        for b in self._option_buttons:
            b.setParent(None)
            b.deleteLater()
        self._option_buttons = []

    # ------------------------------------------------------------------
    # Kirish nuqtalari
    # ------------------------------------------------------------------
    def _select(self, words, struggled_ids=None):
        # D-008: imlo tekshiruvidan o'tmagan ('unchecked'/'suspect') so'zlar AI mashqda ham qatnashmaydi
        words = [w for w in words if db.is_studyable(w)]
        return db.select_practice_words(words, struggled_ids or set(),
                                        limit=db.get_max_questions())

    def _nothing_seen(self):
        QMessageBox.information(
            self, "Hali erta",
            "Bu doirada hali ko'rilgan so'z yo'q. Avval 📚 Kartalar orqali "
            "so'zlarni ko'ring - shundan keyin AI mashq mavjud bo'ladi."
        )
        self.main_window.show_page("home")

    def start_for_pack(self, category: str, pack: int) -> bool:
        """(a) Bo'limcha: shu bo'limchaning ko'rilgan so'zlari (eng ko'pi 14 savol)."""
        chosen = self._select(db.get_pack_words(category, pack))
        if not chosen:
            self._nothing_seen()
            return False
        self._start(chosen, "A", category, pack)
        return True

    def start_for_section(self, category: str) -> bool:
        """(b) Butun bo'lim: HAMMA so'zlar orasidan, savollar soni limit bilan."""
        chosen = self._select(db.get_category_words(category))
        if not chosen:
            self._nothing_seen()
            return False
        self._start(chosen, "A", category, None)
        return True

    def start_free_chat_mode(self) -> bool:
        """(c) B rejimi: hamma bo'limlardagi barcha o'rganilgan so'zlar, limit bilan."""
        chosen = self._select(db.get_all_words())
        if not chosen:
            QMessageBox.information(
                self, "Hali erta",
                "Avval kamida bir nechta so'zni kartalar orqali ko'ring - "
                "shundan keyin AI mashq mavjud bo'ladi."
            )
            self.main_window.show_page("home")
            return False
        self._start(chosen, "B", None, None)
        return True

    def prefetch_after_cards(self, seen_ids, struggled_ids, category=None, pack=None):
        """Kartalar tugashidan OLDIN fonda savollarni tayyorlab qo'yadi (kutish yo'qolsin)."""
        if not db.get_setting("gemini_api_key"):
            return
        chosen = self._select(db.get_words_by_ids(seen_ids), struggled_ids)
        if not chosen:
            return
        gen = self._make_generation(chosen, "A")
        if gen is None:
            return
        gen.scope = (category, pack)
        self._gen = gen

    def discard_prefetch(self):
        """Yangi kartalar sessiyasi boshlanganda eski (ishlatilmagan) tayyorlov tashlanadi."""
        if self._gen is not None and not self._gen.active:
            self._gen = None

    def start_after_cards(self, seen_ids, struggled_ids, category=None, pack=None):
        """(d) A rejimi: kartalar tugagach. Oldindan tayyorlangan savollar bo'lsa, o'shalar ishlatiladi."""
        gen = self._gen
        if gen is not None and getattr(gen, "scope", None) == (category, pack) and not gen.active:
            self._begin_ui(gen.words, "A", category, pack, offer_forgotten=True)
            self.main_window.show_page("ai_practice")
            self._attach(gen)
            return
        chosen = self._select(db.get_words_by_ids(seen_ids), struggled_ids)
        if not chosen:
            self.main_window.offer_forgotten_review(category, pack)
            return
        self._start(chosen, "A", category, pack, offer_forgotten=True)
        self.main_window.show_page("ai_practice")

    # Eski nom (v1.1) - moslik uchun
    def start_after_session(self, words: list):
        self.start_after_cards([w["id"] for w in words], set())

    # ------------------------------------------------------------------
    # Sessiyani boshlash
    # ------------------------------------------------------------------
    def _begin_ui(self, words, mode, category, pack, offer_forgotten=False):
        self.mode, self.category, self.pack = mode, category, pack
        self.offer_forgotten_at_end = offer_forgotten
        self.questions, self.current_index = [], 0
        self.pending, self.summary, self.transcript_lines = [], [], []
        self.word_ids = [w["id"] for w in words]
        self.awaiting_next = False
        self.answered = self.correct_count = 0

        if mode == "B":
            title = "💬 AI mashq · hamma o'rganilgan so'zlar"
        elif category is None:
            title = "🤖 AI tekshiruvi"
        elif pack is None:
            title = f"🤖 AI mashq · {category}"
        else:
            title = f"🤖 AI mashq · {category} · {pack}-bo'limcha"
        self.title_label.setText(title)
        self.status_label.setText(
            f"⏳ Gemini {len(words)} ta so'z bo'yicha savollar tayyorlayapti...")
        self.passage_frame.setVisible(False)
        self.passage_label.setText("")
        self._set_question_visible(False)
        self.feedback_label.setText("")
        self.tip_label.setText("")
        self.summary_label.setText("")
        self.finish_btn.setVisible(False)
        self.forgotten_btn.setVisible(False)
        self.retry_btn.setVisible(False)
        self.skip_btn.setVisible(True)

    def _make_generation(self, words, mode):
        """Savol turlarini biriktirib, Gemini so'rovini fonda boshlaydi (yoki None - hamma savol mahalliy)."""
        plan = bank_service.plan_session(words)       # avval savol banki (internet kerak emas)
        payload = plan.ai_payload()                    # faqat bankdan topilmaganlar Gemini'ga
        level = plan.level
        gen = Generation(words, plan.items, mode, level, None, plan=plan)
        if not payload or not db.get_setting("gemini_api_key"):
            gen.done, gen.error = True, ("API kalit kiritilmagan" if payload else None)
            return gen
        worker = GenerateWorker(payload, level, mode)
        worker.finished_ok.connect(lambda res, g=gen: self._on_generated(g, res, None))
        worker.failed.connect(lambda err, g=gen: self._on_generated(g, None, err))
        gen.worker = worker
        self._workers = [w for w in self._workers if w.isRunning()] + [worker]
        worker.start()
        return gen

    def _start(self, words, mode, category, pack, offer_forgotten=False):
        self._begin_ui(words, mode, category, pack, offer_forgotten)
        gen = self._make_generation(words, mode)
        self._attach(gen)

    def _attach(self, gen: Generation):
        """Sahifa shu so'rov natijasini kutadi (tayyor bo'lsa - darhol ishlatadi)."""
        self._gen = gen
        gen.active = True
        if gen.done:
            self._process_generation(gen)

    def _on_generated(self, gen: Generation, result, error):
        gen.result, gen.error, gen.done = result, error, True
        if gen.active and gen is self._gen:
            self._process_generation(gen)

    def skip_ai(self):
        """'AI'ni o'tkazib yuborish': so'rov natijasi endi e'tiborga olinmaydi."""
        self._gen = None
        self.skip_btn.setVisible(False)
        self.status_label.setText("")
        if self.offer_forgotten_at_end:
            self.offer_forgotten_at_end = False
            self.main_window.offer_forgotten_review(self.category, self.pack)
        else:
            self.main_window.show_page("home")

    def _process_generation(self, gen: Generation):
        self.skip_btn.setVisible(False)
        if gen.error and gen.result is None:
            # Internet/kalit yo'q yoki Gemini xatosi: mahalliy savollar bilan davom etamiz
            raw, note = {}, (f"⚠️ AI bilan bog'lanib bo'lmadi ({gen.error}). "
                             f"Mahalliy savollar bilan davom etamiz.")
        else:
            raw, note = gen.result or {}, ""
        self.questions, fallbacks, _saved = bank_service.finish_session(gen.plan, raw)
        if not note and fallbacks:
            note = f"ℹ️ {fallbacks} ta savol AI'siz (mahalliy) tuzildi."
        self.status_label.setText(note)
        self.current_index = 0
        if not self.questions:
            self._complete()
            return
        self._show_question()

    # ------------------------------------------------------------------
    # Savolni ko'rsatish
    # ------------------------------------------------------------------
    def _show_question(self):
        if self.current_index >= len(self.questions):
            self._finish_questions()
            return
        q = self.questions[self.current_index]
        self._clear_options()
        self.feedback_label.setText("")
        self.tip_label.setText("")
        self.awaiting_next = False
        self.bad_btn.setVisible(False)
        self.bad_btn.setEnabled(True)
        self.bad_btn.setText("👎  Yomon savol")

        self.progress_label.setText(
            f"Savol {self.current_index + 1} / {len(self.questions)}  ·  {qs.TYPE_TITLES[q['type']]}")
        self.question_label.setText(q["question"])
        if q["type"] == "passage" and q.get("passage"):
            self.passage_label.setText(q["passage"])
            self.passage_frame.setVisible(True)
        else:
            self.passage_frame.setVisible(False)

        self._set_question_visible(True)
        if qs.is_mcq(q):
            self.answer_input.setVisible(False)
            self.submit_btn.setVisible(False)
            self.options_box.setVisible(True)
            for i, text in enumerate(q["options"]):
                b = QPushButton(text)
                b.setMinimumHeight(44)
                b.setStyleSheet(theme.secondary_button())
                b.clicked.connect(lambda checked=False, idx=i: self._choose_option(idx))
                self.options_layout.addWidget(b)
                self._option_buttons.append(b)
        else:
            self.options_box.setVisible(False)
            self.answer_input.setVisible(True)
            self.answer_input.setMaximumHeight(52 if q["checker"] == "local" else 90)
            self.answer_input.clear()
            self.answer_input.setEnabled(True)
            self.submit_btn.setVisible(True)
            self.submit_btn.setText("Javobni yuborish")
            self.submit_btn.setEnabled(True)
        self._question_shown_at = time.monotonic()

    # ------------------------------------------------------------------
    # Javob berish
    # ------------------------------------------------------------------
    def _choose_option(self, idx: int):
        if self.awaiting_next:
            return
        self._answer(idx)

    def _on_submit_btn_clicked(self):
        if self.awaiting_next:
            self.current_index += 1
            self._show_question()
            return
        text = self.answer_input.toPlainText().strip()
        if not text:
            QMessageBox.warning(self, "Bo'sh javob", "Iltimos, javob yozing.")
            return
        self._answer(text)

    def _go_next_ready(self, last: bool):
        self.awaiting_next = True
        self.submit_btn.setVisible(True)
        self.submit_btn.setEnabled(True)
        self.submit_btn.setText("Yakunlash 🎉" if last else "Keyingi savol ➜")

    def _answer(self, answer):
        q = self.questions[self.current_index]
        ms = int((time.monotonic() - self._question_shown_at) * 1000)
        last = self.current_index + 1 >= len(self.questions)

        if q["checker"] == "local":
            res = qs.grade_local(q, answer)
            db.review_word(q["word_id"], res["rating"], source="ai", duration_ms=ms)
            bank_service.record_answer(q, res["correct"])
            self.bad_btn.setVisible(bool(q.get("bank_id")))
            self.answered += 1
            self.correct_count += 1 if res["correct"] else 0
            icon = "✅" if res["correct"] else "❌"
            color = theme.SUCCESS if res["correct"] else theme.DANGER
            self.feedback_label.setStyleSheet(f"color: {color}; font-weight: 600;")
            self.feedback_label.setText(f"{icon} {res['feedback']}")
            if q.get("tip") and not res["correct"]:
                self.tip_label.setText(f"💡 {q['tip']}")
            self.summary.append(f"{icon} {q['word']} — {qs.TYPE_TITLES[q['type']]}")
            self.transcript_lines.append(
                f"[{q['type']}] S: {q['question']}\nJ: {answer}\nBaho: {res['rating']}\n")
            for b in self._option_buttons:
                b.setEnabled(False)
            self.answer_input.setEnabled(False)
        else:
            self.pending.append({"id": len(self.pending) + 1, "q": q, "answer": answer, "ms": ms,
                                 "passage": q.get("passage")})
            self.feedback_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
            self.feedback_label.setText("📝 Javob qabul qilindi - oxirida AI hammasini birga baholaydi.")
            self.transcript_lines.append(f"[{q['type']}] S: {q['question']}\nJ: {answer}\n")
            self.answer_input.setEnabled(False)
        self._go_next_ready(last)

    def _mark_bad(self):
        """'Yomon savol': joriy savol bankda 'bad' bo'ladi va boshqa chiqmaydi."""
        if self.current_index >= len(self.questions):
            return
        if bank_service.mark_bad(self.questions[self.current_index]):
            self.bad_btn.setEnabled(False)
            self.bad_btn.setText("✔ Belgilandi - boshqa chiqmaydi")

    # ------------------------------------------------------------------
    # Ochiq javoblarni ommaviy baholash
    # ------------------------------------------------------------------
    def _finish_questions(self):
        self._set_question_visible(False)
        self.passage_frame.setVisible(False)
        self._clear_options()
        if self.pending:
            self._grade_pending()
        else:
            self._complete()

    def _grade_pending(self):
        self.retry_btn.setVisible(False)
        self.finish_btn.setVisible(False)
        self.feedback_label.setText("")
        self.status_label.setText(
            f"⏳ AI {len(self.pending)} ta javobingizni birga tekshirayapti...")
        entries = [{"id": p["id"], "word": p["q"]["word"], "translation": p["q"]["translation"],
                    "type": p["q"]["type"], "question": p["q"]["question"],
                    **({"passage": p["passage"]} if p["passage"] else {}),
                    "answer": p["answer"]} for p in self.pending]
        worker = GradeBatchWorker(entries, db.get_english_level())
        worker.finished_ok.connect(self._on_batch_graded)
        worker.failed.connect(self._on_batch_failed)
        self.grade_worker = worker
        self._workers = [w for w in self._workers if w.isRunning()] + [worker]
        worker.start()

    def _on_batch_failed(self, error: str):
        self.status_label.setText("")
        self.feedback_label.setStyleSheet(f"color: {theme.DANGER};")
        self.feedback_label.setText(
            f"❌ Javoblarni baholab bo'lmadi: {error}\n"
            f"Javoblaringiz saqlangan - internetni tekshirib, qayta urinib ko'ring.")
        self.retry_btn.setVisible(True)
        self.finish_btn.setVisible(True)

    def _on_batch_graded(self, results: dict):
        still = []
        for p in self.pending:
            q, res = p["q"], results.get(p["id"])
            rating = qs.rating_from_ai(q["type"], res)
            if rating is None:
                still.append(p)
                continue
            db.review_word(q["word_id"], rating, source="ai", duration_ms=p["ms"])
            ok = rating in ("good", "easy")
            bank_service.record_answer(q, ok)
            self.answered += 1
            self.correct_count += 1 if ok else 0
            icon = "✅" if ok else ("⚠️" if rating == "hard" else "❌")
            fb = str(res.get("feedback", "")).strip()
            self.summary.append(f"{icon} {q['word']} — {qs.TYPE_TITLES[q['type']]}"
                                + (f": {fb}" if fb else ""))
            self.transcript_lines.append(
                f"[{q['type']}] {q['word']} -> {rating}" + (f" ({fb})" if fb else "") + "\n")
        self.pending = still
        if still:
            self._on_batch_failed(f"{len(still)} ta javob uchun natija kelmadi")
            return
        self.status_label.setText("")
        self._complete()

    # ------------------------------------------------------------------
    def _complete(self):
        self._set_question_visible(False)
        self.passage_frame.setVisible(False)
        self.skip_btn.setVisible(False)
        self.retry_btn.setVisible(False)
        self.feedback_label.setStyleSheet(f"color: {theme.SUCCESS}; font-weight: 600;")
        self.feedback_label.setText(
            f"🎉 Mashq tugadi! To'g'ri javoblar: {self.correct_count} / {self.answered}")
        self.summary_label.setText("\n".join(self.summary))
        self.finish_btn.setVisible(True)
        self.forgotten_btn.setVisible(self.mode == "A")

        db.save_ai_session(self.mode, "\n".join(self.transcript_lines), self.word_ids)

        # Kartalardan keyingi A rejimi tugadi -> "Unutilgan so'zlarni takrorlamoqchimisiz?"
        if self.offer_forgotten_at_end:
            self.offer_forgotten_at_end = False
            self.main_window.offer_forgotten_review(self.category, self.pack)
