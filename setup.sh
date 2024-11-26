#!/bin/bash

# add symlink to ./ComfyUI-Manager into ./ComfyUI/custom_nodes/ if it exists
if [ -d "./ComfyUI/custom_nodes" ]; then
  ln -sfn ../../ComfyUI-Manager ./ComfyUI/custom_nodes/ComfyUI-Manager
fi


