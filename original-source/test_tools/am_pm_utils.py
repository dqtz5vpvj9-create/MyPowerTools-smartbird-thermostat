import importlib, sys, os
from os.path import dirname, pardir
from pathlib import Path
import click
import yaml

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
from py_modules.logging_lib import setup_logging, LogLevel
logger = setup_logging()
from py_modules.check_interpreter import check_conda_interpreter, CONDA_ENV_NAME
if __name__ == '__main__':
    check_conda_interpreter(CONDA_ENV_NAME)
from py_modules.lib_aosp_base import *
from py_modules.lib_aosp_testing import *
assert LIB_AOSP_BASE_INITED

import subprocess

def get_third_party_packages():
    # Get the list of third-party packages using adb with a specific device
    result = As('pm list packages -3', AsOption.STDOUT_NO_PRINT)
    packages = [line.split(':')[1] for line in result.strip().split('\n')]
    return packages

def get_enabled_third_party_packages():
    # Get the list of third-party packages using adb with a specific device
    result = As('pm list packages -3 -e', AsOption.STDOUT_NO_PRINT)
    packages = [line.split(':')[1] for line in result.strip().split('\n')]
    return packages

def get_all_packages():
    # Get the list of third-party packages using adb with a specific device
    result = As('pm list packages', AsOption.STDOUT_NO_PRINT)
    packages = [line.split(':')[1] for line in result.strip().split('\n')]
    return packages

def get_package_uid(package_name):
    # Get package UID for a specific package using pm
    result = As(f'pm list packages -U {package_name}', AsOption.STDOUT_NO_PRINT).strip().split('\n')
    for line in result:
        if f"package:{package_name} " in line:
            uid = line.split('uid:')[1].strip()
            return int(uid)
    print(f"Package {package_name} not found")
    print(result)
    return None

def get_third_party_uids():
    # Get the list of third-party package UIDs using pm
    result = As('pm list packages -3 -U', AsOption.STDOUT_NO_PRINT)
    uids = {}
    for line in result.split('\n'):
        if 'package:' in line and 'uid:' in line:
            package = line.split('package:')[1].split('uid:')[0].strip()
            uid = line.split('uid:')[1].strip()
            uids[int(uid)] = package
    return uids

def get_all_uids() -> dict[int, str]:
    # Get the list of all package UIDs using pm
    result = As('pm list packages -U', AsOption.STDOUT_NO_PRINT)
    uids = {}
    for line in result.split('\n'):
        if 'package:' in line and 'uid:' in line:
            package = line.split('package:')[1].split('uid:')[0].strip()
            uid = line.split('uid:')[1].strip()
            uids[int(uid)] = package
    return uids

def disable_all_third_party_packages(exclude_package_or_activity_list):
    # Get the list of third-party packages using adb with a specific device
    third_party_packages = get_third_party_packages()
    for package in third_party_packages:
        if not any([(package in exclude) for exclude in exclude_package_or_activity_list]):
            As(f'pm disable {package}')
def disable_non_marvin_packages():
    MULTI_APP_BASE_LIST: list[str] = []
    for i in range(40):
        MULTI_APP_BASE_LIST.append(f"edu.washington.cs.nl35.memorywaster{i}")
    disable_all_third_party_packages([MULTI_APP_BASE_LIST])
def enable_all_marvin_packages():
    MULTI_APP_BASE_LIST: list[str] = []
    for i in range(40):
        MULTI_APP_BASE_LIST.append(f"edu.washington.cs.nl35.memorywaster{i}")
    for package in MULTI_APP_BASE_LIST:
        As(f'pm enable {package}')
    
def get_default_activity(pkg):
    act = As("cmd package resolve-activity --brief " + pkg, AsOption.STDOUT_NO_PRINT)
    for line in act.split('\n'):
        if pkg in line:
            return line
    return None

def enable_all_third_party_packages():
    # Get the list of third-party packages using adb with a specific device
    third_party_packages = get_third_party_packages()
    for package in third_party_packages:
        As(f'pm enable {package}')

test_dependencies = [
    'io.appium.settings',
    'io.appium.uiautomator2.server',
    'io.appium.uiautomator2.server.test',
    'com.android.vending',
    'com.rezvorck.tiktokplugin',
    'com.google.android.gms',
    'com.google.android.gsf',
    'app.revanced.android.gms',
]

# Packages that do not have a start activity even when enabled
special_packages = {
    'com.google.android.gsf',
    'io.appium.uiautomator2.server',
    'io.appium.uiautomator2.server.test',
}

