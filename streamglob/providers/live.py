from ..state import *

from .base import *

from dataclasses import *
import abc

@dataclass
class LiveStreamMediaListing(model.ContentMediaListing):

    channel: str = None

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
        self.filters["channel"].connect("changed", self.on_channel_change)
        self.live_channels = list()
        # self._update_alarm = None

    def on_channel_change(self, *args):
        self.refresh()

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

    def parse_identifier(self, identifier):
        if identifier:
            # print(self.view) # FIXME
            self.filters.channel.selected_label = identifier
        raise SGIncompleteIdentifier


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
                    provider_id = self.IDENTIFIER,
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

            listing = self.check_channel(locator)
            channel.updated = datetime.now()
            if listing and listing.channel not in [l.channel for l in self.live_channels]:
                self.live_channels.append(listing)

        self.view.refresh()


    @abc.abstractmethod
    def check_channel(self, channel):
        """
        A method that's called for each defined channel locator to determine if
        it's live or not.  If so, the channel data is returned, if not, the return
        value should be None.
        """
        pass
