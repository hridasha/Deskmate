"""Chunking: how a document is cut into searchable passages.

Strategies (pick one per upload, or leave it on "auto"):
  fixed      fixed-size pieces with overlap
  sentence   whole sentences grouped up to the size limit
  paragraph  paragraphs kept together, small ones merged
  structure  split at headings; every piece starts with its heading for context
  page       one piece per page / sheet section (long ones are split)
  semantic   split where the topic changes, found with the embedding model

Auto mode looks at the document itself (tables, pages, headings, paragraph shape, length) and picks one, and says why.
"""
import os
import re
from dataclasses import dataclass

import embeddings
import memory

STRATEGIES = {
    "auto": "Pick automatically from the document's structure (recommended).",
    "fixed": "Fixed-size pieces with overlap. Simple and predictable.",
    "sentence": "Whole sentences grouped up to the size limit. Never cuts a sentence in half.",
    "paragraph": "Paragraphs kept together, small ones merged. Good for prose and notes.",
    "structure": "Split at headings, and start each piece with its heading. Good for handbooks, manuals and Markdown.",
    "page": "One piece per page or sheet section (long ones are split). Keeps page numbers exact; good for PDFs and tables.",
    "semantic": "Split where the topic changes, found with the embedding model. Good for long unstructured text.",
    "tuned": "Try several methods and sizes on THIS document and keep whichever finds facts best. Runs locally, no AI quota.",
}

# With no strong structure to go on, auto mode measures instead of guessing (set AUTO_TUNE=false to turn it off).
AUTO_TUNE = os.getenv("AUTO_TUNE", "true").lower() not in ("0", "false", "no")
AMBIGUOUS = {"paragraph", "sentence", "semantic", "fixed"}
TUNE_MIN_CHARS = 3000
MIN_SIZE, MAX_SIZE = 200, 3000
MIN_CHUNK = 20


@dataclass(frozen=True)
class Config:
    strategy: str = "auto"
    size: int = 500
    overlap: int = 60

    def validate(self) -> "Config":
        if self.strategy not in STRATEGIES:
            raise ValueError(f"Unknown chunking method '{self.strategy}'. Choose one of: {', '.join(STRATEGIES)}.")
        if not MIN_SIZE <= self.size <= MAX_SIZE:
            raise ValueError(f"Chunk size must be between {MIN_SIZE} and {MAX_SIZE} characters.")
        if not 0 <= self.overlap <= min(500, self.size // 2):
            raise ValueError(f"Overlap must be between 0 and {min(500, self.size // 2)} characters.")
        return self


# ---------------- building blocks ----------------


def _clean(text: str) -> str:
    return re.sub(r"[ \t]+", " ", text).strip()


def split_fixed(text: str, size: int, overlap: int) -> list[str]:
    """Fixed-size windows that prefer to end at a line or sentence boundary, with overlap."""
    text = _clean(text)
    if len(text) <= size:
        return [text] if text else []
    out, start = [], 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            window = text[start:end]
            cut = max(window.rfind("\n"), window.rfind(". "))
            if cut > size * 0.5:
                end = start + cut + 1
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(end - overlap, start + 1)
    return out


_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])|\n+")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE_END.split(_clean(text)) if s and s.strip()]


def split_sentences(text: str, size: int, overlap: int) -> list[str]:
    """Group whole sentences up to `size`; carry the last sentences over as overlap."""
    out, cur = [], []
    for s in sentences(text):
        if cur and len(" ".join(cur)) + len(s) + 1 > size:
            out.append(" ".join(cur))
            carry, total = [], 0
            for prev in reversed(cur):
                if total + len(prev) > overlap:
                    break
                carry.insert(0, prev)
                total += len(prev)
            cur = carry
        cur.append(s)
    if cur:
        out.append(" ".join(cur))
    return out


def split_paragraphs(text: str, size: int, overlap: int) -> list[str]:
    """Keep paragraphs whole, merge short neighbours up to `size`, and split any overlong paragraph by sentences."""
    paras = [_clean(p) for p in re.split(r"\n\s*\n", text) if _clean(p)]
    out, cur = [], ""
    for p in paras:
        if len(p) > size * 1.5:
            if cur:
                out.append(cur)
                cur = ""
            out.extend(split_sentences(p, size, overlap))
        elif cur and len(cur) + len(p) + 2 > size:
            out.append(cur)
            cur = p
        else:
            cur = f"{cur}\n\n{p}" if cur else p
    if cur:
        out.append(cur)
    return out


