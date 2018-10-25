from ..session import *

from . import base
from . import bam
from .filters import *

class MLBProvider(base.SimpleProviderViewMixin,
                  bam.BAMProviderMixin,
                  base.BaseProvider):

    SESSION_CLASS = AuthenticatedStreamSession
    FILTERS = [
        FixedListingFilter(["foo", "bar", "baz"])
    ]

    def login(self):
        print(self.session)

    def listings(self):
        return [ base.MediaItem(title=t) for t in ["a", "b" ,"c" ] ]
