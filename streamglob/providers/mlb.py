from .base import BaseProvider

from ..session import *

class MLBProvider(SimpleProviderViewMixin, base.BaseProvider):

    SESSION_CLASS = AuthenticatedStreamSession
    FILTERS = [
        FixedListingFilter(["foo", "bar", "baz"])
    ]

    def login(self):
        print(self.session)

    def listings(self):
        return [ MediaItem(title=t) for t in ["a", "b" ,"c" ] ]
