"""
Tests for src/controller/compose_layout.py and the layout half of
compose.py (legacy translation, templates). Pure: no OpenCV, no ffmpeg.
"""

import pytest

from src.controller import compose_layout as cl
from src.controller.compose import (
    ComposeError,
    ComposeSpec,
    SessionStream,
    audio_height_px,
    layout_from_legacy,
    layout_template,
    panel_heights,
    plan_regions,
)


def _doc(**over):
    doc = {
        "canvas": {"width": 1920, "height": 1080},
        "tiles": [{"kind": "camera", "source": "cam_a", "rect": [0, 0, 1280, 720]}],
    }
    doc.update(over)
    return doc


# --------------------------------------------------------------------------- #
# validate_layout                                                             #
# --------------------------------------------------------------------------- #


def test_validate_fills_defaults():
    doc = cl.validate_layout(_doc(tiles=[
        {"kind": "camera", "source": "cam_a", "rect": [0, 0, 1280, 720]},
        {"kind": "spectrogram", "source": "mic/dev1", "rect": [0, 720, 1280, 180]},
    ]))
    assert doc["canvas"] == {"width": 1920, "height": 1080, "background": "#000000"}
    cam, spec = doc["tiles"]
    assert cam["id"] == "t1" and cam["z"] == 0
    assert cam["options"] == {"fit": "contain", "crop": None, "rotate": 0, "label": True}
    assert spec["options"]["window_s"] is None and spec["options"]["playhead"] == 0.5
    assert spec["options"]["color"] == "intensity" and spec["options"]["label"] is False
    assert doc["audio_tracks"] == []


@pytest.mark.parametrize("bad,match", [
    ({"canvas": {"width": 1921, "height": 1080}}, "even"),
    ({"canvas": {"width": 10, "height": 1080}}, "canvas.width"),
    ({"canvas": {"width": 1920, "height": 1080, "background": "red"}}, "colour"),
    ({"tiles": []}, "at least one tile"),
    ({"tiles": [{"kind": "spectrogram", "source": "mic/d", "rect": [0, 0, 100, 100]}]},
     "camera tile"),
    ({"tiles": [{"kind": "camera", "source": "cam_a", "rect": [1900, 0, 100, 100]}]},
     "outside the canvas"),
    ({"tiles": [{"kind": "camera", "source": "cam_a", "rect": [0, 0, 4, 100]}]},
     "smaller"),
    ({"tiles": [{"kind": "video", "source": "cam_a", "rect": [0, 0, 100, 100]}]},
     "kind"),
    ({"tiles": [{"kind": "camera", "source": "../x", "rect": [0, 0, 100, 100]}]},
     "camera source"),
    ({"tiles": [{"kind": "camera", "source": "a", "rect": [0, 0, 100, 100],
                 "options": {"rotate": 45}}]}, "rotate"),
    ({"tiles": [{"kind": "camera", "source": "a", "rect": [0, 0, 100, 100],
                 "options": {"crop": [0.5, 0, 0.8, 1]}}]}, "crop"),
    ({"tiles": [{"kind": "camera", "source": "a", "rect": [0, 0, 100, 100]},
                {"kind": "spectrogram", "source": "nodevice", "rect": [0, 0, 100, 100]}]},
     "spectrogram source"),
    ({"tiles": [{"kind": "camera", "source": "a", "rect": [0, 0, 100, 100]},
                {"kind": "spectrogram", "source": "m/d", "rect": [0, 0, 100, 100],
                 "options": {"window_s": 0.1}}]}, "window_s"),
    ({"tiles": [{"kind": "camera", "source": "a", "rect": [0, 0, 100, 100]},
                {"kind": "spectrogram", "source": "m/d", "rect": [0, 0, 100, 100],
                 "options": {"color": "neon"}}]}, "color"),
    ({"tiles": [{"id": "x", "kind": "camera", "source": "a", "rect": [0, 0, 10, 10]},
                {"id": "x", "kind": "camera", "source": "a", "rect": [0, 0, 10, 10]}]},
     "unique"),
    ({"audio_tracks": ["nofolder"]}, "audio track"),
])
def test_validate_rejects(bad, match):
    with pytest.raises(cl.LayoutError, match=match):
        cl.validate_layout(_doc(**bad))


