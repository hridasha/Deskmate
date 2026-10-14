"""OCR robustness check on deliberately messy scans.

Generates document images with known facts, then damages them the way real scans are damaged (rotation, blur, noise, low contrast,
JPEG compression, shadows, a phone-photo style perspective) and measures how many facts the OCR path recovers.

  python -m evals.ocr [--limit N]        uses the real vision model, so it spends API quota (about 2 requests per condition)

For real-world confidence, drop your own messy scans into evals/real_scans/ with a matching <name>.facts.json containing a list of
strings that must appear in the transcription; they are scored the same way.
"""
import asyncio
import io
import json
import random
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

import knowledge

OUT = Path(__file__).parent / "ocr_report.json"
REAL = Path(__file__).parent / "real_scans"

# (line of text, fact that must survive)
INVOICE = [
    ("INVOICE  No. 40817", "40817"),
    ("Issued 14 March 2025 to Northwind Traders", "Northwind"),
    ("Total due: EUR 2,480.50 within 30 days", "2,480.50"),
    ("IBAN DE89 3704 0044 0532 0130 00", "0532 0130 00"),
    ("Contact: Priya Raman, ext. 5521", "5521"),
]
MEMO = [
    ("MEMO - Warehouse safety update", "Warehouse"),
    ("From: Facilities Dept   Date: 02/11/2025", "02/11/2025"),
    ("Forklift certification expires every 18 months.", "18 months"),
    ("Report incidents to Marta Kowalski within 24 hours.", "Marta Kowalski"),
    ("New speed limit in aisle 7 is 5 km/h.", "5 km/h"),
]


def render(lines: list[tuple[str, str]], size: int = 30) -> Image.Image:
    img = Image.new("RGB", (1100, 120 + 70 * len(lines)), "white")
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=size)
    for i, (text, _) in enumerate(lines):
        d.text((60, 60 + 70 * i), text, fill="black", font=font)
    return img


def rotate(img: Image.Image, deg: float = 4.0) -> Image.Image:
    return img.rotate(deg, expand=True, fillcolor="white", resample=Image.BICUBIC)


def blur(img: Image.Image) -> Image.Image:
    return img.filter(ImageFilter.GaussianBlur(1.6))


def noise(img: Image.Image, amount: int = 60) -> Image.Image:
    rng = random.Random(3)
    px = img.load()
    for _ in range(img.width * img.height // 12):
        x, y = rng.randrange(img.width), rng.randrange(img.height)
        v = rng.randrange(0, amount)
        r, g, b = px[x, y]
        px[x, y] = (max(0, r - v), max(0, g - v), max(0, b - v))
    return img


def low_contrast(img: Image.Image) -> Image.Image:
    return ImageOps.autocontrast(img.point(lambda p: 150 + p // 6), cutoff=0).point(lambda p: 140 + p // 4)


def shadow(img: Image.Image) -> Image.Image:
    shade = Image.linear_gradient("L").resize(img.size).rotate(20).point(lambda p: 90 + p * 165 // 255)
    return Image.composite(img, Image.new("RGB", img.size, (60, 60, 60)), shade)


def perspective(img: Image.Image) -> Image.Image:
    w, h = img.size
    coeffs = (1, 0.12, 0, 0.0, 1, 0, 0.0004, 0.0002)  # a phone held at an angle
    return img.transform((w, h), Image.PERSPECTIVE, coeffs, Image.BICUBIC, fillcolor="white")


def jpeg(img: Image.Image, quality: int = 14) -> Image.Image:
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=quality)
    return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")


CONDITIONS = {
    "clean": lambda i: i,
    "rotated 4 degrees": rotate,
    "blurred": blur,
    "speckle noise": noise,
    "low contrast": low_contrast,
    "shadow": shadow,
    "phone perspective": perspective,
    "heavy JPEG": jpeg,
    "rotated + blurred + noisy + JPEG": lambda i: jpeg(noise(blur(rotate(i, 3.0))), 20),
}


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def score(text: str, facts: list[str]) -> float:
    norm = " ".join(text.lower().split())
    return sum(" ".join(f.lower().split()) in norm for f in facts) / len(facts)


async def run(limit: int | None = None) -> dict:
    rows = []
    jobs = [(name, doc_name, lines, fn) for name, fn in CONDITIONS.items() for doc_name, lines in (("invoice", INVOICE), ("memo", MEMO))]
    for name, doc_name, lines, fn in jobs[:limit]:
        img = fn(render(lines))
        facts = [f for _, f in lines]
        try:
            segs = await knowledge.ocr_segments(to_png(img), "image/png")
            text = " ".join(s.text for s in segs)
            rows.append({"condition": name, "doc": doc_name, "facts_found": round(score(text, facts), 2), "chars": len(text)})
        except Exception as e:  # noqa: BLE001
            rows.append({"condition": name, "doc": doc_name, "facts_found": None, "error": f"{type(e).__name__}: {str(e)[:100]}"})
        await asyncio.sleep(1)
    if REAL.is_dir():
        for img_path in sorted(REAL.glob("*.*")):
            facts_file = img_path.with_suffix(".facts.json")
            if img_path.suffix.lower() in (".png", ".jpg", ".jpeg", ".pdf", ".webp") and facts_file.exists():
                mime = {".pdf": "application/pdf", ".png": "image/png", ".webp": "image/webp"}.get(img_path.suffix.lower(), "image/jpeg")
                try:
                    segs = await knowledge.ocr_segments(img_path.read_bytes(), mime)
                    rows.append({"condition": "real scan", "doc": img_path.name, "facts_found": round(score(" ".join(s.text for s in segs), json.loads(facts_file.read_text(encoding="utf-8"))), 2)})
                except Exception as e:  # noqa: BLE001
                    rows.append({"condition": "real scan", "doc": img_path.name, "facts_found": None, "error": f"{type(e).__name__}"})
    scored = [r["facts_found"] for r in rows if r["facts_found"] is not None]
    report = {"when": time.time(), "tested": len(scored), "errors": len(rows) - len(scored), "average_facts_found": round(sum(scored) / len(scored), 3) if scored else None, "rows": rows}
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    rep = asyncio.run(run(lim))
    print(f"\nfacts recovered on average: {rep['average_facts_found']} ({rep['tested']} scored, {rep['errors']} failed)\n")
    for r in rep["rows"]:
        print(f"  {r['condition']:36s} {r['doc']:10s} {'' if r['facts_found'] is None else f'{r['facts_found']:.0%}'} {r.get('error', '')}")
