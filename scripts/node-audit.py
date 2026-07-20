#!/usr/bin/env python3
"""Audit ComfyUI node-pack availability, workflow dependencies, and ID collisions.

The default report is decision-oriented:

1. Which node types used by saved workflows are missing from the running ComfyUI?
2. Which node pack does the workflow itself say each missing node came from?
3. Are those intended packs installed now?
4. Which installed packs are associated by Manager with the same NODE_CLASS_MAPPINGS key?
5. Which currently running packs appear unused by the saved workflows scanned?

ComfyUI Manager's extension-node-map.json is advisory static-analysis metadata.
Workflow-embedded ``properties.cnr_id`` and ``properties.ver`` are preferred when
identifying the pack/version a saved workflow expected. The running /object_info
registry is authoritative for whether a backend node ID is currently available.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

FRONTEND_ONLY_NODE_TYPES = {"Note", "PrimitiveNode", "Reroute"}
INACTIVE_NODE_MODES = {2, 4}


@dataclass(frozen=True)
class ManagerEntry:
    repo: str
    title: str
    node_ids: tuple[str, ...]


@dataclass(frozen=True)
class NodeOccurrence:
    workflow: str
    workflow_node_id: str
    node_type: str
    title: str | None
    mode: int
    cnr_id: str | None
    version: str | None


@dataclass(frozen=True)
class Collision:
    node_id: str
    installed_packs: tuple[str, ...]
    current_provider: str | None
    workflow_files: tuple[str, ...]


@dataclass(frozen=True)
class CollisionGroup:
    label: str
    packs: tuple[str, ...]
    collisions: tuple[Collision, ...]


@dataclass(frozen=True)
class MissingDependency:
    key: str
    display_pack: str
    installed_pack: str | None
    node_types: tuple[str, ...]
    workflows: tuple[str, ...]
    occurrences: tuple[NodeOccurrence, ...]
    manager_candidates: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit ComfyUI workflow dependencies, missing nodes, potential node-ID "
            "collisions, and saved-workflow usage."
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
        "--workflow",
        action="append",
        default=[],
        metavar="FILE",
        help=(
            "inspect only this workflow file; may be repeated. Implies --workflows "
            "using COMFYUI_WORKFLOWS_PATH or ./workflows when --workflows is omitted"
        ),
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="show source paths, technical per-pack statistics, and all potential collisions",
    )
    parser.add_argument(
        "--only-used-overlaps",
        action="store_true",
        help=(
            "compatibility alias: with --verbose, limit collision details to IDs used "
            "by the scanned workflows"
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
    if args.workflows is not None or args.workflow:
        if args.workflows and args.workflows != "__ENV__":
            workflow_root = resolve_host_path(args.workflows, Path.cwd())
        else:
            workflow_root = resolve_host_path(
                env.get("COMFYUI_WORKFLOWS_PATH", "./workflows"), root
            )

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
        clean_node_ids = tuple(node for node in node_ids if isinstance(node, str))
        title = metadata.get("title_aux")
        if not isinstance(title, str) or not title.strip():
            title = repo_basename(repo)
        entries.append(ManagerEntry(repo=repo, title=title, node_ids=clean_node_ids))
    return entries


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
    owners: dict[str, str] = {}
    pack_nodes: dict[str, set[str]] = defaultdict(set)
    for node_id, info in object_info.items():
        pack = runtime_pack_from_module(info.get("python_module"))
        if pack is None:
            continue
        owners[node_id] = pack
        pack_nodes[pack].add(node_id)
    return owners, pack_nodes


def manager_candidates_by_node(entries: Iterable[ManagerEntry]) -> dict[str, list[ManagerEntry]]:
    result: dict[str, list[ManagerEntry]] = defaultdict(list)
    for entry in entries:
        for node_id in entry.node_ids:
            result[node_id].append(entry)
    return result


def match_manager_entries_to_installed(
    entries: Iterable[ManagerEntry], installed: Iterable[str]
) -> dict[ManagerEntry, str]:
    installed_list = list(installed)
    by_normalized: dict[str, list[str]] = defaultdict(list)
    for pack in installed_list:
        by_normalized[normalize_pack_name(pack)].append(pack)

    matched: dict[ManagerEntry, str] = {}
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
    return matched


def workflow_paths(root: Path, selected: list[str]) -> list[Path]:
    if not root.is_dir():
        raise RuntimeError(f"workflow directory not found: {root}")
    if not selected:
        return sorted(root.rglob("*.json"))

    resolved: list[Path] = []
    for name in selected:
        direct = (root / name).resolve(strict=False)
        if direct.is_file():
            resolved.append(direct)
            continue
        matches = [path for path in root.rglob("*.json") if path.name == Path(name).name]
        if not matches:
            raise RuntimeError(f"workflow file not found under {root}: {name}")
        if len(matches) > 1:
            choices = ", ".join(str(path.relative_to(root)) for path in matches)
            raise RuntimeError(f"workflow name is ambiguous: {name}; matches: {choices}")
        resolved.append(matches[0])
    return sorted(set(resolved))


def workflow_node_dicts(data: Any) -> Iterable[dict[str, Any]]:
    """Yield executable nodes from the primary workflow graph only.

    For normal UI workflow JSON, only the top-level ``nodes`` list is inspected.
    This intentionally avoids recursively treating embedded templates, metadata,
    or subdocuments as additional live nodes.
    """
    if isinstance(data, dict) and isinstance(data.get("nodes"), list):
        for node in data["nodes"]:
            if isinstance(node, dict) and isinstance(node.get("type"), str):
                yield node
        return

    if isinstance(data, dict):
        for node_id, value in data.items():
            if not isinstance(value, dict):
                continue
            class_type = value.get("class_type")
            if isinstance(class_type, str):
                yield {
                    "id": node_id,
                    "type": class_type,
                    "mode": 0,
                    "properties": value.get("properties", {}),
                }


def clean_optional_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def scan_workflows(
    root: Path,
    paths: list[Path],
    registered: set[str],
) -> tuple[
    dict[str, set[str]],
    dict[str, list[NodeOccurrence]],
    dict[str, list[NodeOccurrence]],
    list[tuple[str, str]],
]:
    active_usage: dict[str, set[str]] = defaultdict(set)
    active_occurrences: dict[str, list[NodeOccurrence]] = defaultdict(list)
    missing_occurrences: dict[str, list[NodeOccurrence]] = defaultdict(list)
    errors: list[tuple[str, str]] = []

    for path in paths:
        relative = str(path.relative_to(root))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append((relative, str(exc)))
            continue

        for node in workflow_node_dicts(data):
            node_type = node.get("type")
            if not isinstance(node_type, str) or node_type in FRONTEND_ONLY_NODE_TYPES:
                continue
            mode = node.get("mode", 0)
            if not isinstance(mode, int):
                mode = 0
            if mode in INACTIVE_NODE_MODES:
                continue

            properties = node.get("properties")
            if not isinstance(properties, dict):
                properties = {}

            occurrence = NodeOccurrence(
                workflow=relative,
                workflow_node_id=str(node.get("id", "?")),
                node_type=node_type,
                title=clean_optional_string(node.get("title")),
                mode=mode,
                cnr_id=clean_optional_string(properties.get("cnr_id")),
                version=clean_optional_string(properties.get("ver")),
            )
            active_usage[node_type].add(relative)
            active_occurrences[node_type].append(occurrence)
            if node_type not in registered:
                missing_occurrences[node_type].append(occurrence)

    return active_usage, active_occurrences, missing_occurrences, errors


def find_installed_pack(pack_id: str, installed: Iterable[str]) -> str | None:
    target = normalize_pack_name(pack_id)
    matches = [pack for pack in installed if normalize_pack_name(pack) == target]
    return matches[0] if len(matches) == 1 else None


def manager_candidate_names(entries: Iterable[ManagerEntry]) -> tuple[str, ...]:
    values: set[str] = set()
    for entry in entries:
        values.add(entry.title)
    return tuple(sorted(values, key=str.casefold))


def build_missing_dependencies(
    missing_occurrences: dict[str, list[NodeOccurrence]],
    manager_by_node: dict[str, list[ManagerEntry]],
    installed: list[str],
) -> list[MissingDependency]:
    grouped: dict[str, list[NodeOccurrence]] = defaultdict(list)
    group_display: dict[str, str] = {}

    for node_type, occurrences in missing_occurrences.items():
        cnr_ids = sorted(
            {occ.cnr_id for occ in occurrences if occ.cnr_id}, key=str.casefold
        )
        if cnr_ids:
            for cnr_id in cnr_ids:
                key = f"cnr:{cnr_id}"
                group_display[key] = cnr_id
                grouped[key].extend(occ for occ in occurrences if occ.cnr_id == cnr_id)
            unknown = [occ for occ in occurrences if not occ.cnr_id]
            if unknown:
                key = f"unknown:{node_type}"
                group_display[key] = "UNKNOWN"
                grouped[key].extend(unknown)
        else:
            key = f"unknown:{node_type}"
            group_display[key] = "UNKNOWN"
            grouped[key].extend(occurrences)

    dependencies: list[MissingDependency] = []
    for key, occurrences in grouped.items():
        node_types = tuple(sorted({occ.node_type for occ in occurrences}, key=str.casefold))
        workflows = tuple(sorted({occ.workflow for occ in occurrences}))
        display_pack = group_display[key]
        installed_pack = (
            find_installed_pack(display_pack, installed) if display_pack != "UNKNOWN" else None
        )
        manager_entries: list[ManagerEntry] = []
        for node_type in node_types:
            manager_entries.extend(manager_by_node.get(node_type, []))
        dependencies.append(
            MissingDependency(
                key=key,
                display_pack=display_pack,
                installed_pack=installed_pack,
                node_types=node_types,
                workflows=workflows,
                occurrences=tuple(
                    sorted(
                        occurrences,
                        key=lambda occ: (
                            occ.workflow.casefold(),
                            occ.node_type.casefold(),
                            occ.workflow_node_id,
                        ),
                    )
                ),
                manager_candidates=manager_candidate_names(manager_entries),
            )
        )

    return sorted(
        dependencies,
        key=lambda dep: (
            dep.display_pack == "UNKNOWN",
            dep.display_pack.casefold(),
            dep.node_types,
        ),
    )


def build_collisions(
    matched_entries: dict[ManagerEntry, str],
    current_providers: dict[str, str],
    active_usage: dict[str, set[str]],
) -> list[Collision]:
    claimants: dict[str, set[str]] = defaultdict(set)
    for entry, pack in matched_entries.items():
        for node_id in entry.node_ids:
            claimants[node_id].add(pack)

    collisions: list[Collision] = []
    for node_id, packs in claimants.items():
        if len(packs) < 2:
            continue
        collisions.append(
            Collision(
                node_id=node_id,
                installed_packs=tuple(sorted(packs, key=str.casefold)),
                current_provider=current_providers.get(node_id),
                workflow_files=tuple(sorted(active_usage.get(node_id, set()))),
            )
        )
    return sorted(collisions, key=lambda item: item.node_id.casefold())


def collision_groups(collisions: list[Collision]) -> list[CollisionGroup]:
    grouped: dict[tuple[str, ...], list[Collision]] = defaultdict(list)
    for collision in collisions:
        grouped[collision.installed_packs].append(collision)

    groups: list[CollisionGroup] = []
    for index, (packs, items) in enumerate(
        sorted(grouped.items(), key=lambda pair: tuple(p.casefold() for p in pair[0]))
    ):
        label = chr(ord("A") + index) if index < 26 else str(index + 1)
        groups.append(CollisionGroup(label=label, packs=packs, collisions=tuple(items)))
    return groups


def pack_workflow_usage(
    pack_nodes: dict[str, set[str]], active_usage: dict[str, set[str]]
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    used_nodes: dict[str, set[str]] = defaultdict(set)
    workflows: dict[str, set[str]] = defaultdict(set)
    for pack, nodes in pack_nodes.items():
        for node_id in nodes:
            files = active_usage.get(node_id, set())
            if files:
                used_nodes[pack].add(node_id)
                workflows[pack].update(files)
    return used_nodes, workflows


def print_overview(
    installed: list[str],
    pack_nodes: dict[str, set[str]],
    workflow_count: int,
    active_usage: dict[str, set[str]],
    missing_occurrences: dict[str, list[NodeOccurrence]],
    missing_dependencies: list[MissingDependency],
    collisions: list[Collision],
    workflow_errors: list[tuple[str, str]],
) -> None:
    installed_set = set(installed)
    running_packs = {pack for pack in pack_nodes if pack in installed_set}
    used_collisions = [item for item in collisions if item.workflow_files]
    missing_instances = sum(len(items) for items in missing_occurrences.values())
    known_missing_packs = {
        dep.display_pack for dep in missing_dependencies if dep.display_pack != "UNKNOWN"
    }

    print("=== ComfyUI node-pack audit ===")
    print(f"Installed node packs:                    {len(installed)}")
    print(f"Running node packs detected:             {len(running_packs)}")
    print(f"Saved workflows scanned:                 {workflow_count}")
    print(f"Distinct active node types used:         {len(active_usage)}")
    print(f"Missing active node types:               {len(missing_occurrences)}")
    print(f"Missing active node instances:           {missing_instances}")
    print(f"Intended missing pack IDs found:         {len(known_missing_packs)}")
    print(f"Potential installed node-ID collisions:  {len(collisions)}")
    print(f"Collision IDs used by saved workflows:   {len(used_collisions)}")
    print(f"Unreadable workflow files:               {len(workflow_errors)}")


def print_plain_summary(
    missing_occurrences: dict[str, list[NodeOccurrence]],
    missing_dependencies: list[MissingDependency],
    collisions: list[Collision],
) -> None:
    used_collisions = [item for item in collisions if item.workflow_files]
    print("\n=== Summary ===")
    if missing_occurrences:
        workflows = {occ.workflow for items in missing_occurrences.values() for occ in items}
        print(
            f"- PROBLEM: {len(missing_occurrences)} active node type"
            f"{'s are' if len(missing_occurrences) != 1 else ' is'} missing across "
            f"{len(workflows)} saved workflow{'s' if len(workflows) != 1 else ''}."
        )
        explicit = [dep for dep in missing_dependencies if dep.display_pack != "UNKNOWN"]
        if explicit:
            missing_pack_count = sum(1 for dep in explicit if dep.installed_pack is None)
            installed_but_missing = sum(1 for dep in explicit if dep.installed_pack is not None)
            print(
                f"- Workflow metadata identifies {len(explicit)} intended pack ID"
                f"{'s' if len(explicit) != 1 else ''}: {missing_pack_count} are not installed "
                f"under that ID; {installed_but_missing} are installed but still fail to provide "
                "the expected node(s)."
            )
    else:
        print("- OK: No active workflow node types are missing from the running ComfyUI.")

    if used_collisions:
        missing_collision_ids = sum(1 for item in used_collisions if item.current_provider is None)
        print(
            f"- REVIEW: {len(used_collisions)} workflow-used node ID"
            f"{'s have' if len(used_collisions) != 1 else ' has'} potential installed-pack "
            f"collisions. {len(used_collisions) - missing_collision_ids} currently have a live "
            f"provider; {missing_collision_ids} do not. Collision data is advisory."
        )
    else:
        print("- OK: No potential installed-pack collision IDs are used by the scanned workflows.")


def print_missing_dependencies(dependencies: list[MissingDependency]) -> None:
    print("\n=== Missing workflow dependencies ===")
    if not dependencies:
        print("None.")
        return

    for dep in dependencies:
        state = "INSTALLED, NODE STILL MISSING" if dep.installed_pack else "NOT INSTALLED"
        if dep.display_pack == "UNKNOWN":
            state = "PACK UNKNOWN"
        print(f"\n[{state}] {dep.display_pack}")
        if dep.installed_pack:
            print(f"  Installed directory: {dep.installed_pack}")
        print("  Missing node types:")
        for node_type in dep.node_types:
            count = sum(1 for occ in dep.occurrences if occ.node_type == node_type)
            print(f"    - {node_type} ({count} instance{'s' if count != 1 else ''})")

        versions = sorted({occ.version for occ in dep.occurrences if occ.version})
        if versions:
            print(f"  Workflow-recorded version(s): {', '.join(versions)}")

        print("  Saved workflows:")
        for workflow in dep.workflows:
            print(f"    - {workflow}")

        if dep.display_pack == "UNKNOWN" and dep.manager_candidates:
            print("  Manager fallback candidates:")
            for candidate in dep.manager_candidates:
                print(f"    - {candidate}")
        elif dep.manager_candidates and dep.display_pack not in dep.manager_candidates:
            print("  Manager global-map candidates (advisory):")
            for candidate in dep.manager_candidates:
                print(f"    - {candidate}")

        print("  What is actually wrong:")
        print("    The running ComfyUI does not register the node ID(s) above, so these")
        print("    workflow nodes cannot execute as currently saved.")
        print("  Most likely next step:")
        if dep.display_pack == "UNKNOWN":
            print("    Identify the original pack, then install/restore a compatible version if")
            print("    the workflow matters. Manager candidates are hints, not proof.")
        elif dep.installed_pack:
            print("    The expected pack is installed, so inspect its import/startup logs and")
            print("    version history for a failed import, removed node ID, or optional dependency.")
        else:
            print("    Install/restore the workflow-recorded pack if the workflow matters, or archive")
            print("    the workflow if it no longer matters. Do not infer a collision from this alone.")


def print_collision_section(groups: list[CollisionGroup]) -> None:
    print("\n=== Workflow-used potential node-ID collisions ===")
    used_any = False
    for group in groups:
        used = [item for item in group.collisions if item.workflow_files]
        if not used:
            continue
        used_any = True
        print(f"\nGroup {group.label}")
        print("  Installed packs associated with the same node ID(s):")
        for pack in group.packs:
            print(f"    - {pack}")
        print("  Node ID                         Current provider          WFs")
        print("  ------------------------------  ------------------------  ---")
        for item in sorted(used, key=lambda value: value.node_id.casefold()):
            provider = item.current_provider or "MISSING"
            print(f"  {item.node_id[:30]:30}  {provider[:24]:24}  {len(item.workflow_files):3}")
    if not used_any:
        print("None used by the scanned workflows.")
    print("\nPotential collision = Manager's static map associates the same NODE_CLASS_MAPPINGS")
    print("key with more than one installed pack. This does not prove both implementations")
    print("are active or that the collision is causing a problem.")


def print_cleanup_candidates(
    installed: list[str],
    pack_nodes: dict[str, set[str]],
    active_usage: dict[str, set[str]],
) -> None:
    used_nodes, workflows = pack_workflow_usage(pack_nodes, active_usage)
    candidates = [
        pack
        for pack in installed
        if pack_nodes.get(pack) and not used_nodes.get(pack)
    ]
    print("\n=== Packs to review for cleanup ===")
    if not candidates:
        print("No running packs with zero saved-workflow usage were found.")
        return
    for pack in candidates:
        print(f"\n{pack}")
        print(f"  Live nodes registered now: {len(pack_nodes.get(pack, set()))}")
        print(f"  Live nodes used by scanned workflows: {len(used_nodes.get(pack, set()))}")
        print(f"  Saved workflow files using its live nodes: {len(workflows.get(pack, set()))}")
    print("\nReview only. These packs may still be used interactively, by unsaved workflows,")
    print("or by API-generated workflows.")


def print_verbose(
    custom_nodes: Path,
    manager_map: Path,
    url: str,
    workflow_root: Path | None,
    collisions: list[Collision],
    only_used: bool,
    workflow_errors: list[tuple[str, str]],
) -> None:
    print("\n=== Technical details ===")
    print(f"Custom nodes: {custom_nodes}")
    print(f"Manager map: {manager_map}")
    print(f"Runtime registry: {url}")
    if workflow_root:
        print(f"Workflows: {workflow_root}")

    print("\n=== Potential collision details ===")
    selected = [item for item in collisions if item.workflow_files] if only_used else collisions
    if not selected:
        print("None.")
    for item in selected:
        print(f"\n{item.node_id}")
        print("  Installed Manager-associated packs:")
        for pack in item.installed_packs:
            print(f"    - {pack}")
        print(f"  Current provider: {item.current_provider or 'MISSING'}")
        if item.workflow_files:
            print("  Saved workflows:")
            for workflow in item.workflow_files:
                print(f"    - {workflow}")

    if workflow_errors:
        print("\n=== Workflow read errors ===")
        for filename, error in workflow_errors:
            print(f"{filename}: {error}")


def json_report(
    installed: list[str],
    pack_nodes: dict[str, set[str]],
    paths: list[Path],
    root: Path | None,
    active_usage: dict[str, set[str]],
    missing_occurrences: dict[str, list[NodeOccurrence]],
    dependencies: list[MissingDependency],
    collisions: list[Collision],
    workflow_errors: list[tuple[str, str]],
) -> dict[str, Any]:
    running = {pack for pack in pack_nodes if pack in set(installed)}
    return {
        "summary": {
            "installed_packs": len(installed),
            "running_packs": len(running),
            "workflows_scanned": len(paths),
            "active_node_types": len(active_usage),
            "missing_active_node_types": len(missing_occurrences),
            "missing_active_node_instances": sum(len(v) for v in missing_occurrences.values()),
            "missing_dependency_groups": len(dependencies),
            "potential_collision_ids": len(collisions),
            "workflow_used_collision_ids": sum(1 for c in collisions if c.workflow_files),
            "workflow_errors": len(workflow_errors),
        },
        "missing_dependencies": [
            {
                "workflow_pack_id": dep.display_pack,
                "installed_pack": dep.installed_pack,
                "node_types": list(dep.node_types),
                "workflows": list(dep.workflows),
                "manager_candidates": list(dep.manager_candidates),
                "occurrences": [
                    {
                        "workflow": occ.workflow,
                        "workflow_node_id": occ.workflow_node_id,
                        "node_type": occ.node_type,
                        "title": occ.title,
                        "cnr_id": occ.cnr_id,
                        "version": occ.version,
                    }
                    for occ in dep.occurrences
                ],
            }
            for dep in dependencies
        ],
        "potential_collisions": [
            {
                "node_id": item.node_id,
                "installed_packs": list(item.installed_packs),
                "current_provider": item.current_provider,
                "workflow_files": list(item.workflow_files),
            }
            for item in collisions
        ],
        "workflow_files": [
            str(path.relative_to(root)) if root else str(path) for path in paths
        ],
        "workflow_errors": [
            {"file": filename, "error": error} for filename, error in workflow_errors
        ],
    }


def main() -> int:
    args = parse_args()
    try:
        custom_nodes, manager_map, url, workflow_root = default_paths(args)
        installed = installed_pack_names(custom_nodes)
        manager_entries = load_manager_entries(manager_map)
        manager_by_node = manager_candidates_by_node(manager_entries)
        matched_entries = match_manager_entries_to_installed(manager_entries, installed)
        object_info = load_object_info(url)
        current_providers, pack_nodes = runtime_node_owners(object_info)

        paths: list[Path] = []
        active_usage: dict[str, set[str]] = {}
        active_occurrences: dict[str, list[NodeOccurrence]] = {}
        missing_occurrences: dict[str, list[NodeOccurrence]] = {}
        workflow_errors: list[tuple[str, str]] = []

        if workflow_root is not None:
            paths = workflow_paths(workflow_root, args.workflow)
            (
                active_usage,
                active_occurrences,
                missing_occurrences,
                workflow_errors,
            ) = scan_workflows(workflow_root, paths, set(object_info))

        dependencies = build_missing_dependencies(
            missing_occurrences, manager_by_node, installed
        )
        collisions = build_collisions(
            matched_entries, current_providers, active_usage
        )
        groups = collision_groups(collisions)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                json_report(
                    installed,
                    pack_nodes,
                    paths,
                    workflow_root,
                    active_usage,
                    missing_occurrences,
                    dependencies,
                    collisions,
                    workflow_errors,
                ),
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    if workflow_root is None:
        print("=== ComfyUI node-pack audit ===")
        print(f"Installed node packs: {len(installed)}")
        print(f"Potential installed node-ID collisions: {len(collisions)}")
        print_collision_section(groups)
        if args.verbose:
            print_verbose(
                custom_nodes,
                manager_map,
                url,
                workflow_root,
                collisions,
                args.only_used_overlaps,
                workflow_errors,
            )
        return 0

    print_overview(
        installed,
        pack_nodes,
        len(paths),
        active_usage,
        missing_occurrences,
        dependencies,
        collisions,
        workflow_errors,
    )
    print_plain_summary(missing_occurrences, dependencies, collisions)
    print_missing_dependencies(dependencies)
    print_collision_section(groups)
    print_cleanup_candidates(installed, pack_nodes, active_usage)

    if args.verbose:
        print_verbose(
            custom_nodes,
            manager_map,
            url,
            workflow_root,
            collisions,
            args.only_used_overlaps,
            workflow_errors,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