def test_validate_caps_tiles():
    tiles = [{"kind": "camera", "source": "a", "rect": [0, 0, 10, 10]}] * (cl.MAX_TILES + 1)
    with pytest.raises(cl.LayoutError, match="at most"):
        cl.validate_layout(_doc(tiles=tiles))


def test_sources_are_distinct_and_ordered():
    doc = cl.validate_layout(_doc(tiles=[
        {"kind": "camera", "source": "b", "rect": [0, 0, 10, 10]},
        {"kind": "spectrogram", "source": "m/2", "rect": [0, 0, 10, 10]},
        {"kind": "camera", "source": "a", "rect": [0, 0, 10, 10]},
        {"kind": "camera", "source": "b", "rect": [0, 0, 10, 10]},
    ], audio_tracks=["m/1", "m/2", "m/1"]))
    assert cl.camera_sources(doc) == ["b", "a"]
    assert cl.mic_sources(doc) == ["m/2", "m/1"]
    assert doc["audio_tracks"] == ["m/1", "m/2"]


def test_draw_order_by_z_then_list_position():
    tiles = [{"id": "a", "z": 1}, {"id": "b", "z": 0}, {"id": "c", "z": 1}]
    assert [t["id"] for t in cl.draw_order(tiles)] == ["b", "a", "c"]


# --------------------------------------------------------------------------- #
# Geometry                                                                    #
# --------------------------------------------------------------------------- #


def test_fit_to_canvas_scales_and_centres():
    doc = cl.validate_layout({"canvas": {"width": 1920, "height": 540}, "tiles": [
        {"kind": "camera", "source": "a", "rect": [0, 0, 960, 540]},
        {"kind": "camera", "source": "b", "rect": [960, 0, 960, 540]},
    ]})
    out = cl.fit_to_canvas(doc, 1280, 720)
    assert out["canvas"]["width"] == 1280 and out["canvas"]["height"] == 720
    # 2/3 scale; 360 px tall content centred vertically.
    assert [t["rect"] for t in out["tiles"]] == [[0, 180, 640, 360], [640, 180, 640, 360]]
    cl.validate_layout(out)                    # still a valid document
    assert doc["canvas"]["width"] == 1920      # the input is untouched


def test_scaled_keeps_touching_tiles_touching():
    doc = cl.validate_layout({"canvas": {"width": 1920, "height": 1082}, "tiles": [
        {"kind": "camera", "source": "a", "rect": [0, 0, 641, 541]},
        {"kind": "camera", "source": "b", "rect": [641, 0, 1279, 541]},
        {"kind": "camera", "source": "c", "rect": [0, 541, 1920, 541]},
    ]})
    small, scale = cl.scaled(doc, 640)
    assert scale == pytest.approx(1 / 3)
    a, b, c = (t["rect"] for t in small["tiles"])
    assert a[0] + a[2] == b[0]
    assert a[1] + a[3] == c[1]
    assert b[0] + b[2] == small["canvas"]["width"]


def test_scaled_never_upscales():
    doc = cl.validate_layout(_doc())
    same, scale = cl.scaled(doc, 4000)
    assert scale == 1.0 and same == doc


def test_spectrogram_strip_sizes():
    import src.controller.audio_align as aa
    # Auto window: the legacy scroll speed.
    assert cl.spectrogram_strip(1920, None, 60) == aa._strip_pixels_per_second(60)
    # 10 s across 640 px over a 60 s render: 64 px/s.
    width, pps = cl.spectrogram_strip(640, 10, 60)
    assert (width, pps) == (3840, 64.0)
    assert cl.window_px(640, 10, pps) == 640
    # A long render is capped, and the tile then shows fewer strip px.
    width, pps = cl.spectrogram_strip(1920, 5, 3600)
    assert width == aa._MAX_STRIP_PX
    assert cl.window_px(1920, 5, pps) < 1920
    assert cl.window_px(1920, None, pps) == 1920


