"""Shut down all: wait until the modules have really halted.

After "Shut down all" the controller sends each module ``shutdown`` and then
pings them until none answers (a halted Pi stops replying), reporting each
change, before it shuts itself down. The browser then counts down
CONTROLLER_HALT_S and says it's safe to turn off the power.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable

SHUTDOWN_WAIT_S = 60.0   # longest wait for the modules before the controller goes
CONTROLLER_HALT_S = 20   # how long the browser allows the controller to halt
# Answering ping this many times in a row after going quiet means it was a
# dropped packet, not a halt: a module counts as down after DOWN_AFTER misses.
DOWN_AFTER = 3


def ping(ip: str) -> bool:
    """True if *ip* answers one ping within a second."""
    try:
        r = subprocess.run(["ping", "-c", "1", "-W", "1", ip],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=3)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return r.returncode == 0


def wait_for_modules_down(targets: list[dict], *, timeout_s: float = SHUTDOWN_WAIT_S,
                          on_change: Callable[[list[dict]], None] | None = None,
                          ping_fn: Callable[[str], bool] = ping,
                          sleep: Callable[[float], None] = time.sleep,
                          clock: Callable[[], float] = time.monotonic,
                          settle_s: float = 3.0) -> dict[str, bool]:
    """Poll *targets* ([{id, name, ip}]) until each has missed DOWN_AFTER
    pings in a row, or *timeout_s* passes. A module with no IP counts as
    down. *on_change* gets [{id, name, down}] whenever one goes down. Waits
    *settle_s* first so a module still running its shutdown isn't polled
    while it's obviously up. Returns {id: down}."""
    misses = {t["id"]: 0 for t in targets}
    down = {t["id"]: not t.get("ip") for t in targets}

    def report():
        if on_change:
            on_change([{"id": t["id"], "name": t.get("name") or t["id"],
                        "down": down[t["id"]]} for t in targets])

    if any(down.values()):
        report()
    deadline = clock() + timeout_s
    if settle_s:
        sleep(settle_s)
    while not all(down.values()) and clock() < deadline:
        changed = False
        for t in targets:
            mid = t["id"]
            if down[mid]:
                continue
            if ping_fn(t["ip"]):
                misses[mid] = 0
            else:
                misses[mid] += 1
                if misses[mid] >= DOWN_AFTER:
                    down[mid] = True
                    changed = True
        if changed:
            report()
        if not all(down.values()):
            sleep(1.0)
    return down
