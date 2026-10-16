"""
theme.py
--------
Butun ilova uchun YAGONA ranglar va uslub palitrasi. Ilovaning butun
ko'rinishini o'zgartirmoqchi bo'lsangiz - shu faylni o'zgartirasiz,
boshqa fayllarga tegishning hojati yo'q.
"""

# ---------------------------------------------------------------------------
# Ranglar
# ---------------------------------------------------------------------------

BG_MAIN = "#12141c"          # asosiy fon
BG_SIDEBAR = "#181b27"       # chap panel foni
BG_CARD = "#1f2330"          # kartochka/varaq foni
BG_CARD_HOVER = "#272c3d"
BG_INPUT = "#191c27"         # matn kiritish maydonlari foni
BORDER = "#2e3348"

# Urg'u (accent) rangi palitralari: nom -> (asosiy, hover, yumshoq fon, bosilganda).
# Standart - ko'k-kulrang. Tanlov Sozlamalar'da; ilova qayta ochilganda kuchga kiradi.
PALETTES = {
    "Ko'k-kulrang": ("#5b7fa8", "#6d92bd", "#1f2a3b", "#486a91"),
    "Binafsha": ("#6c5ce7", "#7d70ec", "#26234a", "#5849c2"),
    "Yashil": ("#2f9e6e", "#3fb283", "#1b2f29", "#25805a"),
    "To'q sariq": ("#d9822b", "#e8943c", "#38281a", "#b86c20"),
    "Pushti": ("#d6568b", "#e56a9e", "#3a1f2d", "#b4426f"),
}
DEFAULT_PALETTE = "Ko'k-kulrang"

ACCENT, ACCENT_HOVER, ACCENT_SOFT, ACCENT_PRESSED = PALETTES[DEFAULT_PALETTE]
PALETTE_NAME = DEFAULT_PALETTE


def apply_palette(name: str) -> str:
    """Urg'u rangini almashtiradi (ilova oynalari yaratilishidan OLDIN chaqiriladi).
    Noma'lum nom bo'lsa standart palitra olinadi. Ishlatilgan nomni qaytaradi."""
    global ACCENT, ACCENT_HOVER, ACCENT_SOFT, ACCENT_PRESSED, PALETTE_NAME
    if name not in PALETTES:
        name = DEFAULT_PALETTE
    ACCENT, ACCENT_HOVER, ACCENT_SOFT, ACCENT_PRESSED = PALETTES[name]
    PALETTE_NAME = name
    return name


# 🔵🟢🔴 sonlar uchun ranglar (bo'limlar daraxti va izohlar)
COUNT_BLUE = "#4f8cff"
COUNT_GREEN = "#2ecc71"
COUNT_RED = "#e74c3c"


def retention_color(percent) -> str:
    """So'zning eslab qolish foiziga qarab rang: 0-50 qizil, 50-70 to'q sariq,
    70-90 sariq-yashil, 90+ yashil. Hali ko'rilmagan (None) - kulrang."""
    if percent is None:
        return TEXT_MUTED
    if percent < 50:
        return "#e74c3c"
    if percent < 70:
        return "#e67e22"
    if percent < 90:
        return "#a8c23a"
    return "#2ecc71"

TEXT_PRIMARY = "#f1f2f6"
TEXT_SECONDARY = "#9295a8"
TEXT_MUTED = "#686c80"

RATING_AGAIN = "#e74c3c"
RATING_HARD = "#e67e22"
RATING_GOOD = "#27ae60"
RATING_EASY = "#3498db"

SUCCESS = "#2ecc71"
DANGER = "#e74c3c"
WARNING = "#f1c40f"

FONT_FAMILY = "Segoe UI"

# Sidebar navigatsiya tugmalari qanday sahifaga mos kelishi
NAV_ITEMS = [
    ("home", "🏠  Bosh sahifa"),
    ("word_list", "📋  So'zlar ro'yxati"),
    ("stats", "📊  Statistika"),
    ("settings", "⚙️  Sozlamalar"),
]


