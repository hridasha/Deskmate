"""Data-driven chunking: probe questions, the scoreboard, and when auto mode measures instead of using rules."""
import hashlib

import pytest

import chunking
import embeddings
import knowledge
import memory
import tuning
from router import tokens

Seg = knowledge.Segment

DEPOTS = ["Alder", "Birch", "Cedar", "Dogwood", "Elm", "Fir", "Gum", "Hazel", "Iris", "Juniper", "Kauri", "Laurel", "Maple", "Nettle", "Oak", "Pine", "Quince", "Rowan", "Spruce", "Teak"]


def facts_text(n: int = 60) -> str:
    """Unique fact sentences, one running paragraph of plain prose (no headings, no paragraph breaks)."""
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    return " ".join(f"The {DEPOTS[i % 20]}{i} depot ships {40 + i * 3} pallets every {days[i % 5]} from bay {i + 2}." for i in range(n))


def fake_vec(text: str) -> list[float]:
    v = [0.0] * 64
    for t in tokens(text):
        v[int(hashlib.md5(t.encode()).hexdigest(), 16) % 64] += 1.0
    return v


@pytest.fixture(autouse=True)
def fresh(clean_db, monkeypatch):
    knowledge._cache.clear()
    knowledge._version.clear()


@pytest.fixture()
def local_embeddings(monkeypatch):
    async def docs(texts):
        return [fake_vec(t) for t in texts]

    async def query(text):
        return fake_vec(text)

    monkeypatch.setattr(embeddings, "provider", lambda: "local")
    monkeypatch.setattr(embeddings, "embed_documents", docs)
    monkeypatch.setattr(embeddings, "embed_query", query)


# ---------- probes ----------


def test_probes_hide_the_fact_and_point_back_to_the_source_sentence():
    probes = tuning.make_probes([Seg(facts_text(), "text")], n=20)
    assert len(probes) == 20
    for p in probes:
        assert p.answer in p.sentence and p.answer not in p.query.split() and len(p.query.split()) >= 3


def test_probes_are_repeatable():
    segs = [Seg(facts_text(), "text")]
    assert [p.query for p in tuning.make_probes(segs, 10, seed=3)] == [p.query for p in tuning.make_probes(segs, 10, seed=3)]


def test_text_without_concrete_facts_makes_no_probes():
    assert tuning.make_probes([Seg("it was a quiet and pleasant afternoon in the village where nothing happened at all. " * 20, "text")]) == []


# ---------- the scoreboard ----------


@pytest.mark.asyncio
async def test_tuning_tries_every_applicable_method_and_ranks_them():
    best, report = await tuning.tune([Seg(facts_text(), "text")], "txt", chunking.Config("tuned"))
    assert best is not None and report["tested"] == 9  # 3 strategies x 3 sizes: no headings or pages to justify more
    scores = [r["score"] for r in report["results"]]
    assert scores == sorted(scores, reverse=True) and report["chosen"] == report["results"][0]
    assert {r["strategy"] for r in report["results"]} <= {"fixed", "sentence", "paragraph"}
    assert all(0 <= r["hit@600"] <= 1 and 0 <= r["hit@1500"] <= 1 for r in report["results"])


PROBE = tuning.Probe(query="depot ships pallets every Monday bay", answer="40", sentence="The Alder0 depot ships 40 pallets every Monday from bay 2.")


def test_a_sentence_cut_across_two_passages_is_a_miss():
    cut = ["The Alder0 depot ships 40 pallets", "every Monday from bay 2. Other text here."]
    whole = ["The Alder0 depot ships 40 pallets every Monday from bay 2. Other text here."]
    assert tuning._hits(cut, [None, None], PROBE, None) == {600: False, 1500: False}
    assert tuning._hits(whole, [None], PROBE, None) == {600: True, 1500: True}


