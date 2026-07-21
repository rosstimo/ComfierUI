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
from pathlib import Path
from typing import Any

STARTUP = re.compile(r"\*\* ComfyUI startup time:\s*(.+)$")
CANNOT_IMPORT = re.compile(r"Cannot import\s+(.+?)\s+module for custom nodes:\s*(.*)$")
IMPORT_FAILED = re.compile(r"\(IMPORT FAILED\):\s*(\S.*?)\s*$")
EXCEPTION = re.compile(r"^([A-Za-z_][\w.]*(?:Error|Exception)):\s*(.*)$")
MISSING_MODULE = re.compile(r"No module named ['\"]([^'\"]+)['\"]")
PERMISSION_PATH = re.compile(r"Permission denied:\s*['\"]([^'\"]+)['\"]")
FILE_PATH = re.compile(r"No such file or directory:\s*['\"]([^'\"]+)['\"]")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
VERSIONS = {
    "comfyui": re.compile(r"ComfyUI version:\s*(\S+)"),
    "manager": re.compile(r"ComfyUI-Manager \(V([^\)]+)\)"),
    "python": re.compile(r"Python version:\s*([^\[]+?)(?:\s*\[|$)"),
}


def args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Explain current ComfyUI custom-node import failures without modifying production."
    )
    p.add_argument("--container", help="container ID/name; default: auto-discover")
    p.add_argument("--custom-nodes", type=Path, help="host custom_nodes path")
    p.add_argument("--log-file", type=Path, help="offline log instead of docker logs")
    p.add_argument("--log-lines", type=int, default=30000)
    p.add_argument("--all-history", action="store_true")
    p.add_argument("--no-static-scan", action="store_true")
    p.add_argument("--verbose", action="store_true")
    p.add_argument("--json", action="store_true")
    return p.parse_args()


