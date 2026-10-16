"""
audio_service.py  (CLAUDE-3, 3.5-bosqich)
-----------------------------------------
So'z talaffuzi (audio) zanjiri. Tartib (birinchi topilgani ishlatiladi):

  1. lokal fayl      audio/<so'z>.mp3   -> source = 'human' (👤)
  2. dictionaryapi.dev inson ovozi       -> source = 'human' (👤)
  3. edge-tts (Microsoft neyron ovozi)   -> source = 'tts'   (🤖)
  4. gTTS (Google)                       -> source = 'tts'   (🤖)
  5. QtTextToSpeech (Windows ovozi)      -> FAYL SAQLANMAYDI, to'g'ridan-to'g'ri
                                            o'qiladi; bazada source o'zgarmaydi

Hamma tarmoq/sintez ishi fon oqimida (AudioWorker) bajariladi, interfeys
qotib qolmaydi. Zanjir mantiqi PySide6'siz ham sinaladi (providers
parametri orqali soxta provayderlar beriladi).

Bazada: words.audio_source ('human'|'tts'|'none') va words.audio_path
(database.py v4 migratsiyasi). Sozlama: 'auto_play' (standart '1' - yoqiq).
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import database as db

AUDIO_DIR = db._app_base_dir() / "audio"

SOURCE_HUMAN = "human"
SOURCE_TTS = "tts"
SOURCE_NONE = "none"

MARKER_HUMAN = "👤"
MARKER_TTS = "🤖"

EDGE_VOICE = "en-US-AriaNeural"
NETWORK_TIMEOUT = 8          # soniya
_LOCAL_EXTENSIONS = (".mp3", ".wav", ".ogg")


# ---------------------------------------------------------------------------
# Yordamchilar
# ---------------------------------------------------------------------------

@dataclass
class AudioResult:
    """Zanjir natijasi.

    source: 'human' | 'tts' | 'none'
    path:   saqlangan fayl (Qt TTS yoki hech narsa topilmasa None)
    provider: 'local' | 'dictionary' | 'edge' | 'gtts' | 'qt' | 'none'
    use_qt_tts: True bo'lsa chaqiruvchi so'zni QtTextToSpeech bilan o'qishi kerak
    """
    source: str
    path: Path | None
    provider: str
    use_qt_tts: bool = False


def safe_filename(word: str) -> str:
    """So'zdan xavfsiz fayl nomi (kengaytmasiz) yasaydi: 'Look up' -> 'look_up'."""
    name = re.sub(r"[^a-z0-9_-]+", "_", (word or "").strip().lower()).strip("_")
    return name or "word"


def clean_for_tts(word: str) -> str:
    """Sintezga yuborishdan oldin so'zni tozalaydi (ortiqcha bo'sh joylar)."""
    return " ".join((word or "").split())


def audio_marker(source: str | None) -> str:
    """Audio manbasiga mos belgi: human -> 👤, tts -> 🤖, boshqasi -> ''."""
    if source == SOURCE_HUMAN:
        return MARKER_HUMAN
    if source == SOURCE_TTS:
        return MARKER_TTS
    return ""


def is_auto_play() -> bool:
    """Karta ko'rsatilganda audio o'z-o'zidan chalinsinmi? Standart: ha."""
    return db.get_setting("auto_play", "1") != "0"


def find_local_audio(word: str, audio_dir: Path | None = None) -> Path | None:
    """audio/ papkada so'zning tayyor faylini qidiradi (mp3/wav/ogg)."""
    base = Path(audio_dir) if audio_dir is not None else AUDIO_DIR
    stem = safe_filename(word)
    for ext in _LOCAL_EXTENSIONS:
        candidate = base / f"{stem}{ext}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    return None


def _file_ok(path: Path) -> bool:
    """Fayl mavjud va bo'sh emasmi?"""
    return path.is_file() and path.stat().st_size > 0


# ---------------------------------------------------------------------------
# Provayderlar: har biri (word, dest_path) -> bool (muvaffaqiyat)
# ---------------------------------------------------------------------------

