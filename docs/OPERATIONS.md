# Operations

## Lifecycle

```bash
docker compose up -d
docker compose ps
docker compose logs -f comfyui
docker compose restart comfyui
docker compose stop comfyui
docker compose down
```

`down` removes containers and the project network. Bind-mounted data and the
named Python volume remain unless `-v` is supplied. Do not use `down -v` casually.

## Health and identity

```bash
curl -fsS http://127.0.0.1:8188/system_stats
docker compose exec comfyui id
docker compose exec comfyui cat /opt/comfyui-commit
docker compose exec comfyui /opt/venv/bin/python -m pip check
```

## Update core and image dependencies

```bash
bash scripts/update.sh
```

This fast-forwards the repository, pulls the configured base image, rebuilds,
and recreates the service. The configured `COMFYUI_REF` remains pinned during this operation. To advance
ComfyUI core, change the tag or commit deliberately, rebuild, test, and retain a
rollback reference.

Before an important update:

```bash
git rev-parse HEAD
docker compose exec comfyui cat /opt/comfyui-commit
bash scripts/restic-backup.sh   # when restic is configured
```

## Change `.env` or Compose layers

```bash
docker compose config
docker compose up -d --force-recreate comfyui
```

A PUID, PGID, base-image, or PyTorch change requires rebuilding. A port, network,
Manager UI, or normal runtime setting usually needs only recreation.

## Custom nodes

See [Custom nodes and Manager](CUSTOM_NODES.md). After changes:

```bash
docker compose restart comfyui
docker compose logs --since=5m comfyui
```

## Inspect persistent resources

```bash
docker compose config
docker volume ls --filter 'label=com.docker.compose.project=comfierui'
docker inspect "$(docker compose ps -q comfyui)"
```

## Validate repository changes

```bash
bash scripts/validate.sh
docker compose config
```

GitHub Actions runs the same static validation for pushes and pull requests.
Hardware tests remain part of the release checklist because hosted CI has no
representative NVIDIA GPU or external model library.
