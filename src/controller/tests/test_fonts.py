"""Tests for src/controller/fonts.py (FontStore).

Pure file I/O against a temp directory -- no root/hardware needed.
"""

import json
import os
import tempfile

import pytest

from src.controller.fonts import (
    MAX_FACE_BYTES,
    MAX_FACES,
    FontError,
    FontStore,
    sniff_format,
)

WOFF2 = b"wOF2" + b"\x00" * 60
TTF = b"\x00\x01\x00\x00" + b"\x01" * 60
OTF = b"OTTO" + b"\x02" * 60


def _store() -> FontStore:
    return FontStore(fonts_dir=os.path.join(tempfile.mkdtemp(), "fonts"))


def _meta(name="Inter", faces=None, **over) -> dict:
    meta = {"name": name, "faces": faces or [{"field": "f0", "weight": "400"}]}
    meta.update(over)
    return meta


# --- save / list round-trip ------------------------------------------------

def test_save_then_list_round_trips():
    s = _store()
    saved = s.save(_meta("Open Sans", faces=[
        {"field": "f0", "weight": "100 900", "style": "normal",
         "unicode_range": "U+0000-00FF, U+0131"},
        {"field": "f1", "weight": 700, "style": "italic"},
    ], source={"kind": "google",
               "url": "https://fonts.googleapis.com/css2?family=Open+Sans"}),
        {"f0": WOFF2, "f1": TTF})
    assert saved["id"] == "open-sans"
    assert [f["format"] for f in saved["faces"]] == ["woff2", "truetype"]
    assert saved["faces"][0]["weight"] == "100 900"
    assert saved["faces"][1]["weight"] == "700"
    assert saved["bytes"] == len(WOFF2) + len(TTF)
    assert saved["source"]["kind"] == "google"

    listed = s.list()
    assert listed == [saved]


def test_file_names_are_content_hashes_and_resolvable():
    s = _store()
    saved = s.save(_meta(), {"f0": WOFF2})
    fname = saved["faces"][0]["file"]
    assert fname.endswith(".woff2") and len(fname) == 16 + len(".woff2")
    path = s.file_path("inter", fname)
    with open(path, "rb") as f:
        assert f.read() == WOFF2


def test_font_with_an_existing_name_is_refused():
    s = _store()
    s.save(_meta("Inter"), {"f0": WOFF2})
    with pytest.raises(FontError, match="already added"):
        s.save(_meta("inter"), {"f0": OTF})
    assert len(s.list()) == 1


def test_names_that_slug_alike_get_distinct_ids():
    s = _store()
    assert s.save(_meta("Open Sans"), {"f0": WOFF2})["id"] == "open-sans"
    assert s.save(_meta("Open_Sans"), {"f0": OTF})["id"] == "open-sans-2"


def test_name_matching_builtin_is_suffixed():
    s = _store()
    assert s.save(_meta("System"), {"f0": WOFF2})["id"] == "system-2"


def test_duplicate_faces_are_stored_once():
    s = _store()
    saved = s.save(_meta(faces=[{"field": "a"}, {"field": "b"}]),
                   {"a": WOFF2, "b": WOFF2})
    assert len(saved["faces"]) == 1


def test_delete_removes_the_font():
    s = _store()
    s.save(_meta(), {"f0": WOFF2})
    assert s.delete("inter") is True
    assert s.list() == []
    assert s.delete("inter") is False


def test_delete_refuses_builtin_and_bad_ids():
    s = _store()
    with pytest.raises(FontError):
        s.delete("system")
    with pytest.raises(FontError):
        s.delete("../etc")


def test_missing_directory_lists_empty():
    assert _store().list() == []


# --- validation ------------------------------------------------------------

@pytest.mark.parametrize("name", ["", "x" * 61, "Evil</style>", 'A "quoted" name', "a;b"])
def test_bad_names_rejected(name):
    with pytest.raises(FontError):
        _store().save(_meta(name), {"f0": WOFF2})


@pytest.mark.parametrize("weight", ["heavy", "0", "1001", "100 200 300", True])
def test_bad_weights_rejected(weight):
    with pytest.raises(FontError):
        _store().save(_meta(faces=[{"field": "f0", "weight": weight}]), {"f0": WOFF2})


def test_weight_words_and_reversed_ranges_normalised():
    s = _store()
    saved = s.save(_meta(faces=[{"field": "a", "weight": "bold"},
                                {"field": "b", "weight": "900 100"}]),
                   {"a": WOFF2, "b": TTF})
    assert [f["weight"] for f in saved["faces"]] == ["700", "100 900"]


def test_bad_style_rejected():
    with pytest.raises(FontError):
        _store().save(_meta(faces=[{"field": "f0", "style": "slanted"}]), {"f0": WOFF2})


@pytest.mark.parametrize("rng", ["0000-00FF", "U+00FF; } body { x", "U+GGGG"])
def test_bad_unicode_range_rejected(rng):
    with pytest.raises(FontError):
        _store().save(_meta(faces=[{"field": "f0", "unicode_range": rng}]), {"f0": WOFF2})


def test_non_font_file_rejected():
    with pytest.raises(FontError):
        _store().save(_meta(), {"f0": b"<html>not a font</html>"})


def test_missing_file_field_rejected():
    with pytest.raises(FontError):
        _store().save(_meta(), {})


def test_oversized_face_rejected():
    with pytest.raises(FontError):
        _store().save(_meta(), {"f0": b"wOF2" + b"\x00" * MAX_FACE_BYTES})


def test_too_many_faces_rejected():
    faces = [{"field": f"f{i}"} for i in range(MAX_FACES + 1)]
    with pytest.raises(FontError):
        _store().save(_meta(faces=faces), {f"f{i}": WOFF2 for i in range(MAX_FACES + 1)})


def test_source_must_be_google_fonts_or_files():
    s = _store()
    with pytest.raises(FontError):
        s.save(_meta(source={"kind": "google", "url": "https://evil.example/x.css"}),
               {"f0": WOFF2})
    with pytest.raises(FontError):
        s.save(_meta(source={"kind": "url"}), {"f0": WOFF2})
    assert s.save(_meta(source={"kind": "files"}), {"f0": WOFF2})["source"] == {"kind": "files"}


def test_sniff_format():
    assert sniff_format(WOFF2) == ("woff2", "woff2")
    assert sniff_format(b"wOFF....") == ("woff", "woff")
    assert sniff_format(b"true....") == ("truetype", "ttf")
    assert sniff_format(OTF) == ("opentype", "otf")


# --- serving and resilience -------------------------------------------------

@pytest.mark.parametrize("font_id,fname", [
    ("inter", "../font.json"), ("inter", "font.json"), ("../x", "0123456789abcdef.woff2"),
    ("inter", "0123456789abcdef.exe"),
])
def test_file_path_rejects_anything_the_store_did_not_write(font_id, fname):
    s = _store()
    s.save(_meta(), {"f0": WOFF2})
    assert s.file_path(font_id, fname) is None


def test_list_skips_broken_fonts():
    s = _store()
    s.save(_meta("Good"), {"f0": WOFF2})
    broken = os.path.join(s.dir, "broken")
    os.makedirs(broken)
    with open(os.path.join(broken, "font.json"), "w") as f:
        json.dump({"name": "Broken", "faces": [{"file": "0123456789abcdef.woff2"}]}, f)
    os.makedirs(os.path.join(s.dir, ".tmp-leftover"))
    assert [f["id"] for f in s.list()] == ["good"]
