"""Build the KQL database schema file (DatabaseSchema.kql) from the kql/*.kql scripts.

kql/ stays the source. Each file there is one `.execute database script` command. A Fabric KQL database
Git item holds a plain list of commands, and only some command types. This module strips the wrapper and
drops the unsupported commands. It returns the dropped commands so that nobody loses one by accident.
Supported commands: https://learn.microsoft.com/en-us/fabric/real-time-intelligence/git-eventhouse-kql-database
"""
from __future__ import annotations

import re
from pathlib import Path

SUPPORTED = [
    re.compile(r"^\.create-merge table\b"),
    re.compile(r"^\.create-or-alter function\b"),
    re.compile(r"^\.alter table \S+ policy update\b"),
    re.compile(r"^\.alter column \S+ policy encoding\b"),
    re.compile(r"^\.create-or-alter materialized-view\b"),
    re.compile(r"^\.create-or-alter table \S+ ingestion \w+ mapping\b"),
]
WRAPPER = re.compile(r"^\.execute database script\b")
HEADER = (
    "// Generated from kql/*.kql by scripts/fabric/build_fabric_items.py. Do not edit here.\n"
    "// Fabric runs this script when the KQL database is synced or deployed.\n"
)


def _blocks(text: str) -> list[list[str]]:
    """Split into command blocks. A block starts at a line that begins with a dot."""
    blocks: list[list[str]] = []
    for line in text.splitlines():
        if line.startswith("."):
            blocks.append([line])
        elif blocks:
            blocks[-1].append(line)
        else:
            blocks.append([line])
    return blocks


def build(kql_dir: Path) -> tuple[str, list[str]]:
    kept: list[str] = [HEADER]
    dropped: list[str] = []
    for path in sorted(Path(kql_dir).glob("0*.kql")):
        for block in _blocks(path.read_text(encoding="utf-8")):
            head = block[0]
            if WRAPPER.match(head):
                continue
            if head.startswith(".") and not any(p.match(head) for p in SUPPORTED):
                dropped.append(head)
                continue
            kept.append("\n".join(block))
    return "\n".join(kept).rstrip() + "\n", dropped
