"""Keeping documents current: re-read web pages and watched folders, and re-index only what changed.

* A document added from a URL remembers its address; `sync_doc` fetches it again and re-indexes it if the content differs.
* KNOWLEDGE_WATCH_DIR=/some/folder (inside the container) is mirrored into a workspace: new and changed files are indexed,
  files that disappear are removed. KNOWLEDGE_WATCH_USER picks whose knowledge base it is (default "anon").
* `sync_all` runs from the scheduler every SYNC_INTERVAL_MIN minutes (0 turns it off).
"""
import hashlib
import mimetypes
import os
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import chunking
import db
import knowledge
import safe_url

MAX_BYTES = knowledge.MAX_BYTES
_EXT_BY_TYPE = {"text/html": ".html", "application/pdf": ".pdf", "text/plain": ".txt", "text/csv": ".csv", "text/markdown": ".md", "application/json": ".json"}


async def fetch_bytes(url: str) -> tuple[bytes, str]:
    """Download a page or file through the SSRF-guarded client. Returns (data, content type)."""
    safe_url.check_url(url)
    async with safe_url.guarded_client(headers={"User-Agent": "Mozilla/5.0 (compatible; Deskmates/1.0)"}, timeout=30) as client:
        async with client.stream("GET", url.strip()) as r:
            if r.status_code >= 400:
                raise knowledge.IngestError(f"The server answered HTTP {r.status_code}.")
            data = b""
            async for part in r.aiter_bytes():
                data += part
                if len(data) > MAX_BYTES:
                    raise knowledge.IngestError(f"That page is larger than {MAX_BYTES // (1024 * 1024)} MB.")
            return data, r.headers.get("content-type", "").split(";")[0].strip().lower()


def name_for(url: str, content_type: str) -> str:
    parsed = urlparse(url)
    last = Path(parsed.path).name
    ext = Path(last).suffix.lower()
    if ext not in knowledge.SUPPORTED:
        ext = _EXT_BY_TYPE.get(content_type) or mimetypes.guess_extension(content_type or "") or ".html"
        last = (Path(last).stem or "") + ext if last else ""
    stem = re.sub(r"[^\w.-]+", "_", f"{parsed.hostname}{('_' + last) if last else ''}")
    return stem[:110] if stem.endswith(ext) else f"{stem[:100]}{ext}"


async def ingest_url(user: str, url: str, scope: list[str] | None = None, cfg: chunking.Config | None = None) -> dict:
    try:
        data, ctype = await fetch_bytes(url)
    except safe_url.BlockedHost as e:
        raise knowledge.IngestError(str(e)) from e
    except knowledge.IngestError:
        raise
    except Exception as e:  # noqa: BLE001
        raise knowledge.IngestError(f"Couldn't download that address ({type(e).__name__}).") from e
    return await knowledge.ingest(user, name_for(url, ctype), data, scope=scope, cfg=cfg, origin={"type": "url", "url": url.strip()})


async def sync_doc(user: str, doc_id: str) -> dict:
    """Re-fetch a URL document and re-index it if it changed. Returns {"status": "unchanged"|"updated"|"skipped"|"failed", ...}."""
    doc = knowledge.get_doc(user, doc_id)
    if not doc:
        return {"status": "failed", "detail": "No such document."}
    origin = doc.get("origin") or {}
    if origin.get("type") != "url":
        return {"status": "skipped", "detail": "Only documents added from a web address can be re-fetched."}
    try:
        updated = await ingest_url(user, origin["url"], doc["scope"])
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "detail": str(e)[:200]}
    return {"status": "unchanged" if updated.get("unchanged") else "updated", "doc": updated}


async def sync_folder(user: str, folder: Path) -> dict:
    """Mirror a folder into the knowledge base: add or update changed files, drop documents whose file is gone."""
    added = updated = removed = failed = 0
    seen: set[str] = set()
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in knowledge.SUPPORTED and p.stat().st_size <= MAX_BYTES):
        rel = str(path.relative_to(folder))
        name = Path(path.name).name
        seen.add(name)
        data = path.read_bytes()
        before = db.q("SELECT hash FROM kdocs WHERE user=? AND name=?", (user, name))
        if before and before[0]["hash"] == hashlib.sha256(data).hexdigest():
            continue
        try:
            await knowledge.ingest(user, name, data, origin={"type": "folder", "path": rel})
            updated, added = (updated + 1, added) if before else (updated, added + 1)
        except Exception:  # noqa: BLE001
            failed += 1
    for d in db.q("SELECT id, name FROM kdocs WHERE user=? AND origin LIKE '%\"folder\"%'", (user,)):
        if d["name"] not in seen:
            knowledge.delete_doc(user, d["id"])
            removed += 1
    return {"added": added, "updated": updated, "removed": removed, "failed": failed}


async def sync_all() -> dict:
    """One pass over every URL document (not touched in the last hour) and the watched folder."""
    out = {"urls": 0, "changed": 0, "folder": None}
    stale = db.q("SELECT id, user FROM kdocs WHERE origin LIKE '%\"url\"%' AND (synced IS NULL OR synced < ?)", (time.time() - 3600,))
    for d in stale:
        res = await sync_doc(d["user"], d["id"])
        out["urls"] += 1
        out["changed"] += res["status"] == "updated"
        if res["status"] == "failed":  # don't hammer a dead address every cycle
            db.run("UPDATE kdocs SET synced=? WHERE id=?", (time.time(), d["id"]))
    folder = os.getenv("KNOWLEDGE_WATCH_DIR", "").strip()
    if folder and Path(folder).is_dir():
        out["folder"] = await sync_folder(os.getenv("KNOWLEDGE_WATCH_USER", "anon"), Path(folder))
    return out
