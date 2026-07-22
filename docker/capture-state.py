#!/usr/bin/env python3
"""Capture reproducible ComfierUI and custom-node state before a backup.

The capture is observational. It never imports node code, changes a checkout,
contacts an upstream service, or attempts to repair an installation.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import subprocess
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility for host-managed backups.
    tomllib = None  # type: ignore[assignment]


EXCLUDED_CONTENT_DIRS = {".git", ".venv", "__pycache__", "node_modules"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--custom-nodes", type=Path, required=True)
    parser.add_argument("--runtime-state", type=Path, required=True)
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument(
        "--compose-files",
        default=os.environ.get("COMFYUI_BACKUP_COMPOSE_FILES", "compose.yaml"),
        help="colon-separated Compose paths relative to --repository",
    )
    return parser.parse_args()


def run_git(path: Path, *arguments: str, check: bool = True) -> str:
    process = subprocess.run(
        ["git", "-C", str(path), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if check and process.returncode != 0:
        raise RuntimeError(f"git {' '.join(arguments)} failed in {path}")
    return process.stdout.strip()


def sanitize_remote(value: str) -> tuple[str, bool]:
    """Remove HTTP(S) user information while retaining a useful remote URL."""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value, False
    if parsed.scheme not in {"http", "https"} or "@" not in parsed.netloc:
        return value, False
    hostname = parsed.hostname or ""
    if parsed.port:
        hostname = f"{hostname}:{parsed.port}"
    return urlunsplit((parsed.scheme, hostname, parsed.path, parsed.query, parsed.fragment)), True


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hash_paths(root: Path, paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    for path in sorted(set(paths), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        try:
            if path.is_symlink():
                digest.update(b"symlink\0")
                digest.update(os.readlink(path).encode("utf-8", errors="surrogateescape"))
            elif path.is_file():
                digest.update(b"file\0")
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
            else:
                digest.update(b"other\0")
        except OSError as exc:
            digest.update(f"unreadable:{exc.errno}".encode())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def ordinary_content_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for directory, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(name for name in dirnames if name not in EXCLUDED_CONTENT_DIRS)
        base = Path(directory)
        for name in sorted(filenames):
            if name.endswith((".pyc", ".pyo")):
                continue
            paths.append(base / name)
    return paths


def git_content_paths(root: Path) -> list[Path]:
    output = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if output.returncode != 0:
        return ordinary_content_paths(root)
    result: list[Path] = []
    for raw_path in output.stdout.split(b"\0"):
        if not raw_path:
            continue
        candidate = root / os.fsdecode(raw_path)
        if candidate.exists() or candidate.is_symlink():
            result.append(candidate)
    return result


def read_cnr_metadata(path: Path) -> dict[str, str | None] | None:
    pyproject = path / "pyproject.toml"
    tracking = path / ".tracking"
    if not pyproject.is_file() or not tracking.is_file():
        return None
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        return None

    if tomllib is not None:
        try:
            project = tomllib.loads(text).get("project", {})
        except tomllib.TOMLDecodeError:
            return None
    else:
        project = {}
        section = ""
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip()
                continue
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = (item.strip() for item in line.split("=", 1))
            try:
                value = ast.literal_eval(raw_value)
            except (ValueError, SyntaxError):
                continue
            if section == "project" and key in {"name", "version"}:
                project[key] = value
            elif section == "project.urls" and key == "Repository":
                project.setdefault("urls", {})[key] = value
    if not isinstance(project, dict):
        return None
    name = project.get("name")
    version = project.get("version")
    urls = project.get("urls", {})
    repository = urls.get("Repository") if isinstance(urls, dict) else None
    if not isinstance(name, str) or not isinstance(version, (str, int, float)):
        return None
    return {
        "id": name.strip().lower(),
        "version": str(version),
        "repository": repository if isinstance(repository, str) else None,
    }


def git_metadata(path: Path, warnings: list[str]) -> dict[str, Any] | None:
    if not (path / ".git").exists():
        return None
    try:
        commit = run_git(path, "rev-parse", "HEAD")
    except (OSError, RuntimeError):
        warnings.append(f"Could not resolve Git state for node pack {path.name}.")
        return None

    branch = run_git(path, "symbolic-ref", "--quiet", "--short", "HEAD", check=False) or "detached"
    remote = run_git(path, "remote", "get-url", "origin", check=False) or None
    if remote:
        remote, redacted = sanitize_remote(remote)
        if redacted:
            warnings.append(f"Credential-bearing remote URL was redacted for node pack {path.name}.")
    tags = [tag for tag in run_git(path, "tag", "--points-at", "HEAD", check=False).splitlines() if tag]
    status_lines = [
        line for line in run_git(path, "status", "--porcelain=v1", "--untracked-files=all", check=False).splitlines() if line
    ]
    submodules = [
        line for line in run_git(path, "submodule", "status", "--recursive", check=False).splitlines() if line
    ]
    return {
        "commit": commit,
        "branch": branch,
        "remote": remote,
        "tags": tags,
        "dirty": bool(status_lines),
        "changes": status_lines,
        "submodules": submodules,
    }


def cnr_id_from_git(path: Path) -> str | None:
    marker = path / ".git" / ".cnr-id"
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def pack_record(path: Path, root: Path, enabled: bool, warnings: list[str]) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    cnr = read_cnr_metadata(path) if path.is_dir() else None
    git = git_metadata(path, warnings) if path.is_dir() else None
    manager_id = cnr["id"] if cnr else (cnr_id_from_git(path) if git else None)

    if cnr:
        install_type = "registry"
        installed_version = cnr["version"]
        # Manager retains the installed Registry version in the unpacked pack,
        # but not whether the user chose that exact version or chose "latest".
        selected_version = None
        update_channel = None
        source_url = cnr["repository"]
        if source_url:
            source_url, redacted = sanitize_remote(source_url)
            if redacted:
                warnings.append(f"Credential-bearing source URL was redacted for node pack {path.name}.")
    elif git:
        install_type = "git"
        installed_version = None
        selected_version = "nightly" if manager_id else None
        update_channel = "nightly" if manager_id else None
        source_url = git["remote"]
    elif path.is_file():
        install_type = "single-file"
        installed_version = None
        selected_version = None
        update_channel = None
        source_url = None
    else:
        install_type = "manual-or-archive"
        installed_version = None
        selected_version = None
        update_channel = None
        source_url = None

    if path.is_file():
        content_hash = f"sha256:{sha256_file(path)}"
        content_hash_scope = "single-file"
    else:
        content_paths = git_content_paths(path) if git else ordinary_content_paths(path)
        content_hash = hash_paths(path, content_paths)
        content_hash_scope = (
            "git-tracked-and-unignored-files"
            if git
            else "files-excluding-runtime-caches"
        )

    return {
        "directory": relative,
        "enabled": enabled,
        "install_type": install_type,
        "manager": {
            "id": manager_id,
            "installed_version": installed_version,
            "selected_version": selected_version,
            "update_channel": update_channel,
            "selection_known": selected_version is not None,
        },
        "source_url": source_url,
        "git": git,
        "content_hash": content_hash,
        "content_hash_scope": content_hash_scope,
    }


def installed_packs(root: Path, warnings: list[str]) -> list[dict[str, Any]]:
    if not root.is_dir():
        warnings.append(f"Custom-node directory is missing: {root}")
        return []

    candidates: list[tuple[Path, bool]] = []
    for path in root.iterdir():
        if path.name in {"__pycache__", ".disabled"}:
            continue
        if path.name.startswith("."):
            continue
        if path.is_dir() or path.suffix == ".py" or path.name.endswith(".py.disabled"):
            candidates.append((path, not path.name.endswith(".disabled")))

    disabled_root = root / ".disabled"
    if disabled_root.is_dir():
        for path in disabled_root.iterdir():
            if path.is_dir() or path.suffix == ".py" or path.name.endswith(".py.disabled"):
                candidates.append((path, False))

    records: list[dict[str, Any]] = []
    for path, enabled in sorted(candidates, key=lambda item: item[0].as_posix().casefold()):
        try:
            records.append(pack_record(path, root, enabled, warnings))
        except Exception as exc:
            warnings.append(f"Could not fully inspect node pack {path.name}: {type(exc).__name__}: {exc}")
            records.append(
                {
                    "directory": path.relative_to(root).as_posix(),
                    "enabled": enabled,
                    "install_type": "unresolved",
                    "manager": {
                        "id": None,
                        "installed_version": None,
                        "selected_version": None,
                        "update_channel": None,
                        "selection_known": False,
                    },
                    "source_url": None,
                    "git": None,
                    "content_hash": None,
                    "content_hash_scope": None,
                }
            )
    return records


def read_json(path: Path, warnings: list[str]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        warnings.append(f"Runtime state is unavailable: {path}")
        return None
    except (OSError, json.JSONDecodeError) as exc:
        warnings.append(f"Runtime state could not be read: {exc}")
        return None
    if not isinstance(value, dict):
        warnings.append("Runtime state has an unexpected top-level type.")
        return None
    return value


def deployment_state(repository: Path, compose_files: str, warnings: list[str]) -> dict[str, Any]:
    git = git_metadata(repository, warnings)
    files: list[dict[str, str | None]] = []
    for configured in compose_files.split(":"):
        if not configured:
            continue
        candidate = Path(configured)
        if candidate.is_absolute():
            files.append({"path": configured, "sha256": None})
            warnings.append(f"Absolute Compose file was not hashed inside the backup container: {configured}")
            continue
        path = repository / candidate
        files.append(
            {
                "path": configured,
                "sha256": f"sha256:{sha256_file(path)}" if path.is_file() else None,
            }
        )
        if not path.is_file():
            warnings.append(f"Configured Compose file is unavailable to state capture: {configured}")
    env_path = repository / ".env"
    return {
        "git": git,
        "compose_files": files,
        "env_sha256": f"sha256:{sha256_file(env_path)}" if env_path.is_file() else None,
    }


def write_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            json.dump(value, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_name = temporary.name
        os.replace(temporary_name, path)
    finally:
        if temporary_name and os.path.exists(temporary_name):
            os.unlink(temporary_name)


def main() -> int:
    args = parse_args()
    warnings: list[str] = []
    captured_at = datetime.now(timezone.utc)
    node_packs = installed_packs(args.custom_nodes, warnings)
    runtime = read_json(args.runtime_state, warnings)
    if isinstance(runtime, dict) and runtime.get("schema") != 2:
        warnings.append(
            "Runtime state uses an older schema; ComfyUI may not have finished "
            "starting after its most recent image update."
        )
    state = {
        "schema_version": 1,
        "capture_id": f"{captured_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:12]}",
        "captured_at": captured_at.isoformat(),
        "source": "pre-restic-backup",
        "deployment": deployment_state(args.repository, args.compose_files, warnings),
        "runtime": runtime,
        "node_packs": node_packs,
        "node_pack_count": len(node_packs),
        "warnings": warnings,
    }
    write_atomic(args.output, state)
    print(state["capture_id"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
