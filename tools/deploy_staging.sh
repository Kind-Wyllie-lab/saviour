#!/usr/bin/env bash
# Deploy origin/<branch> to the whole rig and wait until it has landed.
# Runs ON the controller (reads the admin password there with sudo, so it
# never leaves the box). From another machine:
#     ssh pi@10.0.0.1 'bash -s' < tools/deploy_staging.sh
#
# Done = the controller restarted (uptime_s fell below its pre-deploy value)
# and then every module was online for two polls in a row. The reported
# `version` is NOT used: it is written one commit behind by the pre-commit
# hook and is often unchanged by a merge, so waiting for it to change can
# hang forever on a deploy that finished in a minute.
#
# Exit codes: 0 deployed, 1 refused (rig busy / bad reply), 2 timed out.
# FORCE=1 skips the signed-in-admin check (sessions/recording still refuse).
set -u
PW=$(sudo cat /etc/saviour/admin_credentials | tr -d "\n")
B=http://10.0.0.1:5000
TIMEOUT_S=${TIMEOUT_S:-600}

state() { curl -s -m 5 -H "Authorization: Bearer $PW" "$B/api/v1/state"; }
field() { python3 -c "import json,sys; d=json.load(sys.stdin); print($1)" 2>/dev/null; }

s=$(state) || { echo "controller not answering"; exit 1; }
active=$(echo "$s" | field "d['sessions']['active']")
recording=$(echo "$s" | field "d['recording']")
admins=$(echo "$s" | field "d['ui_clients']['admins']")
up0=$(echo "$s" | field "d['uptime_s']")
total=$(echo "$s" | field "d['modules']['total']")
[ -n "$up0" ] || { echo "bad /api/v1/state reply: $s"; exit 1; }
if [ "$active" != "0" ] || [ "$recording" != "False" ]; then
    echo "refusing: active sessions=$active recording=$recording"; exit 1
fi
if [ "$admins" != "0" ] && [ "${FORCE:-0}" != "1" ]; then
    echo "refusing: $admins admin(s) signed in to the web UI (FORCE=1 to override)"
    exit 1
fi

code=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
    -H "Content-Type: application/json" -H "Authorization: Bearer $PW" \
    -d '{"apply_controller": true, "deploy_modules": true}' \
    "$B/api/v1/system/update")
[ "$code" = "202" ] || { echo "update refused: HTTP $code"; exit 1; }
echo "update accepted (uptime before: ${up0}s, $total modules)"

start=$(date +%s); restarted=0; good=0
while [ $(( $(date +%s) - start )) -lt "$TIMEOUT_S" ]; do
    sleep 5
    s=$(state) || continue          # down while restarting
    up=$(echo "$s" | field "d['uptime_s']") || continue
    online=$(echo "$s" | field "d['modules']['online']")
    [ -n "$up" ] || continue
    if [ "$restarted" = 0 ] && python3 -c "import sys; sys.exit(not $up < $up0)"; then
        restarted=1; echo "controller restarted ($(( $(date +%s) - start ))s)"
    fi
    if [ "$restarted" = 1 ] && [ "$online" = "$total" ]; then
        good=$((good + 1))
        if [ "$good" -ge 2 ]; then
            ver=$(echo "$s" | field "d['version']")
            echo "deployed in $(( $(date +%s) - start ))s: $online/$total modules online, version $ver"
            git -C "$(dirname "$0")/.." log --oneline -1 2>/dev/null
            exit 0
        fi
    else
        good=0
    fi
done
echo "timed out after ${TIMEOUT_S}s (restarted=$restarted, online=${online:-?}/$total)"
exit 2
