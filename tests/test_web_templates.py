import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from ntfsmnt.web.schemas import StateModel
from tests.web_render import render_detail, render_index


def state(label="HD1", *, state="ro", actions=None, options=None):
    return StateModel.model_validate({
        "generated_at": "2026-10-22T14:00:00Z",
        "host": {"root": True, "read_only": False, "owner": {"uid": 501, "gid": 20},
                 "fuse_t": True, "ntfs_3g": True},
        "doctor": {"status": "ok", "checked_at": "now", "findings": []},
        "operation": None,
        "volumes": [{
            "id": "UUID-1", "device": "/dev/disk4s1", "label": label,
            "uuid": "UUID-1", "size": 10, "fs": "ntfs", "state": state,
            "mount_point": "/Volumes/HD1", "driver": "fskit", "pid": None,
            "flags": {}, "actions": actions or [], "options": options or [],
        }],
    })


def test_hostile_label_is_escaped_everywhere():
    html = render_index(state('<img src=x onerror=alert(1)>'))
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html


def test_read_only_chip_is_text_not_colour_only():
    html = render_detail(state("HD1"))
    assert "read-only" in html


def test_disabled_action_renders_reason():
    html = render_detail(state(actions=[
        {"name": "mount", "enabled": False, "reason": "already writable"}]))
    assert "disabled" in html
    assert "already writable" in html


def test_guarded_option_has_confirm_trigger():
    html = render_detail(state(options=[
        {"name": "remove_hiberfile", "enabled": True, "guarded": True,
         "reason": "Windows hibernation state will be discarded."}]))
    assert "data-confirm-trigger" in html
    assert "Type HD1 to confirm" in html


def test_empty_state_explains_what_to_do():
    empty = state().model_copy(update={"volumes": []})
    html = render_index(empty)
    assert "No NTFS volumes" in html


def test_index_has_live_regions_and_script():
    html = render_index(state())
    assert 'aria-live="polite"' in html
    assert 'role="alert"' in html
    assert "/static/app.js" in html


def test_index_has_doctor_targets_without_oob_swap():
    html = render_index(state())
    assert html.count('id="doctor-status"') == 1
    assert html.count('id="doctor-banners"') == 1
    assert 'class="recheck" href="/"' in html
    assert "hx-swap-oob" not in html


def test_detail_oob_swap_carries_fresh_doctor():
    html = render_detail(state(), oob=True)
    assert 'id="doctor-status"' in html
    assert 'id="doctor-banners" hx-swap-oob="true"' in html
    assert html.count('hx-swap-oob="true"') == 2
    assert "doctor &check;" in html
    assert "Re-check" in html


def test_detail_without_oob_has_no_doctor_fragment():
    html = render_detail(state())
    assert 'id="doctor-status"' not in html
    assert "hx-swap-oob" not in html
