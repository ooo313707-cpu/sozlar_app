"""
main_window.py
--------------
Ilovaning asosiy oynasi. Chap tomonda doimiy navigatsiya paneli
(sidebar), o'ng tomonda QStackedWidget orqali almashadigan sahifalar.

v1.4 (Anki uslubi): yon panelda salomlashuv + punktirli "+ Yangi so'z" tugmasi;
bosh sahifada bo'limlar daraxti (rangli 🔵🟢🔴 sonlar), "Bugungi statistika"
kartasi va faollik xaritasi (heatmap). Bo'lim yoki bo'limcha tanlanib,
"📚 Kartalar" yoki "🤖 AI mashq" bosiladi.
"""

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QStackedWidget, QFrame, QMessageBox, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QStyledItemDelegate, QStyle, QStyleOptionViewItem, QApplication,
)
from PySide6.QtCore import Qt, QSize, QRectF, QTimer
from PySide6.QtGui import QFont, QPainter, QColor

from .. import database as db
from . import theme
from .widgets import CenteredColumn, wrap_scrollable
from .add_word import AddWordPage
from .flashcard import FlashcardPage
from .word_list import WordListPage
from .settings import SettingsPage
from .ai_practice import AIPracticePage
from .stats import StatsPage
from .heatmap import ActivityCard

SCOPE_ROLE = Qt.UserRole          # item.data(0, SCOPE_ROLE) = (bo'lim, bo'limcha | None)
COUNTS_ROLE = Qt.UserRole         # item.data(1, COUNTS_ROLE) = {"blue":.., "green":.., "red":..}
APP_VERSION = "v1.4"


def counts_text(d: dict) -> str:
    """'🔵 5   🟢 8   🔴 7' ko'rinishidagi matn (d: blue/green/red kalitlari)."""
    return f"🔵 {d['blue']}   🟢 {d['green']}   🔴 {d['red']}"


def stars_text(retention) -> str:
    """Eslab qolish foizi -> 5 ta yulduz (None bo'lsa '—')."""
    if retention is None:
        return "—"
    n = max(0, min(5, round(retention / 20)))
    return "★" * n + "☆" * (5 - n)


class CountsDelegate(QStyledItemDelegate):
    """Daraxtning 2-ustunida 3 ta rangli nuqta + son chizadi (Anki'dagi kabi).
    Matn (item.text) saqlanib qoladi, lekin ekranda faqat shu chizma ko'rinadi."""

    SEG_W = 50

    def sizeHint(self, option, index):
        return QSize(self.SEG_W * 3 + 12, 32)

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)

        data = index.data(COUNTS_ROLE)
        if not isinstance(data, dict):
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        x = opt.rect.right() - self.SEG_W * 3 - 4
        for key, color in (("blue", theme.COUNT_BLUE), ("green", theme.COUNT_GREEN),
                           ("red", theme.COUNT_RED)):
            n = data.get(key, 0)
            c = QColor(color)
            painter.setBrush(c)
            painter.setPen(Qt.NoPen)
            cy = opt.rect.center().y()
            painter.drawEllipse(QRectF(x + 4, cy - 4, 8, 8))
            painter.setPen(c if n else QColor(theme.TEXT_MUTED))
            painter.drawText(QRectF(x + 16, opt.rect.y(), self.SEG_W - 18, opt.rect.height()),
                             Qt.AlignLeft | Qt.AlignVCenter, str(n))
            x += self.SEG_W
        painter.restore()


