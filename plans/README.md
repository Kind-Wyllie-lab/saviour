# plans/

One markdown file per prospective feature or non-trivial bug fix — the detail
that would bloat `CLAUDE.md` if it lived inline.

## When to add a file here

- A change big enough that "the plan" is worth writing down before touching code
  (design decisions, phasing, measurement/acceptance criteria, rollback).
- Something `CLAUDE.md`'s "Open work" list mentions in one line but that needs a
  page to actually execute.

Small, obvious fixes stay as a one-liner in `CLAUDE.md` — don't make a file for
everything.

## How it relates to the other docs

| Location | Holds |
|----------|-------|
| `CLAUDE.md` "Open work" | The one-line index of everything outstanding. Links here. |
| `plans/` | The *intended* work, in detail, before it's done. |
| `docs/*_DESIGN.md` | Longer-lived design references (protocol, telemetry, NWB…). Overlaps `plans/`; prefer `plans/` for "we're about to build this", `docs/` for "this is how the built thing is shaped". |
| `docs/CHANGELOG.md` | Completed work, archived with write-ups. |

## Lifecycle

1. Write `plans/<slug>.md`. Add a one-line pointer under the relevant
   `CLAUDE.md` "Open work" heading: `` - **<title>** — see `plans/<slug>.md`. ``
2. Update the plan as decisions land (it's a living doc, not a spec frozen at
   day one).
3. When the work ships: move the substance into `docs/CHANGELOG.md`, replace the
   `CLAUDE.md` line with the usual completed-item note, and either delete the
   plan file or leave a stub pointing at the changelog entry.

## Frontmatter

Start each file with:

```markdown
# <Title>

- **Status:** proposed | in progress | blocked | shipped
- **Created:** YYYY-MM-DD
- **Owner:** <name>
- **CLAUDE.md ref:** <the "Open work" bullet this expands>
```
