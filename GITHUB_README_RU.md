# EbSynth Studio 2

Монтажный Windows-интерфейс для открытого движка
[EbSynth](https://github.com/jamriska/ebsynth): импорт видео или кадров,
проигрываемый таймлайн, drag-and-drop keyframes, свободная трансформация слоёв и
пакетный PNG-рендер.

## Возможности

- импорт MP4/MOV/MKV или последовательности PNG/JPG;
- проигрывание, FPS, цикл и покадровая навигация;
- таймлайн с миниатюрами и диапазонами влияния keyframes;
- несколько слоёв на одном кадре;
- перемещение, масштабирование, поворот, прозрачность и порядок слоёв;
- Undo/Redo и переносимые JSON-проекты;
- автоматический рендер PNG через CPU EbSynth;
- переносимая Windows-сборка с FFmpeg в разделе Releases.

## Сборка Windows

Требуются Python 3.11+, Visual Studio Build Tools с C++ и FFmpeg в `PATH`:

```bat
python -m pip install -r requirements-studio2.txt
build-studio2-win64.bat
```

Результат появится в `dist\EbSynthStudio2`.

Подробная инструкция: [STUDIO2_README_RU.md](STUDIO2_README_RU.md).

Исходный алгоритм EbSynth распространяется как public domain. При использовании
учитывайте патентное предупреждение из оригинального [README](README.md).
