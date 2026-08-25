#!/usr/bin/env python3
"""
label_activity.py -- interactive scrub-and-label tool for marking real,
human-observed activity windows in a pre-recorded habitat_camera test video.
This is independent ground truth: it does not run HabitatMotionDetector at
all, it just lets a person scrub through footage and mark what they actually
see. The output labels CSV (start_utc/end_utc, matching the _timestamps.csv
"timestamp_utc" format) is meant to be fed to replay_habitat_motion.py's
--compare-labels, which does run the real detector/threshold logic and
reports how many labeled segments it would have caught -- so tuning
activity_threshold/inactivity_min_duration_s against real footage becomes:
label once here, then re-run replay_habitat_motion.py with different
--config values against the same labels.

Controls (a window pops up once the video loads -- click it for keyboard
focus, cv2's window doesn't grab it automatically):
  space       play / pause
  , / .       step back / forward 1 frame (while paused)
  [ / ]       step back / forward ~1s (25 frames; while paused)
  i           mark IN point (start of an interesting segment) at current frame
  o           mark OUT point (end of segment) -- commits the pending segment
  1 / 2       tag the just-committed segment minor / major (optional, right
              after pressing 'o' -- e.g. minor = resident shifting in its
              sleep, major = an intruder entering/moving/leaving frame)
  u           undo the last committed segment
  s           save labels to the output CSV now (also auto-saved on quit)
  q / Esc     save and quit
  trackbar    drag to seek -- this is compressed (H264-in-.ts) video, so a
              drag may take a moment and land on the nearest keyframe rather
              than the exact frame; use the frame counter overlay, not the
              trackbar, when you need to be precise about an edge.

The legend and a running list of committed segment timestamps are shown in
a permanent panel to the left of the video (not overlaid on top of it) --
one composited image, panel + letterboxed video side by side, so nothing
ever covers the footage itself. A colored timeline strip along the bottom
shows every committed segment's position across the WHOLE video at a glance
(red = major, orange = minor, gray = untagged), with a white marker for the
current playback position -- click anywhere on it to seek there directly.

Usage:
    python3 src/modules/variants/habitat_camera/analysis/label_activity.py [VIDEO.ts]
        [--timestamps CSV] [--labels-out LABELS.csv] [--start-frame N]

    Omit VIDEO.ts to pick one via a file-open dialog instead of typing a path.
"""

import argparse
import csv
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog

import cv2
import numpy as np

_DISPLAY_MAX_WIDTH = 900
_BIG_STEP = 25  # ~1s at 25fps -- not read from config, tool is fps-agnostic
_MAX_SHOWN = 15  # most recent committed segments to list in the side panel
_PANEL_W = 300  # fixed-width side panel, drawn as its own region, not over the video
_PANEL_BG = (35, 35, 35)
_PANEL_LINE_H = 20
_TIMELINE_H = 28  # colored segment-overview strip along the bottom of the window
_TIMELINE_BG = (50, 50, 50)
_SEVERITY_COLOR = {"major": (0, 0, 220), "minor": (0, 170, 220), "": (150, 150, 150)}
_PLAYHEAD_COLOR = (255, 255, 255)


def _load_frame_metadata(csv_path: Path) -> list[dict]:
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def _letterbox(frame, target_w: int, target_h: int):
    """Scale frame to fit within target_w x target_h preserving aspect ratio,
    padding the remainder with black -- see the call site's comment on why
    cv2.imshow can't be trusted to do this itself for a resizable window."""
    h, w = frame.shape[:2]
    if target_w <= 0 or target_h <= 0 or w <= 0 or h <= 0:
        return frame
    scale = min(target_w / w, target_h / h)
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
    resized = cv2.resize(frame, (new_w, new_h))
    pad_w, pad_h = target_w - new_w, target_h - new_h
    top, bottom = pad_h // 2, pad_h - pad_h // 2
    left, right = pad_w // 2, pad_w - pad_w // 2
    return cv2.copyMakeBorder(resized, top, bottom, left, right,
                               cv2.BORDER_CONSTANT, value=(0, 0, 0))


def _build_panel(height: int, width: int, lines: list[str]):
    """A dedicated side-panel image (own space, not composited over the
    video) with each line of text drawn top-down, clipped once it runs past
    the available height rather than overflowing/crashing."""
    panel = np.full((max(1, height), width, 3), _PANEL_BG, dtype=np.uint8)
    for i, line in enumerate(lines):
        y = 20 + _PANEL_LINE_H * i
        if y >= height:
            break
        if not line:
            continue
        cv2.putText(panel, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX,
                    0.45, (0, 255, 0), 1, cv2.LINE_AA)
    return panel