def fetch_dictionary_audio(word: str, dest: Path) -> bool:
    """dictionaryapi.dev dan inson ovozini yuklaydi. Topilmasa/xato bo'lsa False."""
    url = "https://api.dictionaryapi.dev/api/v2/entries/en/" + urllib.parse.quote(word.strip().lower())
    with urllib.request.urlopen(url, timeout=NETWORK_TIMEOUT) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    urls: list[str] = []
    for entry in data if isinstance(data, list) else []:
        for ph in entry.get("phonetics", []) or []:
            audio_url = (ph.get("audio") or "").strip()
            if audio_url:
                urls.append("https:" + audio_url if audio_url.startswith("//") else audio_url)
    if not urls:
        return False
    # Amerikacha talaffuz ('-us') birinchi tanlanadi
    urls.sort(key=lambda u: 0 if "-us" in u.lower() else 1)
    with urllib.request.urlopen(urls[0], timeout=NETWORK_TIMEOUT) as resp:
        payload = resp.read()
    if not payload:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(payload)
    return _file_ok(dest)


def synth_edge_tts(word: str, dest: Path) -> bool:
    """edge-tts bilan neyron ovoz sintez qiladi (internet kerak)."""
    import edge_tts   # kech import: kutubxona bo'lmasa zanjir keyingi bosqichga o'tadi

    dest.parent.mkdir(parents=True, exist_ok=True)

    async def _run():
        await edge_tts.Communicate(clean_for_tts(word), EDGE_VOICE).save(str(dest))

    asyncio.run(_run())
    return _file_ok(dest)


def synth_gtts(word: str, dest: Path) -> bool:
    """gTTS bilan ovoz sintez qiladi (internet kerak)."""
    from gtts import gTTS   # kech import

    dest.parent.mkdir(parents=True, exist_ok=True)
    gTTS(text=clean_for_tts(word), lang="en").save(str(dest))
    return _file_ok(dest)


DEFAULT_PROVIDERS: list[tuple[str, str, Callable[[str, Path], bool]]] = [
    # (nomi, source, funksiya) - zanjir tartibida
    ("dictionary", SOURCE_HUMAN, fetch_dictionary_audio),
    ("edge", SOURCE_TTS, synth_edge_tts),
    ("gtts", SOURCE_TTS, synth_gtts),
]


# ---------------------------------------------------------------------------
# Zanjir
# ---------------------------------------------------------------------------

def resolve_audio(word: str, *, audio_dir: Path | None = None, providers=None) -> AudioResult:
    """Zanjirni boshidan oxirigacha yuradi va birinchi muvaffaqiyatli natijani qaytaradi.

    1) lokal fayl (audio/<so'z>.mp3) topilsa - tarmoqqa umuman chiqilmaydi;
    2-4) provayderlar ketma-ket; istisno (internet yo'q, kutubxona yo'q) bo'lsa
         keyingisiga o'tiladi;
    5) hammasi muvaffaqiyatsiz bo'lsa use_qt_tts=True qaytadi.
    """
    base = Path(audio_dir) if audio_dir is not None else AUDIO_DIR

    local = find_local_audio(word, base)
    if local is not None:
        return AudioResult(SOURCE_HUMAN, local, "local")

    chain = providers if providers is not None else DEFAULT_PROVIDERS
    for name, source, func in chain:
        dest = base / f"{safe_filename(word)}.mp3"
        try:
            if func(word, dest) and _file_ok(dest):
                return AudioResult(source, dest, name)
        except Exception:
            # Tarmoq xatosi, kutubxona yo'q va h.k. - keyingi bosqichga o'tamiz
            if dest.exists() and dest.stat().st_size == 0:
                dest.unlink(missing_ok=True)
            continue
    return AudioResult(SOURCE_NONE, None, "qt", use_qt_tts=True)


# ---------------------------------------------------------------------------
# Baza bilan ishlash
# ---------------------------------------------------------------------------

