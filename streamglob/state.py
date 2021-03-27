import logging
logger = logging.getLogger(__name__)
from memoize import *
from orderedattrdict import AttrDict, Tree
import asyncio

from pony.orm import *

from . import config
from . import model

class AppData(Tree):
    def __init__(self, *args, **kwargs):
        with db_session:
            saved = model.ApplicationData.select().first() or model.ApplicationData()
        super().__init__(**saved.to_dict()["settings"])
        self.__exclude_keys__ |= {"save"}

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        self.save()

    def save(self):
        with db_session:
            saved = model.ApplicationData.select().first() or model.ApplicationData()
            saved.settings = self
            commit()

class State(AttrDict):

    def __init__(self, *args, **kwargs):
        super().__init__(*kwargs, **kwargs)
        self.__exclude_keys__ |= {
            "_procs", "procs", #"task_manager", "task_manager_task"
        }
        self._app_data = None
        self._store = {}
        self._memo = Memoizer(self._store)
        self._memo.regions['short'] = {'max_age': 60}
        self._memo.regions['medium'] = {'max_age': 60*5}
        self._memo.regions['long'] = {'max_age': 60*60}
        # self.task_manager = TaskManager()


    @property
    def app_data(self):
        if not self._app_data:
            self._app_data = AppData()
        return self._app_data

    @property
    def event_loop(self):
        try:
            return asyncio.get_event_loop()
        except RuntimeError:
            return asyncio.new_event_loop()

    @property
    def procs(self):
        return self._procs

    @procs.setter
    def procs(self, value):
        self._procs = value

    def start_task_manager(self):
        self.task_manager_task = self.event_loop.create_task(self.task_manager.start())

    def stop_task_manager(self):
        self.event_loop.create_task(self.task_manager.stop())

    @property
    def memo(self):
        return self._memo


state = State()

def memo(*args, **kwargs):
    return state.memo(*args, **kwargs)

__all__ = ["state", "memo"]
