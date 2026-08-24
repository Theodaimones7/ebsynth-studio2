"""EbSynth Studio 2: a small frame-accurate animation editor for Windows."""

from __future__ import annotations

import copy
import json
import math
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QMimeData, QPointF, QRectF, QSize, QStandardPaths, Qt, QThread, QTimer, Signal
from PySide6.QtGui import (
    QAction,
    QColor,
    QDrag,
    QIcon,
    QImage,
    QImageReader,
    QKeySequence,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QStyle,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

try:
    from editor_model import Asset, Layer, Project, PROJECT_FILE, copy_sequence, image_files
except ImportError:  # Imported by unit tests as gui.studio_qt.
    from gui.editor_model import Asset, Layer, Project, PROJECT_FILE, copy_sequence, image_files


APP_NAME = "EbSynth Studio 2"
MIME_ASSET = "application/x-ebsynth-keyframe"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
ACCENT = QColor("#ff4f72")
BG = "#0b0d12"
PANEL = "#151a24"
CARD = "#1c2230"
TEXT = "#edf1f7"
MUTED = "#8e99ac"


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def locate_tool(name: str) -> Path | None:
    candidates = [
        application_root() / "bin" / name,
        Path("C:/ffmpeg/bin") / name,
    ]
    found = shutil.which(name)
    if found:
        candidates.append(Path(found))
    return next((path.resolve() for path in candidates if path.is_file()), None)


def read_image_size(path: Path) -> tuple[int, int]:
    reader = QImageReader(str(path))
    size = reader.size()
    if not size.isValid():
        image = QImage(str(path))
        size = image.size()
    if not size.isValid():
        raise ValueError(f"Не удалось прочитать изображение: {path.name}")
    return size.width(), size.height()


def verify_sequence(paths: list[Path]) -> tuple[int, int]:
    if not paths:
        raise ValueError("Последовательность пуста.")
    expected = read_image_size(paths[0])
    for path in paths[1:]:
        actual = read_image_size(path)
        if actual != expected:
            raise ValueError(
                f"Размер {path.name} — {actual[0]}×{actual[1]}, ожидалось {expected[0]}×{expected[1]}."
            )
    return expected


def parse_fps(value: str) -> float:
    if "/" in value:
        numerator, denominator = value.split("/", 1)
        return float(numerator) / max(float(denominator), 1.0)
    return float(value)


def layer_dict(layer: Layer) -> dict[str, object]:
    return {
        "id": layer.id,
        "asset_id": layer.asset_id,
        "center_x": layer.center_x,
        "center_y": layer.center_y,
        "scale": layer.scale,
        "rotation": layer.rotation,
        "opacity": layer.opacity,
        "z": layer.z,
    }


def compose_frame(project: Project, frame_index: int, destination: Path) -> Path:
    base = QImage(str(project.frame_path(frame_index))).convertToFormat(QImage.Format.Format_ARGB32)
    if base.isNull():
        raise RuntimeError(f"Не удалось открыть кадр {frame_index + 1}.")
    painter = QPainter(base)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    for layer in sorted(project.compositions.get(frame_index, []), key=lambda value: value.z):
        asset = QImage(str(project.asset_path(layer.asset_id)))
        if asset.isNull():
            painter.end()
            raise RuntimeError(f"Не удалось открыть keyframe: {project.assets[layer.asset_id].name}")
        painter.save()
        painter.setOpacity(max(0.0, min(1.0, layer.opacity)))
        painter.translate(layer.center_x, layer.center_y)
        painter.rotate(layer.rotation)
        painter.scale(layer.scale, layer.scale)
        painter.drawImage(QPointF(-asset.width() / 2, -asset.height() / 2), asset)
        painter.restore()
    painter.end()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not base.save(str(destination), "PNG"):
        raise RuntimeError(f"Не удалось сохранить {destination.name}.")
    return destination


def build_sprite_sheet(frame_paths: list[Path], columns: int, destination: Path) -> tuple[int, int]:
    if not frame_paths:
        raise ValueError("Нет кадров для экспорта.")
    if columns < 1:
        raise ValueError("Число колонок должно быть больше нуля.")
    first = QImage(str(frame_paths[0]))
    if first.isNull():
        raise ValueError(f"Не удалось открыть кадр: {frame_paths[0].name}")
    frame_width, frame_height = first.width(), first.height()
    rows = math.ceil(len(frame_paths) / columns)
    sheet_width, sheet_height = frame_width * columns, frame_height * rows
    if sheet_width > 32767 or sheet_height > 32767 or sheet_width * sheet_height > 150_000_000:
        raise ValueError("Спрайтшит слишком большой. Сохраните кадры отдельно или разделите анимацию.")
    sheet = QImage(sheet_width, sheet_height, QImage.Format.Format_ARGB32)
    if sheet.isNull():
        raise RuntimeError("Не удалось выделить память для спрайтшита.")
    sheet.fill(Qt.GlobalColor.transparent)
    painter = QPainter(sheet)
    for index, path in enumerate(frame_paths):
        frame = first if index == 0 else QImage(str(path))
        if frame.isNull():
            painter.end()
            raise ValueError(f"Не удалось открыть кадр: {path.name}")
        if frame.size() != first.size():
            painter.end()
            raise ValueError(f"Размер кадра {path.name} отличается от остальных.")
        painter.drawImage((index % columns) * frame_width, (index // columns) * frame_height, frame)
    painter.end()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not sheet.save(str(destination), "PNG"):
        raise RuntimeError(f"Не удалось сохранить файл: {destination}")
    return columns, rows


class VideoImportWorker(QThread):
    completed = Signal(object, float, int, int, str)
    failed = Signal(str)

    def __init__(self, project: Project, source: Path, ffmpeg: Path, ffprobe: Path) -> None:
        super().__init__()
        self.project = project
        self.source = source
        self.ffmpeg = ffmpeg
        self.ffprobe = ffprobe

    def run(self) -> None:
        try:
            self.project.ensure_directories()
            copied_source = self.project.source_dir / self.source.name
            shutil.copy2(self.source, copied_source)
            probe = subprocess.run(
                [
                    str(self.ffprobe), "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=width,height,avg_frame_rate", "-of", "json", str(copied_source),
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
            )
            stream = json.loads(probe.stdout)["streams"][0]
            fps = parse_fps(stream.get("avg_frame_rate", "12/1"))
            pattern = self.project.frames_dir / "frame_%06d.png"
            result = subprocess.run(
                [str(self.ffmpeg), "-y", "-v", "error", "-i", str(copied_source), "-map", "0:v:0", "-fps_mode", "passthrough", str(pattern)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.strip() or "FFmpeg завершился с ошибкой.")
            frames = sorted(self.project.frames_dir.glob("frame_*.png"))
            if not frames:
                raise RuntimeError("FFmpeg не извлёк ни одного кадра.")
            relatives = [path.relative_to(self.project.root).as_posix() for path in frames]
            self.completed.emit(relatives, fps, int(stream["width"]), int(stream["height"]), copied_source.name)
        except Exception as exc:
            self.failed.emit(str(exc))


class RenderWorker(QThread):
    progress = Signal(int, int, int, int)
    log = Signal(str)
    completed = Signal(int, float)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, project: Project, engine: Path) -> None:
        super().__init__()
        self.project = project
        self.engine = engine
        self._cancel = False
        self._process: subprocess.Popen[str] | None = None

    def stop(self) -> None:
        self._cancel = True
        if self._process and self._process.poll() is None:
            self._process.terminate()

    def run(self) -> None:
        started = time.monotonic()
        try:
            keys = self.project.keyframe_indices()
            if not keys:
                raise RuntimeError("Добавьте хотя бы один keyframe на таймлайн.")
            if not self.engine.is_file():
                raise RuntimeError("Не найден встроенный ebsynth.exe.")
            style_paths: dict[int, Path] = {}
            for key in keys:
                style_paths[key] = compose_frame(self.project, key, self.project.styles_dir / f"style_{key + 1:06d}.png")

            self.project.output_dir.mkdir(parents=True, exist_ok=True)
            total = self.project.frame_count
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            for index in range(total):
                if self._cancel:
                    self.cancelled.emit()
                    return
                key = self.project.nearest_keyframe(index)
                self.progress.emit(index, total, index, key)
                output = self.project.output_dir / f"frame_{index + 1:06d}.png"
                if index == key:
                    shutil.copy2(style_paths[key], output)
                else:
                    settings = self.project.settings
                    command = [
                        str(self.engine), "-style", str(style_paths[key]), "-weight", str(settings.style_weight),
                        "-guide", str(self.project.frame_path(key)), str(self.project.frame_path(index)),
                        "-weight", str(settings.guide_weight), "-uniformity", str(settings.uniformity),
                        "-patchsize", str(settings.patch_size), "-searchvoteiters", str(settings.search_vote_iters),
                        "-patchmatchiters", str(settings.patch_match_iters), "-stopthreshold", str(settings.stop_threshold),
                        "-backend", "cpu",
                    ]
                    if settings.pyramid_levels is not None:
                        command.extend(["-pyramidlevels", str(settings.pyramid_levels)])
                    if settings.extra_pass:
                        command.append("-extrapass3x3")
                    command.extend(["-output", str(output)])
                    self._process = subprocess.Popen(
                        command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                        encoding="utf-8", errors="replace", creationflags=creationflags,
                    )
                    output_text, _ = self._process.communicate()
                    return_code = self._process.returncode
                    self._process = None
                    if self._cancel:
                        self.cancelled.emit()
                        return
                    if return_code != 0:
                        raise RuntimeError(f"Кадр {index + 1}:\n{output_text.strip()}")
                self.progress.emit(index + 1, total, index, key)
            self.completed.emit(total, time.monotonic() - started)
        except Exception as exc:
            self.failed.emit(str(exc))


class AssetList(QListWidget):
    filesDropped = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(86, 64))
        self.setGridSize(QSize(105, 90))
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setSpacing(5)

    def startDrag(self, _actions: Qt.DropAction) -> None:
        item = self.currentItem()
        if not item:
            return
        mime = QMimeData()
        mime.setData(MIME_ASSET, str(item.data(Qt.ItemDataRole.UserRole)).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(item.icon().pixmap(72, 54))
        drag.exec(Qt.DropAction.CopyAction)

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasUrls():
            paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
            self.filesDropped.emit(paths)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class LayerItem(QGraphicsObject):
    interactionFinished = Signal(object)
    interactionStarted = Signal()

    def __init__(self, pixmap: QPixmap, layer: Layer) -> None:
        super().__init__()
        self.pixmap = pixmap
        self.layer = layer
        self._mode = "move"
        self._start_scale = layer.scale
        self._start_distance = 1.0
        self._syncing = True
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.sync_from_layer()
        self._syncing = False

    def sync_from_layer(self) -> None:
        self._syncing = True
        self.setPos(self.layer.center_x, self.layer.center_y)
        self.setScale(self.layer.scale)
        self.setRotation(self.layer.rotation)
        self.setOpacity(self.layer.opacity)
        self.setZValue(self.layer.z + 1)
        self._syncing = False

    def boundingRect(self) -> QRectF:
        margin = 42.0 / max(abs(self.scale()), 0.05)
        return QRectF(-self.pixmap.width() / 2 - margin, -self.pixmap.height() / 2 - margin, self.pixmap.width() + 2 * margin, self.pixmap.height() + 2 * margin)

    def paint(self, painter: QPainter, _option, _widget=None) -> None:  # type: ignore[no-untyped-def]
        rect = QRectF(-self.pixmap.width() / 2, -self.pixmap.height() / 2, self.pixmap.width(), self.pixmap.height())
        painter.drawPixmap(rect, self.pixmap, QRectF(self.pixmap.rect()))
        if not self.isSelected():
            return
        width = 2.0 / max(abs(self.scale()), 0.05)
        painter.setPen(QPen(ACCENT, width, Qt.PenStyle.SolidLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        radius = 6.0 / max(abs(self.scale()), 0.05)
        painter.setBrush(QColor("#ffffff"))
        for point in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight()):
            painter.drawEllipse(point, radius, radius)
        rotation_point = QPointF(0, rect.top() - 28.0 / max(abs(self.scale()), 0.05))
        painter.drawLine(QPointF(0, rect.top()), rotation_point)
        painter.setBrush(ACCENT)
        painter.drawEllipse(rotation_point, radius, radius)

    def _near(self, first: QPointF, second: QPointF, pixels: float = 14.0) -> bool:
        return math.hypot(first.x() - second.x(), first.y() - second.y()) <= pixels / max(abs(self.scale()), 0.05)

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self.interactionStarted.emit()
        rect = QRectF(-self.pixmap.width() / 2, -self.pixmap.height() / 2, self.pixmap.width(), self.pixmap.height())
        rotation_point = QPointF(0, rect.top() - 28.0 / max(abs(self.scale()), 0.05))
        if self._near(event.pos(), rotation_point):
            self._mode = "rotate"
        elif any(self._near(event.pos(), point) for point in (rect.topLeft(), rect.topRight(), rect.bottomLeft(), rect.bottomRight())):
            self._mode = "scale"
            center = self.scenePos()
            self._start_distance = max(math.hypot(event.scenePos().x() - center.x(), event.scenePos().y() - center.y()), 1.0)
            self._start_scale = self.scale()
        else:
            self._mode = "move"
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        center = self.scenePos()
        if self._mode == "scale":
            distance = math.hypot(event.scenePos().x() - center.x(), event.scenePos().y() - center.y())
            value = max(0.02, min(20.0, self._start_scale * distance / self._start_distance))
            self.setScale(value)
            self.layer.scale = value
            self.update()
            event.accept()
        elif self._mode == "rotate":
            delta = event.scenePos() - center
            value = math.degrees(math.atan2(delta.y(), delta.x())) + 90.0
            self.setRotation(value)
            self.layer.rotation = value
            self.update()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().mouseReleaseEvent(event)
        self.layer.center_x = self.pos().x()
        self.layer.center_y = self.pos().y()
        self.layer.scale = self.scale()
        self.layer.rotation = self.rotation()
        self.interactionFinished.emit(self.layer)

    def itemChange(self, change, value):  # type: ignore[no-untyped-def]
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged and not self._syncing:
            point = value
            self.layer.center_x = point.x()
            self.layer.center_y = point.y()
        return super().itemChange(change, value)


class FrameView(QGraphicsView):
    assetDropped = Signal(str, object)
    filesDropped = Signal(object, object)
    layerChanged = Signal(object)
    interactionStarted = Signal()
    selectionChanged = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self.setBackgroundBrush(QColor("#07090d"))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.scene_model = QGraphicsScene(self)
        self.setScene(self.scene_model)
        self.scene_model.selectionChanged.connect(self._selection_changed)
        self.project: Project | None = None
        self.frame_index = 0
        self.mode = "Композиция"
        self.layer_items: dict[str, LayerItem] = {}

    def set_frame(self, project: Project | None, index: int, mode: str) -> None:
        self.project, self.frame_index, self.mode = project, index, mode
        self.scene_model.clear()
        self.layer_items.clear()
        if not project or not project.frames:
            self.scene_model.addText("Откройте видео или кадры").setDefaultTextColor(QColor(MUTED))
            return
        source = project.frame_path(index)
        output = project.output_dir / f"frame_{index + 1:06d}.png"
        if mode == "Результат" and output.is_file():
            source = output
        pixmap = QPixmap(str(source))
        base = self.scene_model.addPixmap(pixmap)
        base.setZValue(-1000)
        self.scene_model.setSceneRect(0, 0, project.width, project.height)
        if mode == "Композиция":
            for layer in sorted(project.compositions.get(index, []), key=lambda value: value.z):
                asset_pixmap = QPixmap(str(project.asset_path(layer.asset_id)))
                item = LayerItem(asset_pixmap, layer)
                item.interactionStarted.connect(self.interactionStarted)
                item.interactionFinished.connect(self.layerChanged)
                self.scene_model.addItem(item)
                self.layer_items[layer.id] = item
        self.fitInView(self.scene_model.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        if self.project and self.project.frames:
            self.fitInView(self.scene_model.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasFormat(MIME_ASSET) or event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasFormat(MIME_ASSET) or event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        point = self.mapToScene(event.position().toPoint())
        if event.mimeData().hasFormat(MIME_ASSET):
            asset_id = bytes(event.mimeData().data(MIME_ASSET)).decode("utf-8")
            self.assetDropped.emit(asset_id, point)
            event.acceptProposedAction()
        elif event.mimeData().hasUrls():
            files = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
            self.filesDropped.emit(files, point)
            event.acceptProposedAction()

    def _selection_changed(self) -> None:
        selected = [item for item in self.scene_model.selectedItems() if isinstance(item, LayerItem)]
        self.selectionChanged.emit(selected[0].layer if selected else None)


class Timeline(QWidget):
    frameSelected = Signal(int)
    assetDropped = Signal(str, int)
    filesDropped = Signal(object, int)
    CELL_W = 96
    CELL_H = 92

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumHeight(self.CELL_H)
        self.setMaximumHeight(self.CELL_H)
        self.project: Project | None = None
        self.playhead = 0
        self.thumbnails: dict[int, QPixmap] = {}

    def set_project(self, project: Project | None) -> None:
        self.project = project
        self.playhead = project.playhead if project else 0
        self.thumbnails.clear()
        self.setMinimumWidth(max(600, (project.frame_count if project else 1) * self.CELL_W))
        self.update()

    def set_playhead(self, index: int) -> None:
        self.playhead = index
        self.update()

    def _frame_at(self, x: float) -> int:
        count = self.project.frame_count if self.project else 0
        return max(0, min(int(x // self.CELL_W), max(0, count - 1)))

    def thumbnail(self, index: int) -> QPixmap:
        if index not in self.thumbnails and self.project:
            image = QPixmap(str(self.project.frame_path(index)))
            self.thumbnails[index] = image.scaled(82, 58, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        return self.thumbnails.get(index, QPixmap())

    def paintEvent(self, _event) -> None:  # type: ignore[no-untyped-def]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#11151e"))
        if not self.project:
            painter.setPen(QColor(MUTED))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "Таймлайн появится после импорта")
            return
        influence = self.project.influence_map() if self.project.keyframe_indices() else {}
        palette = [QColor("#ff4f72"), QColor("#6d8cff"), QColor("#41c99b"), QColor("#c78cff"), QColor("#f0a84c")]
        keys = self.project.keyframe_indices()
        key_colors = {key: palette[position % len(palette)] for position, key in enumerate(keys)}
        for index in range(self.project.frame_count):
            rect = QRectF(index * self.CELL_W + 3, 3, self.CELL_W - 6, self.CELL_H - 7)
            painter.fillRect(rect, QColor(CARD))
            if index in influence:
                color = key_colors[influence[index]]
                painter.fillRect(QRectF(rect.left(), rect.bottom() - 5, rect.width(), 5), color)
            thumb = self.thumbnail(index)
            target = QRectF(rect.left() + 5, rect.top() + 5, rect.width() - 10, 58)
            source = QRectF(thumb.rect())
            painter.drawPixmap(target, thumb, source)
            painter.setPen(QColor(TEXT))
            painter.drawText(QRectF(rect.left(), rect.bottom() - 23, rect.width(), 18), Qt.AlignmentFlag.AlignCenter, str(index + 1))
            if index in keys:
                painter.setBrush(key_colors[index])
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QPointF(rect.right() - 10, rect.top() + 10), 6, 6)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(ACCENT if index == self.playhead else QColor("#2a3242"), 3 if index == self.playhead else 1))
            painter.drawRoundedRect(rect, 5, 5)

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.project and self.project.frame_count:
            self.frameSelected.emit(self._frame_at(event.position().x()))

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasFormat(MIME_ASSET) or event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasFormat(MIME_ASSET) or event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        index = self._frame_at(event.position().x())
        if event.mimeData().hasFormat(MIME_ASSET):
            asset_id = bytes(event.mimeData().data(MIME_ASSET)).decode("utf-8")
            self.assetDropped.emit(asset_id, index)
        elif event.mimeData().hasUrls():
            self.filesDropped.emit([Path(url.toLocalFile()) for url in event.mimeData().urls()], index)
        event.acceptProposedAction()


class SettingsDialog(QDialog):
    def __init__(self, project: Project, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.project = project
        self.setWindowTitle("Настройки синтеза")
        layout = QFormLayout(self)
        settings = project.settings
        self.style_weight = self._double(0, 20, settings.style_weight, 0.1)
        self.guide_weight = self._double(0, 20, settings.guide_weight, 0.1)
        self.uniformity = self._double(0, 100000, settings.uniformity, 100)
        self.patch_size = QSpinBox(); self.patch_size.setRange(3, 15); self.patch_size.setSingleStep(2); self.patch_size.setValue(settings.patch_size)
        self.search_iters = QSpinBox(); self.search_iters.setRange(0, 20); self.search_iters.setValue(settings.search_vote_iters)
        self.patch_iters = QSpinBox(); self.patch_iters.setRange(0, 20); self.patch_iters.setValue(settings.patch_match_iters)
        self.extra = QCheckBox(); self.extra.setChecked(settings.extra_pass)
        layout.addRow("Вес стиля", self.style_weight)
        layout.addRow("Вес guide", self.guide_weight)
        layout.addRow("Uniformity", self.uniformity)
        layout.addRow("Размер patch", self.patch_size)
        layout.addRow("Search/vote", self.search_iters)
        layout.addRow("PatchMatch", self.patch_iters)
        layout.addRow("Доп. проход 3×3", self.extra)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    @staticmethod
    def _double(low: float, high: float, value: float, step: float) -> QDoubleSpinBox:
        field = QDoubleSpinBox(); field.setRange(low, high); field.setValue(value); field.setSingleStep(step); return field

    def apply(self) -> None:
        settings = self.project.settings
        settings.style_weight = self.style_weight.value()
        settings.guide_weight = self.guide_weight.value()
        settings.uniformity = self.uniformity.value()
        settings.patch_size = self.patch_size.value()
        settings.search_vote_iters = self.search_iters.value()
        settings.patch_match_iters = self.patch_iters.value()
        settings.extra_pass = self.extra.isChecked()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1440, 900)
        self.setMinimumSize(1050, 720)
        self.setAcceptDrops(True)
        self.project: Project | None = None
        self.import_worker: VideoImportWorker | None = None
        self.render_worker: RenderWorker | None = None
        self._history: list[str] = []
        self._history_index = -1
        self._updating_properties = False
        self.autosave = QTimer(self); self.autosave.setSingleShot(True); self.autosave.timeout.connect(self.save_project)
        self.play_timer = QTimer(self); self.play_timer.timeout.connect(self.advance_playback)
        self._build_actions()
        self._build_ui()
        self._apply_style()
        self.update_project_ui()

    def _apply_style(self) -> None:
        self.setStyleSheet(f"""
            QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; font-family: 'Segoe UI'; font-size: 10pt; }}
            QToolBar {{ background: #111620; border: 0; spacing: 6px; padding: 6px; }}
            QToolButton, QPushButton {{ background: #272f3e; border: 0; border-radius: 5px; padding: 7px 11px; }}
            QToolButton:hover, QPushButton:hover {{ background: #354156; }}
            QPushButton#renderButton {{ background: #ff4f72; color: white; font-weight: 600; padding: 11px; }}
            QPushButton#renderButton:hover {{ background: #ff6b88; }}
            QListWidget, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: #10141c; border: 1px solid #2b3445; border-radius: 4px; padding: 5px; }}
            QListWidget::item:selected {{ background: #353f53; border: 1px solid #ff4f72; }}
            QGroupBox {{ border: 1px solid #2b3445; border-radius: 6px; margin-top: 10px; padding-top: 10px; font-weight: 600; }}
            QGroupBox::title {{ subcontrol-origin: margin; left: 9px; padding: 0 4px; }}
            QSlider::groove:horizontal {{ height: 5px; background: #303848; border-radius: 2px; }}
            QSlider::handle:horizontal {{ width: 14px; margin: -5px 0; background: #ff4f72; border-radius: 7px; }}
            QProgressBar {{ background: #272f3e; border: 0; border-radius: 4px; text-align: center; }}
            QProgressBar::chunk {{ background: #ff4f72; border-radius: 4px; }}
            QScrollBar {{ background: #111620; }}
        """)

    def _build_actions(self) -> None:
        self.new_action = QAction("Новый проект", self); self.new_action.setShortcut(QKeySequence.StandardKey.New); self.new_action.triggered.connect(self.new_project)
        self.open_action = QAction("Открыть проект", self); self.open_action.setShortcut(QKeySequence.StandardKey.Open); self.open_action.triggered.connect(self.open_project)
        self.save_action = QAction("Сохранить", self); self.save_action.setShortcut(QKeySequence.StandardKey.Save); self.save_action.triggered.connect(self.save_project)
        self.import_frames_action = QAction("Открыть кадры", self); self.import_frames_action.triggered.connect(self.import_frames_dialog)
        self.import_video_action = QAction("Открыть видео", self); self.import_video_action.triggered.connect(self.import_video_dialog)
        self.undo_action = QAction("Отменить", self); self.undo_action.setShortcut(QKeySequence.StandardKey.Undo); self.undo_action.triggered.connect(self.undo)
        self.redo_action = QAction("Повторить", self); self.redo_action.setShortcut(QKeySequence.StandardKey.Redo); self.redo_action.triggered.connect(self.redo)
        self.delete_action = QAction("Удалить слой", self); self.delete_action.setShortcut(QKeySequence.StandardKey.Delete); self.delete_action.triggered.connect(self.delete_selected_layer)
        self.settings_action = QAction("Настройки", self); self.settings_action.triggered.connect(self.show_settings)
        self.export_frames_action = QAction("Сохранить кадры…", self); self.export_frames_action.triggered.connect(self.export_rendered_frames)
        self.export_sheet_action = QAction("Сохранить спрайтшит…", self); self.export_sheet_action.triggered.connect(self.export_sprite_sheet)
        self.clear_result_action = QAction("Удалить результат", self); self.clear_result_action.triggered.connect(self.clear_render_result)
        self.restart_action = QAction("Начать сначала", self); self.restart_action.triggered.connect(self.restart_project)
        for action in (self.new_action, self.open_action, self.save_action, self.import_frames_action, self.import_video_action, self.undo_action, self.redo_action, self.delete_action, self.settings_action, self.export_frames_action, self.export_sheet_action, self.clear_result_action, self.restart_action):
            self.addAction(action)

    def _build_ui(self) -> None:
        toolbar = QToolBar("Проект", self); toolbar.setMovable(False); self.addToolBar(toolbar)
        for action in (self.new_action, self.open_action, self.save_action): toolbar.addAction(action)
        toolbar.addSeparator(); toolbar.addAction(self.import_frames_action); toolbar.addAction(self.import_video_action)
        toolbar.addSeparator(); toolbar.addAction(self.undo_action); toolbar.addAction(self.redo_action)
        toolbar.addSeparator(); toolbar.addAction(self.settings_action)
        toolbar.addSeparator(); toolbar.addAction(self.export_frames_action); toolbar.addAction(self.export_sheet_action)
        toolbar.addSeparator(); toolbar.addAction(self.clear_result_action); toolbar.addAction(self.restart_action)

        central = QWidget(); root = QVBoxLayout(central); root.setContentsMargins(10, 10, 10, 8); root.setSpacing(8); self.setCentralWidget(central)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.viewer = FrameView(); splitter.addWidget(self.viewer)
        self.viewer.assetDropped.connect(lambda asset, point: self.add_layer(asset, self.current_frame(), point))
        self.viewer.filesDropped.connect(lambda files, point: self.handle_source_or_keyframe_drop(files, point=point))
        self.viewer.layerChanged.connect(self.on_layer_changed)
        self.viewer.interactionStarted.connect(lambda: None)
        self.viewer.selectionChanged.connect(self.select_layer)

        side = QWidget(); side.setMinimumWidth(295); side.setMaximumWidth(380); side_layout = QVBoxLayout(side); side_layout.setContentsMargins(12, 4, 4, 4)
        heading = QLabel("KEYFRAMES"); heading.setStyleSheet("font-weight: 700; font-size: 12pt; color: #ff6b88;"); side_layout.addWidget(heading)
        hint = QLabel("Перетащите PNG/JPG сюда, затем на кадр или в окно просмотра."); hint.setWordWrap(True); hint.setStyleSheet(f"color:{MUTED};"); side_layout.addWidget(hint)
        self.assets = AssetList(); self.assets.filesDropped.connect(lambda files: self.import_keyframes(files)); self.assets.itemDoubleClicked.connect(self.asset_double_clicked); side_layout.addWidget(self.assets, 2)
        asset_buttons = QHBoxLayout(); import_asset = QPushButton("+ Добавить"); import_asset.clicked.connect(self.import_keyframe_dialog); remove_asset = QPushButton("Удалить"); remove_asset.clicked.connect(self.remove_asset); asset_buttons.addWidget(import_asset); asset_buttons.addWidget(remove_asset); side_layout.addLayout(asset_buttons)
        side_layout.addWidget(QLabel("СЛОИ ТЕКУЩЕГО КАДРА"))
        self.layers = QListWidget(); self.layers.setMaximumHeight(130); self.layers.currentItemChanged.connect(self.layer_list_selected); side_layout.addWidget(self.layers)
        layer_buttons = QHBoxLayout(); self.lower_button = QPushButton("↓ Ниже"); self.raise_button = QPushButton("↑ Выше"); self.delete_layer_button = QPushButton("Удалить"); self.lower_button.clicked.connect(lambda: self.move_layer(-1)); self.raise_button.clicked.connect(lambda: self.move_layer(1)); self.delete_layer_button.clicked.connect(self.delete_selected_layer); layer_buttons.addWidget(self.lower_button); layer_buttons.addWidget(self.raise_button); layer_buttons.addWidget(self.delete_layer_button); side_layout.addLayout(layer_buttons)
        properties = QGroupBox("Трансформация"); form = QFormLayout(properties)
        self.x_field = self._double_field(-100000, 100000, 1); self.y_field = self._double_field(-100000, 100000, 1)
        self.scale_field = self._double_field(0.02, 20, 0.01); self.rotation_field = self._double_field(-360, 360, 1)
        self.opacity_slider = QSlider(Qt.Orientation.Horizontal); self.opacity_slider.setRange(0, 100)
        form.addRow("X", self.x_field); form.addRow("Y", self.y_field); form.addRow("Масштаб", self.scale_field); form.addRow("Поворот", self.rotation_field); form.addRow("Прозрачность", self.opacity_slider)
        for field in (self.x_field, self.y_field, self.scale_field, self.rotation_field): field.valueChanged.connect(self.property_changed)
        self.opacity_slider.valueChanged.connect(self.property_changed)
        side_layout.addWidget(properties)
        self.render_button = QPushButton("Запустить EbSynth"); self.render_button.setObjectName("renderButton"); self.render_button.clicked.connect(self.start_render); side_layout.addWidget(self.render_button)
        self.stop_button = QPushButton("Остановить"); self.stop_button.clicked.connect(self.stop_render); self.stop_button.hide(); side_layout.addWidget(self.stop_button)
        splitter.addWidget(side); splitter.setStretchFactor(0, 1); root.addWidget(splitter, 1)

        controls = QHBoxLayout()
        self.prev_button = QPushButton("◀"); self.play_button = QPushButton("▶"); self.next_button = QPushButton("▶|")
        self.prev_button.clicked.connect(lambda: self.set_frame(self.current_frame() - 1)); self.play_button.clicked.connect(self.toggle_playback); self.next_button.clicked.connect(lambda: self.set_frame(self.current_frame() + 1))
        self.frame_label = QLabel("Кадр — / —"); self.frame_label.setMinimumWidth(120)
        controls.addWidget(self.prev_button); controls.addWidget(self.play_button); controls.addWidget(self.next_button); controls.addWidget(self.frame_label)
        controls.addStretch(); controls.addWidget(QLabel("FPS")); self.fps_field = QDoubleSpinBox(); self.fps_field.setRange(1, 120); self.fps_field.setDecimals(2); self.fps_field.setValue(12); self.fps_field.valueChanged.connect(self.fps_changed); controls.addWidget(self.fps_field)
        self.loop_check = QCheckBox("Цикл"); self.loop_check.setChecked(True); controls.addWidget(self.loop_check)
        controls.addWidget(QLabel("Просмотр")); self.mode_combo = QComboBox(); self.mode_combo.addItems(["Композиция", "Исходник", "Результат"]); self.mode_combo.currentTextChanged.connect(self.refresh_viewer); controls.addWidget(self.mode_combo)
        root.addLayout(controls)
        scroll = QScrollArea(); scroll.setWidgetResizable(False); scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded); scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.timeline = Timeline(); self.timeline.frameSelected.connect(self.set_frame); self.timeline.assetDropped.connect(lambda asset, index: self.add_layer(asset, index)); self.timeline.filesDropped.connect(lambda files, index: self.handle_source_or_keyframe_drop(files, frame_index=index)); scroll.setWidget(self.timeline); self.timeline_scroll = scroll; root.addWidget(scroll)

        status = QStatusBar(); self.setStatusBar(status); self.status_label = QLabel("Создайте проект и откройте кадры"); status.addWidget(self.status_label, 1)
        self.progress = QProgressBar(); self.progress.setFixedWidth(260); self.progress.hide(); status.addPermanentWidget(self.progress)

    @staticmethod
    def _double_field(low: float, high: float, step: float) -> QDoubleSpinBox:
        field = QDoubleSpinBox(); field.setRange(low, high); field.setSingleStep(step); field.setDecimals(3); return field

    def current_frame(self) -> int:
        return self.project.playhead if self.project else 0

    def ensure_project(self, automatic: bool = False) -> bool:
        if self.project:
            return True
        return self.create_automatic_project() if automatic else self.new_project()

    @staticmethod
    def automatic_projects_root() -> Path:
        location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
        return Path(location) / "Projects"

    def create_automatic_project(self) -> bool:
        try:
            base = self.automatic_projects_root()
            stamp = time.strftime("%Y%m%d-%H%M%S")
            suffix = time.time_ns() % 1_000_000
            project_root = base / f"Auto-{stamp}-{suffix:06d}"
            self.project = Project.create(project_root)
            self.reset_history()
            self.update_project_ui()
            self.status_label.setText(f"Рабочий проект создан автоматически: {project_root}")
            return True
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, f"Не удалось создать рабочий проект: {exc}")
            return False

    def new_project(self) -> bool:
        folder = QFileDialog.getExistingDirectory(self, "Выберите пустую папку проекта")
        if not folder:
            return False
        root = Path(folder)
        existing = root / PROJECT_FILE
        if existing.is_file():
            answer = QMessageBox.question(self, APP_NAME, "В этой папке уже есть проект. Открыть его?")
            if answer == QMessageBox.StandardButton.Yes:
                return self.load_project(existing)
            return False
        try:
            self.project = Project.create(root)
            self.reset_history()
            self.update_project_ui()
            return True
        except Exception as exc:
            QMessageBox.critical(self, APP_NAME, str(exc)); return False

    def open_project(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(self, "Открыть проект", filter="EbSynth project (project.ebsynth.json *.ebsynth.json);;JSON (*.json)")
        if file_name:
            self.load_project(Path(file_name))

    def load_project(self, path: Path) -> bool:
        try:
            self.project = Project.load(path)
            self.fps_field.setValue(self.project.fps)
            self.reset_history(); self.update_project_ui(); self.set_frame(self.project.playhead)
            self.status_label.setText(f"Проект открыт: {self.project.name}")
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Не удалось открыть проект", str(exc)); return False

    def save_project(self) -> None:
        if not self.project:
            return
        try:
            self.project.save(); self.status_label.setText("Проект сохранён")
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка сохранения", str(exc))

    def schedule_save(self) -> None:
        self.autosave.start(600)

    def import_frames_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Открыть кадры", filter="Images (*.png *.jpg *.jpeg *.bmp *.tga *.webp)")
        if files:
            self.import_frames([Path(path) for path in files])

    def import_frames(self, paths: list[Path], automatic_project: bool = False) -> None:
        if not self.ensure_project(automatic_project) or not self.project:
            return
        try:
            ordered = image_files(paths)
            verify_sequence(ordered)
            copied = copy_sequence(self.project, ordered)
            self.project.width, self.project.height = verify_sequence(copied)
            self.project.fps = self.fps_field.value()
            self.project.save(); self.reset_history(); self.update_project_ui(); self.set_frame(0)
            self.status_label.setText(f"Импортировано кадров: {len(copied)}")
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка импорта", str(exc))

    def import_video_dialog(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(self, "Открыть видео", filter="Video (*.mp4 *.mov *.mkv *.avi *.webm *.m4v)")
        if file_name:
            self.import_video(Path(file_name))

    def import_video(self, source: Path, automatic_project: bool = False) -> None:
        if not self.ensure_project(automatic_project) or not self.project:
            return
        ffmpeg, ffprobe = locate_tool("ffmpeg.exe"), locate_tool("ffprobe.exe")
        if not ffmpeg or not ffprobe:
            QMessageBox.critical(self, APP_NAME, "Не найдены ffmpeg.exe и ffprobe.exe.")
            return
        self.set_busy(True, "Извлекаю кадры из видео…")
        self.import_worker = VideoImportWorker(self.project, source, ffmpeg, ffprobe)
        self.import_worker.completed.connect(self.video_imported)
        self.import_worker.failed.connect(self.worker_failed)
        self.import_worker.start()

    def video_imported(self, frames: list[str], fps: float, width: int, height: int, source_name: str) -> None:
        if not self.project:
            return
        self.project.frames = frames; self.project.fps = fps; self.project.width = width; self.project.height = height
        self.project.source_kind = "video"; self.project.source_name = source_name; self.project.playhead = 0; self.project.compositions.clear(); self.project.save()
        self.fps_field.setValue(fps); self.set_busy(False); self.reset_history(); self.update_project_ui(); self.set_frame(0)
        self.status_label.setText(f"Видео импортировано: {len(frames)} кадров, {fps:.2f} FPS")

    def worker_failed(self, message: str) -> None:
        self.set_busy(False); QMessageBox.critical(self, APP_NAME, message)

    def import_keyframe_dialog(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, "Добавить keyframes", filter="Images (*.png *.jpg *.jpeg *.bmp *.tga *.webp)")
        if files:
            self.import_keyframes([Path(path) for path in files])

    def handle_source_or_keyframe_drop(
        self,
        paths: list[Path],
        point: QPointF | None = None,
        frame_index: int | None = None,
    ) -> None:
        """Import dropped images as source frames until a source is loaded."""
        if not self.project or not self.project.frames:
            self.import_frames(paths, automatic_project=True)
            return
        target_frame = self.current_frame() if frame_index is None else frame_index
        self.import_keyframes(paths, target_frame, point)

    def import_keyframes(self, paths: list[Path], frame_index: int | None = None, point: QPointF | None = None) -> None:
        if not self.project or not self.project.frames:
            QMessageBox.information(self, APP_NAME, "Сначала откройте исходные кадры или видео."); return
        imported: list[Asset] = []
        try:
            for path in image_files(paths):
                imported.append(self.project.add_asset(path))
            self.project.save(); self.populate_assets()
            if frame_index is not None:
                for asset in imported:
                    self.add_layer(asset.id, frame_index, point)
        except Exception as exc:
            QMessageBox.critical(self, "Ошибка keyframe", str(exc))

    def asset_double_clicked(self, item: QListWidgetItem) -> None:
        self.add_layer(str(item.data(Qt.ItemDataRole.UserRole)), self.current_frame())

    def add_layer(self, asset_id: str, frame_index: int, point: QPointF | None = None) -> None:
        if not self.project or asset_id not in self.project.assets or not self.project.frames:
            return
        frame_index = max(0, min(frame_index, self.project.frame_count - 1))
        width, height = read_image_size(self.project.asset_path(asset_id))
        scale = min(1.0, self.project.width * 0.8 / max(width, 1), self.project.height * 0.8 / max(height, 1))
        center = point or QPointF(self.project.width / 2, self.project.height / 2)
        layers = self.project.compositions.setdefault(frame_index, [])
        layers.append(Layer.create(asset_id, center.x(), center.y(), scale, len(layers)))
        self.project.playhead = frame_index; self.record_history(); self.schedule_save(); self.update_project_ui(); self.set_frame(frame_index)

    def remove_asset(self) -> None:
        item = self.assets.currentItem()
        if not self.project or not item:
            return
        asset_id = str(item.data(Qt.ItemDataRole.UserRole))
        if not self.project.remove_asset(asset_id):
            QMessageBox.information(self, APP_NAME, "Сначала удалите все слои, использующие этот keyframe."); return
        self.project.save(); self.populate_assets()

    def select_layer(self, layer: Layer | None) -> None:
        self._updating_properties = True
        enabled = layer is not None
        for widget in (self.x_field, self.y_field, self.scale_field, self.rotation_field, self.opacity_slider, self.raise_button, self.lower_button, self.delete_layer_button):
            widget.setEnabled(enabled)
        if layer:
            self.x_field.setValue(layer.center_x); self.y_field.setValue(layer.center_y); self.scale_field.setValue(layer.scale); self.rotation_field.setValue(layer.rotation); self.opacity_slider.setValue(round(layer.opacity * 100))
            for row in range(self.layers.count()):
                item = self.layers.item(row)
                if item.data(Qt.ItemDataRole.UserRole) == layer.id:
                    self.layers.setCurrentItem(item); break
        else:
            self.layers.clearSelection()
        self._updating_properties = False

    def selected_layer(self) -> Layer | None:
        if not self.project:
            return None
        item = self.layers.currentItem()
        if not item:
            selected = [entry for entry in self.viewer.scene_model.selectedItems() if isinstance(entry, LayerItem)]
            return selected[0].layer if selected else None
        layer_id = item.data(Qt.ItemDataRole.UserRole)
        return next((layer for layer in self.project.compositions.get(self.current_frame(), []) if layer.id == layer_id), None)

    def layer_list_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if self._updating_properties or not current:
            return
        layer_id = current.data(Qt.ItemDataRole.UserRole)
        item = self.viewer.layer_items.get(layer_id)
        if item:
            self.viewer.scene_model.clearSelection(); item.setSelected(True)

    def property_changed(self, _value=None) -> None:  # type: ignore[no-untyped-def]
        if self._updating_properties:
            return
        layer = self.selected_layer()
        if not layer:
            return
        layer.center_x = self.x_field.value(); layer.center_y = self.y_field.value(); layer.scale = self.scale_field.value(); layer.rotation = self.rotation_field.value(); layer.opacity = self.opacity_slider.value() / 100
        item = self.viewer.layer_items.get(layer.id)
        if item: item.sync_from_layer(); item.update()
        self.record_history(); self.schedule_save()

    def on_layer_changed(self, layer: Layer) -> None:
        self.select_layer(layer); self.record_history(); self.schedule_save(); self.timeline.update()

    def delete_selected_layer(self) -> None:
        if not self.project:
            return
        layer = self.selected_layer()
        if not layer:
            return
        index = self.current_frame(); self.project.compositions[index] = [value for value in self.project.compositions.get(index, []) if value.id != layer.id]; self.project.normalize_z(index)
        self.record_history(); self.schedule_save(); self.update_project_ui(); self.refresh_viewer()

    def move_layer(self, direction: int) -> None:
        if not self.project:
            return
        layer = self.selected_layer(); layers = self.project.compositions.get(self.current_frame(), [])
        if not layer or layer not in layers: return
        position = layers.index(layer); target = max(0, min(position + direction, len(layers) - 1))
        if target == position: return
        layers[position], layers[target] = layers[target], layers[position]; self.project.normalize_z(self.current_frame()); self.record_history(); self.schedule_save(); self.update_project_ui(); self.refresh_viewer()

    def reset_history(self) -> None:
        self._history = []; self._history_index = -1; self.record_history()

    def compositions_state(self) -> str:
        if not self.project: return "{}"
        return json.dumps({str(key): [layer_dict(layer) for layer in layers] for key, layers in self.project.compositions.items()}, sort_keys=True)

    def record_history(self) -> None:
        state = self.compositions_state()
        if self._history_index >= 0 and self._history[self._history_index] == state: return
        self._history = self._history[: self._history_index + 1] + [state]
        if len(self._history) > 100: self._history.pop(0)
        self._history_index = len(self._history) - 1; self.update_history_actions()

    def apply_history(self) -> None:
        if not self.project: return
        data = json.loads(self._history[self._history_index]); self.project.compositions = {int(key): [Layer(**layer) for layer in layers] for key, layers in data.items()}; self.schedule_save(); self.update_project_ui(); self.refresh_viewer(); self.update_history_actions()

    def undo(self) -> None:
        if self._history_index > 0: self._history_index -= 1; self.apply_history()

    def redo(self) -> None:
        if self._history_index + 1 < len(self._history): self._history_index += 1; self.apply_history()

    def update_history_actions(self) -> None:
        self.undo_action.setEnabled(self._history_index > 0); self.redo_action.setEnabled(0 <= self._history_index < len(self._history) - 1)

    def set_frame(self, index: int) -> None:
        if not self.project or not self.project.frames: return
        self.project.playhead = max(0, min(index, self.project.frame_count - 1)); self.timeline.set_playhead(self.project.playhead); self.frame_label.setText(f"Кадр {self.project.playhead + 1} / {self.project.frame_count}"); self.refresh_viewer(); self.populate_layers(); self.schedule_save()
        x = self.project.playhead * self.timeline.CELL_W; self.timeline_scroll.horizontalScrollBar().setValue(max(0, x - self.timeline_scroll.viewport().width() // 2))

    def refresh_viewer(self, _value=None) -> None:  # type: ignore[no-untyped-def]
        if self.project and self.project.frames: self.viewer.set_frame(self.project, self.current_frame(), self.mode_combo.currentText())
        else: self.viewer.set_frame(None, 0, "Композиция")

    def toggle_playback(self) -> None:
        if self.play_timer.isActive(): self.play_timer.stop(); self.play_button.setText("▶")
        elif self.project and self.project.frames:
            self.play_timer.start(max(1, round(1000 / self.fps_field.value()))); self.play_button.setText("Ⅱ")

    def advance_playback(self) -> None:
        if not self.project: return
        next_frame = self.current_frame() + 1
        if next_frame >= self.project.frame_count:
            if self.loop_check.isChecked(): next_frame = 0
            else: self.toggle_playback(); return
        self.set_frame(next_frame)

    def fps_changed(self, value: float) -> None:
        if self.project: self.project.fps = value; self.schedule_save()
        if self.play_timer.isActive(): self.play_timer.setInterval(max(1, round(1000 / value)))

    def populate_assets(self) -> None:
        self.assets.clear()
        if not self.project: return
        for asset in self.project.assets.values():
            pixmap = QPixmap(str(self.project.asset_path(asset.id))).scaled(86, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            item = QListWidgetItem(QIcon(pixmap), asset.name); item.setData(Qt.ItemDataRole.UserRole, asset.id); item.setToolTip(asset.name); self.assets.addItem(item)

    def populate_layers(self) -> None:
        self._updating_properties = True; self.layers.clear()
        if self.project:
            for layer in sorted(self.project.compositions.get(self.current_frame(), []), key=lambda value: value.z):
                asset = self.project.assets.get(layer.asset_id); item = QListWidgetItem(f"{layer.z + 1}. {asset.name if asset else 'Missing'}"); item.setData(Qt.ItemDataRole.UserRole, layer.id); self.layers.addItem(item)
        self._updating_properties = False; self.select_layer(None)

    def update_project_ui(self) -> None:
        self.populate_assets(); self.populate_layers(); self.timeline.set_project(self.project); self.refresh_viewer()
        has_frames = bool(self.project and self.project.frames)
        has_render = bool(self.rendered_frame_paths())
        has_result_files = bool(
            self.project
            and any(folder.is_dir() and any(folder.iterdir()) for folder in (self.project.output_dir, self.project.styles_dir))
        )
        for widget in (self.play_button, self.prev_button, self.next_button, self.render_button, self.mode_combo): widget.setEnabled(has_frames)
        self.export_frames_action.setEnabled(has_render)
        self.export_sheet_action.setEnabled(has_render)
        self.clear_result_action.setEnabled(has_result_files)
        self.restart_action.setEnabled(bool(self.project))
        if self.project and has_frames:
            self.frame_label.setText(f"Кадр {self.current_frame() + 1} / {self.project.frame_count}"); self.setWindowTitle(f"{APP_NAME} — {self.project.name}")
        else: self.frame_label.setText("Кадр — / —"); self.setWindowTitle(APP_NAME)
        self.update_history_actions()

    def show_settings(self) -> None:
        if not self.project: QMessageBox.information(self, APP_NAME, "Сначала создайте проект."); return
        dialog = SettingsDialog(self.project, self)
        if dialog.exec() == QDialog.DialogCode.Accepted: dialog.apply(); self.project.save()

    def rendered_frame_paths(self) -> list[Path]:
        if not self.project or not self.project.frames:
            return []
        paths = [self.project.output_dir / f"frame_{index + 1:06d}.png" for index in range(self.project.frame_count)]
        return paths if all(path.is_file() for path in paths) else []

    def export_rendered_frames(self) -> None:
        frames = self.rendered_frame_paths()
        if not frames:
            QMessageBox.information(self, APP_NAME, "Сначала завершите рендер всех кадров.")
            return
        folder = QFileDialog.getExistingDirectory(self, "Выберите папку для готовых кадров")
        if not folder:
            return
        destination = Path(folder).resolve()
        if destination == frames[0].parent.resolve():
            QMessageBox.information(self, APP_NAME, f"Кадры уже находятся в этой папке:\n{destination}")
            return
        existing = [destination / path.name for path in frames if (destination / path.name).exists()]
        if existing:
            answer = QMessageBox.question(
                self,
                APP_NAME,
                f"Заменить существующие кадры? Файлов: {len(existing)}.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            destination.mkdir(parents=True, exist_ok=True)
            for path in frames:
                shutil.copy2(path, destination / path.name)
            self.status_label.setText(f"Сохранено кадров: {len(frames)}")
            QMessageBox.information(self, APP_NAME, f"Кадры сохранены:\n{destination}")
        except OSError as exc:
            QMessageBox.critical(self, APP_NAME, f"Не удалось сохранить кадры: {exc}")

    def export_sprite_sheet(self) -> None:
        frames = self.rendered_frame_paths()
        if not frames:
            QMessageBox.information(self, APP_NAME, "Сначала завершите рендер всех кадров.")
            return
        default_path = str(self.project.root / "spritesheet.png") if self.project else "spritesheet.png"
        file_name, _ = QFileDialog.getSaveFileName(self, "Сохранить спрайтшит", default_path, "PNG (*.png)")
        if not file_name:
            return
        columns, accepted = QInputDialog.getInt(
            self,
            "Размер спрайтшита",
            "Колонок:",
            max(1, math.ceil(math.sqrt(len(frames)))),
            1,
            len(frames),
        )
        if not accepted:
            return
        destination = Path(file_name)
        if destination.suffix.casefold() != ".png":
            destination = destination.with_suffix(".png")
        try:
            columns, rows = build_sprite_sheet(frames, columns, destination)
            self.status_label.setText(f"Спрайтшит сохранён: {columns} × {rows} ячеек")
            QMessageBox.information(self, APP_NAME, f"Спрайтшит сохранён:\n{destination}")
        except (OSError, RuntimeError, ValueError) as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))

    def clear_render_result(self) -> None:
        if not self.project:
            QMessageBox.information(self, APP_NAME, "Нет открытого проекта.")
            return
        if self.render_worker and self.render_worker.isRunning():
            QMessageBox.information(self, APP_NAME, "Сначала остановите текущий рендер.")
            return
        folders = (self.project.output_dir, self.project.styles_dir)
        if not any(folder.is_dir() and any(folder.iterdir()) for folder in folders):
            self.status_label.setText("Сохранённого результата пока нет")
            return
        answer = QMessageBox.question(
            self,
            APP_NAME,
            "Удалить все сгенерированные кадры? Исходник и keyframes сохранятся.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            for folder in folders:
                if folder.exists():
                    shutil.rmtree(folder)
                folder.mkdir(parents=True, exist_ok=True)
            if self.mode_combo.currentText() == "Результат":
                self.mode_combo.setCurrentText("Композиция")
            self.update_project_ui()
            self.status_label.setText("Результат удалён. Можно запустить EbSynth заново.")
        except OSError as exc:
            QMessageBox.critical(self, APP_NAME, f"Не удалось удалить результат: {exc}")

    def restart_project(self) -> None:
        if not self.project:
            self.status_label.setText("Проект уже пуст — перетащите кадры анимации")
            return
        if self.render_worker and self.render_worker.isRunning():
            QMessageBox.information(self, APP_NAME, "Сначала остановите текущий рендер.")
            return
        answer = QMessageBox.question(
            self,
            APP_NAME,
            "Начать сначала? Копии исходных кадров, keyframes и результат в папке проекта будут удалены.",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        project = self.project
        try:
            self.play_timer.stop()
            self.autosave.stop()
            managed_folders = (project.frames_dir, project.assets_dir, project.source_dir, project.styles_dir, project.output_dir)
            for folder in managed_folders:
                if folder.exists():
                    shutil.rmtree(folder)
            if project.file_path.exists():
                project.file_path.unlink()
            for folder in (project.root / "media", project.root / "cache"):
                if folder.is_dir() and not any(folder.iterdir()):
                    folder.rmdir()
            self.project = None
            self._history = []
            self._history_index = -1
            self.mode_combo.setCurrentText("Композиция")
            self.update_project_ui()
            self.status_label.setText("Готово. Перетащите кадры новой анимации.")
        except OSError as exc:
            QMessageBox.critical(self, APP_NAME, f"Не удалось очистить проект: {exc}")

    def start_render(self) -> None:
        if not self.project or not self.project.frames: return
        if not self.project.keyframe_indices(): QMessageBox.information(self, APP_NAME, "Перетащите хотя бы один keyframe на кадр."); return
        engine = locate_tool("ebsynth.exe")
        if not engine: QMessageBox.critical(self, APP_NAME, "Не найден bin\\ebsynth.exe."); return
        self.project.save(); self.set_busy(True, "Подготовка рендера…"); self.render_button.hide(); self.stop_button.show(); self.progress.setRange(0, self.project.frame_count); self.progress.show()
        self.render_worker = RenderWorker(self.project, engine); self.render_worker.progress.connect(self.render_progress); self.render_worker.completed.connect(self.render_completed); self.render_worker.failed.connect(self.render_failed); self.render_worker.cancelled.connect(self.render_cancelled); self.render_worker.start()

    def render_progress(self, done: int, total: int, frame: int, key: int) -> None:
        self.progress.setMaximum(total); self.progress.setValue(done); self.status_label.setText(f"Кадр {frame + 1}/{total} ← keyframe {key + 1}")

    def render_completed(self, count: int, elapsed: float) -> None:
        self.finish_render(); self.mode_combo.setCurrentText("Результат"); self.refresh_viewer(); QMessageBox.information(self, APP_NAME, f"Готово: {count} PNG за {elapsed:.1f} сек.\n\n{self.project.output_dir if self.project else ''}")

    def render_failed(self, message: str) -> None:
        self.finish_render(); QMessageBox.critical(self, "Ошибка EbSynth", message)

    def render_cancelled(self) -> None:
        self.finish_render(); self.status_label.setText("Рендер остановлен")

    def stop_render(self) -> None:
        if self.render_worker: self.render_worker.stop(); self.stop_button.setEnabled(False)

    def finish_render(self) -> None:
        self.render_worker = None; self.set_busy(False); self.render_button.show(); self.stop_button.hide(); self.stop_button.setEnabled(True); self.progress.hide()

    def set_busy(self, busy: bool, message: str = "") -> None:
        for action in (self.new_action, self.open_action, self.import_frames_action, self.import_video_action): action.setEnabled(not busy)
        if busy:
            for action in (self.export_frames_action, self.export_sheet_action, self.clear_result_action, self.restart_action):
                action.setEnabled(False)
        else:
            self.update_project_ui()
        if message: self.status_label.setText(message)

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.mimeData().hasUrls(): event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        paths = [Path(url.toLocalFile()) for url in event.mimeData().urls()]
        videos = [path for path in paths if path.suffix.casefold() in VIDEO_EXTENSIONS]
        images = image_files(paths)
        if not self.project or not self.project.frames:
            if videos: self.import_video(videos[0], automatic_project=True)
            elif images: self.import_frames(images, automatic_project=True)
        elif images:
            self.import_keyframes(images)
        event.acceptProposedAction()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.render_worker and self.render_worker.isRunning():
            answer = QMessageBox.question(self, APP_NAME, "Рендер идёт. Остановить его и выйти?")
            if answer != QMessageBox.StandardButton.Yes: event.ignore(); return
            self.render_worker.stop(); self.render_worker.wait(3000)
        self.save_project(); event.accept()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setStyle("Fusion")
    window = MainWindow(); window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
