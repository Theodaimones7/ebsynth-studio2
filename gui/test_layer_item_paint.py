import unittest

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from gui.editor_model import Layer
from gui.studio_qt import LayerItem


class LayerItemPaintTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_selection_outline_does_not_cover_keyframe(self):
        pixmap = QPixmap(20, 20)
        pixmap.fill(QColor("#d01020"))
        layer = Layer.create("asset", 20, 20, 1, 0)
        item = LayerItem(pixmap, layer)
        item.setSelected(True)

        image = QImage(40, 40, QImage.Format.Format_ARGB32)
        image.fill(QColor("#000000"))
        painter = QPainter(image)
        painter.translate(QPointF(20, 20))
        item.paint(painter, None)
        painter.end()

        self.assertEqual(image.pixelColor(20, 20), QColor("#d01020"))


if __name__ == "__main__":
    unittest.main()