def run(command: list[str], *, cwd: Path | None = None, check: bool = True,
        merge: bool = False, timeout: int = 60) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command, cwd=cwd, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if merge else subprocess.PIPE,
            check=check, timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(f"required command not found: {command[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stdout or exc.stderr or "").strip()
        raise RuntimeError(f"command failed: {shlex.join(command)}: {detail}") from exc


def root() -> Path:
    return Path(__file__).resolve().parent.parent


def discover_container(explicit: str | None) -> str:
    if explicit:
        return explicit
    result = run(["docker", "compose", "ps", "-q", "comfyui"], cwd=root(), check=False)
    found = [x for x in result.stdout.splitlines() if x.strip()]
    if not found:
        result = run([
            "docker", "ps", "--filter", "label=com.docker.compose.service=comfyui",
            "--format", "{{.ID}}",
        ], check=False)
        found = [x for x in result.stdout.splitlines() if x.strip()]
    if len(found) != 1:
        raise RuntimeError("could not uniquely discover ComfyUI container; use --container")
    return found[0].strip()


def inspect_container(container: str) -> dict[str, Any]:
    payload = json.loads(run(["docker", "inspect", container]).stdout)[0]
    state, config = payload.get("State", {}), payload.get("Config", {})
    mounts = []
    custom_host = None
    for item in payload.get("Mounts", []):
        mount = {
            "source": item.get("Source"), "destination": item.get("Destination"),
            "rw": item.get("RW"), "type": item.get("Type"),
        }
        mounts.append(mount)
        if mount["destination"] == "/opt/ComfyUI/custom_nodes":
            custom_host = mount["source"]
    uid = run(["docker", "exec", container, "id", "-u"], check=False).stdout.strip()
    gid = run(["docker", "exec", container, "id", "-g"], check=False).stdout.strip()
    return {
        "container_id": payload.get("Id"),
        "container_name": str(payload.get("Name", "")).lstrip("/"),
        "image": config.get("Image"), "status": state.get("Status"),
        "health": state.get("Health", {}).get("Status"),
        "runtime_uid": int(uid) if uid.isdigit() else None,
        "runtime_gid": int(gid) if gid.isdigit() else None,
        "custom_nodes_host": custom_host, "mounts": mounts,
    }


def read_logs(ns: argparse.Namespace, container: str | None) -> tuple[list[str], str]:
    if ns.log_file:
        text = ns.log_file.read_text(encoding="utf-8", errors="replace")
        source = str(ns.log_file)
    else:
        result = run(["docker", "logs", "--tail", str(ns.log_lines), container or ""],
                     check=False, merge=True)
        text, source = result.stdout, f"docker logs --tail {ns.log_lines} {container}"
    return [ANSI.sub("", line.rstrip()) for line in text.splitlines()], source


def sessions(lines: list[str]) -> list[dict[str, Any]]:
    marks = [(i, m.group(1).strip()) for i, line in enumerate(lines)
             if (m := STARTUP.search(line))]
    if not marks:
        return [{"number": 1, "started": None, "lines": lines}]
    out = []
    for n, (start, when) in enumerate(marks, 1):
        end = marks[n][0] if n < len(marks) else len(lines)
        out.append({"number": n, "started": when, "lines": lines[start:end]})
    return out


def exception(trace: list[str], summary: str) -> tuple[str | None, str]:
    for raw in reversed(trace):
        line = raw.strip().removeprefix("[WARNING] ").removeprefix("[ERROR] ")
        if match := EXCEPTION.match(line):
            return match.group(1), match.group(2)
    return None, summary


def failures(session: dict[str, Any]) -> list[dict[str, Any]]:
    lines, out, seen = session["lines"], [], set()
    for i, line in enumerate(lines):
        match = CANNOT_IMPORT.search(line)
        if not match:
            continue
        path, summary = match.group(1).strip(), match.group(2).strip()
        start = None
        for j in range(i - 1, max(-1, i - 250), -1):
            if "Traceback (most recent call last):" in lines[j]:
                start = j
                break
            if CANNOT_IMPORT.search(lines[j]):
                break
        trace = lines[start:i] if start is not None else []
        kind, message = exception(trace, summary)
        out.append({
            "pack": Path(path.rstrip("/")).name, "container_path": path,
            "summary": summary, "traceback": trace, "exception_type": kind,
            "exception_message": message, "startup": session["number"],
            "started": session["started"],
        })
        seen.add(path)
    for line in lines:
        if (match := IMPORT_FAILED.search(line)) and match.group(1).strip() not in seen:
            path = match.group(1).strip()
            out.append({
                "pack": Path(path.rstrip("/")).name, "container_path": path,
                "summary": "IMPORT FAILED without captured traceback", "traceback": [],
                "exception_type": None, "exception_message": None,
                "startup": session["number"], "started": session["started"],
            })
    return out


def versions(session: dict[str, Any]) -> dict[str, str]:
    found = {}
    for line in session["lines"]:
        for name, pattern in VERSIONS.items():
            if match := pattern.search(line):
                found[name] = match.group(1).strip()
    return found


def successful_imports(session: dict[str, Any]) -> set[str]:
    found = set()
    for line in session["lines"]:
        if "IMPORT FAILED" in line or "/custom_nodes/" not in line:
            continue
        if "seconds:" in line:
            found.add(Path(line.rsplit(":", 1)[-1].strip()).name)
    return found


def manager_events(lines: list[str]) -> list[dict[str, Any]]:
    events = []
    for number, line in enumerate(lines, 1):
        lower = line.casefold()
        if "extracted zip file to" in lower:
            path = line.split("to", 1)[-1].strip().strip("'\"")
            events.append({"kind": "package_extracted", "pack": Path(path).name,
                           "detail": path, "line": number})
        elif "security_level" in lower and "error" in lower:
            events.append({"kind": "manager_security_block", "pack": None,
                           "detail": line.strip(), "line": number})
        elif (("failed" in lower and any(x in lower for x in
               ("install", "pip", "dependency", "extract", "download"))) or
              "no matching distribution found" in lower or
              "could not find a version that satisfies" in lower or
              "badzipfile" in lower):
            events.append({"kind": "manager_failure", "pack": None,
                           "detail": line.strip(), "line": number})
    return events[-100:]


def host_path(container_path: str, env: dict[str, Any], explicit: Path | None) -> str | None:
    for mount in sorted(env.get("mounts", []), key=lambda x: len(x.get("destination") or ""), reverse=True):
        dst = str(mount.get("destination") or "").rstrip("/")
        if container_path == dst or container_path.startswith(dst + "/"):
            suffix = container_path[len(dst):].lstrip("/")
            return str(Path(mount["source"]) / suffix)
    return str(explicit / Path(container_path).name) if explicit else None


def local_modules(pack: Path) -> set[str]:
    found = set()
    for item in pack.iterdir():
        if item.is_file() and item.suffix == ".py":
            found.add(item.stem)
        elif item.is_dir() and (item / "__init__.py").is_file():
            found.add(item.name)
    return found


def call_name(node: ast.Call) -> str:
    target, parts = node.func, []
    while isinstance(target, ast.Attribute):
        parts.append(target.attr)
        target = target.value
    if isinstance(target, ast.Name):
        parts.append(target.id)
    return ".".join(reversed(parts))


def scan_pack(name: str, location: str | None) -> dict[str, Any]:
    result = {"pack": name, "host_path": location, "exists": False,
              "local_modules": [], "findings": [], "requirements": []}
    if not location or not (pack := Path(location)).is_dir():
        return result
    result["exists"] = True
    modules = local_modules(pack)
    result["local_modules"] = sorted(modules)
    req = pack / "requirements.txt"
    if req.is_file():
        result["requirements"] = [x.strip() for x in req.read_text(errors="replace").splitlines()
                                  if x.strip() and not x.lstrip().startswith("#")]
    for path in list(pack.rglob("*.py"))[:500]:
        if any(part in {".git", "__pycache__", ".venv", "venv"} for part in path.relative_to(pack).parts):
            continue
        rel = str(path.relative_to(pack))
        try:
            text = path.read_text(encoding="utf-8", errors="replace")[:2_000_000]
            tree = ast.parse(text, filename=str(path))
        except (OSError, SyntaxError) as exc:
            result["findings"].append({"severity": "HIGH", "code": "SOURCE_PARSE_FAILURE",
                "file": rel, "line": getattr(exc, "lineno", None), "evidence": str(exc)})
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.split(".", 1)[0] in modules:
                    result["findings"].append({"severity": "MEDIUM",
                        "code": "ABSOLUTE_SIBLING_IMPORT", "file": rel, "line": node.lineno,
                        "evidence": ast.get_source_segment(text, node)})
            if not isinstance(node, ast.Call):
                continue
            called = call_name(node)
            codes = {
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
            if called in codes:
                severity, code = codes[called]
                result["findings"].append({"severity": severity, "code": code,
                    "file": rel, "line": node.lineno,
                    "evidence": ast.get_source_segment(text, node)})
        for number, line in enumerate(text.splitlines(), 1):
            lower = line.casefold()
            if "/opt/comfyui" in lower or "custom_nodes/" in lower:
                result["findings"].append({"severity": "LOW", "code": "HARDCODED_COMFYUI_PATH",
                    "file": rel, "line": number, "evidence": line.strip()})
            if any(x in lower for x in ("pip install", "git clone", "curl ", "wget ", "sudo ")):
                result["findings"].append({"severity": "HIGH", "code": "INSTALL_OR_SHELL_COMMAND",
                    "file": rel, "line": number, "evidence": line.strip()})
            if any(x in lower for x in ("/var/run/docker.sock", ".ssh/", "id_rsa")):
                result["findings"].append({"severity": "CRITICAL", "code": "SENSITIVE_HOST_ACCESS",
                    "file": rel, "line": number, "evidence": line.strip()})
    unique = {(x["code"], x["file"], x["line"], x.get("evidence")): x
              for x in result["findings"]}
    rank = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    result["findings"] = sorted(unique.values(), key=lambda x: (rank[x["severity"]], x["file"], x["line"] or 0))
    return result


def diagnose(failure: dict[str, Any], scan: dict[str, Any]) -> dict[str, Any]:
    text = "\n".join(failure["traceback"] + [failure["summary"]])
    base = {"evidence": [f"{failure['exception_type'] or 'Import failure'}: {failure['exception_message'] or failure['summary']}"]}
    if match := MISSING_MODULE.search(text):
        module = match.group(1).split(".", 1)[0]
        if module in scan["local_modules"]:
            base.update(category="PACKAGE DEFECT", confidence="HIGH",
                explanation="The pack cannot import one of its own files; it likely uses a fragile sibling import or directory-name assumption.",
                reinstall="No; identical source will fail again.", git="Maybe, only if newer upstream source contains a fix.",
                sandbox="Not required unless other high-risk findings exist.",
                actions=["Check upstream for package-relative imports.", "Use a corrected revision or reviewed minimal patch."])
            base["evidence"].append(f"Matching local module exists: {module}")
            return base
        base.update(category="DEPENDENCY FAILURE", confidence="MEDIUM",
            explanation=f"External Python module {module!r} is missing or installed in the wrong interpreter.",
            reinstall="Maybe, only if dependency installation was interrupted.", git="Usually no.",
            sandbox="Optional.", actions=["Inspect requirements and Manager dependency logs.", "Run pip check inside the persistent container environment."])
        return base
    if "PermissionError" in text:
        path = (PERMISSION_PATH.search(text).group(1) if PERMISSION_PATH.search(text) else None)
        outside = bool(path and not path.startswith(failure["container_path"].rstrip("/") + "/"))
        base.update(category="FILESYSTEM/PERMISSION INCOMPATIBILITY", confidence="HIGH",
            explanation=("The pack tried to modify a path outside its own package during import." if outside else "The pack hit a filesystem permission error during import."),
            reinstall="No; identical code repeats the write.", git="No; cloning does not change permissions.",
            sandbox="Recommended if the write behavior is not understood.",
            actions=["Identify why the pack writes during import.", "Prefer a supported writable data/extension path; do not run the whole container as root."])
        if path: base["evidence"].append(f"Attempted path: {path}")
        return base
    if failure["exception_type"] in {"ImportError", "AttributeError", "NameError"} and any(x in text for x in ("comfy.", "folder_paths", "PromptServer")):
        base.update(category="COMFYUI API MISMATCH", confidence="MEDIUM",
            explanation="The pack expects a ComfyUI symbol/API absent from this core revision.",
            reinstall="No.", git="Only if upstream supports this ComfyUI version.", sandbox="Optional.",
            actions=["Check supported ComfyUI revisions.", "Pin a compatible pack or update ComfierUI as a tested set."])
        return base
    if "SyntaxError" in text:
        base.update(category="PYTHON/PACKAGE SYNTAX INCOMPATIBILITY", confidence="HIGH",
            explanation="Python cannot parse the installed package source.", reinstall="No.",
            git="Maybe, if upstream fixed Python compatibility.", sandbox="Not normally required.",
            actions=["Inspect the exact source line and supported Python versions."])
        return base
    if failure["exception_type"] == "OSError" and any(x in text for x in ("shared object", ".so", "undefined symbol")):
        base.update(category="NATIVE LIBRARY FAILURE", confidence="HIGH",
            explanation="A native extension/shared library could not load.", reinstall="Usually no.",
            git="No.", sandbox="Recommended before changing the image.",
            actions=["Identify the missing library/symbol.", "Add stable native requirements to the image, not the host."])
        return base
    if "FileNotFoundError" in text or "No such file or directory" in text:
        base.update(category="MISSING RESOURCE OR INCOMPLETE INSTALLATION", confidence="MEDIUM",
            explanation="The pack expected a file/directory that is absent.", reinstall="Possibly, after identifying the creator of the resource.",
            git="Maybe if the Registry archive omitted files.", sandbox="Recommended for install hooks.",
            actions=["Compare the published package with upstream and inspect install hooks."])
        if match := FILE_PATH.search(text): base["evidence"].append(f"Missing path: {match.group(1)}")
        return base
    dangerous = [x for x in scan["findings"] if x["severity"] in {"CRITICAL", "HIGH"}]
    base.update(category="UNKNOWN", confidence="LOW",
        explanation="The failure was captured, but the initial classifier cannot identify one root cause.",
        reinstall="Do not reinstall until the traceback is understood.", git="Unknown.",
        sandbox=("Strongly recommended; high-risk behavior was detected." if dangerous else "Recommended; static evidence did not explain the failure."),
        actions=["Review the complete traceback and static findings.", "Escalate to focused/sandbox analysis before changing production."])
    return base


def build(ns: argparse.Namespace) -> dict[str, Any]:
    env: dict[str, Any] = {"mounts": [], "custom_nodes_host": None}
    container = None
    if not ns.log_file:
        container = discover_container(ns.container)
        env = inspect_container(container)
    elif ns.container:
        env = inspect_container(ns.container)
        container = ns.container
    lines, source = read_logs(ns, container)
    starts = sessions(lines)
    current = starts[-1]
    custom = ns.custom_nodes.resolve() if ns.custom_nodes else (Path(env["custom_nodes_host"]) if env.get("custom_nodes_host") else None)
    reports = []
    for failure in failures(current):
        location = host_path(failure["container_path"], env, custom)
        scan = {"pack": failure["pack"], "host_path": location, "exists": False,
                "local_modules": [], "findings": [], "requirements": []}
        if not ns.no_static_scan:
            scan = scan_pack(failure["pack"], location)
        reports.append({"failure": failure, "scan": scan, "diagnosis": diagnose(failure, scan)})
    historical = [f for start in starts[:-1] for f in failures(start)]
    installed = sum(1 for p in custom.iterdir() if p.is_dir() and not p.name.startswith(".")) if custom and custom.is_dir() else 0
    success = len(successful_imports(current))
    env.update({"versions": versions(current), "log_source": source, "log_lines": len(lines)})
    overall = ("No current import failures found." if not reports else
        "ComfyUI is running and other packs imported; failures appear isolated." if success else
        "Current import failures found; review each incident before changing the runtime.")
    return {"environment": env, "current_startup": {"number": current["number"], "started": current["started"], "sessions_retained": len(starts)},
            "installed_pack_count": installed, "successful_import_count": success,
            "current_failures": reports, "historical_failures": historical,
            "manager_events": manager_events(lines), "overall_assessment": overall}


def human(report: dict[str, Any], ns: argparse.Namespace) -> None:
    env = report["environment"]
    print("=== ComfierUI Node Pack Doctor ===\n")
    print(f"Container:                  {env.get('container_name') or 'offline log mode'}")
    print(f"Image:                      {env.get('image') or 'unknown'}")
    print(f"State / health:             {env.get('status') or 'unknown'} / {env.get('health') or 'unknown'}")
    print(f"Runtime UID:GID:            {env.get('runtime_uid')}:{env.get('runtime_gid')}")
    print(f"Custom nodes host:          {env.get('custom_nodes_host') or 'unknown'}")
    print(f"Log source:                 {env.get('log_source')}")
    print(f"Log lines inspected:        {env.get('log_lines')}")
    for key, value in env.get("versions", {}).items(): print(f"{key.title() + ':':<28}{value}")
    print("\nLatest startup")
    print(f"Started:                    {report['current_startup']['started'] or 'unknown'}")
    print(f"Startup sessions retained:  {report['current_startup']['sessions_retained']}")
    print(f"Installed pack directories: {report['installed_pack_count']}")
    print(f"Successful imports:         {report['successful_import_count']}")
    print(f"Current import failures:    {len(report['current_failures'])}")
    print(f"Historical failures:        {len(report['historical_failures'])}")
    print(f"\nAssessment\n  {report['overall_assessment']}")
    notable = [x for x in report["manager_events"] if x["kind"] != "package_extracted" or x["pack"] in {r["failure"]["pack"] for r in report["current_failures"]}]
    if notable:
        print("\nRecent Manager activity")
        for event in notable[-20:]: print(f"  [{event['kind']}] line {event['line']}: {event['detail']}")
    for n, item in enumerate(report["current_failures"], 1):
        f, s, d = item["failure"], item["scan"], item["diagnosis"]
        print("\n" + "-" * 72)
        print(f"FAILURE {n}: {f['pack']}")
        print(f"Category / confidence:      {d['category']} / {d['confidence']}")
        print(f"Container path:             {f['container_path']}")
        print(f"Host path:                  {s['host_path'] or 'unknown'}")
        print(f"\nWhat failed\n  {f['exception_type'] or 'Import failure'}: {f['exception_message'] or f['summary']}")
        print(f"\nWhat it probably means\n  {d['explanation']}")
        print("\nEvidence")
        for evidence in d["evidence"]: print(f"  - {evidence}")
        print(f"\nReinstall same version:    {d['reinstall']}")
        print(f"Manual Git install:         {d['git']}")
        print(f"Sandbox audit:              {d['sandbox']}")
        print("\nSuggested next steps")
        for action in d["actions"]: print(f"  - {action}")
        findings = s["findings"] if ns.verbose else [x for x in s["findings"] if x["severity"] != "LOW"][:10]
        if findings:
            print(f"\nStatic findings ({len(s['findings'])} total)")
            for finding in findings:
                print(f"  [{finding['severity']}] {finding['code']} {finding['file']}:{finding['line'] or '-'}")
                if finding.get("evidence"): print(f"    {finding['evidence']}")
        if ns.verbose and f["traceback"]:
            print("\nCaptured traceback")
            for line in f["traceback"]: print("  " + line)
    if ns.all_history and report["historical_failures"]:
        print("\nHistorical failures")
        for f in report["historical_failures"]:
            print(f"  startup {f['startup']} ({f['started'] or 'unknown'}): {f['pack']}: {f['summary']}")


def main() -> int:
    ns = args()
    try:
        report = build(ns)
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True) if ns.json else "", end="" if ns.json else "")
    if not ns.json: human(report, ns)
    return 1 if report["current_failures"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
