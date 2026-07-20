#!/usr/bin/env python3
"""Audit ComfyUI workflow dependencies, model files, and potential node-ID collisions.

The report is intentionally evidence-oriented:

1. Active workflow node IDs are checked against the running ``/object_info`` registry.
2. Workflow ``properties.cnr_id`` metadata is preferred for identifying missing packs.
3. Older workflows without ``cnr_id`` may inherit a pack ID only when other scanned
   workflows unambiguously identify the exact same node type with one explicit pack ID.
4. Model-file references are checked against choices exposed by registered loader nodes.
   Muted/bypassed model references are reported but separated from active-path errors.
5. ComfyUI Manager's ``extension-node-map.json`` is advisory static-analysis metadata
   used for fallback hints and potential node-ID collision reporting.
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
PRIMITIVE_WIDGET_TYPES = {"INT", "FLOAT", "STRING", "BOOLEAN"}
MODEL_FILE_SUFFIXES = {
    ".safetensors",
    ".ckpt",
    ".pt",
    ".pth",
    ".bin",
    ".gguf",
    ".onnx",
    ".engine",
    ".model",
    ".pkl",
    ".pickle",
}
MODEL_SPECIAL_VALUES = {
    "auto",
    "default",
    "disabled",
    "none",
    "pixel_space",
    "taesd",
    "taesdxl",
    "taesd3",
    "taef1",
}


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
class ModelOccurrence:
    workflow: str
    workflow_node_id: str
    node_type: str
    input_name: str
    category: str
    filename: str
    mode: int


@dataclass(frozen=True)
class MissingModel:
    category: str
    filename: str
    workflows: tuple[str, ...]
    occurrences: tuple[ModelOccurrence, ...]


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
    inferred_occurrences: int
    manager_candidates: tuple[str, ...]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Audit ComfyUI workflow dependencies, missing model files, potential "
            "node-ID collisions, and saved-workflow usage."
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
        help="show source paths and all potential collision details",
    )
    parser.add_argument(
        "--only-used-overlaps",
        action="store_true",
        help=(
            "compatibility alias: with --verbose, limit collision details to IDs "
            "used by the scanned workflows"
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


def manager_candidates_by_node(
    entries: Iterable[ManagerEntry],
) -> dict[str, list[ManagerEntry]]:
    result: dict[str, list[ManagerEntry]] = defaultdict(list)
    for entry in entries:
        for node_id in entry.node_ids:
            result[node_id].append(entry)
    return result


def match_manager_entries_to_installed(
    entries: Iterable[ManagerEntry], installed: Iterable[str]
) -> dict[ManagerEntry, str]:
    by_normalized: dict[str, list[str]] = defaultdict(list)
    for pack in installed:
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
    """Yield nodes from the primary workflow graph only."""
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


def mode_label(mode: int) -> str:
    if mode == 2:
        return "MUTED"
    if mode == 4:
        return "BYPASSED"
    return "ACTIVE"


def model_category(input_name: str, node_type: str) -> str | None:
    """Identify model-file selector inputs, not arbitrary model-related enums."""
    name = input_name.casefold()
    node = node_type.casefold()

    exact = {
        "vae_name": "vae",
        "ckpt_name": "checkpoint",
        "checkpoint_name": "checkpoint",
        "lora_name": "lora",
        "control_net_name": "controlnet",
        "controlnet_name": "controlnet",
        "clip_vision_name": "clip_vision",
        "clip_name": "clip",
        "upscale_model_name": "upscale_model",
        "unet_name": "diffusion_model",
        "diffusion_model_name": "diffusion_model",
        "style_model_name": "style_model",
        "gligen_name": "gligen",
        "ipadapter_file": "ipadapter",
        "ipadapter_name": "ipadapter",
    }
    if name in exact:
        return exact[name]

    if name in {"model_name", "model", "file", "filename"}:
        if "upscalemodelloader" in node:
            return "upscale_model"
        if "unetloader" in node or "diffusionmodelloader" in node:
            return "diffusion_model"
        if "stylemodelloader" in node:
            return "style_model"
        if "clipvisionloader" in node:
            return "clip_vision"
        if "controlnetloader" in node:
            return "controlnet"
        if "checkpointloader" in node:
            return "checkpoint"
        if "vaeloader" in node:
            return "vae"
        if "gligenloader" in node:
            return "gligen"
        if "ipadaptermodelloader" in node:
            return "ipadapter"

    return None


def model_category_label(category: str) -> str:
    labels = {
        "vae": "VAE",
        "checkpoint": "checkpoint",
        "lora": "LoRA",
        "controlnet": "ControlNet model",
        "clip_vision": "CLIP Vision model",
        "clip": "CLIP model",
        "upscale_model": "upscale model",
        "diffusion_model": "diffusion model",
        "style_model": "style model",
        "gligen": "GLIGEN model",
        "ipadapter": "IPAdapter model",
    }
    return labels.get(category, category)


def looks_like_model_file(value: str) -> bool:
    clean = value.strip()
    if not clean or clean.casefold() in MODEL_SPECIAL_VALUES:
        return False
    suffix = Path(clean.replace("\\", "/")).suffix.casefold()
    return suffix in MODEL_FILE_SUFFIXES


def widget_input_specs(info: dict[str, Any]) -> list[tuple[str, Any]]:
    """Return input specs that consume entries from workflow ``widgets_values``."""
    result: list[tuple[str, Any]] = []
    input_block = info.get("input")
    if not isinstance(input_block, dict):
        return result

    for section_name in ("required", "optional"):
        section = input_block.get(section_name)
        if not isinstance(section, dict):
            continue
        for input_name, spec in section.items():
            if not isinstance(input_name, str):
                continue
            if not isinstance(spec, (list, tuple)) or not spec:
                continue

            head = spec[0]
            options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            if options.get("forceInput") is True:
                continue

            if isinstance(head, list):
                result.append((input_name, spec))
                continue
            if isinstance(head, str) and head in PRIMITIVE_WIDGET_TYPES:
                result.append((input_name, spec))

    return result


def missing_models_for_node(
    workflow: str,
    node: dict[str, Any],
    info: dict[str, Any],
    mode: int,
) -> list[ModelOccurrence]:
    widgets = node.get("widgets_values")
    if not isinstance(widgets, list):
        return []

    specs = widget_input_specs(info)
    missing: list[ModelOccurrence] = []

    for index, (input_name, spec) in enumerate(specs):
        if index >= len(widgets):
            break
        selected = widgets[index]
        head = spec[0]

        if not isinstance(head, list):
            continue
        if not all(isinstance(choice, str) for choice in head):
            continue

        category = model_category(input_name, str(node.get("type", "")))
        if category is None or not isinstance(selected, str) or not selected.strip():
            continue

        if not looks_like_model_file(selected):
            continue

        if selected not in head:
            missing.append(
                ModelOccurrence(
                    workflow=workflow,
                    workflow_node_id=str(node.get("id", "?")),
                    node_type=str(node.get("type", "?")),
                    input_name=input_name,
                    category=category,
                    filename=selected,
                    mode=mode,
                )
            )

    return missing


def scan_workflows(
    root: Path,
    paths: list[Path],
    object_info: dict[str, dict[str, Any]],
) -> tuple[
    dict[str, set[str]],
    dict[str, list[NodeOccurrence]],
    dict[str, list[NodeOccurrence]],
    list[ModelOccurrence],
    list[tuple[str, str]],
]:
    active_usage: dict[str, set[str]] = defaultdict(set)
    active_occurrences: dict[str, list[NodeOccurrence]] = defaultdict(list)
    missing_occurrences: dict[str, list[NodeOccurrence]] = defaultdict(list)
    missing_models: list[ModelOccurrence] = []
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

            info = object_info.get(node_type)
            if info is not None:
                missing_models.extend(
                    missing_models_for_node(relative, node, info, mode)
                )

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

            if info is None:
                missing_occurrences[node_type].append(occurrence)

    return (
        active_usage,
        active_occurrences,
        missing_occurrences,
        missing_models,
        errors,
    )


def build_missing_models(occurrences: list[ModelOccurrence]) -> list[MissingModel]:
    grouped: dict[tuple[str, str], list[ModelOccurrence]] = defaultdict(list)
    for occurrence in occurrences:
        grouped[(occurrence.category, occurrence.filename)].append(occurrence)

    result: list[MissingModel] = []
    for (category, filename), items in grouped.items():
        result.append(
            MissingModel(
                category=category,
                filename=filename,
                workflows=tuple(sorted({item.workflow for item in items})),
                occurrences=tuple(
                    sorted(
                        items,
                        key=lambda item: (
                            item.workflow.casefold(),
                            item.node_type.casefold(),
                            item.workflow_node_id,
                        ),
                    )
                ),
            )
        )
    return sorted(result, key=lambda item: (item.category, item.filename.casefold()))


def find_installed_pack(pack_id: str, installed: Iterable[str]) -> str | None:
    target = normalize_pack_name(pack_id)
    matches = [pack for pack in installed if normalize_pack_name(pack) == target]
    return matches[0] if len(matches) == 1 else None


def manager_candidate_names(entries: Iterable[ManagerEntry]) -> tuple[str, ...]:
    values = {entry.title for entry in entries}
    return tuple(sorted(values, key=str.casefold))


def explicit_pack_by_node_type(
    missing_occurrences: dict[str, list[NodeOccurrence]],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for node_type, occurrences in missing_occurrences.items():
        packs = {occ.cnr_id for occ in occurrences if occ.cnr_id}
        if len(packs) == 1:
            result[node_type] = next(iter(packs))
    return result


def build_missing_dependencies(
    missing_occurrences: dict[str, list[NodeOccurrence]],
    manager_by_node: dict[str, list[ManagerEntry]],
    installed: list[str],
) -> list[MissingDependency]:
    explicit_map = explicit_pack_by_node_type(missing_occurrences)
    grouped: dict[str, list[tuple[NodeOccurrence, bool]]] = defaultdict(list)
    group_display: dict[str, str] = {}

    for node_type, occurrences in missing_occurrences.items():
        inferred_pack = explicit_map.get(node_type)
        for occurrence in occurrences:
            pack = occurrence.cnr_id
            inferred = False
            if not pack and inferred_pack:
                pack = inferred_pack
                inferred = True

            if pack:
                key = f"cnr:{pack}"
                group_display[key] = pack
            else:
                key = f"unknown:{node_type}"
                group_display[key] = "UNKNOWN"
            grouped[key].append((occurrence, inferred))

    dependencies: list[MissingDependency] = []
    for key, occurrence_pairs in grouped.items():
        occurrences = [item[0] for item in occurrence_pairs]
        inferred_count = sum(1 for _, inferred in occurrence_pairs if inferred)
        node_types = tuple(sorted({occ.node_type for occ in occurrences}, key=str.casefold))
        workflows = tuple(sorted({occ.workflow for occ in occurrences}))
        display_pack = group_display[key]
        installed_pack = (
            find_installed_pack(display_pack, installed)
            if display_pack != "UNKNOWN"
            else None
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
                inferred_occurrences=inferred_count,
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


def model_reference_counts(models: list[MissingModel]) -> tuple[int, int]:
    active = 0
    inactive = 0
    for model in models:
        for occurrence in model.occurrences:
            if occurrence.mode in INACTIVE_NODE_MODES:
                inactive += 1
            else:
                active += 1
    return active, inactive


def print_overview(
    installed: list[str],
    pack_nodes: dict[str, set[str]],
    workflow_count: int,
    active_usage: dict[str, set[str]],
    missing_occurrences: dict[str, list[NodeOccurrence]],
    missing_dependencies: list[MissingDependency],
    missing_models: list[MissingModel],
    collisions: list[Collision],
    workflow_errors: list[tuple[str, str]],
) -> None:
    installed_set = set(installed)
    running_packs = {pack for pack in pack_nodes if pack in installed_set}
    used_collisions = [item for item in collisions if item.workflow_files]
    missing_node_instances = sum(len(items) for items in missing_occurrences.values())
    active_model_refs, inactive_model_refs = model_reference_counts(missing_models)
    known_missing_packs = {
        dep.display_pack for dep in missing_dependencies if dep.display_pack != "UNKNOWN"
    }
    active_path_errors = missing_node_instances + active_model_refs
    total_issues = missing_node_instances + active_model_refs + inactive_model_refs

    print("=== ComfyUI workflow audit ===")
    print(f"Installed node packs:                    {len(installed)}")
    print(f"Running node packs detected:             {len(running_packs)}")
    print(f"Saved workflows scanned:                 {workflow_count}")
    print(f"Distinct active node types used:         {len(active_usage)}")
    print(f"Missing active node types:               {len(missing_occurrences)}")
    print(f"Missing active node instances:           {missing_node_instances}")
    print(f"Intended missing pack IDs found:         {len(known_missing_packs)}")
    print(f"Distinct missing model files:            {len(missing_models)}")
    print(f"Missing model references:                {active_model_refs + inactive_model_refs}")
    print(f"  Active:                                {active_model_refs}")
    print(f"  Muted/bypassed:                        {inactive_model_refs}")
    print(f"Active-path errors:                      {active_path_errors}")
    print(f"Total detected issues:                   {total_issues}")
    print(f"Potential installed node-ID collisions:  {len(collisions)}")
    print(f"Collision IDs used by saved workflows:   {len(used_collisions)}")
    print(f"Unreadable workflow files:               {len(workflow_errors)}")


def print_plain_summary(
    missing_occurrences: dict[str, list[NodeOccurrence]],
    missing_dependencies: list[MissingDependency],
    missing_models: list[MissingModel],
    collisions: list[Collision],
) -> None:
    used_collisions = [item for item in collisions if item.workflow_files]
    print("\n=== Summary ===")

    if missing_occurrences:
        workflows = {
            occ.workflow
            for items in missing_occurrences.values()
            for occ in items
        }
        print(
            f"- PROBLEM: {len(missing_occurrences)} active node type"
            f"{'s are' if len(missing_occurrences) != 1 else ' is'} missing across "
            f"{len(workflows)} saved workflow{'s' if len(workflows) != 1 else ''}."
        )
        explicit = [dep for dep in missing_dependencies if dep.display_pack != "UNKNOWN"]
        if explicit:
            missing_pack_count = sum(1 for dep in explicit if dep.installed_pack is None)
            installed_but_missing = sum(
                1 for dep in explicit if dep.installed_pack is not None
            )
            inferred = sum(dep.inferred_occurrences for dep in explicit)
            text = (
                f"- Workflow evidence identifies {len(explicit)} intended pack ID"
                f"{'s' if len(explicit) != 1 else ''}: {missing_pack_count} are not "
                f"installed under that ID; {installed_but_missing} are installed but "
                "still fail to provide the expected node(s)."
            )
            if inferred:
                text += (
                    f" {inferred} older missing-node occurrence"
                    f"{'s were' if inferred != 1 else ' was'} assigned using unambiguous "
                    "cnr_id evidence from the same node type in other scanned workflows."
                )
            print(text)
    else:
        print("- OK: No active workflow node types are missing from the running ComfyUI.")

    active_model_refs, inactive_model_refs = model_reference_counts(missing_models)
    if active_model_refs:
        print(
            f"- PROBLEM: {active_model_refs} missing model reference"
            f"{'s are' if active_model_refs != 1 else ' is'} on the active execution path."
        )
    if inactive_model_refs:
        print(
            f"- REVIEW: {inactive_model_refs} missing model reference"
            f"{'s are' if inactive_model_refs != 1 else ' is'} only in muted/bypassed nodes. "
            "Manager may still report these, but they do not currently block the active path."
        )
    if not missing_models:
        print("- OK: No missing model files were detected in registered loader nodes.")

    if used_collisions:
        missing_collision_ids = sum(
            1 for item in used_collisions if item.current_provider is None
        )
        print(
            f"- REVIEW: {len(used_collisions)} workflow-used node ID"
            f"{'s have' if len(used_collisions) != 1 else ' has'} potential "
            f"installed-pack collisions. {len(used_collisions) - missing_collision_ids} "
            f"currently have a live provider; {missing_collision_ids} do not. "
            "Collision data is advisory."
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
        if dep.inferred_occurrences:
            print(
                "  Pack identification: explicit workflow cnr_id evidence; "
                f"inferred for {dep.inferred_occurrences} older occurrence"
                f"{'s' if dep.inferred_occurrences != 1 else ''} of the same exact node type"
            )
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
        print("    active workflow nodes cannot execute as currently saved.")
        print("  Most likely next step:")
        if dep.display_pack == "UNKNOWN":
            print("    Identify the original pack, then install/restore a compatible version")
            print("    if the workflow matters. Manager candidates are hints, not proof.")
        elif dep.installed_pack:
            print("    The expected pack is installed, so inspect its import/startup logs")
            print("    and version history for a failed import, removed node ID, or")
            print("    optional dependency.")
        else:
            print("    Install/restore the workflow-recorded pack if the workflow matters,")
            print("    or archive the workflow if it no longer matters.")


def print_missing_models(models: list[MissingModel]) -> None:
    print("\n=== Missing model files ===")
    if not models:
        print("None.")
        return

    for model in models:
        label = model_category_label(model.category)
        states = {mode_label(item.mode) for item in model.occurrences}
        state = "ACTIVE" if "ACTIVE" in states else ("BYPASSED" if "BYPASSED" in states else "MUTED")
        print(f"\n[MISSING {label.upper()}] {model.filename}")
        print(f"  State: {state}")
        print(f"  Category: {label}")
        print("  Saved workflows:")
        for workflow in model.workflows:
            print(f"    - {workflow}")
        print("  Referenced by:")
        for occurrence in model.occurrences:
            print(
                f"    - {occurrence.node_type} "
                f"(workflow node {occurrence.workflow_node_id}, input {occurrence.input_name}, "
                f"{mode_label(occurrence.mode)})"
            )
        print("  What is actually wrong:")
        print("    The workflow selects this file, but the running loader node does not")
        print("    list it among the model choices currently available to ComfyUI.")
        print("  Impact:")
        if any(item.mode not in INACTIVE_NODE_MODES for item in model.occurrences):
            print("    At least one active node references this missing model, so it can block")
            print("    the workflow's current execution path.")
        else:
            print("    All references are muted/bypassed, so this does not currently block")
            print("    the active execution path. It will matter if those nodes are enabled.")
        print("  Most likely next step:")
        print(
            f"    Add/restore the file in a configured {label} model path, or select "
            f"another {label} that is currently available."
        )


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
    missing_models: list[MissingModel],
    collisions: list[Collision],
    workflow_errors: list[tuple[str, str]],
) -> dict[str, Any]:
    running = {pack for pack in pack_nodes if pack in set(installed)}
    missing_node_instances = sum(len(v) for v in missing_occurrences.values())
    active_model_refs, inactive_model_refs = model_reference_counts(missing_models)

    return {
        "summary": {
            "installed_packs": len(installed),
            "running_packs": len(running),
            "workflows_scanned": len(paths),
            "active_node_types": len(active_usage),
            "missing_active_node_types": len(missing_occurrences),
            "missing_active_node_instances": missing_node_instances,
            "missing_dependency_groups": len(dependencies),
            "distinct_missing_model_files": len(missing_models),
            "missing_model_references": active_model_refs + inactive_model_refs,
            "active_missing_model_references": active_model_refs,
            "inactive_missing_model_references": inactive_model_refs,
            "active_path_errors": missing_node_instances + active_model_refs,
            "total_detected_issues": missing_node_instances + active_model_refs + inactive_model_refs,
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
                "inferred_occurrences": dep.inferred_occurrences,
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
        "missing_models": [
            {
                "category": model.category,
                "filename": model.filename,
                "workflows": list(model.workflows),
                "occurrences": [
                    {
                        "workflow": occ.workflow,
                        "workflow_node_id": occ.workflow_node_id,
                        "node_type": occ.node_type,
                        "input_name": occ.input_name,
                        "mode": occ.mode,
                        "state": mode_label(occ.mode),
                    }
                    for occ in model.occurrences
                ],
            }
            for model in missing_models
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
        model_occurrences: list[ModelOccurrence] = []
        workflow_errors: list[tuple[str, str]] = []

        if workflow_root is not None:
            paths = workflow_paths(workflow_root, args.workflow)
            (
                active_usage,
                active_occurrences,
                missing_occurrences,
                model_occurrences,
                workflow_errors,
            ) = scan_workflows(workflow_root, paths, object_info)

        dependencies = build_missing_dependencies(
            missing_occurrences, manager_by_node, installed
        )
        missing_models = build_missing_models(model_occurrences)
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
                    missing_models,
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
        missing_models,
        collisions,
        workflow_errors,
    )
    print_plain_summary(
        missing_occurrences,
        dependencies,
        missing_models,
        collisions,
    )
    print_missing_dependencies(dependencies)
    print_missing_models(missing_models)
    print_collision_section(groups)

    if not args.workflow:
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
