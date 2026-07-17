# syntax=docker/dockerfile:1.7

ARG CUDA_IMAGE=nvidia/cuda:13.0.0-base-ubuntu24.04
FROM ${CUDA_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
ARG COMFYUI_REPO=https://github.com/Comfy-Org/ComfyUI.git
ARG COMFYUI_REF=master
ARG PYTORCH_VERSION=2.11.0
ARG TORCHVISION_VERSION=0.26.0
ARG TORCHAUDIO_VERSION=2.11.0
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu130
ARG PUID=1000
ARG PGID=1000

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PATH=/opt/venv/bin:${PATH}

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        ffmpeg \
        git \
        git-lfs \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
        pkg-config \
        python3 \
        python3-pip \
        python3-venv \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid "${PGID}" comfy \
    && useradd --uid "${PUID}" --gid "${PGID}" --create-home --shell /bin/bash comfy

RUN git clone "${COMFYUI_REPO}" /opt/ComfyUI \
    && cd /opt/ComfyUI \
    && git checkout "${COMFYUI_REF}" \
    && git rev-parse HEAD > /opt/comfyui-commit

RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/python -m pip install --upgrade pip setuptools wheel \
    && /opt/venv/bin/python -m pip install \
        "torch==${PYTORCH_VERSION}" \
        "torchvision==${TORCHVISION_VERSION}" \
        "torchaudio==${TORCHAUDIO_VERSION}" \
        --index-url "${TORCH_INDEX_URL}" \
    && /opt/venv/bin/python -m pip install -r /opt/ComfyUI/requirements.txt \
    && if [ -f /opt/ComfyUI/manager_requirements.txt ]; then \
         /opt/venv/bin/python -m pip install -r /opt/ComfyUI/manager_requirements.txt; \
       fi

RUN printf 'PYTORCH_VERSION=%s\nTORCHVISION_VERSION=%s\nTORCHAUDIO_VERSION=%s\nTORCH_INDEX_URL=%s\n' \
        "${PYTORCH_VERSION}" "${TORCHVISION_VERSION}" "${TORCHAUDIO_VERSION}" "${TORCH_INDEX_URL}" \
        > /opt/pytorch-install.env \
    && { \
      cat /opt/comfyui-commit; \
      printf '%s\n' \
        "torch=${PYTORCH_VERSION}" \
        "torchvision=${TORCHVISION_VERSION}" \
        "torchaudio=${TORCHAUDIO_VERSION}" \
        "index=${TORCH_INDEX_URL}"; \
      sha256sum /opt/ComfyUI/requirements.txt; \
      if [ -f /opt/ComfyUI/manager_requirements.txt ]; then \
        sha256sum /opt/ComfyUI/manager_requirements.txt; \
      fi; \
    } | sha256sum | awk '{print $1}' | tee /opt/comfierui-build-id /opt/venv/.comfierui-build-id \
    && mkdir -p \
        /data/cache \
        /data/home \
        /opt/ComfyUI/custom_nodes \
        /opt/ComfyUI/input \
        /opt/ComfyUI/models \
        /opt/ComfyUI/output \
        /opt/ComfyUI/temp \
        /opt/ComfyUI/user/default/workflows \
    && chown -R comfy:comfy /data /opt/venv

COPY --chmod=0755 docker/entrypoint.sh /usr/local/bin/comfierui-entrypoint

USER comfy
WORKDIR /opt/ComfyUI

ENV HOME=/data/home \
    XDG_CACHE_HOME=/data/cache \
    HF_HOME=/data/cache/huggingface \
    TORCH_HOME=/data/cache/torch \
    PIP_CACHE_DIR=/data/cache/pip

EXPOSE 8188

ENTRYPOINT ["/usr/local/bin/comfierui-entrypoint"]
CMD ["--listen", "0.0.0.0", "--port", "8188", "--enable-manager", "--dont-print-server"]
