# yapf: off
# yapf: disable
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
from stat_tools.android_zram_mm_stat_tool import parse_zram_mm_stat
from stat_tools.android_gettimecnt import *
class MyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, bytes):
            return str(obj, encoding='utf-8')
        if isinstance(obj, types.FunctionType):
            return obj.__name__
        return json.JSONEncoder.default(self, obj)




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
# logger = setup_logging(console_level=LogLevel.INFO)
from py_modules.check_interpreter import check_conda_interpreter, CONDA_ENV_NAME
if __name__ == '__main__':
    check_conda_interpreter(CONDA_ENV_NAME)
from py_modules.lib_aosp_base import *
from py_modules.lib_aosp_testing import *
# from py_modules.simple_http_notification_sender import MockSimpleHttpNotificationSender
# from py_modules.simple_http_notification_conf import (cloud_server_ip,
#                                                       cloud_server_port,
#                                                       cloud_server_protocol)
from py_modules.lib_aosp_testing import AndroidAppFinder, StreamFileFilter, AndroidRuntimeFinder
from ci_config import TestType, Config
from py_modules.lib_sh import *
from py_modules.lib_aosp_testing import GitRepoManager, get_zygote_pid
import shutil
assert LIB_AOSP_BASE_INITED
# yapf: enable
# yapf: on

from datetime import datetime as datetime_class
from copy import deepcopy

from typing import TypeAlias

class Path_Base():
    @staticmethod
    def get_swap_stats_path(par: str, pid: int, reason: str) -> str:
        fn = f"swap-{pid}-{reason}.txt"
        file_path1 = os.path.join(par, 'swap_stats', fn)
        file_path2 = os.path.join(par, 'swap_stats', slugify_path_wrong(fn))
        file_path3 = os.path.join(par, 'per_app', 'swap_stats', fn)


    

