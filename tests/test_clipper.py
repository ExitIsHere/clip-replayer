import os
import shutil
import sys
from pathlib import Path

import importlib
import types
import pytest

# Ensure module import from project root
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

clipper = importlib.import_module("clipper")


def test_sanitize_filename_component_basic():
    s = "My Awesome Game: Level #1!"
    out = clipper.sanitize_filename_component(s)
    assert out.startswith("My_Awesome_Game")
    assert "#" not in out
    assert ":" not in out


def test_ffmpeg_detection_mocked(tmp_path, monkeypatch):
    # Simulate presence of ffmpeg in PATH by crafting a dummy executable
    fake_bin = tmp_path / ("ffmpeg" + (".exe" if os.name == "nt" else ""))
    fake_bin.write_text("#!/bin/sh\nexit 0\n")
    if os.name != "nt":
        fake_bin.chmod(0o755)
    old_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + old_path)
    assert shutil.which("ffmpeg")
    assert clipper.which_ffmpeg()


@pytest.mark.skipif(clipper.get_monitors is None, reason="screeninfo not installed in test env")
def test_detect_primary_monitor_has_fields():
    r = clipper.detect_primary_monitor()
    assert isinstance(r.width, int)
    assert isinstance(r.height, int)
    assert isinstance(r.offset_x, int)
    assert isinstance(r.offset_y, int)
