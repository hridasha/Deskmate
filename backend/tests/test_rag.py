"""RAG tests: file extraction, chunking, hybrid search, scoping, SQL safety and the read-only database connector."""
import hashlib
import io
import sqlite3

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

import agent
import db
import dbsource
import knowledge
import main
import memory
from router import tokens

USER = {"X-User-Id": "rag-user-0001"}
OTHER = {"X-User-Id": "rag-user-0002"}


# ---------- sample files ----------


def make_docx() -> bytes:
    from docx import Document

    d = Document()
    d.add_heading("Parking policy", 1)
    d.add_paragraph("Parking permits cost $45 per month for employees.")
    d.add_heading("Wifi", 1)
    d.add_paragraph("The office wifi password rotates every Thursday.")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def make_xlsx() -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Sales"
    ws.append(["Region", "Revenue"])
    ws.append(["North", 1200])
    ws.append(["South", 800])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def make_pdf() -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=12)
    pdf.multi_cell(0, 8, "The annual offsite will be held in Lisbon in March. The budget per person is 900 euros.")
    pdf.add_page()
    pdf.multi_cell(0, 8, "Vegetarian meals must be requested two weeks in advance.")
    return bytes(pdf.output())


# ---------- fixtures ----------


@pytest.fixture(autouse=True)
def fresh(clean_db, monkeypatch):
    knowledge._cache.clear()
    knowledge._version.clear()
    dbsource._engines.clear()

    async def no_embed(texts):  # keyword-only unless a test installs fake vectors
        return [None] * len(texts)

    async def no_query_embed(text):
        return None

    monkeypatch.setattr(knowledge, "embed_many", no_embed)
    monkeypatch.setattr(memory, "embed", no_query_embed)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(agent, "_mcp_tools", [])
    main._recent.clear()
    with TestClient(main.app) as c:
        yield c


def fake_vec(text: str) -> list[float]:
    """A deterministic bag-of-words embedding, so semantic ranking can be tested offline."""
    v = [0.0] * 64
    for t in tokens(text):
        v[int(hashlib.md5(t.encode()).hexdigest(), 16) % 64] += 1.0
    return v


# ---------- extraction ----------


def test_extracts_text_and_locators_from_every_format():
    docx = knowledge.extract(".docx", make_docx())
    assert any("$45" in s.text and "Parking policy" in s.locator for s in docx)
    xlsx = knowledge.extract(".xlsx", make_xlsx())
    assert xlsx[0].locator == "sheet 'Sales' rows 1-2" and "Region: North; Revenue: 1200" in xlsx[0].text
    pdf = knowledge.extract(".pdf", make_pdf())
    assert [s.locator for s in pdf] == ["p.1", "p.2"] and "Lisbon" in pdf[0].text
    csv_ = knowledge.extract(".csv", b"name,city\nAda,London\nBob,Paris\n")
    assert "name: Ada; city: London" in csv_[0].text
    assert "Hello" in knowledge.extract(".html", b"<html><script>x()</script><body><p>Hello</p></body></html>")[0].text


def test_long_text_is_chunked_with_overlap():
    text = " ".join(f"Sentence number {i} is here." for i in range(300))
    chunks = knowledge.chunk([knowledge.Segment(text, "text")])
    assert len(chunks) > 3 and all(len(c[0]) <= knowledge.CHUNK_CHARS + 5 for c in chunks)
    assert chunks[1][1].endswith("(cont.)")
    # overlap: the end of one chunk reappears at the start of the next
    assert chunks[0][0][-30:].strip()[:15] in chunks[1][0]


# ---------- ingestion and search ----------


@pytest.mark.asyncio
async def test_search_finds_the_right_passage_and_cites_the_source():
    await knowledge.ingest("u1", "handbook.docx", make_docx())
    hits = await knowledge.search("u1", "pam_beesly", "How much does a parking permit cost?")
    assert hits and "$45" in hits[0]["text"]
    assert hits[0]["doc"] == "handbook.docx" and "Parking policy" in hits[0]["locator"]


@pytest.mark.asyncio
async def test_pdf_search_reports_the_page():
    await knowledge.ingest("u1", "offsite.pdf", make_pdf())
    hits = await knowledge.search("u1", "x", "vegetarian meals")
    assert hits[0]["locator"] == "p.2"


