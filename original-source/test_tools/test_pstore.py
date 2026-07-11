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
from py_modules.logging_lib import setup_logging
logger = setup_logging()
from py_modules.check_interpreter import check_conda_interpreter, CONDA_ENV_NAME
if __name__ == '__main__':
    check_conda_interpreter(CONDA_ENV_NAME)
from py_modules.lib_aosp_base import *
from py_modules.lib_aosp_testing import *
assert LIB_AOSP_BASE_INITED
Aa("remount")
Aa("push", str((Path(aosp_host_working_dir) / "selftests_install.tar").absolute()), "/system/")
As("tar xf /system/selftests_install.tar -C /system/")
try:
    logger.debug("Running pstore tests")
    As("cd /system/selftests_install/pstore && /system/selftests_install/pstore/pstore_tests")
except subprocess.CalledProcessError as e:
    print(e.output.decode("utf-8"))

try:
    Aa("wait-for-device")
    Aa("remount")
    logger.debug("Running pstore crash test")
    As("cd /system/selftests_install/pstore && /system/selftests_install/pstore/pstore_crash_test")
    Aa("wait-for-device")
    Aa("remount")
    logger.debug("Running pstore post crash test")
    Aa("wait-for-device")
    Aa("remount")
    As("cd /system/selftests_install/pstore && /system/selftests_install/pstore/pstore_post_reboot_tests")
except subprocess.CalledProcessError as e:
    print(e.output.decode("utf-8"))