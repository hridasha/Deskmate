"""Source citations, original-file links and keeping documents in sync."""
import json

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import agent
import db
import docsync
import knowledge
import main
import memory

from tests.test_rag import make_docx, tool_call  # noqa: E402

USER = {"X-User-Id": "cite-user-0001"}
OTHER = {"X-User-Id": "cite-user-0002"}


@pytest.fixture(autouse=True)
def fresh(clean_db, monkeypatch):
    knowledge._cache.clear()
    knowledge._version.clear()

    async def no_embed(texts):
        return [None] * len(texts)

    async def no_query(text):
        return None

    monkeypatch.setattr(knowledge, "embed_many", no_embed)
    monkeypatch.setattr(memory, "embed", no_query)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(agent, "_mcp_tools", [])
    main._recent.clear()
    with TestClient(main.app) as c:
        yield c


def events(resp) -> list[dict]:
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


# ---------- citations ----------


def test_the_answer_comes_with_the_passages_it_used(client, fake_llm):
    doc = client.post("/knowledge/upload", headers=USER, files={"file": ("handbook.docx", make_docx(), "application/octet-stream")}).json()
    fake_llm([tool_call("search_knowledge", query="parking permit cost"), AIMessage(content="Permits cost $45 per month [1]. [emotion: happy]")])
    evs = events(client.post("/chat", headers=USER, json={"character_id": "jim_halpert", "message": "How much is parking? Check the documents."}))
    src = next(e for e in evs if e["state"] == "sources")["items"]
    assert src[0]["n"] == 1 and src[0]["doc"] == "handbook.docx" and src[0]["doc_id"] == doc["id"] and "$45" in src[0]["text"]
    assert [e["state"] for e in evs].index("sources") < [e["state"] for e in evs].index("speaking")  # sources arrive with the answer


def test_only_the_cited_numbers_are_returned():
    sources = [{"n": 1}, {"n": 2}, {"n": 3}]
    assert agent.cited("It is X [2].", sources) == [{"n": 2}]
    assert agent.cited("No numbers here", sources) == sources  # nothing cited: show what was consulted
    assert agent.cited("Arrays like a[9] aren't sources [1]", sources) == [{"n": 1}]


def test_no_documents_means_no_sources_event(client, fake_llm):
    fake_llm([AIMessage(content="Hello! [emotion: happy]")])
    assert not [e for e in events(client.post("/chat", headers=USER, json={"character_id": "jim_halpert", "message": "hi"})) if e["state"] == "sources"]


# ---------- the original file ----------


def test_the_original_file_can_be_opened_by_its_owner_only(client):
    data = make_docx()
    doc = client.post("/knowledge/upload", headers=USER, files={"file": ("handbook.docx", data, "application/octet-stream")}).json()
    r = client.get(f"/knowledge/{doc['id']}/file", headers=USER)
    assert r.status_code == 200 and r.content == data
    assert client.get(f"/knowledge/{doc['id']}/file", headers=OTHER).status_code == 404
    client.delete(f"/knowledge/{doc['id']}", headers=USER)
    assert client.get(f"/knowledge/{doc['id']}/file", headers=USER).status_code == 404
    assert not list(knowledge.FILES_DIR.glob(f"{doc['id']}.*"))  # and the file is removed from disk


# ---------- updating and syncing ----------


@pytest.mark.asyncio
async def test_a_changed_file_replaces_the_old_one_and_an_identical_one_is_skipped():
    a = await knowledge.ingest("u1", "policy.txt", b"Leave is 20 days per year for everyone in the company.", scope=["jim_halpert"])
    same = await knowledge.ingest("u1", "policy.txt", b"Leave is 20 days per year for everyone in the company.")
    assert same["id"] == a["id"] and same["unchanged"] is True
    b = await knowledge.ingest("u1", "policy.txt", b"Leave is 25 days per year, and one more day for long service.")
    assert len(knowledge.list_docs("u1")) == 1 and b["id"] != a["id"]  # same name, new contents: replaced, not duplicated
    assert b["scope"] == ["jim_halpert"]  # and who may see it is kept
    hits = await knowledge.search("u1", "jim_halpert", "leave days")
    assert "25 days" in hits[0]["text"] and all("20 days" not in h["text"] for h in hits)


@pytest.mark.asyncio
async def test_a_url_document_is_refetched_and_updated_only_when_it_changed(monkeypatch):
    pages = {"v": b"<html><body><h1>Prices</h1><p>The basic plan costs 10 dollars a month.</p></body></html>"}

    async def fake_fetch(url):
        return pages["v"], "text/html"

    monkeypatch.setattr(docsync, "fetch_bytes", fake_fetch)
    doc = await docsync.ingest_url("u1", "https://example.com/pricing")
    assert doc["origin"] == {"type": "url", "url": "https://example.com/pricing"} and doc["name"].endswith(".html")
    assert (await docsync.sync_doc("u1", doc["id"]))["status"] == "unchanged"
    pages["v"] = b"<html><body><h1>Prices</h1><p>The basic plan costs 12 dollars a month.</p></body></html>"
    res = await docsync.sync_doc("u1", doc["id"])
    assert res["status"] == "updated" and len(knowledge.list_docs("u1")) == 1
    assert "12 dollars" in (await knowledge.search("u1", "x", "basic plan cost"))[0]["text"]


@pytest.mark.asyncio
async def test_uploaded_files_cannot_be_refetched():
    doc = await knowledge.ingest("u1", "n.txt", b"Some notes about the quarterly offsite plan and budget.")
    assert (await docsync.sync_doc("u1", doc["id"]))["status"] == "skipped"


@pytest.mark.asyncio
async def test_a_watched_folder_is_mirrored(tmp_path):
    (tmp_path / "a.txt").write_text("Alpha document says the warehouse opens at 7am sharp every day.")
    (tmp_path / "b.md").write_text("# Beta\nBeta document says the dock closes at 6pm on weekdays.")
    r1 = await docsync.sync_folder("u1", tmp_path)
    assert r1 == {"added": 2, "updated": 0, "removed": 0, "failed": 0}
    assert await docsync.sync_folder("u1", tmp_path) == {"added": 0, "updated": 0, "removed": 0, "failed": 0}  # nothing changed
    (tmp_path / "a.txt").write_text("Alpha document says the warehouse opens at 8am now, after the schedule change.")
    (tmp_path / "b.md").unlink()
    r2 = await docsync.sync_folder("u1", tmp_path)
    assert r2 == {"added": 0, "updated": 1, "removed": 1, "failed": 0}
    assert [d["name"] for d in knowledge.list_docs("u1")] == ["a.txt"]


def test_urls_to_internal_addresses_are_refused(client):
    r = client.post("/knowledge/url", headers=USER, json={"url": "http://169.254.169.254/latest/meta-data/"})
    assert r.status_code == 400 and "internal" in r.json()["detail"] or "isn't allowed" in r.json()["detail"]
