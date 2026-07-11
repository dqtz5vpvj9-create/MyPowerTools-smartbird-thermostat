#! /usr/bin/env python3
import sys, os, argparse, random, time, subprocess, re
from os.path import dirname, pardir
from io import TextIOWrapper
import subprocess as sp
from typing import Optional, List, Tuple
sys.path.append(dirname(__file__) + os.sep + pardir)
print(sys.path)
from py_modules.check_interpreter import check_conda_interpreter, CONDA_ENV_NAME
if __name__ == '__main__':
    check_conda_interpreter(CONDA_ENV_NAME)

from py_modules.lib_aosp_base import As, Aa, ASRCDIR, AsOption, serial, aosp_host_working_dir

__full_app_activities = [
    "com.xunmeng.pinduoduo/.ui.activity.HomeActivity",
    "com.taobao.idlefish/.maincontainer.activity.MainActivity",
    "com.taobao.taobao/com.taobao.tao.TBMainActivity",
    "com.ss.android.article.news/.activity.MainActivity",
    "com.amazon.mShop.android.shopping/com.amazon.mShop.navigation.MainActivity",
    "com.mi.global.shop/com.mi.global.home.ui.MainTabActivity",
    "com.bilibili.app.in/tv.danmaku.bili.MainActivityV2",
    "com.alibaba.aliexpresshd/.home.ui.MainActivity",
    "com.youku.phone/com.youku.v2.HomePageEntry",
    "com.zhihu.android/.app.ui.activity.MainActivity",
]

__intensive_app_activities = [
    "com.ss.android.article.news/.activity.MainActivity",
    "com.taobao.taobao/com.taobao.tao.TBMainActivity",
    "com.zhihu.android/.app.ui.activity.MainActivity",
    "com.youku.phone/com.youku.v2.HomePageEntry",
    "com.xunmeng.pinduoduo/.ui.activity.HomeActivity",
    "com.iqiyi.i18n/org.qiyi.android.video.MainActivity",
    "com.sankuai.meituan/com.meituan.android.pt.homepage.activity.MainActivity",
    "com.ss.android.ugc.aweme/.main.MainActivity",
    # "com.alibaba.aliexpresshd/.home.ui.MainActivity",
    # "com.taobao.idlefish/.maincontainer.activity.MainActivity",
    # "tv.danmaku.bili/.MainActivityV2",
    "com.google.android.youtube/com.google.android.apps.youtube.app.application.Shell_HomeActivity",
    "com.google.android.apps.maps/com.google.android.maps.MapsActivity",
    "com.tencent.mm/.ui.LauncherUI",
    "com.starbucks.mobilecard/.main.activity.LandingPageActivity",
    "com.linkedin.android/.authenticator.LaunchActivity"
]

__intensive_app_packages = [
    activity.split('/')[0] for activity in __intensive_app_activities
]

def get_foreground_app(candidates: List[str] = []) -> str:
    dumpsys_output = subprocess.check_output(
        ["adb", "-s", serial, "shell", "/system/bin/sh", "-c", "'dumpsys window windows | grep mSurface=Surface'"], 
        text=True)
    # print(dumpsys_output)
    # 使用正则表达式匹配包名和活动名
    pattern = "mSurface=Surface\(name=(.*?)/"
    print(re.findall(pattern, dumpsys_output))
    matches = [ app for app in re.findall(pattern, dumpsys_output) if app in candidates ]

    return matches[0] if len(matches) > 0 else None

def __get_timecnt() -> Tuple[int, int]:
    try:
        res = As('gettimecnt', [AsOption.STDOUT_NO_PRINT, AsOption.STDERR_TO_STDOUT]).split(',')
        return int(res[0]), int(res[1])
    except:
        return 0

def __prepare_logcat():
    As("dmesg -C", [AsOption.STDOUT_NO_PRINT, AsOption.STDERR_TO_STDOUT])
    As("logcat -c", [AsOption.STDOUT_NO_PRINT, AsOption.STDERR_TO_STDOUT])
    As("logcat -G 150M", [AsOption.STDOUT_NO_PRINT, AsOption.STDERR_TO_STDOUT])
    As("logcat -g", [AsOption.STDOUT_NO_PRINT, AsOption.STDERR_TO_STDOUT])

def am_start_app(activity: str) -> Tuple[str, str, int, int]:
    brief = As(f"am start -W -n {activity}", [AsOption.STDOUT_NO_PRINT, AsOption.STDERR_TO_STDOUT]).splitlines()

    launch_state = 'UNKNOWN'
    started_activity = 'UNKNOWN'
    total_time = 0
    wait_time = 0
    
    for line in brief:
        if 'LaunchState' in line:
            launch_state = (line.split(': ')[1]).split(' ')[0]
        elif 'Activity' in line:
            started_activity = line.split(': ')[1]
        elif 'TotalTime' in line:
            total_time = int(line.split(': ')[1])
        elif 'WaitTime' in line:
            wait_time = int(line.split(': ')[1])
    
    return launch_state, started_activity, total_time, wait_time

