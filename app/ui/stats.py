"""
stats.py
--------
O'rganish statistikasi sahifasi: umumiy holat (necha so'z qaysi
bosqichda), necha kun ketma-ket mashq qilingani (streak), so'nggi
14 kunlik faollik va bo'limlar bo'yicha so'zlar soni - grafik
ko'rinishida.

Pastda "Batafsil statistika": kartalar holati (doira diagramma), savol turlari bo'yicha
natija, eng ko'p xato so'zlar, bo'lim/bo'limcha bo'yicha foiz va haftalik o'sish.
Bu bo'lim yuqoridagi filtr bilan bitta bo'lim (deck) yoki bo'limcha (pack) ga
toraytiriladi.
"""

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame, QLabel, QComboBox,
    QCheckBox, QProgressBar,
)
from PySide6.QtGui import QFont, QPainter, QColor
from PySide6.QtCore import Qt
from PySide6.QtCharts import (
    QChart, QChartView, QBarSeries, QBarSet, QBarCategoryAxis, QValueAxis,
    QPieSeries,
)

from .. import database as db
from .. import questions as qs
from . import theme
from .widgets import CenteredColumn, wrap_scrollable


# Doira diagrammadagi kartalar holati ranglari
STATE_COLORS = {
    "Yangi": theme.COUNT_BLUE,
    "O'rganilmoqda": theme.WARNING,
    "Unutilgan": theme.DANGER,
    "O'rtacha unutilgan": theme.RATING_HARD,
    "Yaxshi o'rganilgan": theme.SUCCESS,
    db.UNCHECKED_LABEL: theme.TEXT_MUTED,
}


def format_percent(value: float) -> str:
    """Foizni chiroyli ko'rsatadi: 76.67 -> "76,67%", 5.0 -> "5%", 0 -> "0%"."""
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",") + "%"


def _section_title(text: str) -> QLabel:
    """Bo'lim sarlavhasi (kichik, kulrang)."""
    label = QLabel(text)
    label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 13px; margin-top: 6px;")
    return label


def _percent_row(title: str, percent: int, detail: str) -> QWidget:
    """Bitta gorizontal ustun: nom | rangli ustun | "78%  (14/18)". Rang eslab qolish
    foiziga qarab (qizil -> yashil)."""
    row = QWidget()
    row.setStyleSheet("background: transparent;")
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 2, 0, 2)
    name = QLabel(title)
    name.setMinimumWidth(170)
    name.setMaximumWidth(220)
    lay.addWidget(name)
    bar = QProgressBar()
    bar.setRange(0, 100)
    bar.setValue(int(percent))
    bar.setTextVisible(False)
    bar.setFixedHeight(10)
    color = theme.retention_color(percent)
    bar.setStyleSheet(
        f"QProgressBar {{ background: {theme.BG_INPUT}; border: none; border-radius: 5px; }}"
        f"QProgressBar::chunk {{ background: {color}; border-radius: 5px; }}")
    lay.addWidget(bar, stretch=1)
    value = QLabel(f"{percent}%  ({detail})")
    value.setMinimumWidth(110)
    value.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
    lay.addWidget(value)
    return row


def _card(widgets) -> QFrame:
    """Kartochka (ramka) ichiga vidjetlar ro'yxatini joylaydi."""
    frame = QFrame()
    frame.setStyleSheet(theme.card_frame_style(radius=14))
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 12, 16, 12)
    for w in widgets:
        lay.addWidget(w)
    return frame


class SummaryCard(QFrame):
    """Statistika sahifasidagi ikonkali kichik xulosa kartochkasi."""

    def __init__(self, icon: str, value, title: str):
        super().__init__()
        self.setStyleSheet(theme.card_frame_style(radius=14))
        self.setMinimumHeight(96)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(2)

        icon_label = QLabel(icon)
        icon_label.setStyleSheet("font-size: 20px;")
        layout.addWidget(icon_label)

        value_label = QLabel(str(value))
        value_label.setFont(QFont(theme.FONT_FAMILY, 22, QFont.Bold))
        value_label.setStyleSheet(f"color: {theme.TEXT_PRIMARY};")
        layout.addWidget(value_label)

        title_label = QLabel(title)
        title_label.setStyleSheet(f"color: {theme.TEXT_SECONDARY}; font-size: 12px;")
        title_label.setWordWrap(True)
        layout.addWidget(title_label)


