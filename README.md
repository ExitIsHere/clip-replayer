# Clipper — Medal-like Desktop Clipping Tool (Python)

Clipper continuously records your primary monitor into a circular buffer using FFmpeg. Press F4 or F5 to instantly save the last 2 minutes as a single MP4 in the `clips/` folder.

- Windows: first-class support (recommended)
- macOS/Linux: best-effort support with documented FFmpeg flags

## Features
- Continuous low-overhead background recording into fixed-length segments (ring buffer)
- Global hotkeys (F4/F5) work even when a fullscreen game is active
- Primary monitor auto-detection (via `screeninfo`), cursor captured smoothly
- Fast saving: concatenates recent segments (copy/remux); re-encodes only if needed
- Clean MP4 output with H.264 (`libx264`, veryfast), `+faststart`
- Metadata in filename: timestamp, duration, active window title
- Logging to `logs/clipper.log`, graceful shutdown, disk-space checks
- Minimal CLI status line (segments count, free disk)

## Quick Start
1) Install FFmpeg (required)
- Windows (Admin PowerShell): `choco install ffmpeg`  (or download from https://www.gyan.dev/ffmpeg/builds/)
- macOS: `brew install ffmpeg`
- Ubuntu/Debian: `sudo apt-get install ffmpeg`

2) Create a Python venv and install deps:
```bash
python -m venv .venv
. .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install keyboard screeninfo psutil  # optional: pynput win10toast
```

3) Run Clipper:
```bash
python clipper.py --clip-length 120 --segment-time 10 --framerate 60
```
- Run as Administrator on Windows for the most reliable global hotkeys.

4) Save a clip: Press F4 or F5 → MP4 saved to `clips/`.

## How It Works
Clipper spawns FFmpeg to continuously write small `.ts` segments (`-f segment -segment_time 10`). The last N segments represent your time buffer (e.g., 12 segments = ~120s). When you press a hotkey, Clipper concatenates the most recent segments into a timestamped MP4:
- First try: `-c copy` (fast remux, near-instant)
- Fallback: re-encode with `libx264` for a guaranteed clean MP4

This approach avoids keeping huge data in RAM, keeps CPU usage low, and makes saving fast.

## Windows FFmpeg Command (used by the script)
```
ffmpeg -f gdigrab -framerate 60 -offset_x <X> -offset_y <Y> -video_size <WxH> -draw_mouse 1 -i desktop \
  -c:v libx264 -preset veryfast -tune zerolatency -pix_fmt yuv420p -g 120 -keyint_min 120 -sc_threshold 0 \
  -f segment -segment_time 10 -reset_timestamps 1 -segment_format mpegts buffer/buf-%05d.ts
```
- To further reduce cursor flicker on supported builds, you can try `ddagrab` (DirectX Desktop Duplication):
```
ffmpeg -f ddagrab -framerate 60 -i desktop \
  -c:v libx264 -preset veryfast -tune zerolatency -pix_fmt yuv420p -g 120 -keyint_min 120 -sc_threshold 0 \
  -f segment -segment_time 10 -reset_timestamps 1 -segment_format mpegts buffer/buf-%05d.ts
```
Note: `ddagrab` may capture all displays; `gdigrab` is used with an explicit region for the primary monitor.

## macOS / Linux Notes
- macOS (avfoundation) template:
```
ffmpeg -f avfoundation -framerate 60 -capture_cursor 1 -video_size <WxH> -i 1:none ...
```
Device index may vary; check with `ffmpeg -f avfoundation -list_devices true -i ""`.

- Linux (X11):
```
ffmpeg -f x11grab -framerate 60 -video_size <WxH> -i :0.0+<X>,<Y> ...
```
Wayland may require switching to an Xorg session or using a portal-based capture tool.

## Hotkeys
- F4 → Save last 2 minutes (default)
- F5 → Save last 2 minutes (default)

Backend selection:
- `keyboard` (default) — reliable on Windows. May need Admin privileges for fullscreen capture.
- `pynput` — automatic fallback if `keyboard` unavailable.

## File Naming
```
YYYYMMDD_HHMMSS_<duration>s_<active-window-title>.mp4
```
Example: `20250110_142312_120s_Apex_Legends.mp4`

## Configuration
- `--clip-length` seconds (default: 120)
- `--segment-time` seconds (default: 10)
- `--framerate` (default: 60)
- `--encoder` (default: libx264)
- `--preset` (default: veryfast)

Edit these flags as needed. Hardware encoders (e.g., `h264_nvenc`) can be used if your FFmpeg supports them.

## Notifications
Clipper prints a message and, on Windows, will use toast notifications if `win10toast` is installed:
```bash
pip install win10toast
```

## Logging
When packaged as a Windows .exe (no console), logs are written to `logs.txt` next to the executable.

## Windows .exe Build (PyInstaller)
Build a single-file, no-console executable in `dist/`:

1) Install build deps:
```bash
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

2) (Optional) Place `ffmpeg.exe` at `vendor/ffmpeg/ffmpeg.exe` before building to embed it. Otherwise, after building, copy `ffmpeg.exe` next to the final `Clipper.exe`.

3) Build using the provided spec (recommended):
```bash
pyinstaller clipper.spec
```
This produces `dist/Clipper/Clipper.exe`.

- Alternative (simple flags):
```bash
pyinstaller --onefile --noconsole --name Clipper clipper.py
```
(Then manually place `ffmpeg.exe` next to `Clipper.exe`.)

4) Run:
- Double-click `dist/Clipper/Clipper.exe`
- On first launch, `clips/` is created automatically. Press F4/F5 to save clips. Check `logs.txt` for errors.

Notes:
- Global hotkeys may require Administrator privileges to work in fullscreen games.
- If FFmpeg isn’t found, put `ffmpeg.exe` alongside `Clipper.exe` or ensure it’s in PATH.

## Troubleshooting
- "FFmpeg not found": Place `ffmpeg.exe` next to `Clipper.exe` or ensure PATH contains FFmpeg.
- Hotkeys not firing in fullscreen: Run `Clipper.exe` as Administrator or try installing with `pynput`.
- No segments appear: OS security prompts may block screen capture; adjust permissions.
- Output won’t play: The tool retries with re-encode automatically; check `logs.txt`.

## Development & Tests
- Single entry point: `clipper.py`
- Unit tests (examples): `tests/test_clipper.py`
Run with pytest:
```bash
pip install pytest
pytest -q
```

## Security & Privacy
- Clipper does not upload anything anywhere. To add uploads, implement your own post-save hook in `clipper.py` and ensure explicit consent.

## License
MIT