def global_stylesheet() -> str:
    """Butun ilovaga bir marta qo'llaniladigan asosiy uslub."""
    return f"""
    QWidget {{
        background: {BG_MAIN};
        color: {TEXT_PRIMARY};
        font-family: "{FONT_FAMILY}";
        font-size: 14px;
    }}
    QMainWindow {{
        background: {BG_MAIN};
    }}
    QLabel {{
        background: transparent;
        border: none;
    }}
    QPushButton {{
        background: {BG_CARD};
        color: {TEXT_PRIMARY};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 10px 16px;
        font-size: 14px;
    }}
    QPushButton:hover {{
        background: {BG_CARD_HOVER};
        border: 1px solid {ACCENT};
    }}
    QPushButton:pressed {{
        background: {ACCENT};
    }}
    QPushButton:disabled {{
        color: {TEXT_MUTED};
        border: 1px solid {BORDER};
    }}
    QLineEdit, QTextEdit, QComboBox, QSpinBox {{
        background: {BG_INPUT};
        border: 1px solid {BORDER};
        border-radius: 8px;
        padding: 9px 10px;
        color: {TEXT_PRIMARY};
        selection-background-color: {ACCENT};
    }}
    QLineEdit:focus, QTextEdit:focus, QComboBox:focus, QSpinBox:focus {{
        border: 1px solid {ACCENT};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 24px;
    }}
    QComboBox QAbstractItemView {{
        background: {BG_INPUT};
        border: 1px solid {BORDER};
        selection-background-color: {ACCENT};
        color: {TEXT_PRIMARY};
    }}
    QTableWidget {{
        background: {BG_CARD};
        border: 1px solid {BORDER};
        border-radius: 12px;
        gridline-color: {BORDER};
        selection-background-color: {ACCENT_SOFT};
    }}
    QTableWidget::item {{
        padding: 6px;
    }}
    QTreeWidget {{
        background: {BG_CARD};
        border: 1px solid {BORDER};
        border-radius: 12px;
        outline: none;
    }}
    QTreeWidget::item {{
        padding: 7px 4px;
        border: none;
    }}
    QTreeWidget::item:hover {{
        background: {BG_CARD_HOVER};
    }}
    QTreeWidget::item:selected {{
        background: {ACCENT_SOFT};
        color: {TEXT_PRIMARY};
    }}
    QHeaderView::section {{
        background: {BG_SIDEBAR};
        color: {TEXT_SECONDARY};
        padding: 10px 8px;
        border: none;
        border-bottom: 1px solid {BORDER};
        font-weight: 600;
    }}
    QScrollBar:vertical {{
        background: {BG_MAIN};
        width: 10px;
        margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {BORDER};
        border-radius: 5px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{
        background: {ACCENT};
    }}
    QMessageBox {{
        background: {BG_CARD};
    }}
    """


def accent_button(padding="14px 18px", font_size="15px") -> str:
    """Asosiy, urg'u beruvchi tugma uslubi (masalan "Boshlash" tugmasi)."""
    return f"""
    QPushButton {{
        background: {ACCENT};
        color: white;
        border: none;
        border-radius: 12px;
        padding: {padding};
        font-size: {font_size};
        font-weight: 600;
        text-align: left;
    }}
    QPushButton:hover {{
        background: {ACCENT_HOVER};
    }}
    QPushButton:pressed {{
        background: {ACCENT_PRESSED};
    }}
    QPushButton:disabled {{
        background: {BG_CARD};
        color: {TEXT_MUTED};
    }}
    """


def secondary_button() -> str:
    """Ikkinchi darajali tugma (masalan "Orqaga")."""
    return f"""
    QPushButton {{
        background: transparent;
        color: {TEXT_SECONDARY};
        border: 1px solid {BORDER};
        border-radius: 10px;
        padding: 10px 16px;
        text-align: left;
    }}
    QPushButton:hover {{
        color: {TEXT_PRIMARY};
        border: 1px solid {TEXT_SECONDARY};
        background: {BG_CARD_HOVER};
    }}
    """


def card_frame_style(radius=16) -> str:
    # QLabel ham QFrame'dan meros oladi - shuning uchun kartochka ichidagi
    # yozuvlar ramkaga tushib qolmasligi uchun alohida qoida berilgan.
    return f"""
    QFrame {{
        background: {BG_CARD};
        border: 1px solid {BORDER};
        border-radius: {radius}px;
    }}
    QLabel {{
        background: transparent;
        border: none;
    }}
    """


def rating_button(color: str) -> str:
    return f"""
    QPushButton {{
        background: {color};
        color: white;
        border: none;
        border-radius: 10px;
        font-weight: bold;
        padding: 10px;
    }}
    QPushButton:hover {{
        background: {color}dd;
    }}
    """
