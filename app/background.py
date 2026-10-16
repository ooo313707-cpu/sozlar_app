"""
background.py  (CLAUDE-3)
-------------------------
Fon ishlari uchun yengil ishchi (QThread o'rniga). audio_service, import_export va
so'z qo'shishdagi imlo tekshiruvi shu sinfdan foydalanadi.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal


class ThreadWorker(QObject):
    """Fon ishi uchun QThread O'RNIGA ishlatiladigan yengil ishchi.

    Nima uchun QThread emas: QThread ishlab turganida uni o'chirib yuborish
    (sahifa yopilishi, ilovadan chiqish) "QThread: Destroyed while thread is still
    running" xatosi bilan butun jarayonni to'xtatadi. Bu yerda ish oddiy daemon
    oqimda bajariladi: ilova yopilganda oqim jim to'xtaydi, oyna esa hech qachon
    qotmaydi. QThread bilan bir xil interfeys: start(), isRunning(), wait(ms),
    finished signali; signallar asosiy oqimga navbat bilan yetkaziladi.
    Voris run() ni yozadi.
    """

    finished = Signal()

    def __init__(self, parent=None):
        """Ishchini yaratadi (oqim start() da ishga tushadi)."""
        super().__init__(parent)
        self._thread = None

    def run(self):
        """Voris bu yerda fon ishini bajaradi."""
        raise NotImplementedError

    def start(self):
        """Fon oqimini ishga tushiradi (allaqachon ishlayotgan bo'lsa - hech narsa)."""
        if self.isRunning():
            return
        self._thread = threading.Thread(target=self._target, daemon=True)
        self._thread.start()

    def _target(self):
        """Oqim funksiyasi: run() ni bajaradi, oxirida finished chiqaradi."""
        try:
            self.run()
        finally:
            self._safe_emit(self.finished)

    def _safe_emit(self, signal, *args):
        """Signalni chiqaradi; obyekt (sahifa) allaqachon o'chirilgan bo'lsa jim o'tadi."""
        try:
            signal.emit(*args)
        except RuntimeError:
            pass

    def isRunning(self) -> bool:
        """Oqim hozir ishlayaptimi?"""
        return self._thread is not None and self._thread.is_alive()

    def wait(self, msecs: int = 30000) -> bool:
        """Oqim tugashini kutadi (millisekund). Tugagan bo'lsa True."""
        if self._thread is not None:
            self._thread.join(msecs / 1000)
        return not self.isRunning()
