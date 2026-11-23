# Post-Process page layout: tool cards, simple/advanced compose

- **Status:** in progress (phases 1 and 1b built 2026-11-23)
- **Created:** 2026-11-23
- **Owner:** ascottg
- **CLAUDE.md ref:** "Open work → Reliability / UX" (Post-Process).

## Why

Post-Process stacks every tool fully open: the compose layout editor alone
fills the screen, then Ephys, then NWB, then every render. Most visits need
one tool. And the compose editor (canvas, tiles, inspector, z-order, canvas
size) is a lot for "give me both cameras side by side with the spectrogram".

## Design

**Tool cards.** Session picker on top; each tool is a collapsible card whose
header carries a one-line summary even when closed, so the page reads as a
status overview:

```
▸ Compose video   2 cameras · 1 mic · last render 09:12 done
▸ Ephys           no recording uploaded
▸ NWB export      written 08:58 · 262 MB
▸ All renders     3 · 1 running
```

- All closed by default; which are open is remembered (`usePersistedState`,
  i.e. per tab, like the page's other choices).
- Panels stay mounted while closed (their socket listeners keep the summary
  live); compose doesn't request previews or take keyboard shortcuts while
  closed.
- A tool that can't apply says why in its summary (no cameras, NWB package
  not installed) rather than showing an empty form.

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

- Cards follow the workflow: for a session recorded with External sync =
  Ephys (`external_sync == "ephys"`, or legacy `ephys` with no
  `external_sync`), Ephys comes first, then Compose and NWB. Otherwise
  Compose and NWB come first and Ephys last, dimmed ("not recorded with
  ephys sync"; uploading still allowed). An upload on a non-ephys session
  undims it.
- Compose says what's missing for an ephys session with no aligned
  headstage (no upload / not aligned / accelerometer not processed), in
  the card summary ("headstage not ready") and a note with Open Ephys.
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

1. **Cards + simple compose** (built 2026-11-23): `ToolCard`, summaries for
   Compose / Ephys / NWB / All renders, simple/advanced compose.
   Accept: page opens with all cards closed and summaries filled in; a
   Simple render of two cameras + strip matches an Advanced "Apply
   template" render of the same choices; switching modes keeps the layout.
1b. **Ephys-first order + headstage in Simple** (built 2026-11-23): card
   order, Compose / NWB notes, `layout_template(headstage={source, show})`
   (trace strip grows the canvas, orientation inset top right; must be
   aligned), Simple's Headstage row. Accept: on a real headstage + camera
   session, the Ephys card leads, Compose's note walks through upload →
   align → process, and a Simple render with "Trace and orientation"
   matches the same tiles placed in Advanced.
2. **One jobs indicator** for every background job type (compose, NWB,
   ephys align/accel, later USV/tracking) in the page header, replacing
   the All renders card. Needs a shared job shape or a small adapter per
   kind.
3. **USV detection as a page tool** (`tools/usv_detect.py`): run on a
   session's AudioMoth FLACs in the background, show the summary, timeline
   and gallery PNGs, download the calls CSV. Later: calls as a compose
   event track (compose phase 3).
4. **APA tracking as a page tool** (`tools/apa_track.py` / `apa_track_report.py`):
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
