"""Chunking strategies, the auto decision, re-chunking and previews."""
import hashlib

import pytest
from fastapi.testclient import TestClient

import agent
import chunking
import embeddings
import knowledge
import main
import memory
from router import tokens

USER = {"X-User-Id": "chunk-user-0001"}
Seg = knowledge.Segment


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


def fake_vec(text: str) -> list[float]:
    v = [0.0] * 64
    for t in tokens(text):
        v[int(hashlib.md5(t.encode()).hexdigest(), 16) % 64] += 1.0
    return v


HANDBOOK = """# Employee Handbook

## Parking
Parking permits cost 45 euros per month for employees. Visitors park free for the first two hours.

## Wifi
The office wifi password rotates every Thursday. Guests use the separate guest network.

## Printing
The color printer is on the second floor. Each employee gets 200 free color pages per month.
"""


# ---------- the building blocks ----------


def test_fixed_chunks_respect_size_and_overlap():
    text = " ".join(f"Sentence number {i} is right here." for i in range(200))
    parts = chunking.split_fixed(text, 400, 80)
    assert len(parts) > 5 and all(len(p) <= 405 for p in parts)
    assert parts[0][-25:].strip()[:12] in parts[1]  # overlap


def test_sentence_chunks_never_cut_a_sentence():
    text = " ".join(f"This is sentence number {i} of the document." for i in range(80))
    parts = chunking.split_sentences(text, 300, 60)
    assert len(parts) > 3 and all(p.endswith("document.") for p in parts)


def test_paragraph_chunks_keep_paragraphs_whole_and_merge_short_ones():
    text = "Short one.\n\nAnother short.\n\n" + ("Long paragraph sentence here. " * 60).strip() + "\n\nEnd."
    parts = chunking.split_paragraphs(text, 500, 60)
    assert parts[0].startswith("Short one.\n\nAnother short.")  # merged
    assert all(len(p) < 800 for p in parts)


@pytest.mark.parametrize(
    "text,count",
    [
        ("# One\nbody\n\n## Two\nmore", 2),
        ("1. Introduction\nWe begin here.\n\n2. Scope\nThis covers things.", 2),
        ("PARKING RULES\n\nPermits are required.\n\nWIFI ACCESS\n\nThe password rotates.", 2),
        ("Just a normal paragraph of prose that goes on and on without any headings at all, ending with a period.", 0),
    ],
)
def test_heading_detection(text, count):
    assert chunking.count_headings(text) == count


# ---------- strategies on whole documents ----------


@pytest.mark.asyncio
async def test_structure_chunks_start_with_their_heading_and_do_not_mix_sections():
    segs = [Seg(HANDBOOK, "text")]
    chunks, used = await chunking.build_chunks(segs, chunking.Config("structure", 900, 100), "md")
    assert used["strategy"] == "structure" and len(chunks) == 3
    parking = next(c for c in chunks if "45 euros" in c[0])
    assert parking[0].startswith("Parking") and "wifi" not in parking[0].lower() and "Parking" in parking[1]


@pytest.mark.asyncio
async def test_page_strategy_keeps_pages_apart_and_splits_long_ones():
    segs = [Seg("First page text. " * 5, "p.1"), Seg("Second page text. " * 5, "p.2"), Seg(("Long page sentence. " * 200), "p.3")]
    chunks, _ = await chunking.build_chunks(segs, chunking.Config("page", 600, 60), "pdf")
    assert [c[1] for c in chunks[:2]] == ["p.1", "p.2"]
    assert any(c[1] == "p.3 (cont.)" for c in chunks) and sum(1 for c in chunks if c[1].startswith("p.3")) > 2


@pytest.mark.asyncio
async def test_semantic_chunks_split_at_the_topic_change(monkeypatch):
    async def embed_documents(texts):
        return [fake_vec(t) for t in texts]

    monkeypatch.setattr(embeddings, "embed_documents", embed_documents)
    parking = " ".join(f"Parking permit rule {i}: permits for parking spaces cost money and expire monthly." for i in range(10))
    cooking = " ".join(f"Kitchen recipe step {i}: stir the soup and season the pasta sauce gently." for i in range(10))
    parts = await chunking.split_semantic(parking + " " + cooking, 700, 100)
    assert len(parts) >= 2
    assert not any("parking" in p.lower() and "soup" in p.lower() for p in parts[:1] + parts[-1:] if p is parts[0] and p is parts[-1])
    first_topic = [("parking" in p.lower(), "soup" in p.lower()) for p in parts]
    assert first_topic[0][0] and not first_topic[0][1]  # the first piece is all parking
    assert first_topic[-1][1] and not first_topic[-1][0]  # the last piece is all cooking


@pytest.mark.asyncio
async def test_semantic_degrades_to_paragraphs_without_embeddings():
    text = "\n\n".join(f"Paragraph {i}. " + "Some sentence here. " * 30 for i in range(5))
    parts = await chunking.split_semantic(text, 900, 100)  # EMBEDDINGS=none in tests
    assert len(parts) >= 3


# ---------- auto mode picks from the document's structure ----------


def auto(segs, kind, total=None):
    return chunking.choose_auto(segs, kind, total if total is not None else sum(len(s.text) for s in segs))


def test_auto_picks_by_document_shape():
    assert auto([Seg("a: 1\nb: 2", "sheet 'S' rows 1-2")], "xlsx")[0] == "page"
    assert auto([Seg("x " * 400, f"section 'H{i}'") for i in range(3)], "docx")[0] == "structure"
    assert auto([Seg("page text " * 100, "p.1"), Seg("more text " * 100, "p.2")], "pdf")[0] == "page"
    assert auto([Seg(HANDBOOK, "text")], "md")[0] == "structure"
    assert auto([Seg("A short note.", "text")], "txt")[0] == "fixed"
    paras = "\n\n".join("A paragraph of natural length. " * 8 for _ in range(6))
    assert auto([Seg(paras, "text")], "txt")[0] == "paragraph"
    assert auto([Seg("Continuous prose sentence. " * 400, "text")], "txt")[0] == "sentence"  # no embeddings in tests


