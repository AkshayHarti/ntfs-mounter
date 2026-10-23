import pytest

from ntfsmnt.web.config import cookie_matches, cookie_value, settings_from, with_port


def make(**kw):
    base = dict(port=0, owner=None, environ={}, euid=0, token="tok")
    base.update(kw)
    return settings_from(**base)


def test_root_with_sudo_owner_can_write():
    s = make(environ={"SUDO_UID": "501", "SUDO_GID": "20"})
    assert s.owner == (501, 20)
    assert s.read_only is False


def test_root_without_owner_is_read_only():
    s = make()
    assert s.owner is None
    assert s.read_only is True


def test_non_root_is_read_only_even_with_owner():
    s = make(environ={"SUDO_UID": "501", "SUDO_GID": "20"}, euid=501)
    assert s.read_only is True


def test_explicit_owner_wins_over_environment():
    s = make(owner="502:99", environ={"SUDO_UID": "501", "SUDO_GID": "20"})
    assert s.owner == (502, 99)


@pytest.mark.parametrize("bad", ["501", "501:", ":20", "a:b", "1:2:3"])
def test_bad_owner_is_rejected(bad):
    with pytest.raises(ValueError):
        make(owner=bad)


def test_with_port_fixes_loopback_hosts_and_origins():
    s = with_port(make(), 8765)
    assert s.port == 8765
    assert s.allowed_hosts == frozenset({"127.0.0.1:8765", "localhost:8765"})
    assert s.allowed_origins == frozenset(
        {"http://127.0.0.1:8765", "http://localhost:8765"}
    )


def test_tokens_are_random_when_not_supplied():
    a, b = make(token=None), make(token=None)
    assert a.token != b.token
    assert len(a.token) >= 43


def test_cookie_matches_only_its_token():
    cookie = cookie_value("tok")
    assert cookie != "tok"
    assert cookie_matches("tok", cookie)
    assert not cookie_matches("other", cookie)
    assert not cookie_matches("tok", None)


def test_non_ascii_presented_never_matches():
    assert not cookie_matches("tok", "ÿ")
    assert not cookie_matches("tok", "\udcff")
