#!/usr/bin/env python3
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
    check_conda_interpreter(CONDA_ENV_NAME, logger)
from py_modules.lib_aosp_base import *
from py_modules.lib_aosp_testing import *
assert LIB_AOSP_BASE_INITED

import argparse
import subprocess
from typing import List

import os
import subprocess
import re

def get_major_number(device_name, serial):
    cmd = f'adb -s {serial} shell cat /proc/devices'
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=True)
    out, err = proc.communicate()

    for line in out.decode().split('\n'):
        if device_name in line:
            return int(line.split()[0])

    print(f"No device found with name '{device_name}'")
    return None

def create_device_file(device_name, major, minor, serial):
    path = f"/dev/{device_name}"
    expected_dev = f'{major}:{minor}'
    cmd_check = f'adb -s {serial} shell ls -l {path}'
    proc = subprocess.Popen(cmd_check, stdout=subprocess.PIPE, shell=True)
    out, err = proc.communicate()

    # File exists, check device number
    if out:
        if re.search(r'c\s+' + expected_dev, out.decode()):
            return 0
        else:
            cmd_remove = f'adb -s {serial} shell rm {path}'
            os.system(cmd_remove)

    cmd_create = f'adb -s {serial} shell mknod {path} c {major} {minor}'
    os.system(cmd_create)
    cmd_chmod = f'adb -s {serial} shell chmod 777 {path}'
    return os.system(cmd_chmod)

def obja_lz4_file():
    device_name = "obja_lz4"

    major = get_major_number(device_name, serial)
    if major is None:
        return

    print(f"Major number for '{device_name}' is {major}")

    # Assuming minor number is 0
    minor = 0
    if create_device_file(device_name, major, minor, serial) != 0:
        print("Error in creating device file")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--no_remap', action='store_true', help='No remap')
    parser.add_argument('--sunfish', action='store_true', help='Sunfish')
    parser.add_argument('--goldfish', action='store_true', help='Goldfish')
    parser.add_argument('--fake_vanilla', action='store_true', help='No remap')
    parser.add_argument('--real_vanilla', action='store_true', help='No remap')
    parser.add_argument('--image_name', type=str, help='Image name')

    args = parser.parse_args()
    
    if args.sunfish:
        TARGET_KSRCDIR = SUNFISH_KSRCDIR
        KMOD_PATH = f"{TARGET_KSRCDIR}/out/android-msm-pixel-4.14/private/msm-google/mm/lxr_swap_logger_kmod/lxr_km_main.ko"
    elif args.goldfish:
        TARGET_KSRCDIR = GOLDFISH_KSRCDIR
        KMOD_PATH = f"{TARGET_KSRCDIR}/out/x86_64/goldfish/mm/lxr_swap_logger_kmod/lxr_km_main.ko"
    else:
        print("You must choose either sunfish or goldfish")
        exit(0)
    if args.image_name:
        if len(args.image_name) > 0 \
        and (not args.image_name.startswith('oaRAM')) \
        and (not args.image_name.startswith('fleet')) \
        and (not args.image_name.startswith('vanilla')):
            KMOD_PATH = f'{aosp_host_working_dir}/products/{args.image_name}/kos/lxr_km_main.ko'
            logger.notice(f'insmod from cached image: {KMOD_PATH}')

    Aa('wait-for-device')
    As("setprop debug.debuggerd.disable 1", AsOption.STDERR_TO_STDOUT)
    As("setprop  persist.debug.debuggerd.disable_fork 1", AsOption.STDERR_TO_STDOUT)
    if not args.real_vanilla:
        try:
            Aa('push', KMOD_PATH, '/data/local/tmp/lxr_km_main.ko')
        except subprocess.CalledProcessError as e:
            if args.fake_vanilla:
                print("push kernel module failed, real vanilla?")
            else:
                raise e
        if args.no_remap:
            As("insmod /data/local/tmp/lxr_km_main.ko enabled_fetures=0,0,0,1,1,0,0,1", options=AsOption.STDERR_TO_STDOUT)
        elif args.fake_vanilla:
            As("insmod /data/local/tmp/lxr_km_main.ko enabled_fetures=1,1,0,1,1,0,1,0", options=AsOption.STDERR_TO_STDOUT)
            As("cat /proc/lxr_reset_stats")
            As("echo 1 > /proc/sys/kernel/kptr_restrict")
            exit(0)
        else:
            As("insmod /data/local/tmp/lxr_km_main.ko enabled_fetures=1,1,0,1,1,0,1,1", options=AsOption.STDERR_TO_STDOUT)

    As('mknod -m 777 /dev/pp_shm_node c 301 0', options=AsOption.STDERR_TO_STDOUT)
    As('mknod -m 777 /dev/debugmem_node c 303 0', options=AsOption.STDERR_TO_STDOUT)
    As('mknod -m 777 /dev/remap_shm_node c 302 0', options=AsOption.STDERR_TO_STDOUT)
    As('ls -l /dev/pp_shm_node')
    As('ls -l /dev/debugmem_node')
    As('ls -l /dev/remap_shm_node')
    As("cat /proc/lxr_reset_stats")
    As("echo 1 > /proc/sys/kernel/kptr_restrict")
    obja_lz4_file()


if __name__ == '__main__':
    main()