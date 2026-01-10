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
from PyQt6.QtGui import QAction, QKeySequence, QShortcut
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

# ---------------- Modern Dark Theme ----------------
APP_QSS = """
QWidget {
    font-family: Segoe UI, Inter, Arial;
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

QListWidget {
    background: #0F1115;
    border: 1px solid #222634;
    border-radius: 14px;
    padding: 6px;
}
QListWidget::item {
    padding: 10px;
    margin: 3px;
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
    border-radius: 5px;
}
QSlider::handle:horizontal {
    width: 18px;
    margin: -6px 0;
    border-radius: 9px;
    background: #3B82F6;
}

QLabel#NowPlaying {
    background: #151924;
    border: 1px solid #2A3145;
    border-radius: 14px;
    padding: 14px;
    font-size: 11pt;
}
"""

# ---------------- Utilities ----------------
def fmt(ms: int) -> str:
    if ms < 0:
        return "00:00"
    s = ms // 1000
    return f"{s//60:02d}:{s%60:02d}"

def safe_str(v: Any) -> str:
    if isinstance(v, list) and v:
        return str(v[0])
    return str(v or "")

# ---------------- Data ----------------
@dataclass
class Track:
    path: str
    title: str
    artist: str
    album: str
    duration_ms: int

    @property
    def display(self) -> str:
        if self.artist:
            return f"{self.artist} – {self.title}"
        return self.title

# ---------------- VLC Engine ----------------
import vlc

class PlayerEngine:
    def __init__(self) -> None:
        vlc_dir = r"C:\Program Files\VideoLAN\VLC"
        if Path(vlc_dir).exists():
            os.add_dll_directory(vlc_dir)
            os.environ["VLC_PLUGIN_PATH"] = os.path.join(vlc_dir, "plugins")

        self.inst = vlc.Instance()
        self.player = self.inst.media_player_new()
        self.on_end = None

        self.player.event_manager().event_attach(
            vlc.EventType.MediaPlayerEndReached,
            self._ended,
        )

    def load(self, path: str) -> None:
        self.player.set_media(self.inst.media_new_path(path))

    def play(self) -> None:
        self.player.play()

    def pause(self) -> None:
        self.player.pause()

    def stop(self) -> None:
        self.player.stop()

    def playing(self) -> bool:
        return bool(self.player.is_playing())

    def time(self) -> int:
        return self.player.get_time()

    def length(self) -> int:
        return self.player.get_length()

    def seek(self, ms: int) -> None:
        self.player.set_time(ms)

    def _ended(self, event) -> None:
        if self.on_end:
            self.on_end()

