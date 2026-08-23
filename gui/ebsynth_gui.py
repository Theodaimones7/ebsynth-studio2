"""A practical batch GUI for the open-source EbSynth command-line tool.

The original binary synthesizes one image at a time.  This application adds the
video-oriented workflow: numbered frame discovery, keyframe matching, automatic
ranges, a cancellable queue, progress reporting, presets, and persistent settings.
It deliberately uses only Python's standard library so it works on a clean Windows
installation that already has Python/Tkinter.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Iterable


APP_NAME = "EbSynth Studio"
APP_VERSION = "1.0"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".bmp", ".tga"}
FRAME_NUMBER = re.compile(r"(\d+)(?!.*\d)")


@dataclass(frozen=True)
class ImageFrame:
    number: int
    path: Path


@dataclass(frozen=True)
class PlannedFrame:
    target: ImageFrame
    key_style: ImageFrame
    key_source: ImageFrame
    output: Path


@dataclass(frozen=True)
class BatchPlan:
    jobs: tuple[PlannedFrame, ...]
    frame_count: int
    keyframe_count: int
    first_frame: int
    last_frame: int


@dataclass(frozen=True)
class RunOptions:
    executable: Path
    frames_dir: Path
    keys_dir: Path
    output_dir: Path
    start_frame: int | None
    end_frame: int | None
    style_weight: float
    guide_weight: float
    uniformity: float
    patch_size: int
    pyramid_levels: int | None
    search_vote_iters: int
    patch_match_iters: int
    stop_threshold: int
    backend: str
    extra_pass: bool
    overwrite: bool


class PlanError(ValueError):
    pass


def frame_number(path: Path) -> int | None:
    """Return the last integer in an image stem, or None if it has no integer."""
    match = FRAME_NUMBER.search(path.stem)
    return int(match.group(1)) if match else None


def scan_frames(folder: Path, label: str) -> dict[int, ImageFrame]:
    if not folder.is_dir():
        raise PlanError(f"{label}: папка не найдена: {folder}")

    result: dict[int, ImageFrame] = {}
    duplicates: list[int] = []
    for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
        if not path.is_file() or path.suffix.casefold() not in IMAGE_EXTENSIONS:
            continue
        number = frame_number(path)
        if number is None:
            continue
        if number in result:
            duplicates.append(number)
        else:
            result[number] = ImageFrame(number, path.resolve())

    if duplicates:
        values = ", ".join(str(n) for n in sorted(set(duplicates))[:8])
        raise PlanError(f"{label}: одинаковые номера кадров: {values}")
    if not result:
        raise PlanError(
            f"{label}: не найдено изображений с номером в имени. "
            "Пример имени: frame_0001.png"
        )
    return result


def make_plan(options: RunOptions) -> BatchPlan:
    frames = scan_frames(options.frames_dir, "Кадры видео")
    keys = scan_frames(options.keys_dir, "Ключевые кадры")

    missing_sources = sorted(set(keys) - set(frames))
    if missing_sources:
        sample = ", ".join(str(n) for n in missing_sources[:10])
        raise PlanError(
            "Для ключевых кадров нет исходных кадров с теми же номерами: " + sample
        )

    first = options.start_frame if options.start_frame is not None else min(frames)
    last = options.end_frame if options.end_frame is not None else max(frames)
    if first > last:
        raise PlanError("Начальный кадр не может быть больше конечного.")

    selected = [frames[n] for n in sorted(frames) if first <= n <= last]
    if not selected:
        raise PlanError(f"В диапазоне {first}–{last} нет кадров.")

    key_numbers = sorted(keys)
    output_dir = options.output_dir.resolve()
    jobs: list[PlannedFrame] = []
    for target in selected:
        # Stable tie-break: the earlier keyframe owns the exact midpoint.
        key_number = min(key_numbers, key=lambda n: (abs(n - target.number), n))
        jobs.append(
            PlannedFrame(
                target=target,
                key_style=keys[key_number],
                key_source=frames[key_number],
                output=output_dir / f"{target.path.stem}.png",
            )
        )

    return BatchPlan(
        jobs=tuple(jobs),
        frame_count=len(jobs),
        keyframe_count=len(keys),
        first_frame=selected[0].number,
        last_frame=selected[-1].number,
    )


def command_for(job: PlannedFrame, options: RunOptions) -> list[str]:
    command = [
        str(options.executable),
        "-style",
        str(job.key_style.path),
        "-weight",
        str(options.style_weight),
        "-guide",
        str(job.key_source.path),
        str(job.target.path),
        "-weight",
        str(options.guide_weight),
        "-uniformity",
        str(options.uniformity),
        "-patchsize",
        str(options.patch_size),
        "-searchvoteiters",
        str(options.search_vote_iters),
        "-patchmatchiters",
        str(options.patch_match_iters),
        "-stopthreshold",
        str(options.stop_threshold),
        "-backend",
        options.backend,
    ]
    if options.pyramid_levels is not None:
        command.extend(["-pyramidlevels", str(options.pyramid_levels)])
    if options.extra_pass:
        command.append("-extrapass3x3")
    command.extend(["-output", str(job.output)])
    return command


class BatchRunner:
    def __init__(self, emit: Callable[[str, object], None]) -> None:
        self.emit = emit
        self.cancelled = threading.Event()
        self.process: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        self.cancelled.set()
        process = self.process
        if process and process.poll() is None:
            process.terminate()

    def run(self, plan: BatchPlan, options: RunOptions) -> None:
        options.output_dir.mkdir(parents=True, exist_ok=True)
        completed = 0
        skipped = 0
        started = time.monotonic()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        try:
            for index, job in enumerate(plan.jobs, start=1):
                if self.cancelled.is_set():
                    self.emit("cancelled", (completed, skipped))
                    return

                self.emit("current", (index, plan.frame_count, job.target.number, job.key_style.number))
                if job.output.exists() and not options.overwrite:
                    skipped += 1
                    self.emit("log", f"Пропуск: {job.output.name} уже существует")
                    self.emit("progress", (index, plan.frame_count))
                    continue

                command = command_for(job, options)
                self.emit("log", f"Кадр {job.target.number} ← key {job.key_style.number}")
                self.process = subprocess.Popen(
                    command,
                    cwd=str(options.executable.parent),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=creationflags,
                )
                output, _ = self.process.communicate()
                code = self.process.returncode
                self.process = None

                if self.cancelled.is_set():
                    self.emit("cancelled", (completed, skipped))
                    return
                if code != 0:
                    details = output.strip() or f"Код завершения: {code}"
                    self.emit("failed", (job.target.number, details))
                    return
                if output.strip():
                    self.emit("log", output.strip())
                completed += 1
                self.emit("progress", (index, plan.frame_count))

            elapsed = time.monotonic() - started
            self.emit("done", (completed, skipped, elapsed))
        except Exception as exc:  # Keep worker exceptions visible in the GUI.
            self.process = None
            self.emit("failed", (None, str(exc)))


class PathRow(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        title: str,
        hint: str,
        variable: tk.StringVar,
        browse: Callable[[], None],
        icon: str,
    ) -> None:
        super().__init__(master, style="Card.TFrame", padding=(16, 12))
        self.columnconfigure(1, weight=1)
        ttk.Label(self, text=icon, style="PathIcon.TLabel").grid(row=0, column=0, rowspan=2, padx=(0, 12))
        ttk.Label(self, text=title, style="CardTitle.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(self, text=hint, style="Hint.TLabel").grid(row=1, column=1, sticky="w", pady=(2, 0))
        entry = ttk.Entry(self, textvariable=variable, style="Path.TEntry")
        entry.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0), padx=(0, 10))
        ttk.Button(self, text="Выбрать", command=browse, style="Secondary.TButton").grid(
            row=2, column=2, pady=(10, 0)
        )


class StudioApp(tk.Tk):
    BG = "#0b0d12"
    PANEL = "#131720"
    CARD = "#191e29"
    CARD_HOVER = "#202737"
    TEXT = "#f4f7fb"
    MUTED = "#8c96a8"
    BORDER = "#2a3242"
    ACCENT = "#ff4f72"
    ACCENT_ACTIVE = "#ff6b88"
    GREEN = "#49d49d"

    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} {APP_VERSION}")
        self.geometry("1180x780")
        self.minsize(980, 700)
        self.configure(bg=self.BG)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.runner: BatchRunner | None = None
        self.worker: threading.Thread | None = None
        self.plan: BatchPlan | None = None
        self._settings_file = Path(os.getenv("APPDATA", str(Path.home()))) / "EbsynthStudio" / "settings.json"

        root = Path(__file__).resolve().parents[1]
        default_exe = root / "bin" / "ebsynth.exe"
        self.vars: dict[str, tk.Variable] = {
            "executable": tk.StringVar(value=str(default_exe) if default_exe.exists() else ""),
            "frames_dir": tk.StringVar(),
            "keys_dir": tk.StringVar(),
            "output_dir": tk.StringVar(),
            "start_frame": tk.StringVar(),
            "end_frame": tk.StringVar(),
            "style_weight": tk.DoubleVar(value=1.0),
            "guide_weight": tk.DoubleVar(value=1.0),
            "uniformity": tk.DoubleVar(value=3500.0),
            "patch_size": tk.IntVar(value=5),
            "pyramid_levels": tk.StringVar(),
            "search_vote_iters": tk.IntVar(value=6),
            "patch_match_iters": tk.IntVar(value=4),
            "stop_threshold": tk.IntVar(value=5),
            "backend": tk.StringVar(value="cpu"),
            "extra_pass": tk.BooleanVar(value=False),
            "overwrite": tk.BooleanVar(value=False),
        }
        self.status_text = tk.StringVar(value="Укажите папки с кадрами и keyframes")
        self.summary_text = tk.StringVar(value="Проект ещё не проверен")
        self.current_text = tk.StringVar(value="Готов к работе")
        self.progress_value = tk.DoubleVar(value=0)

        self._configure_styles()
        self._load_settings()
        self._build_ui()
        self.after(100, self._poll_events)

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=self.BG, foreground=self.TEXT, font=("Segoe UI", 10))
        style.configure("App.TFrame", background=self.BG)
        style.configure("Panel.TFrame", background=self.PANEL)
        style.configure("Card.TFrame", background=self.CARD, relief="flat")
        style.configure("TLabel", background=self.BG, foreground=self.TEXT)
        style.configure("Header.TLabel", background=self.BG, foreground=self.TEXT, font=("Segoe UI Semibold", 22))
        style.configure("Subheader.TLabel", background=self.BG, foreground=self.MUTED, font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background=self.CARD, foreground=self.TEXT, font=("Segoe UI Semibold", 11))
        style.configure("Hint.TLabel", background=self.CARD, foreground=self.MUTED, font=("Segoe UI", 9))
        style.configure("PathIcon.TLabel", background=self.CARD, foreground=self.ACCENT, font=("Segoe UI Semibold", 18))
        style.configure("PanelTitle.TLabel", background=self.PANEL, foreground=self.TEXT, font=("Segoe UI Semibold", 12))
        style.configure("PanelHint.TLabel", background=self.PANEL, foreground=self.MUTED, font=("Segoe UI", 9))
        style.configure("Status.TLabel", background=self.PANEL, foreground=self.MUTED)
        style.configure("Good.TLabel", background=self.PANEL, foreground=self.GREEN, font=("Segoe UI Semibold", 10))

        style.configure("TEntry", fieldbackground="#0f131b", foreground=self.TEXT, insertcolor=self.TEXT, bordercolor=self.BORDER, padding=8)
        style.configure("Path.TEntry", fieldbackground="#10141c", foreground=self.TEXT, bordercolor=self.BORDER, padding=8)
        style.configure("TCombobox", fieldbackground="#0f131b", foreground=self.TEXT, arrowcolor=self.TEXT, bordercolor=self.BORDER, padding=7)
        style.map("TCombobox", fieldbackground=[("readonly", "#0f131b")], foreground=[("readonly", self.TEXT)])
        style.configure("TSpinbox", fieldbackground="#0f131b", foreground=self.TEXT, arrowcolor=self.TEXT, bordercolor=self.BORDER, padding=7)
        style.configure("TCheckbutton", background=self.PANEL, foreground=self.TEXT, indicatorbackground="#0f131b", indicatorforeground=self.ACCENT)
        style.map("TCheckbutton", background=[("active", self.PANEL)])

        style.configure("Primary.TButton", background=self.ACCENT, foreground="white", borderwidth=0, padding=(18, 11), font=("Segoe UI Semibold", 10))
        style.map("Primary.TButton", background=[("active", self.ACCENT_ACTIVE), ("disabled", "#553040")], foreground=[("disabled", "#9a7a84")])
        style.configure("Secondary.TButton", background="#252c3a", foreground=self.TEXT, borderwidth=0, padding=(14, 8))
        style.map("Secondary.TButton", background=[("active", "#313b4e")])
        style.configure("Danger.TButton", background="#3a2028", foreground="#ff9aae", borderwidth=0, padding=(14, 10))
        style.map("Danger.TButton", background=[("active", "#542935")])
        style.configure("Accent.Horizontal.TProgressbar", troughcolor="#252b38", background=self.ACCENT, borderwidth=0, thickness=8)
        style.configure("Vertical.TScrollbar", background="#272e3c", troughcolor=self.PANEL, arrowcolor=self.MUTED)

    def _build_ui(self) -> None:
        shell = ttk.Frame(self, style="App.TFrame", padding=(28, 22))
        shell.pack(fill="both", expand=True)
        shell.columnconfigure(0, weight=7)
        shell.columnconfigure(1, weight=4)
        shell.rowconfigure(1, weight=1)

        header = ttk.Frame(shell, style="App.TFrame")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 18))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text="EbSynth Studio", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="Покадровая стилизация без ручной командной строки", style="Subheader.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))
        ttk.Label(header, text="BATCH GUI  •  v1.0", foreground=self.ACCENT, background=self.BG, font=("Segoe UI Semibold", 9)).grid(row=0, column=1, rowspan=2, sticky="e")

        left = ttk.Frame(shell, style="App.TFrame")
        left.grid(row=1, column=0, sticky="nsew", padx=(0, 18))
        left.columnconfigure(0, weight=1)

        PathRow(left, "Исходные кадры", "Пронумерованная последовательность из видео", self.vars["frames_dir"], lambda: self._choose_folder("frames_dir"), "01").grid(row=0, column=0, sticky="ew", pady=(0, 10))
        PathRow(left, "Нарисованные keyframes", "Имена должны содержать номера исходных кадров", self.vars["keys_dir"], lambda: self._choose_folder("keys_dir"), "02").grid(row=1, column=0, sticky="ew", pady=(0, 10))
        PathRow(left, "Результат", "PNG-кадры для последующей сборки в видео", self.vars["output_dir"], lambda: self._choose_folder("output_dir"), "03").grid(row=2, column=0, sticky="ew", pady=(0, 14))

        range_card = ttk.Frame(left, style="Card.TFrame", padding=(16, 14))
        range_card.grid(row=3, column=0, sticky="ew")
        range_card.columnconfigure(4, weight=1)
        ttk.Label(range_card, text="Диапазон", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 14))
        ttk.Label(range_card, text="от", style="Hint.TLabel").grid(row=0, column=1, padx=(0, 6))
        ttk.Entry(range_card, textvariable=self.vars["start_frame"], width=9).grid(row=0, column=2, padx=(0, 10))
        ttk.Label(range_card, text="до", style="Hint.TLabel").grid(row=0, column=3, padx=(0, 6))
        ttk.Entry(range_card, textvariable=self.vars["end_frame"], width=9).grid(row=0, column=4, sticky="w")
        ttk.Label(range_card, text="Пусто = вся последовательность", style="Hint.TLabel").grid(row=1, column=1, columnspan=4, sticky="w", pady=(7, 0))
        ttk.Button(range_card, text="Проверить проект", command=self.inspect_project, style="Secondary.TButton").grid(row=0, column=5, rowspan=2, sticky="e")

        info = ttk.Frame(left, style="Panel.TFrame", padding=(18, 14))
        info.grid(row=4, column=0, sticky="nsew", pady=(14, 0))
        left.rowconfigure(4, weight=1)
        info.columnconfigure(0, weight=1)
        ttk.Label(info, textvariable=self.summary_text, style="Good.TLabel").grid(row=0, column=0, sticky="w")
        self.log = tk.Text(info, height=7, bg="#0e1219", fg="#aab3c3", insertbackground="white", relief="flat", bd=0, padx=10, pady=8, font=("Cascadia Mono", 9), state="disabled", wrap="word")
        self.log.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        info.rowconfigure(1, weight=1)

        right = ttk.Frame(shell, style="Panel.TFrame", padding=(20, 18))
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)
        ttk.Label(right, text="Настройки синтеза", style="PanelTitle.TLabel").grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(right, text="Сбалансированные значения подходят для старта", style="PanelHint.TLabel").grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 15))

        self._setting_row(right, 2, "EbSynth.exe", "executable", browse=True)
        self._combo_row(right, 3, "Профиль", ["Быстро", "Стандарт", "Качество"], "Стандарт", self._apply_preset)
        self._combo_var_row(right, 4, "Вычисления", "backend", ["cpu", "cuda"])
        self._spin_row(right, 5, "Вес стиля", "style_weight", 0.0, 20.0, 0.1)
        self._spin_row(right, 6, "Вес guide", "guide_weight", 0.0, 20.0, 0.1)
        self._spin_row(right, 7, "Uniformity", "uniformity", 0.0, 100000.0, 100.0)
        self._spin_row(right, 8, "Размер patch", "patch_size", 3, 15, 2)
        self._spin_row(right, 9, "Search/vote", "search_vote_iters", 0, 20, 1)
        self._spin_row(right, 10, "PatchMatch", "patch_match_iters", 0, 20, 1)
        self._setting_row(right, 11, "Pyramid levels", "pyramid_levels", hint="авто")

        checks = ttk.Frame(right, style="Panel.TFrame")
        checks.grid(row=12, column=0, columnspan=2, sticky="ew", pady=(13, 10))
        ttk.Checkbutton(checks, text="Доп. проход 3×3", variable=self.vars["extra_pass"]).pack(anchor="w", pady=2)
        ttk.Checkbutton(checks, text="Перезаписывать готовые кадры", variable=self.vars["overwrite"]).pack(anchor="w", pady=2)

        right.rowconfigure(13, weight=1)
        progress_box = ttk.Frame(right, style="Panel.TFrame")
        progress_box.grid(row=14, column=0, columnspan=2, sticky="sew")
        progress_box.columnconfigure(0, weight=1)
        ttk.Label(progress_box, textvariable=self.current_text, style="Status.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 7))
        ttk.Progressbar(progress_box, variable=self.progress_value, maximum=100, style="Accent.Horizontal.TProgressbar").grid(row=1, column=0, sticky="ew")
        buttons = ttk.Frame(progress_box, style="Panel.TFrame")
        buttons.grid(row=2, column=0, sticky="ew", pady=(14, 0))
        buttons.columnconfigure(0, weight=1)
        self.start_button = ttk.Button(buttons, text="Запустить синтез", command=self.start_run, style="Primary.TButton")
        self.start_button.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.cancel_button = ttk.Button(buttons, text="Стоп", command=self.cancel_run, style="Danger.TButton", state="disabled")
        self.cancel_button.grid(row=0, column=1)

        footer = ttk.Frame(shell, style="App.TFrame")
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        footer.columnconfigure(0, weight=1)
        ttk.Label(footer, textvariable=self.status_text, style="Subheader.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="Открыть результат", command=self.open_output, style="Secondary.TButton").grid(row=0, column=1, sticky="e")

    def _setting_row(self, parent: ttk.Frame, row: int, label: str, key: str, browse: bool = False, hint: str = "") -> None:
        ttk.Label(parent, text=label, style="Status.TLabel").grid(row=row, column=0, sticky="w", pady=5, padx=(0, 12))
        field = ttk.Frame(parent, style="Panel.TFrame")
        field.grid(row=row, column=1, sticky="ew", pady=5)
        field.columnconfigure(0, weight=1)
        ttk.Entry(field, textvariable=self.vars[key]).grid(row=0, column=0, sticky="ew")
        if browse:
            ttk.Button(field, text="…", width=3, command=self._choose_executable, style="Secondary.TButton").grid(row=0, column=1, padx=(5, 0))
        elif hint:
            ttk.Label(field, text=hint, style="PanelHint.TLabel").grid(row=0, column=1, padx=(6, 0))

    def _spin_row(self, parent: ttk.Frame, row: int, label: str, key: str, low: float, high: float, step: float) -> None:
        ttk.Label(parent, text=label, style="Status.TLabel").grid(row=row, column=0, sticky="w", pady=5, padx=(0, 12))
        ttk.Spinbox(parent, textvariable=self.vars[key], from_=low, to=high, increment=step).grid(row=row, column=1, sticky="ew", pady=5)

    def _combo_var_row(self, parent: ttk.Frame, row: int, label: str, key: str, values: list[str]) -> None:
        ttk.Label(parent, text=label, style="Status.TLabel").grid(row=row, column=0, sticky="w", pady=5, padx=(0, 12))
        ttk.Combobox(parent, textvariable=self.vars[key], values=values, state="readonly").grid(row=row, column=1, sticky="ew", pady=5)

    def _combo_row(self, parent: ttk.Frame, row: int, label: str, values: list[str], initial: str, callback: Callable[[str], None]) -> None:
        variable = tk.StringVar(value=initial)
        ttk.Label(parent, text=label, style="Status.TLabel").grid(row=row, column=0, sticky="w", pady=5, padx=(0, 12))
        combo = ttk.Combobox(parent, textvariable=variable, values=values, state="readonly")
        combo.grid(row=row, column=1, sticky="ew", pady=5)
        combo.bind("<<ComboboxSelected>>", lambda _event: callback(variable.get()))

    def _apply_preset(self, preset: str) -> None:
        values = {
            "Быстро": (5, 3, 2, False),
            "Стандарт": (5, 6, 4, False),
            "Качество": (7, 8, 6, True),
        }[preset]
        self.vars["patch_size"].set(values[0])
        self.vars["search_vote_iters"].set(values[1])
        self.vars["patch_match_iters"].set(values[2])
        self.vars["extra_pass"].set(values[3])
        self._append_log(f"Профиль «{preset}» применён")

    def _choose_folder(self, key: str) -> None:
        current = str(self.vars[key].get())
        selected = filedialog.askdirectory(initialdir=current if Path(current).is_dir() else None)
        if not selected:
            return
        self.vars[key].set(selected)
        if key == "frames_dir" and not str(self.vars["output_dir"].get()).strip():
            self.vars["output_dir"].set(str(Path(selected).parent / "ebsynth_output"))

    def _choose_executable(self) -> None:
        selected = filedialog.askopenfilename(title="Выберите ebsynth.exe", filetypes=[("EbSynth", "ebsynth.exe"), ("Программы", "*.exe"), ("Все файлы", "*.*")])
        if selected:
            self.vars["executable"].set(selected)

    @staticmethod
    def _optional_int(value: object, label: str) -> int | None:
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError as exc:
            raise PlanError(f"{label}: требуется целое число.") from exc

    def _collect_options(self) -> RunOptions:
        executable = Path(str(self.vars["executable"].get()).strip())
        if not executable.is_file():
            raise PlanError("Не найден ebsynth.exe. Соберите CPU-версию или укажите готовый файл.")
        patch_size = int(self.vars["patch_size"].get())
        if patch_size < 3 or patch_size % 2 == 0:
            raise PlanError("Размер patch должен быть нечётным числом не меньше 3.")
        backend = str(self.vars["backend"].get())
        if backend not in {"cpu", "cuda"}:
            raise PlanError("Неизвестный backend.")
        return RunOptions(
            executable=executable.resolve(),
            frames_dir=Path(str(self.vars["frames_dir"].get()).strip()),
            keys_dir=Path(str(self.vars["keys_dir"].get()).strip()),
            output_dir=Path(str(self.vars["output_dir"].get()).strip()),
            start_frame=self._optional_int(self.vars["start_frame"].get(), "Начальный кадр"),
            end_frame=self._optional_int(self.vars["end_frame"].get(), "Конечный кадр"),
            style_weight=float(self.vars["style_weight"].get()),
            guide_weight=float(self.vars["guide_weight"].get()),
            uniformity=float(self.vars["uniformity"].get()),
            patch_size=patch_size,
            pyramid_levels=self._optional_int(self.vars["pyramid_levels"].get(), "Pyramid levels"),
            search_vote_iters=int(self.vars["search_vote_iters"].get()),
            patch_match_iters=int(self.vars["patch_match_iters"].get()),
            stop_threshold=int(self.vars["stop_threshold"].get()),
            backend=backend,
            extra_pass=bool(self.vars["extra_pass"].get()),
            overwrite=bool(self.vars["overwrite"].get()),
        )

    def inspect_project(self, quiet: bool = False) -> tuple[BatchPlan, RunOptions] | None:
        try:
            options = self._collect_options()
            plan = make_plan(options)
        except (PlanError, ValueError, tk.TclError) as exc:
            self.plan = None
            self.summary_text.set("Проект требует внимания")
            self.status_text.set(str(exc))
            if not quiet:
                messagebox.showerror("Не удалось проверить проект", str(exc), parent=self)
            return None

        self.plan = plan
        keys = sorted({job.key_style.number for job in plan.jobs})
        self.summary_text.set(f"✓ {plan.frame_count} кадров  •  {plan.keyframe_count} keyframes  •  диапазон {plan.first_frame}–{plan.last_frame}")
        self.status_text.set("Проект готов к запуску")
        self._append_log("Keyframes: " + ", ".join(map(str, keys)))
        self._append_log("Диапазоны назначаются автоматически по ближайшему keyframe.")
        self._save_settings()
        return plan, options

    def start_run(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        checked = self.inspect_project(quiet=False)
        if checked is None:
            return
        plan, options = checked
        options.output_dir.mkdir(parents=True, exist_ok=True)
        self.progress_value.set(0)
        self.current_text.set("Подготовка очереди…")
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.runner = BatchRunner(lambda kind, payload: self.events.put((kind, payload)))
        self.worker = threading.Thread(target=self.runner.run, args=(plan, options), daemon=True)
        self.worker.start()

    def cancel_run(self) -> None:
        if self.runner:
            self.current_text.set("Останавливаю текущий кадр…")
            self.runner.cancel()
            self.cancel_button.configure(state="disabled")

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "current":
                    index, total, frame, key = payload  # type: ignore[misc]
                    self.current_text.set(f"Кадр {index}/{total}: {frame}  •  key {key}")
                elif kind == "progress":
                    done, total = payload  # type: ignore[misc]
                    self.progress_value.set(done * 100 / max(total, 1))
                elif kind == "done":
                    completed, skipped, elapsed = payload  # type: ignore[misc]
                    self.progress_value.set(100)
                    self.current_text.set(f"Готово: {completed} новых, {skipped} пропущено")
                    self.status_text.set(f"Синтез завершён за {elapsed / 60:.1f} мин")
                    self._append_log(f"Готово за {elapsed:.1f} сек. Новых кадров: {completed}; пропущено: {skipped}.")
                    self._finish_run()
                    messagebox.showinfo("EbSynth Studio", "Обработка завершена.", parent=self)
                elif kind == "cancelled":
                    completed, skipped = payload  # type: ignore[misc]
                    self.current_text.set("Обработка остановлена")
                    self.status_text.set(f"Готовых: {completed}; пропущено: {skipped}")
                    self._append_log("Очередь остановлена пользователем.")
                    self._finish_run()
                elif kind == "failed":
                    frame, details = payload  # type: ignore[misc]
                    label = f" на кадре {frame}" if frame is not None else ""
                    self.current_text.set("Ошибка обработки")
                    self.status_text.set(f"EbSynth завершился с ошибкой{label}")
                    self._append_log(str(details))
                    self._finish_run()
                    messagebox.showerror("Ошибка EbSynth", f"Сбой{label}:\n\n{details}", parent=self)
        except queue.Empty:
            pass
        self.after(100, self._poll_events)

    def _finish_run(self) -> None:
        self.runner = None
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")

    def _append_log(self, message: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", message.rstrip() + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def open_output(self) -> None:
        folder = Path(str(self.vars["output_dir"].get()).strip())
        if not folder.is_dir():
            messagebox.showinfo("EbSynth Studio", "Папка результата ещё не создана.", parent=self)
            return
        if hasattr(os, "startfile"):
            os.startfile(str(folder))  # type: ignore[attr-defined]

    def _load_settings(self) -> None:
        try:
            data = json.loads(self._settings_file.read_text(encoding="utf-8"))
            for key, value in data.items():
                if key in self.vars:
                    self.vars[key].set(value)
        except (OSError, ValueError, tk.TclError):
            pass

    def _save_settings(self) -> None:
        try:
            self._settings_file.parent.mkdir(parents=True, exist_ok=True)
            data = {key: variable.get() for key, variable in self.vars.items()}
            self._settings_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            pass

    def on_close(self) -> None:
        if self.worker and self.worker.is_alive():
            if not messagebox.askyesno("EbSynth Studio", "Обработка идёт. Остановить её и выйти?", parent=self):
                return
            if self.runner:
                self.runner.cancel()
        self._save_settings()
        self.destroy()


def main() -> int:
    if sys.version_info < (3, 10):
        print("EbSynth Studio requires Python 3.10 or newer.", file=sys.stderr)
        return 1
    app = StudioApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
