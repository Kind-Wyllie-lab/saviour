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

PRESENCE MODE (--presence): a stripped-down variant for the one binary
question "is the rat in frame or not?", instead of activity windows. The
only labelling key is 't': it toggles at the current frame -- press it where
the rat first appears (opens an interval), press it again where the rat
leaves (closes it). Everything between marked intervals is "not in frame".
No i/o, no minor/major tags. A thick coloured border round the video
(green = in frame here, grey = not) tracks the derived state as you scrub.
Output goes to <video_stem>_presence.csv (same start_utc/end_utc columns,
severity = "present") so it can't clobber an activity _labels.csv, and it
feeds the occupancy-detector eval the same way _labels.csv feeds the
motion-detector eval. If you quit with an interval still open it's closed
at the frame you're on, with a loud warning -- reopen and mark the real
exit if the rat stayed in view past there.

Controls (a window pops up once the video loads -- click it for keyboard
focus, cv2's window doesn't grab it automatically):
  space       play / pause
  , / .       step back / forward 1 frame (while paused)
  [ / ]       step back / forward ~1s (25 frames; while paused)
  t           (--presence only) toggle "rat in frame" at the current frame
  i           mark IN point (start of an interesting segment) at current frame
  o           mark OUT point (end of segment) -- commits the pending segment
  1 / 2       tag the just-committed segment minor / major (optional, right
              after pressing 'o' -- e.g. minor = resident shifting in its
              sleep, major = an intruder entering/moving/leaving frame)
  u           undo the last committed segment (or, in --presence, the last
              't' toggle)
  s           save labels to the output CSV now (also auto-saved on quit)
  b           toggle BOX MODE -- auto-pauses. While on:
                - drag empty space to draw a NEW box around the rat/animal
                  (multiple boxes per frame supported, e.g. more than one
                  animal in view)
                - drag inside an existing box to MOVE it, or drag one of its
                  corner handles to RESIZE it -- an animal rarely jumps far
                  frame to frame, so once a box exists nearby it's usually
                  cheaper to nudge/resize than to redraw from scratch
                - landing on a not-yet-labeled frame auto-shows the nearest
                  already-labeled frame's box(es) as an editable, distinctly
                  colored DRAFT -- drag/resize it to confirm with the
                  adjustment, or press 'c' to accept it as-is (e.g. the
                  animal genuinely hasn't moved). A draft is NEVER written
                  to disk on its own -- stepping through frames without
                  touching the draft leaves them unlabeled, so casually
                  scrubbing through footage can't silently mislabel a frame.
              Every commit (new/moved/resized/confirmed) writes immediately:
              the raw (unannotated) frame to <video>_boxes/images/ and a
              YOLO-format label (class x_center y_center width height,
              normalized 0-1) to <video>_boxes/labels/ -- same flat
              images/+labels/ layout and single "rat" class (id 0) the
              apa_camera rat-detector training pipeline already expects (see
              src/modules/variants/apa_camera/training/README.md) -- this
              replaces that pipeline's external-labeler step (step 3), not
              the extract/train/convert steps around it. Re-running this
              tool against the same video+--boxes-dir reloads existing boxes
              so you can resume/add to a frame later.
  c           confirm the current frame's carried-over draft box(es)
              as-is, without dragging/resizing them first
  x           undo the last box on the CURRENT frame (once a frame's last
              box is removed, its image+label files are deleted entirely --
              this tool only ever produces positive examples, not
              hand-picked negatives)
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
        [--boxes-dir DIR] [--class-name rat] [--presence]

    Omit VIDEO.ts to pick one via a file-open dialog instead of typing a path.
"""

import argparse
import csv
import re
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
_SEVERITY_COLOR = {
    "major": (0, 0, 220), "minor": (0, 170, 220), "": (150, 150, 150),
    "present": (0, 200, 0),  # --presence intervals: rat in frame
}
_PRESENCE_BORDER_PX = 6
_PRESENCE_IN_COLOR = (0, 200, 0)
_PRESENCE_OUT_COLOR = (90, 90, 90)
_PLAYHEAD_COLOR = (255, 255, 255)
_BOX_COLOR = (0, 255, 0)  # committed bounding boxes
_BOX_DRAG_COLOR = (0, 255, 255)  # in-progress drag preview
_MIN_BOX_PX = 4  # ignore an accidental click/micro-drag as a committed box
_HANDLE_PX = 8  # screen-space hit radius for a box's resize-corner handles
_HANDLE_DRAW_R = 4  # drawn corner-handle marker radius, box mode only


def _load_frame_metadata(csv_path: Path) -> list[dict]:
    with open(csv_path, newline="") as f:
        return list(csv.DictReader(f))


def _fit_transform(w: int, h: int, target_w: int, target_h: int):
    """Scale + padding needed to fit (w,h) into (target_w,target_h) while
    preserving aspect ratio -- shared by _letterbox (forward: frame pixel ->
    screen pixel, for drawing) and screen_to_frame in main() (inverse:
    screen pixel -> frame pixel, for mapping a mouse click back to where it
    landed on the actual frame) so the two can never disagree with each
    other about the transform."""
    if target_w <= 0 or target_h <= 0 or w <= 0 or h <= 0:
        return 1.0, w, h, 0, 0
    scale = min(target_w / w, target_h / h)
    new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
    pad_w, pad_h = target_w - new_w, target_h - new_h
    return scale, new_w, new_h, pad_w // 2, pad_h // 2


def _letterbox(frame, target_w: int, target_h: int):
    """Scale frame to fit within target_w x target_h preserving aspect ratio,
    padding the remainder with black -- see the call site's comment on why
    cv2.imshow can't be trusted to do this itself for a resizable window."""
    h, w = frame.shape[:2]
    if target_w <= 0 or target_h <= 0 or w <= 0 or h <= 0:
        return frame
    _scale, new_w, new_h, left, top = _fit_transform(w, h, target_w, target_h)
    resized = cv2.resize(frame, (new_w, new_h))
    pad_w, pad_h = target_w - new_w, target_h - new_h
    bottom, right = pad_h - top, pad_w - left
    return cv2.copyMakeBorder(resized, top, bottom, left, right,
                               cv2.BORDER_CONSTANT, value=(0, 0, 0))


def _draw_box(img, box: tuple[int, int, int, int], color: tuple[int, int, int],
              handles: bool) -> None:
    """Draw one bounding box, optionally with small corner-handle markers
    hinting it can be grabbed there to resize (drawn only in box mode --
    they're not interactive otherwise)."""
    x1, y1, x2, y2 = box
    cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
    if handles:
        for cx, cy in ((x1, y1), (x2, y1), (x1, y2), (x2, y2)):
            cv2.circle(img, (cx, cy), _HANDLE_DRAW_R, color, -1)


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


def _ts_in_segments(ts_iso: str, segments: list[list[str]]) -> bool:
    """Whether an ISO-8601 UTC timestamp falls inside any [start, end]
    interval. All timestamps here share the _timestamps.csv format/zone, so
    plain string comparison is chronological -- no datetime parse needed."""
    return any(start <= ts_iso <= end for start, end, _sev in segments)


_YOLO_LINE_FIELDS = 5  # class x_center y_center width height


class BoxDataset:
    """YOLO-format images/+labels/ output for box-mode -- bundles the
    images_dir/labels_dir/stem triple every operation needs (write, delete,
    reload-on-resume) instead of threading all three through each call
    site. Frame naming matches extract_frames.py's own convention
    ({stem}_f{frame_idx:07d}) so box-mode output can be mixed into the same
    dataset as frames pulled by that tool."""

    def __init__(self, boxes_dir: Path, stem: str):
        self.images_dir = boxes_dir / "images"
        self.labels_dir = boxes_dir / "labels"
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.labels_dir.mkdir(parents=True, exist_ok=True)
        self.stem = stem

    def _paths(self, frame_idx: int) -> tuple[Path, Path]:
        name = f"{self.stem}_f{frame_idx:07d}"
        return self.images_dir / f"{name}.jpg", self.labels_dir / f"{name}.txt"

    def write(self, frame_idx: int, frame, boxes: list[tuple[int, int, int, int]],
              frame_w: int, frame_h: int) -> None:
        """Write (or overwrite) one frame's YOLO label file, and the raw
        (un-annotated) frame image the first time it's needed. boxes is
        assumed non-empty -- call delete() instead once a frame's last box
        is removed."""
        img_path, label_path = self._paths(frame_idx)
        if not img_path.exists():
            cv2.imwrite(str(img_path), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
        with open(label_path, "w") as f:
            for x1, y1, x2, y2 in boxes:
                xc, yc = (x1 + x2) / 2 / frame_w, (y1 + y2) / 2 / frame_h
                bw, bh = (x2 - x1) / frame_w, (y2 - y1) / frame_h
                f.write(f"0 {xc:.6f} {yc:.6f} {bw:.6f} {bh:.6f}\n")

    def delete(self, frame_idx: int) -> None:
        img_path, label_path = self._paths(frame_idx)
        img_path.unlink(missing_ok=True)
        label_path.unlink(missing_ok=True)

    def load_existing(self, frame_w: int, frame_h: int
                       ) -> dict[int, list[tuple[int, int, int, int]]]:
        """Reload a previous run's boxes so re-launching against the same
        video + --boxes-dir resumes rather than starts over. Assumes
        frame_w/frame_h (the current video's own dimensions) match what was
        used when these labels were written -- true for a fixed camera
        re-labeling its own footage, the only case this is meant to
        support."""
        boxes: dict[int, list[tuple[int, int, int, int]]] = {}
        pattern = re.compile(rf"^{re.escape(self.stem)}_f(\d+)\.txt$")
        for path in sorted(self.labels_dir.glob(f"{self.stem}_f*.txt")):
            m = pattern.match(path.name)
            if not m:
                continue
            frame_idx = int(m.group(1))
            frame_list = []
            for line in path.read_text().splitlines():
                parts = line.split()
                if len(parts) != _YOLO_LINE_FIELDS:
                    continue
                _, xc, yc, bw, bh = parts
                xc, yc, bw, bh = float(xc), float(yc), float(bw), float(bh)
                frame_list.append((
                    round((xc - bw / 2) * frame_w), round((yc - bh / 2) * frame_h),
                    round((xc + bw / 2) * frame_w), round((yc + bh / 2) * frame_h),
                ))
            if frame_list:
                boxes[frame_idx] = frame_list
        return boxes


def _move_box(orig: tuple[int, int, int, int], dx: int, dy: int,
              frame_w: int, frame_h: int) -> tuple[int, int, int, int]:
    """Translate a box by (dx, dy), clamped so it stays fully in-frame."""
    x1, y1, x2, y2 = orig
    w_, h_ = x2 - x1, y2 - y1
    nx1 = max(0, min(frame_w - w_, x1 + dx))
    ny1 = max(0, min(frame_h - h_, y1 + dy))
    return round(nx1), round(ny1), round(nx1 + w_), round(ny1 + h_)


def _resize_box(orig: tuple[int, int, int, int], corner: str, fx: int, fy: int,
                 frame_size: tuple[int, int]) -> tuple[int, int, int, int]:
    """Move one named corner ('tl'/'tr'/'bl'/'br') to (fx, fy); the opposite
    corner stays fixed. Re-sorts afterward so dragging a corner past the
    opposite one flips the box rather than producing an inverted rect, and
    clamps to a minimum size so it can never resize down to nothing."""
    frame_w, frame_h = frame_size
    x1, y1, x2, y2 = orig
    if "l" in corner:
        x1 = fx
    if "r" in corner:
        x2 = fx
    if "t" in corner:
        y1 = fy
    if "b" in corner:
        y2 = fy
    x1, x2 = sorted((x1, x2))
    y1, y2 = sorted((y1, y2))
    if x2 - x1 < _MIN_BOX_PX:
        x2 = min(frame_w - 1, x1 + _MIN_BOX_PX)
    if y2 - y1 < _MIN_BOX_PX:
        y2 = min(frame_h - 1, y1 + _MIN_BOX_PX)
    return max(0, x1), max(0, y1), x2, y2


def _nearest_labeled_frame(frame_boxes: dict[int, list], frame_idx: int) -> int | None:
    """The already-labeled frame closest to frame_idx (either direction) --
    used to carry a box forward (or backward) as an editable starting point
    on a not-yet-labeled frame, since an animal rarely jumps far between
    consecutive frames."""
    if not frame_boxes:
        return None
    return min(frame_boxes.keys(), key=lambda k: abs(k - frame_idx))


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
    ap.add_argument("--boxes-dir",
                     help="output dir for box-mode images/+labels/ "
                          "(default: <video_stem>_boxes next to the video)")
    ap.add_argument("--class-name", default="rat",
                     help="class name recorded in classes.txt (default: rat) "
                          "-- box labels are always written as single-class "
                          "id 0, matching the apa_camera training pipeline")
    ap.add_argument("--presence", action="store_true",
                     help="binary 'rat in frame / not' mode: single 't' key "
                          "toggles presence at the current frame; output goes "
                          "to <video_stem>_presence.csv (severity='present')")
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

    presence_mode = args.presence
    _default_labels_name = (
        f"{video_path.stem}_presence.csv" if presence_mode
        else f"{video_path.stem}_labels.csv"
    )
    labels_out = Path(args.labels_out) if args.labels_out else (
        video_path.parent / _default_labels_name
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
        _noun = "presence interval" if presence_mode else "segment"
        print(f"Loaded {len(segments)} existing {_noun}(s) from {labels_out} -- "
              f"continuing to add to them (press 'u' to remove one)")
    pending_start_idx: int | None = None
    playing = False

    window = (
        "label_activity  --  space play/pause, t toggle, u undo, s save, q quit"
        if presence_mode
        else "label_activity  --  space play/pause, i/o mark, u undo, s save, q quit"
    )
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)

    def on_trackbar(pos: int) -> None:
        nonlocal frame_idx
        frame_idx = pos

    cv2.createTrackbar("frame", window, frame_idx, max(0, n_frames - 1), on_trackbar)

    # Click-to-seek on the colored timeline strip at the bottom of the
    # window, and (in box mode) drag-to-draw a bounding box over the video --
    # layout is updated every frame below (it depends on the current window
    # size, which can change), so both callbacks always read up-to-date
    # geometry rather than a stale value captured at setup.
    layout = {
        "width": 1, "timeline_top": 0,
        "video_x0": _PANEL_W, "scale": 1.0, "pad_left": 0, "pad_top": 0,
        "new_w": 1, "new_h": 1, "frame_w": 1, "frame_h": 1,
    }
    box_mode = False
    # "mode" is None (idle), "new" (dragging out a fresh box), "move", or
    # "resize" -- move/resize act on box_idx within whichever list (a
    # frame's own committed boxes, or a carried-over draft) owned it when
    # the drag started; "orig_box"/"anchor" are the pre-drag box and the
    # frame-space point the drag started at, used to compute live deltas.
    drag_state = {
        "mode": None, "box_idx": None, "corner": None,
        "anchor": None, "orig_box": None,
        "start": None, "current": None,
    }
    # Not-yet-saved boxes carried forward from the nearest already-labeled
    # frame, shown so the common case (an animal barely moved between
    # frames) is "nudge, don't redraw" -- but never written to disk until
    # explicitly touched (dragged/resized/confirmed), so just stepping
    # through frames without interacting never silently mislabels one.
    draft_boxes: list[tuple[int, int, int, int]] | None = None

    def refresh_draft() -> None:
        """Recompute the current frame's draft from the nearest labeled
        frame -- call whenever frame_idx, box_mode, or frame_boxes[frame_idx]
        changes. A frame with its own committed boxes never gets a draft."""
        nonlocal draft_boxes
        if box_mode and frame_idx not in frame_boxes:
            src = _nearest_labeled_frame(frame_boxes, frame_idx)
            draft_boxes = (
                [tuple(b) for b in frame_boxes[src]] if src is not None else None
            )
        else:
            draft_boxes = None

    def screen_to_frame(x: int, y: int) -> tuple[int | None, int | None]:
        """Invert _fit_transform to map a window-coordinate mouse event back
        onto a pixel position in the actual (un-letterboxed) video frame.
        Returns (None, None) if the point falls outside the video region
        (side panel, or the letterbox padding) entirely."""
        vx, vy = x - layout["video_x0"], y
        pad_left, pad_top = layout["pad_left"], layout["pad_top"]
        new_w, new_h, scale = layout["new_w"], layout["new_h"], layout["scale"]
        if scale <= 0 or not (pad_left <= vx < pad_left + new_w
                               and pad_top <= vy < pad_top + new_h):
            return None, None
        fx = round((vx - pad_left) / scale)
        fy = round((vy - pad_top) / scale)
        return (max(0, min(layout["frame_w"] - 1, fx)),
                max(0, min(layout["frame_h"] - 1, fy)))

    def _hit_test(fx: int, fy: int, boxes: list[tuple[int, int, int, int]]):
        """Which box (if any) fx,fy landed on when a drag starts, and
        whether it's a corner-resize handle or a body-move grab -- checked
        newest-drawn-first so an overlapping box drawn on top wins. Falls
        back to ("new", None, None): starting a fresh box on empty space."""
        handle_r = _HANDLE_PX / max(layout["scale"], 1e-6)
        for i in reversed(range(len(boxes))):
            x1, y1, x2, y2 = boxes[i]
            corners = {"tl": (x1, y1), "tr": (x2, y1), "bl": (x1, y2), "br": (x2, y2)}
            for corner, (cx, cy) in corners.items():
                if abs(fx - cx) <= handle_r and abs(fy - cy) <= handle_r:
                    return "resize", i, corner
            if x1 <= fx <= x2 and y1 <= fy <= y2:
                return "move", i, None
        return "new", None, None

    def handle_box_down(x: int, y: int) -> None:
        """LBUTTONDOWN in box mode: hit-test against whatever's currently
        shown on this frame (its own committed boxes, else the draft) to
        decide whether this drag will move/resize an existing box or draw a
        new one."""
        nonlocal draft_boxes
        fx, fy = screen_to_frame(x, y)
        if fx is None:
            return
        active = (frame_boxes[frame_idx] if frame_idx in frame_boxes
                  else (draft_boxes or []))
        mode, idx, corner = _hit_test(fx, fy, active)
        drag_state.update(mode=mode, box_idx=idx, corner=corner,
                           anchor=(fx, fy), start=(fx, fy), current=(fx, fy))
        if mode in ("move", "resize"):
            # First touch on a frame that only had a draft promotes it to a
            # real, saved box -- from here it's edited in place.
            if frame_idx not in frame_boxes:
                frame_boxes[frame_idx] = draft_boxes
                draft_boxes = None
            drag_state["orig_box"] = frame_boxes[frame_idx][idx]

    def handle_box_move(x: int, y: int) -> None:
        fx, fy = screen_to_frame(x, y)
        if fx is None:
            return
        drag_state["current"] = (fx, fy)
        if drag_state["mode"] == "new":
            return  # preview only, resolved at mouse-up
        ax, ay = drag_state["anchor"]
        frame_size = (layout["frame_w"], layout["frame_h"])
        box_list = frame_boxes[frame_idx]
        if drag_state["mode"] == "move":
            box_list[drag_state["box_idx"]] = _move_box(
                drag_state["orig_box"], fx - ax, fy - ay, *frame_size)
        else:  # resize -- drag one corner, the opposite corner stays put
            box_list[drag_state["box_idx"]] = _resize_box(
                drag_state["orig_box"], drag_state["corner"], fx, fy, frame_size)

    def handle_box_up(x: int, y: int) -> None:
        nonlocal draft_boxes
        fx, fy = screen_to_frame(x, y)
        if fx is not None:
            drag_state["current"] = (fx, fy)
        mode = drag_state["mode"]
        if mode != "new":
            if frame is not None:
                box_dataset.write(frame_idx, frame, frame_boxes[frame_idx],
                                   layout["frame_w"], layout["frame_h"])
                print(f"Box {drag_state['box_idx']} {mode}d on frame {frame_idx}")
        else:
            x1, x2 = sorted((drag_state["start"][0], drag_state["current"][0]))
            y1, y2 = sorted((drag_state["start"][1], drag_state["current"][1]))
            if x2 - x1 >= _MIN_BOX_PX and y2 - y1 >= _MIN_BOX_PX and frame is not None:
                # A new box always confirms whatever draft was showing too
                # (drawing a 2nd animal on a carried-over frame shouldn't
                # silently drop the first).
                if frame_idx not in frame_boxes and draft_boxes is not None:
                    frame_boxes[frame_idx] = draft_boxes
                    draft_boxes = None
                frame_boxes.setdefault(frame_idx, []).append((x1, y1, x2, y2))
                box_dataset.write(frame_idx, frame, frame_boxes[frame_idx],
                                   layout["frame_w"], layout["frame_h"])
                print(f"Box added on frame {frame_idx} "
                      f"({len(frame_boxes[frame_idx])} on this frame, "
                      f"{sum(len(v) for v in frame_boxes.values())} total)")
        drag_state.update(mode=None, box_idx=None, corner=None,
                           anchor=None, orig_box=None, start=None, current=None)

    def on_mouse(event, x, y, _flags, _param) -> None:
        nonlocal frame_idx
        if event == cv2.EVENT_LBUTTONDOWN and y >= layout["timeline_top"]:
            frac = max(0.0, min(1.0, x / max(1, layout["width"])))
            frame_idx = min(n_frames - 1, max(0, round(frac * (n_frames - 1))))
            return
        if not box_mode or playing:
            return
        if event == cv2.EVENT_LBUTTONDOWN:
            handle_box_down(x, y)
        elif event == cv2.EVENT_MOUSEMOVE and drag_state["mode"] is not None:
            handle_box_move(x, y)
        elif event == cv2.EVENT_LBUTTONUP and drag_state["mode"] is not None:
            handle_box_up(x, y)

    cv2.setMouseCallback(window, on_mouse)

    # Size the window to the video's own aspect ratio (plus the side panel)
    # up front -- otherwise the first frame renders into whatever default
    # rect the OS gave the new window, which is almost never the right shape.
    probe = get_frame(frame_idx)
    pw = ph = 0
    if probe is not None:
        ph, pw = probe.shape[:2]
        video_init_w = min(_DISPLAY_MAX_WIDTH, pw)
        init_h = max(1, round(video_init_w * ph / pw))
        cv2.resizeWindow(window, video_init_w + _PANEL_W, init_h)

    boxes_dir = Path(args.boxes_dir) if args.boxes_dir else (
        video_path.parent / f"{video_path.stem}_boxes"
    )
    box_dataset = BoxDataset(boxes_dir, video_path.stem)
    classes_path = boxes_dir / "classes.txt"
    if not classes_path.exists():
        classes_path.write_text(args.class_name + "\n")

    frame_boxes: dict[int, list[tuple[int, int, int, int]]] = (
        box_dataset.load_existing(pw, ph) if probe is not None else {}
    )
    if frame_boxes:
        print(f"Loaded {len(frame_boxes)} boxed frame(s), "
              f"{sum(len(v) for v in frame_boxes.values())} box(es) from {boxes_dir} "
              f"-- continuing to add to them (press 'x' to remove one)")

    print(f"Loaded {n_frames} frames from {video_path.name}")
    print(f"Labels will be saved to {labels_out}")
    print(f"Box-mode output ('b' to toggle) will be saved to {boxes_dir}")

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

        if frame_idx != last_idx:
            drag_state.update(mode=None, box_idx=None, corner=None,
                               anchor=None, orig_box=None, start=None, current=None)
            refresh_draft()

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
        h0, w0 = frame.shape[:2]
        scale, new_w, new_h, pad_left, pad_top = _fit_transform(w0, h0, video_w, win_h)
        layout["video_x0"] = _PANEL_W
        layout["scale"], layout["new_w"], layout["new_h"] = scale, new_w, new_h
        layout["pad_left"], layout["pad_top"] = pad_left, pad_top
        layout["frame_w"], layout["frame_h"] = w0, h0

        # Only pay for a frame copy + draw when there's actually something to
        # show -- the common case (box mode off, no boxes yet on this frame)
        # stays as cheap as before this feature existed. A box being
        # moved/resized lives directly in frame_boxes[frame_idx] (mutated
        # live by on_mouse), so it's already covered by the committed-boxes
        # loop below -- only a brand-new not-yet-committed drag and the
        # carried-over draft need their own drawing.
        has_committed = frame_idx in frame_boxes
        show_draft = box_mode and not has_committed and draft_boxes is not None
        dragging_new = (box_mode and drag_state["mode"] == "new"
                         and drag_state["start"] is not None)
        frame_for_display = frame
        if has_committed or show_draft or dragging_new:
            frame_for_display = frame.copy()
            for box in frame_boxes.get(frame_idx, []):
                _draw_box(frame_for_display, box, _BOX_COLOR, box_mode)
            if show_draft:
                for box in draft_boxes:
                    _draw_box(frame_for_display, box, _BOX_DRAG_COLOR, True)
            if dragging_new and drag_state["current"] is not None:
                cv2.rectangle(frame_for_display, drag_state["start"],
                               drag_state["current"], _BOX_DRAG_COLOR, 1)
        video_disp = _letterbox(frame_for_display, video_w, win_h)

        ts = rows[frame_idx]["timestamp_utc"] if frame_idx < len(rows) else "?"

        # --presence: thick border tracking the derived state at this frame
        # (an open interval, or the current frame falling inside a committed
        # one), so it's obvious while scrubbing whether the rat is "in".
        rat_in_frame = presence_mode and (
            pending_start_idx is not None or _ts_in_segments(ts, segments)
        )
        if presence_mode:
            bcol = _PRESENCE_IN_COLOR if rat_in_frame else _PRESENCE_OUT_COLOR
            cv2.rectangle(
                video_disp, (0, 0),
                (video_disp.shape[1] - 1, video_disp.shape[0] - 1),
                bcol, _PRESENCE_BORDER_PX,
            )

        pending_note = (
            f"  IN marked @ {rows[pending_start_idx]['timestamp_utc']}"
            if pending_start_idx is not None else ""
        )
        status_lines = [
            f"frame {frame_idx}/{n_frames - 1}",
            ts,
            ("PLAYING" if playing else "paused") + pending_note,
        ]
        if presence_mode:
            status_lines.append(
                ">>> RAT IN FRAME <<<" if rat_in_frame else "... rat out of frame ..."
            )
        if box_mode:
            if draft_boxes is not None and frame_idx not in frame_boxes:
                status_lines.append(
                    f"DRAFT ({len(draft_boxes)} box(es), unsaved) -- "
                    f"drag/resize to adjust, or 'c' to confirm as-is"
                )
            else:
                status_lines.append(
                    f"BOX MODE -- drag empty space for new, "
                    f"corner=resize body=move  "
                    f"({len(frame_boxes.get(frame_idx, []))} box(es) here)"
                )
        if presence_mode:
            legend_lines = [
                "PRESENCE MODE",
                "space:play/pause",
                ",/.: step 1 frame",
                "[/]: step ~1s",
                "t: toggle rat in/out here",
                "u: undo   s: save",
                "b: box mode   x: undo box",
                "q/Esc: save+quit",
            ]
        else:
            legend_lines = [
                "space:play/pause",
                ",/.: step 1 frame",
                "[/]: step ~1s",
                "i: mark IN",
                "o: mark OUT",
                "1/2: tag last minor/major",
                "u: undo   s: save",
                "b: box mode   x: undo box",
                "c: confirm draft box(es)",
                "q/Esc: save+quit",
            ]
        shown = segments[-_MAX_SHOWN:]
        hidden_count = len(segments) - len(shown)
        _noun = "presence interval" if presence_mode else "segment"
        segment_lines = [f"{len(segments)} {_noun}(s):"]
        if hidden_count > 0:
            segment_lines.append(f"  ... ({hidden_count} earlier)")
        for i, (start, end, severity) in enumerate(shown, len(segments) - len(shown) + 1):
            tag = f" [{severity}]" if severity else ""
            segment_lines.append(f"  {i}) {_short_ts(start)}-{_short_ts(end)}{tag}")

        box_lines = [
            f"{len(frame_boxes)} frame(s) boxed, "
            f"{sum(len(v) for v in frame_boxes.values())} box(es) total",
        ]

        panel_lines = (status_lines + [""] + legend_lines + [""] + segment_lines
                       + [""] + box_lines)
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

        # waitKey(0) blocks until a KEYPRESS specifically -- mouse events
        # still update drag_state via the on_mouse callback in the
        # background, but nothing re-renders to show it until the next
        # loop iteration. Box mode needs to keep polling (like playback
        # already does) so a drag's preview rectangle actually redraws as
        # the mouse moves, not just once released.
        key = cv2.waitKey(30 if (playing or box_mode) else 0) & 0xFF

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
        elif presence_mode and key == ord('t'):
            if pending_start_idx is not None:
                # close the open "in frame" interval at this frame
                start_ts = rows[pending_start_idx]["timestamp_utc"]
                end_ts = ts
                if end_ts < start_ts:
                    start_ts, end_ts = end_ts, start_ts
                segments.append([start_ts, end_ts, "present"])
                segments.sort()
                pending_start_idx = None
                print(f"Rat OUT at frame {frame_idx} ({ts}) -- "
                      f"interval {start_ts} -> {end_ts}")
            elif _ts_in_segments(ts, segments):
                print("This frame is already inside a labeled 'in frame' "
                      "interval -- press 'u' to drop it, or move outside it "
                      "before toggling.")
            else:
                pending_start_idx = frame_idx
                print(f"Rat IN at frame {frame_idx} ({ts}) -- "
                      f"press 't' again where it leaves")
        elif key == ord('1'):
            if segments:
                segments[-1][2] = "minor"
                print(f"Tagged last segment: minor  {segments[-1]}")
        elif key == ord('2'):
            if segments:
                segments[-1][2] = "major"
                print(f"Tagged last segment: major  {segments[-1]}")
        elif key == ord('u'):
            if presence_mode and pending_start_idx is not None:
                print(f"Undid open 'in frame' mark at frame {pending_start_idx}")
                pending_start_idx = None
            elif segments:
                print(f"Undid {'interval' if presence_mode else 'segment'} "
                      f"{segments.pop()}")
        elif key == ord('s'):
            _write_labels(labels_out, segments)
            print(f"Saved {len(segments)} "
                  f"{'interval' if presence_mode else 'segment'}(s) to {labels_out}")
            if presence_mode and pending_start_idx is not None:
                print("  (note: 1 'in frame' interval is still open and was "
                      "NOT saved -- press 't' where the rat leaves)")
        elif key == ord('b'):
            box_mode = not box_mode
            playing = False
            drag_state.update(mode=None, box_idx=None, corner=None,
                               anchor=None, orig_box=None, start=None, current=None)
            refresh_draft()
            print(f"Box mode {'ON -- drag to draw a box' if box_mode else 'OFF'}")
        elif key == ord('c'):
            if draft_boxes:
                frame_boxes[frame_idx] = [tuple(b) for b in draft_boxes]
                draft_boxes = None
                box_dataset.write(frame_idx, frame, frame_boxes[frame_idx],
                                   layout["frame_w"], layout["frame_h"])
                print(f"Confirmed {len(frame_boxes[frame_idx])} box(es) on frame "
                      f"{frame_idx} (carried over unchanged)")
            elif frame_idx in frame_boxes:
                print("This frame is already confirmed")
            else:
                print("No draft to confirm -- no nearby labeled frame yet")
        elif key == ord('x'):
            if frame_boxes.get(frame_idx):
                removed = frame_boxes[frame_idx].pop()
                if frame_boxes[frame_idx]:
                    box_dataset.write(frame_idx, frame, frame_boxes[frame_idx],
                                       layout["frame_w"], layout["frame_h"])
                    print(f"Removed box {removed} from frame {frame_idx} "
                          f"({len(frame_boxes[frame_idx])} remain)")
                else:
                    del frame_boxes[frame_idx]
                    box_dataset.delete(frame_idx)
                    print(f"Removed last box from frame {frame_idx} -- "
                          f"deleted its image+label (no boxes left)")
                refresh_draft()
            else:
                print("No boxes on this frame to undo")
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

    if presence_mode and pending_start_idx is not None:
        start_ts = rows[pending_start_idx]["timestamp_utc"]
        end_ts = rows[min(frame_idx, len(rows) - 1)]["timestamp_utc"]
        if end_ts < start_ts:
            start_ts, end_ts = end_ts, start_ts
        segments.append([start_ts, end_ts, "present"])
        segments.sort()
        print("!! WARNING: an 'in frame' interval was still open on quit -- "
              f"closed it at the current frame ({end_ts}). If the rat stayed "
              "in view past there, rerun and mark the real exit with 't'.")

    _write_labels(labels_out, segments)
    print(f"Saved {len(segments)} "
          f"{'interval' if presence_mode else 'segment'}(s) to {labels_out}")
    print(f"Boxes: {len(frame_boxes)} frame(s), "
          f"{sum(len(v) for v in frame_boxes.values())} box(es) total in {boxes_dir}")


if __name__ == "__main__":
    main()
