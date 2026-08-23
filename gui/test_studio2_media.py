import subprocess
import tempfile
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from gui.editor_model import Layer, Project, copy_sequence
from gui.studio_qt import FrameView, VideoImportWorker, locate_tool, read_image_size


class Studio2MediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_video_import_extracts_timeline_frames(self):
        ffmpeg, ffprobe = locate_tool("ffmpeg.exe"), locate_tool("ffprobe.exe")
        if not ffmpeg or not ffprobe:
            self.skipTest("FFmpeg is not available")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "eight-frames.mp4"
            subprocess.run(
                [str(ffmpeg), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=8", "-frames:v", "8", str(source)],
                check=True,
            )
            project = Project.create(root / "project")
            completed, errors = [], []
            worker = VideoImportWorker(project, source, ffmpeg, ffprobe)
            worker.completed.connect(lambda *values: completed.append(values))
            worker.failed.connect(errors.append)
            worker.run()
            self.assertEqual(errors, [])
            self.assertEqual(len(completed[0][0]), 8)
            self.assertAlmostEqual(completed[0][1], 8.0)
            self.assertEqual(completed[0][2:4], (160, 120))

    def test_viewer_builds_editable_layer_scene(self):
        repository = Path(__file__).resolve().parents[1]
        example = repository / "examples" / "texbynum"
        with tempfile.TemporaryDirectory() as folder:
            project = Project.create(Path(folder))
            frames = copy_sequence(project, [example / "source_segment.png"])
            project.width, project.height = read_image_size(frames[0])
            asset = project.add_asset(example / "source_photo.png")
            layer = Layer.create(asset.id, project.width / 2, project.height / 2, 1, 0)
            project.compositions[0] = [layer]
            view = FrameView()
            view.set_frame(project, 0, "Композиция")
            self.assertIn(layer.id, view.layer_items)
            self.assertEqual(len(view.scene_model.items()), 2)


if __name__ == "__main__":
    unittest.main()