@pytest.mark.asyncio
async def test_semantic_matches_work_without_shared_keywords(monkeypatch):
    async def embed_many(texts):
        return [fake_vec(t) for t in texts]

    async def embed_one(t):
        return fake_vec(t)

    monkeypatch.setattr(knowledge, "embed_many", embed_many)
    monkeypatch.setattr(memory, "embed", embed_one)
    import embeddings

    monkeypatch.setattr(embeddings, "threshold", lambda kind: 0.3)
    await knowledge.ingest("u1", "notes.txt", b"The office Wifi password rotates every Thursday morning.\n\nParking permits cost forty five dollars.")
    hits = await knowledge.search("u1", "x", "wifi rotates thursday")
    assert hits and "Wifi" in hits[0]["text"] and hits[0]["cosine"] > 0


@pytest.mark.asyncio
async def test_documents_can_be_limited_to_certain_agents():
    await knowledge.ingest("u1", "secret.txt", b"The vault code is 4821 and it changes monthly.", scope=["oscar_martinez"])
    assert await knowledge.search("u1", "jim_halpert", "vault code") == []
    assert (await knowledge.search("u1", "oscar_martinez", "vault code"))[0]["text"].startswith("The vault")


@pytest.mark.asyncio
async def test_knowledge_is_private_per_user():
    await knowledge.ingest("u1", "a.txt", b"Alice's confidential budget figure is 77,000.")
    assert await knowledge.search("u2", "x", "budget figure") == []


@pytest.mark.asyncio
async def test_reuploading_the_same_file_replaces_it():
    await knowledge.ingest("u1", "a.txt", b"first version of the notes here " * 5)
    await knowledge.ingest("u1", "a.txt", b"first version of the notes here " * 5)
    assert len(knowledge.list_docs("u1")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,data,fragment",
    [("virus.exe", b"MZ", "isn't supported"), ("empty.txt", b"", "empty"), ("big.txt", b"x" * (knowledge.MAX_BYTES + 1), "larger than"), ("broken.pdf", b"not a pdf at all", "PDF")],
)
async def test_bad_files_get_a_helpful_error(name, data, fragment):
    with pytest.raises(knowledge.IngestError) as e:
        await knowledge.ingest("u1", name, data)
    assert fragment in str(e.value)


# ---------- the API ----------


