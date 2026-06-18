#!/usr/bin/env bash
# Deprecated — use saviour-config instead.
exec "$(dirname "$(readlink -f "$0")")/saviour-config" "$@"
