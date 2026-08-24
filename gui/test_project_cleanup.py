import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from gui.editor_model import Project
from gui.studio_qt import MainWindow


class ProjectCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_clear_result_preserves_source_and_keyframes(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Project.create(Path(folder))
            source = project.frames_dir / "frame_000001.png"
            keyframe = project.assets_dir / "key.png"
            result = project.output_dir / "frame_000001.png"
            style = project.styles_dir / "style_000001.png"
            for path in (source, keyframe, result, style):
                path.write_bytes(b"test")

            window = MainWindow(QSettings(str(Path(folder) / "settings.ini"), QSettings.Format.IniFormat))
            window.project = project
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                window.clear_render_result()

            self.assertTrue(source.exists())
            self.assertTrue(keyframe.exists())
            self.assertEqual(list(project.output_dir.iterdir()), [])
            self.assertEqual(list(project.styles_dir.iterdir()), [])
            self.assertIs(window.project, project)
            window.close()

    def test_restart_removes_only_managed_project_data(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            project = Project.create(root)
            unrelated = root / "keep-me.txt"
            unrelated.write_text("keep", encoding="utf-8")
            for directory in (project.frames_dir, project.assets_dir, project.source_dir, project.styles_dir, project.output_dir):
                (directory / "generated.tmp").write_bytes(b"test")

            window = MainWindow(QSettings(str(Path(folder) / "settings.ini"), QSettings.Format.IniFormat))
            window.project = project
            with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes):
                window.restart_project()

            self.assertIsNone(window.project)
            self.assertFalse(project.file_path.exists())
            for directory in (project.frames_dir, project.assets_dir, project.source_dir, project.styles_dir, project.output_dir):
                self.assertFalse(directory.exists())
            self.assertTrue(unrelated.exists())
            window.close()


if __name__ == "__main__":
    unittest.main()
