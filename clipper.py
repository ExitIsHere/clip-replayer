#!/usr/bin/env python3
"""
Clipper: Medal-like desktop clipping tool in Python

Continuously records the primary monitor to a circular buffer using FFmpeg. 
On global hotkey (F4 or F5), saves the last N seconds (default 120s) as a single MP4.

Platform priority: Windows. macOS/Linux best-effort with documented flags.

Dependencies (Python):
- keyboard (global hotkeys, Windows-friendly) OR pynput as fallback
- screeninfo (primary monitor detection)
- psutil (disk usage)

System dependency: FFmpeg (ffmpeg must be available in PATH)

Usage:
  python clipper.py --clip-length 120 --segment-time 10 --framerate 60

Hotkeys:
  F4 -> Save last 2 minutes (default)
  F5 -> Save last 2 minutes (default)

Notes:
- Run from an elevated Command Prompt on Windows for reliable hotkey capture in fullscreen games.
- Cursor flicker is minimized using gdigrab (Windows) with explicit framerate and draw_mouse.
  If your FFmpeg build supports ddagrab, see README for an alternative command template.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

try:
    import psutil  # type: ignore
except Exception:  # pragma: no cover - optional import error will surface later
    psutil = None

# Hotkey backend: prefer keyboard; fallback to pynput
try:  # pragma: no cover - tested via import success only
    import keyboard  # type: ignore
    _HOTKEY_BACKEND = "keyboard"
except Exception:  # pragma: no cover
    try:
        from pynput import keyboard as pynput_keyboard  # type: ignore
        _HOTKEY_BACKEND = "pynput"
    except Exception:
        _HOTKEY_BACKEND = "none"

# Monitor detection
try:  # pragma: no cover - tested via import success only
    from screeninfo import get_monitors  # type: ignore
except Exception:
    get_monitors = None  # type: ignore

APP_NAME = "Clipper"
DEFAULT_BUFFER_SECS = 120
DEFAULT_SEGMENT_SECS = 10
DEFAULT_FRAMERATE = 60

ROOT = Path(__file__).resolve().parent
BUFFER_DIR = ROOT / "buffer"
CLIPS_DIR = ROOT / "clips"
LOGS_DIR = ROOT / "logs"
LOG_FILE = LOGS_DIR / "clipper.log"


def setup_logging(verbose: bool = False) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    handlers: List[logging.Handler] = []
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    handlers.append(console)
    fileh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fileh.setLevel(logging.DEBUG)
    handlers.append(fileh)

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )


def which_ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg")


@dataclass
class MonitorRegion:
    width: int
    height: int
    offset_x: int
    offset_y: int


def detect_primary_monitor() -> MonitorRegion:
    """Detect the primary monitor region (width, height, offset_x, offset_y).
    Uses screeninfo when available; falls back to common 1920x1080 at (0,0).
    """
    if get_monitors is None:
        logging.warning("screeninfo not installed; assuming 1920x1080 at (0,0)")
        return MonitorRegion(1920, 1080, 0, 0)

    try:
        mons = get_monitors()
        primary = None
        for m in mons:
            # Most screeninfo builds expose is_primary; if absent, pick (0,0) or first
            if getattr(m, "is_primary", False):
                primary = m
                break
        if primary is None:
            # choose monitor with origin (0,0) else first
            primary = next((m for m in mons if getattr(m, "x", 0) == 0 and getattr(m, "y", 0) == 0), mons[0])
        width = int(getattr(primary, "width", 1920))
        height = int(getattr(primary, "height", 1080))
        offset_x = int(getattr(primary, "x", 0))
        offset_y = int(getattr(primary, "y", 0))
        return MonitorRegion(width, height, offset_x, offset_y)
    except Exception as e:  # pragma: no cover - depends on environment
        logging.exception("Failed to detect primary monitor: %s", e)
        return MonitorRegion(1920, 1080, 0, 0)


def get_active_window_title() -> str:
    """Try to obtain the current active window title. Platform-specific; safe fallback."""
    system = platform.system().lower()
    try:
        if system == "windows":
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.WinDLL("user32", use_last_error=True)
            GetForegroundWindow = user32.GetForegroundWindow
            GetWindowTextW = user32.GetWindowTextW
            GetWindowTextLengthW = user32.GetWindowTextLengthW

            hwnd = GetForegroundWindow()
            if not hwnd:
                return "unknown"
            length = GetWindowTextLengthW(hwnd)
            buff = ctypes.create_unicode_buffer(length + 1)
            GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value
            return title or "unknown"
        elif system == "darwin":
            # macOS best-effort using AppKit
            try:
                from AppKit import NSWorkspace  # type: ignore

                active_app = NSWorkspace.sharedWorkspace().frontmostApplication()
                return str(active_app.localizedName()) if active_app else "unknown"
            except Exception:
                return "unknown"
        else:
            # Linux best-effort using xprop if available
            if shutil.which("xprop"):
                try:
                    out = subprocess.check_output(
                        ["bash", "-lc", "xprop -root _NET_ACTIVE_WINDOW | awk '{print $5}' | sed 's/,//'"],
                        stderr=subprocess.DEVNULL,
                        text=True,
                    ).strip()
                    if out:
                        win_id = out
                        title = subprocess.check_output(
                            ["bash", "-lc", f"xprop -id {win_id} _NET_WM_NAME | cut -d '" -f2"],
                            stderr=subprocess.DEVNULL,
                            text=True,
                        ).strip()
                        return title or "unknown"
                except Exception:
                    pass
            return "unknown"
    except Exception:  # pragma: no cover - environment dependent
        return "unknown"


def sanitize_filename_component(s: str) -> str:
    s = s.strip()
    if not s:
        return "untitled"
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"[^\w\-\. ]+", "", s)
    s = s.replace(" ", "_")
    return s[:80]  # keep it reasonable


@dataclass
class Config:
    ffmpeg_path: str
    buffer_dir: Path
    clips_dir: Path
    clip_length: int = DEFAULT_BUFFER_SECS
    segment_time: int = DEFAULT_SEGMENT_SECS
    framerate: int = DEFAULT_FRAMERATE
    encoder: str = "libx264"
    preset: str = "veryfast"
    gop: int = 120  # ~2s GOP @60fps
    min_free_gb: int = 2


class Recorder:
    def __init__(self, cfg: Config, region: MonitorRegion):
        self.cfg = cfg
        self.region = region
        self.proc: Optional[subprocess.Popen] = None
        self.stop_event = threading.Event()
        self.cleanup_thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self.cfg.buffer_dir.mkdir(parents=True, exist_ok=True)
        cmd = self._build_ffmpeg_record_cmd()
        logging.debug("Recording command: %s", " ".join(cmd))
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            creationflags=(subprocess.CREATE_NO_WINDOW if platform.system().lower() == "windows" else 0),  # type: ignore[attr-defined]
        )
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self.cleanup_thread.start()
        logging.info("Recording started into %s", self.cfg.buffer_dir)

    def stop(self) -> None:
        self.stop_event.set()
        if self.proc and self.proc.poll() is None:
            logging.info("Stopping FFmpeg recorder...")
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            except Exception:
                pass
        self.proc = None

    def _cleanup_loop(self) -> None:
        """Maintain a soft circular buffer by deleting older segments beyond capacity or on low disk space."""
        capacity = int(self.cfg.clip_length / self.cfg.segment_time) * 3  # generous cushion
        while not self.stop_event.is_set():
            try:
                segs = sorted(self.cfg.buffer_dir.glob("buf-*.ts"), key=lambda p: p.stat().st_mtime)
                if len(segs) > capacity:
                    to_delete = segs[0 : len(segs) - capacity]
                    for p in to_delete:
                        try:
                            p.unlink(missing_ok=True)
                        except Exception:
                            pass
                if psutil:
                    try:
                        usage = psutil.disk_usage(str(self.cfg.clips_dir))
                        free_gb = usage.free / (1024 ** 3)
                        if free_gb < self.cfg.min_free_gb and segs:
                            # free some space by deleting 10% oldest buffer
                            count = max(1, len(segs) // 10)
                            for p in segs[:count]:
                                p.unlink(missing_ok=True)
                            logging.warning("Low disk space (%.2f GB). Pruned %d old segments.", free_gb, count)
                    except Exception:
                        pass
            except Exception:
                pass
            self.stop_event.wait(5)

    def _build_ffmpeg_record_cmd(self) -> List[str]:
        system = platform.system().lower()
        out_pattern = str(self.cfg.buffer_dir / "buf-%05d.ts")
        common_video = [
            "-c:v",
            self.cfg.encoder,
            "-preset",
            self.cfg.preset,
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
            "-g",
            str(self.cfg.gop),
            "-keyint_min",
            str(self.cfg.gop),
            "-sc_threshold",
            "0",
        ]
        segmenter = [
            "-f",
            "segment",
            "-segment_time",
            str(self.cfg.segment_time),
            "-reset_timestamps",
            "1",
            "-segment_format",
            "mpegts",
            out_pattern,
        ]

        if system == "windows":
            # Use gdigrab with explicit region for primary monitor, preserving cursor
            r = self.region
            input_sec = [
                "-f",
                "gdigrab",
                "-framerate",
                str(self.cfg.framerate),
                "-offset_x",
                str(r.offset_x),
                "-offset_y",
                str(r.offset_y),
                "-video_size",
                f"{r.width}x{r.height}",
                "-draw_mouse",
                "1",
                "-i",
                "desktop",
            ]
            return [self.cfg.ffmpeg_path, *input_sec, *common_video, *segmenter]
        elif system == "darwin":
            # Best-effort macOS (primary display). avfoundation device index for screen capture can vary.
            r = self.region
            input_sec = [
                "-f",
                "avfoundation",
                "-framerate",
                str(self.cfg.framerate),
                "-capture_cursor",
                "1",
                "-video_size",
                f"{r.width}x{r.height}",
                "-i",
                "1:none",  # may need adjustment per machine; see README
            ]
            return [self.cfg.ffmpeg_path, *input_sec, *common_video, *segmenter]
        else:
            # Linux X11
            r = self.region
            display = os.environ.get("DISPLAY", ":0.0")
            input_sec = [
                "-f",
                "x11grab",
                "-framerate",
                str(self.cfg.framerate),
                "-video_size",
                f"{r.width}x{r.height}",
                "-i",
                f"{display}+{r.offset_x},{r.offset_y}",
            ]
            return [self.cfg.ffmpeg_path, *input_sec, *common_video, *segmenter]


class ClipAssembler:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.lock = threading.Lock()

    def save_clip(self, length_seconds: Optional[int] = None) -> Optional[Path]:
        length = length_seconds or self.cfg.clip_length
        needed_segments = max(1, int((length + self.cfg.segment_time - 1) / self.cfg.segment_time))

        with self.lock:  # prevent concurrent assemblies
            # Check disk space
            if psutil:
                try:
                    usage = psutil.disk_usage(str(self.cfg.clips_dir))
                    if usage.free < 300 * 1024 * 1024:  # 300MB
                        logging.error("Insufficient free space to save clip.")
                        return None
                except Exception:
                    pass

            segs = sorted(self.cfg.buffer_dir.glob("buf-*.ts"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not segs:
                logging.error("No segments found; recording may not have started yet.")
                return None
            chosen = list(reversed(segs[:needed_segments]))  # chronological order

            # Prepare output path
            now = dt.datetime.now()
            ts = now.strftime("%Y%m%d_%H%M%S")
            active_title = sanitize_filename_component(get_active_window_title())
            duration_lab = f"{length}s"
            filename = f"{ts}_{duration_lab}_{active_title}.mp4"
            self.cfg.clips_dir.mkdir(parents=True, exist_ok=True)
            out_path = self.cfg.clips_dir / filename

            # Create concat list file
            with tempfile.NamedTemporaryFile("w", delete=False, suffix=".txt", dir=str(self.cfg.buffer_dir)) as tf:
                for p in chosen:
                    tf.write(f"file '{p.as_posix()}'\n")
                list_path = Path(tf.name)

            # First attempt: stream copy (fast)
            copy_cmd = [
                self.cfg.ffmpeg_path,
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(out_path),
            ]
            logging.info("Assembling clip (%ds) -> %s", length, out_path.name)
            logging.debug("Concat (copy) command: %s", " ".join(copy_cmd))
            try:
                subprocess.check_call(
                    copy_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.STDOUT,
                    creationflags=(subprocess.CREATE_NO_WINDOW if platform.system().lower() == "windows" else 0),  # type: ignore[attr-defined]
                )
                logging.info("Clip saved: %s", out_path)
                notify(f"Clip saved: {out_path}")
                return out_path
            except subprocess.CalledProcessError:
                logging.warning("Fast concat failed; retrying with re-encode...")
                # Fallback: re-encode to ensure a clean MP4
                reenc_cmd = [
                    self.cfg.ffmpeg_path,
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(list_path),
                    "-c:v",
                    self.cfg.encoder,
                    "-preset",
                    self.cfg.preset,
                    "-pix_fmt",
                    "yuv420p",
                    str(out_path),
                ]
                logging.debug("Concat (reencode) command: %s", " ".join(reenc_cmd))
                try:
                    subprocess.check_call(
                        reenc_cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.STDOUT,
                        creationflags=(subprocess.CREATE_NO_WINDOW if platform.system().lower() == "windows" else 0),  # type: ignore[attr-defined]
                    )
                    logging.info("Clip saved (re-encoded): %s", out_path)
                    notify(f"Clip saved: {out_path}")
                    return out_path
                except subprocess.CalledProcessError as e:
                    logging.exception("Failed to assemble clip: %s", e)
                    return None
                finally:
                    try:
                        list_path.unlink(missing_ok=True)
                    except Exception:
                        pass
            finally:
                try:
                    list_path.unlink(missing_ok=True)
                except Exception:
                    pass


def notify(message: str) -> None:
    """Best-effort user notification. On Windows, tries win10toast if present, else prints."""
    try:
        if platform.system().lower() == "windows":
            try:
                from win10toast import ToastNotifier  # type: ignore

                toaster = ToastNotifier()
                toaster.show_toast(APP_NAME, message, duration=5, threaded=True)
                return
            except Exception:
                pass
        print(f"[NOTICE] {message}")
    except Exception:
        pass


class Hotkeys:
    def __init__(self, assembler: ClipAssembler, default_length: int):
        self.assembler = assembler
        self.default_length = default_length
        self._listener = None

    def start(self) -> None:
        if _HOTKEY_BACKEND == "keyboard":
            keyboard.add_hotkey("F4", lambda: self._save(self.default_length))
            keyboard.add_hotkey("F5", lambda: self._save(self.default_length))
            logging.info("Hotkeys registered with 'keyboard': F4/F5 -> save last %ds", self.default_length)
        elif _HOTKEY_BACKEND == "pynput":
            def on_press(key):  # pragma: no cover - event driven
                try:
                    if key == pynput_keyboard.Key.f4 or key == pynput_keyboard.Key.f5:
                        self._save(self.default_length)
                except Exception:
                    pass
            self._listener = pynput_keyboard.Listener(on_press=on_press)
            self._listener.start()
            logging.info("Hotkeys registered with 'pynput': F4/F5 -> save last %ds", self.default_length)
        else:
            logging.error("No hotkey backend available. Install 'keyboard' or 'pynput'.")

    def wait_forever(self) -> None:
        if _HOTKEY_BACKEND == "keyboard":
            try:
                keyboard.wait()  # pragma: no cover - blocking call
            except KeyboardInterrupt:
                pass
        else:
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                pass

    def stop(self) -> None:
        if _HOTKEY_BACKEND == "keyboard":  # pragma: no cover
            try:
                keyboard.clear_all_hotkeys()
            except Exception:
                pass
        elif _HOTKEY_BACKEND == "pynput":  # pragma: no cover
            try:
                self._listener and self._listener.stop()
            except Exception:
                pass

    def _save(self, length: int) -> None:
        threading.Thread(target=self.assembler.save_clip, args=(length,), daemon=True).start()


def status_loop(cfg: Config, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            seg_count = len(list(cfg.buffer_dir.glob("buf-*.ts")))
            free_gb = 0.0
            if psutil:
                try:
                    usage = psutil.disk_usage(str(cfg.clips_dir))
                    free_gb = usage.free / (1024 ** 3)
                except Exception:
                    pass
            sys.stdout.write(
                f"\rRecording — buffer {cfg.clip_length}s | segments ~{seg_count} | free {free_gb:.2f} GB     "
            )
            sys.stdout.flush()
        except Exception:
            pass
        stop_event.wait(2)
    print()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Background clipper using FFmpeg segmented ring buffer")
    p.add_argument("--clip-length", type=int, default=DEFAULT_BUFFER_SECS, help="Clip length in seconds to save on hotkey")
    p.add_argument("--segment-time", type=int, default=DEFAULT_SEGMENT_SECS, help="Segment duration in seconds for the ring buffer")
    p.add_argument("--framerate", type=int, default=DEFAULT_FRAMERATE, help="Capture framerate")
    p.add_argument("--encoder", type=str, default="libx264", help="Video encoder (e.g., libx264, h264_nvenc)")
    p.add_argument("--preset", type=str, default="veryfast", help="Encoder preset (e.g., veryfast, fast)")
    p.add_argument("--verbose", action="store_true", help="Verbose logging")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)

    ffmpeg = which_ffmpeg()
    if not ffmpeg:
        logging.error("FFmpeg not found. Please install FFmpeg and ensure 'ffmpeg' is in your PATH.")
        logging.error("Windows: choco install ffmpeg  |  macOS: brew install ffmpeg  |  Linux: apt/yum install ffmpeg")
        return 2

    region = detect_primary_monitor()
    logging.info("Primary monitor: %dx%d at (%d,%d)", region.width, region.height, region.offset_x, region.offset_y)

    cfg = Config(
        ffmpeg_path=ffmpeg,
        buffer_dir=BUFFER_DIR,
        clips_dir=CLIPS_DIR,
        clip_length=args.clip_length,
        segment_time=args.segment_time,
        framerate=args.framerate,
        encoder=args.encoder,
        preset=args.preset,
        gop=max(30, int(args.framerate * 2)),  # ~2s GOP for smooth concatenation
    )

    recorder = Recorder(cfg, region)
    assembler = ClipAssembler(cfg)
    hotkeys = Hotkeys(assembler, default_length=cfg.clip_length)

    stop_event = threading.Event()

    def handle_signal(signum, frame):  # pragma: no cover - signal driven
        logging.info("Shutting down (%s)", signum)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handle_signal)
        except Exception:
            pass

    try:
        recorder.start()
        hotkeys.start()
        status_thr = threading.Thread(target=status_loop, args=(cfg, stop_event), daemon=True)
        status_thr.start()
        # Wait
        while not stop_event.is_set():
            time.sleep(0.5)
    finally:
        hotkeys.stop()
        recorder.stop()
        logging.info("Goodbye.")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
