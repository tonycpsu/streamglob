import logging
logger = logging.getLogger(__name__)

import os
import re
from datetime import datetime
from dataclasses import *
import functools
import textwrap
from itertools import chain
import asyncio

from orderedattrdict import AttrDict
from panwid.datatable import *
from panwid.dialog import *
from panwid.keymap import *
from panwid.sparkwidgets import SparkBarWidget, SparkBarItem
from panwid.progressbar import ProgressBar
from limiter import get_limiter, limit
from pony.orm import *
import timeago
import dateparser

from .. import model
from .. import utils

from .base import *

from ..widgets.channels import ChannelTreeBrowser
from .widgets import *
from .filters import *


DATE_RE = r"\b\d{4}(?:[-/]\d{2}(?!\d))?(?:[-/]\d{2}(?!\d))?"
TIME_RE = r"\d{2}?(?:[:.]\d{2})?(?:[:.]\d{2})?"
DATETIME_RE = f"{DATE_RE}(?:[T_ ]{TIME_RE})?"
DATETIME_RANGE_RE = re.compile(
    f"({DATETIME_RE})?\s*-?\s*({DATETIME_RE})?"
)

@model.attrclass()
class FeedMediaChannel(model.MediaChannel):
    """
    A subclass of MediaChannel for providers that can distinguish between
    individual broadcasts / episodes / events, perhaps with the ability to watch
    on demand.
    """

    DEFAULT_MIN_ITEMS=10
    DEFAULT_MAX_ITEMS=500
    DEFAULT_MAX_AGE=90

    @property
    def items(self):
        return self.listings

    @abc.abstractmethod
    async def fetch(self):
        pass

    async def update(self, resume=False, replace=False, *args, **kwargs):

        fetched = 0
        self.provider.update_fetch_indicator(0)
        self.provider.view.footer.show_message(f"{'Fetching' if resume else 'Updating'} {self.name}...")
        async for item in self.fetch(
                limit=self.provider.fetch_limit, resume=resume, replace=replace,
                *args, **kwargs
        ):
            with db_session:
                old = self.provider.LISTING_CLASS.get(guid=item["guid"])
                if old:
                    old.delete()
                item["sources"] = [
                    self.provider.new_media_source(rank=i, **s).attach()
                    for i, s in enumerate(item["sources"])
                ]

                listing = self.provider.new_listing(
                    **item
                ).attach()
                commit()
                if self.provider.config.get("inflate_on_fetch") and not listing.is_inflated:
                    logger.info("inflating on fetch")
                    await listing.inflate()
            with db_session:
                try:
                    listing = self.provider.LISTING_CLASS[listing.media_listing_id]
                except ObjectNotFound:
                    continue
                # self.provider.on_new_listing(listing)
                self.updated = datetime.now()
                fetched += 1
                self.provider.update_fetch_indicator(fetched)

        self.fetched = datetime.utcnow()

        if resume and fetched == 0:
            self.attrs["tail_fetched"] = True

        await self.provider.view.channels.find_node(self.locator).refresh()
        return fetched


    @db_session
    def mark_all_items_read(self):
        for i in self.items.select():
            i.mark_read()

    @classmethod
    @db_session
    def mark_all_feeds_read(cls):
        for f in cls.select():
            for i in f.items.select():
                i.read = datetime.now()

    @db_session
    def reset(self):
        delete(i for i in self.items)
        self.attrs["end_cursor"] = None
        commit()

    @classmethod
    @db_session
    def purge_all(cls,
                  min_items = DEFAULT_MIN_ITEMS,
                  max_items = DEFAULT_MAX_ITEMS,
                  max_age = DEFAULT_MAX_AGE):
        for f in cls.select():
            f.purge(min_items = min_items,
                    max_items = max_items,
                    max_age = max_age)

    @db_session
    def purge(self,
              min_items = DEFAULT_MIN_ITEMS,
              max_items = DEFAULT_MAX_ITEMS,
              max_age = DEFAULT_MAX_AGE):
        """
        Delete items older than "max_age" days, keeping no fewer than
        "min_items" and no more than "max_items"
        """
        for n, i in enumerate(
                self.items.select().order_by(
                    lambda i: desc(i.fetched)
                )[min_items:]
        ):
            if (min_items + n >= max_items
                or
                i.time_since_fetched >= timedelta(days=max_age)):
                i.delete()
        commit()

    @property
    def listing_count(self):
        with db_session:
            return self.items.select().count()

    @property
    def unread_count(self):
        with db_session:
            return self.items.select(lambda i: not i.read).count()


class FeedMediaListingMixin(object):

    @property
    def feed(self):
        return self.channel

    @db_session
    def mark_read(self):
        now = datetime.now()
        l = self.attach()
        l.read = now
        for s in l.sources:
            s.seen = now
        commit()

    @db_session
    def mark_unread(self):
        l = self.attach()
        l.read = None
        for s in l.sources:
            s.seen = None
        commit()

    @property
    def age(self):
        return datetime.now() - self.created

    @property
    def time_since_fetched(self):
        # return datetime.now() - dateutil.parser.parse(self.fetched)
        return datetime.now() - self.fetched

    @property
    def feed_name(self):
        with db_session:
            feed = model.MediaChannel[self.channel.channel_id]
            return feed.name

    @property
    def feed_locator(self):
        with db_session:
            feed = model.MediaChannel[self.channel.channel_id]
            return feed.locator

    # FIXME
    @property
    def timestamp(self):
        return self.created.strftime("%Y%m%d_%H%M%S")

    # FIXME
    @property
    def created_timestamp(self):
        return self.created.isoformat().split(".")[0]

    @property
    def created_date(self):
        return self.created_timestamp.split("T")[0]

    @property
    def content_date(self):
        return self.title_date or self.created_date

    def on_focus(self):
        pass

    @property
    def body(self):
        return self.title

    async def check(self):
        return all([await s.check() for s in self.sources])

    def refresh(self):
        pass

    @property
    def translate_src(self):
        return self.feed.attrs.get("translate", "auto")



