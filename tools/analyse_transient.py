"""Transient (clap) alignment for a SAVIOUR session.

Audio onsets (sharp, sub-ms, unambiguous in a quiet room) are the anchor.
For each onset, take the nearest-in-time camera frame and the local
frame-diff motion max within +/-4 frames -> that camera's clap instant.
All times are PTP wall-clock: cameras via *_timestamps.csv, audio via the
AudioMoth sidecar (STARTED anchor + block-timestamp linear fit as xcheck).
"""
import csv
import re
import sys

import cv2
import numpy as np
import soundfile as sf

FPS = 30.0
BLOCK = 32768


def load_csv_ts(path):
    with open(path) as f:
        return np.array([int(r["timestamp_ns"]) for r in csv.DictReader(f)],
                        dtype=np.int64)


def motion_curve(ts_path, width=200):
    cap = cv2.VideoCapture(ts_path)
    prev = None
    m = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(
            cv2.resize(fr, (width, int(width * fr.shape[0] / fr.shape[1]))),
            cv2.COLOR_BGR2GRAY).astype(np.float32)
        m.append(0.0 if prev is None else float(np.mean(np.abs(g - prev))))
        prev = g
    cap.release()
    return np.array(m)


def parse_sidecar(path):
    started = None
    blocks = []
    with open(path) as f:
        for ln in (x.strip() for x in f if x.strip()):
            if ln.startswith("STARTED "):
                started = float(ln.split()[1])
            elif re.fullmatch(r"\d+\.\d+", ln):
                blocks.append(float(ln))
    return started, np.array(blocks)


def audio_onsets(flac, n_expected=8):
    x, sr = sf.read(flac, dtype="float32")
    if x.ndim > 1:
        x = x[:, 0]
    win = sr // 1000                      # 1 ms
    env = np.sqrt(np.convolve(x * x, np.ones(win) / win, mode="same"))
    e = env[::win]                        # ~1 kHz envelope
    # onset strength = positive rise
    d = np.clip(np.diff(e, prepend=e[0]), 0, None)
    med = np.median(d)
    mad = np.median(np.abs(d - med)) + 1e-12
    thr = med + 8 * mad
    cand = [i for i in range(1, len(d) - 1)
            if d[i] > thr and d[i] >= d[i - 1] and d[i] >= d[i + 1]]
    # de-dup within 300 ms, keep strongest
    picks = []
    for i in cand:
        if picks and i - picks[-1] < 300:
            if d[i] > d[picks[-1]]:
                picks[-1] = i
        else:
            picks.append(i)
    picks.sort(key=lambda i: -d[i])
    picks = sorted(picks[:max(n_expected, 12)])
    return [i / 1000.0 for i in picks], sr, len(x)      # seconds from sample 0


def clap_frame(mot, cam_wall_ns, t_target_s, span=4):
    j = int(np.argmin(np.abs(cam_wall_ns / 1e9 - t_target_s)))
    lo, hi = max(1, j - span), min(len(mot) - 1, j + span + 1)
    k = lo + int(np.argmax(mot[lo:hi]))
    return k, cam_wall_ns[k] / 1e9


