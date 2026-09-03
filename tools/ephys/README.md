# SAVIOUR ↔ OpenEphys Analysis Tools

Tools for aligning SAVIOUR behavioural/video recordings with OpenEphys
neural recordings using a shared pseudorandom TTL sync signal.

---

## Tools

| File | Purpose |
|---|---|
| `tool1_fit_alignment.py` | Select data folders, check PTP sync quality, fit and save the clock alignment model |
| `tool2_convert_timestamps.py` | Load a saved model, convert timestamps between SAVIOUR and OpenEphys time in either direction |
| `tool3_export_video.py` | Export a synchronised .mp4 combining SAVIOUR camera video with LFP traces for selected channels |
| `saviour_oe_pipeline.py` | Full analysis pipeline for use in Spyder — loads data, aligns clocks, plots TTL signals, animated viewer |

---

## Setup

### Option A — conda (recommended)

```
conda env create -f environment.yml
conda activate saviour-ephys
```

### Option B — pip into an existing environment

```
pip install -r requirements.txt
```

Python 3.11 is recommended. The tools have been tested on 3.11.

---

## Running the tools

### From Anaconda Prompt

```
conda activate saviour-ephys
python tool1_fit_alignment.py
python tool2_convert_timestamps.py
```

### Double-click (Windows)

Create a `run_tool1.bat` file in the same folder:

```batch
@echo off
call "C:\Users\<your username>\AppData\Local\anaconda3\Scripts\activate.bat" saviour-ephys
python "%~dp0tool1_fit_alignment.py"
```

Replace `<your username>` with your Windows username. Do the same for tool2.

---

## Typical workflow

1. Run **tool1** after each recording session
   - Point it at your OpenEphys recording folder and SAVIOUR session folder
   - It auto-discovers the TTL files, checks PTP sync quality, fits the model
   - Saves `model.json` next to the script

2. Run **tool2** any time you need to convert a timestamp
   - Loads `model.json` automatically if it is in the same folder
   - Enter a SAVIOUR Unix nanosecond timestamp to get OE seconds + sample number
   - Or enter an OE timestamp to get the SAVIOUR Unix time
   - Batch convert and export to CSV

---

## Folder structure expected

```
saviour-ephys-analysis/
├── tool1_fit_alignment.py
├── tool2_convert_timestamps.py
├── saviour_oe_pipeline.py
├── environment.yml
├── requirements.txt
├── README.md
├── ephys_data/          ← OpenEphys recording folder (or point tool1 at it)
│   └── 2026-05-22_.../
│       └── Record Node .../
│           └── experiment.../
│               └── recording1/
│                   ├── continuous/
│                   ├── events/
│                   └── structure.oebin
└── saviour_data/        ← SAVIOUR session folder (or point tool1 at it)
    └── ephys_sync-XXXXXX/
        └── YYYYMMDD/
            ├── ttl_XXXX/
            │   ├── ephys_sync-..._ttl_...csv
            │   └── ephys_sync-..._health_...csv
            └── Top Camera/
                ├── ephys_sync-..._Top Camera_....ts
                ├── ephys_sync-..._Top Camera_..._timestamps.csv
                └── ephys_sync-..._Top Camera_..._health_...csv
```

Tool1 will walk the directory tree automatically — you only need to point
it at the top level of each folder, not the specific TTL subfolders.

---

## Dependencies

| Package | Used for |
|---|---|
| numpy | All numerical computation, TTL alignment regression |
| pandas | Loading CSV files (SAVIOUR TTL, health metadata, video timestamps) |
| matplotlib | Residual plots in tool1 |
| scipy | Not required — regression uses pure numpy |
| opencv-python | Video playback in the animated viewer (pipeline script only) |
| tkinter | GUI for tool1 and tool2 (built into Python, no install needed) |