import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("jinja2")

from ntfsmnt.web.schemas import ActivityEntryModel, StateModel
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


def _filter_buttons(html):
    return re.findall(
        r'<button[^>]*data-activity-filter="[^"]+"[^>]*>', html, re.S
    )


def test_activity_filter_buttons_and_row_volumes():
    html = render_detail(state("HD1"))
    assert 'data-activity-for="HD1"' in html

    buttons = _filter_buttons(html)
    assert len(buttons) == 2

    volume = next(b for b in buttons if 'data-activity-filter="volume"' in b)
    every = next(b for b in buttons if 'data-activity-filter="all"' in b)
    assert 'aria-pressed="true"' in volume
    assert "is-active" in volume
    assert 'aria-pressed="false"' in every
    assert "is-active" not in every
    assert ">This volume</button>" in html
    assert ">All</button>" in html
    assert "data-activity-filter-empty" in html


def test_activity_rows_carry_their_volume_label():
    activity = (
        ActivityEntryModel.model_validate({
            "id": 2, "at": "2026-10-22T14:00:01Z", "action": "mount",
            "volume": "HD2", "ok": False, "summary": "Volume is hibernated.",
            "detail": "ntfs-3g: hibernated"},),
        ActivityEntryModel.model_validate({
            "id": 1, "at": "2026-10-22T14:00:00Z", "action": "unmount",
            "volume": "HD1", "ok": True, "summary": "Unmounted /Volumes/HD1.",
            "detail": None},),
    )
    html = render_detail(state("HD1"), activity=activity)
    assert html.count("data-activity-volume=") == 2
    assert 'data-activity-volume="HD2"' in html
    assert 'data-activity-volume="HD1"' in html
    assert 'class="log__row is-failure"' in html


def test_busy_notice_renders_operation_text():
    html = render_detail(
        state("HD1"),
        operation={
            "id": "op-1", "action": "mount", "volume": "HD1",
            "started_at": "2026-10-22T14:00:00Z"},
    )
    assert "data-operation-notice" in html
    assert "data-operation-detail" in html
    assert ": mount HD1</span>" in html
    # Visible while busy: the notice is rendered without the hidden attribute.
    notice = html.split("data-operation-notice", 1)[1].split(">", 1)[0]
    assert "hidden" not in notice


def test_busy_notice_is_present_but_hidden_when_idle():
    html = render_detail(state("HD1"))
    assert "data-operation-notice" in html
    notice = html.split("data-operation-notice", 1)[1].split(">", 1)[0]
    assert "hidden" in notice
    assert ": mount" not in html


def test_remove_hiberfile_trigger_has_one_data_label():
    html = render_detail(state(options=[
        {"name": "remove_hiberfile", "enabled": True, "guarded": True,
         "reason": "Windows hibernation state will be discarded."}]))
    # The trigger carries none: the confirm form is the sole data-label holder.
    assert html.count("data-label=") == 1
    assert html.split("data-confirm-trigger", 1)[1].split(">", 1)[0].find(
        "data-label") == -1


def test_index_activity_live_region_stays_polite():
    html = render_index(state())
    assert 'id="activity-live"' in html
    assert 'aria-live="polite"' in html
    assert 'class="sr-only"' in html
