import importlib, sys, os
from os.path import dirname, pardir
from pathlib import Path
from pprint import pprint
from typing import Dict, List, Any, Optional
import threading
import re
import subprocess
from collections import OrderedDict
import pandas as pd
import json
import types

class AndroidFtraceTools:
    @staticmethod
    def get_ftrace_setup_commands():
        commands: List[str] = [
            "mount -t debugfs debugfs /sys/kernel/debug/",
            "echo 0 > sys/kernel/debug/tracing/tracing_on",
            "echo 4096 > /sys/kernel/debug/tracing/buffer_size_kb",
            "echo 4096 > /sys/kernel/debug/tracing/per_cpu/cpu0/buffer_size_kb",
            "echo 4096 > /sys/kernel/debug/tracing/per_cpu/cpu1/buffer_size_kb",
            "echo 4096 > /sys/kernel/debug/tracing/per_cpu/cpu2/buffer_size_kb",
            "echo 4096 > /sys/kernel/debug/tracing/per_cpu/cpu3/buffer_size_kb",
            "echo 4096 > /sys/kernel/debug/tracing/per_cpu/cpu4/buffer_size_kb",
            "echo 4096 > /sys/kernel/debug/tracing/per_cpu/cpu5/buffer_size_kb",
            "echo 4096 > /sys/kernel/debug/tracing/per_cpu/cpu6/buffer_size_kb",
            "echo 4096 > /sys/kernel/debug/tracing/per_cpu/cpu7/buffer_size_kb",
            # "echo 1 > proc/lxr_info_toggle; echo 1 > proc/lxr_debug_toggle; echo 1 > proc/lxr_trace_toggle"
        ]
        return commands