#!/usr/bin/env python3
"""Audit installed ComfyUI node-pack overlaps and saved-workflow usage.

The default report is intentionally plain-language and decision-oriented. It
answers three questions:

1. Are multiple installed packs known to claim the same node names?
2. Do saved workflows use any of those overlapping node names?
3. Are any workflow-used overlapping nodes missing from the running ComfyUI?

ComfyUI Manager's extension-node-map.json is static-analysis metadata, so an
"overlap" is advisory. It means Manager associates the same node ID with more
than one locally installed pack. It does not prove both packs successfully
register that node at runtime. The running /object_info registry is used to show
which pack currently owns a node when ComfyUI exposes that information.
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


@dataclass(frozen=True)
class PairSummary:
    packs: tuple[str, ...]
    overlap_count: int
    used_overlap_count: int
    used_registered_count: int
    used_missing_count: int
    owner_counts: tuple[tuple[str, int], ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit locally installed ComfyUI node packs for Manager-known node-name "
            "overlaps, runtime ownership, and optional saved-workflow usage."
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
            "also inspect saved workflows; optionally provide a workflow directory. "
            "Without PATH, use COMFYUI_WORKFLOWS_PATH or ./workflows"
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show technical pack statistics and per-node overlap details",
    )
    parser.add_argument(
        "--only-used-overlaps",
        action="store_true",
        help=(
            "with --workflows --verbose, show only overlap details referenced by "
            "active saved workflows"
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

        entries.append(
            ManagerEntry(repo=repo, title=title, node_ids=clean_node_ids)
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


def pair_summaries(overlaps: list[Overlap]) -> list[PairSummary]:
    grouped: dict[tuple[str, ...], list[Overlap]] = defaultdict(list)
    for overlap in overlaps:
        grouped[overlap.installed_claimants].append(overlap)

    summaries: list[PairSummary] = []
    for packs, items in grouped.items():
        used = [item for item in items if item.workflow_files]
        owner_counts: dict[str, int] = defaultdict(int)
        for item in used:
            if item.runtime_owner is not None:
                owner_counts[item.runtime_owner] += 1

        summaries.append(
            PairSummary(
                packs=packs,
                overlap_count=len(items),
                used_overlap_count=len(used),
                used_registered_count=sum(
                    1 for item in used if item.runtime_owner is not None
                ),
                used_missing_count=sum(
                    1 for item in used if item.runtime_owner is None
                ),
                owner_counts=tuple(
                    sorted(owner_counts.items(), key=lambda pair: pair[0].casefold())
                ),
            )
        )

    return sorted(summaries, key=lambda item: tuple(p.casefold() for p in item.packs))


def technical_pack_stats(
    installed: list[str],
    overlaps: list[Overlap],
) -> dict[str, dict[str, int]]:
    stats = {
        pack: {
            "overlaps": 0,
            "owns": 0,
            "other_owner": 0,
            "unregistered": 0,
            "workflow_used_overlaps": 0,
        }
        for pack in installed
    }

    for overlap in overlaps:
        for pack in overlap.installed_claimants:
            if pack not in stats:
                continue
            stats[pack]["overlaps"] += 1
            if overlap.runtime_owner is None:
                stats[pack]["unregistered"] += 1
            elif overlap.runtime_owner == pack:
                stats[pack]["owns"] += 1
            else:
                stats[pack]["other_owner"] += 1
            if overlap.workflow_files:
                stats[pack]["workflow_used_overlaps"] += 1

    return stats


def print_plain_report(
    installed: list[str],
    matched: dict[ManagerEntry, str],
    pack_nodes: dict[str, set[str]],
    overlaps: list[Overlap],
    active_usage: dict[str, set[str]],
    workflow_root: Path | None,
    workflow_errors: list[tuple[str, str]],
) -> None:
    installed_set = set(installed)
    matched_packs = set(matched.values())
    runtime_packs = {pack for pack in pack_nodes if pack in installed_set}
    pack_used_nodes, pack_workflows = pack_workflow_usage(pack_nodes, active_usage)
    pairs = pair_summaries(overlaps)

    used_overlaps = [item for item in overlaps if item.workflow_files]
    missing_used = [
        item for item in used_overlaps if item.runtime_owner is None
    ]

    print("=== ComfyUI node-pack audit ===")
    print(f"Installed node packs: {len(installed)}")
    print(f"Running node packs detected: {len(runtime_packs)}")

    if overlaps:
        print(
            "Overlapping node packs installed: YES "
            f"({len(pairs)} pack pair{'s' if len(pairs) != 1 else ''}, "
            f"{len(overlaps)} shared node names known to Manager)"
        )
    else:
        print("Overlapping node packs installed: NO")

    if workflow_root is not None:
        if used_overlaps:
            print(
                "Saved workflows use overlapping node names: YES "
                f"({len(used_overlaps)} node names)"
            )
        else:
            print("Saved workflows use overlapping node names: NO")

        if missing_used:
            print(
                "Workflow-used overlapping nodes missing right now: YES "
                f"({len(missing_used)})"
            )
        else:
            print("Workflow-used overlapping nodes missing right now: NO")

    print("\n=== What this means ===")
    if not overlaps:
        print("No installed pack pairs are known by Manager to share node names.")
    else:
        for pair in pairs:
            names = " + ".join(pair.packs)
            if workflow_root is None:
                print(
                    f"- {names}: {pair.overlap_count} shared node names are known to Manager."
                )
                continue

            if pair.used_overlap_count == 0:
                print(
                    f"- REVIEW: {names} share {pair.overlap_count} node names, but none of "
                    "those shared names are used by the saved workflows scanned."
                )
                continue

            owner_text = ", ".join(
                f"{owner} provides {count}"
                for owner, count in pair.owner_counts
            )
            if pair.used_missing_count:
                print(
                    f"- PROBLEM: {names} share {pair.overlap_count} node names. Saved "
                    f"workflows use {pair.used_overlap_count}; {owner_text or 'no live owner found'}; "
                    f"{pair.used_missing_count} workflow-used node name"
                    f"{'s are' if pair.used_missing_count != 1 else ' is'} missing."
                )
            else:
                print(
                    f"- OK: {names} share {pair.overlap_count} node names. Saved workflows "
                    f"use {pair.used_overlap_count}, and the running instance has a live owner "
                    f"for all of them ({owner_text})."
                )

    if workflow_root is not None:
        print("\n=== Packs to review for cleanup ===")
        candidates = [
            pack
            for pack in installed
            if len(pack_nodes.get(pack, set())) > 0
            and len(pack_used_nodes.get(pack, set())) == 0
        ]
        if not candidates:
            print("Every running node pack has at least one live node used by a saved workflow.")
        else:
            for pack in candidates:
                print(
                    f"- {pack}: running, but none of its own live nodes are referenced by the "
                    "saved workflows scanned."
                )
            print(
                "These are review candidates only. They may still be used interactively, by "
                "unsaved workflows, or through API-generated workflows."
            )

    if missing_used:
        print("\n=== Needs attention ===")
        for overlap in missing_used:
            print(
                f"- {overlap.node_id}: used by {len(overlap.workflow_files)} saved workflow"
                f"{'s' if len(overlap.workflow_files) != 1 else ''}, but not registered by "
                "the running ComfyUI. Manager associates it with installed packs: "
                f"{', '.join(overlap.installed_claimants)}."
            )

    if workflow_errors:
        print(
            f"\nWARNING: {len(workflow_errors)} workflow file"
            f"{'s could' if len(workflow_errors) != 1 else ' could'} not be read."
        )

    unmatched = len(installed_set - matched_packs)
    if unmatched:
        print(
            f"\nNote: {unmatched} installed pack"
            f"{'s were' if unmatched != 1 else ' was'} not matched to Manager's node map."
        )

    print(
        "\nNote: An overlap means Manager says two installed packs may provide the same "
        "node name. It does not prove the overlap is causing a problem."
    )


def print_verbose_report(
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
    only_used_overlaps: bool,
) -> None:
    installed_set = set(installed)
    matched_packs = set(matched.values())
    runtime_packs = {pack for pack in pack_nodes if pack in installed_set}
    pack_used_nodes, pack_workflows = pack_workflow_usage(pack_nodes, active_usage)
    stats = technical_pack_stats(installed, overlaps)

    print("\n=== Technical details ===")
    print(f"Custom nodes: {custom_nodes}")
    print(f"Manager map: {manager_map}")
    print(f"Runtime registry: {url}")
    if workflow_root is not None:
        print(f"Workflows: {workflow_root}")

    print("\n=== Technical summary ===")
    print(f"Installed pack directories: {len(installed)}")
    print(f"Matched to Manager map:     {len(matched_packs)}")
    print(f"Runtime-registered packs:   {len(runtime_packs)}")
    print(f"Manager-known overlaps:     {len(overlaps)}")
    print(
        "Registered overlap IDs:    "
        f"{sum(1 for item in overlaps if item.runtime_owner is not None)}"
    )
    print(
        "Unregistered overlap IDs:  "
        f"{sum(1 for item in overlaps if item.runtime_owner is None)}"
    )
    if workflow_root is not None:
        print(
            "Workflow-used overlaps:   "
            f"{sum(1 for item in overlaps if item.workflow_files)}"
        )
        print(f"Workflow read errors:       {len(workflow_errors)}")

    print("\n=== Per-pack technical statistics ===")
    header = (
        "Pack",
        "Live",
        "Used",
        "WFs",
        "Overlap",
        "Own",
        "Other",
        "Missing",
        "UsedOv",
    )
    rows = []
    for pack in installed:
        rows.append(
            (
                pack,
                str(len(pack_nodes.get(pack, set()))),
                str(len(pack_used_nodes.get(pack, set()))),
                str(len(pack_workflows.get(pack, set()))),
                str(stats[pack]["overlaps"]),
                str(stats[pack]["owns"]),
                str(stats[pack]["other_owner"]),
                str(stats[pack]["unregistered"]),
                str(stats[pack]["workflow_used_overlaps"]),
            )
        )

    widths = [len(value) for value in header]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    print("  ".join(value.ljust(widths[i]) for i, value in enumerate(header)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))

    print("\n=== Per-node overlap details ===")
    selected = (
        [item for item in overlaps if item.workflow_files]
        if only_used_overlaps
        else overlaps
    )
    if not selected:
        print("No matching overlap details to show.")
    else:
        for overlap in selected:
            print(f"\n{overlap.node_id}")
            print(f"  Installed Manager claimants: {', '.join(overlap.installed_claimants)}")
            print(
                "  Current runtime owner: "
                f"{overlap.runtime_owner if overlap.runtime_owner else 'not registered'}"
            )
            if workflow_root is not None:
                print(f"  Active saved workflows: {len(overlap.workflow_files)}")
                for filename in overlap.workflow_files:
                    print(f"    - {filename}")
                if overlap.inactive_workflow_files:
                    print(
                        "  Muted/bypassed-only saved workflows: "
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
    stats = technical_pack_stats(installed, overlaps)
    pairs = pair_summaries(overlaps)

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
            "installed_pack_groups_with_overlaps": len(pairs),
            "manager_known_overlap_node_ids": len(overlaps),
            "workflow_used_overlap_node_ids": sum(
                1 for item in overlaps if item.workflow_files
            ),
            "workflow_used_missing_overlap_node_ids": sum(
                1
                for item in overlaps
                if item.workflow_files and item.runtime_owner is None
            ),
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
        "overlap_groups": [
            {
                "packs": list(pair.packs),
                "overlap_count": pair.overlap_count,
                "workflow_used_overlap_count": pair.used_overlap_count,
                "workflow_used_registered_count": pair.used_registered_count,
                "workflow_used_missing_count": pair.used_missing_count,
                "runtime_owner_counts": dict(pair.owner_counts),
            }
            for pair in pairs
        ],
        "overlaps": [
            {
                "node_id": item.node_id,
                "installed_manager_claimants": list(item.installed_claimants),
                "runtime_owner": item.runtime_owner,
                "workflow_count": len(item.workflow_files),
                "workflow_files": list(item.workflow_files),
                "inactive_workflow_count": len(item.inactive_workflow_files),
                "inactive_workflow_files": list(item.inactive_workflow_files),
            }
            for item in overlaps
        ],
        "ambiguous_manager_matches": {
            pack: [
                {"title": entry.title, "repo": entry.repo}
                for entry in pack_entries
            ]
            for pack, pack_entries in ambiguous.items()
        },
        "workflow_errors": [
            {"file": filename, "error": error}
            for filename, error in workflow_errors
        ],
    }


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

    print_plain_report(
        installed,
        matched,
        pack_nodes,
        overlaps,
        active_usage,
        workflow_root,
        workflow_errors,
    )

    if args.verbose:
        print_verbose_report(
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
            args.only_used_overlaps,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
