"""
flashcard.py
------------
Klassik flashcard ekrani: old tomonda so'z, javob ochilgach tarjima + misol,
so'ng Again/Hard/Good/Easy bilan baholaysiz.

v1.2:
  * Navbat (queue): baholagandan keyin yangi muddat 20 daqiqa ichida bo'lsa,
    karta qayta qo'yiladi (Again ~4 karta keyin, boshqalar oxiriga). Kun
    bo'yi muddatli kartalar qaytmaydi.
  * Tugmalar ustida keyingi muddat ko'rinadi (Again <1 daq · Hard · Good · Easy).
  * Klaviatura: Space/Enter = javobni ko'rsatish, 1-4 = baho.
  * Javob vaqti o'lchanib, review_word(duration_ms=...) ga beriladi.
  * Tanlangan bo'lim/bo'limcha doirasida ishlaydi; tugagach A rejimi
    (AI mashq), undan keyin "Unutilgan so'zlarni takrorlamoqchimisiz?".

v1.5 (CLAUDE-2):
  * Again/Hard olgan kartalar sessiya OXIRIDA qayta ko'rsatiladi (retry_queue).
    FSRS faqat birinchi baholashda yangilanadi; qayta ko'rishda baho yo'q,
    faqat "Keyingi" (Space). Avvalgi "20 daqiqa ichida qaytarish" (apply_requeue)
    endi ishlatilmaydi (funksiya testlar uchun qoldirilgan).
  * Yozish rejimi (typing_mode=1): o'zbekcha ma'no + input, Enter -> avtomatik
    baho (to'g'ri=Good, 5+ harfli so'zda 1 harf xato=Hard, xato=Again).
    To'g'ri javobdan keyin faqat Easy ni qo'lda tanlash mumkin.
  * Imlo holati 'unchecked'/'suspect' so'zlar kartalarda qatnashmaydi.

v1.6 (CLAUDE-3):
  * Audio: talaffuz (🔊 tugmasi, 👤 inson / 🤖 sintez belgisi). `auto_play`
    (standart yoqiq) bo'lsa oddiy rejimda so'z ko'rinishi bilan, yozish rejimida javob
    yozilgach o'z-o'zidan chalinadi. Audio
    fon oqimida topiladi (audio_service zanjiri), interfeys qotmaydi.
"""

import time
from datetime import timedelta
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame, QLineEdit,
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont

from .. import audio_service
from .. import database as db
from . import theme
from .widgets import CenteredColumn

REQUEUE_WINDOW = timedelta(minutes=20)   # shu muddat ichida qaytadigan kartalar
AGAIN_GAP = 4                            # Again ~4 karta keyin qaytadi

# Yozish rejimi: javob yozilgach (to'g'ri yoki xato) talaffuz chalinadi, shundan
# keyin shuncha millisekunddan so'ng keyingi kartaga avtomatik o'tiladi.
TYPED_AUTO_ADVANCE_MS = 6500
# Enter bilan javob yuborilgach shu oraliqda kelgan Enter keyingi kartaga o'tkazmaydi
# (QLineEdit Enter'ni "ignore" qiladi, hodisa karta sahifasiga o'tib, darrov advance qilardi).
ENTER_GUARD_SECONDS = 0.3

RATING_KEYS = {Qt.Key_1: "again", Qt.Key_2: "hard", Qt.Key_3: "good", Qt.Key_4: "easy"}


def typing_mode_enabled() -> bool:
    """Sozlamalardagi `typing_mode` (0/1, standart 0) yoqilganmi."""
    return db.get_setting("typing_mode", "0") == "1"


def _within_one_edit(a: str, b: str) -> bool:
    """a va b orasida ko'pi bilan 1 ta harf farqi (almashtirish, qo'shish yoki
    tushirib qoldirish) bormi."""
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a                       # a - qisqasi
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    if len(a) == len(b):
        return a[i + 1:] == b[i + 1:]     # bitta harf almashgan
    return a[i:] == b[i + 1:]             # b da bitta ortiqcha harf