def test_hex_to_bgr():
    assert cl.hex_to_bgr("#102030") == (0x30, 0x20, 0x10)


# --------------------------------------------------------------------------- #
# Legacy requests -> layouts                                                  #
# --------------------------------------------------------------------------- #


def _streams(*dims):
    return [SessionStream(f"cam_{i}", f"v{i}.ts", f"c{i}.csv", w, h)
            for i, (w, h) in enumerate(dims)]


def _devs(*serials):
    return [{"id": f"mic/dev_{s}", "folder": "mic", "device": f"dev_{s}",
             "label": f"mic · {s}"} for s in serials]


@pytest.mark.parametrize("layout,dims", [
    ("auto", [(1280, 720)]),
    ("auto", [(1280, 720), (640, 480)]),
    ("auto", [(1280, 720)] * 3),
    ("side", [(1280, 720), (480, 640)]),
    ("stack", [(1280, 720), (1280, 720)]),
    ("grid", [(1280, 720)] * 4),
    ("loom", [(1280, 720)] * 3),
])
def test_legacy_cameras_land_where_plan_regions_put_them(layout, dims):
    """An old request renders through the layout path with the same
    placement it always had."""
    spec = ComposeSpec.from_dict({"session_name": "s", "layout": layout})
    doc = layout_from_legacy(spec, _streams(*dims), [])
    regions, cw, ch = plan_regions(dims, layout)
    assert (doc["canvas"]["width"], doc["canvas"]["height"]) == (cw, ch)
    assert [t["rect"] for t in doc["tiles"]] == [list(r) for r in regions]
    assert all(t["options"]["fit"] == "contain" and t["options"]["label"]
               for t in doc["tiles"])
    assert doc["audio_tracks"] == []


def test_legacy_strip_overlays_the_bottom_with_the_first_device():
    spec = ComposeSpec.from_dict({"session_name": "s", "audio": {
        "mode": "strip", "height_pct": 20, "spectrogram": {"color": "magma", "gain": 3}}})
    doc = layout_from_legacy(spec, _streams((1280, 720)), _devs("A", "B"))
    _, cw, ch = plan_regions([(1280, 720)], "auto")
    strip = doc["tiles"][-1]
    h = audio_height_px(ch, 20)
    assert strip["rect"] == [0, ch - h, cw, h] and strip["z"] == 1
    assert strip["source"] == "mic/dev_A" and strip["options"]["label"] is False
    assert strip["options"]["color"] == "magma" and strip["options"]["gain"] == 3
    assert strip["options"]["window_s"] is None
    assert doc["canvas"]["height"] == ch
    assert doc["audio_tracks"] == ["mic/dev_A"]


def test_legacy_panels_stack_under_the_video():
    spec = ComposeSpec.from_dict({"session_name": "s", "audio": {
        "mode": "panel", "height_pct": 100}})
    doc = layout_from_legacy(spec, _streams((1280, 720)), _devs("A", "B"))
    _, cw, ch = plan_regions([(1280, 720)], "auto")
    heights = panel_heights(audio_height_px(ch, 100), 2)
    panels = doc["tiles"][1:]
    assert [t["rect"] for t in panels] == [[0, ch, cw, heights[0]],
                                           [0, ch + heights[0], cw, heights[1]]]
    assert all(t["options"]["label"] for t in panels)
    assert doc["canvas"]["height"] == ch + sum(heights)
    assert doc["audio_tracks"] == ["mic/dev_A", "mic/dev_B"]


