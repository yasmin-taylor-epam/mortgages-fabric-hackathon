"""Generate the Fabric items that come from other sources in this repository.

- fabric/items/<name>.Notebook/      from notebooks/*.ipynb
- DatabaseSchema.kql of the KQL database from kql/*.kql

Run from anywhere:  python scripts/fabric/build_fabric_items.py          (write)
                    python scripts/fabric/build_fabric_items.py --check  (exit 1 if a file is out of date)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hbfabric import ids, kql, notebooks  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
ITEMS = REPO / "fabric" / "items"
KQL_SCHEMA = f"{ids.EVENTHOUSE}.Eventhouse/.children/{ids.KQL_DATABASE}.KQLDatabase/DatabaseSchema.kql"
# Commands in kql/ that a KQL database Git item cannot hold. The deploy docs list how to run them by hand.
EXPECTED_DROPPED = [".alter-merge table RawEventsLanding policy retention softdelete = 7d"]


def generated_files(repo: Path = REPO) -> tuple[dict[str, str], list[str]]:
    files = notebooks.build_all(repo / "notebooks")
    schema, dropped = kql.build(repo / "kql")
    files[KQL_SCHEMA] = schema
    return files, dropped


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="Do not write. Exit 1 if a generated file differs.")
    args = ap.parse_args(argv)
    files, dropped = generated_files()
    stale = []
    for rel, text in files.items():
        path = ITEMS / rel
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != text:
            stale.append(rel)
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8", newline="\n")
    if dropped != EXPECTED_DROPPED:
        print("KQL commands left out of DatabaseSchema.kql changed:", dropped, file=sys.stderr)
        return 1
    for rel in stale:
        print(("out of date: " if args.check else "wrote: ") + rel)
    if args.check and stale:
        print("Run: python scripts/fabric/build_fabric_items.py", file=sys.stderr)
        return 1
    print(f"{len(files)} generated files {'checked' if args.check else 'up to date'}; {len(dropped)} KQL command left out.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
