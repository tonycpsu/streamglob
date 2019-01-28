import logging
logger = logging.getLogger(__name__)
from memoize import *
from orderedattrdict import AttrDict

from . import providers
from . import config

class State(AttrDict):

    def __init__(self, *args, **kwargs):
        super().__init__(*kwargs, **kwargs)
        self.__exclude_keys__ |= {"_procs", "procs"}
        self._procs = list()

    @property
    def procs(self):
        return self._procs

    @procs.setter
    def procs(self, value):
        self._procs = value

    # def spawn_play_process(self, player, **kwargs):
        # raise Exception(kwargs)
        # self.procs.append(player.play(**kwargs))
        # raise Exception(self.procs)


state = State()
store = {}
memo = Memoizer(store)
memo.regions['short'] = {'max_age': 60}
memo.regions['long'] = {'max_age': 900}


# def set_provider(p, **kwargs):

#     global provider
#     provider = providers.get(p, **kwargs)
#     session = provider.session

__all__ = ["state", "memo"]
