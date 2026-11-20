"""
Rig profile check: does this rig have the hardware it should?

Each rig type (APA, acoustic startle, habitat, loom, ...) expects a set of
modules. The expected set is ``rig.expected`` in controller config, with
defaults in each variant's ``<variant>_controller_config.json``::

    "rig": {
      "expected": [
        {"label": "Shock/motor controller", "types": ["apa_arduino"],
         "min": 1, "severity": "required"}
      ],
      "startup_grace_s": 120,
      "alert_after_s": 600
    }

``types`` are glob patterns on the module type (``"*camera"`` = any camera).
A module counts towards ``min`` only when it is online and reports no
``hardware_fault``; online-but-faulty and offline modules are listed by name.
Only modules seen since the controller started are known (the registry is
in memory), so a module that never showed up is counted, not named.

``evaluate()`` is pure; ``RigMonitor`` re-evaluates every few seconds,
pushes changes to the frontend (``rig_check``) and alerts once a day per item
when a required one stays short. Plan: plans/rig-expected-hardware.md.
"""

from __future__ import annotations

import fnmatch
import logging
import threading
import time
from collections.abc import Callable
from datetime import date

from src.shared import ttl_application
from src.shared.supervised import supervise

DEFAULT_GRACE_S = 120
DEFAULT_ALERT_AFTER_S = 600
SEVERITIES = ("required", "optional")
# Session conditions an item can depend on (``when``): such an item counts
# only for sessions with that condition, e.g. an ephys sync module for a
# session that records ephys. The live banner has no session, so it leaves
# them out.
CONDITIONS = ("ephys",)


class RigProfileError(ValueError):
    pass


def normalise_entry(entry: object) -> dict:
    """One ``rig.expected`` entry in canonical form, or RigProfileError."""
    if not isinstance(entry, dict):
        raise RigProfileError("each expected item must be an object")
    types = entry.get("types", entry.get("type"))
    if isinstance(types, str):
        types = [types]
    if not isinstance(types, list):
        raise RigProfileError("'types' must be a list of module types")
    types = [str(t).strip() for t in types if str(t).strip()]
    if not types:
        raise RigProfileError("each expected item needs at least one module type")
    try:
        minimum = int(entry.get("min", 1))
    except (TypeError, ValueError):
        raise RigProfileError(
            f"'min' must be a whole number (got {entry.get('min')!r})") from None
    if minimum < 0:
        raise RigProfileError("'min' can't be negative")
    severity = str(entry.get("severity") or "required")
    if severity not in SEVERITIES:
        raise RigProfileError(f"'severity' must be one of {', '.join(SEVERITIES)}")
    label = str(entry.get("label") or "").strip() or " / ".join(types)
    out = {"label": label, "types": types, "min": minimum, "severity": severity}
    application = entry.get("application")
    if application:
        if application not in ttl_application.APPLICATIONS:
            raise RigProfileError(
                f"'application' must be one of "
                f"{', '.join(ttl_application.APPLICATIONS)}")
        out["application"] = application
    when = entry.get("when")
    if when:
        if when not in CONDITIONS:
            raise RigProfileError(f"'when' must be one of {', '.join(CONDITIONS)}")
        out["when"] = when
    return out


def validate_profile(expected: object) -> list[dict]:
    """The whole list, canonical, or RigProfileError naming the bad item."""
    if expected is None:
        return []
    if not isinstance(expected, list):
        raise RigProfileError("'rig.expected' must be a list")
    out = []
    for i, e in enumerate(expected, 1):
        try:
            out.append(normalise_entry(e))
        except RigProfileError as err:
            raise RigProfileError(f"item {i}: {err}") from None
    return out


def load_profile(config: dict, logger: logging.Logger | None = None) -> dict:
    """``{"expected", "startup_grace_s", "alert_after_s"}`` from the whole
    controller config. Invalid entries are skipped (and logged), so one bad
    item can't hide the rest. With no ``rig.expected`` at all, a non-zero
    legacy ``recording.expected_module_counts`` is used instead."""
    rig = config.get("rig") if isinstance(config.get("rig"), dict) else {}
    expected = rig.get("expected")
    if expected is None:
        legacy = (config.get("recording") or {}).get("expected_module_counts") or {}
        expected = [{"types": [t], "min": n, "severity": "required"}
                    for t, n in sorted(legacy.items())
                    if isinstance(n, int) and n > 0]
    entries = []
    for e in expected if isinstance(expected, list) else []:
        try:
            entries.append(normalise_entry(e))
        except RigProfileError as err:
            (logger or logging.getLogger(__name__)).warning(
                f"Ignoring invalid rig.expected item {e!r}: {err}")

    def _num(key, default):
        try:
            return max(0.0, float(rig.get(key, default)))
        except (TypeError, ValueError):
            return float(default)

    return {
        "expected": entries,
        "startup_grace_s": _num("startup_grace_s", DEFAULT_GRACE_S),
        "alert_after_s": _num("alert_after_s", DEFAULT_ALERT_AFTER_S),
    }


