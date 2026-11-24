"""
Which sound card a sound module has, and how many outputs it gives.

Two HATs are in use (plans/session-protocol.md, "Adding sound"):

- **HiFiBerry DAC8x** (Pi 5 only): 8 channels = 4 stereo outputs. Its overlay
  (``hifiberry-dac8x``) uses a dummy codec, so the kernel has nothing to
  probe: with the overlay loaded the card appears whether or not the board
  is fitted. Presence can't be confirmed from software alone.
- **Raspberry Pi DAC Pro**: 2 channels = 1 stereo output (RCA and headphone
  carry the same signal). Its overlay (``rpi-dacpro``) probes a PCM5122 over
  I2C, so the card only appears when the board answers.

The output count comes from what ALSA says the card accepts
(``aplay --dump-hw-params``), with the board's known count as a fallback,
never from the board name alone. Output *n* is channels 2n-1 and 2n.

Everything here is a pure function of text or files, so it is tested without
the hardware (src/modules/tests/test_sound_hardware.py).
"""

from __future__ import annotations

import os
import re
import subprocess

# Cards that are never the sound module's output.
_IGNORED_CARD = re.compile(r"^(vc4hdmi\d*|hdmi.*|headphones|bcm2835.*)$", re.IGNORECASE)

# Known boards: (name to show, channels, whether the driver proves the board
# is there). Matched against the card's id + names with spaces and
# punctuation removed.
_BOARDS = (
    ("dac8x", "HiFiBerry DAC8x", 8, False),
    ("dacpro", "Raspberry Pi DAC Pro", 2, True),
    ("hifiberry", "HiFiBerry", 2, False),
)

_CARD_LINE = re.compile(r"^\s*(\d+)\s+\[([^\]]+?)\s*\]:\s*(\S+)\s+-\s+(.*)$")


def parse_asound_cards(text: str) -> list[dict]:
    """/proc/asound/cards -> [{index, id, driver, name, longname}]."""
    cards: list[dict] = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = _CARD_LINE.match(line)
        if not m:
            continue
        longname = lines[i + 1].strip() if i + 1 < len(lines) else ""
        cards.append({"index": int(m.group(1)), "id": m.group(2).strip(),
                      "driver": m.group(3), "name": m.group(4).strip(),
                      "longname": longname})
    return cards


def _squash(*parts: str) -> str:
    return re.sub(r"[^a-z0-9]", "", " ".join(parts).lower())


def board_of(card: dict) -> tuple[str, int | None, bool]:
    """(board name, its known channel count or None, presence verifiable)."""
    key = _squash(card.get("id", ""), card.get("driver", ""), card.get("name", ""),
                  card.get("longname", ""))
    for needle, name, channels, verifiable in _BOARDS:
        if needle in key:
            return name, channels, verifiable
    return card.get("name") or card.get("id") or "sound card", None, True


def pick_card(cards: list[dict]) -> dict | None:
    """The card to play on: a known board first, else the first card that
    isn't HDMI or the Pi's own headphone output."""
    usable = [c for c in cards if not _IGNORED_CARD.match(c.get("id", ""))]
    for c in usable:
        if board_of(c)[1] is not None:
            return c
    return usable[0] if usable else None


def parse_channels(dump: str) -> tuple[int, int] | None:
    """The CHANNELS line of ``aplay --dump-hw-params``: "CHANNELS: 2" or
    "CHANNELS: [2 8]" -> (min, max)."""
    m = re.search(r"CHANNELS:\s*(?:\[\s*(\d+)\s+(\d+)\s*\]|(\d+))", dump)
    if not m:
        return None
    if m.group(3):
        n = int(m.group(3))
        return n, n
    return int(m.group(1)), int(m.group(2))


def query_channels(card_id: str, run=subprocess.run) -> tuple[int, int] | None:
    """Ask ALSA how many channels the card takes. Opens the device for a
    moment (empty input), so it fails while something else holds it."""
    try:
        res = run(["aplay", "-D", f"hw:CARD={card_id},DEV=0", "--dump-hw-params",
                   "-t", "raw", "-f", "S16_LE", "-r", "48000", "-c", "2", "-"],
                  input=b"", capture_output=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    out = res.stdout + res.stderr
    text = out.decode("utf-8", "replace") if isinstance(out, bytes) else str(out)
    return parse_channels(text)


def detect(cards_text: str | None = None, run=subprocess.run) -> dict:
    """What sound card this Pi has (*run* None: skip asking ALSA for the
    channel count). Keys: present, board, card_id,
    card_index, device (ALSA name to play on), channels, outputs,
    presence_verified, channels_from ("alsa" | "board" | "default"),
    detail (one line for the readiness check)."""
    if cards_text is None:
        try:
            with open("/proc/asound/cards") as f:
                cards_text = f.read()
        except OSError:
            cards_text = ""
    card = pick_card(parse_asound_cards(cards_text))
    if card is None:
        return {"present": False, "board": None, "card_id": None, "card_index": None,
                "device": None, "channels": 0, "outputs": 0, "presence_verified": False,
                "channels_from": None, "detail": "No sound card (other than HDMI) found"}
    board, known, verifiable = board_of(card)
    # run=None: don't open the device (a stream may hold it).
    queried = query_channels(card["id"], run=run) if run is not None else None
    if queried:
        channels, source = queried[1], "alsa"
    elif known:
        channels, source = known, "board"
    else:
        channels, source = 2, "default"
    outputs = max(1, channels // 2)
    detail = (f"{board} (card {card['index']} '{card['id']}'): {channels} channels, "
              f"{outputs} stereo output{'' if outputs == 1 else 's'}")
    if not verifiable:
        detail += "; the driver can't confirm the board is fitted"
    return {"present": True, "board": board, "card_id": card["id"],
            "card_index": card["index"], "device": f"hw:CARD={card['id']},DEV=0",
            "channels": channels, "outputs": outputs, "presence_verified": verifiable,
            "channels_from": source, "detail": detail}


def sound_files(directory: str) -> dict[str, float | None]:
    """File name -> length in seconds for each playable file in *directory*
    (None when the length can't be read). Hidden files and folders skipped."""
    try:
        import soundfile
    except ImportError:  # pragma: no cover -- a main-env dependency
        soundfile = None
    out: dict[str, float | None] = {}
    try:
        names = sorted(os.listdir(directory))
    except OSError:
        return out
    for name in names:
        path = os.path.join(directory, name)
        if name.startswith(".") or not os.path.isfile(path):
            continue
        length = None
        if soundfile is not None:
            try:
                info = soundfile.info(path)
                if info.samplerate > 0:
                    length = round(info.frames / info.samplerate, 6)
            except Exception:
                continue  # not a sound file soundfile can read: not playable
        out[name] = length
    return out
