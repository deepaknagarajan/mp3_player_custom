# SAI MP3 Player (Subpackage)

Build and install the wheel from this folder.

## Build

```bash
python -m pip install --upgrade pip build wheel
python -m build -w
```

Wheel artifacts are created in `dist/` under this folder.

## Install

```bash
pip install dist/sai_mp3_player-0.1.0-py3-none-any.whl
```

## Run

```bash
sai-mp3-player
```