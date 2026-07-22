#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

git pull --ff-only
build_services=(comfyui)
if docker compose config --services | grep -qx backup; then
    build_services+=(backup)
fi
docker compose build --pull "${build_services[@]}"
docker compose up -d --force-recreate "${build_services[@]}"
docker compose ps
