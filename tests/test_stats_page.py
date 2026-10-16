"""Statistika sahifasi (CLAUDE-4): kartalar holati doirasi, bo'lim/bo'limcha filtri,
savol statistikasi. DB funksiyalari Qt'siz, sahifa Qt offscreen'da tekshiriladi."""
import pytest
from PySide6.QtCore import QEvent
from PySide6.QtCharts import QChartView, QPieSeries
from PySide6.QtWidgets import QApplication, QLabel

from app import database as db

app = QApplication.instance() or QApplication([])


def flush():
    """Qt hodisalarini va deleteLater() qilingan eski vidjetlarni tugatadi."""
    app.processEvents()
    app.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def mk(word, category="Umumiy", spell=db.SPELL_OK):
    wid = db.add_word(word, word + "-tr", category=category)
    db.set_spell_status(wid, spell)
    return wid


@pytest.fixture
def data(fresh_db):
    """Kitob: 21 ta so'z (2 bo'limcha: 20 + 1), Umumiy: 2 ta; bittasi tekshirilmagan."""
    kitob = [mk(f"k{i:02d}", "Kitob") for i in range(21)]
    umumiy = [mk("apple"), mk("pear", spell="unchecked")]
    db.review_word(kitob[0], "good")          # endi 'Yangi' emas
    return {"kitob": kitob, "umumiy": umumiy}


# --------------------------- DB: kartalar holati ---------------------------

def test_state_counts_total_and_unchecked_separate(data):
    r = db.get_card_state_counts()
    assert r["total"] == 23
    assert r["counts"][db.UNCHECKED_LABEL] == 1
    assert sum(r["counts"].values()) == r["total"]
    assert r["counts"]["Yangi"] == 21            # 22 yangi, 1 tasi tekshirilmagan
    assert r["counts"]["Yaxshi o'rganilgan"] + r["counts"]["O'rganilmoqda"] == 1


def test_state_counts_unchecked_merged_when_not_separate(data):
    r = db.get_card_state_counts(separate_unchecked=False)
    assert r["counts"][db.UNCHECKED_LABEL] == 0
    assert r["counts"]["Yangi"] == 22
    assert sum(r["counts"].values()) == 23


def test_state_counts_filter_category_and_pack(data):
    assert db.get_card_state_counts("Kitob")["total"] == 21
    assert db.get_card_state_counts("Umumiy")["total"] == 2
    assert db.get_card_state_counts("Kitob", 1)["total"] == 20
    assert db.get_card_state_counts("Kitob", 2)["total"] == 1
    assert db.get_card_state_counts("Yo'q bo'lim")["total"] == 0


# ------------------------ DB: savol statistikasi filtri ---------------------

def test_question_stats_filters_and_ignores_unchecked(data):
    k0, k20 = data["kitob"][0], data["kitob"][20]
    apple, pear = data["umumiy"]
    db.log_question_answer(k0, "meaning", True)
    db.log_question_answer(k0, "cloze", False)
    db.log_question_answer(k20, "meaning", False)
    db.log_question_answer(apple, "meaning", True)
    db.log_question_answer(pear, "meaning", False)       # tekshirilmagan - hisobga olinmaydi

    allst = db.get_question_stats()
    assert sum(t["total"] for t in allst["by_type"]) == 4
    assert "pear" not in [w["word"] for w in allst["worst_words"]]

    kit = db.get_question_stats(category="Kitob")
    assert sum(t["total"] for t in kit["by_type"]) == 3
    assert [p["pack"] for p in kit["by_pack"]] == [1, 2]

    p2 = db.get_question_stats(category="Kitob", pack=2)
    assert sum(t["total"] for t in p2["by_type"]) == 1
    assert p2["worst_words"][0]["word"] == "k20"
    assert db.get_question_stats(category="Yo'q")["by_type"] == []


# ------------------------------- Sahifa (UI) --------------------------------

class FakeMain:
    def show_page(self, name): pass


def make_page():
    from app.ui.stats import StatsPage
    page = StatsPage(FakeMain())
    page.refresh()
    flush()
    return page


def pie_values(page):
    for view in page.findChildren(QChartView):
        for s in view.chart().series():
            if isinstance(s, QPieSeries):
                return {sl.label(): sl.value() for sl in s.slices()}
    return None


def texts(page):
    return [lb.text() for lb in page.findChildren(QLabel)]


def test_page_empty_db_does_not_crash(fresh_db):
    page = make_page()
    assert pie_values(page) is None                       # so'z yo'q - doira yo'q
    assert any("Bu tanlovda so'z yo'q" in t for t in texts(page))
    assert any("Hali AI mashq javoblari yo'q" in t for t in texts(page))


def test_page_pie_and_filters(data):
    page = make_page()
    assert pie_values(page)["Yangi"] == 21
    assert pie_values(page)[db.UNCHECKED_LABEL] == 1
    assert page.pack_combo.isEnabled() is False           # bo'lim tanlanmagan

    page.category_combo.setCurrentIndex(page.category_combo.findData("Kitob"))
    flush()
    assert page.pack_combo.isEnabled() is True
    assert [page.pack_combo.itemData(i) for i in range(page.pack_combo.count())] == [None, 1, 2]
    assert sum(pie_values(page).values()) == 21

    page.pack_combo.setCurrentIndex(page.pack_combo.findData(2))
    flush()
    assert sum(pie_values(page).values()) == 1


def test_page_checkbox_merges_unchecked(data):
    page = make_page()
    page.separate_check.setChecked(False)
    flush()
    vals = pie_values(page)
    assert db.UNCHECKED_LABEL not in vals
    assert vals["Yangi"] == 22


def test_page_question_stats_sections_and_filter_kept_on_refresh(data):
    db.log_question_answer(data["kitob"][0], "meaning", False)
    db.log_question_answer(data["kitob"][0], "cloze", True)
    page = make_page()
    t = texts(page)
    assert any("Savol turlari" in x for x in t)
    assert any("k00" in x and "xato 1 / 2" in x for x in t)
    page.category_combo.setCurrentIndex(page.category_combo.findData("Kitob"))
    page.refresh()                                        # tanlov saqlanadi
    assert page.category_combo.currentData() == "Kitob"
    assert any("bo'limchalari bo'yicha" in x for x in texts(page))


def test_format_percent():
    from app.ui.stats import format_percent
    assert format_percent(76.6666) == "76,67%"
    assert format_percent(5.0) == "5%"
    assert format_percent(0) == "0%"
