#! /usr/bin/env python3
import sys, os, argparse
from os.path import dirname, pardir
sys.path.append(dirname(__file__) + os.sep + pardir)
print(sys.path)
from py_modules.check_interpreter import check_conda_interpreter, CONDA_ENV_NAME
if __name__ == '__main__':
    check_conda_interpreter(CONDA_ENV_NAME)

from py_modules.lib_aosp_base import As


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--memcg_size', type=str, default="128M")
    parser.add_argument('--memcg_name', type=str, default="all_zygote")
    parser.add_argument('--add_zygote', action='store_true', default=False)
    args = parser.parse_args()

    As(f"mkdir -p /dev/memcg/{args.memcg_name}/")
    As(f"echo {args.memcg_size} > /dev/memcg/{args.memcg_name}/memory.limit_in_bytes")

    if args.add_zygote:
        zygote64_pid = As("ps -A | grep zygote64").split()[1]
        As(f"echo {zygote64_pid} > /dev/memcg/{args.memcg_name}/tasks")
        print("set zygote memcg done!")
    else:
        # change permission, so that ART can set memcg
        As(f'chmod 0777 /dev/memcg/{args.memcg_name}/tasks')
        print("set memcg permission done!")

if __name__ == '__main__':
    main()

    