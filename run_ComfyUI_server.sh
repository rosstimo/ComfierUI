#!/bin/bash
# check if ./ComfyUI/.venv exists if so source it, if not exit
if [ -d "./ComfyUI/.venv" ]; then
    source ./ComfyUI/.venv/bin/activate
else
    echo "No virtual environment found, exiting"
    exit 1
fi

# run the server and pass arguments to it
# run nvidia-smi -L to check the GPU device number
# run .ComfyUI/main.py --help to see all available arguments
python ./ComfyUI/main.py --listen 0.0.0.0 --port 8188 --dont-print-server #--cuda-device 1
