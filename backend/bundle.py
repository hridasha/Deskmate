"""Export and import: move your agents and documents between workspaces or servers as one .zip file.

Included: your custom agents (persona, look, tools, starters) and your uploaded documents (original files, who may see them).
Not included: API keys, database connections (they hold passwords), chats, notes and memories.
Imports are validated like any other input: personas pass the prompt-injection filter, files go through normal ingestion.
"""
import io
import json
import re
import time
import zipfile

import agent as agents
import db
import knowledge
import policy
import safety

VERSION = 1
MAX_ZIP = 80 * 1024 * 1024
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "agent"


def export_bundle(user: str) -> bytes:
    mine = [c for c in agents.load_custom() if c.get("owner") in (None, user)]
    docs = knowledge.list_docs(user)
    manifest = {"version": VERSION, "exported": time.time(), "agents": [], "docs": []}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for i, d in enumerate(docs):
            found = knowledge.original_file(user, d["id"])
            if not found:
                continue
            arc = f"files/{i}-{d['name']}"
            z.write(found[0], arc)
            manifest["docs"].append({"id": d["id"], "file": arc, "name": d["name"], "scope": d["scope"], "chunking": (d.get("chunking") or {}).get("requested")})
        doc_ids = {d["id"] for d in manifest["docs"]}
        for c in mine:
            item = {k: v for k, v in c.items() if k != "owner"}
            kb = item.get("knowledge")
            if kb:
                item["knowledge"] = {"docs": [x for x in kb.get("docs", []) if x in doc_ids], "sources": []}
            manifest["agents"].append(item)
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
    return buf.getvalue()


class ImportProblem(ValueError):
    pass


def _clean_agent(raw: dict) -> dict:
    name = str(raw.get("name", "")).strip()[:40]
    if len(name) < 1:
        raise ImportProblem("an agent has no name")
    persona = str(raw.get("persona", ""))[:2500]
    try:
        persona = safety.sanitize_persona(persona)
    except safety.PersonaRejected as e:
        raise ImportProblem(f"{name}: {e}") from e
    color = raw.get("color") if HEX.match(str(raw.get("color", ""))) else "#8793d1"
    out = {"name": name, "archetype": str(raw.get("archetype", ""))[:80], "color": color, "persona": persona}
    tools = [t for t in raw.get("tools", []) if t in policy.OPTIONAL_TOOLS][:5]
    if tools:
        out["tools"] = tools
    look = raw.get("look")
    if isinstance(look, dict):
        out["look"] = {k: v for k, v in look.items() if k in {"hair", "hairColor", "skin", "shirt", "pants", "jacket", "tie", "glasses", "mustache", "bulk"}}
    out["starters"] = [str(s)[:120] for s in raw.get("starters", [])][:4]
    return out


async def import_bundle(user: str, data: bytes) -> dict:
    if len(data) > MAX_ZIP:
        raise ImportProblem("That file is too large.")
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        manifest = json.loads(z.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as e:
        raise ImportProblem("That isn't a Deskmates export.") from e
    if manifest.get("version") != VERSION:
        raise ImportProblem("This export was made by an incompatible version.")
    names = {i.filename: i for i in z.infolist()}
    report = {"agents": [], "docs": [], "skipped": []}

    doc_map: dict[str, str] = {}
    for d in manifest.get("docs", [])[: knowledge.MAX_DOCS]:
        info = names.get(d.get("file", ""))
        if not info or info.file_size > knowledge.MAX_BYTES or ".." in info.filename or info.filename.startswith("/"):
            report["skipped"].append(f"file {d.get('name', '?')}")
            continue
        try:
            doc = await knowledge.ingest(user, d["name"], z.read(info))
            doc_map[d["id"]] = doc["id"]
            report["docs"].append(doc["name"])
        except Exception as e:  # noqa: BLE001
            report["skipped"].append(f"{d.get('name', 'file')}: {str(e)[:80]}")

    existing = agents.load_characters(user)
    custom = agents.load_custom()
    room = agents.MAX_WORKERS - len([c for c in existing.values() if c.get("role") != "head"])
    idmap: dict[str, str] = {}
    created = []
    for raw in manifest.get("agents", []):
        if room <= 0:
            report["skipped"].append(f"{raw.get('name', 'agent')}: the office is full")
            continue
        try:
            item = _clean_agent(raw)
        except ImportProblem as e:
            report["skipped"].append(str(e))
            continue
        base, cid, n = _slug(item["name"]), _slug(item["name"]), 2
        taken = set(existing) | {c["id"] for c in custom} | {c["id"] for c in created}
        while cid in taken:
            cid, n = f"{base}_{n}", n + 1
        idmap[raw.get("id", base)] = cid
        item["id"], item["owner"] = cid, user
        kb = raw.get("knowledge") or {}
        mapped = [doc_map[x] for x in kb.get("docs", []) if x in doc_map]
        if mapped:
            item["grounded"] = True
            item["knowledge"] = {"docs": mapped, "sources": []}
        created.append(item)
        room -= 1
        report["agents"].append(item["name"])
    if created:
        agents.save_custom([*custom, *created])
    for d in manifest.get("docs", []):  # keep "only this agent may see it" working under the new ids
        new_id = doc_map.get(d.get("id", ""))
        if new_id and d.get("scope"):
            scope = [idmap.get(s, s) for s in d["scope"]]
            knowledge.set_scope(user, new_id, scope)
    return report