def _build_timeline(width: int, video_t0: datetime, video_span_s: float,
                     segments: list[list[str]], current_dt: datetime):
    """Whole-video overview strip: each committed segment drawn as a colored
    block at its proportional position (color by severity), plus a marker
    for the current playhead -- so it's visible at a glance where segments
    sit relative to the whole video, not just the current scrub position."""
    timeline = np.full((_TIMELINE_H, max(1, width), 3), _TIMELINE_BG, dtype=np.uint8)
    if video_span_s <= 0:
        return timeline

    def x_of(dt: datetime) -> int:
        frac = (dt - video_t0).total_seconds() / video_span_s
        return max(0, min(width - 1, round(frac * width)))

    for start, end, severity in segments:
        x0 = x_of(datetime.fromisoformat(start))
        x1 = max(x0 + 2, x_of(datetime.fromisoformat(end)))
        color = _SEVERITY_COLOR.get(severity, _SEVERITY_COLOR[""])
        cv2.rectangle(timeline, (x0, 2), (x1, _TIMELINE_H - 3), color, -1)

    px = x_of(current_dt)
    cv2.line(timeline, (px, 0), (px, _TIMELINE_H - 1), _PLAYHEAD_COLOR, 2)
    return timeline


def _write_labels(path: Path, segments: list[list[str]]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["start_utc", "end_utc", "severity"])
        for start, end, severity in segments:
            w.writerow([start, end, severity])


