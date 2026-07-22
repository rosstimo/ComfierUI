# Custom nodes, models, and ComfyUI Manager

## Why ComfierUI defaults to the legacy Manager interface

ComfyUI currently ships two Manager interfaces. ComfierUI enables the familiar
legacy interface by default because it still provides the broadest convenience
feature set, including **Install Models**, which searches the Manager catalog and
downloads model files directly into the server-side model directory.

The newer Manager interface is available, but some workflow-template model links
are browser downloads. On a headless server that means the file lands on the
computer running the browser rather than in the ComfyUI model directory.

Use the default:

```dotenv
COMFYUI_MANAGER_LEGACY_UI=true
```

Opt into the newer interface:

```dotenv
COMFYUI_MANAGER_LEGACY_UI=false
```

Recreate the container after changing the value. No image rebuild is needed:

```bash
docker compose up -d --force-recreate
```

## Installing a model on the server

With the default legacy interface:

1. Open **Manager**.
2. Select **Install Models**.
3. Search for a checkpoint, LoRA, VAE, ControlNet, or other listed model.
4. Review the destination model category.
5. Select **Install**.
6. Follow the container logs until the download finishes.
7. Refresh ComfyUI's model lists or restart the container when needed.

```bash
docker compose logs -f comfyui
```

Manager downloads into `/opt/ComfyUI/models`, which is the host path configured
by `COMFYUI_MODELS_PATH`. The default host location is `./data/models`.

Manager's catalog is useful but not exhaustive. Models downloaded manually must
be placed in the correct category below `COMFYUI_MODELS_PATH`, such as
`checkpoints`, `loras`, `vae`, or `controlnet`.

## Reusing an existing model library

Do not replace `COMFYUI_MODELS_PATH` with a legacy library just to make old models
visible. That also redirects future Manager downloads into the old tree.

Keep the normal writable model directory and add the old library separately:

```dotenv
COMFYUI_MODELS_PATH=./data/models
COMFYUI_EXTRA_MODELS_PATH=/absolute/path/to/old/models
```

Then rerun initialization:

```bash
bash scripts/init.sh
bash scripts/preflight.sh
docker compose up -d --force-recreate
```

The extra library is mounted read-only at `/opt/ComfyUI/models/external` and
registered through `config/extra_model_paths.yaml`. Standard configured model
categories from the old tree remain selectable, while Manager downloads continue
to land under the writable `COMFYUI_MODELS_PATH` tree.

Custom-node-specific model directories are intentionally not guessed. Configure
those only after installing the node pack that owns them and checking its
expected model paths.

## Persistence model

Manager changes three persistent areas:

- model files under `COMFYUI_MODELS_PATH`,
- node-pack repositories under `COMFYUI_DATA_PATH/custom_nodes`,
- Python packages under the `comfyui-python` named volume.

An optional `COMFYUI_EXTRA_MODELS_PATH` library is separate and read-only by
default. These mounted Manager-managed areas survive a normal image rebuild.

Manager and node packs may also write elsewhere inside `/opt/ComfyUI`, just as
they can in a typical local installation. ComfierUI permits those writes so
legacy installers can work, but unmounted application-tree changes disappear on
container recreation. Durable ComfyUI core updates still come from rebuilding
the pinned image.

ComfierUI does not carry private compatibility patches for third-party packs.
The node-pack doctor and backup state report observed versions, import failures,
and unusual behavior. A broken pack is then handled with its upstream project,
a user-maintained fork, or another case-specific decision.

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
- New Manager interface: https://docs.comfy.org/manager/pack-management
- Manager configuration and model download paths: https://docs.comfy.org/manager/configuration
- Manager repository and legacy Install Models documentation: https://github.com/Comfy-Org/ComfyUI-Manager
- Custom-node installation and trust guidance: https://docs.comfy.org/installation/install_custom_node