def grade_typed_answer(typed: str, target: str) -> str | None:
    """Yozilgan javobni baholaydi (katta-kichik harf va ortiqcha bo'shliqqa
    e'tibor berilmaydi): to'g'ri -> "good"; 5+ harfli so'zda 1 harf xato ->
    "hard" (typo); boshqasi -> "again". Maydon bo'sh bo'lsa None."""
    norm = lambda t: " ".join((t or "").casefold().split())
    typed_n, target_n = norm(typed), norm(target)
    if not typed_n:
        return None
    if typed_n == target_n:
        return "good"
    if len(target_n) >= 5 and _within_one_edit(typed_n, target_n):
        return "hard"
    return "again"


def apply_requeue(queue: list, word_id: int, rating: str, due, now) -> bool:
    """Baholangan kartani kerak bo'lsa navbatga qaytaradi (queue - hozirgi karta
    allaqachon olib tashlangan qolgan navbat). Yangi muddat 20 daqiqa ichida
    bo'lsa: Again -> ~4 karta keyin, Hard/Good/Easy -> navbat oxiriga.
    Qaytarilgan bo'lsa True."""
    if due - now > REQUEUE_WINDOW:
        return False
    if rating == "again":
        queue.insert(min(AGAIN_GAP, len(queue)), word_id)
    else:
        queue.append(word_id)
    return True


class FlashcardPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        # Sessiya holati
        self.queue: list[int] = []        # ko'rsatiladigan so'z id'lari (navbat)
        self.current_id: int | None = None
        self.current_row = None
        self.seen_ids: list[int] = []     # shu sessiyada ko'rilgan so'zlar (tartib bilan)
        self.struggled_ids: set[int] = set()   # shu sessiyada Again/Hard olganlar
        self.category: str | None = None
        self.pack: int | None = None
        self.ai_after = True              # tugagach A rejimi boshlansinmi
        self.answer_shown = False
        self._shown_at = 0.0
        self.retry_queue: list[int] = []  # Again/Hard olganlar - sessiya oxirida qayta ko'rsatiladi
        self.in_retry = False             # hozir "qayta ko'rish" bosqichidami (FSRS yangilanmaydi)
        self.typing_active = False        # joriy karta yozish rejimidami
        self._typed_grade: str | None = None   # yozish natijasidagi avtomatik baho
        self.audio_player = audio_service.AudioPlayer(self)   # talaffuzni o'ynovchi
        self._auto_timer = QTimer(self)                       # yozish rejimi: avto-o'tish
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self._on_auto_advance)
        self._last_submit = 0.0                               # Enter himoyasi uchun
        self._audio_workers: list = []    # ishlayotgan audio oqimlari (GC o'chirib yubormasin)

        self.setFocusPolicy(Qt.StrongFocus)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(40, 30, 40, 30)

        column = CenteredColumn(max_width=620)
        outer.addWidget(column, stretch=1)
        layout = column.layout_for_content()
        layout.setSpacing(18)

        self.scope_label = QLabel("")
        self.scope_label.setAlignment(Qt.AlignCenter)
        self.scope_label.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(self.scope_label)

        self.progress_label = QLabel("")
        self.progress_label.setAlignment(Qt.AlignCenter)
        self.progress_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
        layout.addWidget(self.progress_label)

        self.card_frame = QFrame()
        self.card_frame.setStyleSheet(theme.card_frame_style(radius=20))
        self.card_frame.setMinimumHeight(260)
        card_layout = QVBoxLayout(self.card_frame)
        card_layout.setContentsMargins(30, 30, 30, 30)
        card_layout.addStretch()

        self.word_label = QLabel("")
        self.word_label.setFont(QFont(theme.FONT_FAMILY, 30, QFont.Bold))
        self.word_label.setAlignment(Qt.AlignCenter)
        self.word_label.setWordWrap(True)
        card_layout.addWidget(self.word_label)

        # Audio: 🔊 tugmasi + 👤/🤖 belgisi (so'z ko'ringandagina ko'rinadi)
        audio_row = QHBoxLayout()
        audio_row.addStretch()
        self.audio_btn = QPushButton("🔊")
        self.audio_btn.setToolTip("Talaffuzni eshitish")
        self.audio_btn.setFocusPolicy(Qt.NoFocus)
        self.audio_btn.setFixedSize(44, 36)
        self.audio_btn.setStyleSheet(theme.secondary_button())
        self.audio_btn.clicked.connect(self.play_word_audio)
        audio_row.addWidget(self.audio_btn)
        self.audio_marker = QLabel("")
        self.audio_marker.setToolTip("👤 inson ovozi, 🤖 sintez qilingan ovoz")
        audio_row.addWidget(self.audio_marker)
        audio_row.addStretch()
        card_layout.addLayout(audio_row)
        self._set_audio_visible(False)

        self.translation_label = QLabel("")
        self.translation_label.setFont(QFont(theme.FONT_FAMILY, 18))
        self.translation_label.setStyleSheet(f"color: {theme.ACCENT};")
        self.translation_label.setAlignment(Qt.AlignCenter)
        self.translation_label.setWordWrap(True)
        card_layout.addWidget(self.translation_label)

        self.example_label = QLabel("")
        self.example_label.setFont(QFont(theme.FONT_FAMILY, 12))
        self.example_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; margin-top: 8px;")
        self.example_label.setAlignment(Qt.AlignCenter)
        self.example_label.setWordWrap(True)
        card_layout.addWidget(self.example_label)

        card_layout.addStretch()
        layout.addWidget(self.card_frame)

        # Yozish rejimi (typing_mode=1) uchun input va natija yozuvi
        self.typing_input = QLineEdit()
        self.typing_input.setPlaceholderText("Inglizcha so'zni yozing...")
        self.typing_input.setMinimumHeight(46)
        self.typing_input.setFont(QFont(theme.FONT_FAMILY, 14))
        self.typing_input.returnPressed.connect(self.submit_typed)
        self.typing_input.setVisible(False)
        layout.addWidget(self.typing_input)

        self.typing_feedback = QLabel("")
        self.typing_feedback.setAlignment(Qt.AlignCenter)
        self.typing_feedback.setWordWrap(True)
        layout.addWidget(self.typing_feedback)

        self.show_answer_btn = QPushButton("Javobni ko'rsatish   (Space)")
        self.show_answer_btn.setMinimumHeight(50)
        self.show_answer_btn.setStyleSheet(theme.accent_button())
        self.show_answer_btn.setFocusPolicy(Qt.NoFocus)
        self.show_answer_btn.clicked.connect(self.reveal_answer)
        layout.addWidget(self.show_answer_btn)

        self.rating_row = QHBoxLayout()
        self.rating_row.setSpacing(10)
        self.rating_buttons = {}
        rating_info = [
            ("again", "Again", theme.RATING_AGAIN, "1"),
            ("hard", "Hard", theme.RATING_HARD, "2"),
            ("good", "Good", theme.RATING_GOOD, "3"),
            ("easy", "Easy", theme.RATING_EASY, "4"),
        ]
        self._rating_titles = {}
        for key, text, color, hotkey in rating_info:
            btn = QPushButton(text)
            btn.setMinimumHeight(60)
            btn.setStyleSheet(theme.rating_button(color))
            btn.setFocusPolicy(Qt.NoFocus)
            btn.clicked.connect(lambda checked=False, k=key: self.rate_current(k))
            self.rating_row.addWidget(btn)
            self.rating_buttons[key] = btn
            self._rating_titles[key] = (text, hotkey)
        layout.addLayout(self.rating_row)

        # Yozish natijasidan / qayta ko'rishdan keyin davom etish tugmasi
        self.next_btn = QPushButton("Davom etish   (Space)")
        self.next_btn.setMinimumHeight(50)
        self.next_btn.setStyleSheet(theme.accent_button())
        self.next_btn.setFocusPolicy(Qt.NoFocus)
        self.next_btn.clicked.connect(self.advance)
        self.next_btn.setVisible(False)
        layout.addWidget(self.next_btn)
        self._set_rating_buttons_visible(False)

        back_btn = QPushButton("⬅  Bosh sahifaga qaytish")
        back_btn.setStyleSheet(theme.secondary_button())
        back_btn.setFocusPolicy(Qt.NoFocus)
        back_btn.clicked.connect(lambda: main_window.show_page("home"))
        layout.addWidget(back_btn)

        outer.addStretch()

    # ------------------------------------------------------------------
    def _set_rating_buttons_visible(self, visible: bool):
        for btn in self.rating_buttons.values():
            btn.setVisible(visible)
        self.show_answer_btn.setVisible(not visible)
        self.next_btn.setVisible(False)

    def _set_rating_labels(self, intervals: dict | None):
        for key, btn in self.rating_buttons.items():
            title, hotkey = self._rating_titles[key]
            when = intervals[key] if intervals else ""
            btn.setText(f"{hotkey} · {title}\n{when}" if when else f"{hotkey} · {title}")

    # ------------------------------------------------------------------
    # Sessiyani boshlash
    # ------------------------------------------------------------------
    def start_session(self, words: list, category: str | None = None,
                      pack: int | None = None, ai_after: bool = True):
        """`words` - ko'rsatiladigan so'zlar (qatorlar yoki id'lar).
        `category`/`pack` - doira (tugagach A rejimi va 'unutilganlar' uchun).
        `ai_after=False` - 'unutilganlarni takrorlash' sessiyasi (AI keyin boshlanmaydi)."""
        ids = [w["id"] if not isinstance(w, int) else w for w in words]
        # Imlo tekshiruvidan o'tmagan ('unchecked'/'suspect') so'zlar qatnashmaydi
        studyable = [r["id"] for r in db.get_words_by_ids(ids) if db.is_studyable(r)]
        skipped = len(set(ids)) - len(studyable)
        self.queue = studyable
        self.retry_queue = []
        self.in_retry = False
        self._typed_grade = None
        self.seen_ids = []
        self.struggled_ids = set()
        self.category = category
        self.pack = pack
        self.ai_after = ai_after
        self.current_id = None
        self.current_row = None
        self.initial_ids = set(self.queue)    # hammasi bir marta ko'rilgach AI savollari fonda tayyorlanadi
        self._prefetched = False
        self.main_window.pages["ai_practice"].discard_prefetch()

        if category is None:
            scope = "Barcha bo'limlar"
        elif pack is None:
            scope = f"Bo'lim: {category}"
        else:
            scope = f"Bo'lim: {category} · {pack}-bo'limcha"
        if skipped > 0:
            scope += f"  ·  ⚠ {skipped} ta so'z imlo tekshiruvini kutmoqda"
        self.scope_label.setText(scope + ("" if ai_after else "  ·  🔁 unutilganlarni takrorlash"))
        self.show_current_card()
        self._focus_default()

    def refresh(self):
        pass

    def showEvent(self, event):
        super().showEvent(event)
        self._focus_default()

    def _focus_default(self):
        """Yozish kartasida fokus input'da, aks holda sahifada (klaviatura uchun)."""
        if self.typing_active and not self.answer_shown:
            self.typing_input.setFocus()
        else:
            self.setFocus()

    # ------------------------------------------------------------------
    def _peek_row(self):
        """Navbat boshidagi mavjud so'zning qatorini qaytaradi (bazadan o'chirilgan
        so'zlarni navbatdan tashlab ketadi). Navbat bo'sh bo'lsa None."""
        while self.queue:
            row = db.get_word(self.queue[0])
            if row is not None:
                return row
            self.queue.pop(0)
        return None

    def show_current_card(self):
        """Keyingi kartani ko'rsatadi. Asosiy navbat tugagach Again/Hard olganlar
        (retry_queue) bir marta qayta ko'rsatiladi, so'ng sessiya tugaydi."""
        self._auto_timer.stop()
        row = self._peek_row()
        if row is None and not self.in_retry and self.retry_queue:
            self.in_retry = True
            self.queue = list(self.retry_queue)
            self.retry_queue = []
            row = self._peek_row()
        if row is None:
            self.finish_session()
            return

        self.current_id = self.queue.pop(0)
        self.current_row = row
        left = len(self.queue) + 1
        self.progress_label.setText(
            f"Qaytadan ko'rish: {left}" if self.in_retry else f"Qoldi: {left}")
        self.translation_label.setText("")
        self.example_label.setText("")
        self.typing_feedback.setText("")
        self.answer_shown = False
        self._typed_grade = None
        self.typing_active = (not self.in_retry) and typing_mode_enabled()
        self._set_rating_buttons_visible(False)
        if self.typing_active:
            # Yozish rejimi: o'zbekcha ma'no ko'rsatiladi, foydalanuvchi inglizchasini yozadi
            self.word_label.setText(row["translation"])
            self.typing_input.clear()
            self.typing_input.setEnabled(True)
            self.typing_input.setVisible(True)
            self.show_answer_btn.setVisible(False)
            self.typing_input.setFocus()
            self._set_audio_visible(False)   # inglizcha so'z hali yashirin
        else:
            self.word_label.setText(row["word"])
            self.typing_input.setVisible(False)
            self._refresh_audio_marker()
            self._set_audio_visible(True)   # 🔊 qo'lda bosish mumkin
            if audio_service.is_auto_play():
                self.play_word_audio()      # oddiy rejim: so'z ko'rinishi bilan talaffuz chalinadi
        self._shown_at = time.monotonic()

    def reveal_answer(self):
        if self.current_row is None or self.answer_shown or self.typing_active:
            return
        row = self.current_row
        self.translation_label.setText(row["translation"])
        if row["example"]:
            self.example_label.setText(f"„{row['example']}”")
        self.answer_shown = True   # talaffuz karta ochilganda chalingan (show_current_card)
        if self.in_retry:
            # Qayta ko'rish: baho yo'q, FSRS yangilanmaydi - faqat "Keyingi"
            self.show_answer_btn.setVisible(False)
            self.next_btn.setText("Keyingi   (Space)")
            self.next_btn.setVisible(True)
            return
        self._set_rating_labels(db.preview_intervals(row))
        self._set_rating_buttons_visible(True)

    def submit_typed(self):
        """Enter bosilganda: yozilgan javobni baholaydi, to'g'ri javobni ko'rsatadi.
        Baho hali bazaga yozilmaydi - foydalanuvchi "Davom etish" bosadi yoki
        (to'g'ri bo'lsa) Easy ni tanlaydi. Maydon bo'sh bo'lsa ogohlantiradi."""
        if not self.typing_active or self.answer_shown or self.current_row is None:
            return
        row = self.current_row
        typed = self.typing_input.text()
        grade = grade_typed_answer(typed, row["word"])
        if grade is None:
            self.typing_feedback.setStyleSheet(f"color: {theme.WARNING};")
            self.typing_feedback.setText("Avval so'zni yozing, so'ng Enter bosing.")
            self.typing_input.setFocus()
            return

        self._typed_grade = grade
        self.typing_input.setEnabled(False)
        self.translation_label.setText(row["word"])
        if row["example"]:
            self.example_label.setText(f"„{row['example']}”")
        self.answer_shown = True
        self._refresh_audio_marker()
        self._set_audio_visible(True)
        if audio_service.is_auto_play():
            self.play_word_audio()

        titles = {
            "good": (theme.SUCCESS, "✅ To'g'ri  →  Good"),
            "hard": (theme.WARNING, f"≈ Deyarli to'g'ri (1 harf xato: «{typed.strip()}»)  →  Hard"),
            "again": (theme.DANGER, f"❌ Xato («{typed.strip()}»)  →  Again"),
        }
        color, text = titles[grade]
        self.typing_feedback.setStyleSheet(f"color: {color}; font-weight: bold;")
        self.typing_feedback.setText(text)

        # Faqat to'g'ri javobdan keyin Easy ni qo'lda tanlash mumkin
        self._set_rating_labels(db.preview_intervals(row))
        self.show_answer_btn.setVisible(False)
        for key, btn in self.rating_buttons.items():
            btn.setVisible(key == "easy" and grade == "good")
        self.next_btn.setText("Davom etish   (Space)")
        self.next_btn.setVisible(True)
        self._last_submit = time.monotonic()
        self._auto_timer.start(TYPED_AUTO_ADVANCE_MS)
        self.setFocus()

    def _on_auto_advance(self):
        """Taymer tugadi: javob hali shu kartada ko'rsatilayotgan bo'lsa, keyingisiga o'tadi."""
        if self.current_id is None or not self.answer_shown or self._typed_grade is None:
            return
        self.advance()

    def advance(self):
        """"Davom etish"/"Keyingi": yozish natijasidagi avtomatik bahoni yozadi
        yoki qayta ko'rishda keyingi kartaga o'tadi."""
        self._auto_timer.stop()
        if self.current_id is None or not self.answer_shown:
            return
        if self.in_retry:
            self.show_current_card()
        elif self._typed_grade is not None:
            self._commit_rating(self._typed_grade)

    def rate_current(self, rating: str):
        """Foydalanuvchi bahoni tanlaganda (tugma yoki 1-4). Qayta ko'rishda baho
        yo'q; yozish natijasidan keyin faqat (to'g'ri bo'lsa) Easy qabul qilinadi."""
        if self.current_id is None or not self.answer_shown or self.in_retry:
            return
        if self._typed_grade is not None and not (
                rating == "easy" and self._typed_grade == "good"):
            return
        self._auto_timer.stop()
        self._commit_rating(rating)

    def _commit_rating(self, rating: str):
        """Bahoni FSRS ga yozadi (har kartada FAQAT BIR MARTA - birinchi ko'rsatilganda).
        Again/Hard bo'lsa karta sessiya oxiriga (retry_queue) qo'shiladi."""
        wid = self.current_id
        duration_ms = int((time.monotonic() - self._shown_at) * 1000)
        db.review_word(wid, rating, source="flashcard", duration_ms=duration_ms)

        if wid not in self.seen_ids:
            self.seen_ids.append(wid)
        if rating in ("again", "hard"):
            self.struggled_ids.add(wid)
            if wid not in self.retry_queue:
                self.retry_queue.append(wid)

        # Barcha so'zlar kamida bir marta ko'rildi -> AI savollarini fonda oldindan
        # tayyorlaymiz (kartalar tugaguncha tayyor bo'ladi, kutish qolmaydi).
        if (self.ai_after and not self._prefetched and self.initial_ids
                and self.initial_ids <= set(self.seen_ids)):
            self._prefetched = True
            self.main_window.pages["ai_practice"].prefetch_after_cards(
                list(self.seen_ids), set(self.struggled_ids), self.category, self.pack)
        self.show_current_card()

    # ------------------------------------------------------------------
    # Audio (CLAUDE-3)
    def _set_audio_visible(self, visible: bool):
        """🔊 tugmasi va 👤/🤖 belgisini ko'rsatadi/yashiradi."""
        self.audio_btn.setVisible(visible)
        self.audio_marker.setVisible(visible)

    def _refresh_audio_marker(self):
        """Joriy so'zning audio manbasiga qarab 👤/🤖 belgisini yangilaydi."""
        if self.current_row is None:
            self.audio_marker.setText("")
            return
        source, _ = audio_service.get_word_audio(self.current_row["id"])
        self.audio_marker.setText(audio_service.audio_marker(source))

    def play_word_audio(self):
        """Joriy so'zni o'ynatadi. Fayl tayyor bo'lsa darhol; aks holda audio zanjiri
        fon oqimida ishlaydi va tayyor bo'lgach o'ynaydi (interfeys qotmaydi)."""
        row = self.current_row
        if row is None:
            return
        _, path = audio_service.get_word_audio(row["id"])
        if path and Path(path).is_file():
            self.audio_player.play_file(path)
            return
        worker = audio_service.AudioWorker(row["id"], self)
        worker.audio_ready.connect(self._on_audio_ready)
        worker.finished.connect(lambda w=worker: self._audio_workers.remove(w)
                                if w in self._audio_workers else None)
        self._audio_workers.append(worker)
        worker.start()

    def _on_audio_ready(self, word_id: int, result):
        """Audio tayyor bo'lganda: karta hali o'sha so'zniki bo'lsa belgini yangilab o'ynaydi.
        Foydalanuvchi boshqa kartaga o'tib ketgan bo'lsa, eski so'z chalinmaydi."""
        if self.current_row is None or self.current_row["id"] != word_id:
            return
        if self.audio_btn.isHidden():
            return   # yozish rejimida so'z hali yashirin
        self._refresh_audio_marker()
        self.audio_player.play_result(self.current_row["word"], result)

    # ------------------------------------------------------------------
    def keyPressEvent(self, event):
        key = event.key()
        if key in (Qt.Key_Return, Qt.Key_Enter) and \
                time.monotonic() - self._last_submit < ENTER_GUARD_SECONDS:
            event.accept()   # shu Enter javobni yubordi; keyingi kartaga o'tkazmasin
            return
        if key in (Qt.Key_Space, Qt.Key_Return, Qt.Key_Enter):
            if self.in_retry or self._typed_grade is not None:
                # qayta ko'rish / yozish natijasi: avval javob, so'ng davom etish
                if self.answer_shown:
                    self.advance()
                else:
                    self.reveal_answer()
            elif not self.answer_shown and not self.typing_active:
                self.reveal_answer()
            event.accept()
            return
        if key in RATING_KEYS and self.answer_shown and not self.in_retry:
            self.rate_current(RATING_KEYS[key])
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    def finish_session(self):
        self._auto_timer.stop()
        self.current_id = None
        self.current_row = None
        self.answer_shown = False
        self.progress_label.setText("Kartalar tugadi! 🎉")
        self.word_label.setText("")
        self.translation_label.setText("Yaxshi ishladingiz!")
        self.example_label.setText("")
        self.typing_feedback.setText("")
        self.typing_input.setVisible(False)
        self.typing_active = False
        self.in_retry = False
        self._typed_grade = None
        self._set_rating_buttons_visible(False)
        self.show_answer_btn.setVisible(False)
        self._set_audio_visible(False)

        mw = self.main_window
        if not self.ai_after:
            # 'Unutilganlarni takrorlash' tugadi - qo'shimcha oqim yo'q
            mw.show_page("home")
            return

        if self.seen_ids:
            # A rejimi: shu mashqda ko'rilgan so'zlar bo'yicha AI savollari
            mw.pages["ai_practice"].start_after_cards(
                self.seen_ids, self.struggled_ids, self.category, self.pack)
        else:
            # Hech narsa ko'rilmadi - to'g'ridan-to'g'ri 'unutilganlar' taklifi
            mw.offer_forgotten_review(self.category, self.pack)
