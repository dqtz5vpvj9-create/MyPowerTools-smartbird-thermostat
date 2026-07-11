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

from py_modules.tmux_lib import PaneOperation, SessionOperation
import libtmux
from libtmux.server import Server
from libtmux.session import Session, Window, Pane
from pathlib import Path
from tmuxp.cli import cli as tmuxpcli
import os

class TmuxSessionManager:
    def __init__(self, logger: MyLogger, session_suffix = "_art_ci"):
        self.session_suffix = session_suffix
        self.androidtools = androidtools
        self.ASRCDIR = ASRCDIR
        self.logger = logger
        self.server = libtmux.Server()
        self.session = None
        self.session_operation = None

    def connect_or_create_session(self, tmux_yaml_file = "./test_tools/ci_tmux.yaml"):
        found = False
        for session in self.server.sessions:
            if session and session.session_name:
                if session.session_name.endswith(self.session_suffix):
                    self.session = session
                    found = True
                    break
        if not found:
            # Get time string as new tmux session name
            session_name = datetime_class.now().strftime("%m%d_%H%M_%S") + self.session_suffix

            tmux_file = Path(os.path.join(self.androidtools, tmux_yaml_file)).resolve().as_posix()
            tmuxpcli(["load", "-s", session_name, tmux_file, "-d"])
            
            _session = self.server.sessions.get(session_name=session_name)
            if _session is None:
                raise Exception("Session not found")
            else:
                self.session = _session
                
        self.session_operation = SessionOperation(self.session, self.logger)
        assert isinstance(self.session, libtmux.session.Session)