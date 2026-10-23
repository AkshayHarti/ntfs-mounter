from ntfsmnt.models import Device
from ntfsmnt.volumes import Volume
from ntfsmnt.web.config import Settings


def make_device(identifier="disk4s1", *, label="HD1", uuid="UUID-1",
                size=1_000_000_000, mount_point=None):
    return Device(
        identifier=identifier, device=f"/dev/{identifier}", label=label,
        uuid=uuid, size=size, fs_type="Windows_NTFS", mount_point=mount_point,
    )


def make_volume(*, mounts=(), **kw):
    return Volume(device=make_device(**kw), mounts=tuple(mounts))


def make_settings(*, token="tok", root=True, owner=(501, 20), read_only=False,
                  allowed_hosts=frozenset({"testserver"}),
                  allowed_origins=frozenset({"http://testserver"})):
    return Settings(
        token=token, port=0, owner=owner, root=root, read_only=read_only,
        allowed_hosts=allowed_hosts, allowed_origins=allowed_origins,
    )


class FakeRun:
    def __init__(self, pid="4242"):
        self.pid = pid
        self.calls = []

    def __call__(self, argv):
        self.calls.append(argv)
        if argv[0] == "pgrep":
            return (0, f"{self.pid}\n") if self.pid else (1, "")
        raise AssertionError(f"unexpected command: {argv}")
