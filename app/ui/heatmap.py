"""
heatmap.py
----------
Anki uslubidagi faollik xaritasi: har kun bitta katakcha, rangi o'sha kuni
qancha baholash qilinganiga bog'liq. Yil / Oy / Hafta ko'rinishlari bor.
QPainter bilan chiziladi (qo'shimcha kutubxona kerak emas).
"""

from datetime import timedelta

from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QToolTip,
)
from PySide6.QtGui import QPainter, QColor, QFont
from PySide6.QtCore import Qt, QRectF

from .. import database as db
from . import theme

WEEKDAY_SHORT = ["Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"]       # dushanba boshlanadi
MONTH_SHORT = ["Yan", "Fev", "Mar", "Apr", "May", "Iyn",
               "Iyl", "Avg", "Sen", "Okt", "Noy", "Dek"]

# Ko'rinish -> necha hafta ustuni (7 kunlik) ko'rsatiladi
MODE_WEEKS = {"year": 53, "month": 5, "week": 1}
MODE_TITLES = {"year": "Yil", "month": "Oy", "week": "Hafta"}

GAP = 3
LABEL_W = 22          # chap tomondagi hafta kunlari yozuvi kengligi
HEADER_H = 16         # tepadagi oy nomlari qatori balandligi
MAX_CELL = 20
MIN_CELL = 8


def blend(c1: str, c2: str, t: float) -> QColor:
    """c1 dan c2 ga t (0..1) nisbatda aralashgan rang."""
    a, b = QColor(c1), QColor(c2)
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
    )


def level_of(count: int, peak: int) -> int:
    """0..4 daraja: 0 - faollik yo'q; 4 - eng faol kunlar."""
    if count <= 0:
        return 0
    peak = max(peak, 1)
    ratio = count / peak
    if ratio <= 0.25:
        return 1
    if ratio <= 0.5:
        return 2
    if ratio <= 0.75:
        return 3
    return 4


class HeatmapWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.counts: dict = {}
        self.mode = "year"
        self.setMouseTracking(True)
        self.setFixedHeight(HEADER_H + 7 * (MAX_CELL + GAP) + 4)

    # ------------------------------------------------------------------
    def set_data(self, counts: dict):
        self.counts = counts
        self.update()

    def set_mode(self, mode: str):
        if mode in MODE_WEEKS:
            self.mode = mode
            self.update()

    # ------------------------------------------------------------------
    def _grid(self):
        """Ko'rinadigan katakchalar: [(col, row, sana)], ustunlar soni.
        Ustun = hafta (dushanbadan boshlanadi), qator = hafta kuni."""
        today = db.today_study_day()
        weeks = MODE_WEEKS[self.mode]
        monday_this_week = today - timedelta(days=today.weekday())
        first_monday = monday_this_week - timedelta(weeks=weeks - 1)
        cells = []
        for col in range(weeks):
            for row in range(7):
                d = first_monday + timedelta(weeks=col, days=row)
                if d <= today:
                    cells.append((col, row, d))
        return cells, weeks

    def _cell_size(self, weeks: int) -> float:
        avail = max(self.width() - LABEL_W - 4, 10)
        return max(MIN_CELL, min(MAX_CELL, avail / weeks - GAP))

    def resizeEvent(self, event):
        # Balandlik katakcha o'lchamiga moslanadi (ortiqcha bo'sh joy qolmasin)
        _cells, weeks = self._grid()
        size = self._cell_size(weeks)
        self.setFixedHeight(int(HEADER_H + 7 * (size + GAP) + 2))
        super().resizeEvent(event)

    def _cell_rect(self, col: int, row: int, size: float) -> QRectF:
        x = LABEL_W + col * (size + GAP)
        y = HEADER_H + row * (size + GAP)
        return QRectF(x, y, size, size)

    # ------------------------------------------------------------------
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        cells, weeks = self._grid()
        size = self._cell_size(weeks)
        peak = max(self.counts.values(), default=0)

        palette = [QColor(theme.BG_INPUT)] + [
            blend(theme.BG_INPUT, theme.ACCENT, t) for t in (0.35, 0.55, 0.78, 1.0)
        ]
        # Bo'sh katak chegarasi ko'rinib tursin
        border = QColor(theme.BORDER)

        font = QFont(theme.FONT_FAMILY)
        font.setPixelSize(10)
        p.setFont(font)
        p.setPen(QColor(theme.TEXT_MUTED))
        for row, name in enumerate(WEEKDAY_SHORT):
            if row % 2 == 0 or self.mode == "week":
                r = self._cell_rect(0, row, size)
                p.drawText(QRectF(0, r.y(), LABEL_W - 3, r.height()),
                           Qt.AlignRight | Qt.AlignVCenter, name)

        last_month = None
        for col, row, d in cells:
            level = level_of(self.counts.get(d, 0), peak)
            rect = self._cell_rect(col, row, size)
            p.setPen(border if level == 0 else Qt.NoPen)
            p.setBrush(palette[level])
            p.drawRoundedRect(rect, 3, 3)
            # Oy nomi: oy birinchi ko'ringan ustun tepasida (yil/oy ko'rinishida)
            if row == 0 and self.mode != "week" and d.month != last_month:
                if self.mode == "year" or last_month is None:
                    p.setPen(QColor(theme.TEXT_MUTED))
                    p.drawText(QRectF(rect.x(), 0, 40, HEADER_H - 2),
                               Qt.AlignLeft | Qt.AlignVCenter, MONTH_SHORT[d.month - 1])
                last_month = d.month
        p.end()

    def cell_at(self, x: float, y: float):
        """Sichqoncha ostidagi sana (yo'q bo'lsa None)."""
        cells, weeks = self._grid()
        size = self._cell_size(weeks)
        for col, row, d in cells:
            if self._cell_rect(col, row, size).contains(x, y):
                return d
        return None

    def mouseMoveEvent(self, event):
        pos = event.position()
        d = self.cell_at(pos.x(), pos.y())
        if d is None:
            QToolTip.hideText()
            return
        n = self.counts.get(d, 0)
        text = f"{d.strftime('%d.%m.%Y')}: " + (f"{n} ta baholash" if n else "faollik yo'q")
        QToolTip.showText(event.globalPosition().toPoint(), text, self)