def test_legacy_single_panel_is_unlabelled_and_track_has_no_tiles():
    spec = ComposeSpec.from_dict({"session_name": "s", "audio": {"mode": "panel"}})
    doc = layout_from_legacy(spec, _streams((1280, 720)), _devs("A"))
    assert doc["tiles"][-1]["options"]["label"] is False
    spec = ComposeSpec.from_dict({"session_name": "s", "audio": {"mode": "track"}})
    doc = layout_from_legacy(spec, _streams((1280, 720)), _devs("A", "B"))
    assert [t["kind"] for t in doc["tiles"]] == ["camera"]
    assert doc["audio_tracks"] == ["mic/dev_A", "mic/dev_B"]


# --------------------------------------------------------------------------- #
# ComposeSpec with a layout document                                          #
# --------------------------------------------------------------------------- #


def test_spec_with_layout_doc_is_custom_and_normalised():
    spec = ComposeSpec.from_dict({
        "session_name": "s", "layout": "grid", "streams": ["x"],
        "audio": {"mode": "track"}, "layout_doc": _doc(),
    })
    assert spec.layout == "custom"
    assert spec.streams is None and spec.audio["mode"] == "none"
    assert spec.layout_doc["tiles"][0]["options"]["fit"] == "contain"
    # Round-trips the way a saved job is re-run.
    assert ComposeSpec(**spec.__dict__).layout_doc == spec.layout_doc


def test_spec_rejects_a_bad_layout_doc():
    with pytest.raises(ComposeError, match="outside the canvas"):
        ComposeSpec.from_dict({"session_name": "s", "layout_doc": _doc(tiles=[
            {"kind": "camera", "source": "a", "rect": [0, 0, 4000, 10]}])})


def test_old_saved_job_specs_still_load():
    old = {"session_name": "s", "date_dir": None, "streams": None, "layout": "auto",
           "fps": 15, "fmt": "mp4", "audio": {"mode": "none"}}
    assert ComposeSpec(**old).layout_doc is None


# --------------------------------------------------------------------------- #
# Templates                                                                   #
# --------------------------------------------------------------------------- #


def _session(tmp_path, monkeypatch, cams=("cam_a", "cam_b")):
    monkeypatch.setattr("src.controller.compose.probe_dimensions", lambda _p: (1280, 720))
    for cam in cams:
        d = tmp_path / "sess" / "20261014" / cam
        d.mkdir(parents=True)
        (d / "v.ts").write_bytes(b"x")
        (d / "v_timestamps.csv").write_text("timestamp_ns\n1\n2\n")
    return tmp_path


def test_template_natural_size(tmp_path, monkeypatch):
    share = _session(tmp_path, monkeypatch)
    doc = layout_template(str(share), "sess", None, "side")
    regions, cw, ch = plan_regions([(1280, 720)] * 2, "side")
    assert (doc["canvas"]["width"], doc["canvas"]["height"]) == (cw, ch)
    assert [t["source"] for t in doc["tiles"]] == ["cam_a", "cam_b"]


def test_template_fitted_to_a_canvas(tmp_path, monkeypatch):
    share = _session(tmp_path, monkeypatch)
    doc = layout_template(str(share), "sess", None, "stack", streams=["cam_b"],
                          canvas={"width": 1080, "height": 1080, "background": "#ffffff"})
    assert doc["canvas"] == {"width": 1080, "height": 1080, "background": "#ffffff"}
    (tile,) = doc["tiles"]
    assert tile["source"] == "cam_b"
    x, y, w, h = tile["rect"]
    assert x == 0 and w == 1080
    assert abs(y - (1080 - h) / 2) <= 1        # centred vertically


def test_template_without_a_mic_drops_the_audio(tmp_path, monkeypatch):
    share = _session(tmp_path, monkeypatch)
    doc = layout_template(str(share), "sess", None, "auto", audio_mode="panel")
    assert [t["kind"] for t in doc["tiles"]] == ["camera", "camera"]
