#!/usr/bin/env python3
import argparse
import subprocess

def show_memcg(device_selection):
    devices = {
        "1": "px1:15555",
        "2": "px2:25555",
        "3": "px3:35555",
        "4": "px4:45555",
        "e": "emulator-5554"
    }
    
    selected_devices = ["px1:15555", "px2:25555", "px3:35555", "px4:45555", "emulator-5554"]
    
    if device_selection in devices:
        selected_devices = [devices[device_selection]]
    
    for serial in selected_devices:
        print(f"Device: {serial}")
        print("------------------------")
        
        try:
            mem_limit_bytes = int(subprocess.getoutput(f"adb -s {serial} shell cat /dev/memcg/test_art/memory.limit_in_bytes").strip())
            mem_limit_mb = mem_limit_bytes / (1024 * 1024)
            print(f"Memory Limit: {mem_limit_mb:.2f} MB")

            tasks_output = subprocess.getoutput(f"adb -s {serial} shell cat /dev/memcg/test_art/tasks")
            processed_tids = set()

            for pid in tasks_output.splitlines():
                if pid:
                    if args.pid_only and int(pid) in processed_tids:
                        continue
                    try:
                        tids = subprocess.getoutput(f"adb -s {serial} shell ls /proc/{pid}/task").splitlines()
                        tids_int = [int(tid) for tid in tids if tid]
                        for tid in tids_int:
                            processed_tids.add(tid)
                        group_leader_tid = min(tids_int)
                        if args.pid_only:
                            proc_name = subprocess.getoutput(f"adb -s {serial} shell cat /proc/{group_leader_tid}/comm").strip()
                        else:
                            proc_name = subprocess.getoutput(f"adb -s {serial} shell cat /proc/{pid}/comm").strip()
                        print(f"{pid} (Group Leader: {group_leader_tid}) - {proc_name}")
                    except Exception as e:
                        continue
        except Exception as e:
            print(f"Error accessing device {serial}: {e}")

        print()  # New line for better separation between devices

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Show memory cgroup information for specified devices.")
    parser.add_argument('selection', nargs='?', help='Specify a device number (1-4) or "e" for the emulator', default=None)
    parser.add_argument('-p', '--pid_only', action='store_true', help='Only show the process id of the group leader')
    args = parser.parse_args()

    if args.selection and args.selection not in ['1', '2', '3', '4', 'e']:
        parser.error("Invalid argument. Please provide 1, 2, 3, 4, or e.")
    else:
        show_memcg(args.selection)
