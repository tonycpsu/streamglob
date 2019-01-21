from ..state import *

from .base import *
import abc

class StreamsFilter(ConfigFilter):

    key = "streams"
    with_all = True


class LiveStreamProviderDataTable(ProviderDataTable):

    def keypress(self, size, key):

        if key == "meta r":
            self.provider.update()
            self.reset()
        else:
            return super().keypress(size, key)
        return key


class LiveStreamProviderView(SimpleProviderView):

    PROVIDER_DATA_TABLE_CLASS = LiveStreamProviderDataTable


@with_view(LiveStreamProviderView)
class LiveStreamProvider(BackgroundTasksMixin, BaseProvider):

    FILTERS = AttrDict([
        ("streams", StreamsFilter)
    ])

    TASKS = [
        ("update", 15)
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.live_streams = list()
        # self._update_alarm = None

    # def on_activate(self):
    #     self.update()

    #     def update(loop, user_data): self.update()

    #     if not self._update_alarm:
    #         self._update_alarm = state.loop.set_alarm_in(
    #             self.REFRESH_INTERVAL, update
    #         )

    # def on_deactivate(self):
    #     if self._update_alarm:
    #         state.loop.remove_alarm(self._update_alarm)
    #     self._update_alarm = None


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

        return self.live_streams

    @db_session
    def update(self):

        if self.filters.streams.value:
            streams = [self.filters.streams.value]
        else:
            streams = self.streams

        for locator in streams:
            s = self.check_stream(locator)
            if s and s.stream not in [l.stream for l in self.live_streams]:
                self.live_streams.append(
                    MediaItem(
                        s
                    )
                )

        self.view.table.refresh()


    @abc.abstractmethod
    def check_stream(self, stream):
        """
        A method that's called for each defined stream locator to determine if
        it's live or not.  If so, the stream data is returned, if not, the return
        value should be None.
        """
        pass
