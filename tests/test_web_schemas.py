import pytest

pytest.importorskip("pydantic")

from ntfsmnt.web.models import (ActionError, ActionResult, DoctorStatus, HostView,
                                OwnerView, Snapshot, VolumeView)
from ntfsmnt.web.schemas import error_response, result_model, state_model


def snapshot():
    return Snapshot(
        generated_at="2026-10-22T14:00:00Z",
        host=HostView(True, False, OwnerView(501, 20), True, True),
        doctor=DoctorStatus("ok", "2026-10-22T14:00:00Z", ()),
        operation=None,
        volumes=(VolumeView(
            id="UUID-1", device="/dev/disk4s1", label="HD1", uuid="UUID-1",
            size=10, fs="ntfs", state="ro", mount_point="/Volumes/HD1",
            driver="fskit", pid=None),),
    )


def test_state_round_trips_the_snapshot():
    data = state_model(snapshot()).model_dump(mode="json")
    assert data["volumes"][0]["id"] == "UUID-1"
    assert data["host"]["owner"] == {"uid": 501, "gid": 20}
    assert data["operation"] is None


def test_error_response_keeps_next_step():
    error = ActionError("busy", "HD1 is in use.", 409, "Unmount it first.", "detail", "HD1")
    data = error_response(error).model_dump(exclude_none=True)
    assert data == {"error": {
        "code": "busy", "message": "HD1 is in use.", "next_step": "Unmount it first.",
        "detail": "detail", "volume": "HD1",
    }}


def test_result_model_includes_volume_and_error():
    error = ActionError("busy", "HD1 is in use.", 409)
    result = ActionResult(False, "HD1 is in use.", None, None, error)
    data = result_model(result).model_dump(mode="json", exclude_none=True)
    assert data["result"] == {"ok": False, "message": "HD1 is in use."}
    assert data["error"]["code"] == "busy"
