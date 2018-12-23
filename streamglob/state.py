from memoize import *
from orderedattrdict import AttrDict

from . import providers
from . import config

class State(AttrDict):

    @property
    def session(self):
        return self._provider.session

    @property
    def provider(self):
        return self._provider

    @property
    def provider_config(self):
        return self._provider_config

    def set_provider(self, p, **kwargs):
        self._provider_config = config.settings.profile.providers.get(p)
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
