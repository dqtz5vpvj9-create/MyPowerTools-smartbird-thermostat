#! /usr/bin/env python3
import sys, os
from os.path import dirname, pardir
from queue import Queue
from pathlib import Path
import enum
from enum import Enum
from typing import Any, Callable, Optional, Generic, TypeVar, Union, cast
sys.path.append(dirname(__file__) + os.sep + pardir)
print(sys.path)
from py_modules.lib_aosp_testing import *
from py_modules.check_interpreter import check_conda_interpreter, CONDA_ENV_NAME
from py_modules.logging_lib import setup_logging
if __name__ == '__main__':
    check_conda_interpreter(CONDA_ENV_NAME)

assert LIB_AOSP_BASE_INITED
def sync_time() -> None:
    import datetime
    current_time = datetime_class.now()
    time_formated = current_time.strftime("%m%d%H%M%Y.%S")
    As(f"date {time_formated}")

if __name__ == '__main__':
    logger = setup_logging()
    app_finder = AndroidAppFinder(logger)
    sync_time()
    As("mkdir -p /dev/memcg/test_art/")
    As("echo 20M > /dev/memcg/test_art/memory.limit_in_bytes")
    logger.info("Waiting for app")
    pid = app_finder.get_last_running_application()
    logger.info(f"Found pid: {pid}")
    As(f"echo {pid} > /dev/memcg/test_art/tasks")
    As("cat /dev/memcg/test_art/tasks")