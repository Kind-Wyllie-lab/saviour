# Post-Process page layout: jobs list, New job modal, simple/advanced compose

- **Status:** in progress (phases 1, 1b and the jobs-list layout built 2026-11-23)
- **Created:** 2026-11-23
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (Post-Process).

## Why

Post-Process stacks every tool fully open: the compose layout editor alone
fills the screen, then Ephys, then NWB, then every render. Most visits need
one tool. And the compose editor (canvas, tiles, inspector, z-order, canvas
size) is a lot for "give me both cameras side by side with the spectrogram".

## Design

**Jobs list + New job modal** (2026-11-23; replaced the first try, tool
cards, the same day). For the selected session the page is one **Jobs**
list, running first then newest first, each row tagged Video / NWB /
Ephys:

```
Session [test-085000Z ▾]                       12 files · 1.2 GB
Jobs   Videos, NWB files and ephys alignment…        [+ New job]
 VIDEO  done · 2 cameras · 1 spectrogram · 09:12   [Download] [Edit a copy] [Delete]
 NWB    test-085000Z.nwb · 262 MB · 08:58          [Download] [Export again]
 EPHYS  Ephys alignment · worst residual 0.2 ms    [Open]
▸ Videos from other sessions (3)
```

- Sources (`useSessionJobs`): compose renders (saved history), the NWB
  file on the share (+ this run's running / failed export), the ephys
  clock model and accelerometer results on the share (+ live align /
  accel jobs). `get_ephys_info` reports `model.fitted_at` and each
  accelerometer's `processed_at` (file times) so they sort with the rest.
- **New job** opens a modal: three tiles (Compose video, NWB export, Ephys
  alignment), each with a one-line description and whether it applies
  (no camera video; installs the NWB package first / replaces the file
  written …; recorded with ephys sync, aligned, or not recorded with ephys
  sync). Picking one shows that tool's form in the modal (wide for
  Compose), with "← Job types" to go back. Starting a render or an export
  closes the modal so the job shows in the list; Ephys (upload, align,
  accelerometer) stays open until closed.
- Row actions open the modal on the right tool: Edit a copy (Compose with
  the render loaded), Export again (NWB with the file's saved settings),
  Open (Ephys / a running NWB job), Add to a video (Compose, from an
  aligned accelerometer).
- Ephys sessions: the Ephys tile comes first with "Do this first", and the
  Jobs card shows "align the ephys recording first" until a model exists.

**Compose: Simple and Advanced.** Both edit the same layout document, so a
render is the same either way and Advanced can pick up where Simple left off.

- *Simple* (default): tick cameras, pick a layout (Auto / Side by side /
  Stacked / Grid), then per microphone click **Audio** (its sound muxed
  into the file) and/or **Spectrogram** (a panel under the video), whole
  recording or a part (the timeline, shown only for "a part"), preview,
  Render. No "spectrogram strip over the video" (dropped from the
  templates 2026-11-23; the backend still renders old saved jobs that
  use it). Every change rebuilds the
  layout through the existing `compose_layout_template` (it already takes a
  camera and mic subset); frame rate matches the cameras, format follows
  the content.
- *Advanced*: today's canvas editor, unchanged.
- Simple → Advanced keeps the layout. Advanced → Simple after hand edits
  asks first, since Simple rebuilds the layout from its choices (Undo in
  Advanced brings the custom one back).
- The time range survives a template rebuild in both modes.

**Ephys sessions: prepare before the outputs.** Ephys alignment fits the
clock model between ephys time and session time; cameras, audio and TTL are
already on session time (PTP), so it doesn't change their alignment, but
everything derived from the ephys data (the headstage accelerometer tiles
today, ephys traces later) needs it first.

- Workflow order: for a session recorded with External sync = Ephys
  (`external_sync == "ephys"`, or legacy `ephys` with no `external_sync`),
  Ephys comes first (now: the first New job tile, marked "Do this first").
  Otherwise it comes last, muted ("not recorded with ephys sync";
  uploading still allowed).
- Compose says what's missing for an ephys session with no aligned
  headstage (no upload / not aligned / accelerometer not processed) in a
  note with Open Ephys.
- Simple compose gains **Headstage** (trace under the video / orientation
  inset / both / don't show) once an aligned accelerometer exists, through
  `layout_template(headstage=...)`.
- NWB says it leaves the ephys recording and accelerometer out (phase 6).

**Per-session history.** The bottom card is this session's **Renders**
(every compose job for it, newest first; other sessions folded under
"Other sessions"). Each finished render has **Edit a copy**: its layout,
recording day, frame rate and format load into the Advanced editor with a
note saying it's a copy; rendering makes a new video and the original
stays. Renders from before the layout editor (no `layout_doc`) can be
re-run but not edited. The Compose card keeps only what's rendering, else
the latest result. NWB: each export's form settings are saved beside the
file (`<session>.nwb-settings.json`), so **Load its settings** fills the
form even after a restart; a finished export in this run has **Use these
settings**.

## Phases

1. **Simple / Advanced compose** (built 2026-11-23; first shipped inside
   tool cards). Accept: a Simple render of two cameras + a spectrogram
   panel matches an Advanced "Apply template" render of the same choices;
   switching modes keeps the layout.
1b. **Ephys-first order + headstage in Simple** (built 2026-11-23):
   `layout_template(headstage={source, show})` (trace strip grows the
   canvas, orientation inset top right; must be aligned), Simple's
   Headstage row, Compose / NWB notes. Accept: on a real headstage +
   camera session, Ephys leads, Compose's note walks through upload →
   align → process, and a Simple render with "Trace and orientation"
   matches the same tiles placed in Advanced.
1c. **Jobs list + New job modal** (built 2026-11-23). Accept: a session's
   list shows its videos, NWB file and ephys results in time order; New
   job → Compose → Render closes the modal and the render appears at the
   top; Edit a copy and Export again open the right tool with the saved
   settings.
2. **One job shape** across compose / NWB / ephys (state, progress,
   stage, cancel, result, session), with running jobs sent on connect so
   a reload doesn't lose ephys progress, and a header indicator for jobs
   running in other sessions. Lets USV / tracking plug in as job types.
3. **USV detection as a job type** (`tools/usv_detect.py`): run on a
   session's AudioMoth FLACs in the background, show the summary, timeline
   and gallery PNGs, download the calls CSV. Later: calls as a compose
   event track (compose phase 3).
4. **APA tracking as a job type** (`tools/apa_track.py` / `apa_track_report.py`):
   APA sessions only; time in zone, entries, latency, pulses in/out of zone,
   trajectory and timeline plots, `track.csv`.
5. **Sync / QC report card**: framesync verdict (now on the session page),
   A/V offset (`av_sync_check`), frame-count check, recorded gaps
   (`session_gaps.json`, which nothing reads yet). The provenance the
   aligned-ethogram work (`plans/multicam-frame-alignment-and-sync-provenance.md`)
   needs stated up front.

6. **Ephys data in NWB**: the headstage accelerometer (`_ephys/accel/<port>/
   accel.csv`, already on session time) as a `TimeSeries` with the clock
   model recorded; then the ephys recording itself, either linked (the
   Open Ephys folder by relative path, as video is) or converted through
   neuroconv's Open Ephys interface, with the clock model as the
   timestamp mapping. Decide link vs embed by size (a session's ephys can
   be tens of GB).

## Not in scope

The compose editor's own roadmap (event tracks, text, saved templates:
`plans/compose-layout-editor.md`); habitat campaign renders
(`plans/habitat-post-processing.md`).
