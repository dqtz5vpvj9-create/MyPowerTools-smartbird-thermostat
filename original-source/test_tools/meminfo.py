#! /usr/bin/env python3

import sys, os, argparse, random, time, subprocess, re
from os.path import dirname, pardir

sys.path.append(dirname(__file__) + os.sep + pardir)
from py_modules.check_interpreter import check_conda_interpreter, CONDA_ENV_NAME
if __name__ == '__main__':
    check_conda_interpreter(CONDA_ENV_NAME)

from py_modules.lib_aosp_base import As, Aa, ASRCDIR, AsOption, serial, aosp_host_working_dir

def parse_meminfo(pid):
    # text = As(f'dumpsys meminfo -d {pid}', [AsOption.STDOUT_NO_PRINT])
    text = None
    native_heap_line = re.search(r"Native Heap\s+(\d+)\s+\d+\s+\d+\s+\d+\s+(\d+)", text)
    dalvik_heap_line = re.search(r"Dalvik Heap\s+(\d+)\s+\d+\s+\d+\s+\d+\s+(\d+)", text)
    dalvik_other_line = re.search(r"Dalvik Other\s+(\d+)\s+\d+\s+\d+\s+\d+\s+(\d+)", text)
    total_line = re.search(r"TOTAL\s+(\d+)\s+\d+\s+\d+\s+\d+\s+(\d+)", text)

    # 如果没有找到匹配的行，返回 None
    if not native_heap_line or not dalvik_heap_line or not dalvik_other_line:
        return None

    # 从匹配的行中提取 PssTotal 的值
    native_heap_pss_total = int(native_heap_line.group(1))
    dalvik_heap_pss_total = int(dalvik_heap_line.group(1))
    dalvik_other_pss_total = int(dalvik_other_line.group(1))
    dalvik_pss = dalvik_heap_pss_total + dalvik_other_pss_total
    total_pss = int(total_line.group(1))

    native_heap_rss_total = int(native_heap_line.group(2))
    dalvik_heap_rss_total = int(dalvik_heap_line.group(2))
    dalvik_other_rss_total = int(dalvik_other_line.group(2))
    dalvik_rss = dalvik_heap_rss_total + dalvik_other_rss_total
    total_rss = int(total_line.group(2))
    # 计算比值
    heap_pss_ratio = native_heap_pss_total / dalvik_pss
    heap_rss_ratio = native_heap_rss_total / dalvik_rss
    pss_ratio = native_heap_pss_total / total_pss
    rss_ratio = native_heap_rss_total / total_rss

    # return native_heap_pss_total, dalvik_pss, total_pss, native_heap_rss_total, dalvik_rss, total_rss
    return native_heap_pss_total, dalvik_pss, total_pss, native_heap_rss_total, dalvik_rss, total_rss

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

def get_total_meminfo(package_list: list):

    native_heap_pss = 0
    dalvik_pss = 0
    total_pss = 0
    native_heap_rss = 0
    dalvik_rss = 0
    total_rss = 0
    app_count = 0

    for package in package_list:
        try:
            meminfo = parse_meminfo(package)
            # print(meminfo)
            if meminfo:
                native_heap_pss += meminfo[0]
                dalvik_pss += meminfo[1]
                total_pss += meminfo[2]
                native_heap_rss += meminfo[3]
                dalvik_rss += meminfo[4]
                total_rss += meminfo[5]
                app_count += 1
        except Exception as e:
            continue

    return app_count, native_heap_pss, dalvik_pss, total_pss, native_heap_rss, dalvik_rss, total_rss

def main():
    while True:
        print(get_total_meminfo(__intensive_app_packages))
        time.sleep(1)

if __name__ == '__main__':
    main()
