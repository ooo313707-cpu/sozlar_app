"""3-bosqich: bugungi statistika, heatmap, mavzu, so'z ranglari, sozlamalar, bosh sahifa."""
from datetime import datetime, timedelta, timezone

import pytest
from PySide6.QtGui import QColor

from app import database as db
from app.ui import theme
from app.ui import heatmap as hm
from app.ui.main_window import stars_text, counts_text, COUNTS_ROLE
from tests.test_ui import qapp, boxes, mock_gemini, win, pump, add_words  # noqa: F401


def put_history(word_id, when, rating="Good", ms=4000):
    conn = db.get_connection()
    conn.execute("INSERT INTO history (word_id, timestamp, rating, source, duration_ms) VALUES (?,?,?,?,?)",
                 (word_id, when.isoformat(), rating, "flashcard", ms))
    conn.commit()
    conn.close()


# ---------------- bugungi statistika ----------------

def test_today_stats_empty(fresh_db):
    s = db.get_today_stats()
    assert s == {"studied": 0, "minutes": 0.0, "pace": 0.0, "retention": None}


def test_today_stats_values(fresh_db):
    ids = [db.add_word(f"w{i}", "t") for i in range(4)]
    db.review_word(ids[0], "good", duration_ms=6000)
    db.review_word(ids[1], "easy", duration_ms=2000)
    db.review_word(ids[2], "again", duration_ms=4000)
    db.review_word(ids[3], "hard")                       # vaqt yo'q: tezlikka kirmaydi
    s = db.get_today_stats()
    assert s["studied"] == 4
    assert s["minutes"] == round(12000 / 60000, 1)
    assert s["pace"] == 4.0                              # (6+2+4)/3
    assert s["retention"] == 75                          # 3 tadan 3 ta emas: 4 dan 1 ta Again


def test_today_stats_ignores_yesterday(fresh_db):
    wid = db.add_word("a", "b")
    put_history(wid, db._now_utc() - timedelta(days=2), "Again")
    assert db.get_today_stats()["studied"] == 0


def test_stars_text():
    assert stars_text(None) == "—"
    assert stars_text(100) == "★★★★★"
    assert stars_text(77) == "★★★★☆"
    assert stars_text(0) == "☆☆☆☆☆"


# ---------------- heatmap va streak ----------------

def test_activity_counts_and_longest_streak(fresh_db):
    wid = db.add_word("a", "b")
    today_noon = db._now_utc()
    for d in (0, 1, 2, 5, 6, 7, 8, 9):                   # 3 kun ketma-ket, keyin 5 kun ketma-ket
        put_history(wid, today_noon - timedelta(days=d), "Good")
    counts = db.get_activity_counts(30)
    assert sum(counts.values()) == 8
    assert db.get_longest_streak() == 5
    assert db.get_current_streak() >= 3


def test_longest_streak_empty(fresh_db):
    assert db.get_longest_streak() == 0


def test_heatmap_levels():
    assert hm.level_of(0, 10) == 0
    assert hm.level_of(1, 10) == 1
    assert hm.level_of(5, 10) == 2
    assert hm.level_of(7, 10) == 3
    assert hm.level_of(10, 10) == 4
    assert hm.level_of(3, 0) in (4, 3, 2, 1)             # peak=0 dan bo'linmaydi


def test_heatmap_grid_modes(win, qapp):
    w = win.pages["home"].activity_card.heatmap
    today = db.today_study_day()
    for mode, weeks in (("year", 53), ("month", 5), ("week", 1)):
        w.set_mode(mode)
        cells, n = w._grid()
        assert n == weeks
        assert all(d <= today for _c, _r, d in cells)    # kelajak kunlari yo'q
        assert any(d == today for _c, _r, d in cells)
        assert len({d for _c, _r, d in cells}) == len(cells)


def test_heatmap_hit_test_and_paint(win, qapp):
    wid = add_words(1)[0]
    put_history(wid, db._now_utc(), "Good")
    win.show_page("home")
    pump(qapp)
    w = win.pages["home"].activity_card.heatmap
    w.resize(900, 150)
    pump(qapp, 30)
    cells, weeks = w._grid()
    size = w._cell_size(weeks)
    col, row, d = cells[-1]
    r = w._cell_rect(col, row, size)
    assert w.cell_at(r.center().x(), r.center().y()) == d
    assert w.cell_at(-5, -5) is None
    assert not w.grab().isNull()


def test_activity_card_streak_text(win, qapp):
    wid = add_words(1)[0]
    put_history(wid, db._now_utc(), "Good")
    win.show_page("home")
    txt = win.pages["home"].activity_card.streak_label.text()
    assert "Joriy" in txt and "Eng uzun" in txt


# ---------------- mavzu va ranglar ----------------

