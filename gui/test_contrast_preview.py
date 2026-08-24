import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication, QGraphicsPixmapItem

from gui.editor_model import Layer, Project, copy_sequence
from gui.studio_qt import FrameView, LayerItem, read_image_size


class ContrastPreviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def create_project_with_layer(self, root: Path) -> Project:
        repository = Path(__file__).resolve().parents[1]
        example = repository / "examples" / "texbynum"
        project = Project.create(root)
        frames = copy_sequence(project, [example / "source_segment.png"])
        project.width, project.height = read_image_size(frames[0])
        asset = project.add_asset(example / "source_photo.png")
        project.compositions[0] = [Layer.create(asset.id, project.width / 2, project.height / 2, 1, 0)]
        return project

    @staticmethod
    def base_item(view: FrameView) -> QGraphicsPixmapItem:
        return next(
            item
            for item in view.scene_model.items()
            if isinstance(item, QGraphicsPixmapItem) and not isinstance(item, LayerItem)
        )

    def test_contrast_mode_dims_only_the_source_frame(self):
        with tempfile.TemporaryDirectory() as folder:
            project = self.create_project_with_layer(Path(folder))
            view = FrameView()

            view.set_frame(project, 0, "Композиция", contrast_layers=True)

            self.assertAlmostEqual(self.base_item(view).opacity(), 0.3)
            self.assertAlmostEqual(next(iter(view.layer_items.values())).opacity(), 1.0)

    def test_normal_composition_keeps_source_at_full_brightness(self):
        with tempfile.TemporaryDirectory() as folder:
            project = self.create_project_with_layer(Path(folder))
            view = FrameView()

            view.set_frame(project, 0, "Композиция", contrast_layers=False)

            self.assertAlmostEqual(self.base_item(view).opacity(), 1.0)


if __name__ == "__main__":
    unittest.main()
