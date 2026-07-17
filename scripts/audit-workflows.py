#!/usr/bin/env python3
"""Audit ComfyUI workflow dependencies against a running ComfyUI instance.

Uses only the Python standard library. It reports active node types referenced by
workflows that are not currently registered by ComfyUI, separates nodes that are
muted or bypassed, and flags token-shaped or secret-keyed values without printing
the full value.
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

NODE_MODE_NAMES = {
    0: "always",
    1: "on event",
    2: "never/muted",
    3: "on trigger",
    4: "bypass",
}
INACTIVE_NODE_MODES = {2, 4}

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


def node_mode(node: dict[str, Any]) -> int:
    value = node.get("mode", 0)
    return value if isinstance(value, int) else 0


def node_descriptor(node: dict[str, Any]) -> str:
    mode = node_mode(node)
    mode_name = NODE_MODE_NAMES.get(mode, "unknown")
    return f"id={node.get('id', '?')} mode={mode} ({mode_name})"


def missing_node_occurrences(
    data: Any,
    registered: set[str],
) -> tuple[dict[str, list[str]], dict[str, list[str]]]:
    active: dict[str, list[str]] = {}
    inactive: dict[str, list[str]] = {}

    for node in iter_nodes(data):
        node_type = node["type"]
        if node_type in FRONTEND_ONLY_NODE_TYPES or node_type in registered:
            continue

        target = inactive if node_mode(node) in INACTIVE_NODE_MODES else active
        target.setdefault(node_type, []).append(node_descriptor(node))

    # A type is required when at least one occurrence is active, even if another
    # occurrence of the same type is bypassed elsewhere in the workflow.
    inactive_only = {
        node_type: occurrences
        for node_type, occurrences in inactive.items()
        if node_type not in active
    }
    return active, inactive_only


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


def print_occurrences(title: str, occurrences: dict[str, list[str]]) -> None:
    print(title)
    for node_type in sorted(occurrences):
        details = "; ".join(occurrences[node_type])
        print(f"    - {node_type} [{details}]")


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

    workflows_with_active_missing = 0
    all_active_missing: set[str] = set()
    all_inactive_only_missing: set[str] = set()
    secret_count = 0

    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"\n{path}: ERROR: {exc}")
            continue

        active_missing, inactive_only_missing = missing_node_occurrences(data, registered)
        secrets = list(iter_secret_findings(data))

        if not active_missing and not inactive_only_missing and not secrets and args.workflow:
            print(f"\n{path}: no missing node types or obvious secret values")
            continue

        if active_missing or inactive_only_missing or secrets:
            print(f"\n{path}")

        if active_missing:
            workflows_with_active_missing += 1
            all_active_missing.update(active_missing)
            print_occurrences("  Active missing node types:", active_missing)

        if inactive_only_missing:
            all_inactive_only_missing.update(inactive_only_missing)
            print_occurrences(
                "  Missing only in muted or bypassed nodes:",
                inactive_only_missing,
            )

        if secrets:
            secret_count += len(secrets)
            print("  Possible secrets, values redacted:")
            for label, location, value in secrets:
                print(f"    - {label}: {location}: {value}")

    print("\n=== Summary ===")
    print(f"Workflows with active missing node types: {workflows_with_active_missing}")
    print(f"Unique active missing node types: {len(all_active_missing)}")
    print(
        "Unique missing types used only by muted/bypassed nodes: "
        f"{len(all_inactive_only_missing - all_active_missing)}"
    )
    print(f"Possible secret findings: {secret_count}")

    if all_active_missing:
        print("\nAll active missing node types:")
        for node_type in sorted(all_active_missing):
            print(f"  - {node_type}")

    inactive_summary = all_inactive_only_missing - all_active_missing
    if inactive_summary:
        print("\nMissing only in muted or bypassed nodes:")
        for node_type in sorted(inactive_summary):
            print(f"  - {node_type}")

    return 2 if secret_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