def is_package_enabled(pkg, enabled_packages_set):
    if pkg in special_packages:
        # For special packages, check if they are in the enabled packages list
        return pkg in enabled_packages_set
    else:
        # For other packages, use 'cmd package resolve-activity --brief'
        act = As("cmd package resolve-activity --brief " + pkg, AsOption.STDOUT_NO_PRINT)
        for line in act.strip().split('\n'):
            if pkg in line:
                return True
        return False

def is_package_disabled(pkg, disabled_packages_set):
    if pkg in special_packages:
        # For special packages, check if they are in the disabled packages list
        return pkg in disabled_packages_set
    else:
        # For other packages, use 'cmd package resolve-activity --brief'
        act = As("cmd package resolve-activity --brief " + pkg, AsOption.STDOUT_NO_PRINT)
        return 'No activity found' in act

def ensure_third_party_apps(test_pkgs: list[str], logger: MyLogger = None, my_test_dependencies = None):
    """
    Ensure that among the third-party apps, only test_pkgs and test_dependencies are enabled.
    All other third-party apps are disabled.
    """
    if not logger:
        logger = setup_logging()
    # Get the list of third-party packages
    third_party_packages = get_third_party_packages()

    # Combine test_pkgs and test_dependencies into a set for quick lookup
    if not my_test_dependencies:
        my_test_dependencies = test_dependencies
    allowed_packages = set(test_pkgs + my_test_dependencies)

    packages_to_enable = []
    packages_to_disable = []

    # Determine which packages need to be enabled or disabled
    for package in third_party_packages:
        if package in allowed_packages:
            packages_to_enable.append(package)
        else:
            packages_to_disable.append(package)

    # Disable all packages that are not in the allowed list
    for package in packages_to_disable:
        logger.info(f"Disabling package: {package}")
        As(f'pm disable {package}')

    # Enable all allowed packages
    for package in packages_to_enable:
        logger.info(f"Enabling package: {package}")
        As(f'pm enable {package}')

    # Give the system time to process the enable and disable commands
    import time
    time.sleep(2)  # Adjust the sleep duration if necessary

    # Fetch the lists of enabled and disabled packages once
    enabled_packages_output = As('pm list packages -3 -e', AsOption.STDOUT_NO_PRINT)
    disabled_packages_output = As('pm list packages -3 -d', AsOption.STDOUT_NO_PRINT)

    enabled_packages = [line.split(':')[1] for line in enabled_packages_output.strip().split('\n') if line]
    disabled_packages = [line.split(':')[1] for line in disabled_packages_output.strip().split('\n') if line]

    enabled_packages_set = set(enabled_packages)
    disabled_packages_set = set(disabled_packages)

    # After all commands have been issued, verify the package states
    failed_to_disable = []
    for package in packages_to_disable:
        if is_package_enabled(package, enabled_packages_set):
            logger.error(f"Failed to disable package: {package}")
            failed_to_disable.append(package)

    failed_to_enable = []
    for package in packages_to_enable:
        if not is_package_enabled(package, enabled_packages_set) or is_package_disabled(package, disabled_packages_set):
            if not package == "com.android.vending":
                logger.error(f"Failed to enable package: {package}")
            failed_to_enable.append(package)

    # Optionally, you can retry enabling/disabling the failed packages
    # or handle them according to your requirements

def stop_third_party_apps(test_pkgs: list[str], logger: MyLogger = None, my_test_dependencies=None):
    """
    停止（杀死）所有第三方应用，除了在 test_pkgs 和 my_test_dependencies 中列出的应用。
    """
    if logger is None:
        logger = setup_logging()
    
    # 获取所有第三方包列表
    third_party_packages = get_enabled_third_party_packages()

    # 如果未提供 my_test_dependencies，则使用默认的 test_dependencies
    if my_test_dependencies is None:
        my_test_dependencies = test_dependencies

    # 允许的包集合：包括 test_pkgs 和 my_test_dependencies 中的包
    allowed_packages = set(test_pkgs + my_test_dependencies)

    packages_to_stop = []
    
    # 遍历所有第三方包，找出需要停止的包
    for package in third_party_packages:
        if package not in allowed_packages:
            packages_to_stop.append(package)
    
    # 对不在允许列表内的包，执行停止（杀死）操作

    logger.info(f"Stoping {len(packages_to_stop)} third-party apps")
    for package in packages_to_stop:
        As(f'am force-stop {package}', [AsOption.STDERR_TO_STDOUT, AsOption.STDOUT_NO_PRINT])

