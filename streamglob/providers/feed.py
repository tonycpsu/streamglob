from orderedattrdict import AttrDict

from .base import *

from .widgets import *
from .filters import *

class FeedItem(AttrDict):
    pass

class CachedFeedProviderDataTable(ProviderDataTable):

    HOVER_DELAY = 0.25

    with_scrollbar=True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ignore_blur = False
        self.mark_read_task = None
        self.update_count = True
        urwid.connect_signal(
            self, "focus",
            self.on_focus
        )

    def query_result_count(self):
        if self.update_count:
            with db_session:
                self._row_count = len(self.provider.feed.items)
                self.update_count = False
        return self._row_count


    def row_attr_fn(self, row):
        if not row.get("read"):
            return "unread"
        return None

    @db_session
    def on_focus(self, source, position):
        if self.mark_read_task:
            self.mark_read_task.cancel()
        self.mark_read_task = state.asyncio_loop.call_later(
            self.HOVER_DELAY,
            lambda: self.mark_item_read(position)
        )

    @db_session
    def on_blur(self, source, position):
        if self.ignore_blur:
            self.ignore_blur = False
            return
        self.mark_item_read(position)

    @db_session
    def mark_item_read(self, position):
        try:
            if not isinstance(self[position].data, FeedItem):
                return
        except IndexError:
            return
        item = self.item_at_position(position)
        if not item:
            return
        item.mark_read()
        self.selection.clear_attr("unread")
        self.set_value(position, "read", item.read)
        self.invalidate_rows([self[position].data.index])

    @db_session
    def mark_item_unread(self, position):
        if not isinstance(self[position].data, FeedItem):
            return
        item = self.item_at_position(position)
        if not item:
            return
        item.mark_unread()
        self.selection.set_attr("unread")
        self.set_value(position, "read", item.read)
        self.invalidate_rows([self[position].data.index])

    @db_session
    def toggle_item_read(self, position):
        if not isinstance(self[position].data, FeedItem):
            return
        logger.info(self.get_value(position, "read"))
        if self.get_value(position, "read") is not None:
            self.mark_item_unread(position)
        else:
            self.mark_item_read(position)

    @db_session
    def item_at_position(self, position):
        return self.provider.feed.ITEM_CLASS.get(
            guid=self[position].data.get("guid")
        )

    def reset(self, *args, **kwargs):
        self.update_count = True
        super().reset(*args, **kwargs)

    def keypress(self, size, key):

        if key == "meta r":
            self.provider.update()
            self.reset()
        elif key == "n":
            try:
                idx = next(
                    r.index
                    for r in self[self.focus_position+1:]
                    if not r.data.read
                )
            except StopIteration:
                self.focus_position = len(self)-1
                self.load_more()
                self.focus_position += 1
                return
            pos = self.index_to_position(idx)
            self.focus_position = pos
            self._modified()
        elif key == "p":
            try:
                idx = next(
                    r.index
                    for r in self[self.focus_position-1::-1]
                    if not r.data.read
                )
            except StopIteration:
                return
            pos = self.index_to_position(idx)
            self.focus_position = pos
            self._modified()
        elif key == "A":
            with db_session:
                self.provider.feed.mark_all_read()
            self.reset()
        elif key == "u":
            self.toggle_item_read(self.focus_position)
            self.ignore_blur = True
        else:
            return super().keypress(size, key)
        return key

class FeedsFilter(ListingFilter):

    @property
    def values(self):
        cfg = self.provider.config.feeds
        if isinstance(cfg, dict):
            return cfg
        elif isinstance(cfg, list):
            return [ (i, i) for i in cfg ]

    # @property
    # def widget_sizing(self):
    #     return lambda w: ("weight", 1)

    @property
    def widget_sizing(self):
        return lambda w: ("given", 40)



class ItemStatusFilter(ListingFilter):

    values = AttrDict([
        (s, s.lower().replace(" ", "_"))
        for s in ["All", "Unread", "Not Downloaded"]
    ])

class FeedProvider(BaseProvider):
    """
    A provider that offers multiple feeds to select from
    """

    FILTERS = AttrDict([
        ("feed", FeedsFilter),
        ("status", ItemStatusFilter)
    ])

    REQUIRED_CONFIG = ["feeds"]

    @property
    def selected_feed_label(self):
        return self.filters.feed.label

    @property
    def selected_feed(self):
        return self.filters.feed.value

    def parse_identifier(self, identifier):
        if identifier:
            # print(self.view) # FIXME
            self.filters.feed.label = identifier
        raise SGIncompleteIdentifier

class CachedFeedProvider(FeedProvider):

    UPDATE_INTERVAL = 300
    MAX_ITEMS = 100


    @property
    def feed(self):
        # if not self._feed:
        feed = self.FEED_CLASS.get(
            provider_name = self.IDENTIFIER,
            name = self.selected_feed
        )
        if not feed:
            feed = self.FEED_CLASS(
                provider_name = self.IDENTIFIER,
                name = self.selected_feed
            )
        return feed

    @db_session
    def update(self):
        self.feed.update()

    def listings(self, offset=None, limit=None, *args, **kwargs):

        status_filter = {
            "all": lambda: True,
            "unread": lambda i: i.read is None,
            "not_downloaded": lambda i: i.downloaded is None
        }

        count = 0

        if not offset:
            offset = 0
        if not limit:
            limit = self.limit

        with db_session:
            f = self.FEED_CLASS.get(
                provider_name = self.IDENTIFIER,
                name = self.selected_feed
            )

            if not f:
                f = self.FEED_CLASS(
                    provider_name = self.IDENTIFIER,
                    name = self.selected_feed
                )

            if (f.updated is None
                or
                datetime.now() - f.updated
                > timedelta(seconds=f.update_interval)
            ):
                f.update()
                f.updated = datetime.now()

            for item in self.ITEM_CLASS.select(
                    lambda i: i.feed == f
            ).filter(status_filter[self.filters.status.value])[offset:offset+limit]:
                yield(FeedItem(item.to_dict()))
