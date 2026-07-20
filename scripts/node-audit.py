#!/usr/bin/env python3
"""Audit installed ComfyUI node-pack overlaps and saved-workflow usage.

Uses only the Python standard library. Manager's extension-node map is advisory
static-analysis metadata. A Manager-known installed overlap means multiple
Manager entries associated with locally installed packs list the same node ID.
That does not prove both packs register that node at runtime. /object_info is
used to report the current runtime owner when available.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FRONTEND_ONLY_NODE_TYPES = {"Note", "PrimitiveNode", "Reroute"}
INACTIVE_NODE_MODES = {2, 4}


@dataclass(frozen=True)
class ManagerEntry:
    repo: str
    title: str
    node_ids: tuple[str, ...]


@dataclass(frozen=True)
class Overlap:
    node_id: str
    installed_claimants: tuple[str, ...]
    runtime_owner: str | None
    workflow_files: tuple[str, ...]
    inactive_workflow_files: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Report Manager-known node-ID overlaps between locally installed "
            "ComfyUI node packs, current runtime ownership, and optional workflow usage."
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
            "Manager extension-node-map.json; default is newest cached map under "
            "COMFYUI_DATA_PATH/user/__manager/cache"
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
        "--only-used-overlaps",
        action="store_true",
        help=(
            "with --workflows and --verbose, show only overlaps referenced by "
            "active saved workflows; compact output already focuses on used overlaps"
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="append full per-overlap details and workflow filenames",
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
                env.get("COMFYUI_WORKFLOWS_PATH", "./workflows"), root
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
    return name[:-4] if name.endswith(".git") else name


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
        entries.append(ManagerEntry(repo=repo, title=title, node_ids=clean_node_ids))
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
    if not isinstance(module, str) or not module.startswith("custom_nodes."):
        return None
    remainder = module[len("custom_nodes.") :]
    return remainder.split(".", 1)[0] if remainder else None


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


def build_overlaps(
    matched: dict[ManagerEntry, str],
    runtime_owners: dict[str, str],
    active_usage: dict[str, set[str]],
    inactive_usage: dict[str, set[str]],
) -> list[Overlap]:
    installed_claimants: dict[str, set[str]] = defaultdict(set)
    for entry, installed_pack in matched.items():
        for node_id in entry.node_ids:
            installed_claimants[node_id].add(installed_pack)

    overlaps: list[Overlap] = []
    for node_id, packs in installed_claimants.items():
        if len(packs) < 2:
            continue
        overlaps.append(
            Overlap(
                node_id=node_id,
                installed_claimants=tuple(sorted(packs, key=str.casefold)),
                runtime_owner=runtime_owners.get(node_id),
                workflow_files=tuple(sorted(active_usage.get(node_id, set()))),
                inactive_workflow_files=tuple(
                    sorted(inactive_usage.get(node_id, set()))
                ),
            )
        )
    return sorted(overlaps, key=lambda item: item.node_id.casefold())


def pack_workflow_usage(
    pack_nodes: dict[str, set[str]],
    active_usage: dict[str, set[str]],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    pack_used_nodes: dict[str, set[str]] = defaultdict(set)
    pack_workflows: dict[str, set[str]] = defaultdict(set)
    for pack, nodes in pack_nodes.items():
        for node_id in nodes:
            files = active_usage.get(node_id, set())
            if files:
                pack_used_nodes[pack].add(node_id)
                pack_workflows[pack].update(files)
    return pack_used_nodes, pack_workflows


def pack_overlap_stats(
    installed: list[str],
    overlaps: list[Overlap],
) -> dict[str, dict[str, int]]:
    stats = {
        pack: {
            "manager_overlaps": 0,
            "runtime_wins": 0,
            "other_owner": 0,
            "unregistered": 0,
            "workflow_used": 0,
        }
        for pack in installed
    }
    for overlap in overlaps:
        for pack in overlap.installed_claimants:
            if pack not in stats:
                continue
            stats[pack]["manager_overlaps"] += 1
            if overlap.runtime_owner is None:
                stats[pack]["unregistered"] += 1
            elif overlap.runtime_owner == pack:
                stats[pack]["runtime_wins"] += 1
            else:
                stats[pack]["other_owner"] += 1
            if overlap.workflow_files:
                stats[pack]["workflow_used"] += 1
    return stats


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))

    print("  ".join(header.ljust(widths[i]) for i, header in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))


def compact_overlap_groups(overlaps: list[Overlap]) -> list[list[str]]:
    grouped: dict[tuple[str, ...], list[Overlap]] = defaultdict(list)
    for overlap in overlaps:
        if overlap.workflow_files:
            grouped[overlap.installed_claimants].append(overlap)

    rows: list[list[str]] = []
    for claimants, items in sorted(grouped.items(), key=lambda pair: pair[0]):
        owners = Counter(
            item.runtime_owner for item in items if item.runtime_owner is not None
        )
        owner_text = ", ".join(
            f"{owner}:{count}" for owner, count in sorted(owners.items())
        ) or "-"
        unregistered = sum(1 for item in items if item.runtime_owner is None)
        rows.append(
            [
                " <> ".join(claimants),
                str(len(items)),
                owner_text,
                str(unregistered),
            ]
        )
    return rows


def json_report(
    custom_nodes: Path,
    manager_map: Path,
    url: str,
    workflow_root: Path | None,
    installed: list[str],
    matched: dict[ManagerEntry, str],
    ambiguous: dict[str, list[ManagerEntry]],
    pack_nodes: dict[str, set[str]],
    overlaps: list[Overlap],
    active_usage: dict[str, set[str]],
    workflow_errors: list[tuple[str, str]],
) -> dict[str, Any]:
    installed_set = set(installed)
    matched_packs = set(matched.values())
    runtime_packs = {pack for pack in pack_nodes if pack in installed_set}
    pack_used_nodes, pack_workflows = pack_workflow_usage(pack_nodes, active_usage)
    stats = pack_overlap_stats(installed, overlaps)
    unresolved_used = [
        overlap
        for overlap in overlaps
        if overlap.workflow_files and overlap.runtime_owner is None
    ]
    review_candidates = [
        pack
        for pack in installed
        if workflow_root is not None
        and pack_nodes.get(pack)
        and not pack_used_nodes.get(pack)
    ]

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
            "runtime_registered_packs": len(runtime_packs),
            "manager_known_installed_overlap_node_ids": len(overlaps),
            "runtime_registered_overlap_node_ids": sum(
                1 for overlap in overlaps if overlap.runtime_owner is not None
            ),
            "unregistered_overlap_node_ids": sum(
                1 for overlap in overlaps if overlap.runtime_owner is None
            ),
            "workflow_used_overlap_node_ids": sum(
                1 for overlap in overlaps if overlap.workflow_files
            ),
            "workflow_used_unregistered_overlap_node_ids": len(unresolved_used),
            "workflow_errors": len(workflow_errors),
        },
        "packs": [
            {
                "name": pack,
                "manager_matched": pack in matched_packs,
                "runtime_registered_nodes": len(pack_nodes.get(pack, set())),
                "workflow_used_runtime_nodes": len(pack_used_nodes.get(pack, set())),
                "workflow_files_using_runtime_nodes": len(
                    pack_workflows.get(pack, set())
                ),
                **stats[pack],
            }
            for pack in installed
        ],
        "review_candidates": review_candidates,
        "overlaps": [
            {
                "node_id": overlap.node_id,
                "installed_manager_claimants": list(overlap.installed_claimants),
                "runtime_owner": overlap.runtime_owner,
                "workflow_count": len(overlap.workflow_files),
                "workflow_files": list(overlap.workflow_files),
                "inactive_workflow_count": len(overlap.inactive_workflow_files),
                "inactive_workflow_files": list(overlap.inactive_workflow_files),
            }
            for overlap in overlaps
        ],
        "ambiguous_manager_matches": {
            pack: [{"title": entry.title, "repo": entry.repo} for entry in pack_entries]
            for pack, pack_entries in ambiguous.items()
        },
        "workflow_errors": [
            {"file": filename, "error": error}
            for filename, error in workflow_errors
        ],
    }


def print_compact_report(
    workflow_root: Path | None,
    installed: list[str],
    matched: dict[ManagerEntry, str],
    pack_nodes: dict[str, set[str]],
    overlaps: list[Overlap],
    active_usage: dict[str, set[str]],
    workflow_errors: list[tuple[str, str]],
) -> None:
    installed_set = set(installed)
    matched_packs = set(matched.values())
    runtime_packs = {pack for pack in pack_nodes if pack in installed_set}
    pack_used_nodes, pack_workflows = pack_workflow_usage(pack_nodes, active_usage)
    stats = pack_overlap_stats(installed, overlaps)

    registered_overlaps = sum(
        1 for overlap in overlaps if overlap.runtime_owner is not None
    )
    unregistered_overlaps = len(overlaps) - registered_overlaps
    used_overlaps = [overlap for overlap in overlaps if overlap.workflow_files]
    unresolved_used = [
        overlap for overlap in used_overlaps if overlap.runtime_owner is None
    ]

    print("=== Node audit ===")
    print(
        f"Packs: {len(installed)} installed | {len(matched_packs)} Manager-matched | "
        f"{len(runtime_packs)} runtime-registered"
    )
    print(
        f"Manager overlaps: {len(overlaps)} known | {registered_overlaps} registered | "
        f"{unregistered_overlaps} unregistered"
    )
    if workflow_root is not None:
        print(
            f"Workflow-used overlaps: {len(used_overlaps)} | "
            f"unregistered and used: {len(unresolved_used)} | "
            f"workflow read errors: {len(workflow_errors)}"
        )

    print("\n=== Packs ===")
    headers = ["Pack", "Live", "Used", "WFs", "MapOv", "Own", "Other", "Unreg", "UsedOv"]
    rows: list[list[str]] = []
    for pack in installed:
        rows.append(
            [
                pack,
                str(len(pack_nodes.get(pack, set()))),
                str(len(pack_used_nodes.get(pack, set()))) if workflow_root else "-",
                str(len(pack_workflows.get(pack, set()))) if workflow_root else "-",
                str(stats[pack]["manager_overlaps"]),
                str(stats[pack]["runtime_wins"]),
                str(stats[pack]["other_owner"]),
                str(stats[pack]["unregistered"]),
                str(stats[pack]["workflow_used"]) if workflow_root else "-",
            ]
        )
    print_table(headers, rows)
    print("Live=runtime nodes; Used=saved-workflow-used live nodes; WFs=workflow files")
    print("MapOv=Manager-known overlaps; Own/Other/Unreg=current runtime ownership; UsedOv=workflow-used overlaps")

    if workflow_root is not None:
        group_rows = compact_overlap_groups(overlaps)
        print("\n=== Workflow-relevant overlap groups ===")
        if group_rows:
            print_table(
                ["Installed Manager claimants", "UsedOv", "Runtime owner counts", "Unreg"],
                group_rows,
            )
        else:
            print("None.")

        if unresolved_used:
            print("\n=== Needs attention ===")
            for overlap in unresolved_used:
                print(
                    f"{overlap.node_id}: unregistered; workflows={len(overlap.workflow_files)}; "
                    f"claimants={', '.join(overlap.installed_claimants)}"
                )

        review_candidates = [
            pack
            for pack in installed
            if pack_nodes.get(pack) and not pack_used_nodes.get(pack)
        ]
        if review_candidates:
            print("\n=== Cleanup review candidates ===")
            print("No active saved-workflow references were found for these packs' live nodes:")
            for pack in review_candidates:
                print(f"  - {pack} ({len(pack_nodes.get(pack, set()))} live nodes)")
            print("Review only; this does not prove the pack is unused interactively or by unsaved/API workflows.")


def print_verbose_report(
    custom_nodes: Path,
    manager_map: Path,
    url: str,
    workflow_root: Path | None,
    ambiguous: dict[str, list[ManagerEntry]],
    overlaps: list[Overlap],
    workflow_errors: list[tuple[str, str]],
    only_used_overlaps: bool,
) -> None:
    print("\n=== Sources ===")
    print(f"Custom nodes: {custom_nodes}")
    print(f"Manager map: {manager_map}")
    print(f"Runtime registry: {url}")
    if workflow_root is not None:
        print(f"Workflows: {workflow_root}")

    print("\n=== Overlap details ===")
    selected = (
        [overlap for overlap in overlaps if overlap.workflow_files]
        if only_used_overlaps
        else overlaps
    )
    if not selected:
        print("None.")
    else:
        for overlap in selected:
            print(f"\n{overlap.node_id}")
            print(
                "  Installed Manager claimants: "
                f"{', '.join(overlap.installed_claimants)}"
            )
            print(
                "  Runtime owner: "
                f"{overlap.runtime_owner if overlap.runtime_owner else 'not registered'}"
            )
            if workflow_root is not None:
                print(f"  Active workflow count: {len(overlap.workflow_files)}")
                for filename in overlap.workflow_files:
                    print(f"    - {filename}")
                if overlap.inactive_workflow_files:
                    print(
                        "  Muted/bypassed-only workflow count: "
                        f"{len(overlap.inactive_workflow_files)}"
                    )
                    for filename in overlap.inactive_workflow_files:
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

    print("\nNote: Manager overlap data comes from static analysis and is advisory.")
    print(
        "A Manager-known overlap does not by itself prove that both packs register "
        "the node at runtime."
    )


def main() -> int:
    args = parse_args()
    if args.only_used_overlaps and args.workflows is None:
        print("ERROR: --only-used-overlaps requires --workflows", file=sys.stderr)
        return 1

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

        overlaps = build_overlaps(
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
            overlaps,
            active_usage,
            workflow_errors,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    print_compact_report(
        workflow_root,
        installed,
        matched,
        pack_nodes,
        overlaps,
        active_usage,
        workflow_errors,
    )

    if args.verbose:
        print_verbose_report(
            custom_nodes,
            manager_map,
            url,
            workflow_root,
            ambiguous,
            overlaps,
            workflow_errors,
            args.only_used_overlaps,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
