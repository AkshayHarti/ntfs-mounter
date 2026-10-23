import json

from ntfsmnt.agent.planner import Plan, detect_intent, plan_for
from ntfsmnt.agent.openui import parse_program, validate
from ntfsmnt.agent.state import agent_state
from ntfsmnt.web.models import (DoctorStatus, HostView, OwnerView, Snapshot,
                                VolumeView)
from ntfsmnt.web.models import ActionView, OptionView


def volume(label="HD1", *, node="disk4s1", uuid=None, state="unmounted",
           actions=("mount", "eject"), size=1_000_000_000, mount_point=None):
    return VolumeView(
        id=uuid or f"UUID-{label}", device=f"/dev/{node}", label=label, uuid=uuid,
        size=size, fs="ntfs", state=state, mount_point=mount_point,
        driver=None, pid=None, flags={},
        actions=tuple(ActionView(name=action, enabled=action in actions)
                      for action in ("mount", "unmount", "eject")),
        options=(OptionView(name="rw-default", enabled=True),),
    )


def snapshot(*volumes, findings=(), status="ok"):
    return Snapshot(
        generated_at="2026-10-24T00:00:00Z",
        host=HostView(root=True, read_only=False, owner=OwnerView(501, 20),
                      fuse_t=True, ntfs_3g=True),
        doctor=DoctorStatus(status=status, checked_at="2026-10-24T00:00:00Z",
                            findings=findings),
        operation=None,
        volumes=volumes,
    )


def test_detect_intent_words() -> None:
    assert detect_intent("unmount HD1") == "unmount"
    assert detect_intent("mount ELEMENTS read-write") == "mount"
    assert detect_intent("check my health") == "doctor"
    assert detect_intent("eject the disk") == "eject"
    assert detect_intent("what can you do?") == "help"
    assert detect_intent("explain quantum") == "overview"


def test_overview_program_is_valid_lang() -> None:
    snap = snapshot(volume("HD1"), volume("ELEMENTS", node="disk5s1",
                                          state="rw", actions=("unmount",)))
    plan = plan_for("show me the volumes", snap)
    program = parse_program(plan.program)
    assert validate(program) == []
    assert plan.generator == "deterministic"
    assert ":" not in plan.program  # no key/value noise — statements only


def test_mount_intent_proposes_never_executes() -> None:
    snap = snapshot(volume("HD1"))
    plan = plan_for("mount HD1", snap)
    assert "confirm" in plan.narration.lower() or "ready" in plan.narration.lower()
    assert '@Mount("UUID-HD1")' in plan.program
    program = parse_program(plan.program)
    assert validate(program) == []


def test_disabled_action_gets_reason() -> None:
    snap = snapshot(volume("HD1", state="rw", actions=("unmount",)))
    plan = plan_for("mount HD1", snap)
    assert "cannot be mounted" in plan.narration
    assert "cannot" in plan.narration


def test_ambiguity_asks() -> None:
    snap = snapshot(volume("HD1"), volume("HD2", node="disk5s1"))
    plan = plan_for("mount", snap)
    assert "Which volume should I mount" in plan.narration
    assert "HD1" in plan.program and "HD2" in plan.program

    snap = snapshot(volume("HD1"), volume("HD2", node="disk5s1"))
    plan = plan_for("mount hd1 and hd2", snap)
    assert "2 volumes match" in plan.narration


def test_single_volume_means_that_one() -> None:
    snap = snapshot(volume("HD1"))
    plan = plan_for("mount", snap)
    assert '@Mount("UUID-HD1")' in plan.program


def test_doctor_intent_lists_findings() -> None:
    from ntfsmnt.web.models import Finding
    snap = snapshot(volume("HD1"), findings=(
        Finding(severity="blocker", check="ntfs-3g", message="missing",
                fix="run setup.sh"),
    ))
    plan = plan_for("doctor", snap)
    assert "ntfs-3g" in plan.program
    program = parse_program(plan.program)
    assert validate(program) == []


def test_help_mentions_confirmation() -> None:
    plan = plan_for("help", snapshot())
    assert "never run a disk operation" in plan.program


def test_agent_state_has_no_secrets_and_is_jsonable() -> None:
    snap = snapshot(volume("HD1"))
    state = agent_state(snap)
    text = json.dumps(state)
    assert "token" not in text
    assert state["volumes"][0]["id"] == "UUID-HD1"


def test_llm_fallback_when_unreachable(capsys) -> None:
    from ntfsmnt.agent.llm import LLMConfig
    snap = snapshot(volume("HD1"))
    plan = plan_for("mount HD1", snap,
                    llm=LLMConfig(base_url="http://127.0.0.1:1", model="m"))
    assert plan.generator == "deterministic"
    assert plan.fallback is True
    assert "unreachable" in plan.note


def test_llm_rejected_program_falls_back(monkeypatch) -> None:
    from ntfsmnt.agent import planner as module
    snap = snapshot(volume("HD1"))
    monkeypatch.setattr(module, "completion", lambda *a, **k: 'root = Widget("x")\n')
    plan = module.plan_for("mount HD1", snap, llm=module.LLMConfig("http://x", "m"))
    assert plan.fallback is True and plan.generator == "deterministic"


def test_llm_valid_program_is_used(monkeypatch) -> None:
    from ntfsmnt.agent import planner as module
    snap = snapshot(volume("HD1"))
    program = (
        'root = Root([mark])\n'
        'mark = Banner("mounted!", "info")\n'
        '@Mount("UUID-HD1")\n'  # ignored: statements require name = expr
    )
    valid = 'root = Root([mark])\nmark = Banner("mounted!", "info")\n'
    monkeypatch.setattr(module, "completion", lambda *a, **k: valid)
    plan = module.plan_for("mount HD1", snap, llm=module.LLMConfig("http://x", "m"))
    assert plan.generator == "llm" and plan.model == "m"
    program = parse_program(plan.program)
    assert validate(program) == []


def test_plan_is_a_plan() -> None:
    assert Plan(intent="x", narration="n", program="root = Root([])").intent == "x"