def get_word_audio(word_id: int) -> tuple[str, str | None]:
    """So'zning (audio_source, audio_path) juftligini qaytaradi. Topilmasa ('none', None)."""
    conn = db.get_connection()
    try:
        row = conn.execute("SELECT audio_source, audio_path FROM words WHERE id = ?",
                           (word_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return SOURCE_NONE, None
    return (row["audio_source"] or SOURCE_NONE), row["audio_path"]


def save_word_audio(word_id: int, source: str, path: Path | str | None) -> None:
    """So'zning audio manbasi va fayl yo'lini bazaga yozadi."""
    if source not in (SOURCE_HUMAN, SOURCE_TTS, SOURCE_NONE):
        raise ValueError(f"Noto'g'ri audio manbasi: {source!r}")
    conn = db.get_connection()
    try:
        conn.execute("UPDATE words SET audio_source = ?, audio_path = ? WHERE id = ?",
                     (source, str(path) if path else None, word_id))
        conn.commit()
    finally:
        conn.close()


def ensure_word_audio(word_id: int, *, audio_dir: Path | None = None, providers=None) -> AudioResult:
    """So'z uchun audio'ni zanjir orqali topadi va natijani bazaga yozadi.

    Bazada fayl allaqachon bor va mavjud bo'lsa - hech narsa yuklanmaydi,
    saqlangan manba (👤/🤖) o'zgarmaydi. Qt TTS holatida bazaga yozilmaydi.
    """
    row = db.get_word(word_id)
    if row is None:
        return AudioResult(SOURCE_NONE, None, "none")
    word = row["word"]
    stored_source, stored_path = get_word_audio(word_id)

    if stored_path and _file_ok(Path(stored_path)) and stored_source in (SOURCE_HUMAN, SOURCE_TTS):
        return AudioResult(stored_source, Path(stored_path), "local")

    result = resolve_audio(word, audio_dir=audio_dir, providers=providers)
    if result.path is not None:
        save_word_audio(word_id, result.source, result.path)
    return result


# ---------------------------------------------------------------------------
# Qt qismi: fon oqimi va ijrochi (faqat interfeys ishlayotganda kerak)
# ---------------------------------------------------------------------------

try:
    from PySide6.QtCore import QObject, QUrl, Signal
    from .background import ThreadWorker
except ImportError:   # PySide6 yo'q muhitda (faqat mantiq sinovlari) modul baribir yuklanadi
    QObject = None


if QObject is not None:

    class AudioWorker(ThreadWorker):
        """So'z audio'sini fon oqimida topadi/yuklaydi va natijani signal bilan beradi."""

        # dict/obyekt signallar uchun object ishlatiladi (D-010)
        audio_ready = Signal(int, object)   # (word_id, AudioResult)

        def __init__(self, word_id: int, parent=None):
            """word_id - audio kerak bo'lgan so'zning id'si."""
            super().__init__(parent)
            self.word_id = word_id

        def run(self):
            """Zanjirni ishga tushiradi; xato bo'lsa ham 'none' natija chiqaradi."""
            try:
                result = ensure_word_audio(self.word_id)
            except Exception:
                result = AudioResult(SOURCE_NONE, None, "qt", use_qt_tts=True)
            self._safe_emit(self.audio_ready, self.word_id, result)


    class AudioPlayer(QObject):
        """Fayl yoki matnni ovoz chiqarib o'ynaydi (QMediaPlayer / QtTextToSpeech)."""

        def __init__(self, parent=None):
            """Ijrochi kerak bo'lganda yaratiladi (kech yuklash)."""
            super().__init__(parent)
            self._player = None
            self._output = None
            self._tts = None

        def play_file(self, path: Path | str) -> None:
            """Audio faylni o'ynaydi."""
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
            if self._player is None:
                self._player = QMediaPlayer(self)
                self._output = QAudioOutput(self)
                self._player.setAudioOutput(self._output)
            self._player.setSource(QUrl.fromLocalFile(str(path)))
            self._player.play()

        def speak(self, text: str) -> bool:
            """Matnni Windows ovozi bilan o'qiydi. Imkoni bo'lmasa False."""
            try:
                from PySide6.QtTextToSpeech import QTextToSpeech
                if self._tts is None:
                    self._tts = QTextToSpeech(self)
                self._tts.say(clean_for_tts(text))
                return True
            except Exception:
                return False

        def play_result(self, word: str, result: AudioResult) -> None:
            """AudioResult bo'yicha: fayl bo'lsa o'ynaydi, bo'lmasa Qt TTS."""
            if result.path is not None:
                self.play_file(result.path)
            elif result.use_qt_tts:
                self.speak(word)
