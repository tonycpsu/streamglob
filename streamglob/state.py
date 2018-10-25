from memoize import *
from . import providers

provider = None
session = None
store = {}
memo = Memoizer(store)
memo.regions['short'] = {'max_age': 60}
memo.regions['long'] = {'max_age': 900}

def set_provider(p):

    global provider
    provider = providers.get(p)
