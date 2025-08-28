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
note: if this instance is intended for multiple users or you want to run it as a service the recomended location to clone the repository is `/opt/ComfierUI/` 

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

## Optionally run ComfyUI as a service

I've included a systemd service file to run ComfyUI as a service. To use it, copy the file to the systemd services folder and enable the service.



first create a dedicated user and group for the service. the user should not have a password or login shell and should not be able to log in interactively.

add the group:
```bash
sudo groupadd ComfyUI
```

on arch based systems:
```bash
sudo useradd -r -s /usr/bin/nologin -g ComfyUI ComfyUI
```

on debian based systems:
```bash
sudo adduser --system --no-create-home --group ComfyUI
```

change the ownership of the directories, files, scripts needed by the ComfyUI user:

```bash
sudo chown -R ComfyUI:ComfyUI ComfierUI
```

copy the service file to the systemd services folder:

```bash
sudo cp ComfyUI.service /etc/systemd/system/
```

restart the systemd daemon and enable the service:

```bash
sudo systemctl daemon-reload
sudo systemctl enable ComfyUI
sudo systemctl start ComfyUI
sudo systemctl status ComfyUI.service
```