def test_apply_palette_and_fallback():
    try:
        assert theme.apply_palette("Yashil") == "Yashil"
        assert theme.ACCENT == theme.PALETTES["Yashil"][0]
        assert theme.ACCENT_PRESSED in theme.accent_button()
        assert theme.apply_palette("yo'q-palitra") == theme.DEFAULT_PALETTE
        assert theme.ACCENT == theme.PALETTES[theme.DEFAULT_PALETTE][0]
    finally:
        theme.apply_palette(theme.DEFAULT_PALETTE)


def test_all_palettes_valid():
    for name, vals in theme.PALETTES.items():
        assert len(vals) == 4
        assert all(QColor(v).isValid() for v in vals), name


def test_retention_color_bands():
    assert theme.retention_color(10) == theme.retention_color(49)
    assert theme.retention_color(50) == theme.retention_color(69)
    assert theme.retention_color(70) == theme.retention_color(89)
    assert theme.retention_color(90) == theme.retention_color(100)
    assert len({theme.retention_color(p) for p in (10, 60, 80, 95)}) == 4
    assert theme.retention_color(None) == theme.TEXT_MUTED


# ---------------- sahifalar ----------------

def test_home_tree_items_carry_counts(win, qapp):
    ids = add_words(3, "Kitob")
    db.review_word(ids[0], "good")
    win.show_page("home")
    top = win.pages["home"].tree.topLevelItem(0)
    d = top.data(1, COUNTS_ROLE)
    assert d == {"blue": 2, "green": 1, "red": 0}
    assert top.text(1) == counts_text({"blue": 2, "green": 1, "red": 0})
    assert not win.pages["home"].tree.grab().isNull()      # delegate xatosiz chizadi


def test_home_shows_today_stats_and_name(win, qapp):
    ids = add_words(2, "Kitob")
    db.set_setting("user_name", "Zubayr")
    db.review_word(ids[0], "good", duration_ms=3000)
    win.show_page("home")
    home = win.pages["home"]
    assert "Zubayr" in home.greeting_label.text()
    assert home.today_card.studied.value_label.text() == "1"
    assert home.today_card.retention.value_label.text() == "100%"
    assert "Zubayr" in win.user_label.text()


def test_home_without_history_shows_dash(win, qapp):
    win.show_page("home")
    c = win.pages["home"].today_card
    assert c.studied.value_label.text() == "0"
    assert c.retention.value_label.text() == "—"


def test_sidebar_add_button_opens_add_page(win, qapp):
    win.add_btn.click()
    assert win.stack.currentWidget() is win.pages["add_word"]
    assert "add_word" not in win.nav_buttons


def test_word_list_has_colored_retention_column(win, qapp):
    ids = add_words(2, "Kitob")
    db.review_word(ids[0], "good")
    win.show_page("word_list")
    t = win.pages["word_list"].table
    assert t.horizontalHeaderItem(5).text() == "Eslab qolish"
    values = {t.item(r, 0).text(): t.item(r, 5).text() for r in range(t.rowCount())}
    assert values["Ki0"[0:0] + "ki0"] == "100%" or values["ki1"] == "—"
    assert t.cellWidget(0, 6) is not None and t.cellWidget(0, 7) is not None


def test_settings_save_name_palette_and_notice(win, qapp, boxes):
    s = win.pages["settings"]
    win.show_page("settings")
    s.name_input.setText("Ali")
    s.palette_input.setCurrentText("Pushti")
    s.save_settings()
    assert db.get_setting("user_name") == "Ali"
    assert db.get_setting("accent_color") == "Pushti"
    assert any("qayta ochganingizda" in str(i) for i in boxes["info"])
    assert "Ali" in win.user_label.text()
    # Qayta saqlash (rang o'zgarmadi) - qo'shimcha eslatma yo'q
    boxes["info"].clear()
    s.save_settings()
    assert not any("qayta ochganingizda" in str(i) for i in boxes["info"])


def test_settings_loads_saved_values(win, qapp):
    db.set_setting("user_name", "Vali")
    db.set_setting("accent_color", "Yashil")
    win.show_page("settings")
    s = win.pages["settings"]
    assert s.name_input.text() == "Vali"
    assert s.palette_input.currentText() == "Yashil"


def test_settings_unknown_saved_palette_falls_back(win, qapp):
    db.set_setting("accent_color", "ghost")
    win.show_page("settings")           # xato bermasligi kerak
    assert win.pages["settings"].palette_input.currentText() in theme.PALETTES


def test_all_pages_render_at_small_size(win, qapp):
    add_words(3, "Kitob")
    win.resize(900, 600)
    for page in ("home", "add_word", "word_list", "stats", "settings"):
        win.show_page(page)
        pump(qapp, 20)
        assert not win.grab().isNull()
