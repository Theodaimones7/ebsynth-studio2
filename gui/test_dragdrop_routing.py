from pathlib import Path
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QPointF

from gui.studio_qt import MainWindow


class DropTarget:
    handle_source_or_keyframe_drop = MainWindow.handle_source_or_keyframe_drop

    def __init__(self, frames=None):
        self.project = None if frames is None else SimpleNamespace(frames=frames)
        self.frame_imports = []
        self.keyframe_imports = []

    def import_frames(self, paths, automatic_project=False):
        self.frame_imports.append((paths, automatic_project))

    def import_keyframes(self, paths, frame_index=None, point=None):
        self.keyframe_imports.append((paths, frame_index, point))

    def current_frame(self):
        return 7


class DragDropRoutingTests(unittest.TestCase):
    def test_drop_without_source_imports_animation_frames(self):
        target = DropTarget()
        paths = [Path("frame_001.png"), Path("frame_002.png")]

        target.handle_source_or_keyframe_drop(paths, point=QPointF(10, 20))

        self.assertEqual(target.frame_imports, [(paths, True)])
        self.assertEqual(target.keyframe_imports, [])

    def test_drop_with_source_imports_keyframe_at_timeline_frame(self):
        target = DropTarget(["source_001.png"])
        paths = [Path("painted_key.png")]
        point = QPointF(10, 20)

        target.handle_source_or_keyframe_drop(paths, point=point, frame_index=12)

        self.assertEqual(target.frame_imports, [])
        self.assertEqual(target.keyframe_imports, [(paths, 12, point)])

    def test_viewer_drop_uses_current_frame_after_source_is_loaded(self):
        target = DropTarget(["source_001.png"])
        paths = [Path("painted_key.png")]

        target.handle_source_or_keyframe_drop(paths)

        self.assertEqual(target.keyframe_imports, [(paths, 7, None)])


if __name__ == "__main__":
    unittest.main()