def matches(types: list[str], module_type: str) -> bool:
    return any(fnmatch.fnmatchcase(module_type or "", t) for t in types)


def matches_entry(entry: dict, module: dict) -> bool:
    """Type glob, plus the TTL application when the entry names one."""
    if not matches(entry["types"], module.get("type") or ""):
        return False
    app = entry.get("application")
    return not app or ttl_application.module_application(module) == app


def _is_online(m: dict) -> bool:
    return bool(m.get("online", True)) and m.get("status") != "OFFLINE"


def evaluate(expected: list[dict], modules: dict, health: dict | None = None,
             *, pending: bool = False, conditions=()) -> dict:
    """Compare the expected list with the live modules.

    ``modules``: id -> module dict (``type``, ``name``, ``online``,
    ``status``), as ``facade.get_modules()``. ``health``: id -> health dict
    (``hardware_fault``). ``pending``: inside the startup grace, so a short
    item reads ``pending`` instead of ``short``.

    Returns ``{"level": ok|pending|warning|error, "ok": bool, "pending":
    bool, "items": [...], "summary": str}``; ``error`` = a required item is
    short or faulty, ``warning`` = only optional ones are.

    ``conditions``: the session's (e.g. ``{"ephys"}``). An item with a
    ``when`` outside them is still evaluated but marked ``active: False``
    and left out of the level, summary and shortfalls; see
    ``with_conditions`` to re-judge a result for a session.
    """
    health = health or {}
    items = []
    for e in expected:
        present, faulty, offline = [], [], []
        for mid, m in sorted(modules.items()):
            if not matches_entry(e, m):
                continue
            who = {"id": mid, "name": m.get("name") or mid}
            fault = (health.get(mid) or {}).get("hardware_fault")
            if not _is_online(m):
                offline.append(who)
            elif fault:
                faulty.append({**who, "reason": str(fault)})
            else:
                present.append(who)
        if len(present) >= e["min"]:
            status = "ok"
        elif pending:
            status = "pending"
        elif faulty:
            status = "faulty"
        else:
            status = "short"
        items.append({**e, "count": len(present), "status": status,
                      "present": present, "faulty": faulty, "offline": offline})
    return with_conditions({"items": items}, conditions)


def with_conditions(result: dict, conditions=()) -> dict:
    """``result`` re-judged for a session with these conditions: which
    conditional items are active, and the level/summary over active ones.
    Returns a new dict (other keys kept)."""
    conds = set(conditions or ())
    items = [{**i, "active": not i.get("when") or i["when"] in conds}
             for i in (result or {}).get("items") or []]
    live = [i for i in items if i["active"]]
    bad = [i for i in live if i["status"] in ("short", "faulty")]
    if any(i["severity"] == "required" for i in bad):
        level = "error"
    elif bad:
        level = "warning"
    elif any(i["status"] == "pending" for i in live):
        level = "pending"
    else:
        level = "ok"
    return {
        **(result or {}),
        "level": level,
        "ok": level == "ok",
        "pending": level == "pending",
        "items": items,
        "conditions": sorted(conds),
        "summary": summarise(live),
    }


def describe_item(i: dict) -> str:
    """One line for an item that isn't ok, e.g. 'Shock/motor controller:
    0 of 1 (apa_arduino_3f2a offline)'."""
    parts = [f"{i['label']}: {i['count']} of {i['min']}"]
    notes = [f"{f['name']} — {f['reason']}" for f in i["faulty"]]
    notes += [f"{o['name']} offline" for o in i["offline"]]
    if notes:
        parts.append(f"({'; '.join(notes)})")
    return " ".join(parts)


def summarise(items: list[dict]) -> str:
    bad = [i for i in items if i["status"] in ("short", "faulty")]
    if not bad:
        if any(i["status"] == "pending" for i in items):
            return "Waiting for modules to register"
        return "All expected hardware present" if items else "No expected hardware set"
    return "; ".join(describe_item(i) for i in bad)


