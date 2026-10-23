"""Salt the live snapshot into the JSON state the agent and model see.

Nothing here is privileged: labels, devices, sizes, states, and whatever the
service already declines to show are the only data. The session token never
appears, because it is never part of a snapshot.
"""

from __future__ import annotations

from ntfsmnt.web.models import Snapshot


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if value < 1000 or unit == "PB":
            if unit == "B":
                return f"{value:g} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1000
    return f"{value:.1f} PB"


_STATES = {"rw": "Read-write", "ro": "Read-only (Apple)", "unmounted": "Unmounted"}


def agent_state(snapshot: Snapshot) -> dict:
    volumes = []
    for volume in snapshot.volumes:
        volumes.append({
            "id": volume.id,
            "label": volume.label,
            "device": volume.device,
            "state": volume.state,
            "stateLabel": _STATES.get(volume.state, volume.state),
            "mountPoint": volume.mount_point,
            "driver": volume.driver,
            "size": volume.size,
            "sizeLabel": human_size(volume.size),
            "actions": {a.name: {"enabled": a.enabled, "reason": a.reason}
                        for a in volume.actions},
            "options": {o.name: {"enabled": o.enabled, "guarded": o.guarded,
                                 "reason": o.reason}
                        for o in volume.options},
        })
    host = snapshot.host
    return {
        "generatedAt": snapshot.generated_at,
        "host": {"root": host.root, "readOnly": host.read_only},
        "doctor": {
            "status": snapshot.doctor.status,
            "checkedAt": snapshot.doctor.checked_at,
            "findings": [
                {"severity": f.severity, "check": f.check,
                 "message": f.message, "fix": f.fix}
                for f in snapshot.doctor.findings
            ],
        },
        "operation": ({"action": snapshot.operation.action,
                       "volume": snapshot.operation.volume,
                       "startedAt": snapshot.operation.started_at}
                      if snapshot.operation else None),
        "volumes": volumes,
    }
