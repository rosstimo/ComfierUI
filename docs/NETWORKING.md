# Networking

## Default

Compose creates a project-local bridge network. The published service endpoint
is controlled by `COMFYUI_BIND_ADDRESS` and `COMFYUI_PORT`.

The default is localhost:

```dotenv
COMFYUI_BIND_ADDRESS=127.0.0.1
COMFYUI_PORT=8188
```

This works for local use, SSH tunnels, and a reverse proxy running on the host.

## LAN access

```dotenv
COMFYUI_BIND_ADDRESS=0.0.0.0
```

Binding to all interfaces is not authentication. Restrict the port with a host
firewall and decide whether every LAN client is trusted.

## Optional existing network

Append the override and supply an existing network name:

```dotenv
COMPOSE_FILE=compose.yaml:compose.nvidia.yaml:compose.external-network.yaml
COMFYUI_EXTERNAL_NETWORK=ai-services
```

```bash
docker network inspect ai-services >/dev/null || docker network create ai-services
docker compose up -d --force-recreate
```

The service remains attached to its project network and also joins the external
network. Peers use Docker DNS name `comfyui` and port `8188`, not the published
host port or a container IP.

This is intentionally optional. A public repository must not assume that every
operator has a network named `ai-services`.

## Multiple deployments

Distinct projects can share one external network. Give them unique service names
through a local Compose override when DNS ambiguity matters, or keep them on
separate networks.

## Remote access

Prefer a VPN, SSH tunnel, or authenticated reverse proxy. A tunnel provides a
transport path; application access control still needs deliberate design.

## Official references

- Compose networking: https://docs.docker.com/compose/how-tos/networking/
- External networks: https://docs.docker.com/reference/compose-file/networks/
- Port publishing: https://docs.docker.com/engine/network/port-publishing/
