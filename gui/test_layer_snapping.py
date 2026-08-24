import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSettings
from PySide6.QtWidgets import QApplication

from gui.editor_model import Project, copy_sequence
from gui.studio_qt import MainWindow, read_image_size, snap_layer_position


class LayerSnappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_snaps_near_center_but_leaves_distant_position_free(self):
        frame = QRectF(0, 0, 1000, 800)

        centered = snap_layer_position(QPointF(506, 393), frame, 100, 80, 1, threshold=10)
        free = snap_layer_position(QPointF(620, 510), frame, 100, 80, 1, threshold=10)

        self.assertEqual(centered, QPointF(500, 400))
        self.assertEqual(free, QPointF(620, 510))

    def test_snaps_sprite_edges_to_frame_edges(self):
        frame = QRectF(0, 0, 1000, 800)

        top_left = snap_layer_position(QPointF(54, 43), frame, 100, 80, 1, threshold=5)
        bottom_right = snap_layer_position(QPointF(947, 758), frame, 100, 80, 1, threshold=5)

        self.assertEqual(top_left, QPointF(50, 40))
        self.assertEqual(bottom_right, QPointF(950, 760))

    def test_new_keyframe_uses_one_to_one_scale(self):
        repository = Path(__file__).resolve().parents[1]
        example = repository / "examples" / "texbynum"
        with tempfile.TemporaryDirectory() as folder:
            project = Project.create(Path(folder))
            frames = copy_sequence(project, [example / "source_segment.png"])
            project.width, project.height = read_image_size(frames[0])
            asset = project.add_asset(example / "source_photo.png")
            window = MainWindow(QSettings(str(Path(folder) / "settings.ini"), QSettings.Format.IniFormat))
            window.project = project

            window.add_layer(asset.id, 0, QPointF(100, 100))

            self.assertEqual(project.compositions[0][0].scale, 1.0)
            window.close()


if __name__ == "__main__":
    unittest.main()