def _load_existing_labels(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return [
            [row["start_utc"], row["end_utc"], row.get("severity", "")]
            for row in csv.DictReader(f)
        ]


def _pick_video_path() -> str | None:
    """One-shot tkinter file-open dialog -- used only when no video path was
    given on the command line. No persistent Tk window/mainloop: create a
    hidden root just long enough to show the dialog, then tear it down
    before cv2 opens its own window, so the two GUI toolkits never overlap."""
    root = tk.Tk()
    root.withdraw()
    path = filedialog.askopenfilename(
        title="Select a habitat_camera test video",
        filetypes=[("Video files", "*.ts *.mp4 *.h264"), ("All files", "*.*")],
    )
    root.destroy()
    return path or None


def _short_ts(iso: str) -> str:
    """HH:MM:SS from a timestamp_utc ISO string, for compact onscreen display."""
    return iso[11:19] if len(iso) >= 19 else iso


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("video", nargs="?",
                     help="video file; omit to pick one via a file-open dialog")
    ap.add_argument("--timestamps",
                     help="paired _timestamps.csv (default: derived from video path)")
    ap.add_argument("--labels-out",
                     help="output labels CSV (default: <video_stem>_labels.csv)")
    ap.add_argument("--start-frame", type=int, default=0)
    args = ap.parse_args()

    video_arg = args.video or _pick_video_path()
    if not video_arg:
        sys.exit("No video selected")
    video_path = Path(video_arg)
    ts_path = Path(args.timestamps) if args.timestamps else (
        video_path.parent / f"{video_path.stem}_timestamps.csv"
    )
    if not ts_path.exists():
        sys.exit(f"No timestamps CSV found at {ts_path} -- pass --timestamps")

    labels_out = Path(args.labels_out) if args.labels_out else (
        video_path.parent / f"{video_path.stem}_labels.csv"
    )

    rows = _load_frame_metadata(ts_path)
    n_frames = len(rows)
    if n_frames == 0:
        sys.exit(f"No rows in {ts_path}")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        sys.exit(f"Could not open video: {video_path}")

    frame_idx = max(0, min(args.start_frame, n_frames - 1))
    cap_pos = -1  # where the decoder actually is; forces a seek on first read
    t0 = datetime.fromisoformat(rows[0]["timestamp_utc"])
    video_span_s = (datetime.fromisoformat(rows[-1]["timestamp_utc"]) - t0).total_seconds()

    def get_frame(idx: int):
        # Seeking by cv2.CAP_PROP_POS_FRAMES is NOT reliable for this .ts
        # container -- measured live, it lands ~17% short of the requested
        # frame's real position (consistent with the ffmpeg backend assuming
        # 30fps for a stream that's actually 25fps), growing to well over a
        # minute of error by partway through a 20-minute video. Seeking by
        # CAP_PROP_POS_MSEC instead, using the target frame's OWN real
        # timestamp from the CSV (ground truth, independent of any fps
        # assumption) rather than an assumed frame rate, measured accurate to
        # within ~0.1-0.3s in the same test. Any jump here (trackbar drag,
        # '['/']' big-step) previously risked landing on a completely
        # different, unrelated moment than the one shown/recorded -- meaning
        # a labeled timestamp could silently not match what was actually
        # onscreen when 'i'/'o' was pressed. Sequential playback (space,
        # single-frame ','/'.' step) was never affected -- it only advances
        # via cap.read(), no seek involved.
        nonlocal cap_pos
        if idx != cap_pos:
            target_ms = (datetime.fromisoformat(rows[idx]["timestamp_utc"]) - t0).total_seconds() * 1000
            cap.set(cv2.CAP_PROP_POS_MSEC, target_ms)
        ok, frame = cap.read()
        cap_pos = idx + 1 if ok else -1
        return frame if ok else None

    segments: list[list[str]] = _load_existing_labels(labels_out)
    if segments:
        print(f"Loaded {len(segments)} existing segment(s) from {labels_out} -- "
              f"continuing to add to them (press 'u' if you want to remove one)")
    pending_start_idx: int | None = None
    playing = False

    window = "label_activity  --  space play/pause, i/o mark, u undo, s save, q quit"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    def on_trackbar(pos: int) -> None:
        nonlocal frame_idx
        frame_idx = pos

    cv2.createTrackbar("frame", window, frame_idx, max(0, n_frames - 1), on_trackbar)

    # Click-to-seek on the colored timeline strip at the bottom of the
    # window -- layout is updated every frame below (it depends on the
    # current window size, which can change), so the callback always reads
    # the up-to-date geometry rather than a stale value captured at setup.
    layout = {"width": 1, "timeline_top": 0}

    def on_mouse(event, x, y, _flags, _param) -> None:
        nonlocal frame_idx
        if event == cv2.EVENT_LBUTTONDOWN and y >= layout["timeline_top"]:
            frac = max(0.0, min(1.0, x / max(1, layout["width"])))
            frame_idx = min(n_frames - 1, max(0, round(frac * (n_frames - 1))))

    cv2.setMouseCallback(window, on_mouse)

    # Size the window to the video's own aspect ratio (plus the side panel)
    # up front -- otherwise the first frame renders into whatever default
    # rect the OS gave the new window, which is almost never the right shape.
    probe = get_frame(frame_idx)
    if probe is not None:
        ph, pw = probe.shape[:2]
        video_init_w = min(_DISPLAY_MAX_WIDTH, pw)
        init_h = max(1, round(video_init_w * ph / pw))
        cv2.resizeWindow(window, video_init_w + _PANEL_W, init_h)

    print(f"Loaded {n_frames} frames from {video_path.name}")
    print(f"Labels will be saved to {labels_out}")

    last_idx = -1
    frame = None

    while True:
        # The OS titlebar close button destroys the window without going
        # through our key handling -- without this check, the next iteration's
        # cv2.getWindowImageRect/imshow calls crash on the now-dead window
        # instead of exiting cleanly (found live: closing via the X while
        # mid-session raised "NULL window" from getWindowImageRect).
        if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
            break

        if playing or frame_idx != last_idx:
            new_frame = get_frame(frame_idx)
            if new_frame is not None:
                frame = new_frame
            elif playing:
                playing = False  # ran off the end
            last_idx = frame_idx

        if frame is None:
            break

        # cv2.imshow stretches whatever image it's given to fill the window's
        # current size -- with a resizable (WINDOW_NORMAL) window that means
        # dragging/maximizing warps the picture unless we do the fit
        # ourselves. Query the real window size, reserve _PANEL_W of it for
        # the side panel, and letterbox the video into what's left, then
        # concatenate the two -- so the panel is genuinely its own region,
        # never drawn on top of the footage (see this file's history: an
        # overlay-on-top-of-video approach was tried first and didn't work
        # out -- covered the picture, grew as more got labeled, felt
        # obtrusive at any fixed size. A real side-by-side layout like this
        # is what a tkinter rewrite would have bought too, without needing
        # the rewrite: cv2 can already composite two image regions.)
        _, _, win_w, win_h = cv2.getWindowImageRect(window)
        if win_w <= 0 or win_h <= 0:
            h0, w0 = frame.shape[:2]
            win_w = min(_DISPLAY_MAX_WIDTH, w0) + _PANEL_W
            win_h = max(1, round((win_w - _PANEL_W) * h0 / w0))
        video_w = max(50, win_w - _PANEL_W)
        video_disp = _letterbox(frame, video_w, win_h)

        ts = rows[frame_idx]["timestamp_utc"] if frame_idx < len(rows) else "?"
        pending_note = (
            f"  IN marked @ {rows[pending_start_idx]['timestamp_utc']}"
            if pending_start_idx is not None else ""
        )
        status_lines = [
            f"frame {frame_idx}/{n_frames - 1}",
            ts,
            ("PLAYING" if playing else "paused") + pending_note,
        ]
        legend_lines = [
            "space:play/pause",
            ",/.: step 1 frame",
            "[/]: step ~1s",
            "i: mark IN",
            "o: mark OUT",
            "1/2: tag last minor/major",
            "u: undo   s: save",
            "q/Esc: save+quit",
        ]
        shown = segments[-_MAX_SHOWN:]
        hidden_count = len(segments) - len(shown)
        segment_lines = [f"{len(segments)} segment(s):"]
        if hidden_count > 0:
            segment_lines.append(f"  ... ({hidden_count} earlier)")
        for i, (start, end, severity) in enumerate(shown, len(segments) - len(shown) + 1):
            tag = f" [{severity}]" if severity else ""
            segment_lines.append(f"  {i}) {_short_ts(start)}-{_short_ts(end)}{tag}")

        panel_lines = status_lines + [""] + legend_lines + [""] + segment_lines
        panel = _build_panel(win_h, _PANEL_W, panel_lines)

        frame_disp = cv2.hconcat([panel, video_disp])

        current_dt = datetime.fromisoformat(rows[min(frame_idx, len(rows) - 1)]["timestamp_utc"])
        timeline = _build_timeline(
            frame_disp.shape[1], t0, video_span_s, segments, current_dt
        )
        layout["width"] = frame_disp.shape[1]
        layout["timeline_top"] = frame_disp.shape[0]
        frame_disp = cv2.vconcat([frame_disp, timeline])

        cv2.imshow(window, frame_disp)
        cv2.setTrackbarPos("frame", window, frame_idx)

        key = cv2.waitKey(30 if playing else 0) & 0xFF

        if key == ord(' '):
            playing = not playing
        elif key in (27, ord('q')):
            break
        elif key == ord('i'):
            pending_start_idx = frame_idx
            print(f"IN marked at frame {frame_idx} ({ts})")
        elif key == ord('o'):
            if pending_start_idx is None:
                print("No IN point set -- press 'i' first")
            else:
                start_ts = rows[pending_start_idx]["timestamp_utc"]
                end_ts = rows[frame_idx]["timestamp_utc"]
                if end_ts < start_ts:
                    start_ts, end_ts = end_ts, start_ts
                segments.append([start_ts, end_ts, ""])
                print(f"Segment committed: {start_ts} -> {end_ts}  "
                      f"(press 1=minor / 2=major to tag it)")
                pending_start_idx = None
        elif key == ord('1'):
            if segments:
                segments[-1][2] = "minor"
                print(f"Tagged last segment: minor  {segments[-1]}")
        elif key == ord('2'):
            if segments:
                segments[-1][2] = "major"
                print(f"Tagged last segment: major  {segments[-1]}")
        elif key == ord('u'):
            if segments:
                print(f"Undid segment {segments.pop()}")
        elif key == ord('s'):
            _write_labels(labels_out, segments)
            print(f"Saved {len(segments)} segment(s) to {labels_out}")
        elif key == ord(','):
            frame_idx = max(0, frame_idx - 1)
            playing = False
        elif key == ord('.'):
            frame_idx = min(n_frames - 1, frame_idx + 1)
            playing = False
        elif key == ord('['):
            frame_idx = max(0, frame_idx - _BIG_STEP)
            playing = False
        elif key == ord(']'):
            frame_idx = min(n_frames - 1, frame_idx + _BIG_STEP)
            playing = False

        if playing:
            frame_idx = min(n_frames - 1, frame_idx + 1)

    cap.release()
    cv2.destroyAllWindows()
    _write_labels(labels_out, segments)
    print(f"Saved {len(segments)} segment(s) to {labels_out}")


if __name__ == "__main__":
    main()