def main(main_ts, main_csv, ai_ts, ai_csv, flac, sidecar):
    m_wall = load_csv_ts(main_csv)
    a_wall = load_csv_ts(ai_csv)
    m_mot = motion_curve(main_ts)
    a_mot = motion_curve(ai_ts)
    print(f"main {len(m_wall)} csv / {len(m_mot)} frames   "
          f"ai {len(a_wall)} csv / {len(a_mot)} frames")

    started, blocks = parse_sidecar(sidecar)
    onsets, sr, nsamp = audio_onsets(flac)
    t_started = [started + s for s in onsets]
    if len(blocks) >= 3:
        bx = np.arange(len(blocks)) * float(BLOCK)
        slope, icpt = np.polyfit(bx, blocks, 1)
        t_fit = [icpt + slope * (s * sr) for s in onsets]
        eff_sr = 1.0 / slope
    else:
        t_fit, eff_sr = t_started, sr
    print(f"audio sr={sr} eff_sr~{eff_sr:.1f}  STARTED={started:.6f}")
    print(f"{len(onsets)} onsets at s= " + ", ".join(f"{s:.3f}" for s in onsets))

    print("\nper clap (ms; +ve => that stream LATER than the reference):")
    hdr = (f"{'#':>2} {'audio_s':>8} | {'ai-main':>8} | {'main-aud':>9} "
           f"{'ai-aud':>8} | {'main-aud_fit':>11} {'ai-aud_fit':>10} | "
           f"{'mF':>4} {'aF':>4}")
    print(hdr)
    rows = []
    for i, (ts_s, tf_s) in enumerate(zip(t_started, t_fit, strict=False)):
        mk, mt = clap_frame(m_mot, m_wall, ts_s)
        ak, at = clap_frame(a_mot, a_wall, ts_s)
        r = dict(
            aud=onsets[i],
            ai_main=(at - mt) * 1e3,
            main_aud=(mt - ts_s) * 1e3, ai_aud=(at - ts_s) * 1e3,
            main_audf=(mt - tf_s) * 1e3, ai_audf=(at - tf_s) * 1e3,
            mF=mk, aF=ak)
        rows.append(r)
        print(f"{i:>2} {onsets[i]:8.3f} | {r['ai_main']:8.1f} | "
              f"{r['main_aud']:9.1f} {r['ai_aud']:8.1f} | "
              f"{r['main_audf']:11.1f} {r['ai_audf']:10.1f} | "
              f"{mk:>4} {ak:>4}")

    # keep only claps where BOTH cameras' motion max landed within ~1 frame
    # of the audio onset -- otherwise the frame-diff picked a wrong frame.
    clean = [r for r in rows
             if abs(r["main_aud"]) < 45 and abs(r["ai_aud"]) < 45]

    def summ(rr, key):
        v = np.array([r[key] for r in rr])
        return (f"{v.mean():8.1f} +/- {v.std():5.1f} ms   "
                f"({v.mean() / 1000 * FPS:+.2f} fr)   n={len(v)}")

    print(f"\nALL n={len(rows)}:")
    for k, lbl in [("ai_main", "ai_cam - main_cam        "),
                   ("main_aud", "main_cam - audio(STARTED) "),
                   ("ai_aud", "ai_cam  - audio(STARTED)  ")]:
        print(f"  {lbl} {summ(rows, k)}")
    print(f"\nCLEAN (both cams within ~1 frame of audio) n={len(clean)}:")
    for k, lbl in [("ai_main", "ai_cam - main_cam         "),
                   ("main_aud", "main_cam - audio(STARTED)  "),
                   ("ai_aud", "ai_cam  - audio(STARTED)   "),
                   ("main_audf", "main_cam - audio(blkfit)   "),
                   ("ai_audf", "ai_cam  - audio(blkfit)    ")]:
        print(f"  {lbl} {summ(clean, k)}")
    d0 = abs(int(m_wall[0]) - int(a_wall[0])) / 1e3
    print(f"\n  frame-0 wall gap main vs ai: {d0:.1f} us  (framesync quality)")


USAGE = """\
usage: analyse_transient.py MAIN.ts MAIN_timestamps.csv AI.ts AI_timestamps.csv \\
                            AUDIO.flac AUDIO_timestamps.txt

Two-camera + audio transient (clap) alignment for a SAVIOUR session. First
used to validate the hailo inference worker thread
(docs/hailo-inference-sweep-2026-09-08.md). Needs cv2 + numpy + soundfile.
'main' is the sync server, 'ai' the sync client; offsets are ms, +ve = that
stream later than the reference.
"""

if __name__ == "__main__":
    if len(sys.argv) != 7:
        sys.exit(USAGE)
    main(*sys.argv[1:7])
