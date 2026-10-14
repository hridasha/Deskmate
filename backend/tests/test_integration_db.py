"""Run against REAL Postgres and MySQL servers (skipped unless they're reachable).

  docker compose --profile test up -d pg-test mysql-test
  docker compose exec -T backend python -m pytest -m integration -q
"""
import os

import pytest
from sqlalchemy import create_engine, text

import dbsource

pytestmark = pytest.mark.integration

URLS = {
    "postgresql": os.getenv("TEST_PG_URL", "postgresql://castcall:testpass@pg-test:5432/shop"),
    "mysql": os.getenv("TEST_MYSQL_URL", "mysql://root:testpass@mysql-test:3306/shop"),
}


def _admin_engine(url: str):
    return create_engine(dbsource._normalize(url))


@pytest.fixture(params=list(URLS))
def server(request, monkeypatch):
    monkeypatch.setenv("ALLOW_PRIVATE_HOSTS", "true")  # the test servers live on the Docker network
    url = URLS[request.param]
    eng = _admin_engine(url)
    try:
        with eng.begin() as c:
            c.execute(text("DROP TABLE IF EXISTS orders"))
            c.execute(text("CREATE TABLE orders (id INT PRIMARY KEY, customer VARCHAR(40), total INT)"))
            for i, (cust, tot) in enumerate([("Dunder", 40), ("Staples", 15), ("Lackawanna", 90)], 1):
                c.execute(text("INSERT INTO orders VALUES (:i, :c, :t)"), {"i": i, "c": cust, "t": tot})
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"{request.param} test server not reachable: {type(e).__name__}")
    yield request.param, url
    eng.dispose()


def test_connect_read_schema_and_query(clean_db, server):
    kind, url = server
    src = dbsource.add_url_source("u1", "shop", url)
    assert src["kind"] == kind and "orders" in src["schema"] and "customer" in src["schema"]


@pytest.mark.asyncio
async def test_select_works_and_writes_are_refused(clean_db, server):
    _, url = server
    dbsource.add_url_source("u1", "shop", url)
    out = await dbsource.query("u1", "a", "shop", "SELECT customer, total FROM orders WHERE total > 20 ORDER BY total")
    assert "Dunder" in out and "Lackawanna" in out and "Staples" not in out
    assert (await dbsource.query("u1", "a", "shop", "DELETE FROM orders")).startswith("Query refused")
    assert "3" in await dbsource.query("u1", "a", "shop", "SELECT COUNT(*) AS n FROM orders")


def test_the_server_itself_rejects_writes_even_if_the_validator_were_bypassed(clean_db, server):
    """Defence in depth: the connection is read-only at the database level, not just by our SQL checks."""
    _, url = server
    src = dbsource.add_url_source("u1", "shop", url)
    row = {"id": src["id"], "url": None}
    engine = dbsource._engines[src["id"]]
    with pytest.raises(Exception) as e:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM orders"))
    assert "read" in str(e.value).lower()  # "read-only transaction"
    with _admin_engine(url).connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM orders")).scalar() == 3
    assert row


@pytest.mark.asyncio
async def test_a_slow_query_is_stopped(clean_db, server, monkeypatch):
    kind, url = server
    monkeypatch.setattr(dbsource, "TIMEOUT_S", 1)
    dbsource._engines.clear()
    dbsource.add_url_source("u1", "shop", url)
    sleep = "SELECT pg_sleep(5)" if kind == "postgresql" else "SELECT SLEEP(5)"
    out = await dbsource.query("u1", "a", "shop", sleep)
    assert out.startswith(("Query failed", "The query took too long", "Query refused"))
