#!/usr/bin/env python3
"""Read-only diagnosis of failed ComfyUI custom-node installations/imports."""
from __future__ import annotations

import argparse
import ast
import json
import re
import shlex
import subprocess
import sys
import warnings
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

STARTUP = re.compile(r"\*\* ComfyUI startup time:\s*(.+)$")
CANNOT_IMPORT = re.compile(r"Cannot import\s+(.+?)\s+module for custom nodes:\s*(.*)$")
IMPORT_FAILED = re.compile(r"\(IMPORT FAILED\):\s*(\S.*?)\s*$")
EXCEPTION = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception)):\s*(.*)$")
MISSING_MODULE = re.compile(r"No module named ['\"]([^'\"]+)['\"]")
PERMISSION_PATH = re.compile(r"Permission denied:\s*['\"]([^'\"]+)['\"]")
FILE_PATH = re.compile(r"No such file or directory:\s*['\"]([^'\"]+)['\"]")
TRACEBACK_FILE = re.compile(r'^\s*File\s+["\']([^"\']+)["\'],\s+line\s+(\d+)')
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
VERSIONS = {
    "comfyui": re.compile(r"ComfyUI version:\s*(\S+)"),
    "manager": re.compile(r"ComfyUI-Manager \(V([^\)]+)\)"),
    "python": re.compile(r"Python version:\s*([^\[]+?)(?:\s*\[|$)"),
}
SEVERITY_RANK = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Explain current ComfyUI custom-node installation/import failures "
            "without modifying production."
        )
    )
    parser.add_argument("--container", help="container ID/name; default: auto-discover")
    parser.add_argument("--custom-nodes", type=Path, help="host custom_nodes path")
    parser.add_argument("--log-file", type=Path, help="offline log instead of docker logs")
    parser.add_argument("--log-lines", type=int, default=30000)
    parser.add_argument("--all-history", action="store_true")
    parser.add_argument("--no-static-scan", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--json", action="store_true")
    return parser.parse_args()


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    merge: bool = False,
    timeout: int = 60,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge else subprocess.PIPE,
            check=check,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"required command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stdout or exc.stderr or "").strip()
        raise RuntimeError(f"command failed: {shlex.join(command)}: {detail}") from exc


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def discover_container(explicit: str | None) -> str:
    if explicit:
        return explicit

    result = run(
        ["docker", "compose", "ps", "-q", "comfyui"],
        cwd=repo_root(),
        check=False,
    )
    found = [value.strip() for value in result.stdout.splitlines() if value.strip()]

    if not found:
        result = run(
            [
                "docker",
                "ps",
                "--filter",
                "label=com.docker.compose.service=comfyui",
                "--format",
                "{{.ID}}",
            ],
            check=False,
        )
        found = [value.strip() for value in result.stdout.splitlines() if value.strip()]

    if len(found) != 1:
        raise RuntimeError(
            "could not uniquely discover ComfyUI container; use --container"
        )
    return found[0]


def inspect_container(container: str) -> dict[str, Any]:
    payload = json.loads(run(["docker", "inspect", container]).stdout)[0]
    state = payload.get("State", {})
    config = payload.get("Config", {})

    mounts: list[dict[str, Any]] = []
    custom_host: str | None = None
    for item in payload.get("Mounts", []):
        mount = {
            "source": item.get("Source"),
            "destination": item.get("Destination"),
            "rw": item.get("RW"),
            "type": item.get("Type"),
        }
        mounts.append(mount)
        if mount["destination"] == "/opt/ComfyUI/custom_nodes":
            custom_host = mount["source"]

    uid = run(["docker", "exec", container, "id", "-u"], check=False).stdout.strip()
    gid = run(["docker", "exec", container, "id", "-g"], check=False).stdout.strip()

    return {
        "container_id": payload.get("Id"),
        "container_name": str(payload.get("Name", "")).lstrip("/"),
        "image": config.get("Image"),
        "status": state.get("Status"),
        "health": state.get("Health", {}).get("Status"),
        "runtime_uid": int(uid) if uid.isdigit() else None,
        "runtime_gid": int(gid) if gid.isdigit() else None,
        "custom_nodes_host": custom_host,
        "mounts": mounts,
    }


def read_logs(
    namespace: argparse.Namespace, container: str | None
) -> tuple[list[str], str]:
    if namespace.log_file:
        text = namespace.log_file.read_text(encoding="utf-8", errors="replace")
        source = str(namespace.log_file)
    else:
        result = run(
            [
                "docker",
                "logs",
                "--tail",
                str(namespace.log_lines),
                container or "",
            ],
            check=False,
            merge=True,
        )
        text = result.stdout
        source = f"docker logs --tail {namespace.log_lines} {container}"

    return [ANSI.sub("", line.rstrip()) for line in text.splitlines()], source


def startup_sessions(lines: list[str]) -> list[dict[str, Any]]:
    marks = [
        (index, match.group(1).strip())
        for index, line in enumerate(lines)
        if (match := STARTUP.search(line))
    ]
    if not marks:
        return [
            {
                "number": 1,
                "started": None,
                "start_index": 0,
                "end_index": len(lines),
                "lines": lines,
            }
        ]

    sessions: list[dict[str, Any]] = []
    for position, (start, when) in enumerate(marks, 1):
        end = marks[position][0] if position < len(marks) else len(lines)
        sessions.append(
            {
                "number": position,
                "started": when,
                "start_index": start,
                "end_index": end,
                "lines": lines[start:end],
            }
        )
    return sessions


def extract_exception(trace: list[str], summary: str) -> tuple[str | None, str]:
    for raw in reversed(trace):
        line = raw.strip().removeprefix("[WARNING] ").removeprefix("[ERROR] ")
        if match := EXCEPTION.match(line):
            return match.group(1), match.group(2)
    return None, summary


def import_failures(session: dict[str, Any]) -> list[dict[str, Any]]:
    lines = session["lines"]
    failures: list[dict[str, Any]] = []
    seen: set[str] = set()

    for index, line in enumerate(lines):
        match = CANNOT_IMPORT.search(line)
        if not match:
            continue

        path = match.group(1).strip()
        summary = match.group(2).strip()
        start: int | None = None
        for previous in range(index - 1, max(-1, index - 250), -1):
            if "Traceback (most recent call last):" in lines[previous]:
                start = previous
                break
            if CANNOT_IMPORT.search(lines[previous]):
                break

        trace = lines[start:index] if start is not None else []
        exception_type, exception_message = extract_exception(trace, summary)
        failures.append(
            {
                "pack": Path(path.rstrip("/")).name,
                "container_path": path,
                "summary": summary,
                "traceback": trace,
                "exception_type": exception_type,
                "exception_message": exception_message,
                "startup": session["number"],
                "started": session["started"],
            }
        )
        seen.add(path)

    for line in lines:
        match = IMPORT_FAILED.search(line)
        if not match:
            continue
        path = match.group(1).strip()
        if path in seen:
            continue
        failures.append(
            {
                "pack": Path(path.rstrip("/")).name,
                "container_path": path,
                "summary": "IMPORT FAILED without captured traceback",
                "traceback": [],
                "exception_type": None,
                "exception_message": None,
                "startup": session["number"],
                "started": session["started"],
            }
        )

    return failures


def session_versions(session: dict[str, Any]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in session["lines"]:
        for name, pattern in VERSIONS.items():
            if match := pattern.search(line):
                found[name] = match.group(1).strip()
    return found


def successful_imports(session: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for line in session["lines"]:
        if "IMPORT FAILED" in line or "/custom_nodes/" not in line:
            continue
        if "seconds:" in line:
            found.add(Path(line.rsplit(":", 1)[-1].strip()).name)
    return found


def containing_session(index: int, sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    for session in sessions:
        if session["start_index"] <= index < session["end_index"]:
            return session
    return None


def next_session(index: int, sessions: list[dict[str, Any]]) -> dict[str, Any] | None:
    for session in sessions:
        if session["start_index"] > index:
            return session
    return None


def manager_events(
    lines: list[str], sessions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    latest_number = sessions[-1]["number"]

    for index, line in enumerate(lines):
        lower = line.casefold()
        kind: str | None = None
        pack: str | None = None
        detail: str | None = None

        if "extracted zip file to" in lower:
            kind = "package_extracted"
            path = line.split("to", 1)[-1].strip().strip("'\"")
            pack = Path(path).name
            detail = path
        elif "security_level" in lower and "error" in lower:
            kind = "manager_security_block"
            detail = line.strip()
        elif (
            ("failed" in lower and any(
                token in lower
                for token in ("install", "pip", "dependency", "extract", "download")
            ))
            or "no matching distribution found" in lower
            or "could not find a version that satisfies" in lower
            or "badzipfile" in lower
        ):
            kind = "manager_failure"
            detail = line.strip()

        if kind is None:
            continue

        observed = containing_session(index, sessions)
        applies = next_session(index, sessions) if kind in {
            "package_extracted",
            "manager_failure",
        } else observed

        events.append(
            {
                "kind": kind,
                "pack": pack,
                "detail": detail,
                "line": index + 1,
                "observed_startup": observed["number"] if observed else None,
                "observed_started": observed["started"] if observed else None,
                "applies_to_startup": applies["number"] if applies else None,
                "applies_to_started": applies["started"] if applies else None,
                "current": bool(applies and applies["number"] == latest_number),
                "pending_restart": kind in {"package_extracted", "manager_failure"}
                and applies is None,
            }
        )

    return events[-200:]


def map_container_path(
    container_path: str,
    environment: dict[str, Any],
    explicit_custom_nodes: Path | None,
) -> str | None:
    mounts = sorted(
        environment.get("mounts", []),
        key=lambda item: len(item.get("destination") or ""),
        reverse=True,
    )
    for mount in mounts:
        destination = str(mount.get("destination") or "").rstrip("/")
        if container_path == destination or container_path.startswith(destination + "/"):
            suffix = container_path[len(destination):].lstrip("/")
            return str(Path(mount["source"]) / suffix)

    if explicit_custom_nodes:
        return str(explicit_custom_nodes / Path(container_path).name)
    return None


def local_modules(pack: Path) -> set[str]:
    found: set[str] = set()
    for item in pack.iterdir():
        if item.is_file() and item.suffix == ".py":
            found.add(item.stem)
        elif item.is_dir() and (item / "__init__.py").is_file():
            found.add(item.name)
    return found


def call_name(node: ast.Call) -> str:
    target = node.func
    parts: list[str] = []
    while isinstance(target, ast.Attribute):
        parts.append(target.attr)
        target = target.value
    if isinstance(target, ast.Name):
        parts.append(target.id)
    return ".".join(reversed(parts))


def finding(
    severity: str,
    code: str,
    file: str,
    line: int | None,
    evidence: str | None,
) -> dict[str, Any]:
    return {
        "severity": severity,
        "code": code,
        "file": file,
        "line": line,
        "evidence": evidence,
    }


def scan_pack(name: str, location: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "pack": name,
        "host_path": location,
        "exists": False,
        "local_modules": [],
        "findings": [],
        "requirements": [],
    }
    if not location or not (pack := Path(location)).is_dir():
        return result

    result["exists"] = True
    modules = local_modules(pack)
    result["local_modules"] = sorted(modules)

    requirements = pack / "requirements.txt"
    if requirements.is_file():
        result["requirements"] = [
            line.strip()
            for line in requirements.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

    python_files = sorted(pack.rglob("*.py"))[:500]
    for path in python_files:
        relative_parts = path.relative_to(pack).parts
        if any(part in {".git", "__pycache__", ".venv", "venv"} for part in relative_parts):
            continue

        relative = str(path.relative_to(pack))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:2_000_000]
        except OSError as exc:
            result["findings"].append(
                finding("HIGH", "SOURCE_READ_FAILURE", relative, None, str(exc))
            )
            continue

        caught: list[warnings.WarningMessage]
        try:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always", SyntaxWarning)
                tree = ast.parse(text, filename=str(path))
        except SyntaxError as exc:
            result["findings"].append(
                finding(
                    "HIGH",
                    "SOURCE_PARSE_FAILURE",
                    relative,
                    exc.lineno,
                    str(exc),
                )
            )
            continue

        for warning in caught:
            result["findings"].append(
                finding(
                    "LOW",
                    "PYTHON_SYNTAX_WARNING",
                    relative,
                    warning.lineno,
                    str(warning.message),
                )
            )

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.split(".", 1)[0] in modules:
                    result["findings"].append(
                        finding(
                            "MEDIUM",
                            "ABSOLUTE_SIBLING_IMPORT",
                            relative,
                            node.lineno,
                            ast.get_source_segment(text, node),
                        )
                    )

            if not isinstance(node, ast.Call):
                continue

            called = call_name(node)
            call_codes = {
                "sys.path.append": ("MEDIUM", "SYS_PATH_MUTATION"),
                "sys.path.insert": ("MEDIUM", "SYS_PATH_MUTATION"),
                "os.system": ("HIGH", "SUBPROCESS_EXECUTION"),
                "subprocess.run": ("HIGH", "SUBPROCESS_EXECUTION"),
                "subprocess.Popen": ("HIGH", "SUBPROCESS_EXECUTION"),
                "eval": ("HIGH", "DYNAMIC_CODE_EXECUTION"),
                "exec": ("HIGH", "DYNAMIC_CODE_EXECUTION"),
                "requests.get": ("MEDIUM", "NETWORK_ACCESS"),
                "requests.post": ("MEDIUM", "NETWORK_ACCESS"),
                "urllib.request.urlopen": ("MEDIUM", "NETWORK_ACCESS"),
                "os.makedirs": ("MEDIUM", "FILESYSTEM_CHANGE"),
                "os.mkdir": ("MEDIUM", "FILESYSTEM_CHANGE"),
                "shutil.copy": ("HIGH", "FILESYSTEM_CHANGE"),
                "shutil.move": ("HIGH", "FILESYSTEM_CHANGE"),
                "os.chmod": ("HIGH", "PERMISSION_CHANGE"),
                "os.chown": ("HIGH", "PERMISSION_CHANGE"),
            }
            if called in call_codes:
                severity, code = call_codes[called]
                result["findings"].append(
                    finding(
                        severity,
                        code,
                        relative,
                        node.lineno,
                        ast.get_source_segment(text, node),
                    )
                )

        for number, line in enumerate(text.splitlines(), 1):
            lower = line.casefold()
            if "/opt/comfyui" in lower or "custom_nodes/" in lower:
                result["findings"].append(
                    finding(
                        "LOW",
                        "HARDCODED_COMFYUI_PATH",
                        relative,
                        number,
                        line.strip(),
                    )
                )
            if any(
                token in lower
                for token in ("pip install", "git clone", "curl ", "wget ", "sudo ")
            ):
                result["findings"].append(
                    finding(
                        "HIGH",
                        "INSTALL_OR_SHELL_COMMAND",
                        relative,
                        number,
                        line.strip(),
                    )
                )
            if any(
                token in lower
                for token in ("/var/run/docker.sock", ".ssh/", "id_rsa")
            ):
                result["findings"].append(
                    finding(
                        "CRITICAL",
                        "SENSITIVE_HOST_ACCESS",
                        relative,
                        number,
                        line.strip(),
                    )
                )

    unique = {
        (
            item["code"],
            item["file"],
            item["line"],
            item.get("evidence"),
        ): item
        for item in result["findings"]
    }
    result["findings"] = sorted(
        unique.values(),
        key=lambda item: (
            SEVERITY_RANK.get(item["severity"], 99),
            item["file"],
            item["line"] or 0,
            item["code"],
        ),
    )
    return result


def traceback_files(failure: dict[str, Any]) -> set[str]:
    package_prefix = failure["container_path"].rstrip("/") + "/"
    files: set[str] = {"__init__.py"}
    for line in failure["traceback"]:
        match = TRACEBACK_FILE.match(line.removeprefix("[WARNING] ").removeprefix("[ERROR] "))
        if not match:
            continue
        path = match.group(1)
        if path.startswith(package_prefix):
            files.add(path[len(package_prefix):])
    return files


def split_findings(
    failure: dict[str, Any], scan: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    relevant_files = traceback_files(failure)
    missing_name: str | None = None
    text = "\n".join(failure["traceback"] + [failure["summary"]])
    if match := MISSING_MODULE.search(text):
        missing_name = match.group(1).split(".", 1)[0]

    relevant: list[dict[str, Any]] = []
    pack_wide: list[dict[str, Any]] = []
    for item in scan["findings"]:
        evidence = item.get("evidence") or ""
        is_relevant = item["file"] in relevant_files
        if missing_name and missing_name in evidence:
            is_relevant = True
        copied = dict(item)
        copied["relevance"] = "failure" if is_relevant else "pack-wide"
        (relevant if is_relevant else pack_wide).append(copied)
    return relevant, pack_wide


def diagnose(failure: dict[str, Any], scan: dict[str, Any]) -> dict[str, Any]:
    text = "\n".join(failure["traceback"] + [failure["summary"]])
    diagnosis: dict[str, Any] = {
        "evidence": [
            f"{failure['exception_type'] or 'Import failure'}: "
            f"{failure['exception_message'] or failure['summary']}"
        ]
    }

    if match := MISSING_MODULE.search(text):
        module = match.group(1).split(".", 1)[0]
        if module in scan["local_modules"]:
            diagnosis.update(
                category="PACKAGE DEFECT",
                confidence="HIGH",
                explanation=(
                    "The pack cannot import one of its own files; it likely uses a "
                    "fragile sibling import or directory-name assumption."
                ),
                reinstall="No; identical source will fail again.",
                git="Maybe, only if newer upstream source contains a fix.",
                sandbox="Not required unless other high-risk findings exist.",
                actions=[
                    "Check upstream for package-relative imports.",
                    "Use a corrected revision or reviewed minimal patch.",
                ],
            )
            diagnosis["evidence"].append(f"Matching local module exists: {module}")
            return diagnosis

        diagnosis.update(
            category="DEPENDENCY FAILURE",
            confidence="MEDIUM",
            explanation=(
                f"External Python module {module!r} is missing or installed in the "
                "wrong interpreter."
            ),
            reinstall="Maybe, only if dependency installation was interrupted.",
            git="Usually no.",
            sandbox="Optional.",
            actions=[
                "Inspect requirements and Manager dependency logs.",
                "Run pip check inside the persistent container environment.",
            ],
        )
        return diagnosis

    if "PermissionError" in text:
        permission_match = PERMISSION_PATH.search(text)
        path = permission_match.group(1) if permission_match else None
        outside = bool(
            path
            and not path.startswith(failure["container_path"].rstrip("/") + "/")
        )
        diagnosis.update(
            category="FILESYSTEM/PERMISSION INCOMPATIBILITY",
            confidence="HIGH",
            explanation=(
                "The pack tried to modify a path outside its own package during import."
                if outside
                else "The pack hit a filesystem permission error during import."
            ),
            reinstall="No; identical code repeats the write.",
            git="No; cloning does not change permissions.",
            sandbox="Recommended if the write behavior is not understood.",
            actions=[
                "Identify why the pack writes during import.",
                "Prefer a supported writable data/extension path; do not run the whole container as root.",
            ],
        )
        if path:
            diagnosis["evidence"].append(f"Attempted path: {path}")
        return diagnosis

    if failure["exception_type"] in {"ImportError", "AttributeError", "NameError"} and any(
        token in text for token in ("comfy.", "folder_paths", "PromptServer")
    ):
        diagnosis.update(
            category="COMFYUI API MISMATCH",
            confidence="MEDIUM",
            explanation=(
                "The pack expects a ComfyUI symbol/API absent from this core revision."
            ),
            reinstall="No.",
            git="Only if upstream supports this ComfyUI version.",
            sandbox="Optional.",
            actions=[
                "Check supported ComfyUI revisions.",
                "Pin a compatible pack or update ComfierUI as a tested set.",
            ],
        )
        return diagnosis

    if "SyntaxError" in text:
        diagnosis.update(
            category="PYTHON/PACKAGE SYNTAX INCOMPATIBILITY",
            confidence="HIGH",
            explanation="Python cannot parse the installed package source.",
            reinstall="No.",
            git="Maybe, if upstream fixed Python compatibility.",
            sandbox="Not normally required.",
            actions=["Inspect the exact source line and supported Python versions."],
        )
        return diagnosis

    if failure["exception_type"] == "OSError" and any(
        token in text for token in ("shared object", ".so", "undefined symbol")
    ):
        diagnosis.update(
            category="NATIVE LIBRARY FAILURE",
            confidence="HIGH",
            explanation="A native extension/shared library could not load.",
            reinstall="Usually no.",
            git="No.",
            sandbox="Recommended before changing the image.",
            actions=[
                "Identify the missing library/symbol.",
                "Add stable native requirements to the image, not the host.",
            ],
        )
        return diagnosis

    if "FileNotFoundError" in text or "No such file or directory" in text:
        diagnosis.update(
            category="MISSING RESOURCE OR INCOMPLETE INSTALLATION",
            confidence="MEDIUM",
            explanation="The pack expected a file/directory that is absent.",
            reinstall="Possibly, after identifying the creator of the resource.",
            git="Maybe if the Registry archive omitted files.",
            sandbox="Recommended for install hooks.",
            actions=[
                "Compare the published package with upstream and inspect install hooks."
            ],
        )
        if match := FILE_PATH.search(text):
            diagnosis["evidence"].append(f"Missing path: {match.group(1)}")
        return diagnosis

    dangerous = [
        item
        for item in scan["findings"]
        if item["severity"] in {"CRITICAL", "HIGH"}
    ]
    diagnosis.update(
        category="UNKNOWN",
        confidence="LOW",
        explanation=(
            "The failure was captured, but the initial classifier cannot identify "
            "one root cause."
        ),
        reinstall="Do not reinstall until the traceback is understood.",
        git="Unknown.",
        sandbox=(
            "Strongly recommended; high-risk behavior was detected."
            if dangerous
            else "Recommended; static evidence did not explain the failure."
        ),
        actions=[
            "Review the complete traceback and failure-relevant static findings.",
            "Escalate to focused/sandbox analysis before changing production.",
        ],
    )
    return diagnosis


def correlate_events(
    events: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    current_startup: int,
) -> list[dict[str, Any]]:
    failed_packs = {failure["pack"] for failure in failures}
    correlated: list[dict[str, Any]] = []
    for event in events:
        copied = dict(event)
        if event["pack"] in failed_packs and event["applies_to_startup"] == current_startup:
            copied["correlation"] = "current failed pack"
            copied["confidence"] = "HIGH"
        elif event["current"]:
            copied["correlation"] = "current startup"
            copied["confidence"] = "MEDIUM"
        elif event["pending_restart"]:
            copied["correlation"] = "pending next restart"
            copied["confidence"] = "MEDIUM"
        else:
            copied["correlation"] = "historical or unrelated"
            copied["confidence"] = "LOW"
        correlated.append(copied)
    return correlated


def empty_scan(pack: str, location: str | None) -> dict[str, Any]:
    return {
        "pack": pack,
        "host_path": location,
        "exists": False,
        "local_modules": [],
        "findings": [],
        "requirements": [],
    }


def build_report(namespace: argparse.Namespace) -> dict[str, Any]:
    environment: dict[str, Any] = {"mounts": [], "custom_nodes_host": None}
    container: str | None = None

    if not namespace.log_file:
        container = discover_container(namespace.container)
        environment = inspect_container(container)
    elif namespace.container:
        container = namespace.container
        environment = inspect_container(container)

    lines, source = read_logs(namespace, container)
    sessions = startup_sessions(lines)
    current = sessions[-1]

    if namespace.custom_nodes:
        custom_nodes = namespace.custom_nodes.resolve(strict=False)
    elif environment.get("custom_nodes_host"):
        custom_nodes = Path(environment["custom_nodes_host"])
    else:
        custom_nodes = None

    raw_failures = import_failures(current)
    reports: list[dict[str, Any]] = []
    for failure in raw_failures:
        location = map_container_path(
            failure["container_path"], environment, custom_nodes
        )
        scan = empty_scan(failure["pack"], location)
        if not namespace.no_static_scan:
            scan = scan_pack(failure["pack"], location)
        relevant, pack_wide = split_findings(failure, scan)
        scan["failure_relevant_findings"] = relevant
        scan["pack_wide_findings"] = pack_wide
        reports.append(
            {
                "failure": failure,
                "scan": scan,
                "diagnosis": diagnose(failure, scan),
            }
        )

    historical = [
        failure
        for session in sessions[:-1]
        for failure in import_failures(session)
    ]

    installed = (
        sum(
            1
            for path in custom_nodes.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        )
        if custom_nodes and custom_nodes.is_dir()
        else 0
    )
    successful = len(successful_imports(current))

    environment.update(
        {
            "versions": session_versions(current),
            "log_source": source,
            "log_lines": len(lines),
        }
    )

    if not reports:
        overall = "No current import failures found."
    elif successful:
        overall = "ComfyUI is running and other packs imported; failures appear isolated."
    else:
        overall = "Current import failures found; review each incident before changing the runtime."

    events = correlate_events(
        manager_events(lines, sessions),
        raw_failures,
        current["number"],
    )

    return {
        "environment": environment,
        "current_startup": {
            "number": current["number"],
            "started": current["started"],
            "sessions_retained": len(sessions),
        },
        "installed_pack_count": installed,
        "successful_import_count": successful,
        "current_failures": reports,
        "historical_failures": historical,
        "manager_events": events,
        "overall_assessment": overall,
    }


def print_finding(item: dict[str, Any]) -> None:
    print(
        f"  [{item['severity']}] {item['code']} "
        f"{item['file']}:{item['line'] or '-'}"
    )
    if item.get("evidence"):
        print(f"    {item['evidence']}")


def finding_summary(findings: Iterable[dict[str, Any]]) -> str:
    counts = Counter(item["code"] for item in findings)
    return ", ".join(
        f"{code}={count}" for code, count in sorted(counts.items())
    )


def print_human(report: dict[str, Any], namespace: argparse.Namespace) -> None:
    environment = report["environment"]
    print("=== ComfierUI Node Pack Doctor ===\n")
    print(
        f"Container:                  "
        f"{environment.get('container_name') or 'offline log mode'}"
    )
    print(f"Image:                      {environment.get('image') or 'unknown'}")
    print(
        f"State / health:             {environment.get('status') or 'unknown'} / "
        f"{environment.get('health') or 'unknown'}"
    )
    print(
        f"Runtime UID:GID:            {environment.get('runtime_uid')}:"
        f"{environment.get('runtime_gid')}"
    )
    print(
        f"Custom nodes host:          "
        f"{environment.get('custom_nodes_host') or 'unknown'}"
    )
    print(f"Log source:                 {environment.get('log_source')}")
    print(f"Log lines inspected:        {environment.get('log_lines')}")
    for key, value in environment.get("versions", {}).items():
        print(f"{key.title() + ':':<28}{value}")

    print("\nLatest startup")
    print(
        f"Started:                    "
        f"{report['current_startup']['started'] or 'unknown'}"
    )
    print(
        f"Startup sessions retained:  "
        f"{report['current_startup']['sessions_retained']}"
    )
    print(f"Installed pack directories: {report['installed_pack_count']}")
    print(f"Successful imports:         {report['successful_import_count']}")
    print(f"Current import failures:    {len(report['current_failures'])}")
    print(f"Historical failures:        {len(report['historical_failures'])}")
    print(f"\nAssessment\n  {report['overall_assessment']}")

    current_events = [
        event
        for event in report["manager_events"]
        if event["current"] or event["pending_restart"]
    ]
    if current_events:
        print("\nManager activity relevant to current or pending state")
        for event in current_events[-20:]:
            target = (
                f"startup {event['applies_to_startup']}"
                if event["applies_to_startup"]
                else "next restart"
            )
            print(
                f"  [{event['kind']}; {event['confidence']}; {event['correlation']}] "
                f"line {event['line']} -> {target}: {event['detail']}"
            )

    for number, item in enumerate(report["current_failures"], 1):
        failure = item["failure"]
        scan = item["scan"]
        diagnosis = item["diagnosis"]

        print("\n" + "-" * 72)
        print(f"FAILURE {number}: {failure['pack']}")
        print(
            f"Category / confidence:      {diagnosis['category']} / "
            f"{diagnosis['confidence']}"
        )
        print(f"Container path:             {failure['container_path']}")
        print(f"Host path:                  {scan['host_path'] or 'unknown'}")
        print(
            f"\nWhat failed\n  {failure['exception_type'] or 'Import failure'}: "
            f"{failure['exception_message'] or failure['summary']}"
        )
        print(f"\nWhat it probably means\n  {diagnosis['explanation']}")

        print("\nEvidence")
        for evidence in diagnosis["evidence"]:
            print(f"  - {evidence}")

        print(f"\nReinstall same version:    {diagnosis['reinstall']}")
        print(f"Manual Git install:         {diagnosis['git']}")
        print(f"Sandbox audit:              {diagnosis['sandbox']}")

        print("\nSuggested next steps")
        for action in diagnosis["actions"]:
            print(f"  - {action}")

        relevant = scan["failure_relevant_findings"]
        pack_wide = scan["pack_wide_findings"]
        if relevant:
            print(
                f"\nFailure-relevant static findings "
                f"({len(relevant)} of {len(scan['findings'])} total)"
            )
            for static_finding in relevant:
                print_finding(static_finding)

        if pack_wide:
            print(
                f"\nAdditional pack-wide capabilities/findings "
                f"({len(pack_wide)})"
            )
            if namespace.verbose:
                for static_finding in pack_wide:
                    print_finding(static_finding)
            else:
                print(f"  {finding_summary(pack_wide)}")
                print("  Use --verbose to show each pack-wide finding.")

        if namespace.verbose and failure["traceback"]:
            print("\nCaptured traceback")
            for line in failure["traceback"]:
                print("  " + line)

    if namespace.all_history and report["historical_failures"]:
        print("\nHistorical failures")
        for failure in report["historical_failures"]:
            print(
                f"  startup {failure['startup']} "
                f"({failure['started'] or 'unknown'}): "
                f"{failure['pack']}: {failure['summary']}"
            )

        historical_events = [
            event
            for event in report["manager_events"]
            if not event["current"] and not event["pending_restart"]
        ]
        if historical_events:
            print("\nHistorical Manager activity")
            for event in historical_events[-50:]:
                print(
                    f"  [{event['kind']}; {event['correlation']}] line "
                    f"{event['line']}: {event['detail']}"
                )


# Compatibility aliases retained for the initial regression suite and callers.
args = parse_args
root = repo_root
sessions = startup_sessions
exception = extract_exception
failures = import_failures
versions = session_versions
host_path = map_container_path
build = build_report
human = print_human


def main() -> int:
    namespace = parse_args()
    try:
        report = build_report(namespace)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if namespace.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report, namespace)
    return 1 if report["current_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
