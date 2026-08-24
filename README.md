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
- контрастный предпросмотр спрайта с шахматной подложкой;
- Undo/Redo, переносимые JSON-проекты и список недавних проектов;
- автоматический рендер PNG через CPU EbSynth;
- экспорт готовой последовательности в выбранную папку или единый PNG-спрайтшит;
- переносимая Windows-сборка с FFmpeg в разделе Releases.

## Готовая Windows-сборка

Скачайте `EbSynthStudio2-win64.zip` из раздела
[Releases](https://github.com/Theodaimones7/ebsynth-studio2/releases), распакуйте
архив и запустите `EbSynthStudio2.exe`.

## Сборка из исходников

Требуются Python 3.11+, Visual Studio Build Tools с C++ и FFmpeg в `PATH`:

```bat
python -m pip install -r requirements-studio2.txt
build-studio2-win64.bat
```

Результат появится в `dist\EbSynthStudio2`.

Подробная инструкция: [STUDIO2_README_RU.md](STUDIO2_README_RU.md).

Исходный алгоритм EbSynth распространяется как public domain. При использовании
учитывайте лицензионное и патентное предупреждение из
[оригинального README](README_EBSYNTH_UPSTREAM.md).
