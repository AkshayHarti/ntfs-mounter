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


def test_mcp_session_mints_an_agent_token_but_still_has_a_human_token():
    s = make(token=None, mcp_session=True)
    assert s.agent_token and s.token and s.agent_token != s.token
    assert len(s.agent_token) >= 43


def test_normal_mode_has_no_agent_token():
    assert make().agent_token is None


def test_mcp_session_with_explicit_token_keeps_it():
    s = make(token="tok", mcp_session=True)
    assert s.token == "tok"
    assert s.agent_token and s.agent_token != "tok"


def test_exit_on_stdin_eof_is_off_by_default():
    assert make().exit_on_stdin_eof is False
    assert make(exit_on_stdin_eof=True).exit_on_stdin_eof is True
