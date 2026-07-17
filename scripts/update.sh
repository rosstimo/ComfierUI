#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

git pull --ff-only
docker compose build --pull comfyui
docker compose up -d --force-recreate comfyui
docker compose ps