def test_upload_search_scope_and_delete_through_the_api(client):
    r = client.post("/knowledge/upload", headers=USER, files={"file": ("handbook.docx", make_docx(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert r.status_code == 200 and r.json()["chunks"] >= 1
    doc_id = r.json()["id"]
    assert client.get("/knowledge", headers=USER).json()["docs"][0]["name"] == "handbook.docx"
    hits = client.post("/knowledge/search", headers=USER, json={"query": "parking permit price"}).json()
    assert hits and "$45" in hits[0]["text"]
    assert client.get("/knowledge", headers=OTHER).json()["docs"] == []  # isolated
    assert client.patch(f"/knowledge/{doc_id}/scope", headers=USER, json={"agents": ["oscar_martinez"]}).json()["scope"] == ["oscar_martinez"]
    assert client.delete(f"/knowledge/{doc_id}", headers=OTHER).status_code == 404
    assert client.delete(f"/knowledge/{doc_id}", headers=USER).status_code == 200
    assert client.get("/knowledge", headers=USER).json()["docs"] == []


def test_the_api_rejects_unsupported_uploads(client):
    r = client.post("/knowledge/upload", headers=USER, files={"file": ("tool.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 400 and "supported" in r.json()["detail"]


# ---------- SQL safety ----------


@pytest.mark.parametrize("sql", ["SELECT * FROM t", "select a, b from t where a > 1 order by b", "WITH x AS (SELECT 1 AS n) SELECT * FROM x", "SELECT 'drop table x' AS note", "SELECT 1;"])
def test_safe_queries_pass(sql):
    assert dbsource.validate_select(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM t",
        "UPDATE t SET a = 1",
        "INSERT INTO t VALUES (1)",
        "DROP TABLE t",
        "SELECT 1; DROP TABLE t",
        "PRAGMA table_info(t)",
        "ATTACH DATABASE 'x.db' AS y",
        "SELECT * INTO copy FROM t",
        "SELECT pg_read_file('/etc/passwd')",
        "-- sneaky\nDROP TABLE t",
        "/* x */ DELETE FROM t",
        "",
    ],
)
def test_unsafe_queries_are_refused(sql):
    with pytest.raises(dbsource.UnsafeQuery):
        dbsource.validate_select(sql)


# ---------- the database connector ----------


def sqlite_bytes(tmp_path, rows: int = 5) -> bytes:
    path = tmp_path / "shop.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, customer TEXT, total REAL)")
    conn.executemany("INSERT INTO orders (customer, total) VALUES (?, ?)", [(f"cust{i}", i * 10.5) for i in range(rows)])
    conn.commit()
    conn.close()
    return path.read_bytes()


@pytest.mark.asyncio
async def test_agents_can_query_an_uploaded_database_read_only(tmp_path):
    src = dbsource.add_sqlite_upload("u1", "shop", sqlite_bytes(tmp_path))
    assert "orders(" in src["schema"] and "customer" in src["schema"]
    out = await dbsource.query("u1", "oscar_martinez", "shop", "SELECT customer, total FROM orders WHERE total > 20 ORDER BY total")
    assert "cust2" in out and "| customer | total |" in out
    refused = await dbsource.query("u1", "oscar_martinez", "shop", "DELETE FROM orders")
    assert refused.startswith("Query refused")
    assert "5" in await dbsource.query("u1", "oscar_martinez", "shop", "SELECT COUNT(*) AS n FROM orders")  # data untouched


@pytest.mark.asyncio
async def test_results_are_capped_at_fifty_rows(tmp_path):
    dbsource.add_sqlite_upload("u1", "big", sqlite_bytes(tmp_path, rows=120))
    out = await dbsource.query("u1", "a", "big", "SELECT * FROM orders")
    assert out.count("\n| ") <= dbsource.MAX_ROWS + 1 and "first 50 rows" in out


@pytest.mark.asyncio
async def test_a_bad_query_returns_an_error_the_agent_can_use(tmp_path):
    dbsource.add_sqlite_upload("u1", "shop", sqlite_bytes(tmp_path))
    assert (await dbsource.query("u1", "a", "shop", "SELECT nope FROM orders")).startswith("Query failed")
    assert "Unknown database" in await dbsource.query("u1", "a", "missing", "SELECT 1")


@pytest.mark.asyncio
async def test_databases_can_be_limited_to_certain_agents(tmp_path):
    dbsource.add_sqlite_upload("u1", "shop", sqlite_bytes(tmp_path), scope=["oscar_martinez"])
    assert "Unknown database" in await dbsource.query("u1", "jim_halpert", "shop", "SELECT 1")
    assert "1" in await dbsource.query("u1", "oscar_martinez", "shop", "SELECT 1 AS one")


def test_only_real_sqlite_uploads_and_never_the_apps_own_files(tmp_path):
    with pytest.raises(dbsource.UnsafeQuery):
        dbsource.add_sqlite_upload("u1", "fake", b"definitely not sqlite")
    with pytest.raises(dbsource.UnsafeQuery):
        dbsource.add_url_source("u1", "own", f"sqlite:///{db.DB_PATH}")  # the app's own database must never be reachable
    with pytest.raises(dbsource.UnsafeQuery):
        dbsource.add_url_source("u1", "weird", "mongodb://localhost/x")


def test_database_endpoints_and_admin_protection(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "s3cret")
    files = {"file": ("shop.db", sqlite_bytes(tmp_path), "application/octet-stream")}
    assert client.post("/knowledge/databases/upload", headers=USER, data={"name": "shop"}, files=files).status_code == 401
    r = client.post("/knowledge/databases/upload", headers={**USER, "X-Admin-Token": "s3cret"}, data={"name": "shop"}, files=files)
    assert r.status_code == 200 and "orders" in r.json()["schema"]
    assert client.get("/knowledge", headers=USER).json()["sources"][0]["name"] == "shop"
    assert "url" not in client.get("/knowledge", headers=USER).text  # the connection string is never returned
    assert client.delete(f"/knowledge/databases/{r.json()['id']}", headers={**USER, "X-Admin-Token": "s3cret"}).status_code == 200


# ---------- wired into the agents ----------


def tool_call(name: str, **args) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{name}"}])


def test_agent_searches_documents_and_the_output_is_fenced_as_untrusted(client, fake_llm):
    client.post("/knowledge/upload", headers=USER, files={"file": ("handbook.docx", make_docx(), "application/octet-stream")})
    f = fake_llm([tool_call("search_knowledge", query="parking permit cost"), AIMessage(content="Permits cost $45 [handbook.docx]. [emotion: happy]")])
    r = client.post("/chat", headers=USER, json={"character_id": "jim_halpert", "message": "How much is parking? Check the documents."})
    assert '"tool": "search_knowledge"' in r.text and '"kind": "knowledge"' in r.text
    tool_msg = next(m for m in f.calls[1] if getattr(m, "name", "") == "search_knowledge")
    assert tool_msg.content.startswith('<untrusted source="search_knowledge"') and "$45" in tool_msg.content
    assert "handbook.docx" in f.calls[0][0].content  # the system prompt tells the agent what was uploaded


def test_only_permitted_agents_get_the_database_tools(client, fake_llm, tmp_path):
    f = fake_llm([AIMessage(content="ok [emotion: neutral]"), AIMessage(content="ok [emotion: neutral]")])
    client.post("/chat", headers=USER, json={"character_id": "oscar_martinez", "message": "hi"})
    client.post("/chat", headers=USER, json={"character_id": "jim_halpert", "message": "hi"})
    assert "query_database" in f.tools_seen[0] and "query_database" not in f.tools_seen[1]
    assert "search_knowledge" in f.tools_seen[1]  # but everyone can search documents


def test_the_connection_itself_refuses_writes_even_if_validation_were_bypassed(tmp_path):
    """The last line of defense: SQLite is opened in read-only mode, so a write fails at the database."""
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    src = dbsource.add_sqlite_upload("u1", "shop", sqlite_bytes(tmp_path))
    row = db.q("SELECT * FROM ksources WHERE id=?", (src["id"],))[0]
    with dbsource._engine_for(row).connect() as conn:
        with pytest.raises(OperationalError):
            conn.execute(text("DELETE FROM orders"))


def test_database_access_can_be_scoped_through_the_api(client, tmp_path):
    files = {"file": ("shop.db", sqlite_bytes(tmp_path), "application/octet-stream")}
    sid = client.post("/knowledge/databases/upload", headers=USER, data={"name": "shop"}, files=files).json()["id"]
    r = client.patch(f"/knowledge/databases/{sid}/scope", headers=USER, json={"agents": ["oscar_martinez"]})
    assert r.status_code == 200 and r.json()["scope"] == ["oscar_martinez"]
    assert client.patch("/knowledge/databases/nope/scope", headers=USER, json={"agents": None}).status_code == 404


# ---------- OCR: scans and images ----------


def make_png(text: str = "Invoice 4821") -> bytes:
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 200), "white")
    ImageDraw.Draw(img).text((20, 60), text, fill="black", font=ImageFont.load_default(size=40))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def make_scanned_pdf() -> bytes:
    """A PDF whose pages are only pictures, with no text layer."""
    from fpdf import FPDF

    pdf = FPDF()
    for _ in range(2):
        pdf.add_page()
        pdf.image(io.BytesIO(make_png("scan")), x=10, y=20, w=150)
    return bytes(pdf.output())


@pytest.fixture()
def ocr(monkeypatch):
    import llm

    monkeypatch.setattr(llm, "keys_for", lambda provider: ["fake-key"])
    seen = {}

    def install(text: str):
        async def fake(data, mime):
            seen["mime"] = mime
            return text

        monkeypatch.setattr(knowledge, "_ocr_call", fake)
        return seen

    return install


@pytest.mark.asyncio
async def test_images_are_read_with_ocr_and_become_searchable(ocr):
    seen = ocr("Invoice 4821\nCustomer: Acme Ltd\nTotal due: 1,250 EUR by 15 March")
    doc = await knowledge.ingest("u1", "invoice.png", make_png())
    assert doc["chunks"] >= 1 and seen["mime"] == "image/png"
    hits = await knowledge.search("u1", "x", "total due 1,250 EUR")
    assert hits and "1,250" in hits[0]["text"] and hits[0]["locator"] == "scan"


@pytest.mark.asyncio
async def test_scanned_pdfs_fall_back_to_ocr_and_keep_page_numbers(ocr):
    seen = ocr("=== Page 1 ===\nThe warranty period is 24 months.\n=== Page 2 ===\nReturns are accepted within 30 days.")
    assert knowledge.NeedsOCR  # the text extractor finds nothing and asks for OCR
    with pytest.raises(knowledge.NeedsOCR):
        knowledge.extract_pdf(make_scanned_pdf())
    await knowledge.ingest("u1", "contract-scan.pdf", make_scanned_pdf())
    assert seen["mime"] == "application/pdf"
    hit = (await knowledge.search("u1", "x", "how long do I have to return something"))[0]
    assert hit["locator"] == "p.2 (scanned)" and "30 days" in hit["text"]


@pytest.mark.asyncio
async def test_ocr_problems_get_helpful_errors(ocr, monkeypatch):
    ocr("NO_TEXT")
    with pytest.raises(knowledge.IngestError, match="No readable text"):
        await knowledge.ingest("u1", "blank.png", make_png())
    with pytest.raises(knowledge.IngestError, match="over 8 MB"):
        await knowledge.ingest("u1", "huge.png", b"x" * (knowledge.MAX_OCR_BYTES + 1))
    import llm

    monkeypatch.setattr(llm, "keys_for", lambda provider: [])
    with pytest.raises(knowledge.IngestError, match="Gemini API key"):
        await knowledge.ingest("u1", "scan.png", make_png())
