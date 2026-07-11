import importlib, sys, os
from os.path import dirname, pardir
from pathlib import Path
def import_parents(level: int = 1) -> None:
    global __package__
    file = Path(__file__).resolve()
    parent, top = file.parent, file.parents[level]
    
    sys.path.append(str(top))
#    try:
#        sys.path.remove(str(parent))
#    except ValueError: # already removed
#        pass

    __package__ = '.'.join(parent.parts[len(top.parts):])
    importlib.import_module(__package__) # won't be needed after that

if __name__ == '__main__' and (__package__ is None or len(__package__) == 0):
    import_parents()
sys.path.append(dirname(__file__) + os.sep + pardir)
from py_modules.logging_lib import setup_logging, LogLevel
logger = setup_logging()
from py_modules.check_interpreter import check_conda_interpreter, CONDA_ENV_NAME
if __name__ == '__main__':
    check_conda_interpreter(CONDA_ENV_NAME)
from py_modules.lib_aosp_base import *
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
parser = argparse.ArgumentParser()
parser.add_argument("--vanilla", action='store_true', required=False)
parser.add_argument("--oaRAM", action='store_true', required=False)
parser.add_argument("--debug", action='store_true', required=False)
parser.add_argument("--product", type=str, default='', required=False, choices=["vanilla-cms", "vanilla-cc", "fleet", "oaRAM"])
args = parser.parse_args()

if not args.vanilla and not args.oaRAM and args.product == '':
    raise ValueError("Please specify --vanilla or --oaRAM or --product")

product = args.product if args.product != ''\
    else "vanilla-cms" if args.vanilla else "oaRAM"

logger.info(f"use product: {product}")
if not product == "oaRAM":
    subprocess.run(ssh_command('lxr2-r743', f"bash --login -c 'export OVERRIDE_ANDROID_TOOLS_serial={serial} && CONDA_EXE=~/miniconda3/bin/conda python3 ~/repo/androidtools/compile_tools/cgdroid.py --sunfish --Restart --SyncOnly --SyncProduct {product}'"), shell=True)
else:
    subprocess.run("cgdroid.py --sunfish --Restart", shell=True)

flash_vanilla_kernel_script = f"C:\\Users\\lixinrui\\miniconda3\\envs\\android_automatic\\python.exe C:\\Users\\lixinrui\\repo\\androidtools\\Flash-45555.py --flash_product {product}"
flash_oaRAM_kernel_script = "C:\\Users\\lixinrui\\miniconda3\\envs\\android_automatic\\python.exe C:\\Users\\lixinrui\\repo\\androidtools\\Flash-45555.py --oaRAM"
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
    if product != "oaRAM":
        cmd = f"{flash_vanilla_kernel_script} --id {request_id}"
        return jsonify({"message": cmd}), 200
    else:
        cmd = f"{flash_oaRAM_kernel_script} --id {request_id}"
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

cloud_server_port: int = 8889
check_conda_interpreter(CONDA_ENV_NAME)
app.run(host='0.0.0.0', port=cloud_server_port)