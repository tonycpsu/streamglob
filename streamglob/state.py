import logging
logger = logging.getLogger(__name__)
from memoize import *
from orderedattrdict import AttrDict

from . import providers
from . import config

class State(AttrDict):

    def __init__(self, *args, **kwargs):
        super().__init__(*kwargs, **kwargs)
        self.__exclude_keys__ |= {
            "_procs", "procs",# "task_manager", "task_manager_task"
        }
        self._store = {}
        self._memo = Memoizer(self._store)
        self._memo.regions['short'] = {'max_age': 60}
        self._memo.regions['medium'] = {'max_age': 60*5}
        self._memo.regions['long'] = {'max_age': 60*60}
        # self.task_manager = TaskManager()

    @property
    def procs(self):
        return self._procs

    @procs.setter
    def procs(self, value):
        self._procs = value

    def start_task_manager(self):
        self.task_manager_task = self.asyncio_loop.create_task(self.task_manager.start())

    def stop_task_manager(self):
        self.asyncio_loop.create_task(self.task_manager.stop())

    @property
    def memo(self):
        return self._memo


state = State()

def memo(*args, **kwargs):
    return state.memo(*args, **kwargs)

# def set_provider(p, **kwargs):

#     global provider
#     provider = providers.get(p, **kwargs)
#     session = provider.session

__all__ = ["state", "memo"]