@model.attrclass()
class FeedMediaListing(FeedMediaListingMixin, model.ChannelMediaListing, model.TitledMediaListing):
    """
    An individual media clip, broadcast, episode, etc. within a particular
    FeedMediaChannel.
    """
    # FIXME: move FeedMediaChannel here?
    # feed = Required(lambda: FeedMediaChannel)
    guid = Required(str, index=True)
    created = Required(datetime, default=datetime.now)
    fetched = Required(datetime, default=datetime.now)
    read = Optional(datetime)


class FeedMediaSourceMixin(object):

    def mark_seen(self):
        with db_session:
            self.seen = datetime.now()
            commit()

    def mark_unseen(self):
        with db_session:
            self.seen = None
            commit()

    @property
    def uri(self):
        with db_session:
            listing = self.provider.LISTING_CLASS[self.listing.media_listing_id]
            try:
                return f"{self.provider.IDENTIFIER}/{listing.channel.locator}.{listing.guid}"
            except AttributeError:
                raise

    async def check(self):
        return True

    def refresh(self):
        for s in self.sources:
            s.refresh()


@model.attrclass()
class FeedMediaSource(FeedMediaSourceMixin, model.MediaSource):

    seen = Optional(datetime)
    created = Required(datetime, default=datetime.now)


class CachedFeedProviderDetailBox(DetailBox):

    def detail_table(self):
        columns = [
            c for c in  self.parent_table._columns.copy()
            if not isinstance(c, DataTableDivider)
        ]
        return CachedFeedProviderDetailDataTable(
            self.parent_table.provider,
            self.listing, self.parent_table, columns=columns
        )


@keymapped()
class CachedFeedProviderDetailDataTable(DetailDataTable):

    signals = ["next_unseen"]

    KEYMAP = {
        "m": "toggle_selection_seen",
        "n": "next_unseen",
        "N": "next_unread",
    }

    def keypress(self, size, key):
        return super().keypress(size, key)

    async def mark_selection_seen(self):
        with db_session:
            try:
                # import ipdb; ipdb.set_trace()
                source = FeedMediaSource[self.selected_source.media_source_id]
            except ObjectNotFound:
                return
                # source = self.selected_source
            source.mark_seen()
            commit()
        self.selection.clear_attr("unread")
        # logger.info("mark seen")

    async def mark_selection_unseen(self):
        with db_session:
            source = self.selected_source
            # source = FeedMediaSource[self.selection.data.media_source_id]
            source.mark_unseen()
        self.selection.set_attr("unread")
        # logger.info("mark unseen")


    async def toggle_selection_seen(self):
        with db_session:
            source = FeedMediaSource[self.selection.data.media_source_id]

        if source.seen:
            await self.mark_selection_unseen()
        else:
            await self.mark_selection_seen()

    @keymap_command
    async def next_unseen(self):
        # await self.parent_table.next_unseen()
        # import ipdb; ipdb.set_trace()
        if self.selection:
            await self.mark_selection_seen()

        with db_session:
            # raise Exception(self.listing)
            listing = FeedMediaListing[self.listing.media_listing_id]
            next_unseen =  select(s for s in listing.sources
                       if not s.seen).order_by(lambda s: s.rank).first()
            if next_unseen:
                self.focus_position = next_unseen.rank
            else:
                await self.next_unread()

    @keymap_command
    async def next_unread(self):
        await self.parent_table.next_unread(no_sources=True)


