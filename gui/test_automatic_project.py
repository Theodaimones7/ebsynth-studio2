import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QFileDialog

from gui.studio_qt import MainWindow


class AutomaticProjectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_drag_import_can_create_project_without_folder_dialog(self):
        with tempfile.TemporaryDirectory() as folder:
            projects_root = Path(folder) / "Projects"
            window = MainWindow()
            window.automatic_projects_root = lambda: projects_root

            with patch.object(QFileDialog, "getExistingDirectory") as folder_dialog:
                created = window.ensure_project(automatic=True)

            self.assertTrue(created)
            self.assertIsNotNone(window.project)
            self.assertEqual(window.project.root.parent, projects_root)
            self.assertTrue(window.project.file_path.is_file())
            folder_dialog.assert_not_called()
            window.close()


if __name__ == "__main__":
    unittest.main()
