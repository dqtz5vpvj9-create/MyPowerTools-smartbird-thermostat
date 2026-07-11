#! /usr/bin/env python3
from pathlib import Path
import sys, os, argparse, random, time, subprocess, re
from os.path import dirname, pardir
def import_parents(level: int = 1) -> None:
    global __package__
    try:
        file = Path(__file__).resolve()
    except Exception:
        return
    parent, top = file.parent, file.parents[level]
    
    sys.path.append(str(top))

    __package__ = '.'.join(parent.parts[len(top.parts):])
    importlib.import_module(__package__) # won't be needed after that

from test_tools.am_pm_utils import get_package_uid

sys.path.append(dirname(__file__) + os.sep + pardir)
from py_modules.check_interpreter import check_conda_interpreter, CONDA_ENV_NAME
if __name__ == '__main__':
    check_conda_interpreter(CONDA_ENV_NAME)

from py_modules.lib_aosp_base import As, Aa, serial, AsOption
from py_modules.lib_aosp_testing import *

parser = argparse.ArgumentParser(description='Install APKs')
parser.add_argument('--apk', help='APK file to install', default=None)
parser.add_argument('--skip-install', action='store_true', help='Skip installing APKs')
args = parser.parse_args()
# apk_list = [
#     "今日头条_v1001_8990_811.apk",
#     "淘宝_v10.12.20.apk",
#     "知乎_8.37.0.apk",
#     "优酷-10.2.59.apk",
#     "拼多多_v6.31.0.apk",
#     "iQIYI_4.9.0.apk",
#     "美团-美好生活小帮手_12.12.203_Apkpure.apk",
#     "抖音_26.4.0.apk",
#     "YouTube_18.31.36_Apkpure.apk",
#     "Google-Maps_11.53.0004.apk",
#     "WeChat_8.0.40_Apkpure.apk",
#     "Starbucks_6.53_Apkpure.apk",
#     "LinkedIn_4.1.842.1.apk",
# ]

apks_dir = '/android2/test-apks'
apk_list = [ (apks_dir + '/' + apk) for apk in [
    "TikTok_with_Plugin/TikTokPlugin_v1.27.apk",
    "TikTok_with_Plugin/TikTok_v33.3.4.apk",
    "Google-Maps_11.53.0004.apk",
    # "AmazonShopping_v24.12.6.100.apk",
    # "64bit/meituan.apk",
    "iQIYI_4.9.0.apk",
    # "LinkedIn_4.1.842.1.apk",
    "64bit/X.apk",
    # "Starbucks_6.60_Apkpure.apk",
    # "64bit/Spotify.apk",
    "64bit/com.einnovation.temu.apk",
    "64bit/墨迹天气.apk",
    "64bit/应用宝.apk",
    # "64bit/百度网盘.apk",
    # "64bit/快手.apk",
    "64bit/QQ音乐.apk",
    "64bit/哈啰.apk",
    "64bit/QQ邮箱.apk",
    ]
]

# package_list = [
    # activity.split('/')[0] for activity in [
    #     "com.ss.android.article.news/.activity.MainActivity",
    #     "com.taobao.taobao/com.taobao.tao.TBMainActivity",
    #     "com.zhihu.android/.app.ui.activity.MainActivity",
    #     "com.youku.phone/com.youku.phone.ActivityWelcome",
    #     "com.xunmeng.pinduoduo/.ui.activity.HomeActivity",
    #     "com.iqiyi.i18n/org.qiyi.android.video.MainActivity",
    #     "com.sankuai.meituan/com.meituan.android.pt.homepage.activity.MainActivity",
    #     "com.ss.android.ugc.trill/com.ss.android.ugc.aweme.splash.SplashActivity",
    #     # "com.google.android.youtube/com.google.android.apps.youtube.app.application.Shell_HomeActivity",
    #     "com.google.android.apps.maps/com.google.android.maps.MapsActivity",
    #     # "com.tencent.mm/.ui.LauncherUI",
    #     "com.starbucks.mobilecard/.main.activity.LandingPageActivity",
    #     "com.linkedin.android/.authenticator.LaunchActivity"
    # ]
# ]

# package_list = [
#     activity.split('/')[0] for activity in [
#         "com.ss.android.ugc.trill/com.ss.android.ugc.aweme.splash.SplashActivity",
#         "com.zhihu.android/com.zhihu.android.app.ui.activity.LauncherActivity",
#         "com.youku.phone/com.youku.phone.ActivityWelcome",
#         "com.google.android.apps.maps/com.google.android.maps.MapsActivity",
#         "com.amazon.mShop.android.shopping/com.amazon.mShop.home.HomeActivity",
#     ]
# ]
    

def install_app(apk: str):
    Aa("install", os.path.realpath(apk))
    


def special_processing_temu(host_apk_path: str):
    pkg_name = "com.einnovation.temu"
    cmd = f"pm list packages -f | grep {pkg_name}"
    output = As(cmd).strip()
    if output:
        device_app_path = ''.join(output.split('=')[:-1]).split(':')[1]
        device_app_dir = os.path.dirname(device_app_path)
        host_apk_dir = os.path.dirname(host_apk_path)
        for f in os.listdir(host_apk_dir):
            host_file_path = os.path.join(host_apk_dir, f)
            Aa("push", host_file_path, f"{device_app_dir}/{f}")
    else:
        print(f"Package {pkg_name} not found.")

import os, sys

logger = setup_logging()
def main():
    # Check if the --apk argument is provided
    if args.apk:
        install_app(args.apk)
        pkg_name = get_apk_package_name(args.apk)
        print(pkg_name)
        uid = get_package_uid(pkg_name)
        print(uid)
    else:
        if not args.skip_install:
            for apk in apk_list:
                logger.info(f"Installing {apk}")
                install_app(apk)
                # if "com.einnovation.temu" in apk:
                #     special_processing_temu(apk)

if __name__ == '__main__':
    main()