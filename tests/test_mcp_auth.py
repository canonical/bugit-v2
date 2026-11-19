import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from bugit_v2.mcp_server.auth import BearerAuthMiddleware

KEY = "s3cret-key"


def make_client() -> TestClient:
    async def ok(_request):
        return PlainTextResponse("hello")

    app = Starlette(routes=[Route("/sse", ok)])
    return TestClient(BearerAuthMiddleware(app, api_key=KEY))


def test_missing_header_is_401():
    r = make_client().get("/sse")
    assert r.status_code == 401
    assert r.headers["www-authenticate"].lower().startswith("bearer")


@pytest.mark.parametrize(
    "header",
    ["Bearer wrong", "Bearer ", "Basic s3cret-key", "s3cret-key", f"Bearer {KEY}x"],
)
def test_bad_credentials_are_401(header: str):
    r = make_client().get("/sse", headers={"Authorization": header})
    assert r.status_code == 401
    assert "hello" not in r.text


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER"])
def test_valid_key_passes(scheme: str):
    r = make_client().get("/sse", headers={"Authorization": f"{scheme} {KEY}"})
    assert r.status_code == 200
    assert r.text == "hello"


@pytest.mark.parametrize("key", ["", "   ", None])
def test_refuses_empty_key(key):
    with pytest.raises(ValueError):
        BearerAuthMiddleware(Starlette(), api_key=key)