def _styled_chart(chart: QChart) -> QChartView:
    """Grafikni ilovaning qora-binafsha uslubiga moslaydi: shaffof fon,
    legend (izoh) yashirin - chunki bitta ustun turi bor, izohga hojat yo'q."""
    chart.setBackgroundVisible(False)
    chart.legend().setVisible(False)
    view = QChartView(chart)
    view.setRenderHint(QPainter.Antialiasing)
    view.setStyleSheet("background: transparent; border: none;")
    view.setMinimumHeight(220)
    return view


class StatsPage(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        # Butun kontent skroll qilinadigan konteyner ichida - kichik
        # oyna balandligida ham hech narsa "siqilib" ketmaydi va
        # gorizontal skroll hech qachon chiqmaydi.
        self.content = QWidget()
        content_layout = QVBoxLayout(self.content)
        content_layout.setContentsMargins(40, 36, 40, 30)

        column = CenteredColumn(max_width=900)
        content_layout.addWidget(column)
        layout = column.layout_for_content()
        layout.setSpacing(18)

        title = QLabel("Statistika")
        title.setFont(QFont(theme.FONT_FAMILY, 22, QFont.Bold))
        layout.addWidget(title)

        self.summary_grid = QGridLayout()
        self.summary_grid.setSpacing(12)
        layout.addLayout(self.summary_grid)

        activity_title = QLabel("Oxirgi 14 kunlik faollik")
        activity_title.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: 13px; margin-top: 6px;"
        )
        layout.addWidget(activity_title)

        self.activity_container = QVBoxLayout()
        layout.addLayout(self.activity_container)

        category_title = QLabel("Bo'limlar bo'yicha so'zlar")
        category_title.setStyleSheet(
            f"color: {theme.TEXT_SECONDARY}; font-size: 13px; margin-top: 6px;"
        )
        layout.addWidget(category_title)

        self.category_container = QVBoxLayout()
        layout.addLayout(self.category_container)

        detail_title = QLabel("Batafsil statistika")
        detail_title.setFont(QFont(theme.FONT_FAMILY, 16, QFont.Bold))
        detail_title.setStyleSheet("margin-top: 14px;")
        layout.addWidget(detail_title)

        # Filtr: bitta bo'lim (deck) yoki bo'limcha (pack) ni aniq ko'rish
        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Bo'lim:"))
        self.category_combo = QComboBox()
        self.category_combo.setMinimumWidth(180)
        filter_row.addWidget(self.category_combo)
        filter_row.addWidget(QLabel("Bo'limcha:"))
        self.pack_combo = QComboBox()
        self.pack_combo.setMinimumWidth(150)
        filter_row.addWidget(self.pack_combo)
        filter_row.addStretch()
        layout.addLayout(filter_row)
        # Anki'dagi "Excluded alohida" kabi: tekshirilmagan so'zlar doirada alohida bo'lak
        self.separate_check = QCheckBox("Tekshirilmaganlar alohida ko'rsatilsin")
        self.separate_check.setChecked(True)
        self.separate_check.stateChanged.connect(self._refresh_detail)
        layout.addWidget(self.separate_check)
        self.category_combo.currentIndexChanged.connect(self._on_category_changed)
        self.pack_combo.currentIndexChanged.connect(self._refresh_detail)

        self.detail_container = QVBoxLayout()
        self.detail_container.setSpacing(12)
        layout.addLayout(self.detail_container)

        layout.addStretch()
        content_layout.addStretch()

        outer.addWidget(wrap_scrollable(self.content))

    def refresh(self):
        self._clear_grid(self.summary_grid)
        self._clear_layout(self.activity_container)
        self._clear_layout(self.category_container)

        stats = db.get_stats()
        streak = db.get_current_streak()
        today_count = db.get_reviews_per_day(1)[0][1]

        cards = [
            ("📚", stats.get("jami", 0), "Jami so'zlar"),
            ("🔥", streak, "Ketma-ket kun (streak)"),
            ("✅", stats.get("Yaxshi o'rganilgan", 0), "Yaxshi o'rganilgan"),
            ("📝", today_count, "Bugun ko'rilgan"),
        ]
        for i, (icon, value, card_title) in enumerate(cards):
            self.summary_grid.addWidget(SummaryCard(icon, value, card_title), 0, i)

        self.activity_container.addWidget(self._build_activity_chart())

        category_counts = db.get_category_counts()
        if category_counts:
            self.category_container.addWidget(self._build_category_chart(category_counts))
        else:
            empty_label = QLabel("Hali so'z yo'q.")
            empty_label.setStyleSheet(f"color: {theme.TEXT_MUTED};")
            self.category_container.addWidget(empty_label)

        self._reload_filters()
        self._refresh_detail()

    def _clear_grid(self, grid: QGridLayout):
        while grid.count():
            item = grid.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)        # darhol ekrandan olib tashlanadi (deleteLater kutilmaydi)
                w.deleteLater()

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            w = item.widget()
            if w:
                w.setParent(None)        # darhol ekrandan olib tashlanadi (deleteLater kutilmaydi)
                w.deleteLater()

    # ------------------------------------------------------------------
    # Filtr (bo'lim / bo'limcha)
    # ------------------------------------------------------------------

    def _reload_filters(self):
        """Bo'lim ro'yxatini bazadan yangilaydi; avvalgi tanlov saqlanadi (bo'lim
        o'chib ketgan bo'lsa "Hammasi" ga qaytadi)."""
        keep_cat = self.category_combo.currentData()
        keep_pack = self.pack_combo.currentData()
        sections = db.get_sections()
        self._section_packs = {sec["category"]: [p["pack"] for p in sec["packs"]]
                               for sec in sections}
        self.category_combo.blockSignals(True)
        self.category_combo.clear()
        self.category_combo.addItem("Hammasi", None)
        for sec in sections:
            self.category_combo.addItem(sec["category"], sec["category"])
        idx = self.category_combo.findData(keep_cat) if keep_cat is not None else 0
        self.category_combo.setCurrentIndex(max(idx, 0))
        self.category_combo.blockSignals(False)
        self._fill_packs(keep_pack)

    def _fill_packs(self, keep_pack=None):
        """Bo'limcha ro'yxatini tanlangan bo'limga qarab to'ldiradi (bo'lim tanlanmagan
        bo'lsa - o'chirilgan)."""
        cat = self.category_combo.currentData()
        self.pack_combo.blockSignals(True)
        self.pack_combo.clear()
        self.pack_combo.addItem("Hammasi", None)
        for pk in self._section_packs.get(cat, []) if cat is not None else []:
            self.pack_combo.addItem(f"Bo'limcha {pk}", pk)
        idx = self.pack_combo.findData(keep_pack) if keep_pack is not None else 0
        self.pack_combo.setCurrentIndex(max(idx, 0))
        self.pack_combo.setEnabled(cat is not None)
        self.pack_combo.blockSignals(False)

    def _on_category_changed(self):
        """Bo'lim o'zgarsa bo'limchalar qayta to'ldiriladi va statistika yangilanadi."""
        self._fill_packs()
        self._refresh_detail()

    # ------------------------------------------------------------------
    # Batafsil statistika
    # ------------------------------------------------------------------

    def _refresh_detail(self, *_):
        """Filtrga mos batafsil statistikani qayta quradi."""
        self._clear_layout(self.detail_container)
        cat = self.category_combo.currentData()
        pack = self.pack_combo.currentData()
        add = self.detail_container.addWidget

        add(self._build_state_card(cat, pack))

        qstats = db.get_question_stats(category=cat, pack=pack)
        if not qstats["by_type"]:
            empty = QLabel("Hali AI mashq javoblari yo'q - savol statistikasi mashq qilgach chiqadi.")
            empty.setWordWrap(True)
            empty.setStyleSheet(f"color: {theme.TEXT_MUTED};")
            add(empty)
            return

        add(_section_title("Savol turlari bo'yicha natija (eng zaifi tepada)"))
        add(_card([_percent_row(qs.TYPE_TITLES.get(t["type"], t["type"]), t["percent"],
                                f"{t['correct']}/{t['total']}") for t in qstats["by_type"]]))

        add(_section_title("Eng ko'p xato qilingan so'zlar"))
        if qstats["worst_words"]:
            rows = []
            for w in qstats["worst_words"]:
                lbl = QLabel(f"{w['word']}   —   xato {w['wrong']} / {w['total']}")
                rows.append(lbl)
            add(_card(rows))
        else:
            ok = QLabel("Xato qilingan so'z yo'q.")
            ok.setStyleSheet(f"color: {theme.TEXT_MUTED};")
            add(ok)

        if cat is None:
            add(_section_title("Bo'limlar bo'yicha to'g'ri javoblar"))
            add(_card([_percent_row(c["category"], c["percent"], f"{c['correct']}/{c['total']}")
                       for c in qstats["by_category"]]))
        elif pack is None and qstats["by_pack"]:
            add(_section_title(f"«{cat}» bo'limchalari bo'yicha to'g'ri javoblar"))
            add(_card([_percent_row(f"Bo'limcha {b['pack']}", b["percent"],
                                    f"{b['correct']}/{b['total']}") for b in qstats["by_pack"]]))

        add(_section_title("Haftalik o'sish (to'g'ri javoblar foizi)"))
        add(self._build_weekly_chart(qstats["weekly"]))

    def _build_state_card(self, cat, pack) -> QFrame:
        """Kartalar holati: chapda doira diagramma, o'ngda sonlar va foizlar jadvali."""
        data = db.get_card_state_counts(cat, pack, self.separate_check.isChecked())
        counts, total = data["counts"], data["total"]

        frame = QFrame()
        frame.setStyleSheet(theme.card_frame_style(radius=14))
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(16, 12, 16, 12)
        head = QLabel("Kartalar soni")
        head.setFont(QFont(theme.FONT_FAMILY, 14, QFont.Bold))
        outer.addWidget(head)

        if total == 0:
            empty = QLabel("Bu tanlovda so'z yo'q.")
            empty.setStyleSheet(f"color: {theme.TEXT_MUTED};")
            outer.addWidget(empty)
            return frame

        body = QHBoxLayout()
        series = QPieSeries()
        for label, n in counts.items():
            if n <= 0:
                continue
            sl = series.append(label, n)
            sl.setBrush(QColor(STATE_COLORS[label]))
            sl.setBorderColor(QColor(theme.BG_CARD))
        chart = QChart()
        chart.addSeries(series)
        view = _styled_chart(chart)
        view.setMinimumSize(240, 240)
        view.setMaximumWidth(300)
        body.addWidget(view, stretch=2)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        for i, (label, n) in enumerate(counts.items()):
            if label == db.UNCHECKED_LABEL and not self.separate_check.isChecked():
                continue
            dot = QLabel()
            dot.setFixedSize(12, 12)
            dot.setStyleSheet(f"background: {STATE_COLORS[label]}; border-radius: 2px;")
            grid.addWidget(dot, i, 0)
            grid.addWidget(QLabel(label), i, 1)
            num = QLabel(str(n))
            num.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            grid.addWidget(num, i, 2)
            pct = QLabel(format_percent(100 * n / total))
            pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            pct.setStyleSheet(f"color: {theme.TEXT_SECONDARY};")
            grid.addWidget(pct, i, 3)
        last = len(counts)
        grid.addWidget(QLabel("Jami so'zlar"), last, 1)
        total_label = QLabel(str(total))
        total_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        total_label.setFont(QFont(theme.FONT_FAMILY, 14, QFont.Bold))
        grid.addWidget(total_label, last, 2)
        grid.setColumnStretch(1, 1)
        body.addLayout(grid, stretch=3)
        outer.addLayout(body)
        return frame

    def _build_weekly_chart(self, weekly: list) -> QChartView:
        """Oxirgi haftalar bo'yicha to'g'ri javoblar foizi (0-100) ustunli grafigi."""
        bar_set = QBarSet("To'g'ri javoblar %")
        bar_set.setColor(QColor(theme.ACCENT))
        bar_set.append([w["percent"] for w in weekly])
        series = QBarSeries()
        series.append(bar_set)
        chart = QChart()
        chart.addSeries(series)

        axis_x = QBarCategoryAxis()
        axis_x.append([w["week_start"].strftime("%d.%m") for w in weekly])
        axis_x.setLabelsColor(QColor(theme.TEXT_SECONDARY))
        axis_x.setGridLineColor(QColor(theme.BORDER))
        axis_x.setLinePenColor(QColor(theme.BORDER))
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)

        axis_y = QValueAxis()
        axis_y.setRange(0, 100)
        axis_y.setLabelFormat("%d%%")
        axis_y.setLabelsColor(QColor(theme.TEXT_SECONDARY))
        axis_y.setGridLineColor(QColor(theme.BORDER))
        axis_y.setLinePenColor(QColor(theme.BORDER))
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)
        return _styled_chart(chart)

    def _build_activity_chart(self) -> QChartView:
        data = db.get_reviews_per_day(14)

        bar_set = QBarSet("Mashqlar")
        bar_set.setColor(QColor(theme.ACCENT))
        bar_set.append([count for _, count in data])

        series = QBarSeries()
        series.append(bar_set)

        chart = QChart()
        chart.addSeries(series)

        # Sana yorliqlari ko'p bo'lsa bir-biriga tig'ilib o'qib
        # bo'lmay qoladi - shuning uchun faqat har ikkinchi kunning
        # sanasini ko'rsatamiz.
        display_labels = [label if i % 2 == 0 else "" for i, (label, _) in enumerate(data)]
        axis_x = QBarCategoryAxis()
        axis_x.append(display_labels)
        axis_x.setLabelsColor(QColor(theme.TEXT_SECONDARY))
        axis_x.setGridLineColor(QColor(theme.BORDER))
        axis_x.setLinePenColor(QColor(theme.BORDER))
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)

        max_val = max([count for _, count in data] + [1])
        axis_y = QValueAxis()
        axis_y.setRange(0, max_val + 1)
        axis_y.setLabelFormat("%d")
        axis_y.setLabelsColor(QColor(theme.TEXT_SECONDARY))
        axis_y.setGridLineColor(QColor(theme.BORDER))
        axis_y.setLinePenColor(QColor(theme.BORDER))
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)

        return _styled_chart(chart)

    def _build_category_chart(self, category_counts: list) -> QChartView:
        categories = [c for c, _ in category_counts]

        bar_set = QBarSet("So'zlar")
        bar_set.setColor(QColor(theme.ACCENT))
        bar_set.append([count for _, count in category_counts])

        series = QBarSeries()
        series.append(bar_set)

        chart = QChart()
        chart.addSeries(series)

        axis_x = QBarCategoryAxis()
        axis_x.append(categories)
        axis_x.setLabelsColor(QColor(theme.TEXT_SECONDARY))
        axis_x.setGridLineColor(QColor(theme.BORDER))
        axis_x.setLinePenColor(QColor(theme.BORDER))
        chart.addAxis(axis_x, Qt.AlignBottom)
        series.attachAxis(axis_x)

        max_val = max([count for _, count in category_counts] + [1])
        axis_y = QValueAxis()
        axis_y.setRange(0, max_val + 1)
        axis_y.setLabelFormat("%d")
        axis_y.setLabelsColor(QColor(theme.TEXT_SECONDARY))
        axis_y.setGridLineColor(QColor(theme.BORDER))
        axis_y.setLinePenColor(QColor(theme.BORDER))
        chart.addAxis(axis_y, Qt.AlignLeft)
        series.attachAxis(axis_y)

        return _styled_chart(chart)