class Ci_Base():
    def __init__(self, serial: str, config: Config, logger: MyLogger) -> None:
        self.serial = serial
        self.device_type_map = {
            r"emulator-.*": "goldfish",
            r"127.0.0.1:.*": "sunfish",
            r"px\d:.*": "sunfish",
        }
        self.check_device_match()
        assert self.sunfish is not None
        # self.notifier: MockSimpleHttpNotificationSender
        self.channel_name: str
        self.test_name: Optional[str] = None
        self.test_report_folder: str
        self.art_test_stdout_log: str
        self.art_test_instance_stdout_logs: dict[int, str] = {}
        self.logger: MyLogger = logger
        self.zram_stat_fn: str
        self.zram_mm_stat_fn: str
        self.disk_swap_device: Optional[str] = None
        self.reload_test_configuration(config)
    
    def check_device_match(self) -> None:
        for pattern, device_type in self.device_type_map.items():
            if re.match(pattern, self.serial):
                if device_type == "goldfish":
                    self.sunfish = False
                    raise Exception("Goldfish is not supported")
                elif device_type == "sunfish":
                    self.sunfish = True
                else:
                    raise Exception("Unknown device type")
                    assert False, "Unknown device type"
                break

    def reload_test_configuration(self, config: Config):
        self.config = config
        self.setup_report_name()
        self.test_configuration = self.get_test_configuration()

    @property
    def sync_time_cmd(self) -> str:
        cmd = f"date {datetime_class.now().strftime('%m%d%H%M%Y.%S')}"
        return cmd

    def check_notifier(self) -> None:
        """检查消息通知服务是否可用
        """
        # self.notifier = MockSimpleHttpNotificationSender(cloud_server_protocol,
        #                                              cloud_server_ip,
        #                                              cloud_server_port, logger)
        # self.channel_name = "art_ci"
        # self.notifier.clear(self.channel_name)
        pass
    
    @staticmethod
    def get_test_time_spec(root_dir) -> dict:
        now = datetime_class.now()

        # Determine AM or PM
        if now.hour < 12:
            meridian = 'AM'
        else:
            meridian = 'PM'

        # Format the date and time as YY_MM_DD and H_M_S
        date_str = now.strftime('%y_%m_%d')
        time_str = now.strftime('%H_%M_%S')

        # Search for existing folders with the same prefix
        ptn = f"{date_str}.+{meridian}-test"
        existing_folders = [
            folder for folder in os.listdir(root_dir)
            if re.search(ptn, folder)
        ]
        test_num = len(existing_folders) + 1
        return {
            'date_str': date_str,
            'time_str': time_str,
            'meridian': meridian,
            'test_num': test_num,
            'ptn': ptn,
            'existing_folders': existing_folders,
        }


    def setup_report_name(self) -> None:
        """设置测试结果report路径
        xx年xx月xx日 上午/下午 第n次测试（进行中/失败）
        """
        if self.config.test_result_dir_override and self.config.test_result_dir_override != "":
            self.test_report_folder = self.config.test_result_dir_override
            return
        
        test_time_spec = Ci_Base.get_test_time_spec(aosp_host_working_dir)
        date_str = test_time_spec['date_str']
        time_str = test_time_spec['time_str']
        meridian = test_time_spec['meridian']
        test_num = test_time_spec['test_num']
        ptn = test_time_spec['ptn']
        existing_folders = test_time_spec['existing_folders']

        # Create the new folder name with the format YY_MM_DD_AM/PM_H_M_S_test<n>
        if self.config.current_config.value == TestType.TEST_PCMARK.value:
            self.test_name = f"pcmark-{date_str}-{time_str}-MEM-{self.config.art_test_memcg_mb}-{meridian}-{self.config.art_test_type.value}-test{test_num}"
        elif self.config.current_config.value == TestType.TEST_MULTIAPP.value:
            self.test_name = f"MULTIAPP-{date_str}-{time_str}-MEM-{self.config.art_test_memcg_mb}-{meridian}-test{test_num}"
        else:
            self.test_name = f"{date_str}-{time_str}-{meridian}-test{test_num}-mem_{self.config.art_test_memcg_mb}-n_{self.config.art_test_instance_cnt}"

        # Print the folder name and the number of existing folders with the same prefix
        self.logger.info(f"Folder name: {self.test_name}")
        self.logger.info(
            f"Number of existing folders with same pattern {ptn}: {len(existing_folders)}"
        )
        if self.test_name is None:
            self.logger.error("Test name is not set")
            raise ValueError("Test name is not set")
        self.test_report_folder = os.path.join(aosp_host_working_dir,
                                               self.test_name)

    def setup_report_dir(self, record_status_repos_manager) -> None:
        try:
            os.makedirs(self.test_report_folder, exist_ok=True)
            self.logger.notice(f"Test report folder: {self.test_report_folder}")
        except FileExistsError:
            pass
        # with open(os.path.join(self.test_report_folder, "repo_describe.diff"),
        #           "w") as f:
        #     f.write(record_status_repos_manager.get_current_status())
        with open(os.path.join(self.test_report_folder, "test_describe.json"),
                  "w") as f:
            dumped_test_configuration = deepcopy(self.test_configuration)
            dumped_test_configuration
            json.dump(self.test_configuration, fp=f, indent=4, cls=MyEncoder)
            # pprint(self.test_configuration, stream=f)
        self.logcat_fn = f"{self.test_report_folder}/adb-{FnStr.time()}.log"
        self.zram_stat_fn = f"{self.test_report_folder}/zram_stat.csv"
        self.zram_mm_stat_fn = f"{self.test_report_folder}/zram_mm_stat.csv"

    @property
    def logcat_cmd(self) -> str:
        logcat_cmd = f"adb -s {self.serial} logcat -v usec 2>&1 | tee {self.logcat_fn}"
        return logcat_cmd

    @property
    def insmod_cmd(self) -> Dict[str, Any]:
        return {
            "cmd": self.test_configuration["insmod_cmd"],
            "succ": self.test_configuration["insmod_succ"]
        }

    def dmesg_ctl_cmd(self, info, debug, trace) -> str:
        kctl_cmd = f"adb -s {self.serial} shell 'echo {info} > proc/lxr_info_toggle; echo {debug} > proc/lxr_debug_toggle; echo {trace} > proc/lxr_trace_toggle'"
        return kctl_cmd

    @property
    def rosalloc_page_release_mode_cmd(self) -> str:
        if self.config.rosalloc_page_release_mode.value == self.config.RosAllocPageReleaseMode.ALL.value:
            return "setprop persist.dalvikvm.pagereleasemode.keep 0"
        elif self.config.rosalloc_page_release_mode.value == self.config.RosAllocPageReleaseMode.SizeAndEnd.value:
            return "setprop persist.dalvikvm.pagereleasemode.keep 1"
        else:
            raise Exception("未知的Rosalloc Page Release Mode")

    # 输入的字符串
    def replace_timestr(self, input_str, i):
        # 使用正则表达式找到日期字符串
        date_pattern = re.compile(r"\d{8}\.\d{2}\.\d{2}\.\d{2}")
        matches = date_pattern.findall(input_str)

        if matches:
            last_date = matches[-1]  # 获取最后一个日期字符串
            replacement = last_date + f"-{i}"  # 生成替换字符串
            input_str = input_str.replace(last_date, replacement)  # 替换最后一个日期字符串
        return input_str

    def run_test_cmd(self, instance_cnt: int) -> Dict[Any, Any]:
        instance_stdout_fn = self.replace_timestr(self.art_test_stdout_log, instance_cnt)
        self.art_test_instance_stdout_logs[instance_cnt] = instance_stdout_fn
        cmds: list[str] = deepcopy(self.test_configuration["test_cmds"])
        for i, _ in enumerate(cmds):
            cmds[i] = cmds[i].replace(self.art_test_stdout_log, instance_stdout_fn)
        return {
            "cmd": cmds,
            "stdout_fn": instance_stdout_fn,
            "ps_name": self.test_configuration["ps_name"]
        }

    def record_test_start(self, start_time) -> None:
        with open(os.path.join(self.test_report_folder, "test_name.txt"),
                  "w") as f:
            assert self.test_name is not None
            f.write(self.test_name + "\n")
            f.write(f"test start time: {start_time}\n")
            for cmd in self.test_configuration["test_cmds"]:
                f.write(cmd + "\n")
            pprint(self.test_configuration, stream=f)
        # Copy contents of ci_config.py and ci_config_user.yml to test report folder
        p = str((Path(__file__).parent.parent / "ci_config.py").absolute())
        shutil.copyfile(p, os.path.join(self.test_report_folder,
                                        "ci_config.py"))
        p = str(
            (Path(__file__).parent.parent / "ci_config_user.yml").absolute())
        shutil.copyfile(p, os.path.join(self.test_report_folder,
                                        "ci_config.py"))

    def find_test_pid(self, test_start_time) -> Optional[int]:
        return self.test_configuration["pid_finder"](test_start_time)

    def setup_info_dir(self, pid) -> None:
        self.info_dir = os.path.join(self.test_report_folder,
                                     f"art_test_info_{pid}")
        try:
            os.mkdir(self.info_dir)
        except FileExistsError:
            pass

    @staticmethod
    def ps_monitor(ci_code, proc_name: str, flag: threading.Event):
        with open(os.path.join(ci_code.info_dir, "ps.txt"), "w") as f:
            f.write(
                "  pid,  minfl, majfl,  rss,  swap, comm,  CMD, cpu, psr\n")
        with open(os.path.join(ci_code.info_dir, "ps.txt"), "w") as f:
            while not flag.is_set():
                ps_out = As(
                    f"ps -AT -o pid,minfl,majfl,rss,swap,name,CMD,%cpu,psr | grep {proc_name}",
                    options=[
                        AsOption.STDERR_TO_STDOUT, AsOption.STDOUT_NO_PRINT
                    ])
                ps_out = ps_out.strip()
                f.write(f"{ps_out}\n")
            flag.wait(1)

    @staticmethod
    def logcat_monitor(ci_code: 'Ci_Base', test_pid,
                       failed_event: threading.Event,
                       stop_event: threading.Event, failed_reasons: List[str]):
        stream_file_filter = StreamFileFilter(ci_code.logger,
                                              ci_code.logcat_fn,
                                              test_pid,
                                              failed_event=failed_event,
                                              stop=stop_event,
                                              failed_reasons=failed_reasons)
        ci_code.__setattr__("app_logcat_fn", stream_file_filter.app_logcat_fn)
        stream_file_filter.run()

    def after_test_gather_info(self, test_pid):
        Path(self.info_dir).mkdir(parents=True, exist_ok=True)
        assert os.path.exists(self.info_dir)
        self.logger.info(f"Art test info will be saved to {self.info_dir}")
        # with open(os.path.join(info_dir, "logcat.txt"), "w") as f:
        #     f.write(As(f"as logcat --pid {test_pid} -d"))

        file_dict = {
            "maps.txt": {
                "path": f"/proc/{test_pid}/maps",
                "header": None
            },
            "smaps.txt": {
                "path": f"/proc/{test_pid}/smaps",
                "header": None
            },
            "status.txt": {
                "path": f"/proc/{test_pid}/status_extended",
                "header": None
            },
            "stat_readable.txt": {
                "path": f"/proc/{test_pid}/stat_readable",
                "header": None
            },
            "zram0_stat.txt": {
                "path":
                "/sys/block/zram0/stat",
                "header":
                "readIO\treadMerge\treadSectors\treadTicks\twriteIO\twriteMerge\twriteSectors\twriteTicks\tinFlight\tioTicks\ttime_in_queue\n"
            }
        }

        for filename, info in file_dict.items():
            try:
                with open(os.path.join(self.info_dir, filename), "w") as f:
                    if info["header"] is not None:
                        f.write(info["header"])
                    stdout, _ = shell_run(
                        f"adb -s {self.serial} shell cat {info['path']}",
                        timeout=1,
                        check_error=True,
                        callback=lambda line: True)
                    f.write(stdout)
            except subprocess.CalledProcessError as e:
                with open(os.path.join(self.info_dir, "proc_crash.log"),
                          "a") as f:
                    f.write(e.stderr)

    def pull_perf_data(self, test_pid):
        pf_file = os.path.join(self.info_dir, f"perf_{test_pid}.data")
        pf_ret = os.path.join(self.info_dir, f"report_{test_pid}.html")
        Aa("pull", "/data/local/tmp/perf.data", pf_file)
        self.logger.info(f"Art test info saved to {self.info_dir}")
        self.logger.info("decoding perf.data")
        parse_p = subprocess.Popen(
            f"python {ASRCDIR}/system/extras/simpleperf/scripts/report_html.py --no_browser -i {pf_file} -o {pf_ret}; rm {pf_file}",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True)
        return parse_p

    def fix_cpu_freq(self, cpu_id: int):
        cpu_freq_governor = f"/sys/devices/system/cpu/cpu{cpu_id}/cpufreq/scaling_governor"
        As(f"echo performance > {cpu_freq_governor}",
           options=[AsOption.STDERR_TO_STDOUT, AsOption.STDOUT_NO_PRINT])
        cpu_freq_max = f"/sys/devices/system/cpu/cpu{cpu_id}/cpufreq/scaling_max_freq"
        current_max = As(
            f"cat {cpu_freq_max}",
            options=[AsOption.STDERR_TO_STDOUT,
                     AsOption.STDOUT_NO_PRINT]).strip()
        cpu_freq_min = f"/sys/devices/system/cpu/cpu{cpu_id}/cpufreq/scaling_min_freq"
        # set min to max
        As(f"echo {current_max} > {cpu_freq_min}",
           options=[AsOption.STDERR_TO_STDOUT, AsOption.STDOUT_NO_PRINT])
        current_min = As(
            f"cat {cpu_freq_min}",
            options=[AsOption.STDERR_TO_STDOUT,
                     AsOption.STDOUT_NO_PRINT]).strip()
        self.logger.debug(
            f"current min freq of cpu {cpu_id}: {current_min} -> {current_max}"
        )
    
    

    def get_test_configuration(self) -> Dict[str, Any]:
        configuration: Dict[str, Any] = {}

        configuration["test_type"] = self.config.current_config.value
        # Insmod commands
        match self.sunfish:
            case True:
                insmod_sunfish = "--sunfish"
            case False:
                insmod_sunfish = "--goldfish"
        getSameDirFn = lambda x: os.path.join(os.path.dirname(__file__), x)
        setup_zram = getSameDirFn("setup_zram.py")
        if self.config.use_disk_swap:
            setup_zram = f"{setup_zram} --use_disk_swap"
        elif self.config.setup_marvin_zram:
            setup_zram = f"{setup_zram} --setup_marvin_zram"
        zram_insmod = getSameDirFn("zram_insmod.py") + " " + insmod_sunfish
        if self.config.image_name and len(self.config.image_name) > 0:
            zram_insmod += f" --image_name {self.config.image_name}"
        test_art = getSameDirFn("test_art.py")

        # Test commands
        test_cmd = []
        ps_name = None

        if self.config.check_test_type(TestType.TEST_GUIAPP):
            configuration[
                "insmod_cmd"] = f"{setup_zram}; {zram_insmod}; test_app.py"
            configuration["insmod_succ"] = ["- test_app -", "Waiting for app"]

            test_cmd += [
                f"adb -s {self.serial} install ~/aosp_host_working_dir/largeobjectstest.apk",
                f"adb -s {self.serial} am force-stop com.example.largeobjectstest",
                f"adb -s {self.serial} am start -n com.example.largeobjectstest/.MainActivity"
            ]
            ps_name = "largeobjectstest"
            app_finder = AndroidAppFinder(self.logger)
            configuration[
                "pid_finder"] = lambda test_start_time: app_finder.get_last_running_application(
                    test_start_time)

        elif self.config.check_test_type(self.config.AndroidRuntimeTestGroup) \
            or self.config.check_test_type(TestType.TEST_PCMARK) \
            or self.config.check_test_type(TestType.TEST_MULTIAPP) \
            or self.config.check_test_type(self.config.KernelStressTestGroup):
            art_test_type = self.config.art_test_type.value
            art_test_instance_cnt = self.config.art_test_instance_cnt
            art_test_memcg_mb = self.config.art_test_memcg_mb
            if art_test_type == self.config.AndroidRuntimeTestType.INSTR_STRESS.value:
                art_test_insmod_cmd = f"{setup_zram}; {zram_insmod}; {test_art} -m {art_test_memcg_mb} -r {art_test_instance_cnt}"
                prompt = [f"Waiting for 1/{art_test_instance_cnt} art.pid"]
                art_test_succ = prompt
            elif art_test_type == self.config.AndroidRuntimeTestType.VANILLA_STRESS.value:
                art_test_insmod_cmd = f"{setup_zram} --lz4; {zram_insmod} --fake_vanilla ; {test_art} -m {art_test_memcg_mb} -r {art_test_instance_cnt}"
                prompt = [f"Waiting for 1/{art_test_instance_cnt} art.pid"]
                art_test_succ = prompt
            elif art_test_type == self.config.AndroidRuntimeTestType.INSTR_NO_STRESS.value:
                art_test_insmod_cmd = f"{setup_zram}; {zram_insmod}"
                art_test_succ = ["/dev/remap_shm_node"]
            elif art_test_type == self.config.AndroidRuntimeTestType.VANILLA_NO_STRESS.value:
                art_test_insmod_cmd = f"{setup_zram} --lz4; {zram_insmod} --fake_vanilla"
                art_test_succ = ["zram0 is enabled"]
            else:
                raise Exception("未指定或未知的art test运行模式")

            configuration["insmod_cmd"] = art_test_insmod_cmd
            configuration["insmod_succ"] = art_test_succ

            test_name = self.config.current_config.value
            self.art_test_stdout_log = f"{self.test_report_folder}/test-{FnStr.time()}.log"

            if self.sunfish:
                env_prefix = sunfish_env_command
                out_dir_name = sunfish_out_dir_name
                ANDROID_PRODUCT_OUT = f"{ASRCDIR}/{out_dir_name}/target/product/sunfish"
            else:
                env_prefix = goldfish_env_command
                out_dir_name = goldfish_out_dir_name
                ANDROID_PRODUCT_OUT = f"{ASRCDIR}/{out_dir_name}/target/product/generic_x86_64"
            
            if self.config.check_test_type(self.config.KernelStressTestGroup):
                force_remap_cmd = f"adb -s {self.serial} shell 'echo 1 > /proc/lxr_force_use_remap'"
                push_comp_stress_test_cmd = f"adb -s {self.serial} push {ANDROID_PRODUCT_OUT}/system/bin/{test_name} /system/bin/"
                test_args = self.config.KernelStressTestArgument
                self.art_test_stdout_log = f"{self.test_report_folder}/test-{FnStr.time()}.log"
                kernel_stress_test_cmd = f"adb -s {self.serial} shell {test_name} {test_args} 2>&1 | tee {self.art_test_stdout_log}"
                test_cmd.extend([force_remap_cmd, push_comp_stress_test_cmd, kernel_stress_test_cmd])
                ps_name = test_name
                runtime_finder_logger = setup_logging("runtime_finder",
                                                    console_level=LogLevel.DEBUG)
                runtime_finder = AndroidRuntimeFinder(
                    runtime_finder_logger,
                    pid_file_dir="/data/local/tmp",
                    pid_file="cpfd_stress.pid")
                configuration[
                    "pid_finder"] = lambda test_start_time: runtime_finder.find_runtime(
                        test_start_time)
            else:
                if not self.config.art_test_use_jit:
                    raise RuntimeError("JIT must be enabled for ART tests")
                jit_option = " --jit " if self.config.art_test_use_jit else ""
                opt_option = " -O " if self.config.art_test_use_optimized_libart else ""
                if not self.config.art_test_use_large_heap:
                    raise RuntimeError("Large heap must be enabled for ART tests")
                large_heap_option = f" --runtime-option -Xmx512m --runtime-option -XX:HeapGrowthLimit={512*1024*1024} " if self.config.art_test_use_large_heap else ""
                
                if self.config.art_test_attach_to_gdb:
                    gdb_option = "--gdbserver --gdbserver-port :11234 --gdbserver-bin /android/original_clang_llvm-13.0.0-x86_64-linux-gnu-ubuntu-20.04/bin/lldb-server "
                else:
                    gdb_option = ""
                art_test_cmd = f"./{env_prefix} env ANDROID_SERIAL={self.serial} ./art/test/run-test {large_heap_option} --dev {gdb_option} --no-prebuild --never-clean --64 --timeout 864000 {opt_option} {jit_option} {test_name} 2>&1 | tee {self.art_test_stdout_log}"
                test_cmd.append(art_test_cmd)
                ps_name = "dalvikvm64"
                runtime_finder_logger = setup_logging("runtime_finder",
                                                    console_level=LogLevel.DEBUG)
                runtime_finder = AndroidRuntimeFinder(runtime_finder_logger)
                configuration[
                    "pid_finder"] = lambda test_start_time: runtime_finder.find_runtime(
                        test_start_time)
        else:
            raise Exception("未知测试类型")

        configuration["test_cmds"] = test_cmd
        configuration["ps_name"] = ps_name

        return configuration

    
    @property
    def prepare_logcat_cmd(self) -> str:
        # ret = f"adb -s {self.serial} shell 'dmesg -C; logcat -c; logcat -G 150M; logcat -g'"
        ret = f"adb -s {self.serial} shell 'logcat -c; logcat -G 150M; logcat -g'"
        return ret
    
    def record_swap_statistics(self, pid: Optional[int], reason: str) -> None:
        """ This function records swap, f2fs, compressing statistics for a given process to a file

        Args:
            pid (Optional[int]): The process ID to record statistics for
            reason (str): The reason for recording statistics, used to name the output file
        """
        reason = reason.replace('.', '_')
        output_dir = f"{self.test_report_folder}/swap_stats"
        os.makedirs(output_dir, exist_ok=True)

        # 执行adb shell命令，捕获输出
        swapin_cmd = f"adb -s {self.serial} shell 'cat /proc/lxr_swapin_statistics'"
        swapin_output = subprocess.check_output(swapin_cmd, shell=True, encoding='utf-8')

        swapout_cmd = f"adb -s {self.serial} shell 'cat /proc/lxr_swapout_statistics'"
        swapout_output = subprocess.check_output(swapout_cmd, shell=True, encoding='utf-8')

        # 将输出保存到响应文件中
        fn = slugify_path(f"swap-{pid}-{reason}.txt")
        with open(f"{output_dir}/{fn}", "w") as swap_file:
            swap_file.write(swapin_output)
            swap_file.write(swapout_output)
        
 

        try: 
            f2fs_cmd = f"adb -s {self.serial} shell 'cat /proc/lxr_f2fs_fault_statistics'"
            f2fs_output = subprocess.check_output(f2fs_cmd, shell=True, encoding='utf-8')
            with open(f"{output_dir}/f2fs-{pid}-{reason}.txt", "w") as f2fs_file:
                f2fs_file.write(f2fs_output)
        except subprocess.CalledProcessError:
            pass

        try: 
            f2fs_cmd = f"adb -s {self.serial} shell 'cat /proc/lxr_f2fs_fault_statistics_launching'"
            f2fs_output = subprocess.check_output(f2fs_cmd, shell=True, encoding='utf-8')
            with open(f"{output_dir}/f2fs_launching-{pid}-{reason}.txt", "w") as f2fs_file:
                f2fs_file.write(f2fs_output)
        except subprocess.CalledProcessError:
            pass

        try: 
            comp_stat_cmd = f"adb -s {self.serial} shell 'cat /proc/lxr_compress_statistics'"
            comp_stat_output = subprocess.check_output(comp_stat_cmd, shell=True, encoding='utf-8')
            with open(f"{output_dir}/comp_stat-{pid}-{reason}.txt", "w") as comp_stat_file:
                comp_stat_file.write(comp_stat_output)
        except subprocess.CalledProcessError:
            pass

    def reset_swap_statistics(self) -> None:
        clear_stats_cmd = f"adb -s {self.serial} shell 'cat /proc/lxr_reset_stats'"
        subprocess.run(clear_stats_cmd, shell=True)

    def record_zram_statistics(self, timespec: timespec_t, reason: Optional[str], pid: Optional[int]) -> None:
        with open(self.zram_stat_fn, "a") as zram_stat_f:
            try:
                mono_time, vct, ratio = timespec
                if self.config.use_disk_swap:
                    return
                    if self.disk_swap_device is None:
                        found = 0
                        swap_info = As("cat /proc/swaps")
                        if "loop" in swap_info:
                            lines = swap_info.split('\n')
                            for line in lines:
                                if "/dev/block/loop" in line:
                                    self.disk_swap_device = line.split()[0]
                                    found += 1
                                    break
                        if found == 0 or found > 1:
                            raise Exception(f"Found {found} disk swap devices")
                    assert self.disk_swap_device is not None
                    sys_fs_path = f"/sys/block/{self.disk_swap_device.split('/')[-1]}"
                    ret = As(f'cat {sys_fs_path}/stat', options=AsOption.STDOUT_NO_PRINT).split()
                    self.logger.warning(f"Using disk swap, pulled stats from {sys_fs_path}")
                else:
                    ret = As('cat /sys/block/zram0/stat', options=AsOption.STDOUT_NO_PRINT).split()
                readIO, _, _, readTicks, writeIO, _, _, writeTicks, _, _, _ = map(int, ret)
                to_write = f"{mono_time},{vct},{ratio},{reason},{readIO},{readTicks},{writeIO},{writeTicks},{pid}\n"
                # print("Write to zram_stat.csv: " + to_write, end="")
                zram_stat_f.write(to_write)
            except TypeError as e:
                self.logger.error("Type error, timespec is {}".format(timespec))
                raise(e)
                
    def record_zram_mm_stat_record(self):
        """This function fetches zram and memcg statistics once and appends them to a file."""
        import fcntl
        try:
            # Fetch zram_mm_stat data
            zram_mm_stat = parse_zram_mm_stat()
            if zram_mm_stat:
                # Convert the dictionary to a DataFrame
                df_new = pd.DataFrame([zram_mm_stat])

                # Determine the file mode
                file_exists = os.path.exists(self.zram_mm_stat_fn)
                file_mode = 'r+' if file_exists else 'w+'

                # Open the file with exclusive lock
                with open(self.zram_mm_stat_fn, file_mode) as zram_mm_stat_f:
                    # Acquire an exclusive lock
                    fcntl.flock(zram_mm_stat_f, fcntl.LOCK_EX)
                    try:
                        # Move the file pointer to the beginning
                        zram_mm_stat_f.seek(0)

                        # Read existing data if the file is not empty
                        try:
                            df_existing = pd.read_csv(zram_mm_stat_f)
                        except pd.errors.EmptyDataError:
                            df_existing = pd.DataFrame()

                        # Append new data
                        df_combined = pd.concat([df_existing, df_new], ignore_index=True)

                        # Move the file pointer to the beginning and truncate the file
                        zram_mm_stat_f.seek(0)
                        zram_mm_stat_f.truncate()

                        # Write the combined DataFrame back to the file
                        df_combined.to_csv(zram_mm_stat_f, index=False)

                        # Flush the file buffer
                        zram_mm_stat_f.flush()
                    finally:
                        # Release the lock
                        fcntl.flock(zram_mm_stat_f, fcntl.LOCK_UN)
        except Exception as e:
            print(f"An error occurred: {e}")
            raise e
    
    def record_zram_bd_stat(self):

        def parse_zram_bd_stat() -> Optional[dict]:

            try:
                content = As(f'cat /sys/block/zram0/bd_stat', [AsOption.STDOUT_NO_PRINT])
                bd_stat_pattern = (
                    r"(\d+)\s+"  # bd_count
                    r"(\d+)\s+"  # bd_reads
                    r"(\d+)\s+"  # bd_writes
                    r"(\d+)\s+"  # bd_read_ticks
                    r"(\d+)"     # bd_write_ticks
                    r"(?:\s+(\d+))?"  # optional bd_write_obj_size
                )
                bd_stat_match = re.search(bd_stat_pattern, content)
                
                result = OrderedDict()
                if bd_stat_match:
                    values = bd_stat_match.groups()
                    # 前面5个字段对应旧版本日志
                    keys = ['bd_count', 'bd_reads', 'bd_writes', 'bd_read_ticks', 'bd_write_ticks']
                    # 如果存在第6个字段则添加新字段
                    if len(values) == 6 and values[-1] is not None:
                        keys.append('bd_write_obj_size')

                    for k, v in zip(keys, map(int, filter(None, values))):
                        result[f"bd_stat_{k}"] = v
                    if 'bd_stat_bd_write_obj_size' not in result:
                        result['bd_stat_bd_write_obj_size'] = 0

                return result
            except Exception as e:
                print(f"An error occurred: {e}")
                # raise e
                return {'bd_count': 0, 'bd_reads': 0, 'bd_writes': 0, 'bd_read_ticks': 0, 'bd_write_ticks': 0, 'bd_write_obj_size': 0}

        self.zram_bd_stat_fn = f"{self.test_report_folder}/zram_bd_stat.csv"
        try:
            zram_bd_stat = parse_zram_bd_stat()
            if zram_bd_stat:
                df = pd.DataFrame([zram_bd_stat])
                with open(self.zram_bd_stat_fn, 'a') as zram_bd_stat_f:
                    if zram_bd_stat_f.tell() == 0:
                        file_is_empty = True
                    else:
                        file_is_empty = False
                    df.to_csv(zram_bd_stat_f, header=file_is_empty, index=False)

        except Exception as e:
            print(f"An error occurred: {e}")
            raise e