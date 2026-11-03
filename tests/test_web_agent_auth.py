import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from ntfsmnt.web.auth import require_agent, require_session
from ntfsmnt.web.config import cookie_value
from tests.web_helpers import make_settings


def client(*, agent_token="agent"):
    settings = make_settings(agent_token=agent_token)
    app = FastAPI()

    @app.get("/agent-only")
    def agent_only(request: Request):
        require_agent(settings, request)
        return {"ok": True}

    @app.get("/cookie-only")
    def cookie_only(request: Request):
        require_session(settings, request)
        return {"ok": True}

    return TestClient(app, base_url="http://testserver"), settings


def test_agent_token_in_bearer_header_is_accepted():
    c, _ = client()
    r = c.get("/agent-only", headers={"Authorization": "Bearer agent"})
    assert r.status_code == 200


def test_missing_header_is_401():
    c, _ = client()
    assert c.get("/agent-only").status_code == 401


def test_wrong_scheme_is_401():
    c, _ = client()
    r = c.get("/agent-only", headers={"Authorization": "Basic agent"})
    assert r.status_code == 401


def test_empty_bearer_is_401():
    c, _ = client()
    r = c.get("/agent-only", headers={"Authorization": "Bearer "})
    assert r.status_code == 401


def test_human_token_is_not_an_agent_token():
    c, settings = client()
    r = c.get("/agent-only", headers={"Authorization": f"Bearer {settings.token}"})
    assert r.status_code == 401


def test_agent_token_is_not_a_session_cookie():
    c, _ = client()
    c.cookies.set("ntfsmnt_session", cookie_value("agent"))
    assert c.get("/cookie-only").status_code == 401


def test_no_agent_token_means_no_request_is_accepted():
    c, _ = client(agent_token=None)
    assert c.get("/agent-only", headers={"Authorization": "Bearer "}).status_code == 401
