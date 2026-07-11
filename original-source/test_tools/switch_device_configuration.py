#!/usr/bin/env python3
import importlib
import sys
import os
from os.path import dirname, pardir
from pathlib import Path
import subprocess

import shtab

def import_parents(level: int = 1) -> None:
    global __package__
    file = Path(__file__).resolve()
    parent, top = file.parent, file.parents[level]

    sys.path.append(str(top))
    # try:
    #     sys.path.remove(str(parent))
    # except ValueError: # already removed
    #     pass

    __package__ = '.'.join(parent.parts[len(top.parts):])
    importlib.import_module(__package__)  # won't be needed after that

if __name__ == '__main__' and (__package__ is None or len(__package__) == 0):
    import_parents()
sys.path.append(dirname(__file__) + os.sep + pardir)
from py_modules.logging_lib import setup_logging, LogLevel
logger = setup_logging()
from py_modules.check_interpreter import check_conda_interpreter, CONDA_ENV_NAME
if __name__ == '__main__':
    check_conda_interpreter(CONDA_ENV_NAME)
from py_modules.lib_aosp_base import *
from py_modules.lib_aosp_base import _find_android_sdk
from py_modules.lib_aosp_testing import *
from py_modules.lib_sh import shell_run, ssh_command
assert LIB_AOSP_BASE_INITED

from flask import Flask, Response, jsonify, request
import jsonschema
import logging
import redis
from flask import Flask, Response, jsonify, request
import threading
import schedule
import time
import os
import argparse
from compile_tools.lib_aosp_image_store import verify_ArtProduct

from tap import Tap  # 引入 Tap 库

# 定义配置类，继承自 Tap
class ShellConfig(Tap):
    vanilla: bool = False
    oaRAM: bool = False
    debug: bool = False
    product: str = ''
    skip_kernel: bool = False
    kernel_only: bool = False
    vanilla_from_ubuntu: bool = False
    device: str
    def configure(self):
        shtab.add_argument_to(self, ["-s", "--print-completion"])

# parser = argparse.ArgumentParser()
# parser.add_argument("--vanilla", action='store_true', required=False)
# parser.add_argument("--oaRAM", action='store_true', required=False)
# parser.add_argument("--debug", action='store_true', required=False)
# parser.add_argument("--product", type=str, default='', required=False)
# parser.add_argument("--skip_kernel", action='store_true', required=False)
# parser.add_argument("--kernel_only", action='store_true', required=False)
# parser.add_argument("--device", type=str, required=True)
# args = parser.parse_args()

args = ShellConfig().parse_args()
if not args.vanilla and not args.oaRAM and args.product == '':
    raise ValueError("Please specify --vanilla or --oaRAM or --product")
if not verify_ArtProduct(args.product):
    raise ValueError("Invalid product name")

target_product = args.product

# handle args.vanilla and args.oaRAM compatibility
if args.vanilla and args.oaRAM:
    print("No vanilla oaRAM at the same time")
    exit(1)
elif args.vanilla:
    print("Use vanilla")
    assert args.product == '' or args.product == 'vanilla-cms' or args.product == 'vanilla-compact'
    target_product = 'vanilla-cms' if args.product == '' else args.product
elif args.oaRAM:
    print("Use oaRAM")
    assert args.product == '' or args.product == 'oaRAM'
    target_product = 'oaRAM'
elif args.product:
    pass
else:
    print("No target specified")
    exit(1)
# handle args.vanilla and args.oaRAM compatibility end

# determine the remote compile server
if 'oaRAM' in target_product:
    remote_compile_server = None
elif args.vanilla_from_ubuntu:
    remote_compile_server = 'ubuntu'
else:
    remote_compile_server = 'lxr2-r743'

# determine whether to compile the kernel only
if 'cached-' in target_product:
    sync_only_argument = '--SyncOnly'
else:
    sync_only_argument = ''

# Build the command
if remote_compile_server:
    cmd = f"ssh {remote_compile_server} 'env OVERRIDE_ANDROID_TOOLS_serial={args.device} CONDA_EXE=~/miniconda3/bin/conda python3 ~/repo/androidtools/compile_tools/cgdroid.py --sunfish {sync_only_argument} --ArtProduct {target_product}'"
elif args.vanilla_from_ubuntu:
    cmd = f"env OVERRIDE_ANDROID_TOOLS_serial={args.device} cgdroid.py --sunfish {sync_only_argument} --ArtProduct {target_product}"
else:
    _sdk_dir = _find_android_sdk() or ''
    cmd = f"env PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin:{_sdk_dir}/platform-tools:{os.path.expanduser('~/.local/bin')} OVERRIDE_ANDROID_TOOLS_serial={args.device} cgdroid.py --sunfish {sync_only_argument} --ArtProduct {target_product}"

if not args.kernel_only:
    # Run the command
    subprocess.run(cmd, shell=True, check=True)
if args.skip_kernel:
    exit(0)

if args.vanilla_from_ubuntu:
    flash_kernel_script = f"C:\\Users\\lixinrui\\miniconda3\\envs\\android_automatic\\python.exe C:\\Users\\lixinrui\\repo\\androidtools\\Flash-lxr.py --flash_product {target_product} --device {args.device} --vanilla_from_ubuntu"
else:
    flash_kernel_script = f"C:\\Users\\lixinrui\\miniconda3\\envs\\android_automatic\\python.exe C:\\Users\\lixinrui\\repo\\androidtools\\Flash-lxr.py --flash_product {target_product} --device {args.device}"

import uuid
request_id = uuid.uuid4()
app = Flask(__name__)
logger = app.logger
app.logger.addHandler(logging.StreamHandler(sys.stdout))
app.logger.setLevel(logging.INFO)
@app.route('/get_request', methods=['GET'])
def get_request() -> Tuple[Response, int]:
    # Generate random request_id
    print("Get request")
    cmd = f"{flash_kernel_script} --id {request_id}"
    return jsonify({"message": cmd}), 200
    

add_schema = {
    "type": "object",
    "required": ["message"],
    "properties": {
        "message": {"type": "string"},
    }
}

@app.route('/clear', methods=['GET'])
def clear_notifications() -> Tuple[Response, int]:
    kill_all_children_process(logger, True)
    return jsonify({"status": "ok"}), 200

cloud_server_port: int = 8888
check_conda_interpreter("android_automatic")
app.run(host='0.0.0.0', port=cloud_server_port)