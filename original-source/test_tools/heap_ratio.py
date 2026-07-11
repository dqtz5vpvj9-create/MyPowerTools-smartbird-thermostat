#! /usr/bin/env python3

import re
import subprocess
import time

serial = "px4:45555"

def get_foreground_app():
    dumpsys_output = subprocess.check_output(
        ["adb", "-s", serial, "shell", "/system/bin/sh", "-c", "'dumpsys window windows | grep mSurface=Surface'"], 
        text=True)
    # print(dumpsys_output)
    # 使用正则表达式匹配包名和活动名
    pattern = "mSurface=Surface\(name=(.*?)/"

    matches = re.findall(pattern, dumpsys_output)

    # 过滤掉不想要的包名
    matches = [app for app in matches if 'com.miui' not in app and 'GestureStub' not in app]
    
    # 获取最后一个匹配项，这通常是前台活动的app包名
    if matches:
        foreground_app = matches[-2]
        return foreground_app

    return None

import re

def extract_heap_info(text):
    # 使用正则表达式匹配 Native Heap 和 Dalvik Heap 的行
    native_heap_line = re.search(r"Native Heap\s+(\d+)", text)
    dalvik_heap_line = re.search(r"Dalvik Heap\s+(\d+)", text)
    total_line = re.search(r"TOTAL\s+(\d+)", text)

    # 如果没有找到匹配的行，返回 None
    if not native_heap_line or not dalvik_heap_line:
        return None

    # 从匹配的行中提取 PssTotal 的值
    native_heap_pss_total = int(native_heap_line.group(1))
    dalvik_heap_pss_total = int(dalvik_heap_line.group(1))
    total = int(total_line.group(1))

    # 计算比值
    ratio = native_heap_pss_total / dalvik_heap_pss_total

    return native_heap_pss_total//1000, dalvik_heap_pss_total//1000, ratio, total//1000
print("Warning, you are using dumpsys meminfo -d, which will cause an explicit GC. Do NOT use this in production code!")
while True:
    forg_name = get_foreground_app()
    if forg_name and len(forg_name) > 3 and not forg_name.endswith(')'):
        try:
            meminfo = subprocess.check_output(
                ["adb", "-s", serial, "shell", "/system/bin/sh", "-c", "'dumpsys meminfo -d {}'".format(forg_name)], 
                text=True)
            print(forg_name, extract_heap_info(meminfo))
        except Exception as e:
            print(e)
    time.sleep(5)
    