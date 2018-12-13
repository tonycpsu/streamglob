import abc
import re

from orderedattrdict import AttrDict

from .. import session
from functools import wraps

from . import base
# from . import mlb
from .mlb import *

def get(provider, *args, **kwargs):
    provider_class = next( v for k, v in globals().items()
                           if k.lower() == f"{provider}Provider".lower())
    return provider_class(*args, **kwargs)

# @with_filters(DateFilter, FixedListingFilter)
class TestProvider(base.SimpleProviderViewMixin, base.BaseProvider):

    SESSION_CLASS = session.StreamSession
    @property
    def filters(self):
        return AttrDict([
            ("foo",  FixedListingFilter(["foo", "bar", "baz"]))
        ])

    def login(self):
        print(self.session)

    def listings(self):
        return [ MediaItem(title=t) for t in ["a", "b" ,"c" ] ]

PROVIDERS_RE = re.compile(r"(.+)Provider$")
PROVIDERS = [ k.replace("Provider", "").lower()
              for k in globals() if PROVIDERS_RE.search(k) ]
