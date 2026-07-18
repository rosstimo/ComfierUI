# Recovery blueprint

ComfierUI backups separate **unique recovery data** from **reproducible state**.
The goal is to recover a known working installation without copying hundreds of
gigabytes that can be rebuilt or downloaded again.

## Recovery payload

The default encrypted backup stores the small state that is difficult or annoying
to recreate:

- local deployment configuration,
- custom-node code,
- ComfyUI and Manager user state,
- workflows.

Models, input/output images, and the Python volume remain opt-in.

## Recovery blueprint

Every built-in snapshot also stores a small recovery blueprint. It records the
known state at backup time even for categories whose contents were intentionally
excluded from the backup payload.

The blueprint currently contains:

- ComfierUI repository commit when detectable,
- ComfyUI and PyTorch-family version pins,
- active Compose layers,
- Manager behavior settings,
- backup include/exclude coverage,
- custom-node directory names and Git commits when detectable,
- installed Python package names and versions found in the persistent venv,
- writable model-library filenames and sizes,
- extra/legacy model-library filenames and sizes when configured,
- input filenames and sizes,
- output filenames and sizes.

The blueprint deliberately avoids model hashing by default. Hashing a large model
library would require reading every byte and could make a lightweight backup act
like a full-disk verification job.

The inventory also avoids recording custom-node remote URLs. Git remotes can
occasionally contain embedded credentials; the backed-up custom-node code and
commit inventory are enough for the default recovery roadmap.

## Portable state versus host-local state

A recovery should reproduce the application state without assuming the new host
is identical to the old one.

Portable recovery state includes things such as:

- repository commit,
- ComfyUI version,
- PyTorch, TorchVision, and TorchAudio versions,
- Manager settings,
- custom-node state,
- workflows and user state.

Host-local state should normally be detected or configured on the recovery host:

- GPU UUID and accelerator selection,
- UID/GID and supplementary group IDs,
- bind address and port,
- absolute host paths,
- external Docker network names,
- backup destination policy.

This distinction is important when restoring a backup onto a freshly cloned
ComfierUI repository on another machine.

## Intended fresh-clone recovery flow

The target workflow is:

1. Clone ComfierUI.
2. Make the encrypted restic repository and its password available to the clone.
3. Run normal initialization so the new host detects its own hardware, identity,
   paths, and networking.
4. List the available snapshots and select a known-good recovery point.
5. Read the snapshot recovery blueprint.
6. Reconstruct the portable ComfierUI version/configuration state while preserving
   the new host's local settings.
7. Restore custom nodes, user/Manager state, and workflows.
8. Rebuild the pinned ComfierUI/PyTorch base environment.
9. Use the Python-package and model inventories to reconcile anything deliberately
   excluded from the backup payload.
10. Start ComfierUI and verify a representative workflow.

The recovery blueprint is a roadmap. An inventory entry for an excluded model or
input proves that the file existed at backup time, not that the file can be
recovered from restic. Important irreplaceable assets should still be included in
backup or protected separately.

## Why this model

A full copy of every model and Python package is often wasteful. ComfierUI already
pins the reproducible core environment, Git preserves the deployment source, and
restic efficiently protects the unique state. The recovery blueprint connects
those pieces so a small backup can still describe a much larger known-good
installation.
