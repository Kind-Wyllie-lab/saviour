# tools/ephys — vendored from saviour-ephys-analysis

`tool1_fit_alignment.py`, `tool2_convert_timestamps.py`,
`tool3_export_video.py` and `SAVIOUR-Ephys-Pipeline_Demo.py` are copied
verbatim from
[`Kind-Wyllie-lab/saviour-ephys-analysis`](https://github.com/Kind-Wyllie-lab/saviour-ephys-analysis)
(the repo's own test-data / demo-output directories are deliberately not
vendored — only the code). Re-sync by copying the `src/*.py` + top-level
docs over again; there is no git-subtree link.

## Headless use

The three `toolN_*.py` files are tkinter GUIs and can't run on a
controller. `align_cli.py` is a thin CLI wrapper around
`tool1_fit_alignment`'s discovery / loader / fit functions:

```
python tools/ephys/align_cli.py --oe <open-ephys-dir> --session <session-date-dir>
```

The controller's Post-Process page ("Ephys alignment" panel) shells out
to this after an operator uploads the Open Ephys recording.

## Dependencies

`requirements.txt` (numpy, pandas, matplotlib, scipy, opencv-python).
**pandas is not in the controller's main venv** — the align job runs
under `env2` if present, else it fails with a clear `ModuleNotFoundError`
and the operator installs pandas.

## Status

`align_cli.py` is correct-by-construction from the vendored functions but
has **not been validated running on a controller** against a real paired
Open Ephys + SAVIOUR session. Treat a fitted `model.json` as needing a
manual sanity check (R² ~1, residual max < ~2 ms, IPI correlation > 0.999).
