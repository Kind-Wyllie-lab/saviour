#!/usr/bin/env python3
"""Export a SAVIOUR session folder to one NWB file, on a workstation.

The same converter the controller's Post-processing page runs
(src/controller/nwb_convert.py). Needs the NWB extra:

    pip install -e ".[nwb]"        # or: pip install pynwb

    python tools/export_nwb.py /path/to/share/<session> \\
        [--species "Rattus norvegicus"] [--subject-id R7] [--sex M] [--age P90D] \\
        [--output out.nwb] [--no-audio] [--no-video] [--overwrite]

Writes <session>/<session>.nwb by default; video is linked by relative path,
so keep the .nwb inside the session folder.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.controller.nwb_convert import main  # noqa: E402 -- after the path fix

if __name__ == "__main__":
    sys.exit(main())
