from pathlib import Path
from setuptools import setup


here = Path(__file__).parent
readme_path = here / "README.md"
long_description = ""
if readme_path.exists():
    long_description = readme_path.read_text(encoding="utf-8")


setup(
    name="sai-mp3-player",
    version="0.1.0",
    description="Slick MP3 player with themes, TTS, and VLC backend (PyQt6)",
    long_description=long_description or "Slick MP3 player with themes, TTS, and VLC backend (PyQt6).",
    long_description_content_type="text/markdown",
    author="Sai",
    url="",
    # The package 'mp3_player' lives in the current directory (this folder)
    packages=["mp3_player"],
    package_dir={"mp3_player": "."},
    include_package_data=True,
    exclude_package_data={
        "mp3_player": ["setup.py", "pyproject.toml", "README.md"],
    },
    python_requires=">=3.10",
    install_requires=[
        "PyQt6>=6.4",
        "mutagen>=1.45",
        "python-vlc>=3.0.0",
    ],
    entry_points={
        "console_scripts": [
            (
                "sai-mp3-player="
                "mp3_player.sai_mp3_player_slick_theme_voice_autoplay_tts_fix:main"
            )
        ]
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3 :: Only",
        "Operating System :: OS Independent",
        "Environment :: X11 Applications :: Qt",
        "License :: OSI Approved :: MIT License",
        "Topic :: Multimedia :: Sound/Audio :: Players",
    ],
    license="MIT",
)
