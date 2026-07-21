"""ZipFile.extractall() ignores the Unix mode bits stored in each entry's
external_attr, so files extracted while running as root come out with root's
umask instead of their original (e.g. executable) permissions. Both the
controller and module update paths need extraction restored, hence shared here.
"""

import os
import stat
import zipfile


def extract_preserving_permissions(zip_path: str, extract_dir: str) -> None:
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(extract_dir)
        for info in z.infolist():
            mode = info.external_attr >> 16
            if not mode:
                continue
            os.chmod(os.path.join(extract_dir, info.filename), stat.S_IMODE(mode))
