import json
import os
import queue
import random
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSlider,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)


APP_QSS = """
QWidget {
    font-family: "Segoe UI Variable", "Segoe UI", "Nunito", "Quicksand",
                 "Arial Rounded MT Bold", Arial;
    font-size: 10.5pt;
    color: #EAEAEA;
    background: #0F1115;
}

QLineEdit {
    background: #151924;
    border: 1px solid #2A3145;
    border-radius: 10px;
    padding: 10px 12px;
}
QLineEdit:focus {
    border: 1px solid #3B82F6;
}

QPushButton {
    background: #151924;
    border: 1px solid #2A3145;
    border-radius: 12px;
    padding: 10px 14px;
}
QPushButton:hover {
    background: #1B2030;
    border: 1px solid #3A4563;
}
QPushButton:pressed {
    background: #101422;
}

QCheckBox {
    spacing: 10px;
}
QCheckBox::indicator {
    width: 18px;
    height: 18px;
    border-radius: 6px;
    border: 1px solid #2A3145;
    background: #151924;
}
QCheckBox::indicator:checked {
    background: #3B82F6;
    border: 1px solid #3B82F6;
}

QComboBox {
    background: #151924;
    border: 1px solid #2A3145;
    border-radius: 10px;
    padding: 8px 10px;
}
QComboBox:hover {
    border: 1px solid #3A4563;
}
QComboBox::drop-down {
    border: 0px;
}
QComboBox QAbstractItemView {
    background: #151924;
    border: 1px solid #2A3145;
    selection-background-color: #1F2A44;
    selection-color: #EAEAEA;
    outline: 0;
}

QListWidget {
    background: #0F1115;
    border: 1px solid #222634;
    border-radius: 14px;
    padding: 6px;
    outline: 0;
}
QListWidget::item {
    padding: 10px 10px;
    margin: 3px 4px;
    border-radius: 10px;
}
QListWidget::item:selected {
    background: #1F2A44;
    border: 1px solid #3B82F6;
}
QListWidget::item:hover {
    background: #151924;
}

QSlider::groove:horizontal {
    height: 8px;
    background: #151924;
    border: 1px solid #2A3145;
    border-radius: 5px;
}
QSlider::handle:horizontal {
    width: 18px;
    margin: -6px 0px;
    border-radius: 9px;
    background: #3B82F6;
    border: 1px solid #3B82F6;
}

QLabel#NowPlaying {
    background: #151924;
    border: 1px solid #2A3145;
    border-radius: 14px;
    padding: 12px 14px;
    color: #EAEAEA;
}
"""


def _format_ms(ms: int) -> str:
    if ms is None or ms < 0:
        ms = 0
    total_sec = ms // 1000
    minutes = total_sec // 60
    seconds = total_sec % 60
    return f"{minutes:02d}:{seconds:02d}"


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list) and value:
        return str(value[0])
    return str(value)


@dataclass
class Track:
    path: str
    title: str = ""
    artist: str = ""
    album: str = ""
    duration_ms: int = -1

    @property
    def display(self) -> str:
        base = self.title or Path(self.path).stem
        if self.artist:
            return f"{self.artist} — {base}"
        return base


def _setup_vlc_dll_paths() -> str | None:
    candidates = [
        r"C:\Program Files\VideoLAN\VLC",
        r"C:\Program Files (x86)\VideoLAN\VLC",
    ]

    for d in candidates:
        if Path(d).exists():
            try:
                os.add_dll_directory(d)
            except Exception:
                pass
            os.environ["VLC_PLUGIN_PATH"] = os.path.join(d, "plugins")
            return d

    return None


_VLC_DIR = _setup_vlc_dll_paths()

import vlc  # noqa: E402