def test_auto_uses_semantic_chunking_for_long_unstructured_text_when_embeddings_exist(monkeypatch):
    monkeypatch.setattr(embeddings, "provider", lambda: "local")
    strategy, reason = auto([Seg("Continuous prose sentence. " * 400, "text")], "txt")
    assert strategy == "semantic" and "topic" in reason


def test_auto_explains_its_choice():
    assert "headings" in auto([Seg(HANDBOOK, "text")], "md")[1]
    assert "table" in auto([Seg("a", "rows 1-2")], "csv")[1]


# ---------- configuration ----------


@pytest.mark.parametrize("cfg", [chunking.Config("nonsense"), chunking.Config("fixed", 50), chunking.Config("fixed", 9000), chunking.Config("fixed", 900, 600)])
def test_bad_settings_are_rejected(cfg):
    with pytest.raises(ValueError):
        cfg.validate()


# ---------- through ingestion ----------


@pytest.mark.asyncio
async def test_ingest_records_what_auto_chose_and_why():
    doc = await knowledge.ingest("u1", "handbook.md", HANDBOOK.encode())
    assert doc["chunking"]["strategy"] == "structure" and doc["chunking"]["requested"] == "auto" and "headings" in doc["chunking"]["reason"]
    assert doc["chunks"] == 3


@pytest.mark.asyncio
async def test_manual_settings_override_auto():
    big = await knowledge.ingest("u1", "a.txt", ("Plain sentence about nothing in particular. " * 120).encode(), cfg=chunking.Config("fixed", 900, 100))
    small = await knowledge.ingest("u1", "b.txt", ("Plain sentence about nothing in particular. " * 120).encode(), cfg=chunking.Config("fixed", 300, 50))
    assert small["chunks"] > big["chunks"] and small["chunking"]["reason"] == "chosen by you"


@pytest.mark.asyncio
async def test_search_finds_the_right_section_with_structure_chunking():
    await knowledge.ingest("u1", "handbook.md", HANDBOOK.encode())
    hit = (await knowledge.search("u1", "x", "how many free color pages"))[0]
    assert "200 free color pages" in hit["text"] and hit["text"].startswith("Printing")


@pytest.mark.asyncio
async def test_rechunk_changes_the_pieces_without_the_original_file():
    doc = await knowledge.ingest("u1", "handbook.md", HANDBOOK.encode())
    before = [r["text"] for r in knowledge.preview("u1", doc["id"], 20)]
    again = await knowledge.rechunk("u1", doc["id"], chunking.Config("fixed", 200, 20))
    after = [r["text"] for r in knowledge.preview("u1", doc["id"], 20)]
    assert again["chunking"]["strategy"] == "fixed" and again["chunking"]["reason"] == "chosen by you"
    assert after != before and doc["chunking"]["strategy"] == "structure"  # a different cut of the same text
    assert (await knowledge.search("u1", "x", "parking permits cost"))  # still searchable
    assert len(knowledge.list_docs("u1")) == 1  # replaced, not duplicated


@pytest.mark.asyncio
async def test_rechunk_is_private_to_the_owner():
    doc = await knowledge.ingest("u1", "handbook.md", HANDBOOK.encode())
    with pytest.raises(knowledge.IngestError):
        await knowledge.rechunk("someone-else", doc["id"], chunking.Config("fixed", 300, 30))


@pytest.mark.asyncio
async def test_preview_shows_the_first_pieces_in_order():
    doc = await knowledge.ingest("u1", "handbook.md", HANDBOOK.encode())
    rows = knowledge.preview("u1", doc["id"], 2)
    assert [r["ord"] for r in rows] == [0, 1] and knowledge.preview("other", doc["id"]) == []


# ---------- API ----------


def test_the_api_lists_strategies_and_accepts_chunking_choices(client):
    s = client.get("/knowledge/strategies").json()
    assert {x["id"] for x in s["strategies"]} >= {"auto", "fixed", "sentence", "paragraph", "structure", "page", "semantic"}
    r = client.post("/knowledge/upload", headers=USER, data={"chunking": "fixed", "chunk_size": "250", "overlap": "25"}, files={"file": ("h.md", HANDBOOK.encode(), "text/markdown")})
    assert r.status_code == 200 and r.json()["chunking"]["strategy"] == "fixed" and r.json()["chunking"]["size"] == 250
    doc_id = r.json()["id"]
    assert client.get(f"/knowledge/{doc_id}/chunks?limit=3", headers=USER).json()[0]["ord"] == 0
    rc = client.post(f"/knowledge/{doc_id}/rechunk", headers=USER, json={"strategy": "structure", "size": 900, "overlap": 100})
    assert rc.status_code == 200 and rc.json()["chunking"]["strategy"] == "structure" and rc.json()["chunks"] == 3


def test_the_api_rejects_bad_chunking_settings(client):
    files = {"file": ("h.md", HANDBOOK.encode(), "text/markdown")}
    assert client.post("/knowledge/upload", headers=USER, data={"chunking": "nonsense"}, files=files).status_code == 400
    assert client.post("/knowledge/upload", headers=USER, data={"chunk_size": "10"}, files=files).status_code == 400
    assert client.post("/knowledge/nope/rechunk", headers=USER, json={"strategy": "fixed"}).status_code == 400
    assert client.get("/knowledge/nope/chunks", headers=USER).status_code == 404