# ---------------- headings ----------------

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_NUMBERED = re.compile(r"^(\d+(?:\.\d+){0,3})[.)]?\s+([A-Z].{2,70})$")


def _is_heading(line: str, prev_blank: bool, next_line: str) -> str | None:
    s = line.strip()
    if not s or len(s) > 90:
        return None
    m = _MD_HEADING.match(s)
    if m:
        return m.group(2).strip()
    m = _NUMBERED.match(s)
    if m and not s.endswith((".", ",", ";")):
        return s
    letters = [c for c in s if c.isalpha()]
    if len(letters) >= 4 and len(s) <= 60 and all(c.isupper() for c in letters) and prev_blank:
        return s.title()
    words = s.split()
    if prev_blank and next_line.strip() and 1 <= len(words) <= 7 and not s.endswith((".", ",", ";", ":", "?")) and s[0].isupper() and next_line.strip()[0].isupper():
        return s
    return None


def sections(text: str) -> list[tuple[str, str]]:
    """Split text into (heading, body) sections. Text before the first heading has an empty heading."""
    lines = text.splitlines()
    out: list[tuple[str, list[str]]] = [("", [])]
    for i, line in enumerate(lines):
        prev_blank = i == 0 or not lines[i - 1].strip()
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        h = _is_heading(line, prev_blank, nxt)
        if h:
            out.append((h, []))
        else:
            out[-1][1].append(line)
    return [(h, "\n".join(b).strip()) for h, b in out if "\n".join(b).strip() or h]


def count_headings(text: str) -> int:
    return sum(1 for heading, _ in sections(text) if heading)


# ---------------- strategies over segments ----------------


def _expand(pieces: list[str], locator: str, prefix: str = "") -> list[tuple[str, str]]:
    return [((prefix + p).strip(), locator if i == 0 else f"{locator} (cont.)") for i, p in enumerate(pieces) if p.strip()]


def _by_structure(segments, cfg: Config) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for seg in segments:
        m = re.match(r"section '(.+)'", seg.locator)  # Word files already arrive split by heading
        if m:
            out += _expand(split_paragraphs(seg.text, cfg.size, cfg.overlap), seg.locator, f"{m.group(1)}\n")
            continue
        for heading, body in sections(seg.text):
            loc = f"{seg.locator} · {heading}" if heading else seg.locator
            out += _expand(split_paragraphs(body or heading, cfg.size, cfg.overlap), loc, f"{heading}\n" if heading and body else "")
    return out


def _by_page(segments, cfg: Config) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for seg in segments:
        text = _clean(seg.text)
        if len(text) <= cfg.size * 1.6:
            out.append((text, seg.locator))
        else:
            out += _expand(split_paragraphs(seg.text, cfg.size, cfg.overlap), seg.locator)
    return out


async def split_semantic(text: str, size: int, overlap: int) -> list[str]:
    """Cut where adjacent sentences are least similar (a topic change), within size limits."""
    sents = sentences(text)
    if len(sents) < 8:
        return split_paragraphs(text, size, overlap)
    vecs = await embeddings.embed_documents(sents)
    if any(v is None for v in vecs):
        return split_paragraphs(text, size, overlap)  # no embedding model available: degrade gracefully
    sims = [memory.cosine(vecs[i], vecs[i + 1]) for i in range(len(vecs) - 1)]
    cutoff = sorted(sims)[max(0, int(len(sims) * 0.2) - 1)]  # break at the least similar ~20% of boundaries
    groups, cur = [], [sents[0]]
    for i, s in enumerate(sents[1:]):
        too_big = len(" ".join(cur)) + len(s) > size * 1.6
        topic_shift = sims[i] <= cutoff and len(" ".join(cur)) >= size * 0.3
        if too_big or topic_shift:
            groups.append(" ".join(cur))
            cur = []
        cur.append(s)
    groups.append(" ".join(cur))
    merged: list[str] = []
    for g in groups:  # fold tiny leftovers into the previous piece
        if merged and len(g) < size * 0.2:
            merged[-1] += " " + g
        else:
            merged.append(g)
    return merged


