#!/bin/bash

# Check if Python 3.12 is installed
if ! command -v python3.12 &> /dev/null; then
    echo "Error: Python 3.12 is not installed or not in PATH."
    exit 1
fi

# check if the ComfyUI exist and if so change working directory to it 
if [ -d "ComfyUI" ]; then
    cd ComfyUI
else
    echo "directory ComfyUI not found"
    exit 1
fi
# Remove any existing virtual environment
rm -rf .venv

# Create a new virtual environment using Python 3.12
python3.12 -m venv .venv || { echo "Failed to create a virtual environment."; cd ../; exit 1; }

# Activate the virtual environment
source .venv/bin/activate || { echo "Failed to activate the virtual environment."; cd ../; exit 1; }

# Upgrade pip to the latest version
pip install --upgrade pip || { echo "Failed to upgrade pip."; deactivate; cd ../; exit 1; }

# Override torch, torchvision, and torchaudio with custom index URL
pip install torch torchvision torchaudio --extra-index-url https://download.pytorch.org/whl/cu128/ || { echo "Failed to install PyTorch packages."; deactivate; cd ../; exit 1; }

# Check if requirements.txt exists and install the packages
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt || { echo "Failed to install the required packages."; deactivate; cd ../; exit 1; }
else
    echo "requirements.txt not found"
    deactivate
    cd ../
    exit 1
fi

# Deactivate the virtual environment
Deactivate

cd ../

echo "Virtual environment setup complete with Python 3.12 and overridden PyTorch packages."

