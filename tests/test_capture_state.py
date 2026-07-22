from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CAPTURE_SCRIPT = REPOSITORY_ROOT / "docker" / "capture-state.py"


def git(path: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def initialize_git(path: Path, filename: str = "node.py") -> None:
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    git(path, "config", "user.name", "ComfierUI Test")
    git(path, "config", "user.email", "test@example.invalid")
    (path / filename).write_text("VALUE = 1\n", encoding="utf-8")
    git(path, "add", filename)
    git(path, "commit", "-qm", "initial")


class CaptureStateTests(unittest.TestCase):
    def run_capture(self, root: Path) -> dict:
        output = root / "backups" / "state" / "current.json"
        runtime_state = root / "data" / "user" / ".comfierui" / "runtime-state.json"
        runtime_state.parent.mkdir(parents=True, exist_ok=True)
        runtime_state.write_text('{"comfyui": {"commit": "abc123"}}\n', encoding="utf-8")
        (root / "compose.yaml").write_text("services: {}\n", encoding="utf-8")

        subprocess.run(
            [
                "python3",
                str(CAPTURE_SCRIPT),
                "--output",
                str(output),
                "--custom-nodes",
                str(root / "data" / "custom_nodes"),
                "--runtime-state",
                str(runtime_state),
                "--repository",
                str(root),
                "--compose-files",
                "compose.yaml",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        return json.loads(output.read_text(encoding="utf-8"))

    def test_captures_git_registry_manual_and_disabled_packs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialize_git(root, "README.md")
            custom_nodes = root / "data" / "custom_nodes"

            git_pack = custom_nodes / "git-pack"
            initialize_git(git_pack)
            git(git_pack, "remote", "add", "origin", "https://user:secret@example.com/owner/pack.git")
            (git_pack / ".git" / ".cnr-id").write_text("git-pack-id\n", encoding="utf-8")
            (git_pack / "node.py").write_text("VALUE = 2\n", encoding="utf-8")

            registry_pack = custom_nodes / "registry-pack"
            registry_pack.mkdir()
            (registry_pack / "pyproject.toml").write_text(
                """
[project]
name = "registry-pack-id"
version = "2.5.1"

[project.urls]
Repository = "https://example.com/owner/registry-pack"
""".strip()
                + "\n",
                encoding="utf-8",
            )
            (registry_pack / ".tracking").write_text("pyproject.toml\n", encoding="utf-8")

            manual_pack = custom_nodes / "manual-pack"
            manual_pack.mkdir()
            (manual_pack / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")

            disabled_pack = custom_nodes / ".disabled" / "disabled-pack"
            disabled_pack.mkdir(parents=True)
            (disabled_pack / "__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n", encoding="utf-8")

            state = self.run_capture(root)
            packs = {item["directory"]: item for item in state["node_packs"]}

            self.assertEqual(1, state["schema_version"])
            self.assertEqual(4, state["node_pack_count"])
            self.assertEqual("abc123", state["runtime"]["comfyui"]["commit"])

            git_state = packs["git-pack"]
            self.assertEqual("git", git_state["install_type"])
            self.assertTrue(git_state["git"]["dirty"])
            self.assertEqual("nightly", git_state["manager"]["update_channel"])
            self.assertEqual("git-pack-id", git_state["manager"]["id"])
            self.assertEqual(
                "https://example.com/owner/pack.git",
                git_state["git"]["remote"],
            )
            self.assertTrue(git_state["content_hash"].startswith("sha256:"))

            registry_state = packs["registry-pack"]
            self.assertEqual("registry", registry_state["install_type"])
            self.assertEqual("registry-pack-id", registry_state["manager"]["id"])
            self.assertEqual("2.5.1", registry_state["manager"]["installed_version"])
            self.assertIsNone(registry_state["manager"]["selected_version"])
            self.assertFalse(registry_state["manager"]["selection_known"])

            self.assertEqual("manual-or-archive", packs["manual-pack"]["install_type"])
            self.assertFalse(packs[".disabled/disabled-pack"]["enabled"])
            self.assertTrue(any("redacted" in warning for warning in state["warnings"]))

    def test_replaces_manifest_atomically_and_changes_hash_with_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            initialize_git(root, "README.md")
            pack = root / "data" / "custom_nodes" / "manual-pack"
            pack.mkdir(parents=True)
            source = pack / "node.py"
            source.write_text("VALUE = 1\n", encoding="utf-8")

            first = self.run_capture(root)
            first_hash = first["node_packs"][0]["content_hash"]
            source.write_text("VALUE = 2\n", encoding="utf-8")
            second = self.run_capture(root)
            second_hash = second["node_packs"][0]["content_hash"]

            self.assertNotEqual(first["capture_id"], second["capture_id"])
            self.assertNotEqual(first_hash, second_hash)
            state_dir = root / "backups" / "state"
            self.assertEqual(["current.json"], sorted(path.name for path in state_dir.iterdir()))


if __name__ == "__main__":
    unittest.main()
