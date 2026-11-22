#!/usr/bin/env python3
"""Validate the n8n workflow exports and scan the repository for secrets / personal data.

Usage:
    python scripts/validate_repo.py                  # run from anywhere, checks the whole repo
    EXTRA_DENYLIST="acme corp,john doe" python scripts/validate_repo.py
        # optional, local-only: extra case-insensitive terms that must never appear
        # (keep real names out of committed files, so pass them through the environment)

Exit code 0 = clean, 1 = problems found.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SELF = Path(__file__).resolve()
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", ".venv", "venv"}
TEXT_SUFFIXES = {
    ".json", ".md", ".py", ".sql", ".yml", ".yaml", ".txt", ".example", ".sh", ".toml", ".cfg", ".ini", "",
}
PLACEHOLDER_CRED_ID = "REPLACE_WITH_YOUR_CREDENTIAL"
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

# --- secret / PII patterns ----------------------------------------------------
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("Slack token", re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}|xapp-[A-Za-z0-9-]{10,}")),
    ("Google API key", re.compile(r"AIza[0-9A-Za-z_-]{30,}")),
    ("Google OAuth client secret", re.compile(r"GOCSPX-[A-Za-z0-9_-]{10,}")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("AWS access key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Bearer token", re.compile(r"Bearer [A-Za-z0-9._~+/-]{25,}")),
    ("Real WhatsApp group JID", re.compile(r"(?<!\d)(?!120363000000000\d{3}@)\d{10,}(?:-\d+)?@g\.us")),
    ("Real WhatsApp user JID", re.compile(r"(?<!\d)(?!1555000000\d@)\d{10,15}@s\.whatsapp\.net")),
    ("Phone-number-like value", re.compile(r"(?<![\w.-])(?!1555000000\d(?!\d))\+?(?:92|91|44|1)\d{9,11}(?![\w-])")),
    ("Real Slack channel ID", re.compile(r"\bC0(?!EXAMPL)[A-Z0-9]{9}\b")),
    ("Google Sheet link", re.compile(r"docs\.google\.com/spreadsheets/d/(?!YOUR_SPREADSHEET_ID)[A-Za-z0-9_-]{10,}")),
    ("Cloudflare quick-tunnel host", re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+\.trycloudflare\.com")),
    (
        "E-mail address",
        re.compile(
            r"[A-Za-z0-9._%+-]+@(?!example\.(?:com|org)\b|users\.noreply\.github\.com\b|s\.whatsapp\.net\b|g\.us\b)"
            r"[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}"
        ),
    ),
]

FORBIDDEN_FILES = {".env", "credentials.json", "full_pmo_backup.sql", "config"}


def iter_text_files():
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.is_file() and path != SELF and path.suffix.lower() in TEXT_SUFFIXES:
            yield path


def check_workflow(path: Path, problems: list[str]) -> None:
    rel = path.relative_to(ROOT)
    try:
        wf = json.loads(path.read_text(encoding="utf-8"))
    except Exception as err:  # noqa: BLE001
        problems.append(f"{rel}: invalid JSON ({err})")
        return
    for key in ("name", "nodes", "connections"):
        if key not in wf:
            problems.append(f"{rel}: missing '{key}'")
    if wf.get("active") is not False:
        problems.append(f"{rel}: must be exported with \"active\": false")
    for leaked in ("id", "versionId", "pinData", "meta"):
        if wf.get(leaked):
            problems.append(f"{rel}: remove '{leaked}' (instance specific)")
    names = {n.get("name") for n in wf.get("nodes", [])}
    for src, outputs in wf.get("connections", {}).items():
        if src not in names:
            problems.append(f"{rel}: connection from unknown node '{src}'")
        for branches in outputs.values():
            for branch in branches:
                for link in branch:
                    if link["node"] not in names:
                        problems.append(f"{rel}: connection to unknown node '{link['node']}'")
    for node in wf.get("nodes", []):
        for ctype, cred in (node.get("credentials") or {}).items():
            if cred.get("id") != PLACEHOLDER_CRED_ID:
                problems.append(f"{rel}: node '{node['name']}' has a real credential id for {ctype}")
        if node.get("type", "").endswith(".webhook") and UUID_RE.match(str(node["parameters"].get("path", ""))):
            problems.append(f"{rel}: webhook '{node['name']}' uses an unguessable instance-specific path")


def main() -> int:
    problems: list[str] = []

    workflows = sorted((ROOT / "workflows").rglob("*.json"))
    if not workflows:
        problems.append("no workflow files found under workflows/")
    for wf in workflows:
        check_workflow(wf, problems)

    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.is_file() and path.name in FORBIDDEN_FILES:
            problems.append(f"{path.relative_to(ROOT)}: file must never be committed")

    extra = [t.strip().lower() for t in os.getenv("EXTRA_DENYLIST", "").split(",") if t.strip()]
    for path in iter_text_files():
        rel = path.relative_to(ROOT)
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in PATTERNS:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                problems.append(f"{rel}:{line}: {label}")
        lowered = text.lower()
        for term in extra:
            if term in lowered:
                problems.append(f"{rel}: contains denylisted term '{term}'")

    if problems:
        print(f"FAILED: {len(problems)} problem(s)\n")
        for p in problems:
            print(" -", p)
        return 1
    print(f"OK: {len(workflows)} workflow files valid; no secrets or personal data found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
