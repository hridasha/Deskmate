"""Remove data left behind by tests and demos: notes, memories, documents, databases, traces, tasks, presence and chat threads.

Run from the backend folder:
    python -m scripts.cleanup_test_data --dry-run
    python -m scripts.cleanup_test_data
    python -m scripts.cleanup_test_data --doc office-handbook.md      # also delete a named document, for any user
"""
import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db  # noqa: E402

DEFAULT_PREFIXES = ["tester-", "live-", "status-demo", "expert-demo", "eval-", "rag-", "presence-", "test-user", "fresh-user", "someone-else", "u1", "u2"]
USER_TABLES = ["notes", "memories", "traces", "tasks", "usage", "presence"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--prefix", action="append", help="extra user-id prefix to remove")
    ap.add_argument("--doc", action="append", default=[], help="also delete documents with this exact name (any user)")
    args = ap.parse_args()
    prefixes = DEFAULT_PREFIXES + (args.prefix or [])
    like = " OR ".join(["user LIKE ?"] * len(prefixes))
    params = tuple(f"{p}%" for p in prefixes)
    total = {}

    def count(table: str, where: str, p: tuple) -> int:
        return db.q(f"SELECT COUNT(*) AS n FROM {table} WHERE {where}", p)[0]["n"]

    for t in USER_TABLES:
        total[t] = count(t, like, params)
        if not args.dry_run and total[t]:
            db.run(f"DELETE FROM {t} WHERE {like}", params)

    doc_ids = [r["id"] for r in db.q(f"SELECT id FROM kdocs WHERE {like}", params)]
    for name in args.doc:
        doc_ids += [r["id"] for r in db.q("SELECT id FROM kdocs WHERE name=?", (name,))]
    total["documents"] = len(set(doc_ids))
    if not args.dry_run:
        for d in set(doc_ids):
            db.run("DELETE FROM kchunks WHERE doc_id=?", (d,))
            db.run("DELETE FROM kdocs WHERE id=?", (d,))

    sources = db.q(f"SELECT id, url FROM ksources WHERE {like}", params)
    total["databases"] = len(sources)
    if not args.dry_run:
        import dbsource

        for s in sources:
            row = db.q("SELECT user FROM ksources WHERE id=?", (s["id"],))
            dbsource.delete_source(row[0]["user"], s["id"])

    threads = 0
    if db.CHECKPOINT_PATH.exists():
        con = sqlite3.connect(db.CHECKPOINT_PATH)
        tlike = " OR ".join(["thread_id LIKE ?"] * len(prefixes))
        for table in ("checkpoints", "writes"):
            try:
                n = con.execute(f"SELECT COUNT(*) FROM {table} WHERE {tlike}", params).fetchone()[0]
                threads += n
                if not args.dry_run and n:
                    con.execute(f"DELETE FROM {table} WHERE {tlike}", params)
            except sqlite3.OperationalError:
                pass
        con.commit()
        con.close()
    total["chat checkpoints"] = threads

    verb = "would remove" if args.dry_run else "removed"
    for k, v in total.items():
        print(f"{verb:12s} {v:5d}  {k}")
    print("\nUsers kept:", [r["user"] for r in db.q("SELECT DISTINCT user FROM usage")] or "none recorded")


if __name__ == "__main__":
    main()
