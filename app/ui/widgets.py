"""
widgets.py
----------
Umumiy, qayta ishlatiladigan kichik UI yordamchilari.
"""

from PySide6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QScrollArea, QSizePolicy
from PySide6.QtCore import Qt


class CenteredColumn(QWidget):
    """Forma kabi tarkibni katta ekranda ham cho'zib yubormaydi - kontentni
    o'rtada, belgilangan maksimal kenglikda ushlab turadi. Chap/o'ng
    tomonlarda bo'sh joy avtomatik qoladi."""

    def __init__(self, max_width: int = 640):
        super().__init__()
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addStretch(1)

        self.inner = QWidget()
        self.inner.setMaximumWidth(max_width)
        self.inner.setMinimumWidth(0)
        # Mavjud kenglikni to'ldiradi (max_width gacha) - kontent o'rtada qoladi
        self.inner.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.content_layout = QVBoxLayout(self.inner)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self.inner, stretch=100)

        outer.addStretch(1)

    def layout_for_content(self) -> QVBoxLayout:
        return self.content_layout


def wrap_scrollable(widget: QWidget) -> QScrollArea:
    """Kontent oyna balandligidan oshib ketsa ham (masalan kichik monitorda),
    pastga surilib ko'rish imkonini beradi. Gorizontal skroll hech qachon
    chiqmaydi - kontent doim oyna kengligiga moslashadi."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    scroll.setWidget(widget)
    return scroll
