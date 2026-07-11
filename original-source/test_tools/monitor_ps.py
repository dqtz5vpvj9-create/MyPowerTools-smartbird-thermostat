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
logger = setup_logging()
from py_modules.check_interpreter import check_conda_interpreter, CONDA_ENV_NAME
if __name__ == '__main__':
    check_conda_interpreter(CONDA_ENV_NAME)
from py_modules.lib_aosp_base import *
from py_modules.lib_aosp_testing import *
assert LIB_AOSP_BASE_INITED

import subprocess
import time
import argparse
from prettytable import PrettyTable
import prettytable
from rich.console import Console
from rich.table import Table as RichTable

class AndroidProcessMonitor:
    def __init__(self, serial, ps_format, interval, pid_only, use_rich, blacklist):
        self.serial = serial
        self.ps_format = ps_format
        self.interval = interval
        self.blacklist = blacklist
        self.headers = ps_format.split(',') + ['MCG']
        self.pid_only = pid_only
        self.target_uid_list = fetch_target_uid_list()
        self.callback = self.uid_callback
        self.use_rich = use_rich
        self.console = Console() if use_rich else None


    def fetch_ps_data(self):
        ps_opt = "-A" if self.pid_only else "-AT"
        cmd = f"adb -s {self.serial} shell \"ps {ps_opt} -o {self.ps_format}\""
        process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = process.communicate()

        if err:
            print("Error:", err.decode())
            return None

        return out.decode()

    def handle_blacklisted_items(self, line):
        for item in self.blacklist:
            if item in line:
                line = line.replace(item, item.replace(' ', '_'))
        return line

    def parse_ps_output(self, output):
        if self.use_rich:
            table = RichTable(show_header=True, header_style="bold magenta")
            for header in self.headers:
                table.add_column(header)
        else:
            table = PrettyTable()
            table.set_style(prettytable.SINGLE_BORDER)
            table.field_names = self.headers


        try:
            mem_limit_bytes = int(subprocess.getoutput(f"adb -s {serial} shell cat /dev/memcg/test_art/memory.limit_in_bytes").strip())
            mem_limit_mb = mem_limit_bytes / (1024 * 1024)
        except Exception as e:
            mem_limit_mb = "N/A"
        lines = output.split('\n')
        for line in lines[1:]:
            if line.strip() == "":
                continue
            line = self.handle_blacklisted_items(line)
            fields = line.split()
            if mem_limit_mb != "N/A":
                fields = fields + [f"{mem_limit_mb:g}MB"]
            else:
                fields = fields + ["N/A"]
            if len(fields) >= len(self.headers):
                # 还原被替换的空格
                fields = [field.replace('_', ' ') for field in fields]

                # 构造字典 {header: value}
                data_dict = dict(zip(self.headers, fields[:len(self.headers)]))
                # 使用回调函数判断是否输出该行
                if self.callback(data_dict):
                    if self.use_rich:
                        table.add_row(*fields[:len(self.headers)])
                    else:
                        table.add_row(fields[:len(self.headers)])
        if self.use_rich:
            self.console.print(table)
        else:
            print(table)

    def monitor(self):
        while True:
            self.target_uid_list = fetch_target_uid_list()
            output = self.fetch_ps_data()
            if output:
                if self.use_rich:
                    self.console.clear()
                self.parse_ps_output(output)
            time.sleep(self.interval)

        

    def is_target_uid(self, data_dict):
        uid = int(data_dict.get('UID', 0))
        return uid in self.target_uid_list

    def uid_callback(self, data_dict):
        name = data_dict.get('name', '')
        return self.is_target_uid(data_dict) or name == 'dalvikvm64'


    def sample_callback(self, data_dict):
        return int(data_dict.get('UID', 0)) == 10135

blacklist = '''
ADB-JDWP Connec
AsyncTask #1
AsyncTask #2
BT Service Call
Crashlytics Exc
Jit thread pool
NDK MediaCodec_
Network File Th
Okio Watchdog
POSIX timer 
POSIX timer 0
POSIX timer 1
POSIX timer 2
POSIX timer 3
POSIX timer 4
POSIX timer 5
POSIX timer 56
POSIX timer 6
POSIX timer 7
Profile Saver
Signal Catcher
UsbService host
adbd auth
ipa driver ntfy
irq/32-KRYO L3-
jdwp control
logger write th
netlink socket
server socket
shell svc 28516
shell svc 28635
sound trigger c
usb ffs open
OkHttp 
OkHttp Connecti
Runtime worker
Measurement Wor
GLThread 
process reaper
HybridData Dest
 Writer
tuan.net Writer
V8 DefaultWorke
SntpClock #
'''.splitlines()

def main():
    ps_default_opt = 'UID,pid,minfl,majfl,rss,swap,name,CMD,%cpu,psr'
    parser = argparse.ArgumentParser(description='Monitor Android process information.')
    parser.add_argument('-s', '--serial', help='Device serial number', default=serial)
    parser.add_argument('-p', '--pid', help='Only show pid', action='store_true', default=False)
    parser.add_argument('-f', '--format', default=ps_default_opt, help='Format for ps command')
    parser.add_argument('-i', '--interval', type=int, default=2, help='Interval between updates in seconds')
    parser.add_argument('-r', '--rich', help='Use rich library for printing tables', action='store_true')


    args = parser.parse_args()


    monitor = AndroidProcessMonitor(args.serial, args.format, args.interval, args.pid, args.rich, blacklist)
    monitor.monitor()

if __name__ == "__main__":
    main()
