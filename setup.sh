#!/bin/bash

# add symlink to ./ComfyUI-Manager into ./ComfyUI/custom_nodes/ if it exists
if [ -d "./ComfyUI/custom_nodes" ]; then
  ln -sfn ../../ComfyUI-Manager ./ComfyUI/custom_nodes/ComfyUI-Manager
fi

# if models directory doesn't exists in the current directory, create a symlink to ./ComfyUI/models
if [ -d "./models" ]; then
  mv ./ComfyUI/models ./ComfyUI/models~
  ln -sf ../models ./ComfyUI/models
fi

# if output directory doesn't exists in the current directory, create a symlink to ./ComfyUI/output
if [ -d "./output" ]; then
  mv ./ComfyUI/output ./ComfyUI/output~
  ln -sf ../output ./ComfyUI/output
fi

# if input directory doesn't exists in the current directory, create a symlink to ./ComfyUI/input
if [ -d "./input" ]; then
  mv ./ComfyUI/input ./ComfyUI/input~
  ln -sf ../input ./ComfyUI/input
fi

# if workflows directory doesn't exists in the current directory, create a symlink to ./ComfyUI/user/default/workflows
if [ -d "./ComfyUI/user/default/workflows" ]; then
  mv ./ComfyUI/user/default/workflows ./ComfyUI/user/default/workflows~
  ln -sf ../workflows ./ComfyUI/user/default/workflows
fi
