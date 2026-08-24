import tempfile
import unittest
from pathlib import Path

from PySide6.QtGui import QColor, QImage

from gui.studio_qt import build_sprite_sheet


class SpriteSheetTests(unittest.TestCase):
    def test_builds_grid_and_keeps_empty_cell_transparent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            colors = (QColor("#ff0000"), QColor("#00ff00"), QColor("#0000ff"))
            frames = []
            for index, color in enumerate(colors):
                path = root / f"frame_{index:06d}.png"
                image = QImage(4, 3, QImage.Format.Format_ARGB32)
                image.fill(color)
                self.assertTrue(image.save(str(path), "PNG"))
                frames.append(path)

            output = root / "sheet.png"
            grid = build_sprite_sheet(frames, 2, output)
            sheet = QImage(str(output))

            self.assertEqual(grid, (2, 2))
            self.assertEqual((sheet.width(), sheet.height()), (8, 6))
            self.assertEqual(sheet.pixelColor(1, 1), colors[0])
            self.assertEqual(sheet.pixelColor(5, 1), colors[1])
            self.assertEqual(sheet.pixelColor(1, 4), colors[2])
            self.assertEqual(sheet.pixelColor(5, 4).alpha(), 0)

    def test_rejects_frames_with_different_sizes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            frames = []
            for index, size in enumerate(((4, 3), (5, 3))):
                path = root / f"frame_{index:06d}.png"
                image = QImage(*size, QImage.Format.Format_ARGB32)
                image.fill(QColor("#ffffff"))
                self.assertTrue(image.save(str(path), "PNG"))
                frames.append(path)

            with self.assertRaisesRegex(ValueError, "отличается"):
                build_sprite_sheet(frames, 2, root / "sheet.png")


if __name__ == "__main__":
    unittest.main()
