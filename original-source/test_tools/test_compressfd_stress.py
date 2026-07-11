#!/usr/bin/env python3
from time import sleep
import os
# 获取当前脚本的原始路径
current_file = os.path.realpath(__file__)
# 获取路径的目录部分
current_dir = os.path.dirname(current_file)
# 改变当前的工作目录
os.chdir(current_dir)
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
from py_modules.logging_lib import setup_logging, LogLevel, MyLogger
logger = setup_logging(console_level=LogLevel.INFO)
from py_modules.check_interpreter import check_conda_interpreter, CONDA_ENV_NAME
if __name__ == '__main__':
    check_conda_interpreter('android_automatic', logger=logger)
from py_modules.lib_aosp_base import *
from py_modules.lib_aosp_testing import *
assert LIB_AOSP_BASE_INITED

import argparse
import subprocess

# def keep_freq():
#     full_cmd = ""
#     for cpu_id in range(0, 7):
#         if 0 <= cpu_id <= 5:
#             current_max = 1804800
#         else:
#             current_max = 2208000
#         cpu_freq_min = f"/sys/devices/system/cpu/cpu{cpu_id}/cpufreq/scaling_min_freq"
#         full_cmd += f"echo {current_max} > {cpu_freq_min};"
#         # set min to max
#     logger.debug(f"Change CPU frequency full_cmd: {full_cmd}")
#     As(full_cmd,options=[AsOption.STDERR_TO_STDOUT, AsOption.STDOUT_NO_PRINT])

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--send_pid', action='store_true', help='Send PID')
    parser.add_argument('-m', '--mem', type=int, default=10, help='Memory limit in MB')
    parser.add_argument('-r', '--round', type=int, default=1, help='Wait for #round art instances')
    # parser.add_argument('-f', '--freq', action='store_true', help='keep cpu freq at max')

    args = parser.parse_args()
    
    As(f"date {datetime_class.now().strftime('%m%d%H%M%Y.%S')}")
    As("mkdir -p /dev/memcg/cpfd_stress/")
    As(f"echo {args.mem}M > /dev/memcg/cpfd_stress/memory.limit_in_bytes")
    As("chmod 777 /dev/memcg/cpfd_stress/tasks")
    for i in range(args.round):
        time = datetime_class.now()
        prompt = f"Waiting for {i+1}/{args.round} cpfd_stress.pid"
        logger.info(prompt)
        finder = AndroidRuntimeFinder(setup_logging("RuntimeFinder"))
        pid = finder.find_runtime(time)
        if pid:
            try:
                As(f"echo {pid} > /dev/memcg/cpfd_stress/tasks")
            except subprocess.CalledProcessError as e:
                logger.error(f"Failed to write pid {pid} to memcg")
                raise e
        logger.info(f"Found cpfd_stress.pid in round {i+1}/{args.round}: {pid}")
    # if args.freq:
    #     while True:
    #         keep_freq()
    #         sleep(0.1)
    sys.exit(0)
if __name__ == '__main__':
    main()