async def _semantic(segments, cfg: Config) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for seg in segments:
        out += _expand(await split_semantic(seg.text, cfg.size, cfg.overlap), seg.locator)
    return out


def _simple(segments, cfg: Config, splitter) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for seg in segments:
        out += _expand(splitter(seg.text, cfg.size, cfg.overlap), seg.locator)
    return out


# ---------------- auto ----------------


def choose_auto(segments, kind: str, total_chars: int) -> tuple[str, str]:
    """Pick a strategy from the document's own structure, and explain the choice."""
    locators = [s.locator for s in segments]
    if kind in ("xlsx", "xlsm", "csv", "tsv") or any("rows" in loc for loc in locators):
        return "page", "a table: kept in groups of rows with the header repeated"
    if sum(1 for loc in locators if loc.startswith("section '")) >= 2:
        return "structure", f"a document with {len(locators)} headed sections"
    if len(segments) >= 2 and all(loc.startswith("p.") for loc in locators):
        return "page", f"{len(segments)} pages: keeping page numbers exact"
    text = "\n\n".join(s.text for s in segments)
    headings = count_headings(text)
    if headings >= 2:
        return "structure", f"{headings} headings found"
    if total_chars <= 1200:
        return "fixed", "short enough to keep as one piece"
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) >= 5 and 150 <= sum(len(p) for p in paras) / len(paras) <= 1400:
        return "paragraph", f"{len(paras)} paragraphs of a natural length"
    if total_chars >= 6000 and embeddings.provider() != "none":
        return "semantic", "long text without headings or clear paragraphs: splitting where the topic changes"
    return "sentence", "continuous prose: grouping whole sentences"


async def build_chunks(segments, cfg: Config, kind: str, _tuning: bool = True) -> tuple[list[tuple[str, str]], dict]:
    """Cut segments into (text, locator) passages. Returns the passages and what was actually used."""
    cfg.validate()
    requested = cfg.strategy
    total = sum(len(s.text) for s in segments)
    strategy, reason = cfg.strategy, "chosen by you"
    size, overlap = cfg.size, cfg.overlap
    tuning: dict | None = None
    if strategy == "auto":
        strategy, reason = choose_auto(segments, kind, total)
    wants_tuning = strategy == "tuned" or (cfg.strategy == "auto" and AUTO_TUNE and strategy in AMBIGUOUS and total >= TUNE_MIN_CHARS and embeddings.provider() != "none")
    if wants_tuning and _tuning and kind not in ("xlsx", "xlsm", "csv", "tsv"):
        import tuning

        best, tuning = await tuning.tune(segments, kind, cfg)
        if best:
            strategy, size, overlap = best.strategy, best.size, best.overlap
            reason = f"tuned on {tuning['probes']} questions made from your document ({tuning['tested']} methods and sizes tried)"
        else:  # too little text to measure: fall back to the rules
            strategy, fallback = (choose_auto(segments, kind, total)[0], "not enough facts in the text to tune")
            reason = f"{fallback}; used the rules instead"
    elif strategy == "tuned":
        strategy = choose_auto(segments, kind, total)[0]
        reason = "tuning isn't available for this file type; used the rules instead"
    cfg = Config(strategy, size, overlap)
    if strategy == "structure":
        chunks = _by_structure(segments, cfg)
    elif strategy == "page":
        chunks = _by_page(segments, cfg)
    elif strategy == "semantic":
        chunks = await _semantic(segments, cfg)
    elif strategy == "paragraph":
        chunks = _simple(segments, cfg, split_paragraphs)
    elif strategy == "sentence":
        chunks = _simple(segments, cfg, split_sentences)
    else:
        chunks = _simple(segments, cfg, lambda t, s, o: split_fixed(t, s, o))
    chunks = [(t, loc) for t, loc in chunks if len(t) >= MIN_CHUNK]
    info = {"strategy": strategy, "requested": requested, "size": size, "overlap": overlap, "reason": reason}
    if tuning:
        info["tuning"] = tuning
    return chunks, info
