import abc

from ..session import *

class BaseProvider(abc.ABC):

    SESSION_CLASS = StreamSession
    FILTERS = []
    ATTRIBUTES = ["title"]

    def __init__(self, *args, **kwargs):
        self.session = self.SESSION_CLASS(*args, **kwargs)
        # self.filters = [ f() for f in self.FILTERS ]

    @abc.abstractmethod
    def login(self):
        pass

    @abc.abstractmethod
    def listings(self, filters=None):
        pass

    @abc.abstractmethod
    def make_view(self):
        pass
