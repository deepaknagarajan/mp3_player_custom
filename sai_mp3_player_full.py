# sreesaibaba

import json
import os
import random
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from mutagen import File as MutagenFile
from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction, QIcon, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
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

import vlc


def _format_ms(ms: int) -> str:
    if ms < 0:
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
            return f"{self.artist} - {base}"
        return base


class PlayerEngine:
    def __init__(self, vlc_dir: str | None = None) -> None:
        if vlc_dir:
            os.add_dll_directory(vlc_dir)
            os.environ["VLC_PLUGIN_PATH"] = os.path.join(vlc_dir, "plugins")

        self._vlc_instance = vlc.Instance()
        self._player = self._vlc_instance.media_player_new()
        self._current_location: str | None = None

        events = self._player.event_manager()
        events.event_attach(vlc.EventType.MediaPlayerEndReached,
                            self._on_end_reached)

        self._end_callback = None

    def set_on_track_end(self, callback) -> None:
        self._end_callback = callback

    def load_path(self, path: str) -> None:
        self._current_location = path
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

    def _on_end_reached(self, event) -> None:
        if self._end_callback is not None:
            self._end_callback()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SAI MP3 Player (Full)")
        self.resize(1000, 650)

        # ---- Configure VLC discovery (adjust only if needed) ----
        vlc_dir = r"C:\Program Files\VideoLAN\VLC"
        if not Path(vlc_dir).exists():
            vlc_dir = None

        self.engine = PlayerEngine(vlc_dir=vlc_dir)
        self.engine.set_on_track_end(self._handle_track_end)

        self.tracks: list[Track] = []
        self.visible_indices: list[int] = []
        self.play_order: list[int] = []
        self.play_pos: int = -1
        self.history: list[int] = []

        self.repeat_one = False
        self.repeat_all = True
        self.shuffle = False

        self._user_seeking = False
        self._last_status = ""

        self._build_ui()
        self._build_tray()
        self._build_shortcuts()
        self._build_timer()

        self.setAcceptDrops(True)

    # ---------------- UI ----------------
    def _build_ui(self) -> None:
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search (title / artist / album)...")
        self.search.textChanged.connect(self._apply_filter)

        self.playlist = QListWidget()
        self.playlist.itemDoubleClicked.connect(self._on_item_double_clicked)

        self.btn_add_files = QPushButton("Add Files")
        self.btn_add_folder = QPushButton("Add Folder")
        self.btn_save = QPushButton("Save Playlist")
        self.btn_load = QPushButton("Load Playlist")

        self.btn_prev = QPushButton("◀ Prev")
        self.btn_play_pause = QPushButton("Play")
        self.btn_next = QPushButton("Next ▶")

        self.chk_shuffle = QCheckBox("Shuffle")
        self.chk_repeat_all = QCheckBox("Repeat All")
        self.chk_repeat_one = QCheckBox("Repeat 1")
        self.chk_repeat_all.setChecked(True)

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
        self.slider.sliderPressed.connect(self._on_seek_start)
        self.slider.sliderReleased.connect(self._on_seek_end)
        self.slider.valueChanged.connect(self._on_seek_value_changed)

        self.status = QLabel("Drop MP3 files here, or click Add Files/Folder.")
        self.status.setWordWrap(True)

        top_row = QHBoxLayout()
        top_row.addWidget(self.btn_add_files)
        top_row.addWidget(self.btn_add_folder)
        top_row.addWidget(self.btn_save)
        top_row.addWidget(self.btn_load)
        top_row.addStretch(1)
        top_row.addWidget(QLabel(" "))

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
        layout.addWidget(self.search)
        layout.addWidget(self.playlist, stretch=1)
        layout.addLayout(controls_row)
        layout.addLayout(seek_row)
        layout.addWidget(self.status)
        self.setCentralWidget(root)

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
        QShortcut(QKeySequence(Qt.Key.Key_Space), self,
                  activated=self.toggle_play_pause)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self,
                  activated=self.play_next)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self,
                  activated=self.play_prev)

        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.add_files)
        QShortcut(QKeySequence("Ctrl+Shift+O"), self,
                  activated=self.add_folder)
        QShortcut(QKeySequence("Ctrl+S"), self,
                  activated=self.save_playlist)
        QShortcut(QKeySequence("Ctrl+L"), self,
                  activated=self.load_playlist)
        QShortcut(QKeySequence("Ctrl+F"), self,
                  activated=self._focus_search)

    def _build_timer(self) -> None:
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._tick)
        self.timer.start()

    # ---------------- Drag & Drop ----------------
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
            if p.is_file() and p.suffix.lower() in {".mp3", ".wav", ".flac"}:
                paths.append(p)
            elif p.is_dir():
                for f in p.rglob("*"):
                    if f.is_file() and f.suffix.lower() in {
                        ".mp3", ".wav", ".flac"
                    }:
                        paths.append(f)

        if paths:
            paths.sort()
            self._add_paths(paths)

    # ---------------- Add files/folder ----------------
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
        added = 0
        for p in paths:
            if not p.exists():
                continue
            if any(Path(t.path) == p for t in self.tracks):
                continue

            track = self._read_metadata(p)
            self.tracks.append(track)
            added += 1

        if added:
            self._apply_filter()
            self._rebuild_play_order(keep_current=False)
            self._set_status(f"Added {added} track(s).")

    # ---------------- Metadata + Filtering ----------------
    def _read_metadata(self, path: Path) -> Track:
        title = ""
        artist = ""
        album = ""
        duration_ms = -1

        try:
            audio = MutagenFile(path)
            if audio is not None:
                duration_ms = int(getattr(audio.info, "length", 0) * 1000)
                tags = audio.tags
                if tags:
                    title = _safe_str(tags.get("TIT2") or tags.get("title"))
                    artist = _safe_str(tags.get("TPE1") or tags.get("artist"))
                    album = _safe_str(tags.get("TALB") or tags.get("album"))
        except Exception:
            pass

        if not title:
            title = path.stem

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

    def _focus_search(self) -> None:
        self.search.setFocus()
        self.search.selectAll()

    # ---------------- Playback order ----------------
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

    def _set_status(self, text: str) -> None:
        self._last_status = text
        self.status.setText(text)
        if self.tray:
            self.tray.setToolTip(text[:200])

    # ---------------- UI Events ----------------
    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        row = self.playlist.row(item)
        if row < 0 or row >= len(self.visible_indices):
            return
        idx = self.visible_indices[row]
        self._play_by_track_index(idx)

    def _play_by_track_index(self, idx: int) -> None:
        if idx < 0 or idx >= len(self.tracks):
            return

        # Update play_pos to match selected track in play_order.
        if idx in self.play_order:
            self.play_pos = self.play_order.index(idx)
        else:
            self._rebuild_play_order(keep_current=False)
            if idx in self.play_order:
                self.play_pos = self.play_order.index(idx)

        self._load_current_and_play()

    def _load_current_and_play(self) -> None:
        idx = self._current_track_index()
        if idx == -1:
            return

        t = self.tracks[idx]
        self.engine.load_path(t.path)
        self.engine.play()
        self.btn_play_pause.setText("Pause")

        extra = ""
        if t.album:
            extra = f"\nAlbum: {t.album}"

        self._set_status(f"Playing: {t.display}{extra}\n{t.path}")

        # Sync selection in list widget (if visible).
        if idx in self.visible_indices:
            row = self.visible_indices.index(idx)
            self.playlist.setCurrentRow(row)

    # ---------------- Controls ----------------
    def toggle_play_pause(self) -> None:
        if not self.play_order:
            QMessageBox.information(
                self,
                "No tracks",
                "Add MP3 files (or load a playlist) first.",
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

        cur = self._current_track_index()
        if cur != -1:
            self.history.append(cur)

        if self.repeat_one:
            self._load_current_and_play()
            return

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

        # If we have shuffle history, prefer it.
        if self.history:
            idx = self.history.pop()
            self._play_by_track_index(idx)
            return

        prv = self.play_pos - 1
        if prv < 0:
            if self.repeat_all:
                prv = len(self.play_order) - 1
            else:
                prv = 0

        self.play_pos = prv
        self._load_current_and_play()

    def _handle_track_end(self) -> None:
        # End of track: honor repeat_one/next/repeat_all behavior.
        if self.repeat_one:
            self._load_current_and_play()
            return
        self.play_next()

    def _on_shuffle_changed(self, state: int) -> None:
        self.shuffle = (state == Qt.CheckState.Checked.value)
        self.history = []
        self._rebuild_play_order(keep_current=True)
        self._set_status(f"Shuffle: {self.shuffle}")

    def _on_repeat_all_changed(self, state: int) -> None:
        self.repeat_all = (state == Qt.CheckState.Checked.value)
        self._set_status(f"Repeat All: {self.repeat_all}")

    def _on_repeat_one_changed(self, state: int) -> None:
        self.repeat_one = (state == Qt.CheckState.Checked.value)
        self._set_status(f"Repeat 1: {self.repeat_one}")

    # ---------------- Seek bar ----------------
    def _on_seek_start(self) -> None:
        self._user_seeking = True

    def _on_seek_end(self) -> None:
        self._user_seeking = False
        self._apply_seek_from_slider()

    def _on_seek_value_changed(self, value: int) -> None:
        if self._user_seeking:
            length = self.engine.get_length_ms()
            if length > 0:
                ms = int((value / 1000.0) * length)
                self.lbl_time.setText(
                    f"{_format_ms(ms)} / {_format_ms(length)}"
                )

    def _apply_seek_from_slider(self) -> None:
        length = self.engine.get_length_ms()
        if length <= 0:
            return
        value = self.slider.value()
        ms = int((value / 1000.0) * length)
        self.engine.set_time_ms(ms)

    def _tick(self) -> None:
        if self._user_seeking:
            return

        length = self.engine.get_length_ms()
        pos = self.engine.get_time_ms()

        if length > 0:
            ratio = pos / float(length)
            value = int(max(0.0, min(1.0, ratio)) * 1000.0)
            self.slider.blockSignals(True)
            self.slider.setValue(value)
            self.slider.blockSignals(False)
            self.lbl_time.setText(
                f"{_format_ms(pos)} / {_format_ms(length)}"
            )
        else:
            self.lbl_time.setText("00:00 / 00:00")

    # ---------------- Playlist JSON ----------------
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
            self._set_status(f"Saved playlist: {path}")
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
            if not p:
                continue
            if not Path(p).exists():
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
        self._set_status(f"Loaded playlist: {path}")

    # ---------------- Tray / window behavior ----------------
    def closeEvent(self, event) -> None:
        # Keep playing in background; minimize to tray.
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
        QApplication.quit()


def main() -> int:
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())