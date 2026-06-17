#!/usr/bin/env bash
# Detect and remove git-tracked files that contain null bytes, then restore them
# from HEAD via git reset --hard.  Null-byte corruption is caused by SD card
# writes interrupted during ungraceful power-off.
#
# Usage:
#   ./repair_null_bytes.sh            # run from repo root, or
#   bash /usr/local/src/saviour/repair_null_bytes.sh

set -euo pipefail

REPO_ROOT="$(git -C "$(dirname "$0")" rev-parse --show-toplevel)"
cd "$REPO_ROOT"

echo "Scanning for null-byte corruption in tracked files..."

REMOVED=0
while IFS= read -r f; do
    [ -f "$f" ] || continue
    if python3 -c "
import sys
with open(sys.argv[1], 'rb') as fh:
    sys.exit(0 if b'\\x00' in fh.read() else 1)
" "$f" 2>/dev/null; then
        echo "  removing corrupted: $f"
        rm -f "$f"
        REMOVED=$((REMOVED + 1))
    fi
done < <(git ls-files)

if [ "$REMOVED" -gt 0 ]; then
    echo "Removed $REMOVED corrupted file(s). Running git reset --hard..."
    git reset --hard HEAD
    echo "Done."
else
    echo "No corrupted files found."
fi
