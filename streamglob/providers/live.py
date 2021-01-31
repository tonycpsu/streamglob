from ..state import *

from .base import *

from dataclasses import *
import abc

@model.attrclass()
class LiveStreamMediaListing(model.ChannelMediaListing, model.TitledMediaListing):
    pass

class ChannelsFilter(ConfigFilter):

    key = "channels"
    with_all = True


class LiveStreamProviderDataTable(SynchronizedPlayerProviderMixin, ProviderDataTable):

    def keypress(self, size, key):

        if key == "meta r":
            self.provider.update()
            self.reset()
        else:
            return super().keypress(size, key)
        return key


class LiveStreamProviderView(SimpleProviderView):

    PROVIDER_BODY_CLASS = LiveStreamProviderDataTable


class LiveStreamProvider(BackgroundTasksMixin, BaseProvider):

    FILTERS = AttrDict([
        ("channel", ChannelsFilter)
    ])

    UPDATE_INTERVAL = 300

    TASKS = [
        ("update", UPDATE_INTERVAL, {"instant": True})
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.filters["channel"].connect("changed", self.on_channel_change)
        self.live_channels = list()


    @property
    def VIEW(self):
        return SimpleProviderView(self, LiveStreamProviderDataTable(self))

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
        cls = getattr(pkg, clsname, model.MediaChannel)
        return cls.attr_class

    # def parse_identifier(self, identifier):
    #     if identifier:
    #         # print(self.view) # FIXME
    #         self.filters.channel.selected_label = identifier
    #     raise SGIncompleteIdentifier


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
        for locator, name in self.channels.items():
            channel = self.CHANNEL_CLASS.orm_class.get(locator=locator)
            if not channel:
                channel = self.CHANNEL_CLASS.orm_class(
                    provider_id = self.IDENTIFIER,
                    name = name or locator,
                    locator = locator
                    # **self.feed_attrs(name)
                )
                commit()

    def listings(self, offset=None, limit=None, *args, **kwargs):

        return iter(self.live_channels)

    def on_activate(self):
        super().on_activate()
        self.create_channels()

    @db_session
    async def update(self):
        self.refresh()

    @db_session
    def refresh(self):
        if self.filters.channel.value:
            channels = [self.filters.channel.selected_label]
        else:
            channels = self.channels

        self.live_channels = list()
        for locator in channels:
            channel = self.CHANNEL_CLASS.orm_class.get(locator=locator)
            if not channel:
                raise Exception(locator)

            listing = self.check_channel(locator)
            logger.info(f"listing: {listing}")
            channel.updated = datetime.now()
            # if listing and listing.channel not in [l.channel for l in self.live_channels]:
            if listing:
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
