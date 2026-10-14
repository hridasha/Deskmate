"""Security: SSRF guard, locked-by-default admin actions, per-IP rate limits."""
import pytest
from fastapi.testclient import TestClient

import agent
import dbsource
import main
import safe_url

# ---------- SSRF guard ----------


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/admin",
        "http://localhost/secret",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/router",
        "http://172.16.0.9/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata service
        "http://[::1]/",
        "http://0.0.0.0/",
        "http://[::ffff:127.0.0.1]/",  # IPv4 hidden inside IPv6
    ],
)
def test_internal_addresses_are_blocked(url):
    with pytest.raises(safe_url.BlockedHost):
        safe_url.check_url(url)


@pytest.mark.parametrize("url", ["ftp://example.com/file", "file:///etc/passwd", "gopher://x/", "javascript:alert(1)", ""])
def test_only_http_and_https_are_allowed(url):
    with pytest.raises(safe_url.BlockedHost):
        safe_url.check_url(url)


def test_public_addresses_pass():
    safe_url.check_url("https://8.8.8.8/")  # a public IP literal needs no DNS lookup


def test_private_hosts_can_be_allowed_for_local_development(monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_HOSTS", "true")
    safe_url.check_url("http://127.0.0.1:5432/")


@pytest.mark.asyncio
async def test_the_fetch_tool_refuses_internal_urls(monkeypatch):
    import webfetch

    assert (await webfetch.fetch("http://169.254.169.254/latest/meta-data/")).startswith("Blocked:")
    assert (await webfetch.fetch("http://localhost:8000/")).startswith("Blocked:")
    assert (await webfetch.fetch("file:///etc/passwd")).startswith("Blocked:")


@pytest.mark.asyncio
async def test_dns_rebinding_is_stopped_at_connect_time(monkeypatch):
    """The name looks public when checked, then resolves to an internal address when the connection is made."""
    import socket

    import webfetch

    answers = iter([[(2, 1, 6, "", ("8.8.8.8", 0))]] + [[(2, 1, 6, "", ("127.0.0.1", 0))]] * 5)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: next(answers))
    out = await webfetch.fetch("http://rebind.example.test/")
    assert out.startswith("Blocked:") and "internal address" in out


def test_a_database_host_is_pinned_to_the_address_that_was_checked(monkeypatch):
    import socket

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("8.8.4.4", 0))])
    assert safe_url.pin_host("db.example.test") == "8.8.4.4"


def test_html_is_turned_into_readable_text():
    import webfetch

    out = webfetch.to_text("<html><head><title>Hi</title><script>evil()</script></head><body><nav>menu</nav><p>Hello <b>world</b></p></body></html>", "text/html")
    assert "Hello" in out and "world" in out and "evil" not in out and "menu" not in out and out.startswith("# Hi")


def test_databases_on_internal_hosts_are_refused_before_connecting(clean_db):
    with pytest.raises(dbsource.UnsafeQuery) as e:
        dbsource.add_url_source("u", "internal", "postgresql://user:pw@10.0.0.5:5432/app")
    assert "internal address" in str(e.value)


# ---------- admin lock ----------


@pytest.fixture()
def client(clean_db, monkeypatch):
    monkeypatch.setattr(agent, "_mcp_tools", [])
    main._recent.clear()
    with TestClient(main.app) as c:
        yield c


def test_admin_actions_are_locked_by_default_in_production(client, monkeypatch):
    monkeypatch.setenv("CASTCALL_ENV", "prod")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    r = client.get("/settings/keys")
    assert r.status_code == 401 and "ADMIN_TOKEN" in r.json()["detail"]
    assert client.post("/knowledge/databases", json={"name": "x", "url": "postgresql://a:b@host/db"}).status_code == 401


def test_a_token_unlocks_admin_actions_in_production(client, monkeypatch):
    monkeypatch.setenv("CASTCALL_ENV", "prod")
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    assert client.get("/settings/keys", headers={"X-Admin-Token": "wrong"}).status_code == 401
    assert client.get("/settings/keys", headers={"X-Admin-Token": "s3cret"}).status_code == 200


def test_dev_mode_leaves_admin_open_for_local_use(client, monkeypatch):
    monkeypatch.setenv("CASTCALL_ENV", "dev")
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert client.get("/settings/keys").status_code == 200


# ---------- rate limits ----------


def test_one_network_cannot_dodge_limits_by_inventing_user_ids(client, fake_llm, monkeypatch):
    from langchain_core.messages import AIMessage

    fake_llm([AIMessage(content="ok [emotion: neutral]")] * 20)
    monkeypatch.setattr(main, "IP_MINUTE_LIMIT", 3)
    codes = [client.post("/chat", headers={"X-User-Id": f"fresh-user-{i:04d}"}, json={"character_id": "jim_halpert", "message": "hi"}).status_code for i in range(5)]
    assert codes == [200, 200, 200, 429, 429]
