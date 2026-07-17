# Migrating an existing ComfyUI installation

## Principle

Build a clean deployment first. Do not copy an old venv, core checkout, Manager
checkout, cache tree, or complete custom-node directory into the new system.

Recommended order:

1. Start with new local data and run one core workflow.
2. Point the model mount at the existing library.
3. Point at or selectively copy workflows.
4. Move only needed inputs and selected outputs.
5. Load one workflow at a time and install its required node packs.
6. Migrate compatible user settings.
7. Prove backup and rollback before retiring the old installation.

## Inventory before changing anything

Record resolved paths, symlinks, ownership, size, and file counts. Work from the
real target of a symlink, not its displayed path.

```bash
readlink -f /path/to/old/models
stat -c '%U:%G %a %n' /path/to/old/models
find /path/to/old/models -type f | wc -l
du -sh /path/to/old/models
```

## Models

Reuse a large library with a bind mount:

```dotenv
COMFYUI_MODELS_PATH=/absolute/path/to/old/models
COMFYUI_SHARED_GID=<numeric group owning the library>
```

Run `scripts/check-permissions.sh`. Preserve owner/group policy unless a change
is intentional.

When model categories span several independent roots, use the extra-model-path
examples rather than creating undocumented symlinks inside the container.

## Workflows

Workflow JSON can contain prompts, filenames, API keys, access tokens, and node
configuration. Audit before committing or sharing:

```bash
python scripts/audit-workflows.py \
  --url http://127.0.0.1:8188/object_info \
  /path/to/workflows
```

The audit separates active missing nodes from missing nodes used only by muted or
bypassed branches. It does not guarantee that current node input schemas still
match an old workflow.

## Custom nodes

Use Manager's missing-node view. Install small batches and restart between them.
Copying the old tree imports dependencies for abandoned workflows and can retain
conflicting package pins.

```bash
docker compose restart comfyui
docker compose logs --since=5m comfyui
```

## Inputs, outputs, and user state

Copy only the material worth preserving. Old outputs can be archived outside the
live output directory. User settings and Manager snapshots can be useful, but a
clean current configuration is often safer than restoring every cache file.

## Rollback

Keep the old service disabled but intact until the new deployment proves:

- model visibility and writes where intended,
- core and representative custom-node workflows,
- auxiliary model downloads,
- output saving,
- restart and rebuild persistence,
- staged backup restoration.
