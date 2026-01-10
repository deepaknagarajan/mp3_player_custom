# sreesaibaba

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import vlc
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


@dataclass
class Track:
    path: Path

    @property
    def title(self) -> str:
        return self.path.stem


class PlayerEngine:
    def __init__(self) -> None:
        self._vlc_instance = vlc.Instance()
        self._player = self._vlc_instance.media_player_new()
        self._current_path: Path | None = None
        self.repeat_one = False

        events = self._player.event_manager()
        events.event_attach(
            vlc.EventType.MediaPlayerEndReached,
            self._on_end_reached,
        )

        self._end_callback = None

    def set_on_track_end(self, callback) -> None:
        self._end_callback = callback

    def load(self, path: Path) -> None:
        self._current_path = path
        media = self._vlc_instance.media_new(str(path))
        self._player.set_media(media)

    def play(self) -> None:
        self._player.play()

    def pause(self) -> None:
        self._player.pause()

    def stop(self) -> None:
        self._player.stop()

    def is_playing(self) -> bool:
        return bool(self._player.is_playing())

    def _on_end_reached(self, event) -> None:
        if self.repeat_one and self._current_path is not None:
            self.load(self._current_path)
            self.play()
            return

        if self._end_callback is not None:
            self._end_callback()


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MP3 Playlist Player (Starter)")
        self.resize(900, 500)

        self.engine = PlayerEngine()
        self.engine.set_on_track_end(self.play_next)

        self.tracks: list[Track] = []
        self.current_index: int = -1

        self.playlist = QListWidget()
        self.playlist.itemDoubleClicked.connect(self._on_item_double_clicked)

        self.status = QLabel("No track loaded.")
        self.status.setWordWrap(True)

        self.btn_add_files = QPushButton("Add Files")
        self.btn_add_folder = QPushButton("Add Folder")
        self.btn_prev = QPushButton("◀ Prev")
        self.btn_play_pause = QPushButton("Play")
        self.btn_next = QPushButton("Next ▶")
        self.chk_repeat_one = QCheckBox("Repeat 1")

        self.btn_add_files.clicked.connect(self.add_files)
        self.btn_add_folder.clicked.connect(self.add_folder)
        self.btn_prev.clicked.connect(self.play_prev)
        self.btn_play_pause.clicked.connect(self.toggle_play_pause)
        self.btn_next.clicked.connect(self.play_next)
        self.chk_repeat_one.stateChanged.connect(self._on_repeat_changed)

        controls_row = QHBoxLayout()
        controls_row.addWidget(self.btn_add_files)
        controls_row.addWidget(self.btn_add_folder)
        controls_row.addStretch(1)
        controls_row.addWidget(self.btn_prev)
        controls_row.addWidget(self.btn_play_pause)
        controls_row.addWidget(self.btn_next)
        controls_row.addWidget(self.chk_repeat_one)

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.addLayout(controls_row)
        layout.addWidget(self.playlist, stretch=1)
        layout.addWidget(self.status)

        self.setCentralWidget(root)

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Select MP3 files",
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
        start_len = len(self.tracks)
        for p in paths:
            self.tracks.append(Track(path=p))
            item = QListWidgetItem(p.stem)
            item.setToolTip(str(p))
            self.playlist.addItem(item)

        if start_len == 0 and self.tracks:
            self.current_index = 0
            self.playlist.setCurrentRow(0)
            self._load_current(update_status=True)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        row = self.playlist.row(item)
        self.play_index(row)

    def play_index(self, index: int) -> None:
        if index < 0 or index >= len(self.tracks):
            return

        self.current_index = index
        self.playlist.setCurrentRow(index)
        self._load_current(update_status=True)
        self.engine.play()
        self.btn_play_pause.setText("Pause")

    def _load_current(self, update_status: bool) -> None:
        if self.current_index < 0 or self.current_index >= len(self.tracks):
            return

        track = self.tracks[self.current_index]
        self.engine.load(track.path)

        if update_status:
            self.status.setText(f"Loaded: {track.title}\n{track.path}")

    def toggle_play_pause(self) -> None:
        if not self.tracks:
            QMessageBox.information(
                self,
                "No tracks",
                "Add some MP3 files first.",
            )
            return

        if self.current_index == -1:
            self.current_index = 0
            self.playlist.setCurrentRow(0)
            self._load_current(update_status=True)

        if self.engine.is_playing():
            self.engine.pause()
            self.btn_play_pause.setText("Play")
        else:
            self.engine.play()
            self.btn_play_pause.setText("Pause")

    def play_next(self) -> None:
        if not self.tracks:
            return

        nxt = self.current_index + 1
        if nxt >= len(self.tracks):
            nxt = 0

        self.play_index(nxt)

    def play_prev(self) -> None:
        if not self.tracks:
            return

        prv = self.current_index - 1
        if prv < 0:
            prv = len(self.tracks) - 1

        self.play_index(prv)

    def _on_repeat_changed(self, state: int) -> None:
        self.engine.repeat_one = (state == Qt.CheckState.Checked.value)


def main() -> int:
    # On Windows, VLC discovery can fail if PATH doesn't include VLC.
    # If you hit VLC DLL errors, install VLC and try again.
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())