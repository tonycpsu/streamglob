from memoize import *
from orderedattrdict import AttrDict

from . import providers

class State(AttrDict):

    @property
    def session(self):
        return None
        # return self._provider.session

    @property
    def provider(self):
        return self._provider

    def set_provider(self, p, **kwargs):
        self._provider = providers.get(p, **kwargs)

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
