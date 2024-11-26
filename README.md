# ComfierUI

This exists because i forget how to setup the ComfyUI server every time.

both ComfyUI and ComfyUI-Manager are included here as submodules. To clone the repository with the submodules, use the following command:

```bash
git clone --recurse-submodules
```

If you have already cloned the repository and forgot to include the submodules, you can use the following command to clone the submodules:

```bash
git submodule update --init --recursive
```

ComfyUI-Manager is normally cloned into ComfyUI/custom_nodes/ in this case add a symlink to the ComfyUI-Manager folder in the ComfyUI/custom_nodes/ folder.

```bash
ln -s ComfyUI-Manager ComfyUI/custom_nodes/ComfyUI-Manager
```


## Usage

run the setup_python_venv.sh script to setup a python virtual environment and install the required packages.

```bash
./setup_python_venv.sh
```

to start the ComfyUI server run the run_ComfyUI_server.sh script. (edit the script as needed)

```bash
./run_ComfyUI_server.sh
```

note: I've included the script `setup.sh` as a work in progress. I plan to use it to setup symlinks ans execute other setup scripts
