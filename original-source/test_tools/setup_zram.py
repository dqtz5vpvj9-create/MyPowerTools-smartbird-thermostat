#! /usr/bin/env python3
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
    check_conda_interpreter(CONDA_ENV_NAME, logger=logger)
from py_modules.lib_aosp_base import *
from py_modules.lib_aosp_testing import *
assert LIB_AOSP_BASE_INITED

from typing import Optional, Union, List
from enum import Enum
import argparse
import subprocess
import os
from py_modules.lib_aosp_testing import *
from py_modules.lib_aosp_base import *
from py_modules.check_interpreter import check_conda_interpreter, CONDA_ENV_NAME
from py_modules.logging_lib import setup_logging, LogLevel, MyLogger
import importlib
import sys
import os
from os.path import dirname, pardir
from pathlib import Path

def main():
    # Parse arguments
    parser = argparse.ArgumentParser()
    parser.add_argument("--lz4", action="store_true",
                        help="Enable LZ4 algorithm")
    parser.add_argument("--use_disk_swap", action="store_true",
                        help="Use disk swap")
    parser.add_argument("--setup_marvin_zram", action="store_true",
                        help="setup zram for marvin")
    parser.add_argument("--disable_swap", action="store_true",
                        help="Disable all swap devices")
    args = parser.parse_args()
    # args.disable_swap = True
    if args.disable_swap or args.use_disk_swap:
        try:
            swap_info = As("cat /proc/swaps")
            if swap_info:
                lines = swap_info.split('\n')[1:]  # Skip the header line
                for line in lines:
                    if line.strip():
                        swap_device = line.split()[0]
                        As(f"swapoff {swap_device}")
                        logger.info(f"Disabled swap on {swap_device}")
            else:
                logger.info("No active swap devices found.")
        except Exception as e:
            logger.error(f"Failed to disable swap devices: {e}")
            return
        return

    if args.setup_marvin_zram:
        try:
            logger.info("Setting up zram for Marvin...")
            # 关闭zram swap设备
            As("swapoff /dev/block/zram0")
            logger.info("Disabled swap on zram0")
            
            # 格式化为ext4文件系统
            As("mkfs.ext4 /dev/block/zram0")
            logger.info("Formatted zram0 as ext4")
            
            # 创建挂载点
            As("mkdir -p /sdcard/zram")
            logger.info("Created mount point at /sdcard/zram")
            
            # 挂载zram设备
            As("mount /dev/block/zram0 /sdcard/zram/")
            logger.info("Mounted zram0 to /sdcard/zram/")
            
            # 设置权限
            As("chmod -R 777 /sdcard/zram/")
            logger.info("Set permissions for /sdcard/zram/")
            
            logger.info("Successfully set up zram for Marvin")
            return
        except Exception as e:
            logger.error(f"Failed to setup zram for Marvin: {e}")
            return

    if args.use_disk_swap:
        logger.warning("Using disk swap is not recommended for normal use. It is only for testing purposes.")
        exit(1)

    # Aa and As functions

    def Aa_wait_for_device():
        Aa("wait-for-device")
        Aa("wait-for-device")

    def Aa_remount():
        Aa("remount")

    Aa_wait_for_device()

    try:
        swap_info = As("cat /proc/swaps")
        if "zram0" in swap_info:
            print("zram0 is already enabled")
            As("swapoff /dev/block/zram0")
        if "loop" in swap_info:
            print("Disk swap is already enabled")
            # find the loop device name and disable it
            lines = swap_info.split('\n')
            for line in lines:
                if "/dev/block/loop" in line:
                    loop_device = line.split()[0]
                    As(f"swapoff {loop_device}")
                    print(f"Disabled swap on {loop_device}")
    except Exception as e:
        print(e)
        print("Failed to check swap status")

    if args.use_disk_swap:
        try:
            swapfile_path = "/data/local/tmp/swapfile.img"
            swapfile_size = 4294967296

            # Check if the swap file exists and has the correct size
            if "No such file or directory" in As(f"ls {swapfile_path}"):
                As(f"dd if=/dev/zero of={swapfile_path} bs=1M count=4096")
            else:
                current_size = int(As(f"stat -c%s {swapfile_path}").strip())
                if current_size != swapfile_size:
                    As(f"dd if=/dev/zero of={swapfile_path} bs=1M count=4096")
            loop_device = As(f"losetup --show -f /data/local/tmp/swapfile.img", [AsOption.STDOUT_NO_PRINT, AsOption.STDERR_TO_STDOUT]).strip()
            # Check device matches "/dev/block/loopX" pattern
            if loop_device.startswith("/dev/block/loop") and loop_device[15:].isdigit():
                As(f"mkswap {loop_device}", [AsOption.STDERR_TO_STDOUT])
                As(f"swapon {loop_device}")
            else:
                raise Exception(f"Loop device {loop_device} does not match the expected pattern")
        except Exception as e:
            print(e)
            print("Failed to enable disk swap")
    else:
        try:
                
            try:
                backing_dev = As("cat /sys/block/zram0/backing_dev").rstrip("\n")
            except:
                backing_dev = ""

            As("echo 1 > /sys/block/zram0/reset")

            if not args.lz4:
                As("echo obja_lz4 > /sys/block/zram0/comp_algorithm")
            else:
                As("echo lz4 > /sys/block/zram0/comp_algorithm")

            if backing_dev != "":
                As(f"echo {backing_dev} > /sys/block/zram0/backing_dev")
            As(f"echo {4 * 1024 * 1024 * 1024} > /sys/block/zram0/disksize")
            As("mkswap /dev/block/zram0")
            As("swapon /dev/block/zram0")
            Aa_remount()
            print("zram0 is enabled")
        except Exception as e:
            print(e)
            print("Failed to enable zram0")

    try:
        swap_info = As("cat /proc/swaps")
        if args.use_disk_swap:
            if "loop" in swap_info and "zram0" not in swap_info:
                print("Disk swap is correctly enabled and is the only swap device.")
            else:
                print("Error: Disk swap is not correctly enabled or there are other swap devices active.")
        else:
            if "zram0" in swap_info and "loop" not in swap_info:
                print("zram0 is correctly enabled and is the only swap device.")
            else:
                print("Error: zram0 is not correctly enabled or there are other swap devices active.")
    except Exception as e:
        print(e)
        print("Failed to verify swap status")

if __name__ == "__main__":
    main()
