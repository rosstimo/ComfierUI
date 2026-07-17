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
- private workflows,
- private input/output assets,
- Manager configuration containing credentials.

Workflow JSON and PNG metadata can contain prompts, filenames, URLs, and tokens.
`scripts/audit-workflows.py` detects several obvious shapes but is not proof that
a file is safe.

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

Restic encrypts repository contents, but the password and backend credentials
remain critical. Keep the password file outside this repository and maintain an
independent recovery copy. Do not place the only password copy inside the backup
it unlocks.

A backup containing private models, prompts, workflows, or outputs can be as
sensitive as the live deployment.
