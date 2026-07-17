#!/usr/bin/env python3
"""Audit ComfyUI workflow dependencies against a running ComfyUI instance.

Uses only the Python standard library. It reports node types referenced by each
workflow that are not currently registered by ComfyUI, and flags token-shaped
or secret-keyed values without printing the full value.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

FRONTEND_ONLY_NODE_TYPES = {
    "Note",
    "PrimitiveNode",
    "Reroute",
}

TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{20,}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")),
    ("OpenAI-style token", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("Bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}\b", re.I)),
)

SECRET_KEY_PATTERN = re.compile(
    r"(?:api[_-]?key|access[_-]?token|auth[_-]?token|bearer|password|secret|token)$",
    re.I,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "workflow_dir",
        nargs="?",
        type=Path,
        default=Path("workflows"),
        help="workflow directory (default: workflows)",
    )
    parser.add_argument(
        "--url",
        default="http://127.0.0.1:8189/object_info",
        help="ComfyUI object_info URL",
    )
    parser.add_argument(
        "--workflow",
        action="append",
        default=[],
        help="audit only this workflow filename; may be repeated",
    )
    return parser.parse_args()


def load_registered_node_types(url: str) -> set[str]:
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read {url}: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected object_info response from {url}")

    return set(payload)


def iter_nodes(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        nodes = value.get("nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if isinstance(node, dict) and isinstance(node.get("type"), str):
                    yield node
        for child in value.values():
            yield from iter_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_nodes(child)


def redact(value: str) -> str:
    if len(value) <= 10:
        return f"<redacted length={len(value)}>"
    return f"{value[:4]}...{value[-4:]} (length={len(value)})"


def iter_secret_findings(value: Any, path: str = "$") -> Iterator[tuple[str, str, str]]:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if (
                isinstance(child, str)
                and child.strip()
                and SECRET_KEY_PATTERN.search(str(key))
            ):
                yield ("secret-keyed value", child_path, redact(child.strip()))
            yield from iter_secret_findings(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_secret_findings(child, f"{path}[{index}]")
    elif isinstance(value, str):
        for label, pattern in TOKEN_PATTERNS:
            for match in pattern.finditer(value):
                yield (label, path, redact(match.group(0)))


def workflow_paths(root: Path, selected: list[str]) -> list[Path]:
    if selected:
        paths = [root / name for name in selected]
    else:
        paths = sorted(root.rglob("*.json"))

    missing_files = [path for path in paths if not path.is_file()]
    if missing_files:
        names = ", ".join(str(path) for path in missing_files)
        raise RuntimeError(f"workflow file not found: {names}")

    return paths


def main() -> int:
    args = parse_args()

    try:
        registered = load_registered_node_types(args.url)
        paths = workflow_paths(args.workflow_dir, args.workflow)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Registered ComfyUI node types: {len(registered)}")
    print(f"Workflow files examined: {len(paths)}")

    workflows_with_missing = 0
    all_missing: set[str] = set()
    secret_count = 0

    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"\n{path}: ERROR: {exc}")
            continue

        node_types = {
            node["type"]
            for node in iter_nodes(data)
            if node["type"] not in FRONTEND_ONLY_NODE_TYPES
        }
        missing = sorted(node_types - registered)
        secrets = list(iter_secret_findings(data))

        if not missing and not secrets and args.workflow:
            print(f"\n{path}: no missing node types or obvious secret values")
            continue

        if missing or secrets:
            print(f"\n{path}")

        if missing:
            workflows_with_missing += 1
            all_missing.update(missing)
            print("  Missing node types:")
            for node_type in missing:
                print(f"    - {node_type}")

        if secrets:
            secret_count += len(secrets)
            print("  Possible secrets, values redacted:")
            for label, location, value in secrets:
                print(f"    - {label}: {location}: {value}")

    print("\n=== Summary ===")
    print(f"Workflows with missing node types: {workflows_with_missing}")
    print(f"Unique missing node types: {len(all_missing)}")
    print(f"Possible secret findings: {secret_count}")

    if all_missing:
        print("\nAll missing node types:")
        for node_type in sorted(all_missing):
            print(f"  - {node_type}")

    return 2 if secret_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
