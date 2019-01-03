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

    def spawn_play_process(self, player, **kwargs):
        # raise Exception(kwargs)
        self.procs.append(player.play(**kwargs))
        # raise Exception(self.procs)

    @property
    def session(self):
        return self._provider.session

    @property
    def provider(self):
        return self._provider

    @property
    def provider_config(self):
        # return self._provider_config
        return self.provider.config

    def set_provider(self, p):
        # self._provider_config = config.settings.profile.providers.get(p)
        self._provider = providers.get(p)



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
