"""flashcard.py audio ulanishi: offscreen Qt smoke-sinov (CLAUDE-3)."""
import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from app import audio_service as au
from app import database as db


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


class _MW:
    pages = {}
    def show_page(self, *_): pass
    def offer_forgotten_review(self, *_): pass


@pytest.fixture
def page(qapp, fresh_db, monkeypatch):
    from app.ui.flashcard import FlashcardPage
    played = []
    p = FlashcardPage(_MW())
    monkeypatch.setattr(p.audio_player, "play_file", lambda path: played.append(("file", str(path))))
    monkeypatch.setattr(p.audio_player, "play_result",
                        lambda word, res: played.append(("result", word, res.source)))
    p._played = played
    return p


def _card(page, word="apple"):
    wid = db.add_word(word, "olma")
    db.set_spell_status(wid, "confirmed")
    page.queue = [wid]
    page.show_current_card()
    return wid


def test_auto_play_uses_stored_file(page, tmp_path):
    f = tmp_path / "apple.mp3"; f.write_bytes(b"x")
    wid = db.add_word("apple", "olma"); db.set_spell_status(wid, "confirmed")
    au.save_word_audio(wid, "human", f)
    page.queue = [wid]; page.show_current_card()
    assert page._played == [("file", str(f))]       # oddiy rejim: so'z ko'rinishi bilan chalinadi
    assert page.audio_marker.text() == "👤" and not page.audio_btn.isHidden()
    page.reveal_answer()                            # "Javobni ko'rsatish" qayta chalmaydi
    assert page._played == [("file", str(f))]


def test_auto_play_off_does_not_play(page, tmp_path):
    db.set_setting("auto_play", "0")
    f = tmp_path / "apple.mp3"; f.write_bytes(b"x")
    wid = db.add_word("apple", "olma"); db.set_spell_status(wid, "confirmed")
    au.save_word_audio(wid, "tts", f)
    page.queue = [wid]; page.show_current_card()
    page.reveal_answer()
    assert page._played == [] and page.audio_marker.text() == "🤖"
    page.play_word_audio()            # 🔊 qo'lda bosilsa baribir chalinadi
    assert page._played == [("file", str(f))]


def test_no_file_starts_worker_and_stale_result_ignored(page, monkeypatch):
    started = []

    class FakeWorker:
        def __init__(self, wid, parent=None):
            self.word_id = wid
            class Sig:
                def connect(self_, fn): self.cb = fn
            self.audio_ready = Sig()
            class Fin:
                def connect(self_, fn): pass
            self.finished = Fin()
        def start(self): started.append(self.word_id)

    monkeypatch.setattr(au, "AudioWorker", FakeWorker)
    wid = _card(page)
    page.reveal_answer()
    assert started == [wid]
    # natija KELGUNCHA foydalanuvchi boshqa kartaga o'tib ketdi -> eski so'z chalinmasin
    other = db.add_word("pear", "nok"); db.set_spell_status(other, "confirmed")
    page.queue = [other]; page.show_current_card()
    page._on_audio_ready(wid, au.AudioResult("tts", None, "qt", use_qt_tts=True))
    assert all(p[0] != "result" for p in page._played)


def test_ready_result_for_current_card_plays(page, monkeypatch):
    class FakeWorker:
        def __init__(self, wid, parent=None):
            class Sig:
                def connect(self_, fn): pass
            self.audio_ready = Sig(); self.finished = Sig()
        def start(self): pass
    monkeypatch.setattr(au, "AudioWorker", FakeWorker)
    wid = _card(page)
    page.reveal_answer()
    page._on_audio_ready(wid, au.AudioResult("tts", None, "qt", use_qt_tts=True))
    assert page._played == [("result", "apple", "tts")]


def test_typing_mode_hides_audio_until_answer(page, monkeypatch, tmp_path):
    db.set_setting("typing_mode", "1")
    f = tmp_path / "apple.mp3"; f.write_bytes(b"x")
    wid = db.add_word("apple", "olma"); db.set_spell_status(wid, "confirmed")
    au.save_word_audio(wid, "human", f)
    page.queue = [wid]; page.show_current_card()
    assert page.audio_btn.isHidden() and page._played == []    # so'z hali yashirin
    page.typing_input.setText("apple"); page.submit_typed()
    assert not page.audio_btn.isHidden() and page._played == [("file", str(f))]


def test_audio_hidden_when_session_finishes(page):
    _card(page)
    page.queue = []; page.retry_queue = []
    page.show_current_card()
    assert page.audio_btn.isHidden()
