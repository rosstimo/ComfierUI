# Security

## Trust model

Custom nodes execute Python code. They can read and modify every mounted model,
workflow, input, output, user, custom-node, cache, and home path. A container
boundary reduces host exposure but does not make an untrusted extension safe.

## Defaults

- localhost-only published port,
- non-root host-matched UID/GID,
- one explicitly selected GPU,
- no privileged mode,
- no Docker socket mount,
- no undeclared host paths,
- optional external networking rather than a required shared network.

## Network exposure

Before changing the bind address to `0.0.0.0`:

- configure a host firewall,
- decide whether LAN users are trusted,
- put authentication at a reverse proxy, VPN, or tunnel boundary,
- keep Manager away from unauthenticated public access.

## Secrets and metadata

Do not commit:

- `.env`,
- API keys or access tokens,
- restic credentials or password files,
- workflows unless you have inspected and intentionally sanitized them,
- input/output/generated image assets unless you have inspected their metadata,
- backup repositories or staged restore directories,
- Manager configuration containing credentials.

Workflow JSON can retain API keys or access tokens entered into downloader/API
nodes, including Civitai or Hugging Face integrations. ComfyUI-generated images
can embed workflow metadata and repeat those same values alongside prompts,
filenames, and URLs. `scripts/audit-workflows.py` detects several obvious shapes
but is not proof that a workflow or generated image is safe to publish.

The repository `.gitignore` excludes the normal runtime workflow, image, backup,
and restore directories. That is a guardrail, not a sanitizer: files already
tracked, force-added, copied elsewhere, or committed under another path can still
leak sensitive data.

## Git history

Removing a secret or private workflow from the current tree does not remove it
from earlier commits. Rotate exposed credentials first. Decide separately
whether the repository needs a history rewrite before a generalized public
release, and coordinate that rewrite because it changes commit identities.

## Manager policy

The entrypoint writes explicit policy values from `.env`. Lowering security
broadens what Manager may execute. Review node-pack sources and package install
scripts as code.

## Backup security

The built-in restic repository is encrypted, but the generated password remains
critical. The default local backup directory is designed for low-friction
recovery from bad updates, deletion, or configuration mistakes; a copy on the
same disk does not protect against loss of that disk or machine.

Keep an independent copy of the restic password if the backups matter. For
stronger protection, use the advanced backup options to place another copy on a
different disk, system, NAS, or offsite backend.

A backup containing private models, prompts, workflows, or outputs can be as
sensitive as the live deployment. Staged restores are plaintext and should be
protected and deleted when no longer needed.