def _get_windows_voices() -> list[str]:
    if not sys.platform.startswith("win"):
        return []

    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.GetInstalledVoices() | ForEach-Object { $_.VoiceInfo.Name }"
    )

    try:
        out = subprocess.check_output(
            ["powershell", "-NoProfile", "-Command", ps],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except Exception:
        return []

    voices: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if line:
            voices.append(line)
    return voices


class TTSAnnouncer:
    def __init__(self) -> None:
        self._q: queue.Queue[str] = queue.Queue()
        self._stop_event = threading.Event()
        self._ps_proc: subprocess.Popen[str] | None = None
        self._voice_name: str = ""

        self._worker = threading.Thread(
            target=self._run_worker,
            daemon=True,
        )
        self._worker.start()

    def set_voice(self, voice_name: str) -> None:
        self._voice_name = (voice_name or "").strip()

    def say(self, phrase: str) -> None:
        phrase = (phrase or "").strip()
        if not phrase:
            return
        self._drain_queue()
        self._q.put(phrase)

    def shutdown(self) -> None:
        self._stop_event.set()
        self._q.put("")
        self._kill_running()

    def _drain_queue(self) -> None:
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            return

    def _kill_running(self) -> None:
        if self._ps_proc is None:
            return
        try:
            self._ps_proc.kill()
        except Exception:
            pass
        self._ps_proc = None

    def _powershell_speak(self, phrase: str) -> None:
        safe_phrase = phrase.replace("'", "''")
        safe_voice = self._voice_name.replace("'", "''")

        select_voice = ""
        if safe_voice:
            select_voice = f"$s.SelectVoice('{safe_voice}'); "

        ps = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            f"{select_voice}"
            f"$s.Speak('{safe_phrase}');"
        )

        self._ps_proc = subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden",
             "-Command", ps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            self._ps_proc.wait(timeout=20)
        except Exception:
            self._kill_running()

        self._ps_proc = None

    def _run_worker(self) -> None:
        while not self._stop_event.is_set():
            phrase = self._q.get()
            if self._stop_event.is_set():
                break
            phrase = (phrase or "").strip()
            if not phrase:
                continue
            self._kill_running()
            if sys.platform.startswith("win"):
                self._powershell_speak(phrase)


