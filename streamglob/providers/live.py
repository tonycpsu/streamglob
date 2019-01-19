from .base import *
import abc

class StreamsFilter(ConfigFilter):

    key = "streams"
    with_all = True

class LiveStreamProvider(BaseProvider):

    FILTERS = AttrDict([
        ("streams", StreamsFilter)
    ])

    @property
    def ATTRIBUTES(self):
        return AttrDict(
            stream = {"width": 32},
            created = {"width": 19},
            description = {"width": ("weight", 1)},
        )

    @property
    def streams(self):
        if isinstance(self.config.streams, dict):
            return self.config.streams
        else:
            return AttrDict([
                (f, f) for f in self.config.streams
            ])

    def listings(self, offset=None, limit=None, *args, **kwargs):

        count = 0

        if not offset:
            offset = 0
        if not limit:
            limit = self.limit

        if self.filters.streams.value:
            streams = [self.filters.streams.value]
        else:
            streams = self.streams

        for stream in filter(
                lambda x: x is not None,
                [ self.check_stream(x) for x in streams ]
        ):
            yield MediaItem(
                stream
            )

    @abc.abstractmethod
    def check_stream(self, stream):
        """
        A method that's called for each defined stream locator to determine if
        it's live or not.  If so, the stream data is returned, if not, the return
        value should be None.
        """
        pass
