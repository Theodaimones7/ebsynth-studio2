"""Persistent project model for the EbSynth Studio timeline editor."""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable


PROJECT_VERSION = 2
PROJECT_FILE = "project.ebsynth.json"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp"}
_NATURAL_PARTS = re.compile(r"(\d+)")


def natural_key(value: str | Path) -> tuple[object, ...]:
    text = Path(value).name.casefold()
    return tuple(int(part) if part.isdigit() else part for part in _NATURAL_PARTS.split(text))


def image_files(paths: Iterable[Path]) -> list[Path]:
    files = [Path(path) for path in paths if Path(path).is_file() and Path(path).suffix.casefold() in IMAGE_EXTENSIONS]
    return sorted(files, key=natural_key)


@dataclass
class Layer:
    id: str
    asset_id: str
    center_x: float
    center_y: float
    scale: float = 1.0
    rotation: float = 0.0
    opacity: float = 1.0
    z: int = 0

    @classmethod
    def create(cls, asset_id: str, center_x: float, center_y: float, scale: float, z: int) -> "Layer":
        return cls(uuid.uuid4().hex, asset_id, center_x, center_y, scale, 0.0, 1.0, z)


@dataclass
class Asset:
    id: str
    name: str
    path: str


@dataclass
class SynthesisSettings:
    style_weight: float = 1.0
    guide_weight: float = 1.0
    uniformity: float = 3500.0
    patch_size: int = 5
    search_vote_iters: int = 6
    patch_match_iters: int = 4
    stop_threshold: int = 5
    pyramid_levels: int | None = None
    extra_pass: bool = False
    overwrite: bool = True


@dataclass
class Project:
    root: Path
    name: str
    fps: float = 12.0
    width: int = 0
    height: int = 0
    source_kind: str = "sequence"
    source_name: str = ""
    frames: list[str] = field(default_factory=list)
    assets: dict[str, Asset] = field(default_factory=dict)
    compositions: dict[int, list[Layer]] = field(default_factory=dict)
    settings: SynthesisSettings = field(default_factory=SynthesisSettings)
    playhead: int = 0

    @property
    def file_path(self) -> Path:
        return self.root / PROJECT_FILE

    @property
    def frame_count(self) -> int:
        return len(self.frames)

    @property
    def frames_dir(self) -> Path:
        return self.root / "media" / "frames"

    @property
    def assets_dir(self) -> Path:
        return self.root / "media" / "keyframes"

    @property
    def source_dir(self) -> Path:
        return self.root / "media" / "source"

    @property
    def styles_dir(self) -> Path:
        return self.root / "cache" / "styles"

    @property
    def output_dir(self) -> Path:
        return self.root / "output"

    def ensure_directories(self) -> None:
        for folder in (self.frames_dir, self.assets_dir, self.source_dir, self.styles_dir, self.output_dir):
            folder.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative: str) -> Path:
        return (self.root / relative).resolve()

    def frame_path(self, index: int) -> Path:
        return self.resolve(self.frames[index])

    def asset_path(self, asset_id: str) -> Path:
        return self.resolve(self.assets[asset_id].path)

    def keyframe_indices(self) -> list[int]:
        return sorted(index for index, layers in self.compositions.items() if layers and 0 <= index < self.frame_count)

    def nearest_keyframe(self, frame_index: int) -> int:
        keys = self.keyframe_indices()
        if not keys:
            raise ValueError("Добавьте хотя бы один keyframe.")
        return min(keys, key=lambda key: (abs(key - frame_index), key))

    def influence_map(self) -> dict[int, int]:
        return {index: self.nearest_keyframe(index) for index in range(self.frame_count)} if self.frame_count else {}

    def normalize_z(self, frame_index: int) -> None:
        layers = sorted(self.compositions.get(frame_index, []), key=lambda layer: layer.z)
        for z, layer in enumerate(layers):
            layer.z = z
        if layers:
            self.compositions[frame_index] = layers
        else:
            self.compositions.pop(frame_index, None)

    def add_asset(self, source: Path) -> Asset:
        self.ensure_directories()
        source = source.resolve()
        asset_id = uuid.uuid4().hex
        safe_name = re.sub(r"[^\w. -]+", "_", source.name, flags=re.UNICODE)
        destination = self.assets_dir / f"{asset_id[:8]}_{safe_name}"
        shutil.copy2(source, destination)
        asset = Asset(asset_id, source.stem, destination.relative_to(self.root).as_posix())
        self.assets[asset_id] = asset
        return asset

    def remove_asset(self, asset_id: str) -> bool:
        if any(layer.asset_id == asset_id for layers in self.compositions.values() for layer in layers):
            return False
        asset = self.assets.pop(asset_id, None)
        if asset:
            try:
                self.resolve(asset.path).unlink()
            except OSError:
                pass
        return asset is not None

    def to_dict(self) -> dict[str, object]:
        return {
            "version": PROJECT_VERSION,
            "name": self.name,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "source_kind": self.source_kind,
            "source_name": self.source_name,
            "frames": self.frames,
            "assets": {key: asdict(value) for key, value in self.assets.items()},
            "compositions": {str(key): [asdict(layer) for layer in layers] for key, layers in self.compositions.items()},
            "settings": asdict(self.settings),
            "playhead": self.playhead,
        }

    def save(self) -> None:
        self.ensure_directories()
        temporary = self.file_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.file_path)

    @classmethod
    def create(cls, root: Path) -> "Project":
        project = cls(root.resolve(), root.name or "EbSynth Project")
        project.ensure_directories()
        project.save()
        return project

    @classmethod
    def load(cls, file_path: Path) -> "Project":
        data = json.loads(file_path.read_text(encoding="utf-8"))
        if int(data.get("version", 0)) != PROJECT_VERSION:
            raise ValueError("Неподдерживаемая версия проекта.")
        root = file_path.resolve().parent
        settings_data = data.get("settings", {})
        project = cls(
            root=root,
            name=str(data.get("name", root.name)),
            fps=float(data.get("fps", 12.0)),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            source_kind=str(data.get("source_kind", "sequence")),
            source_name=str(data.get("source_name", "")),
            frames=[str(path) for path in data.get("frames", [])],
            assets={key: Asset(**value) for key, value in dict(data.get("assets", {})).items()},
            compositions={int(key): [Layer(**layer) for layer in layers] for key, layers in dict(data.get("compositions", {})).items()},
            settings=SynthesisSettings(**settings_data),
            playhead=int(data.get("playhead", 0)),
        )
        project.playhead = max(0, min(project.playhead, max(0, project.frame_count - 1)))
        project.ensure_directories()
        return project


def copy_sequence(project: Project, sources: Iterable[Path]) -> list[Path]:
    ordered = image_files(sources)
    if not ordered:
        raise ValueError("Не выбраны изображения поддерживаемого формата.")
    project.ensure_directories()
    copied: list[Path] = []
    for index, source in enumerate(ordered, start=1):
        destination = project.frames_dir / f"frame_{index:06d}{source.suffix.casefold()}"
        shutil.copy2(source, destination)
        copied.append(destination)
    project.frames = [path.relative_to(project.root).as_posix() for path in copied]
    project.source_kind = "sequence"
    project.source_name = ordered[0].parent.name
    project.playhead = 0
    project.compositions.clear()
    return copied
