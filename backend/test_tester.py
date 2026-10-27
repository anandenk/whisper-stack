"""The tester must never expose credentials or contact providers on page load."""
from fastapi.testclient import TestClient
import httpx
import pytest

from backend.app import Settings, create_app


@pytest.fixture
def client(tmp_path):
    def unexpected(request):
        pytest.fail("Serving the tester must not call any provider")
    with TestClient(create_app(
        Settings(api_key="fake-secret", anthropic_api_key="sk-ant-fake-secret"),
        transport=httpx.MockTransport(unexpected), anthropic_transport=httpx.MockTransport(unexpected),
        diagnostics_path=tmp_path / "diagnostics.sqlite3",
    )) as test_client:
        yield test_client


@pytest.mark.parametrize("path,media_type", [
    ("/tester", "text/html"), ("/tester/index.html", "text/html"),
    ("/tester/tester.css", "text/css"), ("/tester/tester.js", "text/javascript"),
])
def test_page_assets_are_local_and_no_store(client, path, media_type):
    response = client.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(media_type)
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "microphone=(self)" in response.headers["permissions-policy"]
    assert "fake-secret" not in response.text


@pytest.mark.parametrize("asset", [".env", "app.py", "output/diagnostics.sqlite3", "%2e%2e%2f.env"])
def test_assets_do_not_expose_backend_files(client, asset):
    assert client.get("/tester/" + asset).status_code == 404