def am_stop_app(package: str) -> str:
    return As(f"am force-stop {package}", [AsOption.STDOUT_NO_PRINT, AsOption.STDERR_TO_STDOUT])

def random_switch_scroll(activities: list, swipe_times: int, frgd_file: TextIOWrapper):
    # stop all apps to ensure no cold start
    for activity in activities:
        am_stop_app(activity.split('/')[0])

    test_list = activities.copy()
    random.shuffle(test_list)
    for activity in test_list:
        launch_state, started_activity, total_time, wait_time = am_start_app(activity)
        frgd_file.write(f"{started_activity} {launch_state} {total_time} {wait_time} {__get_timecnt()[1]}\n")
        if 'HOT' not in launch_state:
            # sleep 5 seconds to wait for app to COLD start
            time.sleep(3)
        else:
            print("[warning] unexpected hot start")
        for _ in range(swipe_times):
            swipe_down()

def swipe_up():
    As("input swipe 300 300 500 1000")

def swipe_down():
    As("input swipe 500 1000 300 300")

def do_switch_scroll_test(activities: list, repeat: int, swipe_times: int, frgd_file: TextIOWrapper):

    for idx in range(repeat):
        start = datetime_class.now()
        random_switch_scroll(activities, swipe_times, frgd_file)
        print(f"test {idx} takes {datetime_class.now() - start}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-r', '--repeat', type=int, default=3)
    parser.add_argument('--swipe_times', type=int, default=3)
    args = parser.parse_args()

    output_dir = f"{aosp_host_working_dir}/switch_and_scroll/{datetime_class.now().strftime('%Y-%m-%d-%H-%M-%S')}"
    os.mkdir(output_dir)

    # prepare get timecnt
    Aa("remount")
    # Aa("push", ASRCDIR + "/sunfish_out/target/product/sunfish/system/bin/gettimecnt", "/system/bin/")

    with open(f"{output_dir}/app_list.log", 'w') as app_list_f:
        app_list_f.write('\n'.join(__full_app_activities))

    with open(f"{output_dir}/test_setup.log", 'w') as setup_f:
        setup_f.write(f"repeat: {args.repeat}\n")
        setup_f.write(f"swipe_times: {args.swipe_times}\n")
        memcg_size = As("cat /dev/memcg/all_zygote/memory.limit_in_bytes", [AsOption.STDOUT_NO_PRINT, AsOption.STDERR_TO_STDOUT])
        setup_f.write(f"memcg_size: {memcg_size}\n")

    with open(f"{output_dir}/test_time_record", 'w') as time_f:
        # datetime time_millis timecnt
        time_f.write(f"start: {datetime_class.now().strftime('%m%d_%H%M_%S')} {__get_timecnt()[0]} {__get_timecnt()[1]}\n")

    with open(f"{output_dir}/zram_stat.log", 'w') as zram_f:
        ret = As('cat /sys/block/zram0/stat', options=AsOption.STDOUT_NO_PRINT).split()
        readIO, _, _, readTicks, writeIO, _, _, writeTicks, _, _, _ = map(int, ret)
        zram_f.write(f"{readIO} {readTicks} {writeIO} {writeTicks}\n")

    with open(f"{output_dir}/swap.log", 'w') as swap_f, \
        open(f"{output_dir}/gc.log", 'w') as gc_f, \
        open(f"{output_dir}/mem-ftpr.log", 'w') as ftpr_f, \
        open(f"{output_dir}/frgd.log", 'w') as frgd_f:

        __prepare_logcat()
        
        swap_p = sp.Popen(f"adb -s {serial} shell dmesg -w | grep YZY", stdout=swap_f, shell=True)
        gc_p = sp.Popen(f"adb -s {serial} logcat | grep 'FreedLargeObjectsCount'", stdout=gc_f, shell=True)
        ftpr_p = sp.Popen(f"adb -s {serial} logcat | grep '\[mem-ftpr\]'", stdout=ftpr_f, shell=True)

        try:
            do_switch_scroll_test(__intensive_app_activities, args.repeat, args.swipe_times, frgd_f)
        except Exception as e:
            print(f'Unexpected error: {e}')
            print(f'Terminating...')
        finally:
            swap_p.kill()
            gc_p.kill()
            ftpr_p.kill()
            os.system(f'kill -9 {swap_p.pid}')
            os.system(f'kill -9 {gc_p.pid}')
            os.system(f'kill -9 {ftpr_p.pid}')

    with open(f"{output_dir}/zram_stat.log", 'a') as zram_f:
        ret = As('cat /sys/block/zram0/stat', options=AsOption.STDOUT_NO_PRINT).split()
        readIO, _, _, readTicks, writeIO, _, _, writeTicks, _, _, _ = map(int, ret)
        zram_f.write(f"{readIO} {readTicks} {writeIO} {writeTicks}\n")

    with open(f"{output_dir}/test_time_record", 'a') as time_f:
        # datetime time_millis timecnt
        time_f.write(f"end: {datetime_class.now().strftime('%m%d_%H%M_%S')} {__get_timecnt()[0]} {__get_timecnt()[1]}\n")

if __name__ == '__main__':
    main()


