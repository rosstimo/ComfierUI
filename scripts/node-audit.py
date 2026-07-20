#!/usr/bin/env python3
"""Audit installed ComfyUI node packs for node-ID collisions and workflow impact.

Terminology
-----------
ComfyUI workflows refer to backend node classes by a string node ID. On the
backend, custom-node packs register those IDs through NODE_CLASS_MAPPINGS.

ComfyUI Manager's extension-node-map.json is built from static analysis of node
pack source code. If Manager associates the same node ID with more than one
locally installed pack, this script calls that a "potential node-ID collision".

A potential collision is advisory. It does not prove that both packs currently
register the ID or that anything is broken. The running ComfyUI /object_info
endpoint is used to show which pack currently provides each node ID.

The default report is decision-oriented:
1. high-level numerical overview;
2. plain-language summary by installed pack group;
3. a narrow terminal-friendly table of workflow-used collision IDs;
4. detailed vertical records only for items that need attention;
5. cleanup-review candidates.

Use --verbose for technical per-pack statistics and every potential collision.
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
class Collision:
    node_id: str
    installed_packs: tuple[str, ...]
    current_provider: str | None
    workflow_files: tuple[str, ...]
    inactive_workflow_files: tuple[str, ...]


@dataclass(frozen=True)
class PairSummary:
    packs: tuple[str, ...]
    collision_count: int
    used_count: int
    available_used_count: int
    missing_used_count: int
    provider_counts: tuple[tuple[str, int], ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit installed ComfyUI node packs for potential node-ID collisions, "
            "current runtime providers, and optional saved-workflow usage."
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
            "inspect saved workflows; optionally provide a workflow directory. "
            "Without PATH, use COMFYUI_WORKFLOWS_PATH or ./workflows"
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show technical per-pack statistics and every potential node-ID collision",
    )
    parser.add_argument(
        "--only-used-overlaps",
        action="store_true",
        help=(
            "compatibility option: with --workflows --verbose, limit verbose "
            "collision details to node IDs used by active saved workflows"
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
    """Yield UI workflow node records, including nested/subgraph node lists."""
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


def build_collisions(
    matched: dict[ManagerEntry, str],
    current_providers: dict[str, str],
    active_usage: dict[str, set[str]],
    inactive_usage: dict[str, set[str]],
) -> list[Collision]:
    installed_claimants: dict[str, set[str]] = defaultdict(set)
    for entry, installed_pack in matched.items():
        for node_id in entry.node_ids:
            installed_claimants[node_id].add(installed_pack)

    collisions: list[Collision] = []
    for node_id, packs in installed_claimants.items():
        if len(packs) < 2:
            continue
        collisions.append(
            Collision(
                node_id=node_id,
                installed_packs=tuple(sorted(packs, key=str.casefold)),
                current_provider=current_providers.get(node_id),
                workflow_files=tuple(sorted(active_usage.get(node_id, set()))),
                inactive_workflow_files=tuple(
                    sorted(inactive_usage.get(node_id, set()))
                ),
            )
        )

    return sorted(collisions, key=lambda item: item.node_id.casefold())


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


def pair_summaries(collisions: list[Collision]) -> list[PairSummary]:
    grouped: dict[tuple[str, ...], list[Collision]] = defaultdict(list)
    for item in collisions:
        grouped[item.installed_packs].append(item)

    summaries: list[PairSummary] = []
    for packs, items in grouped.items():
        used = [item for item in items if item.workflow_files]
        provider_counts: dict[str, int] = defaultdict(int)
        for item in used:
            if item.current_provider is not None:
                provider_counts[item.current_provider] += 1

        summaries.append(
            PairSummary(
                packs=packs,
                collision_count=len(items),
                used_count=len(used),
                available_used_count=sum(
                    1 for item in used if item.current_provider is not None
                ),
                missing_used_count=sum(
                    1 for item in used if item.current_provider is None
                ),
                provider_counts=tuple(
                    sorted(provider_counts.items(), key=lambda pair: pair[0].casefold())
                ),
            )
        )

    return sorted(summaries, key=lambda item: tuple(p.casefold() for p in item.packs))


def technical_pack_stats(
    installed: list[str],
    collisions: list[Collision],
) -> dict[str, dict[str, int]]:
    stats = {
        pack: {
            "potential_collisions": 0,
            "provides": 0,
            "other_provider": 0,
            "unregistered": 0,
            "workflow_used_collisions": 0,
        }
        for pack in installed
    }

    for item in collisions:
        for pack in item.installed_packs:
            if pack not in stats:
                continue
            stats[pack]["potential_collisions"] += 1
            if item.current_provider is None:
                stats[pack]["unregistered"] += 1
            elif item.current_provider == pack:
                stats[pack]["provides"] += 1
            else:
                stats[pack]["other_provider"] += 1
            if item.workflow_files:
                stats[pack]["workflow_used_collisions"] += 1

    return stats


def print_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    """Print a compact table. Keep wide free-text fields out of these tables."""
    if not rows:
        return

    widths = [len(value) for value in headers]
    for row in rows:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(value))

    print("  ".join(value.ljust(widths[i]) for i, value in enumerate(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)))


def collision_status(item: Collision) -> str:
    return "PROBLEM" if item.current_provider is None else "OK"


def provider_risk_text(item: Collision) -> str:
    if item.current_provider is None:
        return (
            "The workflow references this exact node ID, but the running ComfyUI "
            "does not currently have any backend node registered under that ID. "
            "Those workflows cannot execute that node as saved."
        )
    return (
        f"The workflow currently resolves this node ID to {item.current_provider}. "
        "Manager also associates the same ID with another installed pack, so this is "
        "a potential collision, but there is no evidence of a runtime failure right now."
    )


def print_pair_legend(pairs: list[PairSummary]) -> dict[tuple[str, ...], str]:
    labels: dict[tuple[str, ...], str] = {}
    if not pairs:
        return labels

    print("\n=== Installed pack groups with potential node-ID collisions ===")
    for index, pair in enumerate(pairs, start=1):
        label = chr(ord("A") + index - 1) if index <= 26 else str(index)
        labels[pair.packs] = label
        print(f"Group {label}")
        for pack in pair.packs:
            print(f"  - {pack}")
        print(f"  Potential collision IDs: {pair.collision_count}")
        if pair.used_count:
            print(f"  Used by saved workflows:  {pair.used_count}")
        else:
            print("  Used by saved workflows:  0")
    return labels


def print_plain_report(
    installed: list[str],
    matched: dict[ManagerEntry, str],
    pack_nodes: dict[str, set[str]],
    collisions: list[Collision],
    active_usage: dict[str, set[str]],
    workflow_root: Path | None,
    workflow_errors: list[tuple[str, str]],
) -> None:
    installed_set = set(installed)
    matched_packs = set(matched.values())
    running_packs = {pack for pack in pack_nodes if pack in installed_set}
    pack_used_nodes, pack_workflows = pack_workflow_usage(pack_nodes, active_usage)
    pairs = pair_summaries(collisions)

    used_collisions = [item for item in collisions if item.workflow_files]
    available_used = [
        item for item in used_collisions if item.current_provider is not None
    ]
    missing_used = [
        item for item in used_collisions if item.current_provider is None
    ]

    cleanup_candidates = [
        pack
        for pack in installed
        if len(pack_nodes.get(pack, set())) > 0
        and len(pack_used_nodes.get(pack, set())) == 0
    ]

    print("=== ComfyUI node-pack audit ===")
    print(f"Installed node packs:                       {len(installed)}")
    print(f"Running node packs detected:                {len(running_packs)}")
    print(f"Pack groups with potential collisions:      {len(pairs)}")
    print(f"Potential node-ID collisions:               {len(collisions)}")
    if workflow_root is not None:
        print(f"Collision IDs used by saved workflows:      {len(used_collisions)}")
        print(f"  Available now:                            {len(available_used)}")
        print(f"  Missing now:                              {len(missing_used)}")
        print(f"Packs with no saved-workflow use found:     {len(cleanup_candidates)}")
        print(f"Unreadable workflow files:                  {len(workflow_errors)}")

    print("\nWhat 'potential collision' means:")
    print(
        "  Manager associates the same ComfyUI node ID (the NODE_CLASS_MAPPINGS key "
        "stored in workflows) with more than one installed pack."
    )
    print(
        "  It does NOT mean the packs contain the same exact implementation, and it "
        "does NOT prove both packs are actively registering the node right now."
    )

    print("\n=== Summary ===")
    if not collisions:
        print("- OK: No potential node-ID collisions were found between installed packs.")
    else:
        for pair in pairs:
            names = " + ".join(pair.packs)
            if workflow_root is None:
                print(
                    f"- REVIEW: {names}: Manager associates {pair.collision_count} of the "
                    "same node IDs with both installed packs."
                )
                continue

            if pair.used_count == 0:
                print(
                    f"- REVIEW: {names}: {pair.collision_count} potential collision IDs, "
                    "but none are used by the saved workflows scanned."
                )
                continue

            provider_text = ", ".join(
                f"{provider} currently provides {count}"
                for provider, count in pair.provider_counts
            )
            if pair.missing_used_count:
                print(
                    f"- PROBLEM: {names}: saved workflows use {pair.used_count} potentially "
                    f"colliding node IDs. {provider_text or 'No current provider found'}. "
                    f"{pair.missing_used_count} used node ID"
                    f"{'s are' if pair.missing_used_count != 1 else ' is'} missing."
                )
            else:
                print(
                    f"- OK FOR NOW: {names}: saved workflows use {pair.used_count} potentially "
                    f"colliding node IDs, and all currently resolve to a live provider "
                    f"({provider_text})."
                )

    group_labels = print_pair_legend(pairs)

    if workflow_root is not None and used_collisions:
        print("\n=== Workflow-used potential collisions ===")
        rows: list[tuple[str, ...]] = []
        for item in sorted(
            used_collisions,
            key=lambda value: (
                0 if value.current_provider is None else 1,
                group_labels.get(value.installed_packs, ""),
                value.node_id.casefold(),
            ),
        ):
            rows.append(
                (
                    collision_status(item),
                    group_labels.get(item.installed_packs, "?"),
                    item.node_id,
                    item.current_provider or "MISSING",
                    str(len(item.workflow_files)),
                )
            )

        print_table(
            ("Status", "Group", "Node ID", "Current provider", "WFs"),
            rows,
        )
        print("WFs = number of active saved workflow files using that node ID.")

    if missing_used:
        print("\n=== Needs attention ===")
        for item in missing_used:
            print(f"\n[PROBLEM] {item.node_id}")
            print("  Installed packs Manager associates with this node ID:")
            for pack in item.installed_packs:
                print(f"    - {pack}")
            print("  Current provider: MISSING")
            print("  Actual problem:")
            print(
                "    The saved workflow references this exact node ID, but the running "
                "ComfyUI has no backend node registered under that ID."
            )
            print("  Impact:")
            print(
                f"    {len(item.workflow_files)} saved workflow"
                f"{'s' if len(item.workflow_files) != 1 else ''} cannot execute this "
                "node as currently saved."
            )
            print("  Likely causes to investigate:")
            print("    - the installed pack version removed or renamed the node ID")
            print("    - an optional component/submodule/dependency did not load")
            print("    - the workflow was created against an older pack version")
            print("  Saved workflows:")
            for filename in item.workflow_files:
                print(f"    - {filename}")
            print("  Decision:")
            print(
                "    Keep the workflows -> restore a compatible pack/version that registers "
                "this node ID. Do not care about them -> archive/delete the workflows. "
                "This missing node is not, by itself, proof that the two installed packs "
                "are actively conflicting."
            )

    if workflow_root is not None:
        print("\n=== Packs to review for cleanup ===")
        if not cleanup_candidates:
            print("No running node packs were found with zero saved-workflow usage.")
        else:
            for pack in cleanup_candidates:
                print(f"\n{pack}")
                print(f"  Live nodes registered now: {len(pack_nodes.get(pack, set()))}")
                print(f"  Live nodes used by scanned workflows: {len(pack_used_nodes.get(pack, set()))}")
                print(f"  Saved workflow files using its live nodes: {len(pack_workflows.get(pack, set()))}")
                print(
                    "  Why review: none of this pack's currently registered nodes appear "
                    "in the saved workflows scanned."
                )
            print(
                "\nReview only. A pack may still be used interactively, by unsaved workflows, "
                "or by API-generated workflows."
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


def print_verbose_report(
    custom_nodes: Path,
    manager_map: Path,
    url: str,
    workflow_root: Path | None,
    installed: list[str],
    matched: dict[ManagerEntry, str],
    ambiguous: dict[str, list[ManagerEntry]],
    pack_nodes: dict[str, set[str]],
    collisions: list[Collision],
    active_usage: dict[str, set[str]],
    workflow_errors: list[tuple[str, str]],
    only_used_overlaps: bool,
) -> None:
    installed_set = set(installed)
    matched_packs = set(matched.values())
    running_packs = {pack for pack in pack_nodes if pack in installed_set}
    pack_used_nodes, pack_workflows = pack_workflow_usage(pack_nodes, active_usage)
    stats = technical_pack_stats(installed, collisions)

    print("\n=== Technical details ===")
    print(f"Custom nodes: {custom_nodes}")
    print(f"Manager map: {manager_map}")
    print(f"Runtime registry: {url}")
    if workflow_root is not None:
        print(f"Workflows: {workflow_root}")
    print(f"Manager-matched packs: {len(matched_packs)}")
    print(f"Running packs: {len(running_packs)}")

    print("\n=== Per-pack technical statistics ===")
    rows: list[tuple[str, ...]] = []
    for pack in installed:
        rows.append(
            (
                pack,
                str(len(pack_nodes.get(pack, set()))),
                str(len(pack_used_nodes.get(pack, set()))),
                str(len(pack_workflows.get(pack, set()))),
                str(stats[pack]["potential_collisions"]),
                str(stats[pack]["provides"]),
                str(stats[pack]["other_provider"]),
                str(stats[pack]["unregistered"]),
                str(stats[pack]["workflow_used_collisions"]),
            )
        )

    print_table(
        (
            "Pack",
            "Live",
            "Used",
            "WFs",
            "Collide",
            "Provides",
            "Other",
            "Missing",
            "UsedCol",
        ),
        rows,
    )

    print("\n=== Per-node potential collision details ===")
    selected = (
        [item for item in collisions if item.workflow_files]
        if only_used_overlaps
        else collisions
    )
    if not selected:
        print("No matching potential collision details to show.")
    else:
        for item in selected:
            print(f"\n{item.node_id}")
            print("  Installed packs associated by Manager:")
            for pack in item.installed_packs:
                print(f"    - {pack}")
            print(f"  Current provider: {item.current_provider or 'MISSING'}")
            print(f"  Interpretation: {provider_risk_text(item)}")
            if workflow_root is not None:
                print(f"  Active saved workflows: {len(item.workflow_files)}")
                for filename in item.workflow_files:
                    print(f"    - {filename}")
                if item.inactive_workflow_files:
                    print(
                        "  Muted/bypassed-only saved workflows: "
                        f"{len(item.inactive_workflow_files)}"
                    )
                    for filename in item.inactive_workflow_files:
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
    collisions: list[Collision],
    active_usage: dict[str, set[str]],
    workflow_errors: list[tuple[str, str]],
) -> dict[str, Any]:
    installed_set = set(installed)
    matched_packs = set(matched.values())
    running_packs = {pack for pack in pack_nodes if pack in installed_set}
    pack_used_nodes, pack_workflows = pack_workflow_usage(pack_nodes, active_usage)
    stats = technical_pack_stats(installed, collisions)
    pairs = pair_summaries(collisions)

    return {
        "terminology": {
            "potential_node_id_collision": (
                "Manager associates the same NODE_CLASS_MAPPINGS key / workflow node ID "
                "with more than one locally installed pack. This is advisory and does not "
                "prove both packs currently register the ID."
            )
        },
        "sources": {
            "custom_nodes": str(custom_nodes),
            "manager_map": str(manager_map),
            "object_info": url,
            "workflows": str(workflow_root) if workflow_root else None,
        },
        "summary": {
            "installed_packs": len(installed),
            "manager_matched_packs": len(matched_packs),
            "running_packs": len(running_packs),
            "installed_pack_groups_with_potential_collisions": len(pairs),
            "potential_node_id_collisions": len(collisions),
            "workflow_used_collision_ids": sum(
                1 for item in collisions if item.workflow_files
            ),
            "workflow_used_available_collision_ids": sum(
                1
                for item in collisions
                if item.workflow_files and item.current_provider is not None
            ),
            "workflow_used_missing_collision_ids": sum(
                1
                for item in collisions
                if item.workflow_files and item.current_provider is None
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
        "collision_groups": [
            {
                "packs": list(pair.packs),
                "collision_count": pair.collision_count,
                "workflow_used_count": pair.used_count,
                "workflow_used_available_count": pair.available_used_count,
                "workflow_used_missing_count": pair.missing_used_count,
                "current_provider_counts": dict(pair.provider_counts),
            }
            for pair in pairs
        ],
        "collisions": [
            {
                "node_id": item.node_id,
                "installed_packs": list(item.installed_packs),
                "current_provider": item.current_provider,
                "workflow_count": len(item.workflow_files),
                "workflow_files": list(item.workflow_files),
                "inactive_workflow_count": len(item.inactive_workflow_files),
                "inactive_workflow_files": list(item.inactive_workflow_files),
                "status": collision_status(item),
            }
            for item in collisions
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
        current_providers, pack_nodes = runtime_node_owners(object_info)

        active_usage: dict[str, set[str]] = {}
        inactive_usage: dict[str, set[str]] = {}
        workflow_errors: list[tuple[str, str]] = []
        if workflow_root is not None:
            active_usage, inactive_usage, workflow_errors = workflow_usage(workflow_root)

        collisions = build_collisions(
            matched,
            current_providers,
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
            collisions,
            active_usage,
            workflow_errors,
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0

    print_plain_report(
        installed,
        matched,
        pack_nodes,
        collisions,
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
            collisions,
            active_usage,
            workflow_errors,
            args.only_used_overlaps,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
