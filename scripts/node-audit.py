#!/usr/bin/env python3
"""Audit installed ComfyUI node-pack conflicts against a running instance.

Uses only the Python standard library. The audit combines:
- installed custom-node directories on the host,
- ComfyUI Manager's extension-node-map.json,
- the running ComfyUI /object_info registry,
- and optionally saved workflow usage.

Manager's node map is treated as advisory metadata. "Installed conflict" means
Manager knows multiple extensions that claim the same node ID and multiple
matching extension directories are installed locally. "Runtime owner" is the
currently registered Python module reported by /object_info.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FRONTEND_ONLY_NODE_TYPES = {
    "Note",
    "PrimitiveNode",
    "Reroute",
}

INACTIVE_NODE_MODES = {2, 4}


@dataclass(frozen=True)
class ManagerEntry:
    repo: str
    title: str
    node_ids: tuple[str, ...]


@dataclass(frozen=True)
class Conflict:
    node_id: str
    installed_packs: tuple[str, ...]
    runtime_owner: str | None
    workflow_files: tuple[str, ...]
    inactive_workflow_files: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Report ComfyUI node-ID conflicts where multiple conflicting "
            "Manager extensions are actually installed locally."
        )
    )
    parser.add_argument(
        "--custom-nodes",
        type=Path,
        help=(
            "host custom_nodes directory; default is "
            "COMFYUI_DATA_PATH/custom_nodes or ./data/custom_nodes"
        ),
    )
    parser.add_argument(
        "--manager-map",
        type=Path,
        help=(
            "Manager extension-node-map.json; default is the newest cached map "
            "under COMFYUI_DATA_PATH/user/__manager/cache"
        ),
    )
    parser.add_argument(
        "--url",
        help=(
            "ComfyUI object_info URL; default is "
            "http://127.0.0.1:${COMFYUI_PORT:-8188}/object_info"
        ),
    )
    parser.add_argument(
        "--workflows",
        nargs="?",
        const="__ENV__",
        metavar="PATH",
        help=(
            "also report saved-workflow usage; optionally provide a workflow "
            "directory. Without PATH, use COMFYUI_WORKFLOWS_PATH or ./workflows"
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="emit machine-readable JSON instead of the human report",
    )
    return parser.parse_args()


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def env_values(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def resolve_host_path(value: str, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve(strict=False)


def default_paths(args: argparse.Namespace) -> tuple[Path, Path, str, Path | None]:
    root = repo_root()
    env = env_values(root / ".env")

    data_path = resolve_host_path(env.get("COMFYUI_DATA_PATH", "./data"), root)
    custom_nodes = (
        args.custom_nodes.resolve(strict=False)
        if args.custom_nodes
        else data_path / "custom_nodes"
    )

    if args.manager_map:
        manager_map = args.manager_map.resolve(strict=False)
    else:
        cache_root = data_path / "user" / "__manager" / "cache"
        candidates = sorted(
            cache_root.glob("*_extension-node-map.json"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        if not candidates:
            raise RuntimeError(
                f"no cached Manager extension-node-map.json found under {cache_root}"
            )
        manager_map = candidates[0]

    port = env.get("COMFYUI_PORT", "8188")
    url = args.url or f"http://127.0.0.1:{port}/object_info"

    workflow_root: Path | None = None
    if args.workflows is not None:
        if args.workflows == "__ENV__":
            workflow_root = resolve_host_path(
                env.get("COMFYUI_WORKFLOWS_PATH", "./workflows"),
                root,
            )
        else:
            workflow_root = resolve_host_path(args.workflows, Path.cwd())

    return custom_nodes, manager_map, url, workflow_root


def normalize_pack_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def repo_basename(repo: str) -> str:
    parsed = urllib.parse.urlparse(repo)
    path = parsed.path.rstrip("/")
    name = path.rsplit("/", 1)[-1] if path else repo.rstrip("/").rsplit("/", 1)[-1]
    if name.endswith(".git"):
        name = name[:-4]
    return name


def installed_pack_names(custom_nodes: Path) -> list[str]:
    if not custom_nodes.is_dir():
        raise RuntimeError(f"custom_nodes directory not found: {custom_nodes}")

    return sorted(
        (
            path.name
            for path in custom_nodes.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ),
        key=str.casefold,
    )


def load_manager_entries(path: Path) -> list[ManagerEntry]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read Manager node map {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected Manager node-map structure in {path}")

    entries: list[ManagerEntry] = []
    for repo, value in payload.items():
        if not isinstance(repo, str) or not isinstance(value, list) or not value:
            continue

        node_ids = value[0]
        metadata = value[1] if len(value) > 1 and isinstance(value[1], dict) else {}
        if not isinstance(node_ids, list):
            continue

        clean_node_ids = tuple(
            node_id for node_id in node_ids if isinstance(node_id, str)
        )
        title = metadata.get("title_aux")
        if not isinstance(title, str) or not title.strip():
            title = repo_basename(repo)

        entries.append(
            ManagerEntry(
                repo=repo,
                title=title,
                node_ids=clean_node_ids,
            )
        )
    return entries


def match_entries_to_installed(
    entries: list[ManagerEntry],
    installed: list[str],
) -> tuple[dict[ManagerEntry, str], dict[str, list[ManagerEntry]]]:
    by_normalized: dict[str, list[str]] = defaultdict(list)
    for pack in installed:
        by_normalized[normalize_pack_name(pack)].append(pack)

    matched: dict[ManagerEntry, str] = {}
    ambiguous: dict[str, list[ManagerEntry]] = defaultdict(list)

    for entry in entries:
        aliases = {
            normalize_pack_name(repo_basename(entry.repo)),
            normalize_pack_name(entry.title),
        }
        candidates: set[str] = set()
        for alias in aliases:
            candidates.update(by_normalized.get(alias, []))

        if len(candidates) == 1:
            matched[entry] = next(iter(candidates))
        elif len(candidates) > 1:
            for candidate in sorted(candidates, key=str.casefold):
                ambiguous[candidate].append(entry)

    return matched, ambiguous


def load_object_info(url: str) -> dict[str, dict[str, Any]]:
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            payload = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read {url}: {exc}") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"unexpected object_info response from {url}")

    return {
        node_id: info
        for node_id, info in payload.items()
        if isinstance(node_id, str) and isinstance(info, dict)
    }


def runtime_pack_from_module(module: Any) -> str | None:
    if not isinstance(module, str):
        return None
    prefix = "custom_nodes."
    if not module.startswith(prefix):
        return None
    remainder = module[len(prefix) :]
    if not remainder:
        return None
    return remainder.split(".", 1)[0]


def runtime_node_owners(
    object_info: dict[str, dict[str, Any]],
) -> tuple[dict[str, str], dict[str, set[str]]]:
    node_owner: dict[str, str] = {}
    pack_nodes: dict[str, set[str]] = defaultdict(set)

    for node_id, info in object_info.items():
        pack = runtime_pack_from_module(info.get("python_module"))
        if pack is None:
            continue
        node_owner[node_id] = pack
        pack_nodes[pack].add(node_id)

    return node_owner, pack_nodes


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


def workflow_usage(
    root: Path,
) -> tuple[dict[str, set[str]], dict[str, set[str]], list[tuple[str, str]]]:
    if not root.is_dir():
        raise RuntimeError(f"workflow directory not found: {root}")

    active: dict[str, set[str]] = defaultdict(set)
    inactive: dict[str, set[str]] = defaultdict(set)
    errors: list[tuple[str, str]] = []

    for path in sorted(root.rglob("*.json")):
        relative = str(path.relative_to(root))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append((relative, str(exc)))
            continue

        active_in_file: set[str] = set()
        inactive_in_file: set[str] = set()

        for node in iter_nodes(data):
            node_type = node["type"]
            if node_type in FRONTEND_ONLY_NODE_TYPES:
                continue
            mode = node.get("mode", 0)
            if mode in INACTIVE_NODE_MODES:
                inactive_in_file.add(node_type)
            else:
                active_in_file.add(node_type)

        for node_type in active_in_file:
            active[node_type].add(relative)
        for node_type in inactive_in_file - active_in_file:
            inactive[node_type].add(relative)

    return active, inactive, errors


def build_conflicts(
    entries: list[ManagerEntry],
    matched: dict[ManagerEntry, str],
    runtime_owners: dict[str, str],
    active_usage: dict[str, set[str]],
    inactive_usage: dict[str, set[str]],
) -> tuple[list[Conflict], dict[str, int]]:
    global_claimants: dict[str, list[ManagerEntry]] = defaultdict(list)
    installed_claimants: dict[str, set[str]] = defaultdict(set)

    for entry in entries:
        for node_id in entry.node_ids:
            global_claimants[node_id].append(entry)

        installed_pack = matched.get(entry)
        if installed_pack is None:
            continue
        for node_id in entry.node_ids:
            installed_claimants[node_id].add(installed_pack)

    conflicts: list[Conflict] = []
    for node_id, packs in installed_claimants.items():
        if len(packs) < 2:
            continue
        conflicts.append(
            Conflict(
                node_id=node_id,
                installed_packs=tuple(sorted(packs, key=str.casefold)),
                runtime_owner=runtime_owners.get(node_id),
                workflow_files=tuple(sorted(active_usage.get(node_id, set()))),
                inactive_workflow_files=tuple(
                    sorted(inactive_usage.get(node_id, set()))
                ),
            )
        )

    global_conflict_counts: dict[str, int] = defaultdict(int)
    for node_id, claimants in global_claimants.items():
        if len(claimants) < 2:
            continue
        packs_seen: set[str] = set()
        for entry in claimants:
            installed_pack = matched.get(entry)
            if installed_pack is not None:
                packs_seen.add(installed_pack)
        for pack in packs_seen:
            global_conflict_counts[pack] += 1

    return sorted(conflicts, key=lambda item: item.node_id.casefold()), global_conflict_counts


def installed_conflict_counts(conflicts: list[Conflict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for conflict in conflicts:
        for pack in conflict.installed_packs:
            counts[pack] += 1
    return counts


def workflow_conflict_counts(conflicts: list[Conflict]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for conflict in conflicts:
        if not conflict.workflow_files:
            continue
        for pack in conflict.installed_packs:
            counts[pack] += 1
    return counts


def json_report(
    custom_nodes: Path,
    manager_map: Path,
    url: str,
    workflow_root: Path | None,
    installed: list[str],
    matched: dict[ManagerEntry, str],
    ambiguous: dict[str, list[ManagerEntry]],
    pack_nodes: dict[str, set[str]],
    conflicts: list[Conflict],
    global_conflict_counts: dict[str, int],
    workflow_errors: list[tuple[str, str]],
) -> dict[str, Any]:
    installed_counts = installed_conflict_counts(conflicts)
    workflow_counts = workflow_conflict_counts(conflicts)
    matched_packs = set(matched.values())
    installed_set = set(installed)

    return {
        "sources": {
            "custom_nodes": str(custom_nodes),
            "manager_map": str(manager_map),
            "object_info": url,
            "workflows": str(workflow_root) if workflow_root else None,
        },
        "summary": {
            "installed_packs": len(installed),
            "manager_matched_packs": len(matched_packs),
            "runtime_registered_packs": len(
                {pack for pack in pack_nodes if pack in installed_set}
            ),
            "installed_conflict_node_ids": len(conflicts),
            "workflow_used_conflict_node_ids": sum(
                1 for conflict in conflicts if conflict.workflow_files
            ),
            "workflow_errors": len(workflow_errors),
        },
        "packs": [
            {
                "name": pack,
                "manager_matched": pack in matched_packs,
                "runtime_registered_nodes": len(pack_nodes.get(pack, set())),
                "known_global_conflict_nodes": global_conflict_counts.get(pack, 0),
                "installed_conflict_nodes": installed_counts.get(pack, 0),
                "workflow_used_conflict_nodes": workflow_counts.get(pack, 0),
            }
            for pack in installed
        ],
        "conflicts": [
            {
                "node_id": conflict.node_id,
                "installed_packs": list(conflict.installed_packs),
                "runtime_owner": conflict.runtime_owner,
                "workflow_count": len(conflict.workflow_files),
                "workflow_files": list(conflict.workflow_files),
                "inactive_workflow_count": len(conflict.inactive_workflow_files),
                "inactive_workflow_files": list(conflict.inactive_workflow_files),
            }
            for conflict in conflicts
        ],
        "ambiguous_manager_matches": {
            pack: [
                {"title": entry.title, "repo": entry.repo}
                for entry in entries
            ]
            for pack, entries in ambiguous.items()
        },
        "workflow_errors": [
            {"file": filename, "error": error}
            for filename, error in workflow_errors
        ],
    }


def print_human_report(
    custom_nodes: Path,
    manager_map: Path,
    url: str,
    workflow_root: Path | None,
    installed: list[str],
    matched: dict[ManagerEntry, str],
    ambiguous: dict[str, list[ManagerEntry]],
    pack_nodes: dict[str, set[str]],
    conflicts: list[Conflict],
    global_conflict_counts: dict[str, int],
    workflow_errors: list[tuple[str, str]],
) -> None:
    installed_set = set(installed)
    matched_packs = set(matched.values())
    runtime_packs = {pack for pack in pack_nodes if pack in installed_set}
    installed_counts = installed_conflict_counts(conflicts)
    workflow_counts = workflow_conflict_counts(conflicts)

    print(f"Custom nodes: {custom_nodes}")
    print(f"Manager map: {manager_map}")
    print(f"Runtime registry: {url}")
    if workflow_root is not None:
        print(f"Workflows: {workflow_root}")

    print("\n=== Summary ===")
    print(f"Installed node-pack directories: {len(installed)}")
    print(f"Matched to Manager node map:     {len(matched_packs)}")
    print(f"Runtime-registered node packs:   {len(runtime_packs)}")
    print(f"Installed conflict node IDs:     {len(conflicts)}")
    if workflow_root is not None:
        print(
            "Workflow-used conflict node IDs: "
            f"{sum(1 for conflict in conflicts if conflict.workflow_files)}"
        )
        print(f"Unreadable workflow files:       {len(workflow_errors)}")

    print("\n=== Installed pack summary ===")
    for pack in installed:
        runtime_count = len(pack_nodes.get(pack, set()))
        manager_state = "yes" if pack in matched_packs else "no"
        print(pack)
        print(f"  Manager map match:          {manager_state}")
        print(f"  Runtime registered nodes:   {runtime_count}")
        print(f"  Known global conflicts:     {global_conflict_counts.get(pack, 0)}")
        print(f"  Installed conflicts:        {installed_counts.get(pack, 0)}")
        if workflow_root is not None:
            print(
                "  Workflow-used conflicts:    "
                f"{workflow_counts.get(pack, 0)}"
            )

    print("\n=== Installed node-ID conflicts ===")
    if not conflicts:
        print("No Manager-known node-ID conflicts were found between installed packs.")
    else:
        for conflict in conflicts:
            print(f"\n{conflict.node_id}")
            print(f"  Installed claimants: {', '.join(conflict.installed_packs)}")
            print(
                "  Runtime owner: "
                f"{conflict.runtime_owner or 'not registered / unknown'}"
            )
            if workflow_root is not None:
                print(f"  Active workflow count: {len(conflict.workflow_files)}")
                for filename in conflict.workflow_files:
                    print(f"    - {filename}")
                if conflict.inactive_workflow_files:
                    print(
                        "  Muted/bypassed-only workflow count: "
                        f"{len(conflict.inactive_workflow_files)}"
                    )
                    for filename in conflict.inactive_workflow_files:
                        print(f"    - {filename}")

    if ambiguous:
        print("\n=== Ambiguous Manager-to-directory matches ===")
        for pack in sorted(ambiguous, key=str.casefold):
            print(pack)
            for entry in ambiguous[pack]:
                print(f"  - {entry.title}: {entry.repo}")

    if workflow_errors:
        print("\n=== Workflow read errors ===")
        for filename, error in workflow_errors:
            print(f"{filename}: {error}")


def main() -> int:
    args = parse_args()

    try:
        custom_nodes, manager_map, url, workflow_root = default_paths(args)
        installed = installed_pack_names(custom_nodes)
        entries = load_manager_entries(manager_map)
        matched, ambiguous = match_entries_to_installed(entries, installed)
        object_info = load_object_info(url)
        runtime_owners, pack_nodes = runtime_node_owners(object_info)

        active_usage: dict[str, set[str]] = {}
        inactive_usage: dict[str, set[str]] = {}
        workflow_errors: list[tuple[str, str]] = []
        if workflow_root is not None:
            active_usage, inactive_usage, workflow_errors = workflow_usage(workflow_root)

        conflicts, global_conflict_counts = build_conflicts(
            entries,
            matched,
            runtime_owners,
            active_usage,
            inactive_usage,
        )
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        report = json_report(
            custom_nodes,
            manager_map,
            url,
            workflow_root,
            installed,
            matched,
            ambiguous,
            pack_nodes,
            conflicts,
            global_conflict_counts,
            workflow_errors,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human_report(
            custom_nodes,
            manager_map,
            url,
            workflow_root,
            installed,
            matched,
            ambiguous,
            pack_nodes,
            conflicts,
            global_conflict_counts,
            workflow_errors,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
