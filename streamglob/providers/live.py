from ..state import *

from .base import *
import abc

class LiveStreamMediaListing(MediaListing):
    pass

class ChannelsFilter(ConfigFilter):

    key = "channels"
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
        ("channel", ChannelsFilter)
    ])

    UPDATE_INTERVAL = 300

    TASKS = [
        ("update", UPDATE_INTERVAL)
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.live_channels = list()
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
            channel = {"width": 32},
            created = {"width": 19},
            title = {"width": ("weight", 1)},
        )

    @classproperty
    def CHANNEL_CLASS(cls):
        clsname = f"{cls.NAME}Channel"
        pkg = sys.modules.get(cls.__module__)
        return getattr(pkg, clsname, model.MediaSource)


    @property
    def channels(self):
        if isinstance(self.config.channels, dict):
            return self.config.channels
        else:
            return AttrDict([
                (f, f) for f in self.config.channels
            ])

    @db_session
    def create_channels(self):
        for name, locator in self.channels.items():
            feed = self.CHANNEL_CLASS.get(locator=locator)
            if not feed:
                feed = self.CHANNEL_CLASS(
                    provider_name = self.IDENTIFIER,
                    name = name,
                    locator=self.filters.channel[name]
                    # **self.feed_attrs(name)
                )
                commit()

    def listings(self, offset=None, limit=None, *args, **kwargs):

        return self.live_channels


    @db_session
    def update(self):
        self.create_channels()
        self.refresh()

    @db_session
    def refresh(self):
        if self.filters.channel.value:
            channels = [self.filters.channel.value]
        else:
            channels = self.channels

        self.live_channels = list()
        for locator in channels:
            channel = self.CHANNEL_CLASS.get(locator=locator)
            if not channel:
                raise Exception

            s = self.check_channel(locator)
            channel.updated = datetime.now()
            if s and s.channel not in [l.channel for l in self.live_channels]:
                listing_cls = getattr(
                    self, "LISTING_CLASS", LiveStreamMediaListing
                )
                self.live_channels.append(
                    listing_cls(
                        s
                    )
                )

        self.view.refresh()


    @abc.abstractmethod
    def check_channel(self, channel):
        """
        A method that's called for each defined channel locator to determine if
        it's live or not.  If so, the channel data is returned, if not, the return
        value should be None.
        """
        pass
