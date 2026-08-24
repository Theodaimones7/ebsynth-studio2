import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from gui.editor_model import Project
from gui.studio_qt import MainWindow


class RecentProjectsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @staticmethod
    def settings(path: Path) -> QSettings:
        return QSettings(str(path), QSettings.Format.IniFormat)

    def test_project_is_remembered_on_close_and_can_be_reopened(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings_path = root / "settings.ini"
            project = Project.create(root / "My Animation")
            first = MainWindow(self.settings(settings_path))
            first.project = project

            first.close()
            first.settings.sync()

            second = MainWindow(self.settings(settings_path))
            self.assertEqual(second._recent_project_paths, [project.file_path.resolve()])
            self.assertTrue(any("My Animation" in action.text() for action in second.recent_menu.actions()))

            second.open_recent_project(project.file_path)
            self.assertIsNotNone(second.project)
            self.assertEqual(second.project.root, project.root)
            second.close()

    def test_missing_projects_are_removed_from_menu(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            settings = self.settings(root / "settings.ini")
            project = Project.create(root / "Deleted Project")
            window = MainWindow(settings)
            window.remember_project(project.file_path)
            project.file_path.unlink()

            window.update_recent_projects_menu()

            self.assertEqual(window._recent_project_paths, [])
            self.assertEqual(window.recent_menu.actions()[0].text(), "Список пуст")
            window.close()


if __name__ == "__main__":
    unittest.main()