@keymapped()
class CachedFeedProviderDataTable(SynchronizedPlayerProviderMixin, ProviderDataTable):

    signals = ["focus", "keypress", "unread_change", "next_feed"]

    HOVER_DELAY = 0.25

    sort_by = ("created", True)
    index = "media_listing_id"
    no_load_on_init = True
    detail_auto_open = True
    detail_replace = True
    detail_selectable = True

    KEYMAP = {
        "cursor up": "prev_item",
        "cursor down": "next_item",
        # "n": "next_unread",
        "N": ("next_unread", [True]),
        "b": "prev_unread",
        "m": "toggle_selection_read",
        "i": "inflate_listing",
        # "a": "mark_feed_read"
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # self.mark_read_on_focus = False
        # self.mark_read_task = None
        self.update_count = True
        self.updat_task = None
        # urwid.connect_signal(self, "requery", self.on_requery)

    def on_requery(self, source, count):
        super().on_requery(source, count)
        self.update_count = True

    def reset(self):
        super().reset()
        logger.info('reset')

    # def detail_box(self):
    #    return CachedFeedProviderDetailBox(self)

    def detail_table(self, *args, **kwargs):
        return self.CachedFeedProvideDetailTable(self, *args, **kwargs)

    def query_result_count(self):
        if self.update_count:
            with db_session:
                if self.provider.items_query is None:
                    return 0
                # self._row_count = len(self.provider.feed.items)
                self._row_count = self.provider.items_query.count()
                logger.info(f"row count: {self._row_count}")
                self.update_count = False
        return self._row_count

    def unseen_sources(self, row):
        with db_session:
            listing = row.attach()
            # logger.info(listing)
            # logger.info(len([s for s in listing.sources if not s.seen]))
            return len([s for s in listing.sources if not s.seen])

    def row_attr_fn(self, position, data, row):
        return "unread" if not data.read else "normal"

    @keymap_command()
    async def inflate_listing(self, index=None):
        # async with self.provider.listing_lock:
        with db_session(optimistic=False):
            listing = self.get_listing(index=index).attach()
            # listing = self.selection.data_source.attach()
            if await listing.inflate(force=True):
                # position = self.focus_position
                self.invalidate_rows([listing.media_listing_id])
                self.selection.update()

    # @keymap_command()
    # async def inflate_selection(self):
    #     async with self.provider.listing_lock:
    #         with db_session:
    #             listing = self.selection.data_source.attach()
    #             if await listing.inflate(force=True):
    #                 # position = self.focus_position
    #                 self.invalidate_rows([listing.media_listing_id])
    #                 self.selection.update()
    #                 # self.selection.close_details()
    #                 # self.selection.open_details()
    #                 # self.refresh()
    #                 # self.focus_position = position

    # FIXME
    # def on_focus(self, source, position):
    #     if self.mark_read_on_focus:
    #         self.mark_read_on_focus = False
    #         if self.mark_read_task:
    #             self.mark_read_task.cancel()
    #         self.mark_read_task = state.event_loop.call_later(
    #             self.HOVER_DELAY,
    #             lambda: self.mark_item_read(position)
    #         )


    async def mark_item_read(self, position, no_signal=False):
        logger.debug(f"mark_item_read: {position}")

        try:
            row = self[position]
        except IndexError:
            return
        with db_session:
            listing = row.data_source
            item = self.item_at_position(position)
            if not item:
                return
            item.mark_read()
            row.clear_attr("unread")
            self.set_value(position, "read", item.read)
            await self.update_row_attribute(row)
            # self.invalidate_rows([self[position].data.media_listing_id])
            if not no_signal:
                self._emit("unread_change", listing.channel.detach())

    async def mark_item_unread(self, position, no_signal=False):
        logger.debug(f"mark_item_unreadread: {position}")
        row = self[position]
        with db_session:
            listing = row.data_source
            item = self.item_at_position(position)
            if not item:
                return
            item.mark_unread()
            row.set_attr("unread")
            self.set_value(position, "read", item.read)
            await self.update_row_attribute(row)
            # self.invalidate_rows([self[position].data.media_listing_id])
            if not no_signal:
                self._emit("unread_change", listing.channel.detach())


    @db_session
    async def toggle_item_read(self, position):
        listing = self[position].data_source
        if position >= len(self):
            return
        if listing.read:
            await self.mark_item_unread(position)
        else:
            await self.mark_item_read(position)

    async def toggle_selection_read(self):
        logger.info("toggle_selection_read")
        await self.toggle_item_read(self.focus_position)

    # def mark_feed_read(self):
    #     listing = self.selection.data_source
    #     with db_session:
    #         listing.feed.attach().mark_all_items_read()
    #     self._emit("unread_change", listing.channel.detach())
    #     self.reset()

    async def prev_item(self):
        if self.focus_position > 0:
            self.focus_position -= 1

    async def next_item(self):
        if self.focus_position < len(self)-1:
            self.focus_position += 1

    @staticmethod
    def is_unread(listing):
        return not listing.data_source.attach().read

    async def next_unread(self, no_sources=False):
        return await self.next_matching(self.is_unread, no_sources=no_sources)

    async def next_matching(self, predicate, no_sources=False):
        # FIXME: this is sort of a mish-mash between a general purpose
        # function and one particular to marking read and moving to the next
        # unread.  Will require some cleanup if it's used for other purposes.

        idx = None
        count = 0
        last_count = None

        row = self.selection
        if not row:
            self.provider.view.channels.cycle_unread()
            return
        with db_session:

            # async with self.provider.listing_lock:
            listing = model.MediaListing[row.data_source.media_listing_id]
            for i, s in enumerate(listing.sources):
                source = FeedMediaSource[s.media_source_id]
                # logger.info(f"{i}, {source}")
                # source.attach().read = now
                source.mark_seen()
                commit()
                if len(listing.sources) > 1:
                    logger.info(f"{len(listing.sources)}, {len(row.details.contents.table)}")
                    row.details.contents.table[i].clear_attr("unread")
                # else:
                #     listing_source.attach().read = now
            row.close_details()
            row.clear_attr("unread")

            old_pos = self.focus_position
            try:
                idx = next(
                    r.data.media_listing_id
                    for r in self[self.focus_position+1:]
                    if predicate(r)
                )
            except (StopIteration, AttributeError):
                if self.focus_position == len(self)-1:
                    if self.selection.data["read"]:
                        if self.sort_by == ("created", False):
                            self.provider.view.columns.focus_position = 0
                            self.provider.view.channels.cycle_unread(1)
                            # self.provider.view.channels.advance(skip=True)
                            # self._emit("next_feed")
                        elif self.sort_by == ("created", True):
                            updated = self.load_more(self.focus_position)
                            if updated:
                                if self.focus_position < len(self) - 1:
                                    self.focus_position += 1
                            else:
                                new = await self.provider.view.body.update(
                                    force=True, resume=True
                                )
                                if new:
                                    self.reset()
                                else:
                                    # await self.mark_item_read(self.focus_position, no_signal=True)
                                    self.provider.view.channels.cycle_unread()
                                    # break
                                    # self._emit("unread_change", listing.channel.detach())
                                    # return

                else:
                    # self.focus_position = len(self)-1
                    self.provider.view.channels.cycle_unread()

            await self.mark_item_read(self.focus_position, no_signal=True)

            if idx:
                pos = self.index_to_position(idx)
                focus_position_orig = self.focus_position
                self.focus_position = pos
                self.mark_read_on_focus = True
                self._modified()
            self._emit("unread_change", listing.channel.detach())


    @keymap_command
    async def prev_unread(self):
        try:
            idx = next(
                r.data.media_listing_id
                for r in self[self.focus_position-1::-1]
                if not r.data.read
            )
            await self.mark_item_read(self.focus_position)
            pos = self.index_to_position(idx)
        except StopIteration:
            if self.focus_position >= 1:
                pos = self.focus_position - 1
        self.focus_position = pos
        self.mark_read_on_focus = True
        self._modified()

    def select_guid(self, guid):
        pass

    @db_session
    def item_at_position(self, position):
        return self.provider.LISTING_CLASS.get(
            guid=self[position].data.get("guid")
        )

    def keypress(self, size, key):
        return super().keypress(size, key)


class FeedsFilter(HiddenFilterMixin, PropertyFilter):
    pass

class ItemStatusFilter(ListingFilter):

    items = AttrDict([
        (s, s.lower().replace(" ", "_"))
        for s in ["All", "Unread", "Not Downloaded"]
    ])

FEED_URI_RE = re.compile("([^/.]+)(?:\.(.*))?")
class FeedProvider(BaseProvider):
    """
    A provider that offers multiple feeds to select from
    """

    FILTERS_BROWSE = AttrDict([
        ("feed", FeedsFilter),
    ])

    REQUIRED_CONFIG = ["feeds"]

    CHANNELS_LABEL = "channels"

    @property
    def FILTERS_OPTIONS(self):
        return AttrDict([
            ("status", ItemStatusFilter),
            ("custom", CustomFilter),
            ("search", TextFilter),
            ("subject", BooleanFilter)
        ], **super().FILTERS_OPTIONS)


    @property
    def default_filter_values(self):

        return AttrDict(
            feed=self.provider_data.get("selected_feed", None),
            status=self.provider_data.get("selected_status", None),
            filters=self.provider_data.get("selected_filter", None),
        )

    @property
    def feed(self):
        return self.view.selected_channels

    @feed.setter
    def feed(self, value):
        self.view.selected_channels = value

    @property
    def selected_channels(self):
        selection = self.view.selected_channels
        def parse_node(node):

            if node.is_leaf:
                return [node.get_key()]
            else:
                return node.get_leaf_keys()

        # logger.info(f"selection: {selection}")
        if selection:
            locators = list(chain.from_iterable([
                parse_node(node)
                for node in selection
            ]))
        else:
            locators = []

        # logger.info(f"locators: {locators}")
        with db_session:
            return list(
                select(
                    f for f in self.FEED_CLASS
                    if f.provider_id == self.IDENTIFIER
                    and f.locator in locators
                )
            )

    def parse_identifier(self, identifier):
        try:
            (feed, guid) = FEED_URI_RE.search(identifier).groups()
        except (IndexError, TypeError) as e:
            feed = identifier
            guid = None

        if guid:
            self.filters["search"].value = f"guid:{guid}"

        return (
            None,
            (feed,),
            # (feed or self.provider_data.get("selected_feed", None),),
            {}
        )

    def apply_options(self, options):
        if "status" not in options:
            options.status = self.provider_data.get("selected_status", None)
        super().apply_options(options)


class CachedFeedProviderFooter(urwid.WidgetWrap):

    def __init__(self, parent):

        self.parent = parent
        self.position_placeholder = urwid.WidgetPlaceholder(urwid.Text(""))
        self.indicator_placeholder = urwid.WidgetPlaceholder(urwid.Text(""))
        self.message_placeholder = urwid.WidgetPlaceholder(urwid.Text(""))
        self._width = None
        self.filler = urwid.Filler(
            urwid.AttrMap(
                urwid.Columns([
                    ("weight", 1, urwid.Padding(self.position_placeholder)),
                    ("weight", 2, urwid.Padding(self.indicator_placeholder)),
                    ("weight", 1, urwid.Padding(self.message_placeholder)),
                ], dividechars=1),
                "footer"
            )
        )
        super().__init__(self.filler)

    def render(self, size, focus=False):

        update = self._width is None
        self._width = size[0]
        if update:
            self.update()
        return super().render(size, focus=focus)

    def update(self, count=0):

        self.update_status_indicator(count)
        self.update_position_indicator(count)
        self.update_count()

    def update_fetch_indicator(self, num, count):

        if self._width:
            self.set_position_widget(
                ProgressBar(
                    width=int(self._width *(1/4)), # FIXME: urwid/urwid#225 strikes again
                    maximum=count,
                    value=num,
                    progress_color="light green",
                    remaining_color="dark green"
                )
            )
            state.loop.draw_screen()

    def update_position_indicator(self, count=0):

        if not (self._width):
            self.set_position_widget(urwid.Text(""))
            return

        self.set_position_widget(
            ProgressBar(
                width=int(self._width *(1/4)), # FIXME: urwid/urwid#225 strikes again
                maximum=self.parent.footer_attrs["shown"](),
                value=self.parent.footer_attrs["selected"](),
                progress_color="light blue",
                remaining_color="dark blue"
                # min_width=5
            )
        )

    def update_status_indicator(self, count=0):

        if not (self._width):
            self.set_indicator_widget(urwid.Text(""))
            return

        spark_vals = [
            SparkBarItem(
                func(),
                bcolor=attr,
                label=f"{label}{self.parent.footer_attrs[name](): >3}",
                fcolor="black",
                align=">" if i else "<"
            )
            for i, (name, label, attr, func) in enumerate(self.parent.indicator_bars)
        ]

        logger.info([i.value for i in spark_vals])
        self.set_indicator_widget(
            SparkBarWidget(
                spark_vals,
                int(self._width *(1/2)) - 5, # FIXME: urwid/urwid#225 strikes again
                fit_label=True
                # min_width=5
            )
        )

    def update_count(self):
        self.show_message(", ".join((
            f"{label}: {func()}"
            for label, func in self.parent.footer_attrs.items()
            if label in ["refreshed", "updated"]
        )))

    def show_message(self, message):
        text = urwid.Text(message, align="right")
        self.set_message_widget(text)

    def set_message_widget(self, widget):
        self.message_placeholder.original_widget = widget

    def set_position_widget(self, widget):
        self.position_placeholder.original_widget = widget

    def set_indicator_widget(self, widget):
        self.indicator_placeholder.original_widget = widget


@keymapped()
class CachedFeedProviderBodyView(urwid.WidgetWrap):

    signals = ["select", "cycle_filter", "keypress", "feed_change", "feed_select"]

    KEYMAP = {
        "n": "advance",
        "a": "mark_feed_read",
        "A": "mark_all_read",
        "ctrl a": "mark_visible_read",
        "ctrl up": ("move_feed", [-1]),
        "ctrl down": ("move_feed", [1]),
        "delete": "delete_feed",
        "meta a": ("mark_visible_read", [-1]),
        "meta A": ("mark_visible_read", [1]),
        "meta ctrl k": "kill_all",
        "r": ("update", [], {"force": True}),
        "R": ("update", [], {"force": True, "resume": True}),
        "meta R": ("update", [], {"force": True, "resume": True, "replace": True})
    }

    def __init__(self, provider, body):
        self.provider = provider
        self.body = body
        self.detail = urwid.WidgetPlaceholder(urwid.Filler(urwid.Text("")))
        self.footer = CachedFeedProviderFooter(self)
        self.channels = ChannelTreeBrowser(
            self.provider.config.feeds,
            self.provider,
            label="All " + self.provider.CHANNELS_LABEL
        )
        self.channels_pile = urwid.Pile([
            ("weight", 1, self.channels)
        ])
        self.columns = urwid.Columns([
            (*(self.provider.config.get_path(
                "display.columns.channels"
            ) or ("weight", 1)), self.channels_pile),
            (*(self.provider.config.get_path(
                "display.columns.listings"
            ) or ("weight", 3)), self.body)
        ], dividechars=1)
        self.columns.focus_position=1
        self.pile = urwid.Pile([
            ("weight", 4, self.columns),
            (1, urwid.SolidFill(u"\N{BOX DRAWINGS LIGHT HORIZONTAL}")),
            ("weight", 1, self.detail),
            (1, self.footer),
        ])

        super().__init__(self.pile)
        self.pile.focus_position = 0
        urwid.connect_signal(self.body, "keypress", lambda *args: self._emit(*args))
        urwid.connect_signal(self.body, "unread_change", self.on_unread_change)
        urwid.connect_signal(self.body, "next_feed", self.on_next_feed)
        urwid.connect_signal(self.body, "focus", self.on_focus)
        urwid.connect_signal(self.channels, "select",
                             lambda s, *args: self._emit("feed_select", *args))
        urwid.connect_signal(self.channels, "advance", self.on_channels_advance)
        urwid.connect_signal(self.channels, "change",
                             lambda s, *args: self._emit("feed_change", *args))

    def advance(self):
        self.channels.advance()

    def mark_all_read(self):
        with db_session:
            for f in self.provider.selected_channels:
                f.mark_all_items_read()
        self.reset()

    async def mark_visible_read(self, direction=None):
        for n, item in enumerate(self.body):
            if direction and (
                    direction < 0 and n > self.body.focus_position
                    or direction> 0 and n < self.body.focus_position
            ):
                continue
            await self.body.mark_item_read(n)
        self.reset()


    def mark_feed_read(self):
        with db_session:
            self.channels.listbox.focus.channel.mark_all_items_read()
        self.reset()

    def move_feed(self, direction):
        self.channels.move_selection(direction)

    def delete_feed(self):
        with db_session:
            self.channels.delete_selection()
        # self.reset()

    def on_unread_change(self, source, listing):
        async def refresh_channels():
            await self.channels.find_node(listing.locator).refresh()
        asyncio.create_task(refresh_channels())
        # self.channels._invalidate()

    def on_next_feed(self, source):
        self.channels.cycle(step=1)

    def on_channels_advance(self, source):
        async def advance():
            # if self.columns.focus_position == 0:
            await self.body.next_unread()
            self.columns.focus_position = 1
        asyncio.create_task(advance())


    @keymap_command
    async def update(self, force=False, resume=False, replace=False):
        return await self.provider.update(force=force, resume=resume, replace=replace)

    @db_session
    def kill_all(self):
        for feed in self.provider.selected_channels:
            logger.info(f"killing all messages for {feed.locator}")
            feed.reset()
        self.reset()

    def cycle_feed(self, step=1):
        self.channels.cycle(step)

    def keypress(self, size, key):
        return super().keypress(size, key)

    # def keypress(self, size, key):
    #     if key == "enter" and self.columns.focus_position == 0:
    #         super().keypress(size, key)
    #         self.columns.focus_position = 1
    #     else:
    #         return super().keypress(size, key)

    @property
    def selected_channels(self):

        return self.channels.selected_items

    @selected_channels.setter
    def selected_channels(self, value):
        self.channels.selected_items = value

    @property
    def all_channels(self):
        # import ipdb; ipdb.set_trace()
        return self.channels.all_channels

    @property
    def footer_attrs(self):

        if len(self.provider.selected_channels) == 1:
            feed = self.provider.selected_channels[0]
            return AttrDict([
                ("refreshed", lambda: timeago.format(feed.fetched, datetime.now(), "en_short")),
                ("updated", lambda: timeago.format(feed.updated, datetime.now(), "en_short")),
                ("selected", lambda: self.body.focus_position+1 if len(self) else 0),
                ("shown", lambda: len(self)),
                ("matching", lambda: self.body.query_result_count()),
                ("fetched", lambda: self.provider.feed_item_count),
                # ("fetched total", lambda: self.provider.total_item_count)
            ])
        else:
            # FIXME: aggregate stats
            return AttrDict([
                ("refreshed", lambda: "?"),
                ("updated", lambda: "?"),
                ("selected", lambda: self.body.focus_position+1),
                ("shown", lambda: 0),
                ("matching", lambda: 0),
                ("fetched", lambda: 0),
                # ("fetched total", lambda: self.provider.total_item_count)
            ])

    @property
    def indicator_bars(self):
        return [
            ("matching", "✓", "light blue",
             lambda: self.footer_attrs["matching"]()),
             # lambda: self.footer_attrs["matching"]()),
            ("fetched", "↓", "dark red",
             lambda: self.footer_attrs["fetched"]() - self.footer_attrs["matching"]()),
             # lambda: self.footer_attrs["fetched"]()),
        ]

    def on_requery(self, source, count):
        super().on_requery(source, count)
        self.footer.update(count)

    def on_focus(self, source, index):
        self.update_detail(source.selection)
        self.footer.update(len(source))

    def update_detail(self, selection):
        # FIXME: this is so hacktacular :/

        if not selection:
            self.detail.original_widget = urwid.Filler(urwid.Text(""))
            return
        listing = selection.data_source
        index = getattr(listing, self.body.df.index_name)
        row = self.body.render_item(index)
        body = listing.body
        detail = urwid.Pile([
            (1, urwid.Filler(
                urwid.AttrMap(
                    urwid.Columns([
                        ("pack", urwid.Text(listing.channel.name)),
                        ("weight", 1, row),
                        ("weight", 1,
                         urwid.Text(", ".join(listing.subjects or [""]))
                         ),
                    ], dividechars=1),
                    {
                        None: "table_row_header",
                        "table_row_body": "table_row_header"
                    }
                ),
                valign="top"
            )),
            ("weight", 1, urwid.Filler(urwid.Text(listing.body), valign="top"))
        ])
        detail.selectable = lambda: False
        self.detail.original_widget = detail

    def __iter__(self):
        return iter(self.body)

    def __len__(self):
        return len(self.body)

    def __getattr__(self, attr):
        return getattr(self.body, attr)

    def update_fetch_indicator(self, num, count):
        self.footer.update_fetch_indicator(num, count)


@keymapped()
class FeedProviderView(SimpleProviderView):

    signals = ["feed_change", "feed_select"]

    KEYMAP = {
        # "ctrl e": ("focus_filter", ["feed"]),
        "ctrl s": ("focus_filter", ["status"]),
    }

    def __init__(self, provider, body):
        self.provider = provider
        self.body = body
        urwid.connect_signal(self.body, "feed_change", lambda *args: self._emit(*args))
        urwid.connect_signal(self.body, "feed_select", lambda *args: self._emit(*args))
        super().__init__(self.provider, self.body)

    def keypress(self, size, key):
        return super().keypress(size, key)

    @property
    def feed(self):
        return self.body.feed

    @property
    def selected_channels(self):
        return self.body.selected_channels

    @selected_channels.setter
    def selected_channels(self, value):
        self.body.selected_channels = value

    def __getattr__(self, attr):
        return getattr(self.body, attr)


class CachedFeedProvider(BackgroundTasksMixin, TabularProviderMixin, FeedProvider):

    UPDATE_INTERVAL = (60 * 60 * 4)

    RATE_LIMIT = 5
    BURST_LIMIT = 5

    DEFAULT_FETCH_LIMIT = 50

    TASKS = [
        # ("update", UPDATE_INTERVAL, [], {"force": True})
        ("update", UPDATE_INTERVAL)
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.search_filter = None
        self.items_query = None
        self.custom_filters = AttrDict()
        self.filters["status"].connect("changed", self.on_status_change)
        self.filters["custom"].connect("changed", self.on_custom_change)
        self.pagination_cursor = None
        self.limiter = get_limiter(rate=self.RATE_LIMIT, capacity=self.BURST_LIMIT)
        self.listing_lock = asyncio.Lock()

    @property
    def VIEW(self):
        view = FeedProviderView(self, CachedFeedProviderBodyView(self, CachedFeedProviderDataTable(self)))
        return view

    def init_config(self):
        super().init_config()
        if not isinstance(self.view, InvalidConfigView):
            urwid.connect_signal(self.view, "feed_change", self.on_feed_change)
            urwid.connect_signal(self.view, "feed_select", self.on_feed_select)
        if config.settings.profile.cache.max_age > 0:
            with db_session(optimistic=False):
                FeedMediaChannel.purge_all(
                    min_items = config.settings.profile.cache.min_items,
                    max_items = config.settings.profile.cache.max_items,
                    max_age = config.settings.profile.cache.max_age
                )
        if not isinstance(self.view, InvalidConfigView): # FIXME
            self.create_feeds()

    def format_feed(feed):
        return feed.name if hasattr(feed, "name") else ""

    @property
    def ATTRIBUTES(self):
        # import ipdb; ipdb.set_trace()
        attrs = list(super().ATTRIBUTES.items())
        idx, attr = next(
            (i, a ) for i, a in enumerate(attrs)
            if a[0] == "title"
        )
        return AttrDict(
            attrs[:idx]
            + [
                ("media_listing_id", {"hide": True}),
                ("channel", {"width": 20, "truncate": True, "hide": True}),
                ("created", {"width": 19}),
            ]
            + attrs[idx:]
            + [
                ("content_date", {
                    "label": "date",
                    "width": 10,
                    "truncate": True}
                 )
            ]
        )

    # ATTRIBUTES = AttrDict(
    #     feed={"width": 30, "format_fn": format_feed},
    #     created={"width": 19},
    #     title={"width": ("weight", 1), "truncate": True},
    # )
    # @property
    # def ATTRIBUTES(self):
    #     def format_feed(feed):
    #         return feed.name if hasattr(feed, "name") else ""

    #     return AttrDict(
    #         media_listing_id = {"hide": True},
    #         feed = {"width": 32, "format_fn": format_feed },
    #         created = {"width": 19},
    #         title = {"width": ("weight", 1), "truncate": False},
    #     )

    # @property
    # def RPC_METHODS(self):
    #     return [
    #         ("mark_items_read", self.mark_items_read)
    #     ]

    @property
    def status(self):
        return self.filters["status"].value

    @property
    def apply_subject_filters(self):
        return self.filters["subject"].value

    # @property
    # def search_string(self):
    #     return self.filters["search"].value

    @property
    def channels(self):
        return self.view.channels

    @property
    def feeds(self):
        locators = [c.locator for c in self.view.selected_channels]
        with db_session:
            return [
                self.FEED_CLASS.get(locator=locator)
                for locator in locators
            ]

    @property
    def fetch_limit(self):
        return self.config.fetch_limit or self.DEFAULT_FETCH_LIMIT

    @property
    def translate(self):
        return (self.translate_src and super().translate)

    def create_feeds(self):

        all_channels = list(self.view.all_channels)
        with db_session:
            for channel in all_channels:

                feed = self.FEED_CLASS.get(
                    provider_id=self.IDENTIFIER,
                    locator=channel.locator
                )
                if not feed:
                    feed = self.FEED_CLASS(
                        provider_id=self.IDENTIFIER,
                        locator=channel.locator
                    )
                feed.name = channel.name
                feed.attrs.update(channel.attrs)


            for channel in self.FEED_CLASS.select():
                if channel.locator not in [ c.get_key() for c in  all_channels ]:
                    self.FEED_CLASS[channel.channel_id].delete()


    def feed_attrs(self, feed_name):
        return {}

    @property
    def feed_filters(self):
        return None

    def on_feed_change(self, source, selection):
        self.provider_data["selected_feed"] = [n.identifier for n in selection]
        self.save_provider_data()

    def on_feed_select(self, source, selection):
        if not self.is_active:
            return
        self.update_count = True
        self.reset()

    def on_status_change(self, status, *args):
        if not self.is_active:
            return
        with db_session:
            self.provider_data["selected_status"] = status
            self.save_provider_data()
        self.reset()

    def on_custom_change(self, custom, *args):
        logger.info(f"{custom}, {args}")
        self.custom_filters = custom
        with db_session:
            self.provider_data["selected_filter"] = custom
            self.save_provider_data()
        if not self.is_active:
            return
        self.reset()

    def open_popup(self, text):
        class UpdateMessage(BasePopUp):
            def __init__(self):
                self.text = urwid.Text(text, align="center")
                self.filler = urwid.Filler(self.text)
                super().__init__(self.filler)

            def selectable(self):
                return False

        self.message = UpdateMessage()
        self.view.open_popup(self.message, width=24, height=5)

    def close_popup(self):
        self.view.close_popup()

    async def update(self, force=False, resume=False, replace=False):
        logger.info(f"update: force={force} resume={resume}")
        state.loop.draw_screen()
        # self.open_popup("Updating feeds...")
        # asyncio.create_task(
        count = await self.update_feeds(force=force, resume=resume, replace=replace)
        # )
        # self.close_popup()
        self.reset()
        return count
        # update_task = state.event_loop.run_in_executor(None, update_feeds)

    async def update_feeds(self, force=False, resume=False, replace=False):
        logger.info(f"update_feeds: {force} {resume} {replace}")
        with db_session:
            # feeds = select(f for f in self.FEED_CLASS if f in self.selected_channels)
            # if not self.feed_entity:
            #     feeds = self.FEED_CLASS.select()
            # else:
            #     feeds = [self.feed_entity]

            new = 0
            for feed in self.selected_channels:
                if (force
                    or
                    feed.updated is None
                    or
                    datetime.now() - feed.updated > timedelta(seconds=feed.update_interval)
                ):
                    logger.info(f"updating {feed.locator}")
                    with limit(self.limiter):
                        new += await feed.update(resume=resume, replace=replace)

        return new

    def refresh(self):
        logger.info("+feed provider refresh")
        # self.update_query()
        self.view.refresh()
        # state.loop.draw_screen()
        logger.info("-feed provider refresh")

    def reset(self):
        logger.info("provider reset")
        self.pagination_cursor = None
        self.update_query()
        super().reset()

    def on_activate(self):
        if isinstance(self.view, InvalidConfigView):
            return
        super().on_activate()
        # self.reset()

    # def on_deactivate(self):
    #     if self.view.player:
    #         self.view.quit_player()
    #     super().on_deactivate()

    @property
    def total_item_count(self):
        with db_session:
            return self.all_items_query.count()

    @property
    def feed_item_count(self):
        with db_session:
            return self.feed_items_query.count()

    def filter_config_to_query(self, config):

        OP_MAP = {
            "all": "AND",
            "any": "OR"
        }
        op = OP_MAP.get(config.get("match", "all"))

        return "(" + f" {op} ".join(
            self.filter_rule_to_query(rule) for rule in config["rules"]
        ) + ")"


    def filter_rule_to_query(self, rule):

        BOOL_RE = re.compile(r"\s*([&|])\s*")
        TERM_MAP = {
            "&": "AND",
            "|": "OR"
        }

        OP_MAP = {
            "=~": "regexp",
            "!~": "not regexp",
        }

        def parse_term(term):
            if BOOL_RE.search(term):
                return TERM_MAP.get(term)

            try:
                (attr, op, value) = re.search(
                    r"""(\w+)\s*(\S+)\s*(.*)""", term
                ).groups()
            except ValueError:
                import ipdb; ipdb.set_trace()
            op = OP_MAP.get(op, op)
            if attr == "label":
                attr = "title"
                value = self.rule_config[value].pattern

            if attr in ["title", "content"]:
                sql = f"""lower({attr}) {op} lower('{value.replace("'", "''")}')"""
            else:
                sql = f"""{attr} {op} {value}"""
            return sql

        return "(" + " ".join([
            parse_term(term)
            for term in BOOL_RE.split(rule)
        ]) + ")"


    def apply_filters(self, query, filters):

        for rule in filters:
            sql = self.filter_rule_to_query(rule)
            # logger.info(sql)
            query = query.filter(raw_sql(sql))

        return query

    @db_session
    def update_query(self, sort=None, cursor=None):

        if isinstance(self.view, InvalidConfigView):
            return
        logger.info(f"update_query: {cursor}")
        # import ipdb; ipdb.set_trace()
        status_filters =  {
            "all": lambda: True,
            "unread": lambda i: i.read is None,
            "not_downloaded": lambda i: i.downloaded is None
        }

        self.all_items_query = (
            self.LISTING_CLASS.select()
        )

        def feed_to_filter(feed):
            sql = f"channel = '{feed.channel_id}'"
            feed_config = feed.config.get_value()
            if self.apply_subject_filters and "filters" in feed_config:
                sql += " AND " + self.filter_config_to_query(feed_config["filters"])
                # import ipdb; ipdb.set_trace()
            return sql

        self.feed_items_query = self.all_items_query
        if self.selected_channels:
            self.feed_items_query = self.feed_items_query.filter(
                raw_sql(
                    "("
                    +
                    " OR ".join(
                        [feed_to_filter(feed)
                         for feed in self.selected_channels
                        ])
                    + ")"
                )
            )
        else:
            self.feed_items_query = self.all_items_query

        self.items_query = self.feed_items_query
        if self.feed_filters:
            for f in self.feed_filters:
                self.items_query = self.items_query.filter(f)

        self.items_query = self.items_query.filter(status_filters[self.filters.status.value])

        if self.search_filter:
            (field, query) = re.search("(?:(\w+):)?\s*(.*)", self.search_filter).groups()
            if field == "date":
                try:
                    (after, before) = DATETIME_RANGE_RE.search(
                        query
                    ).groups()
                    if after:
                        d = dateparser.parse(
                            after, settings={"PREFER_DAY_OF_MONTH": "first"}
                        )
                        if d:
                            self.items_query = self.items_query.filter(
                                lambda i: i.created >= d
                            )
                    if before:
                        d = dateparser.parse(
                            before, settings={"PREFER_DAY_OF_MONTH": "first"}
                        )
                        if d:
                            self.items_query = self.items_query.filter(
                                lambda i: i.created <= d
                            )
                except AttributeError:
                    pass

            elif field and field in [a.name for a in self.LISTING_CLASS._attrs_]:
                self.items_query = self.items_query.filter(
                    lambda i: getattr(i, field) == query
                )
            else:
                # raise Exception(query, self.pagination_cursor)
                self.items_query = self.items_query.filter(
                    lambda i: query.lower() in i.title.lower()
                )

        if self.custom_filters:
           self.items_query = self.items_query.filter(
               raw_sql(self.filter_config_to_query(self.custom_filters))
            )

        (sort_field, sort_desc) = sort if sort else self.view.sort_by
        if cursor:
            op = "<" if sort_desc else ">"
            self.items_query = self.items_query.filter(
                raw_sql(f"{sort_field} {op} '{cursor}'")
            )

        if sort_field:
            if sort_desc:
                sort_fn = lambda i: desc(getattr(i, sort_field))
            else:
                sort_fn = lambda i: getattr(i, sort_field)

            # break ties with primary key to ensure pagination cursor is correct
            pk_sort_attr = self.LISTING_CLASS._pk_
            if sort_desc:
                pk_sort_attr = desc(pk_sort_attr)
            # self.items_query = self.items_query.order_by(pk_sort_attr)
            self.items_query = self.items_query.order_by(sort_fn)
            # logger.info(self.items_query.get_sql())
        self.view.update_count = True

    async def apply_search_query(self, query):
        self.pagination_cursor=None
        self.search_filter = query
        self.update_query()
        self.reset()

    def update_fetch_indicator(self, num):
        self.view.update_fetch_indicator(num, self.fetch_limit)

    def show_message(self, message):
        self.view.show_message(message)

    def listings(self, sort=None, cursor=None, offset=None, limit=None, *args, **kwargs):

        count = 0
        # cursor = None
        #
        # if cursor:
        #     import ipdb; ipdb.set_trace()

        if not offset:
            offset = 0

        if not limit:
            limit = self.limit

        with db_session(optimistic=False):

            self.update_query(sort=sort, cursor=cursor)

            for listing in self.items_query[:limit]:
                sources = [
                    source.detach()
                    for source in listing.sources.select().order_by(lambda s: s.rank)
                ]
                listing = listing.detach()
                listing.channel = listing.channel.detach()
                listing.channel.listings = None
                listing.sources = sources

                # get last item's sort key and store it as our pagination cursor
                # cursor = getattr(listing, self.view.sort_by[0])

                # if not listing.check():
                #     logger.debug("listing broken, fixing...")
                #     listing.refresh()
                #     # have to force a reload here since sources may have changed
                #     listing = listing.attach().detach()

                yield listing

        self.update_query(sort=sort, cursor=cursor)

        self.pagination_cursor = cursor
        # self.update_query(cursor, sort=sort)

    @db_session
    async def mark_items_read(self, request):
        media_listing_ids = list(set(request.params))
        logger.info(f"mark_items_read: {media_listing_ids}")
        with db_session:
            try:
                for item in self.LISTING_CLASS.select(
                    lambda i: i.media_listing_id in media_listing_ids
                ):
                    item.read = datetime.now()
                commit()
                self.reset()
            except pony.orm.core.ObjectNotFound:
                logger.info(f("mark_item_read: item {media_listing_id} not found"))

    @property
    def playlist_title(self):
        return f"[{self.IDENTIFIER}]"
        # return f"{self.IDENTIFIER}/{self.feed.locator if self.selected_channels else 'all'}"
