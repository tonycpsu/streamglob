import abc
import re

from stevedore import extension
from orderedattrdict import AttrDict

from .. import session
from functools import wraps

# from . import base
# from . import mlb
# from .mlb import *

PROVIDERS = AttrDict()

def get(provider, *args, **kwargs):
    return PROVIDERS.get(provider)(*args, **kwargs)

def load():
    global PROVIDERS
    mgr = extension.ExtensionManager(
        namespace='streamglob.providers',
        # invoke_on_load=True,
        # invoke_args=(parsed_args.width,),
    )

    PROVIDERS = AttrDict((x.name, x.plugin) for x in mgr)


# @with_filters(DateFilter, FixedListingFilter)
# class TestProvider(base.SimpleProviderViewMixin, base.BaseProvider):

#     SESSION_CLASS = session.StreamSession
#     @property
#     def filters(self):
#         return AttrDict([
#             ("foo",  FixedListingFilter(["foo", "bar", "baz"]))
#         ])

#     def login(self):
#         print(self.session)

#     def listings(self):
#         return [ MediaItem(title=t) for t in ["a", "b" ,"c" ] ]

# PROVIDERS_RE = re.compile(r"(.+)Provider$")
# PROVIDERS = [ k.replace("Provider", "").lower()
#               for k in globals() if PROVIDERS_RE.search(k) ]
