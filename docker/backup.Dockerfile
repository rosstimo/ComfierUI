# syntax=docker/dockerfile:1.7

ARG RESTIC_VERSION=0.19.1
FROM restic/restic:${RESTIC_VERSION}

# The backup service inspects Git worktrees and writes a structured state
# manifest immediately before each snapshot. Keep these tools in the isolated
# backup image rather than mounting the host Docker socket or host binaries.
USER root
RUN apk add --no-cache git jq python3 util-linux
