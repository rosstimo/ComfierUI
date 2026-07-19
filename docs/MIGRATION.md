# Migrating an existing ComfyUI installation

## Principle

Build a clean deployment first. Do not copy an old venv, core checkout, Manager
checkout, cache tree, or complete custom-node directory into the new system.

Recommended order:

1. Start with new local data and run one core workflow.
2. Add the existing model library as a read-only extra model source.
3. Verify both an old model and a newly Manager-installed model.
4. Point at or selectively copy workflows.
5. Move only needed inputs and selected outputs.
6. Load one workflow at a time and install its required node packs.
7. Migrate compatible user settings.
8. Prove backup and rollback before retiring the old installation.

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

Keep the new deployment's normal writable model directory:

```dotenv
COMFYUI_MODELS_PATH=./data/models
```

This is where Manager installs new models. Do not replace it with the old model
library unless you deliberately want all new downloads mixed into that library.

Instead, expose the old library separately:

```dotenv
COMFYUI_EXTRA_MODELS_PATH=/absolute/path/to/old/models
COMFYUI_SHARED_GID=<numeric group that can read the library>
```

Then rerun initialization and preflight:

```bash
bash scripts/init.sh
bash scripts/preflight.sh
```

Initialization enables `compose.extra-models.yaml`, pre-creates the nested mount
point, mounts the old library read-only at
`/opt/ComfyUI/models/external`, and passes
`config/extra_model_paths.yaml` to ComfyUI.

The result is intentionally split:

- old/shared models remain in the existing library and are read-only to the
  container,
- new Manager downloads go to `COMFYUI_MODELS_PATH`, normally `./data/models`,
- both sets appear in ComfyUI's model selectors for configured standard model
  categories.

Verify the migration with both directions before moving on:

1. Generate an image with an old checkpoint from the extra library.
2. Install a new model through Manager and generate with it.
3. Confirm the new file appeared under `COMFYUI_MODELS_PATH`.
4. Confirm the old library was not modified.

Custom-node-specific directories such as face-restoration, InsightFace, LLM, or
other node-pack-owned model trees are not guessed automatically. Configure those
only after installing the node pack that uses them and checking its documented
paths.

When model categories span several independent roots, use the customizable
`config/extra_model_paths.yaml.example` and
`examples/compose.extra-model-paths.yaml` examples rather than creating
undocumented container symlinks.

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

- old and new model visibility,
- new model downloads write only where intended,
- core and representative custom-node workflows,
- auxiliary model downloads,
- output saving,
- restart and rebuild persistence,
- staged backup restoration.
