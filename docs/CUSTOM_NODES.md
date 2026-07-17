# Custom nodes and ComfyUI Manager

## Persistence model

Manager changes two persistent areas:

- node-pack repositories under `COMFYUI_DATA_PATH/custom_nodes`,
- Python packages under the `comfyui-python` named volume.

Both survive a normal image rebuild. ComfyUI core itself is image-owned and is
updated by rebuilding, not by Manager.

## Installing nodes

1. Load a workflow.
2. Use Manager's missing-node view.
3. Review the proposed node packs and their source repositories.
4. Install a small batch.
5. Restart ComfyUI.
6. Inspect complete logs before filtering.
7. Re-run `scripts/audit-workflows.py`.

Avoid copying an old custom-node tree wholesale. It carries abandoned code,
unknown package pins, and dependencies for workflows that may no longer matter.

## Manager policy

The entrypoint writes these keys to the persistent Manager config:

```dotenv
COMFYUI_MANAGER_SECURITY_LEVEL=normal
COMFYUI_MANAGER_NETWORK_MODE=personal_cloud
```

These defaults support registered pack installation for a self-hosted personal
instance. Lower settings allow broader execution. Do not change them without
understanding the rejected operation.

## Current and legacy interfaces

```dotenv
COMFYUI_MANAGER_LEGACY_UI=false
```

Set `true` when the familiar legacy interface is temporarily useful, then
recreate the container. No image rebuild is needed.

## Dependency troubleshooting

```bash
docker compose restart comfyui
docker compose logs --since=5m comfyui
docker compose exec comfyui /opt/venv/bin/python -m pip check
```

Native-library failures belong in the Dockerfile when they are stable runtime
requirements. Do not install a host package and expect the container to see it.

## Clean venv recovery

Recreating the named volume removes all additional custom-node packages:

```bash
docker compose down
volume="$(docker volume ls -q \
  --filter "label=com.docker.compose.project=${COMPOSE_PROJECT_NAME:-comfierui}" \
  --filter 'label=com.docker.compose.volume=comfyui-python' | head -n1)"
docker volume rm "${volume}"
docker compose up -d --build
```

Retain custom-node code, then reinstall or repair only the packages those nodes
actually require.

## Official references

- Manager installation and launch flags: https://docs.comfy.org/manager/install
- Current Manager interface: https://docs.comfy.org/manager/pack-management
- Manager configuration and risk levels: https://docs.comfy.org/manager/configuration
- Custom-node installation and trust guidance: https://docs.comfy.org/installation/install_custom_node
