# Contributing to SAVIOUR
SAVIOUR is designed to be easily extensible to new types of modules and new user interfaces to control them in experiment specific ways.
Any contributions you make are **greatly appreciated**.

For a deeper understanding of how the SAVIOUR software works, please refer to [How SAVIOUR Works](../how_it_works.md).

## New Modules
The basic steps for creating the new module "AmazingNewModule":

1. Create the folder src/modules/variants/amazing_new_module
2. In that folder, create the files amazing_new_module.py, amazing_new_module_config.json
3. Following the example of src/modules/variants/template, create your new module
4. Add a `variant.conf` in the same folder (see [Variant manifests](#variant-manifests-variantconf) below) — this is what makes `saviour-config` offer your module as a selectable type. No `saviour-config` code changes needed for a normal module.
5. Use saviour-config to deploy your AmazingNewModule on a network with a controller and test its behaviour

## New Controllers / GUIs
Many experiments are able to use the basic SAVIOUR GUI, but sometimes novel variants are developed for tasks with specific requirements such as APA, Loom, Habitat, and Acoustic Startle. This may be due to the need for a particular visual layout (a 4x4 grid of livestreams), or specific features ("activate grid" or "play sound" buttons). 

The SAVIOUR frontend is built with React, meaning that there is a library of reusable components (e.g. livestream cards, a sidebar) that can be used for creating the appropriate GUI for your use case. 

Some experiments also require the implementation of specific logic, either to make the GUI work properly (new websocket routes) or to enable experimental logic (e.g. in APA, if apa_camera_module detects rat in shock zone, tell apa_arduino_module to activate shock grid). In this case, a new controller / web program should be added in src/controller/variants, following the pattern in other controllers found there — and needs its own `variant.conf` too, same as a module.

More details to come soon here as a refactor is planned around how controllers and frontends are implemented!

## Variant manifests (`variant.conf`)

Every folder under `src/controller/variants/` and `src/modules/variants/` needs a
`variant.conf` — a flat `KEY=value` file `saviour-config` reads (via
`list_variants()`) to build its type-selection menus. Without one, your
variant exists on disk but never shows up as a selectable option.

```bash
# src/modules/variants/amazing_new_module/variant.conf
NAME="Amazing New Module"
DESCRIPTION="One-line description shown in the saviour-config menu"
APT_PACKAGES="some-apt-package another-package"   # optional, space-separated
```

```bash
# src/controller/variants/amazing_new_task/variant.conf
NAME="Amazing New Task"
DESCRIPTION="One-line description shown in the saviour-config menu"
FRONTEND="amazing_new_task"   # optional, defaults to the folder's own slug
```

- `NAME` / `DESCRIPTION` are required — these populate the whiptail menu.
- `APT_PACKAGES` (module-side only) — space-separated apt packages
  `saviour-config` installs automatically when that module type is selected
  (e.g. `hailo-all` for a Hailo-accelerated camera). Skip it if your module
  needs nothing beyond what `setup.sh`'s base install already covers.
- `FRONTEND` (controller-side only) — which frontend variant
  (`src/controller/frontend/src/<slug>/`) this controller uses. Only needed
  if it differs from your controller's own folder name.
- Quote values with spaces — `read_variant_value` strips one layer of
  surrounding double quotes.

Not yet supported: overriding the Python entrypoint filename or config
filename via the manifest (`ENTRYPOINT`/`CONFIG_FILE`) — every variant
today follows the `<slug>_<role>.py` / `<slug>_<role>_config.json`
convention, and there's no way to deviate from it yet. See CLAUDE.md's
"Architectural concerns" section if you need this — it's a deliberately
unbuilt follow-on, blocked on a separate systemd/config-loading fix.

## Development setup

After cloning, point git at this repo's tracked hooks so `src/__version__.py`
stays current (it's regenerated from `git describe` on every commit):

```bash
git config core.hooksPath scripts/git-hooks
```

This is a per-clone/per-machine setting (`.git/hooks/` itself is never
tracked by git), so it needs to be run once on every machine you commit
from — skipping it doesn't break anything immediately, it just means
`src/__version__.py` silently stops updating again.

## Branches

- main - The latest release of saviour e.g. v1.2
- staging - This branch is used for final testing of new releases
- fix/ - Prefix for a branch in which a fix is developed
- feat/ - Prefix for a branch in which a new feature is developed
- refactor/ - Prefix for a branch in which a refactor is implemented

## Commits
Use the conventional commits framework wherever possible
https://www.conventionalcommits.org/en/v1.0.0/#summary 

## Style
Style is enforced by ruff (ruff check/ruff format); it's PEP 8 based with an 88-character line length.

## Workflow

1. Fork the Project
2. Create your Feature Branch (`git checkout -b feat/AmazingNewModule`)
3. Commit your Changes (`git commit -m 'feat: Create AmazingNewModule'`)
4. Push to the Branch (`git push origin feat/AmazingNewModule`)
5. Open a Pull Request between your branch and the "staging" branch
6. When the changes are stable, staging will be given a tag for a new release and this will be merged with main - your AmazingNewModule is now part of SAVIOUR!