# def get_foreground_activities():
#     # Get the foreground activity using dumpsys
#     result = adb_shell('dumpsys activity activities', AsOption.STDOUT_NO_PRINT)
#     display_name = 0
#     act_name = ''
#     ret = {}
#     for lines in result.split('\n'):
#         if 'Display #' in lines:
#             display_name = int(lines.split('#')[1].split(' ')[0].strip())
#         elif 'mResumedActivity' in lines:
#             app_section = lines.split('{')[1]
#             for seg in app_section.split(' '):
#                 if '/' in seg:
#                     act_name = seg
#                     break
#             ret[display_name] = act_name
#     return ret
import re

def get_foreground_activities():
    # Get the foreground activity using dumpsys
    result = adb_shell('dumpsys activity activities', AsOption.STDOUT_NO_PRINT)
    display_to_activity = {}
    current_display = 0
    
    for line in result.split('\n'):
        # Extract display number
        if 'Display #' in line:
            try:
                current_display = int(line.split('#')[1].split(' ')[0].strip())
            except (IndexError, ValueError):
                pass
        
        # Android 11 format: mResumedActivity
        elif 'mResumedActivity' in line:
            try:
                app_section = line.split('{')[1]
                for seg in app_section.split(' '):
                    if '/' in seg:
                        display_to_activity[current_display] = seg
                        break
            except (IndexError, ValueError):
                pass
        
        # Android 13 format: topResumedActivity
        elif 'topResumedActivity' in line:
            try:
                activity_match = re.search(r'ActivityRecord\{[^ ]+ u\d+ ([^ }]+)', line)
                if activity_match:
                    display_to_activity[current_display] = activity_match.group(1)
            except Exception:
                pass
    
    return display_to_activity


# ACTIVITY MANAGER ACTIVITIES (dumpsys activity activities)
# Display #0 (activities from top to bottom):
#   Stack #391: type=standard mode=fullscreen
#   isSleeping=false
#   mBounds=Rect(0, 0 - 0, 0)
#     mResumedActivity: ActivityRecord{e320a5f u0 com.facebook.katana/.LoginActivity t391}  



def extract_display_info(display_info):
    results = []
    
    # 1) 修改这里，让正则既能处理 \n 也能处理 \r\n，
    #   并让它一直匹配到下一个大写字母开头的段落（例如 "DisplayModeDirector"）为止
    logical_displays_match = re.search(
        r'Logical Displays:(.*?)(?=\r?\n[A-Z])',  # 注意此处(?=\r?\n[A-Z])替换了原本的(?=\n\S)
        display_info,
        re.DOTALL
    )
    
    if not logical_displays_match:
        return results
    
    logical_displays_section = logical_displays_match.group(1)
    
    # 2) 拆分块时，也改为既能识别\n也能识别\r\n：
    display_blocks = re.split(r'\r?\n\s*Display \d+:', logical_displays_section)
    
    # 从 display_blocks[1:] 开始是为了跳过前面的“size=xxx”等头部信息
    for block in display_blocks[1:]:
        # 匹配 mDisplayId=xxx
        mDisplayId_match = re.search(r'mDisplayId=(\d+)', block)
        if not mDisplayId_match:
            continue
        
        displayId = mDisplayId_match.group(1)
        
        # 匹配 mBaseDisplayInfo=DisplayInfo{... removeMode 0}
        mBaseDisplayInfo_match = re.search(
            r'mBaseDisplayInfo=DisplayInfo\{(.*?)removeMode 0\}',
            block,
            re.DOTALL
        )
        if not mBaseDisplayInfo_match:
            continue
        
        displayInfo_str = mBaseDisplayInfo_match.group(1)
        
        # 提取 real 宽高
        real_match = re.search(r'real\s+(\d+)\s+x\s+(\d+)', displayInfo_str)
        if real_match:
            width = real_match.group(1)
            height = real_match.group(2)
        else:
            width = None
            height = None
        
        # 提取 density
        density_match = re.search(r'density\s+(\d+)', displayInfo_str)
        density = density_match.group(1) if density_match else None
        
        # 提取 rotation
        rotation_match = re.search(r'rotation\s+(\d+)', displayInfo_str)
        rotation = rotation_match.group(1) if rotation_match else None
        
        results.append({
            'displayId': int(displayId),
            'width': int(width) if width else None,
            'height': int(height) if height else None,
            'density': float(density) if density else None,
            'orientation': int(rotation) if rotation else None
        })
    
    return results

def get_last_displayId():
    dumpsys_display_result = As("dumpsys display", [AsOption.STDOUT_NO_PRINT, AsOption.STDERR_TO_STDOUT])
    displays = extract_display_info(dumpsys_display_result)
    if len(displays) == 0:
        return 0
    else:
        return max([display['displayId'] for display in displays])