def shortfalls(result: dict | None, severity: str | None = None) -> list[dict]:
    """Items that are short or faulty (optionally of one severity)."""
    if not isinstance(result, dict):
        return []
    return [i for i in result.get("items") or []
            if i.get("active", True)
            and i["status"] in ("short", "faulty")
            and (severity is None or i["severity"] == severity)]


class RigMonitor:
    """Re-evaluates the rig profile every ``interval`` s, emits changes,
    and sends one alert per item per day when a required item has been
    short for ``rig.alert_after_s`` (teams toggle ``notify_rig_incomplete``)."""

    def __init__(self, *, get_config: Callable[[], dict],
                 get_modules: Callable[[], dict],
                 get_health: Callable[[], dict],
                 emit: Callable[[str, dict], None] | None = None,
                 send_alert: Callable[..., None] | None = None,
                 logger: logging.Logger | None = None,
                 interval: float = 5.0,
                 clock: Callable[[], float] = time.monotonic,
                 today: Callable[[], str] = lambda: date.today().isoformat()):
        self.get_config = get_config
        self.get_modules = get_modules
        self.get_health = get_health
        self.emit = emit
        self.send_alert = send_alert
        self.logger = logger or logging.getLogger(__name__)
        self.interval = interval
        self.clock = clock
        self.today = today
        self._started = clock()
        self._lock = threading.Lock()
        self._latest: dict | None = None
        self._short_since: dict[str, float] = {}   # label -> clock()
        self._alerted: dict[str, str] = {}         # label -> date alerted
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is None:
            self._thread = supervise("rig-monitor", self._loop,
                                     stop_event=self._stop, logger=self.logger)

    def stop(self) -> None:
        self._stop.set()

    def restart_grace(self) -> None:
        """Start the grace period again (e.g. every module is restarting
        after a fleet update)."""
        self._started = self.clock()

    def evaluate_now(self) -> dict:
        config = self.get_config() or {}
        profile = load_profile(config, self.logger)
        pending = self.clock() - self._started < profile["startup_grace_s"]
        try:
            health = self.get_health() or {}
        except Exception:
            health = {}
        result = evaluate(profile["expected"], self.get_modules() or {},
                          health, pending=pending)
        result["alert_after_s"] = profile["alert_after_s"]
        return result

    def latest(self) -> dict:
        with self._lock:
            if self._latest is not None:
                return self._latest
        return self.tick(emit=False)

    def for_session(self, conditions=()) -> dict:
        """The latest result re-judged for a session (e.g. ``{"ephys"}``)."""
        return with_conditions(self.latest(), conditions)

    def tick(self, emit: bool = True) -> dict:
        result = self.evaluate_now()
        with self._lock:
            changed = result != self._latest
            self._latest = result
        if changed and emit and self.emit:
            try:
                self.emit("rig_check", result)
            except Exception as e:
                self.logger.warning(f"rig_check emit failed: {e}")
        if changed and result["level"] in ("error", "warning"):
            self.logger.warning(f"Rig check: {result['summary']}")
        self._maybe_alert(result)
        return result

    def _maybe_alert(self, result: dict) -> None:
        now = self.clock()
        short = {i["label"]: i for i in shortfalls(result, "required")}
        for label in list(self._short_since):
            if label not in short:
                del self._short_since[label]
        if not short or not self.send_alert:
            for label in short:
                self._short_since.setdefault(label, now)
            return
        config = self.get_config() or {}
        enabled = (config.get("teams") or {}).get("notify_rig_incomplete", True)
        today = self.today()
        for label, item in short.items():
            since = self._short_since.setdefault(label, now)
            if now - since < result.get("alert_after_s", DEFAULT_ALERT_AFTER_S):
                continue
            if not enabled or self._alerted.get(label) == today:
                continue
            self._alerted[label] = today
            try:
                self.send_alert(
                    key=f"rig_incomplete_{label}_{today}",
                    title=f"Rig missing hardware — {label}",
                    message=(f"{describe_item(item)}.\n\nThis rig expects at least "
                             f"{item['min']} working {' / '.join(item['types'])} "
                             f"module(s). Check it is powered, cabled and online."),
                    severity="warning",
                )
            except Exception as e:
                self.logger.warning(f"rig alert failed: {e}")

    def _loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            self.tick()
            if stop_event.wait(self.interval):
                return