class ActivityCard(QFrame):
    """Sarlavha + Yil/Oy/Hafta tugmalari + xarita + ketma-ket kunlar (streak)."""

    def __init__(self):
        super().__init__()
        self.setStyleSheet(theme.card_frame_style(radius=14))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        top = QHBoxLayout()
        title = QLabel("Faollik")
        title.setFont(QFont(theme.FONT_FAMILY, 13, QFont.Bold))
        top.addWidget(title)
        top.addStretch()

        self.mode_buttons = {}
        for key in ("year", "month", "week"):
            b = QPushButton(MODE_TITLES[key])
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda checked=False, k=key: self.set_mode(k))
            top.addWidget(b)
            self.mode_buttons[key] = b
        layout.addLayout(top)

        self.heatmap = HeatmapWidget()
        layout.addWidget(self.heatmap)

        self.streak_label = QLabel("")
        self.streak_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        layout.addWidget(self.streak_label)

        self.set_mode("year")

    def _style_mode_buttons(self, active: str):
        for key, b in self.mode_buttons.items():
            b.setChecked(key == active)
            if key == active:
                b.setStyleSheet(
                    f"QPushButton {{ background: {theme.ACCENT_SOFT}; color: {theme.ACCENT_HOVER};"
                    f" border: none; border-radius: 8px; padding: 4px 12px; font-size: 12px;"
                    f" font-weight: 600; }}")
            else:
                b.setStyleSheet(
                    f"QPushButton {{ background: transparent; color: {theme.TEXT_SECONDARY};"
                    f" border: none; border-radius: 8px; padding: 4px 12px; font-size: 12px; }}"
                    f"QPushButton:hover {{ color: {theme.TEXT_PRIMARY}; }}")

    def set_mode(self, mode: str):
        self.heatmap.set_mode(mode)
        self._style_mode_buttons(mode)

    def refresh(self):
        self.heatmap.set_data(db.get_activity_counts(371))
        self.streak_label.setText(
            f"🔥 Joriy ketma-ketlik: {db.get_current_streak()} kun    ·    "
            f"🏆 Eng uzun: {db.get_longest_streak()} kun")
