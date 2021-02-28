import logging
logger = logging.getLogger(__name__)

import re
from datetime import datetime
from dataclasses import *
import functools
import textwrap

from orderedattrdict import AttrDict
from panwid.datatable import *
from panwid.dialog import *
from panwid.keymap import *
from panwid.sparkwidgets import SparkBarWidget
from limiter import get_limiter, limit
from pony.orm import *
import timeago

from .. import model
from .. import utils

from .base import *

from .widgets import *
from .filters import *



@model.attrclass()
class FeedMediaChannel(model.MediaChannel):
    """
    A subclass of MediaChannel for providers that can distinguish between
    individual broadcasts / episodes / events, perhaps with the abilit to watch
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

    async def update(self, *args, **kwargs):

        fetched = 0
        self.provider.update_fetch_indicator(0)
        async for item in self.fetch(limit=self.provider.fetch_limit, *args, **kwargs):
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
                self.provider.on_new_listing(listing)
                self.updated = datetime.now()
                fetched+=1
                self.provider.update_fetch_indicator(fetched)

        self.fetched = datetime.now()

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
        return self.feed.name

    @property
    def feed_locator(self):
        return self.feed.locator

    # FIXME
    @property
    def timestamp(self):
        return self.created.strftime("%Y%m%d_%H%M%S")

    # FIXME
    @property
    def created_timestamp(self):
        return self.created.isoformat().split(".")[0]

    def on_focus(self):
        pass

    @property
    def body(self):
        return self.title

    def check(self):
        return all(s.check() for s in self.sources)

    def refresh(self):
        pass



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
    watched = Optional(datetime)
    downloaded = Optional(datetime)

    # was_downloaded = Required(bool, default=False)
    #
#     @property
#     def read(self):
#         seen = self.sources.seen.select()
#         return all(seen)
# #

class FeedMediaSourceMixin(object):

    def mark_seen(self):
        with db_session:
            self.seen = datetime.now()

    def mark_unseen(self):
        with db_session:
            self.seen = None

    @property
    def uri(self):
        return f"{self.provider.IDENTIFIER}/{self.listing.channel.locator}.{self.listing.guid}"

    @property
    def is_downloaded(self):
        with db_session:
            try:
                source = self.provider.MEDIA_SOURCE_CLASS[self.media_source_id]
            except:
                return False
            # listing = self.provider.LISTING_CLASS.orm_class[self.listing.media_listing_id]
            try:
                return os.path.exists(source.download_filename(listing=source.listing))
            except SGInvalidFilenameTemplate as e:
                logger.error(e)

    def check(self):
        return True

    def refresh(self):
        for s in self.sources:
            s.refresh()


@model.attrclass(FeedMediaSourceMixin)
class FeedMediaSource(FeedMediaSourceMixin, model.MediaSource):

    seen = Optional(datetime)
    created = Required(datetime, default=datetime.now)


class CachedFeedProviderDetailBox(DetailBox):

    def detail_table(self):
        columns = [
            c for c in  self.parent_table._columns.copy()
            if not isinstance(c, DataTableDivider)
        ]
        return CachedFeedProviderDetailDataTable(self.listing, self.parent_table, columns=columns)


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

    def row_attr_fn(self, position, data, row):
        # if not getattr(row, "seen", False):
        with db_session:
            source = FeedMediaSource[data.media_source_id]
        if not source.seen:
            # logger.info("detail unread")
            return "unread"
        return super().row_attr_fn(position, data, row)

    async def mark_selection_seen(self):
        with db_session:
            source = FeedMediaSource[self.selection.data.media_source_id]
            source.mark_seen()
        self.selection.clear_attr("unread")
        # logger.info("mark seen")

    async def mark_selection_unseen(self):
        with db_session:
            source = FeedMediaSource[self.selection.data.media_source_id]
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
        if self.selection:
            await self.mark_selection_seen()

        with db_session:
            # raise Exception(self.listing)
            listing = self.listing.attach()
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

    signals = ["focus", "keypress"]

    HOVER_DELAY = 0.25

    with_scrollbar=True
    sort_by = ("created", True)
    index = "media_listing_id"
    no_load_on_init = True
    detail_auto_open = True
    detail_replace = True
    detail_selectable = True

    KEYMAP = {
        "cursor up": "prev_item",
        "cursor down": "next_item",
        # "ctrl r": "reset",
        # "ctrl d": "download",
        "A": "mark_all_read",
        "ctrl a": "mark_visible_read",
        "meta a": ("mark_visible_read", [-1]),
        "meta A": ("mark_visible_read", [1]),
        "n": "next_unread",
        "N": ("next_unread", [True]),
        "b": "prev_unread",
        "m": "toggle_selection_read",
        "i": "inflate_selection",
        "meta ctrl k": "kill_all",
        "r": ("update", [], {"force": True}),
        "R": ("update", [], {"force": True, "resume": True}),
        # "f": ("update", [], {"force": True, "resume": True}),
        # "F": ("update", [], {"force": True, "resume": True, "replace": True}),
        # "q": "quit_app"
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # self.mark_read_on_focus = False
        # self.mark_read_task = None
        self.update_count = True
        # urwid.connect_signal(self, "requery", self.on_requery)

    def on_requery(self, source, count):
        super().on_requery(source, count)
        self.update_count = True

    # def detail_box(self):
    #    return CachedFeedProviderDetailBox(self)

    def detail_table(self, *args, **kwargs):
        return self.CachedFeedProvideDetailTable(self, *args, **kwargs)

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

    def unseen_sources(self, row):
        with db_session:
            listing = row.attach()
            # logger.info(listing)
            # logger.info(len([s for s in listing.sources if not s.seen]))
            return len([s for s in listing.sources if not s.seen])

    def row_attr_fn(self, position, data, row):
        return "unread" if not data.read else super().row_attr_fn(position, data, row)

    @keymap_command()
    async def inflate_selection(self):
        with db_session:
            listing = self.selection.data_source.attach()
            if await listing.inflate(force=True):
                # position = self.focus_position
                self.invalidate_rows([listing.media_listing_id])
                self.selection.close_details()
                self.selection.open_details()
                self.refresh()
                # self.focus_position = position

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


    @db_session
    def mark_item_read(self, position):
        logger.debug(f"mark_item_read: {position}")

        row = self[position]
        item = self.item_at_position(position)
        if not item:
            return
        item.mark_read()
        row.clear_attr("unread")
        self.set_value(position, "read", item.read)
        self.invalidate_rows([self[position].data.media_listing_id])
        # return self.inner_table is not None

    @db_session
    def mark_item_unread(self, position):
        logger.debug(f"mark_item_unread: {position}")
        # if not isinstance(self[position].data, model.TitledMediaListing):
        #     return
        item = self.item_at_position(position)
        if not item:
            return
        item.mark_unread()
        self[position].set_attr("unread")
        self.set_value(position, "read", item.read)
        self.invalidate_rows([self[position].data.media_listing_id])

        # partial = self.inner_table is not None
        # # FIXME: HACK until there's a better UI for marking parts read
        # # partial = False
        # if partial:
        #     pos = self.inner_focus
        #     item.mark_part_unread(pos)
        #     self.inner_table.set_value(pos, "read", False)
        #     self.inner_table[pos].set_attr("unread")
        # else:
        #     item.mark_unread()
        #     self[position].set_attr("unread")
        #     self.set_value(position, "read", item.read)
        # self.invalidate_rows([self[position].data.media_listing_id])


    @db_session
    def toggle_item_read(self, position):
        # if not isinstance(self[position].data, model.TitledMediaListing):
        #     return
        # logger.info(self.get_value(position, "read"))
        if position >= len(self):
            return
        if self[position].data_source.read:
        # if self.get_value(position, "read") is not None:
            self.mark_item_unread(position)
        else:
            self.mark_item_read(position)
        # self.invalidate_rows([self[position].data.media_listing_id])

    @keymap_command
    def toggle_selection_read(self):
        logger.info("toggle_selection_read")
        self.toggle_item_read(self.focus_position)

    @keymap_command
    def mark_all_read(self):
        with db_session:
            if self.provider.feed:
                self.provider.feed.mark_all_items_read()
            else:
                self.provider.FEED_CLASS.mark_all_feeds_read()
        self.reset()


    def mark_visible_read(self, direction=None):
        for n, item in enumerate(self):
            if direction and (
                    direction < 0 and n > self.focus_position
                    or direction> 0 and n < self.focus_position
            ):
                continue
            self.mark_item_read(n)
        self.reset()

    @keymap_command
    async def prev_item(self):
        if self.focus_position > 0:
            self.focus_position -= 1

    @keymap_command
    async def next_item(self):
        if self.focus_position < len(self)-1:
            self.focus_position += 1

    @staticmethod
    def is_unread(listing):
        return not listing.data_source.attach().read

    @keymap_command
    async def next_unread(self, no_sources=False):
        return await self.next_matching(self.is_unread, no_sources=no_sources)

    async def next_matching(self, predicate, no_sources=False):
        # FIXME: this is sort of a mish-mash between a general purpose
        # function and one particular to marking read and moving to the next
        # unread.  Will require some cleanup if it's used for other purposes.

        idx = None
        count = 0
        last_count = None

        if not self.selection:
            return

        with db_session:
            for i, s in enumerate(self.selection.data.sources):
            # # now = datetime.now()
                source = FeedMediaSource[s.media_source_id]
                # logger.info(f"{i}, {source}")
                # source.attach().read = now
                source.mark_seen()
                # commit()
                if len(self.selection.data.sources) > 1:
                    logger.info(f"{len(self.selection.data.sources)}, {len(self.selection.details.contents.table)}")
                    self.selection.details.contents.table[i].clear_attr("unread")
                # else:
                #     self.selection.data_source.attach().read = now
            self.selection.close_details()
            self.selection.clear_attr("unread")

        rc = self.mark_item_read(self.focus_position)

        try:
            idx = next(
                r.data.media_listing_id
                for r in self[self.focus_position+1:]
                if predicate(r)
            )
        except (StopIteration, AttributeError):
            if self.focus_position == len(self)-1:
                updated = self.load_more(self.focus_position)
                if updated:
                    if self.focus_position < len(self) - 1:
                        self.focus_position += 1
                else:
                    await self.update(force=True, resume=True)

            else:
                self.focus_position = len(self)-1
        if idx:
            pos = self.index_to_position(idx)
            logger.info(pos)
            focus_position_orig = self.focus_position
            self.focus_position = pos
            self.mark_read_on_focus = True
            self._modified()


    @keymap_command
    async def prev_unread(self):
        try:
            idx = next(
                r.data.media_listing_id
                for r in self[self.focus_position-1::-1]
                if not r.data.read
            )
            self.mark_item_read(self.focus_position)
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

    # def refresh(self, *args, **kwargs):
    #     logger.info("datatable refresh")
    #     super().refresh(*args, **kwargs)

    @keymap_command
    async def update(self, force=False, resume=False, replace=False):
        self.provider.view.footer.show_message("Updating...")
        await self.provider.update(force=force, resume=resume, replace=replace)
        self.reset()

    # # FIXME: move to base view
    # @keymap_command
    # def quit_app(self):
    #     self.view.quit_app()

    @db_session
    def kill_all(self):
        if not self.provider.feed:
            return
        logger.info(f"killing all messages for {self.provider.feed.locator}")
        self.provider.feed.reset()
        self.reset()

    def keypress(self, size, key):
        return super().keypress(size, key)


class FeedsFilter(ConfigFilter):

    key = "feeds"
    with_all = True


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

    @property
    def FILTERS_OPTIONS(self):
        return AttrDict([
            ("status", ItemStatusFilter),
            ("search", TextFilter)
        ],**super().FILTERS_OPTIONS)

    @property
    def selected_feed_label(self):
        return self.filters.feed.selected_label

    @property
    def default_filter_values(self):

        return AttrDict(
            feed=self.provider_data.get("selected_feed", None),
            status=self.provider_data.get("selected_status", None)
        )

    @property
    def selected_feed(self):
        return self.filters.feed.value

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

        raise SGIncompleteIdentifier

    def apply_options(self, options):
        if "status" not in options:
            options.status = self.provider_data.get("selected_status", None)
        super().apply_options(options)


class CachedFeedProviderFooter(urwid.WidgetWrap):

    def __init__(self, parent):

        self.parent = parent
        self.indicator_placeholder = urwid.WidgetPlaceholder(urwid.Text(""))
        self.message_placeholder = urwid.WidgetPlaceholder(urwid.Text(""))
        self._width = None
        self.filler = urwid.Filler(
            urwid.AttrMap(
                urwid.Columns([
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
            self.update_status_indicator()
        return super().render(size, focus=focus)

    def update(self, count=0):

        self.update_status_indicator(count)
        self.update_count()

    def update_fetch_indicator(self, num, count):

        spark_vals = [
            (num, "light green", ("{value}", "black", ">")),
            (count-num, "dark green", (count, "black", ">"))
        ]
        indicator_widget = SparkBarWidget(
            spark_vals,
            int(self._width *(2/3)),
            fit_label=True
            # min_width=3
        )
        self.set_indicator_widget(indicator_widget)
        state.loop.draw_screen()

    def update_status_indicator(self, count=0):

        if not (count and self._width):
            self.set_indicator_widget(urwid.Text(""))
            return

        spark_vals = [
            (func(), attr, (f"{label}{self.parent.footer_attrs[name](): >3}", "black", ">" if i else "<"))
            for i, (name, label, attr, func) in enumerate(self.parent.indicator_bars)
        ]
        # logger.error(spark_vals)
        # if not len(spark_vals):
        #     self.set_indicator_widget(urwid.Text(""))
        #     return
        self.set_indicator_widget(
            SparkBarWidget(
                spark_vals,
                int(self._width *(2/3))-1, # FIXME: urwid/urwid#225 strikes again
                fit_label=True
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

    def set_indicator_widget(self, widget):
        self.indicator_placeholder.original_widget = widget



class CachedFeedProviderBodyView(urwid.WidgetWrap):

    signals = ["select", "cycle_filter", "keypress"]

    def __init__(self, provider, body):
        self.provider = provider
        self.body = body
        self.detail = urwid.WidgetPlaceholder(urwid.Filler(urwid.Text("")))
        self.footer = CachedFeedProviderFooter(self)
        self.pile = urwid.Pile([
            ("weight", 4, self.body),
            (1, urwid.SolidFill(u"\N{BOX DRAWINGS LIGHT HORIZONTAL}")),
            ("weight", 1, self.detail),
            (1, self.footer),
        ])

        super().__init__(self.pile)
        self.pile.focus_position = 0
        urwid.connect_signal(self.body, "keypress", lambda *args: self._emit(*args))
        urwid.connect_signal(self.body, "focus", self.on_focus)

    @property
    def footer_attrs(self):
        if self.provider.feed:
            return AttrDict([
                ("refreshed", lambda: timeago.format(self.provider.feed.fetched, datetime.now())),
                ("updated", lambda: timeago.format(self.provider.feed.updated, datetime.now())),
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
            ("selected", "", "dark green",
             lambda: self.footer_attrs["selected"]()),
            ("shown", "👓", "dark blue",
             lambda: self.footer_attrs["shown"]()),
            ("matching", "✓", "light blue",
             # lambda: self.footer_attrs["matching"]() - self.footer_attrs["shown"]()),
             lambda: self.footer_attrs["matching"]()),
            ("fetched", "↓", "dark red",
             # lambda: self.footer_attrs["fetched"]() - self.footer_attrs["matching"]()),
             lambda: self.footer_attrs["fetched"]()),
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
                urwid.AttrMap(row, {"table_row_body": "table_row_header"}),
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
        if not self.provider.feed:
            return
        self.footer.update_fetch_indicator(num, count)


@keymapped()
class CachedFeedProviderView(SimpleProviderView):

    KEYMAP = {
        "ctrl e": ("focus_filter", ["feed"]),
        "ctrl s": ("focus_filter", ["status"]),
    }

    def __init__(self, provider, body):
        self.provider = provider
        self.body = body
        super().__init__(self.provider, self.body)

    def keypress(self, size, key):
        return super().keypress(size, key)

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
        self.items_query = None
        self.filters["feed"].connect("changed", self.on_feed_change)
        self.filters["status"].connect("changed", self.on_status_change)
        self.pagination_cursor = None
        self.game_map = AttrDict()
        self.limiter = get_limiter(rate=self.RATE_LIMIT, capacity=self.BURST_LIMIT)

    @property
    def VIEW(self):
        return CachedFeedProviderView(self, CachedFeedProviderBodyView(self, CachedFeedProviderDataTable(self)))

    def init_config(self):
        super().init_config()
        if config.settings.profile.cache.max_age > 0:
            with db_session(optimistic=False):
                FeedMediaChannel.purge_all(
                    min_items = config.settings.profile.cache.min_items,
                    max_items = config.settings.profile.cache.max_items,
                    max_age = config.settings.profile.cache.max_age
                )

    def format_feed(feed):
        return feed.name if hasattr(feed, "name") else ""

    ATTRIBUTES = AttrDict(
        media_listing_id = {"hide": True},
        feed = {"width": 30, "format_fn": format_feed },
        created = {"width": 19},
        title = {"width": ("weight", 1), "truncate": True},
    )
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

    @property
    def RPC_METHODS(self):
        return [
            ("mark_items_read", self.mark_items_read)
        ]

    @property
    def status(self):
        return self.filters["status"].value


    @property
    def feed(self):
        if not self.selected_feed:
            return None
        with db_session:
            feed = self.FEED_CLASS.get(
                provider_id = self.IDENTIFIER,
                locator = self.selected_feed.locator
            )
        return feed

    # @property
    # def search_string(self):
    #     return self.filters["search"].value

    @property
    def feeds(self):
        return AttrDict([
            FeedConfig.from_kv(k, v)
            for k, v in self.config.feeds.items()
        ])
        # return self.filters.feed.items

        # if isinstance(self.config.feeds, dict):
        #     return self.config.feeds
        # else:
        #     return AttrDict([
        #         reversed(list(f.items())[0]) if isinstance(f, dict) else (f, f)
        #         for f in self.config.feeds
        #     ])


    @property
    def fetch_limit(self):
        return self.config.fetch_limit or self.DEFAULT_FETCH_LIMIT

    @property
    def translate(self):
        return (self.translate_src and super().translate)

    @property
    def translate_src(self):
        if not self.feed:
            return None
        cfg = self.config.feeds[self.feed.locator]
        if cfg and isinstance(cfg, AttrDict):
            return getattr(cfg, "translate", "auto")
        return None

    def create_feeds(self):
        with db_session:
            for n, f in self.feeds.items():
                feed = self.FEED_CLASS.get(locator=f.locator)
                if not feed:
                    feed = self.FEED_CLASS(
                        provider_id = self.IDENTIFIER,
                        name = n,
                        locator= f.locator
                        # **self.feed_attrs(name)
                    )
                    commit()

    def feed_attrs(self, feed_name):
        return {}

    @property
    def feed_filters(self):
        return None

    def on_feed_change(self, feed):
        if feed and hasattr(feed, "locator"):
            self.provider_data["selected_feed"] = feed.locator
        else:
            self.provider_data["selected_feed"] = None
        self.save_provider_data()

        if not self.is_active:
            return
        self.update_count = True
        self.reset()

    def on_status_change(self, status, *args):
        self.provider_data["selected_status"] = status
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
        await self.update_feeds(force=force, resume=resume, replace=replace)
        # )
        # self.close_popup()
        self.reset()
        # update_task = state.event_loop.run_in_executor(None, update_feeds)

    async def update_feeds(self, force=False, resume=False, replace=False):
        logger.info(f"update_feeds: {force} {resume}")
        with db_session:
            if not self.feed:
                feeds = self.FEED_CLASS.select()
            else:
                feeds = [self.feed]

            for feed in feeds:
                if (force
                    or
                    feed.updated is None
                    or
                    datetime.now() - feed.updated > timedelta(seconds=feed.update_interval)
                ):
                    logger.info(f"updating {feed.locator}")
                    with limit(self.limiter):
                        await feed.update(resume=resume, replace=replace)
                        # f.updated = datetime.now()
                    # commit()
                    #
        self.reset()


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
        super().on_activate()
        self.create_feeds()
        self.reset()

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

    @db_session
    def update_query(self, search_filter=None):

        logger.info("update_query")
        status_filters =  {
            "all": lambda: True,
            "unread": lambda i: i.read is None,
            "not_downloaded": lambda i: i.downloaded is None
        }

        self.all_items_query = (
            self.LISTING_CLASS.select()
        )

        if self.feed:
            self.feed_items_query = self.all_items_query.filter(
                lambda i: i.channel == self.feed
            )
        else:
            self.feed_items_query = self.all_items_query

        self.items_query = self.feed_items_query

        if self.feed_filters:
            for f in self.feed_filters:
                self.items_query = self.items_query.filter(f)

        self.items_query = self.items_query.filter(status_filters[self.filters.status.value])

        if search_filter:
            (field, query) = re.search("(?:(\w+):)?(.*)", search_filter).groups()
            if field and field in [a.name for a in self.LISTING_CLASS._attrs_]:
                self.items_query = self.items_query.filter(
                    lambda i: getattr(i, field) == query
                )
            else:
                self.items_query = self.items_query.filter(
                    lambda i: query.lower() in i.title.lower()
                )

        (sort_field, sort_desc) = self.view.sort_by

        if self.pagination_cursor:
            # raise Exception(self.pagination_cursor)

            op = "<" if sort_desc else ">"
            self.items_query = self.items_query.filter(
                raw_sql(f"{sort_field} {op} '{self.pagination_cursor}'")
            )

        if sort_field:
            sort_fn = lambda i: desc(getattr(i, sort_field))
            if sort_desc:
                sort_fn = desc(sort_fn)

            # break ties with primary key to ensure pagination cursor is correct
            pk_sort_attr = self.LISTING_CLASS._pk_
            if sort_desc:
                pk_sort_attr = desc(pk_sort_attr)
            self.items_query = self.items_query.order_by(pk_sort_attr)
            self.items_query = self.items_query.order_by(sort_fn)

        self.view.update_count = True

    def apply_search_query(self, query):
        self.update_query(query)
        self.refresh()

    def update_fetch_indicator(self, num):
        self.view.update_fetch_indicator(num, self.fetch_limit)

    def show_message(self, message):
        self.view.show_message(message)

    def listings(self, offset=None, limit=None, *args, **kwargs):

        count = 0
        cursor = None

        if not offset:
            offset = 0

        if not limit:
            limit = self.limit

        with db_session(optimistic=False):

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
                cursor = getattr(listing, self.view.sort_by[0])
                if not listing.check():
                    logger.debug("listing broken, fixing...")
                    listing.refresh()
                    # have to force a reload here since sources may have changed
                    listing = listing.attach().detach()
                yield listing

        self.pagination_cursor = cursor
        self.update_query()

    @db_session
    def mark_items_read(self, request):
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
        # return f"[{self.provider}]"
        return f"{self.IDENTIFIER}/{self.feed.locator if self.feed else 'all'}"