# ---------------- Main Window ----------------
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("SAI MP3 Player")
        self.resize(1000, 680)

        self.engine = PlayerEngine()
        self.engine.on_end = self.next

        self.tracks: list[Track] = []
        self.order: list[int] = []
        self.pos = -1

        self.shuffle = False
        self.repeat_all = True
        self.repeat_one = False
        self.seeking = False

        self._ui()
        self._tray()
        self._shortcuts()
        self._timer()

        self.setAcceptDrops(True)

    # ---------- UI ----------
    def _ui(self) -> None:
        self.now = QLabel("Now Playing: —")
        self.now.setObjectName("NowPlaying")

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search...")
        self.search.textChanged.connect(self.refresh)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self.play_selected)

        self.btn_add = QPushButton("Add Files")
        self.btn_add.clicked.connect(self.add_files)

        self.btn_prev = QPushButton("◀")
        self.btn_play = QPushButton("Play")
        self.btn_next = QPushButton("▶")

        self.btn_prev.clicked.connect(self.prev)
        self.btn_play.clicked.connect(self.toggle)
        self.btn_next.clicked.connect(self.next)

        self.chk_shuffle = QCheckBox("Shuffle")
        self.chk_repeat = QCheckBox("Repeat All")
        self.chk_repeat.setChecked(True)
        self.chk_one = QCheckBox("Repeat 1")

        self.chk_shuffle.stateChanged.connect(self._opts)
        self.chk_repeat.stateChanged.connect(self._opts)
        self.chk_one.stateChanged.connect(self._opts)

        self.time_lbl = QLabel("00:00 / 00:00")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.sliderPressed.connect(lambda: setattr(self, "seeking", True))
        self.slider.sliderReleased.connect(self._seek_done)

        top = QHBoxLayout()
        top.addWidget(self.btn_add)
        top.addStretch()

        ctr = QHBoxLayout()
        ctr.addWidget(self.btn_prev)
        ctr.addWidget(self.btn_play)
        ctr.addWidget(self.btn_next)
        ctr.addStretch()
        ctr.addWidget(self.chk_shuffle)
        ctr.addWidget(self.chk_repeat)
        ctr.addWidget(self.chk_one)

        seek = QHBoxLayout()
        seek.addWidget(self.time_lbl)
        seek.addWidget(self.slider)

        root = QWidget()
        lay = QVBoxLayout(root)
        lay.addLayout(top)
        lay.addWidget(self.now)
        lay.addWidget(self.search)
        lay.addWidget(self.list, 1)
        lay.addLayout(ctr)
        lay.addLayout(seek)
        self.setCentralWidget(root)

    # ---------- Features ----------
    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Select MP3s", "", "Audio (*.mp3 *.wav *.flac)"
        )
        for p in paths:
            self._add(Path(p))
        self.refresh()

    def _add(self, p: Path) -> None:
        try:
            audio = MutagenFile(p)
            self.tracks.append(
                Track(
                    path=str(p),
                    title=safe_str(audio.tags.get("TIT2") if audio else p.stem),
                    artist=safe_str(audio.tags.get("TPE1") if audio else ""),
                    album=safe_str(audio.tags.get("TALB") if audio else ""),
                    duration_ms=int(audio.info.length * 1000) if audio else -1,
                )
            )
        except Exception:
            pass

    def refresh(self) -> None:
        q = self.search.text().lower()
        self.list.clear()
        self.order = []

        for i, t in enumerate(self.tracks):
            if q in f"{t.title} {t.artist} {t.album}".lower():
                self.order.append(i)
                self.list.addItem(QListWidgetItem(t.display))

        if self.shuffle:
            random.shuffle(self.order)

    def play_selected(self) -> None:
        row = self.list.currentRow()
        if row >= 0:
            self.pos = row
            self._play()

    def _play(self) -> None:
        idx = self.order[self.pos]
        t = self.tracks[idx]
        self.engine.load(t.path)
        self.engine.play()
        self.btn_play.setText("Pause")
        self.now.setText(f"Now Playing: {t.display}")

    def toggle(self) -> None:
        if self.engine.playing():
            self.engine.pause()
            self.btn_play.setText("Play")
        else:
            if self.pos < 0 and self.order:
                self.pos = 0
                self._play()
            else:
                self.engine.play()
                self.btn_play.setText("Pause")

    def next(self) -> None:
        if not self.order:
            return
        if self.repeat_one:
            self._play()
            return
        self.pos += 1
        if self.pos >= len(self.order):
            if not self.repeat_all:
                return
            self.pos = 0
        self._play()

    def prev(self) -> None:
        if not self.order:
            return
        self.pos = max(0, self.pos - 1)
        self._play()

    def _opts(self) -> None:
        self.shuffle = self.chk_shuffle.isChecked()
        self.repeat_all = self.chk_repeat.isChecked()
        self.repeat_one = self.chk_one.isChecked()
        self.refresh()

    # ---------- Seek ----------
    def _timer(self) -> None:
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(250)

    def _tick(self) -> None:
        if self.seeking:
            return
        dur = self.engine.length()
        pos = self.engine.time()
        if dur > 0:
            self.slider.setValue(int(pos / dur * 1000))
            self.time_lbl.setText(f"{fmt(pos)} / {fmt(dur)}")

    def _seek_done(self) -> None:
        self.seeking = False
        dur = self.engine.length()
        if dur > 0:
            self.engine.seek(int(self.slider.value() / 1000 * dur))

    # ---------- Tray ----------
    def _tray(self) -> None:
        self.tray = QSystemTrayIcon(self)
        self.tray.setIcon(self.style().standardIcon(
            self.style().StandardPixmap.SP_MediaPlay))
        menu = QMenu()
        menu.addAction("Play/Pause", self.toggle)
        menu.addAction("Next", self.next)
        menu.addAction("Quit", QApplication.quit)
        self.tray.setContextMenu(menu)
        self.tray.show()

    def closeEvent(self, e) -> None:
        self.hide()
        e.ignore()

    def _shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self.toggle)
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self.next)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, self.prev)

# ---------------- Entry ----------------
def main() -> int:
    app = QApplication(sys.argv)
    app.setStyleSheet(APP_QSS)
    win = MainWindow()
    win.show()
    return app.exec()

if __name__ == "__main__":
    raise SystemExit(main())