class PlayerEngine:
    def __init__(self) -> None:
        self._vlc_instance = vlc.Instance()
        self._player = self._vlc_instance.media_player_new()

    def load_path(self, path: str) -> None:
        media = self._vlc_instance.media_new_path(path)
        self._player.set_media(media)

    def play(self) -> None:
        self._player.play()

    def pause(self) -> None:
        self._player.pause()

    def stop(self) -> None:
        self._player.stop()

    def is_playing(self) -> bool:
        return bool(self._player.is_playing())

    def get_time_ms(self) -> int:
        return int(self._player.get_time())

    def get_length_ms(self) -> int:
        return int(self._player.get_length())

    def set_time_ms(self, ms: int) -> None:
        self._player.set_time(int(ms))


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SAI MP3 Player (Slick)")
        self.resize(1000, 700)

        self.engine = PlayerEngine()
        self._tts = TTSAnnouncer()

        self.auto_play = True

        self.tracks: list[Track] = []
        self.visible_indices: list[int] = []
        self.play_order: list[int] = []
        self.play_pos: int = -1
        self.history: list[int] = []

        self.shuffle = False
        self.repeat_all = True
        self.repeat_one = False

        self._user_seeking = False
        self._last_known_length_ms = -1
        self._playing_list_row: int = -1

        self._ended_fired_for_track_idx: int = -1

        self._build_ui()
        self._build_tray()
        self._build_shortcuts()
        self._build_timer()

        self.setAcceptDrops(True)

    def _build_ui(self) -> None:
        self.now_playing = QLabel("Now Playing: —")
        self.now_playing.setObjectName("NowPlaying")
        self.now_playing.setWordWrap(True)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search (title / artist / album)...")
        self.search.textChanged.connect(self._apply_filter)

        self.playlist = QListWidget()
        self.playlist.setAlternatingRowColors(True)
        self.playlist.setUniformItemSizes(True)
        self.playlist.itemDoubleClicked.connect(self._on_item_double_clicked)

        self.btn_add_files = QPushButton("Add Files")
        self.btn_add_folder = QPushButton("Add Folder")
        self.btn_save = QPushButton("Save Playlist")
        self.btn_load = QPushButton("Load Playlist")

        self.btn_prev = QPushButton("◀ Prev")
        self.btn_play_pause = QPushButton("Play")
        self.btn_next = QPushButton("Next ▶")

        for b in (
            self.btn_add_files,
            self.btn_add_folder,
            self.btn_save,
            self.btn_load,
            self.btn_prev,
            self.btn_play_pause,
            self.btn_next,
        ):
            b.setMinimumHeight(42)

        self.chk_shuffle = QCheckBox("Shuffle")
        self.chk_repeat_all = QCheckBox("Repeat All")
        self.chk_repeat_one = QCheckBox("Repeat 1")
        self.chk_repeat_all.setChecked(True)

        self.voice_combo = QComboBox()
        self.voice_combo.setToolTip("Choose text-to-speech voice")
        self.voice_combo.addItem("Default Voice", "")
        for v in _get_windows_voices():
            self.voice_combo.addItem(v, v)
        self.voice_combo.currentIndexChanged.connect(self._on_voice_changed)

        self.btn_add_files.clicked.connect(self.add_files)
        self.btn_add_folder.clicked.connect(self.add_folder)
        self.btn_save.clicked.connect(self.save_playlist)
        self.btn_load.clicked.connect(self.load_playlist)

        self.btn_prev.clicked.connect(self.play_prev)
        self.btn_play_pause.clicked.connect(self.toggle_play_pause)
        self.btn_next.clicked.connect(self.play_next)

        self.chk_shuffle.stateChanged.connect(self._on_shuffle_changed)
        self.chk_repeat_all.stateChanged.connect(self._on_repeat_all_changed)
        self.chk_repeat_one.stateChanged.connect(self._on_repeat_one_changed)

        self.lbl_time = QLabel("00:00 / 00:00")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(1000)
        self.slider.setValue(0)
        self.slider.sliderPressed.connect(self._on_seek_start)
        self.slider.sliderReleased.connect(self._on_seek_end)
        self.slider.valueChanged.connect(self._on_seek_value_changed)

        self.status = QLabel(
            "Tip: Drag & drop MP3 files/folders. "
            "Space=Play/Pause, Left/Right=Prev/Next."
        )
        self.status.setWordWrap(True)

        top_row = QHBoxLayout()
        top_row.addWidget(self.btn_add_files)
        top_row.addWidget(self.btn_add_folder)
        top_row.addWidget(self.btn_save)
        top_row.addWidget(self.btn_load)
        top_row.addStretch(1)
        top_row.addWidget(QLabel("Voice:"))
        top_row.addWidget(self.voice_combo)

        controls_row = QHBoxLayout()
        controls_row.addWidget(self.btn_prev)
        controls_row.addWidget(self.btn_play_pause)
        controls_row.addWidget(self.btn_next)
        controls_row.addStretch(1)
        controls_row.addWidget(self.chk_shuffle)
        controls_row.addWidget(self.chk_repeat_all)
        controls_row.addWidget(self.chk_repeat_one)

        seek_row = QHBoxLayout()
        seek_row.addWidget(self.lbl_time)
        seek_row.addWidget(self.slider, stretch=1)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addLayout(top_row)
        layout.addWidget(self.now_playing)
        layout.addWidget(self.search)
        layout.addWidget(self.playlist, stretch=1)
        layout.addLayout(controls_row)
        layout.addLayout(seek_row)
        layout.addWidget(self.status)
        self.setCentralWidget(root)

    def _on_voice_changed(self) -> None:
        voice = self.voice_combo.currentData()
        self._tts.set_voice(str(voice or ""))

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        event.ignore()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        paths: list[Path] = []

        for u in urls:
            p = Path(u.toLocalFile())
            if not p.exists():
                continue

            if p.is_file():
                if p.suffix.lower() in {".mp3", ".wav", ".flac"}:
                    paths.append(p)
                continue

            if p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file() and f.suffix.lower() in {
                        ".mp3",
                        ".wav",
                        ".flac",
                    }:
                        paths.append(f)

        if paths:
            paths.sort()
            self._add_paths(paths)

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select audio files",
            "",
            "Audio Files (*.mp3 *.wav *.flac);;All Files (*)",
        )
        if not paths:
            return
        self._add_paths([Path(p) for p in paths])

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select folder")
        if not folder:
            return

        root = Path(folder)
        paths: list[Path] = []
        for p in root.rglob("*"):
            if p.is_file() and p.suffix.lower() in {".mp3", ".wav", ".flac"}:
                paths.append(p)

        if not paths:
            QMessageBox.information(
                self,
                "No audio files",
                "No .mp3/.wav/.flac files found in that folder.",
            )
            return

        paths.sort()
        self._add_paths(paths)

    def _add_paths(self, paths: list[Path]) -> None:
        was_empty = (len(self.tracks) == 0)
        added = 0
        existing = {str(Path(t.path)) for t in self.tracks}

        for p in paths:
            if not p.exists():
                continue
            if str(p) in existing:
                continue

            track = self._read_metadata(p)
            self.tracks.append(track)
            existing.add(str(p))
            added += 1

        if not added:
            return

        self._apply_filter()
        self._rebuild_play_order(keep_current=False)
        self.status.setText(f"Added {added} track(s).")

        if self.auto_play and was_empty and self.play_order:
            self.play_pos = 0
            self._load_current_and_play()

    def _read_metadata(self, path: Path) -> Track:
        title = path.stem
        artist = ""
        album = ""
        duration_ms = -1

        try:
            audio = MutagenFile(path)
            if audio is not None:
                duration_ms = int(getattr(audio.info, "length", 0) * 1000)
                tags = audio.tags
                if tags:
                    title = (
                        _safe_str(tags.get("TIT2") or tags.get("title"))
                        or title
                    )
                    artist = _safe_str(tags.get("TPE1") or tags.get("artist"))
                    album = _safe_str(tags.get("TALB") or tags.get("album"))
        except Exception:
            pass

        return Track(
            path=str(path),
            title=title,
            artist=artist,
            album=album,
            duration_ms=duration_ms,
        )

    def _apply_filter(self) -> None:
        q = (self.search.text() or "").strip().lower()
        self.visible_indices = []

        for i, t in enumerate(self.tracks):
            hay = f"{t.title} {t.artist} {t.album}".lower()
            if not q or q in hay:
                self.visible_indices.append(i)

        self.playlist.clear()
        for i in self.visible_indices:
            t = self.tracks[i]
            item = QListWidgetItem(t.display)
            item.setToolTip(t.path)
            self.playlist.addItem(item)

        self._rebuild_play_order(keep_current=True)
        self._update_now_playing_highlight()

    def _rebuild_play_order(self, keep_current: bool) -> None:
        current_idx = self._current_track_index()
        base = list(self.visible_indices)

        if self.shuffle:
            random.shuffle(base)

        self.play_order = base

        if not self.play_order:
            self.play_pos = -1
            return

        if keep_current and current_idx in self.play_order:
            self.play_pos = self.play_order.index(current_idx)
        else:
            self.play_pos = 0

    def _current_track_index(self) -> int:
        if self.play_pos < 0 or self.play_pos >= len(self.play_order):
            return -1
        return self.play_order[self.play_pos]

    def _announce_track_name(self, track: Track) -> None:
        self._tts.say(Path(track.path).stem.replace('_', ' '))

    def _update_now_playing_highlight(self) -> None:
        if self._playing_list_row != -1:
            old_item = self.playlist.item(self._playing_list_row)
            if old_item is not None:
                text = old_item.text()
                if text.startswith("▶ "):
                    old_item.setText(text[2:])
                f = old_item.font()
                f.setBold(False)
                old_item.setFont(f)

        idx = self._current_track_index()
        self._playing_list_row = -1

        if idx != -1 and idx in self.visible_indices:
            row = self.visible_indices.index(idx)
            self._playing_list_row = row

            item = self.playlist.item(row)
            if item is not None:
                if not item.text().startswith("▶ "):
                    item.setText("▶ " + item.text())
                f = item.font()
                f.setBold(True)
                item.setFont(f)

            self.playlist.setCurrentRow(row)

    def _load_current_and_play(self) -> None:
        idx = self._current_track_index()
        if idx == -1:
            return

        self._ended_fired_for_track_idx = -1

        t = self.tracks[idx]
        self.engine.load_path(t.path)
        self.engine.play()
        self.btn_play_pause.setText("Pause")

        self._announce_track_name(t)

        info = f"{t.title}"
        if t.artist:
            info = f"{t.artist} — {t.title}"
        if t.album:
            info += f"  •  {t.album}"
        self.now_playing.setText(f"Now Playing: {info}")

        self._update_now_playing_highlight()

    def toggle_play_pause(self) -> None:
        if not self.play_order:
            QMessageBox.information(
                self,
                "No tracks",
                "Add files/folders or load a playlist first.",
            )
            return

        if self._current_track_index() == -1:
            self.play_pos = 0
            self._load_current_and_play()
            return

        if self.engine.is_playing():
            self.engine.pause()
            self.btn_play_pause.setText("Play")
        else:
            self.engine.play()
            self.btn_play_pause.setText("Pause")

    def play_next(self) -> None:
        if not self.play_order:
            return

        if self.repeat_one:
            self._load_current_and_play()
            return

        cur = self._current_track_index()
        if cur != -1:
            self.history.append(cur)

        nxt = self.play_pos + 1
        if nxt >= len(self.play_order):
            if self.repeat_all:
                nxt = 0
            else:
                self.engine.stop()
                self.btn_play_pause.setText("Play")
                return

        self.play_pos = nxt
        self._load_current_and_play()

    def play_prev(self) -> None:
        if not self.play_order:
            return

        if self.history:
            idx = self.history.pop()
            if idx in self.play_order:
                self.play_pos = self.play_order.index(idx)
                self._load_current_and_play()
                return

        prv = self.play_pos - 1
        if prv < 0:
            prv = len(self.play_order) - 1 if self.repeat_all else 0

        self.play_pos = prv
        self._load_current_and_play()

    def _on_seek_start(self) -> None:
        self._user_seeking = True

    def _on_seek_end(self) -> None:
        self._user_seeking = False
        self._apply_seek_from_slider()

    def _on_seek_value_changed(self, value: int) -> None:
        if not self._user_seeking:
            return

        length = self.engine.get_length_ms()
        if length <= 0:
            length = self._last_known_length_ms
        if length > 0:
            ms = int((value / 1000.0) * length)
            self.lbl_time.setText(f"{_format_ms(ms)} / {_format_ms(length)}")

    def _apply_seek_from_slider(self) -> None:
        length = self.engine.get_length_ms()
        if length <= 0:
            length = self._last_known_length_ms
        if length <= 0:
            return

        value = self.slider.value()
        ms = int((value / 1000.0) * length)
        self.engine.set_time_ms(ms)

    def _build_timer(self) -> None:
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    def _maybe_autoplay_next(self, pos: int, length: int) -> None:
        if not self.auto_play:
            return
        if self._user_seeking:
            return
        if length <= 0 or pos <= 0:
            return

        idx = self._current_track_index()
        if idx == -1:
            return

        remaining = length - pos
        if remaining > 700:
            return

        if self._ended_fired_for_track_idx == idx:
            return

        self._ended_fired_for_track_idx = idx

        def _go() -> None:
            if self.repeat_one:
                self._load_current_and_play()
                return
            self.play_next()

        QTimer.singleShot(0, _go)

    def _tick(self) -> None:
        if self._user_seeking:
            return

        length = self.engine.get_length_ms()
        pos = self.engine.get_time_ms()

        if length > 0:
            self._last_known_length_ms = length

            ratio = pos / float(length)
            ratio = max(0.0, min(1.0, ratio))
            value = int(ratio * 1000.0)

            self.slider.blockSignals(True)
            self.slider.setValue(value)
            self.slider.blockSignals(False)

            self.lbl_time.setText(f"{_format_ms(pos)} / {_format_ms(length)}")
            self._maybe_autoplay_next(pos, length)
        else:
            self.slider.blockSignals(True)
            self.slider.setValue(0)
            self.slider.blockSignals(False)
            self.lbl_time.setText("00:00 / 00:00")

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        row = self.playlist.row(item)
        if row < 0 or row >= len(self.visible_indices):
            return

        idx = self.visible_indices[row]
        if idx in self.play_order:
            self.play_pos = self.play_order.index(idx)
        else:
            self._rebuild_play_order(keep_current=False)
            if idx in self.play_order:
                self.play_pos = self.play_order.index(idx)

        self._load_current_and_play()

    def _on_shuffle_changed(self, state: int) -> None:
        self.shuffle = (state == Qt.CheckState.Checked.value)
        self.history = []
        self._rebuild_play_order(keep_current=True)
        self._update_now_playing_highlight()

    def _on_repeat_all_changed(self, state: int) -> None:
        self.repeat_all = (state == Qt.CheckState.Checked.value)

    def _on_repeat_one_changed(self, state: int) -> None:
        self.repeat_one = (state == Qt.CheckState.Checked.value)

    def save_playlist(self) -> None:
        if not self.tracks:
            QMessageBox.information(self, "Empty", "No tracks to save.")
            return

        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save playlist",
            "",
            "Playlist JSON (*.json);;All Files (*)",
        )
        if not path:
            return

        payload = {
            "version": 1,
            "saved_at": int(time.time()),
            "tracks": [asdict(t) for t in self.tracks],
        }

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            self.status.setText(f"Saved playlist: {path}")
        except OSError as exc:
            QMessageBox.critical(self, "Save failed", str(exc))

    def load_playlist(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Load playlist",
            "",
            "Playlist JSON (*.json);;All Files (*)",
        )
        if not path:
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except OSError as exc:
            QMessageBox.critical(self, "Load failed", str(exc))
            return
        except json.JSONDecodeError as exc:
            QMessageBox.critical(self, "Invalid JSON", str(exc))
            return

        tracks = payload.get("tracks", [])
        loaded: list[Track] = []

        for t in tracks:
            p = t.get("path", "")
            if not p or not Path(p).exists():
                continue
            loaded.append(
                Track(
                    path=p,
                    title=t.get("title", ""),
                    artist=t.get("artist", ""),
                    album=t.get("album", ""),
                    duration_ms=int(t.get("duration_ms", -1)),
                )
            )

        if not loaded:
            QMessageBox.information(
                self,
                "Nothing loaded",
                "No valid local files found in that playlist.",
            )
            return

        self.tracks = loaded
        self.history = []
        self._apply_filter()
        self._rebuild_play_order(keep_current=False)
        self.status.setText(f"Loaded playlist: {path}")

        if self.auto_play and self.play_order:
            self.play_pos = 0
            self._load_current_and_play()

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        icon = self.style().standardIcon(
            self.style().StandardPixmap.SP_MediaPlay
        )
        self.tray.setIcon(icon)
        self.tray.setToolTip("SAI MP3 Player")

        menu = QMenu()
        act_show = QAction("Show", self)
        act_play_pause = QAction("Play/Pause", self)
        act_next = QAction("Next", self)
        act_prev = QAction("Prev", self)
        act_quit = QAction("Quit", self)

        act_show.triggered.connect(self._tray_show)
        act_play_pause.triggered.connect(self.toggle_play_pause)
        act_next.triggered.connect(self.play_next)
        act_prev.triggered.connect(self.play_prev)
        act_quit.triggered.connect(self._quit)

        menu.addAction(act_show)
        menu.addSeparator()
        menu.addAction(act_play_pause)
        menu.addAction(act_prev)
        menu.addAction(act_next)
        menu.addSeparator()
        menu.addAction(act_quit)

        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()

    def _build_shortcuts(self) -> None:
        QShortcut(
            QKeySequence(Qt.Key.Key_Space),
            self,
            activated=self.toggle_play_pause,
        )
        QShortcut(
            QKeySequence(Qt.Key.Key_Right),
            self,
            activated=self.play_next,
        )
        QShortcut(
            QKeySequence(Qt.Key.Key_Left),
            self,
            activated=self.play_prev,
        )

        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.add_files)
        QShortcut(QKeySequence("Ctrl+Shift+O"), self, activated=self.add_folder)
        QShortcut(QKeySequence("Ctrl+S"), self, activated=self.save_playlist)
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.load_playlist)
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)

    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    def closeEvent(self, event) -> None:
        self.hide()
        self.tray.showMessage(
            "SAI MP3 Player",
            "Still running in tray (music keeps playing).",
            QSystemTrayIcon.MessageIcon.Information,
            2000,
        )
        event.ignore()

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self._tray_show()

    def _tray_show(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _quit(self) -> None:
        try:
            self.engine.stop()
        except Exception:
            pass
        try:
            self._tts.shutdown()
        except Exception:
            pass
        QApplication.quit()


def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_QSS)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
