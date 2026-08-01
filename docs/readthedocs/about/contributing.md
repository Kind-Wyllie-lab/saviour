# Contributing
SAVIOUR is designed to be easily extensible to new types of modules and new user interfaces to control them in experiment specific ways.
Any contributions you make are **greatly appreciated**.

For a deeper understanding of how the SAVIOUR software works, please refer to [How SAVIOUR Works](../how_it_works.md).

## New Modules
The basic steps for creating the new module "AmazingNewModule":<br>
<ol>
<li>Create the folder src/modules/examples/amazing_new_module 
<li>In that folder, create the files amazing_new_module.py, amazing_new_module_config.json
<li>Following the example of src/modules/examples/template_module, create your new module
<li>Modify the saviour-config script to be able to configure a pi to run as your module
<li>Use saviour-config to deploy your AmazingNewModule on a network with a controller and test its behaviour
</ol>

## New Controllers / GUIs
Many experiments are able to use the basic SAVIOUR GUI, but sometimes novel variants are developed for tasks with specific requirements such as APA, Loom, Habitat, and Acoustic Startle. This may be due to the need for a particular visual layout (a 4x4 grid of livestreams), or specific features ("activate grid" or "play sound" buttons). 

The SAVIOUR frontend is built with React, meaning that there is a library of reusable components (e.g. livestream cards, a sidebar) that can be used for creating the appropriate GUI for your use case. 

Some experiments also require the implementation of specific logic, either to make the GUI work properly (new websocket routes) or to enable experimental logic (e.g. in APA, if apa_camera_module detects rat in shock zone, tell apa_arduino_module to activate shock grid). In this case, a new controller / web program should be added in src/controllers/examples, following the pattern in other controllers found there.

More details to come soon here as a refactor is planned around how controllers and frontends are implemented!

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