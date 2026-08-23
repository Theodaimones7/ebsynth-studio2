import tempfile
import unittest
from pathlib import Path

from gui.editor_model import Asset, Layer, Project, natural_key


class EditorModelTests(unittest.TestCase):
    def test_natural_sort(self):
        names = ["frame10.png", "frame2.png", "frame1.png"]
        self.assertEqual(sorted(names, key=natural_key), ["frame1.png", "frame2.png", "frame10.png"])

    def test_influence_tie_uses_earlier_key(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Project.create(Path(folder))
            project.frames = [f"media/frames/{i}.png" for i in range(9)]
            project.compositions = {
                0: [Layer.create("a", 0, 0, 1, 0)],
                8: [Layer.create("b", 0, 0, 1, 0)],
            }
            self.assertEqual(project.nearest_keyframe(4), 0)
            self.assertEqual(project.nearest_keyframe(5), 8)

    def test_project_round_trip(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Project.create(Path(folder))
            project.width, project.height, project.fps = 1920, 1080, 24
            project.frames = ["media/frames/frame_000001.png"]
            project.assets["asset"] = Asset("asset", "paint", "media/keyframes/paint.png")
            project.compositions[0] = [Layer.create("asset", 960, 540, 0.5, 0)]
            project.save()
            loaded = Project.load(project.file_path)
            self.assertEqual((loaded.width, loaded.height, loaded.fps), (1920, 1080, 24))
            self.assertEqual(loaded.compositions[0][0].asset_id, "asset")

    def test_z_order_is_normalized(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Project.create(Path(folder))
            project.compositions[3] = [
                Layer.create("a", 0, 0, 1, 8),
                Layer.create("b", 0, 0, 1, 2),
            ]
            project.normalize_z(3)
            self.assertEqual([layer.z for layer in project.compositions[3]], [0, 1])


if __name__ == "__main__":
    unittest.main()
