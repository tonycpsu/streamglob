from orderedattrdict import AttrDict

from .. import model

from .base import *

from .widgets import *
from .filters import *

class FeedListing(MediaListing):

    # @property
    # def locator(self):
    #     return self.content

    @property
    def feed_name(self):
        return self.feed.name

    @property
    def timestamp(self):
        return self.created.strftime("%Y%m%d_%H%M%S")



# class URLFeed(model.MediaFeed):

#     url = Required(str)

class CachedFeedProviderDataTable(ProviderDataTable):

    signals = ["focus"]

    HOVER_DELAY = 0.25

    with_scrollbar=True
    sort_by = ("created", True)
    index = "media_item_id"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ignore_blur = False
        self.mark_read_task = None
        self.update_count = True
        self.items_query = None
        urwid.connect_signal(
            self, "focus",
            self.on_focus
        )

    def query_result_count(self):
        if self.update_count:
            with db_session:
                if not self.provider.items_query:
                    return 0
                # self._row_count = len(self.provider.feed.items)
                self._row_count = self.provider.items_query.count()
                logger.info(f"row count: {self._row_count}")
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
            if not isinstance(self[position].data, MediaListing):
                return
        except IndexError:
            return
        item = self.item_at_position(position)
        if not item:
            return
        item.mark_read()
        self[position].clear_attr("unread")
        self.set_value(position, "read", item.read)
        self.invalidate_rows([self[position].data.media_item_id])

    @db_session
    def mark_item_unread(self, position):
        if not isinstance(self[position].data, MediaListing):
            return
        item = self.item_at_position(position)
        if not item:
            return
        item.mark_unread()
        self[position].set_attr("unread")
        self.set_value(position, "read", item.read)
        self.invalidate_rows([self[position].data.media_item_id])

    @db_session
    def toggle_item_read(self, position):
        if not isinstance(self[position].data, MediaListing):
            return
        logger.info(self.get_value(position, "read"))
        if self.get_value(position, "read") is not None:
            self.mark_item_unread(position)
        else:
            self.mark_item_read(position)

    @db_session
    def item_at_position(self, position):
        return self.provider.ITEM_CLASS.get(
            guid=self[position].data.get("guid")
        )

    # def reset(self, *args, **kwargs):
    #     self.update_c ount = True
    #     self.provider.update_query()
    #     super().reset(*args, **kwargs)

    def keypress(self, size, key):

        if key == "meta r":
            asyncio.create_task(self.provider.update(force=True))
        elif key == "n":
            try:
                idx = next(
                    r.data.media_item_id
                    for r in self[self.focus_position+1:]
                    if not r.data.read
                )
            except StopIteration:
                self.focus_position = len(self)-1
                self.load_more(self.focus_position)
                self.focus_position += 1
                return
            pos = self.index_to_position(idx)
            self.focus_position = pos
            self._modified()
        elif key == "p":
            try:
                idx = next(
                    r.data.media_item_id
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
                if self.provider.feed:
                    self.provider.feed.mark_all_items_read()
                else:
                    self.provider.FEED_CLASS.mark_all_feeds_read()
            self.reset()
        elif key == "u":
            self.toggle_item_read(self.focus_position)
            self.ignore_blur = True
        else:
            return super().keypress(size, key)
        return key

class FeedsFilter(ConfigFilter):

    key = "feeds"
    with_all = True


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


class CachedFeedProviderView(SimpleProviderView):

    PROVIDER_DATA_TABLE_CLASS = CachedFeedProviderDataTable


@with_view(CachedFeedProviderView)
class CachedFeedProvider(BackgroundTasksMixin, FeedProvider):


    UPDATE_INTERVAL = 300

    TASKS = [
        ("update", UPDATE_INTERVAL, [], {"force": True})
    ]

    @property
    def ITEM_CLASS(self):
        return self.FEED_CLASS.ITEM_CLASS


    @property
    def ATTRIBUTES(self):
        return AttrDict(
            media_item_id = {"hide": True},
            feed = {"width": 32, "format_fn": lambda f: f.name if hasattr(f, "name") else "none"},
            created = {"width": 19},
            title = {"width": ("weight", 1)},
        )

    @property
    def feed(self):

        if not self.selected_feed:
            return None
        with db_session:
            feed = self.FEED_CLASS.get(
                provider_name = self.IDENTIFIER,
                name = self.selected_feed_label
            )
        return feed

    @property
    def feeds(self):
        if isinstance(self.config.feeds, dict):
            return self.config.feeds
        else:
            return AttrDict([
                (f, f) for f in self.config.feeds
            ])

    @db_session
    def create_feeds(self):
        for name, locator in self.feeds.items():
            feed = self.FEED_CLASS.get(locator=locator)
            if not feed:
                feed = self.FEED_CLASS(
                    provider_name = self.IDENTIFIER,
                    name = name,
                    locator=self.filters.feed[name]
                    # **self.feed_attrs(name)
                )
                commit()

    def feed_attrs(self, feed_name):
        return {}

    @db_session
    def update_feeds(self, force=False):
        logger.info("update_feeds")
        if not self.feed:
            feeds = self.FEED_CLASS.select()
        else:
            feeds = [self.feed]

        for f in feeds:
            if (force
                or
                f.updated is None
                or
                datetime.now() - f.updated
                > timedelta(seconds=f.update_interval)
            ):
                logger.info(f"update {f}")
                f.update()
                # asyncio.create_task(f.update())
                # await f.update()
                # state.asyncio_loop.run_in_executor(None, f.update)
                f.updated = datetime.now()
        # state.asyncio_loop.create_task(self.refresh())

    @property
    def feed_filters(self):
        return None

    def on_feed_change(self, *args):
        self.refresh()
        # state.asyncio_loop.create_task(self.refresh())
        self.view.table.reset()

    # @db_session
    async def update(self, force=False):
        logger.info(f"update: {force}")
        self.refresh()
        self.create_feeds()
        # state.loop.draw_screen()
        logger.info("-update foo")
        def update_feeds():
            self.update_feeds(force=force)
            self.refresh()
        update_task = state.asyncio_loop.run_in_executor(None, update_feeds)
        # logger.info("-update bar")
        # state.loop.draw_screen()
        logger.info("-update")
        # state.loop.draw_screen()

    def refresh(self):
        logger.info("+refresh")
        self.update_query()
        self.view.table.refresh()
        # state.loop.draw_screen()
        logger.info("-refresh")

    # def on_activate(self):
    #     self.refresh()
    #     self.update()


    @db_session
    def update_query(self):

        status_filters =  {
            "all": lambda: True,
            "unread": lambda i: i.read is None,
            "not_downloaded": lambda i: i.downloaded is None
        }

        (sort_field, sort_desc) = self.view.table.sort_by
        if sort_desc:
            sort_fn = lambda i: desc(getattr(i, sort_field))
        else:
            sort_fn = lambda i: getattr(i, sort_field)

        self.items_query = (
            self.ITEM_CLASS.select()
            .order_by(sort_fn)
            .filter(status_filters[self.filters.status.value])
                # [offset:offset+limit]
        )

        if self.feed_filters:
            for f in self.feed_filters:
                self.items_query = self.items_query.filter(f)

        if self.feed:
            self.items_query = self.items_query.filter(
                lambda i: i.feed == self.feed
            )
        self.view.table.update_count = True

    def listings(self, offset=None, limit=None, *args, **kwargs):

        count = 0

        if not offset:
            offset = 0
        if not limit:
            limit = self.limit

        with db_session:
            # self.update_feeds()

            for item in self.items_query[offset:offset+limit]:
                l = self.LISTING_CLASS(**item.to_dict(related_objects=True))
                source_cls = getattr(self, "MEDIA_SOURCE_CLASS", model.MediaSource)
                l.content = source_cls.schema().loads(l["content"], many=True)
                # d.content = MediaSource.from_json(d.content, many=True)
                # raise Exception(type(d.content))
                l.feed = AttrDict(l.feed.to_dict())
                yield(l)