def test_one_giant_passage_cannot_win_just_by_containing_everything():
    """Regression: with 'top 3 passages' scoring the biggest size always won. A passage only counts for what fits the budget."""
    filler = "Unrelated filler words about nothing at all. " * 60  # ~2,700 characters
    giant = [filler + PROBE.sentence]  # the fact sits far beyond either budget
    small = [filler[:400], PROBE.sentence, filler[400:800]]  # the same text in small passages: the fact passage ranks first on keywords
    assert tuning._hits(giant, [None], PROBE, None) == {600: False, 1500: False}
    assert tuning._hits(small, [None, None, None], PROBE, None) == {600: True, 1500: True}


@pytest.mark.asyncio
async def test_headed_and_paged_documents_get_the_methods_that_fit_them():
    text = "\n\n".join(f"## Depot {i}\n" + facts_text(6) for i in range(8))
    cands = {c.strategy for c in tuning.candidate_configs([Seg(text, "text")], chunking.Config())}
    assert "structure" in cands and "page" not in cands
    pages = [Seg(facts_text(6), f"p.{i}") for i in range(1, 4)]
    assert "page" in {c.strategy for c in tuning.candidate_configs(pages, chunking.Config())}


@pytest.mark.asyncio
async def test_too_little_text_to_measure_falls_back_to_the_rules():
    chunks, info = await chunking.build_chunks([Seg("A short note about the office. " * 3, "text")], chunking.Config("tuned"), "txt")
    assert chunks and "not enough facts" in info["reason"] and "tuning" in info and info["tuning"]["probes"] < tuning.MIN_PROBES


# ---------- when auto mode measures ----------


@pytest.mark.asyncio
async def test_auto_measures_when_there_is_no_structure_to_go_on(local_embeddings):
    chunks, info = await chunking.build_chunks([Seg(facts_text(), "text")], chunking.Config("auto"), "txt")
    assert info["requested"] == "auto" and "tuned on" in info["reason"] and info["tuning"]["tested"] >= 9
    assert info["strategy"] == info["tuning"]["chosen"]["strategy"] and info["size"] == info["tuning"]["chosen"]["size"]


@pytest.mark.asyncio
async def test_auto_keeps_using_rules_when_the_structure_is_clear(local_embeddings):
    handbook = "\n\n".join(f"## Section {i}\n" + facts_text(5) for i in range(6))
    _, info = await chunking.build_chunks([Seg(handbook, "text")], chunking.Config("auto"), "md")
    assert info["strategy"] == "structure" and "tuning" not in info
    _, table = await chunking.build_chunks([Seg("a: 1\nb: 2", "sheet 'S' rows 1-2")], chunking.Config("auto"), "xlsx")
    assert table["strategy"] == "page" and "tuning" not in table


@pytest.mark.asyncio
async def test_tuning_can_be_turned_off(local_embeddings, monkeypatch):
    monkeypatch.setattr(chunking, "AUTO_TUNE", False)
    _, info = await chunking.build_chunks([Seg(facts_text(), "text")], chunking.Config("auto"), "txt")
    assert "tuning" not in info


@pytest.mark.asyncio
async def test_tables_are_never_tuned():
    _, info = await chunking.build_chunks([Seg("a: 1\nb: 2", "sheet 'S' rows 1-2")], chunking.Config("tuned"), "csv")
    assert "isn't available" in info["reason"]


# ---------- through ingestion ----------


@pytest.mark.asyncio
async def test_ingest_stores_the_scoreboard_and_the_winner_is_what_was_used(local_embeddings):
    doc = await knowledge.ingest("u1", "depots.txt", facts_text(70).encode(), cfg=chunking.Config("tuned"))
    info = doc["chunking"]
    assert info["tuning"]["results"][0]["strategy"] == info["strategy"] and doc["chunks"] >= 1
    again = await knowledge.rechunk("u1", doc["id"], chunking.Config("tuned"))  # re-tune from the stored text
    assert again["chunking"]["tuning"]["probes"] >= tuning.MIN_PROBES
