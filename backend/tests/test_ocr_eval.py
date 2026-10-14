"""The OCR robustness harness itself (the real vision model is only used by `python -m evals.ocr`)."""
import pytest

from evals import ocr
from PIL import Image


def test_every_damage_condition_produces_a_valid_image_of_readable_size():
    base = ocr.render(ocr.INVOICE)
    for name, fn in ocr.CONDITIONS.items():
        out = fn(base.copy())
        assert isinstance(out, Image.Image) and out.width > 500 and out.height > 300, name
        assert len(ocr.to_png(out)) > 1000, name


def test_damage_really_changes_the_pixels():
    base = ocr.render(ocr.MEMO)
    clean = ocr.to_png(base)
    for name, fn in ocr.CONDITIONS.items():
        if name != "clean":
            assert ocr.to_png(fn(base.copy())) != clean, name


def test_scoring_counts_facts_ignoring_case_and_spacing():
    assert ocr.score("Total due: EUR  2,480.50 within 30 DAYS", ["2,480.50", "30 days"]) == 1.0
    assert ocr.score("nothing useful", ["2,480.50"]) == 0.0
    assert ocr.score("Northwind only", ["Northwind", "40817"]) == 0.5


@pytest.mark.asyncio
async def test_the_runner_scores_with_a_stand_in_ocr(monkeypatch, tmp_path):
    async def fake_ocr(data, mime):
        return [ocr.knowledge.Segment("INVOICE 40817 Northwind 2,480.50 0532 0130 00 ext. 5521 Warehouse 02/11/2025 18 months Marta Kowalski 5 km/h", "scan")]

    monkeypatch.setattr(ocr.knowledge, "ocr_segments", fake_ocr)
    monkeypatch.setattr(ocr, "OUT", tmp_path / "r.json")
    monkeypatch.setattr(ocr, "REAL", tmp_path / "none")
    rep = await ocr.run(limit=4)
    assert rep["tested"] == 4 and rep["average_facts_found"] == 1.0 and (tmp_path / "r.json").exists()
