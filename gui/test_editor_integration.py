import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from gui.editor_model import Layer, Project, copy_sequence
from gui.studio_qt import RenderWorker, locate_tool, read_image_size


class EditorIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_two_frame_render(self):
        root = Path(__file__).resolve().parents[1]
        engine = locate_tool("ebsynth.exe")
        if not engine:
            self.skipTest("ebsynth.exe is not built")
        example = root / "examples" / "texbynum"
        with tempfile.TemporaryDirectory() as folder:
            project = Project.create(Path(folder))
            frames = copy_sequence(
                project,
                [example / "source_segment.png", example / "target_segment.png"],
            )
            project.width, project.height = read_image_size(frames[0])
            asset = project.add_asset(example / "source_photo.png")
            project.compositions[0] = [
                Layer.create(asset.id, project.width / 2, project.height / 2, 1.0, 0)
            ]
            project.save()
            errors = []
            worker = RenderWorker(project, engine)
            worker.failed.connect(errors.append)
            worker.run()
            self.assertEqual(errors, [])
            outputs = sorted(project.output_dir.glob("frame_*.png"))
            self.assertEqual(len(outputs), 2)
            self.assertEqual(read_image_size(outputs[1]), (project.width, project.height))


if __name__ == "__main__":
    unittest.main()
