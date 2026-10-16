"""SettingsPage: auto_play va Import/Eksport ulanishi (offscreen Qt smoke, CLAUDE-3)."""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from app import audio_service as au
from app import database as db
from app import import_export as ie


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _Home:
    refreshed = 0
    def refresh(self): _Home.refreshed += 1


class _MW:
    pages = {"home": _Home()}
    def refresh_user_name(self): pass


@pytest.fixture
def page(qapp, fresh_db, monkeypatch):
    from app.ui.settings import SettingsPage
    boxes = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: boxes.append(("info", a[2])))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: boxes.append(("warn", a[2])))
    monkeypatch.setattr(au, "ensure_word_audio", lambda wid, **k: None)   # tarmoqsiz
    p = SettingsPage(_MW())
    p._boxes = boxes
    p.refresh()
    return p


def test_auto_play_default_checked_and_saved(page):
    assert page.auto_play_input.isChecked()
    page.auto_play_input.setChecked(False)
    page.save_settings()
    assert db.get_setting("auto_play") == "0"
    page.refresh()
    assert not page.auto_play_input.isChecked()


def test_export_dialog(page, tmp_path, monkeypatch):
    db.add_word("apple", "olma")
    out = tmp_path / "out"       # kengaytmasiz tanlandi -> .csv qo'shiladi
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out), ""))
    page.export_file(ie.MODE_WORDS)
    assert (tmp_path / "out.csv").exists() and page._boxes[-1][0] == "info"


def test_import_runs_queue_and_makes_words_studyable(page, qapp, tmp_path, monkeypatch):
    f = tmp_path / "w.csv"
    f.write_text("word,translation\napple,olma\naple,xato\n", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(f), ""))
    page.import_file()
    assert "Qo'shildi: 2" in page._boxes[-1][1]
    assert page.queue_worker is not None
    page.queue_worker.wait(10000)
    qapp.processEvents()
    status = {r["word"]: r["spell_status"] for r in
              db.get_connection().execute("SELECT word, spell_status FROM words")}
    assert status == {"apple": "ok", "aple": "suspect"}
    assert ie.pending_count() == 0
    assert "tugadi" in page.queue_label.text()
    assert _Home.refreshed >= 2


def test_import_bad_file_shows_warning(page, tmp_path, monkeypatch):
    f = tmp_path / "bad.txt"
    f.write_text("x", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *a, **k: (str(f), ""))
    page.import_file()
    assert page._boxes[-1][0] == "warn" and page.queue_worker is None


def test_resume_queue_after_restart(page, qapp, tmp_path):
    f = tmp_path / "w.csv"
    f.write_text("word,translation\napple,olma\n", encoding="utf-8")
    ie.import_words(f)                       # navbat bazada qoldi (ilova yopildi deb faraz)
    assert ie.pending_count() == 2
    page.resume_import_queue()
    page.queue_worker.wait(10000); qapp.processEvents()
    assert ie.pending_count() == 0


def test_resume_does_nothing_when_queue_empty(page):
    page.resume_import_queue()
    assert page.queue_worker is None


def test_resume_picks_up_unchecked_words(page, qapp):
    pytest.importorskip("spellchecker")
    wid = db.add_word("apple", "olma")                    # so'z qo'shish sahifasidan: unchecked
    page.resume_import_queue()
    assert page.queue_worker is not None
    page.queue_worker.wait(10000); qapp.processEvents()
    assert db.get_word(wid)["spell_status"] == "ok"


def test_check_unchecked_button(page, qapp):
    pytest.importorskip("spellchecker")
    wid = db.add_word("apple", "olma")
    page.check_unchecked_now()
    page.queue_worker.wait(10000); qapp.processEvents()
    assert db.get_word(wid)["spell_status"] == "ok"
    page.queue_worker = None
    page.check_unchecked_now()                            # endi tekshiradigan narsa yo'q
    assert page._boxes[-1] == ("info", "Tekshirilmagan so'z yo'q ✅") and page.queue_worker is None