class StatTile(QFrame):
    """'Bugungi statistika'dagi bitta ko'rsatkich: katta qiymat + izoh."""

    def __init__(self, title: str):
        super().__init__()
        self.setStyleSheet(
            f"QFrame {{ background: {theme.BG_INPUT}; border: none; border-radius: 10px; }}"
            f"QLabel {{ background: transparent; border: none; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        self.value_label = QLabel("0")
        self.value_label.setFont(QFont(theme.FONT_FAMILY, 18, QFont.Bold))
        lay.addWidget(self.value_label)
        self.title_label = QLabel(title)
        self.title_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        lay.addWidget(self.title_label)
        self.extra_label = QLabel("")
        lay.addWidget(self.extra_label)

    def set_value(self, value: str, extra: str = "", color: str | None = None,
                  extra_color: str | None = None):
        self.value_label.setText(value)
        self.value_label.setStyleSheet(f"color: {color or theme.TEXT_PRIMARY};")
        self.extra_label.setText(extra)
        self.extra_label.setStyleSheet(
            f"color: {extra_color or theme.TEXT_MUTED}; font-size: 12px;")


class TodayStatsCard(QFrame):
    """'Bugungi statistika': ko'rilgan kartalar, vaqt, tezlik, eslab qolish (% + yulduz)."""

    def __init__(self):
        super().__init__()
        self.setStyleSheet(theme.card_frame_style(radius=14))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(10)
        title = QLabel("Bugungi statistika")
        title.setFont(QFont(theme.FONT_FAMILY, 13, QFont.Bold))
        lay.addWidget(title)

        row = QHBoxLayout()
        row.setSpacing(10)
        self.studied = StatTile("Ko'rilgan kartalar")
        self.time = StatTile("Vaqt")
        self.pace = StatTile("Tezlik")
        self.retention = StatTile("Eslab qolish")
        for t in (self.studied, self.time, self.pace, self.retention):
            row.addWidget(t, stretch=1)
        lay.addLayout(row)

    def refresh(self):
        s = db.get_today_stats()
        self.studied.set_value(str(s["studied"]))
        self.time.set_value(f"{s['minutes']:g} daq")
        self.pace.set_value(f"{s['pace']:g} s", "soniya / karta" if s["pace"] else "")
        r = s["retention"]
        self.retention.set_value("—" if r is None else f"{r}%", stars_text(r),
                                 theme.retention_color(r) if r is not None else None,
                                 extra_color=theme.WARNING)


class HomePage(QWidget):
    """Bosh sahifa: bo'limlar daraxti + 'Kartalar' / 'AI mashq' + bugungi statistika + faollik."""

    def __init__(self, main_window: "MainWindow"):
        super().__init__()
        self.main_window = main_window

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        content = QWidget()
        content_lay = QVBoxLayout(content)
        content_lay.setContentsMargins(40, 30, 40, 24)
        column = CenteredColumn(max_width=980)
        content_lay.addWidget(column)
        content_lay.addStretch()
        layout = column.layout_for_content()
        layout.setSpacing(14)
        outer.addWidget(wrap_scrollable(content))

        self.greeting_label = QLabel("Xush kelibsiz 👋")
        self.greeting_label.setFont(QFont(theme.FONT_FAMILY, 24, QFont.Bold))
        layout.addWidget(self.greeting_label)

        self.subtitle_label = QLabel("Bo'lim yoki bo'limchani tanlang va mashqni boshlang.")
        self.subtitle_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 14px;")
        layout.addWidget(self.subtitle_label)

        legend = QLabel("🔵 yangi (hali ko'rilmagan)    🟢 yaxshi eslab qolingan    🔴 unutilgan")
        legend.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(legend)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setMinimumHeight(200)
        self.tree.setItemDelegateForColumn(1, CountsDelegate(self.tree))
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.currentItemChanged.connect(self._on_selection_changed)
        self.tree.itemDoubleClicked.connect(lambda *_: self.start_cards())
        layout.addWidget(self.tree, stretch=1)

        self.empty_label = QLabel("Hali so'z yo'q. «➕ Yangi so'z» orqali birinchi so'zni qo'shing.")
        self.empty_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
        layout.addWidget(self.empty_label)

        self.selection_label = QLabel("")
        self.selection_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 13px;")
        layout.addWidget(self.selection_label)

        row = QHBoxLayout()
        row.setSpacing(12)
        self.cards_btn = QPushButton("📚  Kartalar")
        self.cards_btn.setMinimumHeight(54)
        self.cards_btn.setStyleSheet(theme.accent_button())
        self.cards_btn.clicked.connect(self.start_cards)
        row.addWidget(self.cards_btn)

        self.ai_btn = QPushButton("🤖  AI mashq")
        self.ai_btn.setMinimumHeight(54)
        self.ai_btn.setStyleSheet(theme.accent_button())
        self.ai_btn.clicked.connect(self.start_ai)
        row.addWidget(self.ai_btn)
        layout.addLayout(row)

        free_btn = QPushButton("💬  AI mashq: hamma o'rganilgan so'zlar (B rejimi)")
        free_btn.setMinimumHeight(44)
        free_btn.setStyleSheet(theme.secondary_button())
        free_btn.clicked.connect(self.start_free_chat)
        layout.addWidget(free_btn)

        self.today_card = TodayStatsCard()
        layout.addWidget(self.today_card)
        self.activity_card = ActivityCard()
        layout.addWidget(self.activity_card)

        self._set_buttons_enabled(False)

    # ------------------------------------------------------------------
    def refresh(self):
        name = db.get_user_name()
        self.greeting_label.setText(f"Xush kelibsiz, {name} 👋" if name else "Xush kelibsiz 👋")
        stats = db.get_stats()
        total = stats.get("jami", 0)
        if total:
            blue = stats.get("Yangi", 0)
            green = stats.get("O'rganilmoqda", 0) + stats.get("Yaxshi o'rganilgan", 0)
            red = stats.get("O'rtacha unutilgan", 0) + stats.get("Unutilgan", 0)
            self.subtitle_label.setText(
                f"Jami {total} ta so'z  ·  🔵 {blue}   🟢 {green}   🔴 {red}")
        else:
            self.subtitle_label.setText("Bo'lim yoki bo'limchani tanlang va mashqni boshlang.")
        self.today_card.refresh()
        self.activity_card.refresh()
        self.reload_tree()

    def _make_item(self, label: str, counts: dict, scope, tip: str, top: bool):
        item = QTreeWidgetItem([label, counts_text(counts)])
        item.setData(0, SCOPE_ROLE, scope)
        item.setData(1, COUNTS_ROLE, {k: counts[k] for k in ("blue", "green", "red")})
        item.setToolTip(0, tip)
        item.setTextAlignment(1, Qt.AlignRight | Qt.AlignVCenter)
        if top:
            f = item.font(0)
            f.setBold(True)
            item.setFont(0, f)
        return item

    def reload_tree(self):
        """Bo'limlar daraxtini qayta quradi (tanlov va ochiq bo'limlar saqlanadi)."""
        expanded, selected = set(), None
        cur = self.tree.currentItem()
        if cur is not None:
            selected = cur.data(0, SCOPE_ROLE)
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            if top.isExpanded():
                expanded.add(top.data(0, SCOPE_ROLE)[0])

        self.tree.blockSignals(True)
        self.tree.clear()
        sections = db.get_sections()
        restore = None
        for sec in sections:
            top = self._make_item(
                f"📁  {sec['category']}", sec, (sec["category"], None),
                f"{sec['total']} ta so'z, {len(sec['packs'])} ta bo'limcha", top=True)
            self.tree.addTopLevelItem(top)
            if selected == (sec["category"], None):
                restore = top
            for p in sec["packs"]:
                child = self._make_item(
                    f"{p['pack']}-bo'limcha", p, (sec["category"], p["pack"]),
                    f"{p['total']} ta so'z", top=False)
                top.addChild(child)
                if selected == (sec["category"], p["pack"]):
                    restore = child
            # Faqat bitta bo'lim bo'lsa - ochiq ko'rsatamiz; aks holda oldingi holat
            if sec["category"] in expanded or len(sections) == 1:
                top.setExpanded(True)
        self.tree.blockSignals(False)

        has = bool(sections)
        self.tree.setVisible(has)
        self.empty_label.setVisible(not has)
        if restore is not None:
            self.tree.setCurrentItem(restore)
        else:
            self.tree.setCurrentItem(None)
        self._on_selection_changed(self.tree.currentItem(), None)

    # ------------------------------------------------------------------
    def current_scope(self):
        """Tanlangan (bo'lim, bo'limcha | None) yoki None."""
        item = self.tree.currentItem()
        return item.data(0, SCOPE_ROLE) if item is not None else None

    def _set_buttons_enabled(self, enabled: bool):
        self.cards_btn.setEnabled(enabled)
        self.ai_btn.setEnabled(enabled)

    def _on_selection_changed(self, current, _previous):
        scope = current.data(0, SCOPE_ROLE) if current is not None else None
        if scope is None:
            self.selection_label.setText("Bo'lim yoki bo'limchani tanlang.")
            self.cards_btn.setText("📚  Kartalar")
            self._set_buttons_enabled(False)
            return
        category, pack = scope
        name = category if pack is None else f"{category} · {pack}-bo'limcha"
        due = len(db.get_study_words(category, pack))
        self.selection_label.setText(f"Tanlandi: {name}")
        self.cards_btn.setText(f"📚  Kartalar  ·  {due} ta" if due else "📚  Kartalar")
        self._set_buttons_enabled(True)

    # ------------------------------------------------------------------
    def start_cards(self):
        scope = self.current_scope()
        if scope is None:
            return
        category, pack = scope
        words = db.get_study_words(category, pack)
        if not words:
            QMessageBox.information(
                self, "Ajoyib!",
                "Bu doirada hozircha ko'rib chiqadigan karta yo'q "
                "(muddati kelgan yoki bugungi yangi limit ichidagi so'z qolmadi)."
            )
            self.main_window.offer_forgotten_review(category, pack)
            return
        self.main_window.pages["flashcard"].start_session(words, category, pack)
        self.main_window.show_page("flashcard")

    def start_ai(self):
        scope = self.current_scope()
        if scope is None:
            return
        category, pack = scope
        ai = self.main_window.pages["ai_practice"]
        started = ai.start_for_section(category) if pack is None else ai.start_for_pack(category, pack)
        if started:
            self.main_window.show_page("ai_practice")

    def start_free_chat(self):
        ai = self.main_window.pages["ai_practice"]
        if ai.start_free_chat_mode():
            self.main_window.show_page("ai_practice")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Ingliz So'zlarini Yodlash")
        self.resize(1120, 720)
        self.setMinimumSize(860, 560)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        root.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        root.addWidget(self.stack, stretch=1)

        self.pages = {}
        self.pages["home"] = HomePage(self)
        self.pages["add_word"] = AddWordPage(self)
        self.pages["flashcard"] = FlashcardPage(self)
        self.pages["word_list"] = WordListPage(self)
        self.pages["stats"] = StatsPage(self)
        self.pages["settings"] = SettingsPage(self)
        self.pages["ai_practice"] = AIPracticePage(self)

        for page in self.pages.values():
            self.stack.addWidget(page)

        self.show_page("home")

        # CLAUDE-3: avvalgi importdan qolgan imlo/audio vazifalari bo'lsa, ilova
        # to'liq ochilgach (3 soniyadan keyin) fonda davom ettiriladi.
        QTimer.singleShot(3000, self.pages["settings"].resume_import_queue)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setFixedWidth(232)
        sidebar.setStyleSheet(f"QFrame {{ background: {theme.BG_SIDEBAR}; border: none; }}")

        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(18, 26, 18, 20)
        layout.setSpacing(6)

        self.welcome_label = QLabel("XUSH KELIBSIZ")
        self.welcome_label.setStyleSheet(
            f"color: {theme.TEXT_MUTED}; font-size: 11px; letter-spacing: 2px;")
        layout.addWidget(self.welcome_label)

        self.user_label = QLabel("")
        self.user_label.setFont(QFont(theme.FONT_FAMILY, 15, QFont.Bold))
        self.user_label.setWordWrap(True)
        self.user_label.setStyleSheet("padding-bottom: 10px;")
        layout.addWidget(self.user_label)

        self.add_btn = QPushButton("+  Yangi so'z")
        self.add_btn.setMinimumHeight(46)
        self.add_btn.setCursor(Qt.PointingHandCursor)
        self.add_btn.setStyleSheet(f"""
            QPushButton {{
                background: transparent; color: {theme.ACCENT_HOVER};
                border: 2px dashed {theme.ACCENT}; border-radius: 12px;
                font-weight: 600; font-size: 14px;
            }}
            QPushButton:hover {{ background: {theme.ACCENT_SOFT}; }}
        """)
        self.add_btn.clicked.connect(lambda: self.show_page("add_word"))
        layout.addWidget(self.add_btn)
        layout.addSpacing(8)

        self.nav_buttons = {}
        for key, label in theme.NAV_ITEMS:
            btn = QPushButton(label)
            btn.setMinimumHeight(42)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(lambda checked=False, k=key: self.show_page(k))
            layout.addWidget(btn)
            self.nav_buttons[key] = btn

        layout.addStretch()

        version_label = QLabel(f"{APP_VERSION} · FSRS + Gemini")
        version_label.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(version_label)

        return sidebar

    def refresh_user_name(self):
        name = db.get_user_name()
        self.user_label.setText(name if name else "📚 So'z Yodlash")

    def _style_nav_buttons(self, active_key: str):
        for key, btn in self.nav_buttons.items():
            if key == active_key:
                btn.setStyleSheet(f"""
                    QPushButton {{
                        background: {theme.ACCENT_SOFT};
                        color: {theme.ACCENT_HOVER};
                        border: none;
                        border-radius: 10px;
                        padding: 10px 14px;
                        text-align: left;
                        font-weight: 600;
                    }}
                """)
            else:
                btn.setStyleSheet(theme.secondary_button())

    def show_page(self, name: str):
        page = self.pages[name]
        self.refresh_user_name()
        if hasattr(page, "refresh"):
            page.refresh()
        self.stack.setCurrentWidget(page)
        self._style_nav_buttons(name if name in self.nav_buttons else "")

    # ------------------------------------------------------------------
    def offer_forgotten_review(self, category=None, pack=None):
        """Kartalar (va A rejimi) tugagach: 'Unutilgan so'zlarni takrorlamoqchimisiz?'
        Unutilgan so'z bo'lmasa - 'Unutilgan so'z yo'q' xabari. 'Ha' bo'lsa shu
        bo'lim/bo'limchadagi 🔴 so'zlar kartalar bilan takrorlanadi."""
        words = db.get_forgotten_words(category, pack)
        if not words:
            QMessageBox.information(self, "Unutilgan so'z yo'q",
                                    "Unutilgan so'z yo'q 🎉 Barakalla!")
            self.show_page("home")
            return
        reply = QMessageBox.question(
            self, "Takrorlash",
            f"Unutilgan so'zlarni takrorlamoqchimisiz? ({len(words)} ta so'z)",
        )
        if reply == QMessageBox.Yes:
            self.pages["flashcard"].start_session(words, category, pack, ai_after=False)
            self.show_page("flashcard")
        else:
            self.show_page("home")
