# Node Pack Doctor

`node-pack-doctor.py` is a read-only production diagnostic for failed ComfyUI
custom-node installations and imports.

It is intended for the moment when Manager has downloaded a pack, ComfyUI has
restarted, and the interface only says **IMPORT FAILED**.

## Quick use

Run it from the ComfierUI repository root:

```bash
python3 scripts/node-pack-doctor.py
```

The default command:

1. discovers the active Compose `comfyui` container,
2. reads retained Docker logs,
3. isolates the latest ComfyUI startup,
4. finds every custom-node import failure in that startup,
5. separates current failures from older retained failures,
6. maps container package paths to the host `custom_nodes` bind mount,
7. statically inspects each failed pack without importing it,
8. classifies likely causes and suggests conservative next steps.

A nonzero exit status means current import failures were found:

- `0`: no current import failures found,
- `1`: one or more current import failures found,
- `2`: the doctor itself could not complete.

## Safety model

The production doctor does not:

- import or execute custom-node code,
- run `install.py` or `prestartup_script.py`,
- install Python packages,
- change ownership or permissions,
- modify package files,
- restart or recreate ComfyUI,
- contact GitHub or another upstream service.

It reads Docker metadata and logs, then reads package source files from the host
bind mount.

The static scan is a compatibility and risk indicator, not proof that a package
is safe or malicious. Dynamic imports, generated code, compiled extensions, and
behavior triggered only during node execution may require a later disposable
sandbox audit.

## Useful options

Show all static findings and captured tracebacks:

```bash
python3 scripts/node-pack-doctor.py --verbose
```

Emit JSON for automation or attaching to an issue:

```bash
python3 scripts/node-pack-doctor.py --json > node-pack-doctor.json
```

Include detailed historical failures retained from older startup sessions:

```bash
python3 scripts/node-pack-doctor.py --all-history
```

Inspect a saved log without touching Docker:

```bash
python3 scripts/node-pack-doctor.py \
  --log-file /path/to/comfyui.log \
  --custom-nodes /path/to/custom_nodes
```

Select a container explicitly when more than one ComfyUI stack is running:

```bash
python3 scripts/node-pack-doctor.py --container CONTAINER_NAME
```

## Initial classifications

The first implementation recognizes these broad failure families:

- package defect, such as a local module imported as though it were global,
- missing Python dependency,
- filesystem or permission incompatibility,
- ComfyUI API mismatch,
- Python/package syntax incompatibility,
- native shared-library failure,
- missing package resource or incomplete installation,
- unknown failures requiring deeper review.

The report deliberately distinguishes among:

- evidence that the overall environment is broken,
- evidence isolated to one node pack,
- a pack/container compatibility problem,
- a failure that is not yet understood.

It also records recent Manager extraction, security-policy, and recognizable
installation-failure messages from the retained logs.

## Static findings

For failed packs, the doctor currently looks for evidence such as:

- absolute sibling imports that should be package-relative,
- `sys.path` mutation,
- hardcoded ComfyUI or `custom_nodes` paths,
- filesystem changes,
- subprocess or shell execution,
- network access,
- dynamic `eval` or `exec`,
- package-install, download, or privilege commands,
- Docker socket or SSH-material references.

Findings are evidence for review. Some are normal for a particular extension and
some are merely sloppy. Severity indicates how carefully the behavior should be
reviewed, not a verdict about intent.

## Testing

Run the parser and classifier regression tests from the repository root:

```bash
python3 -m unittest discover -s tests -v
```

The fixtures are synthetic and pack-agnostic. They verify that the doctor finds
all current failures, does not confuse historical failures with the latest
startup, recognizes a broken local sibling import, recognizes a permission
failure, and leaves unexplained failures classified as unknown.

## Planned escalation path

The production doctor is the first layer. A later sandbox mode will use a
disposable container with no production mounts to observe installation and
import behavior. The production report should recommend that deeper audit when
static evidence is incomplete or a pack performs invasive operations.
