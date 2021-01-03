import logging
logger = logging.getLogger(__name__)

from datetime import datetime
from dataclasses import *
import tempfile
import pipes
import functools

from orderedattrdict import AttrDict
from panwid.datatable import *
from panwid.dialog import *
from panwid.keymap import *
from limiter import get_limiter, limit

from .. import model
from .. import utils

from .base import *

from .widgets import *
from .filters import *



@model.attrclass()
class MediaFeed(model.MediaChannel):
    """
    A subclass of MediaChannel for providers that can distinguish between
    individual broadcasts / episodes / events, perhaps with the abilit to watch
    on demand.
    """

    # FIXME: move to feed.py?

    DEFAULT_FETCH_LIMIT = 100

    DEFAULT_MIN_ITEMS=10
    DEFAULT_MAX_ITEMS=500
    DEFAULT_MAX_AGE=90

    items = Set(lambda: FeedMediaListing, reverse="feed")

    @abc.abstractmethod
    def fetch(self):
        pass

    async def update(self, *args, **kwargs):
        for item in self.fetch(*args, **kwargs):
            # content = [
            #     self.provider.new_media_source(**s).dict(exclude_unset = True, exclude_none = True)
            #     for s in item["content"]
            # ]
            # del item["content"]
            item["sources"] = [
                self.provider.new_media_source(**s).attach()
                for s in item["sources"]
            ]
            listing = self.provider.new_listing(
                **item
            ).attach()
            self.provider.on_new_listing(listing)
            self.updated = datetime.now()
            commit()

    @db_session
    def mark_all_items_read(self):
        for i in self.items.select():
            i.read = datetime.now()

    @classmethod
    @db_session
    def mark_all_feeds_read(cls):
        for f in cls.select():
            for i in f.items.select():
                i.read = datetime.now()

    @db_session
    def reset(self):
        delete(i for i in self.items)
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

    @db_session
    def mark_read(self):
        self.read = datetime.now()

    @db_session
    def mark_unread(self):
        self.read = None

    @db_session
    def mark_part_read(self, index):
        if not "parts_read" in self.attrs:
            self.attrs["parts_read"] = dict()
        self.attrs["parts_read"][str(index)] = True

    @db_session
    def mark_part_unread(self, index):
        self.attrs["parts_read"].pop(str(index), None)

    @property
    def age(self):
        return datetime.now() - self.created

    @property
    def time_since_fetched(self):
        # return datetime.now() - dateutil.parser.parse(self.fetched)
        return datetime.now() - self.fetched

    @property
    def locator(self):
        return self.content

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


@model.attrclass(FeedMediaListingMixin)
class FeedMediaListing(FeedMediaListingMixin, model.MultiSourceMediaListing, model.TitledMediaListing):
    """
    An individual media clip, broadcast, episode, etc. within a particular
    MediaFeed.
    """
    # FIXME: move MediaFeed here?
    feed = Required(lambda: MediaFeed)
    guid = Required(str, index=True)
    created = Required(datetime, default=datetime.now)
    fetched = Required(datetime, default=datetime.now)
    read = Optional(datetime)
    watched = Optional(datetime)
    downloaded = Optional(datetime)
    # was_downloaded = Required(bool, default=False)


@keymapped()
class CachedFeedProviderDataTable(ProviderDataTable):

    class DetailTable(BaseDataTable):

        with_header = False
        # cell_selection = True

        attr_map = {
            # None: "table_row_body",
            "table_row_body focused": "table_row_body highlight",
            # "table_row_body highlight": "table_row_body highlight focused",
            # "unread": "unread highlight column_focused",
            "unread": "unread highlight",
            "unread focused": "unread highlight focused",
        }

        def keypress(self, size, key):
            if key == ".":
                raise Exception(self.selection.__class__.__name__, self.selection.ATTR, self.selection.focus_map)
            else:
                return super().keypress(size, key)

        def row_attr_fn(self, row):
            if not row.get("read"):
                return "unread"
            return None

    class DetailBox(urwid.WidgetWrap):

        def __init__(self, columns, data, *args, **kwargs):
            self.table = CachedFeedProviderDataTable.DetailTable(
            columns=columns,
            data=[dict(
                source,
                **dict(
                    title=f"[{i+1}/{data.source_count}] {data.title}",
                    # title = f"[{i+1}] {data.title}",
                    feed = data.feed,
                    created = data.created,
                    read = data.attrs.get("parts_read", {}).get(i, False)
                ))
                  for i, source in enumerate(data.sources)
            ])
            self.box = urwid.BoxAdapter(self.table, 1)
            # self.pile = urwid.Pile([
            #     (1, urwid.SolidFill(" ")),
            #     # ("pack", urwid.BoxAdapter(self.table, len(data.content)+1))
            #     ("pack", urwid.BoxAdapter(self.table, 1))
            # ])
            # ]
            super().__init__(self.box)

        @property
        def focus_position(self):
            return self.table.focus_position

    signals = ["focus"]

    HOVER_DELAY = 0.25

    with_scrollbar=True
    sort_by = ("created", True)
    index = "media_listing_id"
    # no_load_on_init = True
    detail_auto_open = True
    detail_replace = True
    detail_selectable = True
    with_sidecar = True


    KEYMAP = {
        "any": {
            "home": "first_item",
            "end": "last_item",
            "cursor up": "prev_item",
            "cursor down": "next_item",
            "ctrl r": "reset",
            "ctrl d": "download",
            "n": "next_unread",
            "p": "prev_unread",
            "meta r": ("update", [], {"force": True}),
            "meta f": ("update", [], {"force": True, "resume": True}),
            # "meta f": "fetch_more",
            "meta p": "play_all",
            "f": ["cycle", "fullscreen"],
            "q": "quit_app"
        }
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ignore_blur = False
        self.mark_read_on_focus = False
        self.mark_read_task = None
        self.update_count = True
        self.player = None
        self.player_task = None
        self.queued_task = None
        self.pending_event_task = None
       
        self.change_playlist_pos_on_focus = True
        self.change_focus_on_playlist_pos = True
        urwid.connect_signal(self, "focus", self.on_focus)

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


    def check_parts(self, row):
        return (
            k for k, v in row.attrs.get("parts_read", {}).items() if v
        )

    def row_attr_fn(self, row):
        if not (row.read or list(self.check_parts(row))):
            return "unread"
        return None

    def detail_fn(self, data):

        if data.source_count <= 1:
            return

        columns = self.columns.copy()
        next(c for c in columns if c.name=="title").truncate = True

        box = self.DetailBox(columns, data)
        urwid.connect_signal(box.table, "focus", lambda s, i: self.on_focus(s, self.focus_position))

        def on_inner_focus(source, position):
            logger.info(position)

        return box

    def playlist_pos_to_row(self, pos):
        return self.play_items[pos].row_num

    def row_to_playlist_pos(self, row):
        try:
            media_listing_id = self[row].data.media_listing_id
        except IndexError:
            return None
        try:
            return next(
                n for n, i in enumerate(self.play_items)
                if i.media_listing_id == media_listing_id
            )
        except StopIteration:
            return None

    async def set_playlist_pos(self, pos):
        if not self.player:
            return
        await self.player.command(
            "set_property", "playlist-pos", pos
        )
        # HACK to work around https://github.com/mpv-player/mpv/issues/7247
        #
        # await asyncio.sleep(0.5)
        geom = await self.player.command(
            "get_property", "geometry"
        )
        await self.player.command(
            "set_property", "geometry", geom
        )

    @property
    def inner_table(self):
        if self.selection.details_open and self.selection.details:
            return self.selection.details.contents.table
        return None

    @property
    def inner_focus(self):
        if self.inner_table:
            return self.inner_table.focus_position
        return 0

    def run_queued_task(self):
        if self.pending_event_task:
            state.event_loop.create_task(self.queued_task())
            self.pending_event_task = None

    @db_session
    def on_focus(self, source, position):

        if self.player and len(self):
            try:
                index = self.row_to_playlist_pos(position) + self.inner_focus
            except (StopIteration, AttributeError, TypeError):
                index = self.row_to_playlist_pos(position)

            if index is None:
                return
            
            if self.pending_event_task:
                logger.warn("canceling task")
                self.pending_event_task.cancel()
                delay = 1
            else:
                delay = 0
            self.queued_task = lambda: self.set_playlist_pos(index)

            self.pending_event_task = state.event_loop.call_later(
                delay,
                self.run_queued_task
            )

        if len(self):
            listing = self[position].data_source.attach()
            if listing.on_focus():
                self.invalidate_rows([listing.media_listing_id])
                self.selection.close_details()
                self.selection.open_details()
                self.refresh()

        # FIXME
        # if self.mark_read_on_focus:
        #     self.mark_read_on_focus = False
        #     if self.mark_read_task:
        #         self.mark_read_task.cancel()
        #     self.mark_read_task = state.event_loop.call_later(
        #         self.HOVER_DELAY,
        #         lambda: self.mark_item_read(position)
        #     )


    def on_blur(self, source, position):
        if not self._initialized or self.ignore_blur:
            return
        # if len(self[position].data.content) <= 1:
        #     self[position].close_details()


    @db_session
    def mark_item_read(self, position):
        logger.info(f"mark_item_read: {position}")
        try:
            if not isinstance(self[position].data, model.TitledMediaListing):
                return
        except IndexError:
            return
        row = self[position]
        item = self.item_at_position(position)
        if not item:
            return

        partial = self.inner_table is not None
        # FIXME: HACK until there's a better UI for marking parts read
        # partial = False
        if partial:
            pos = self.inner_focus
            logger.info(f"mark part read: {pos}, {len(self.inner_table)}")
            item.mark_part_read(pos)
            # row.clear_attr("unread")
            self.inner_table.set_value(pos, "read", True)
            self.inner_table[pos].clear_attr("unread")
            logger.info(f"{item.attrs}, {len(self.inner_table)}")
            if pos == len(self.inner_table)-1 and len(list(self.check_parts(item))) == len(self.inner_table):
                partial = False
                logger.info("all parts read")
                item.mark_read()
                row.clear_attr("unread")
                self.set_value(position, "read", item.read)
                # row.close_details()
            else:
                self.inner_table.focus_position += 1
            # self.inner_table.reset()
        else:
            logger.info("mark item read")
            item.mark_read()
            row.clear_attr("unread")
            self.set_value(position, "read", item.read)
            self.invalidate_rows([self[position].data.media_listing_id])
        return partial

    @db_session
    def mark_item_unread(self, position):
        logger.info(f"mark_item_unread: {position}")
        if not isinstance(self[position].data, model.TitledMediaListing):
            return
        item = self.item_at_position(position)
        if not item:
            return

        partial = self.inner_table is not None
        # FIXME: HACK until there's a better UI for marking parts read
        # partial = False
        if partial:
            pos = self.inner_focus
            item.mark_part_unread(pos)
            self.inner_table.set_value(pos, "read", False)
            self.inner_table[pos].set_attr("unread")
        else:
            item.mark_unread()
            self[position].set_attr("unread")
            self.set_value(position, "read", item.read)
        self.invalidate_rows([self[position].data.media_listing_id])


    @db_session
    def toggle_item_read(self, position):
        if not isinstance(self[position].data, model.TitledMediaListing):
            return
        # logger.info(self.get_value(position, "read"))
        if self.get_value(position, "read") is not None:
            self.mark_item_unread(position)
        else:
            self.mark_item_read(position)


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

    @keymap_command
    async def first_item(self):
        self.focus_position = 0

    @keymap_command
    async def last_item(self):
        self.focus_position = len(self)-1

    @keymap_command
    async def next_unread(self):

        idx = None
        count = 0
        last_count = None

        rc = self.mark_item_read(self.focus_position)
        logger.info(rc)
        if rc:
            logger.info("mark was partial")
            return

        while True:
            if count == last_count:
                return
            count += len(self)
            try:
                idx = next(
                    r.data.media_listing_id
                    for r in self[self.focus_position+1:]
                    if not r.data.read
                )
                break
            except StopIteration:
                if len(self) >= self.query_result_count():
                    return
                self.focus_position = len(self)-1
                self.load_more(self.focus_position)
                self.focus_position += 1
                last_count = count
        if idx:
            pos = self.index_to_position(idx)
            logger.info(pos)
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


    @db_session
    def item_at_position(self, position):
        return self.provider.LISTING_CLASS.get(
            guid=self[position].data.get("guid")
        )

    @keymap_command("reset")
    def reset(self, *args, **kwargs):
        logger.info("datatable reset")
        state.foo = state.event_loop.create_task(self.play_all())
        super().reset()

    def refresh(self, *args, **kwargs):
        logger.info("datatable refresh")
        super().refresh(*args, **kwargs)


    @keymap_command()
    async def fetch_more(self):
        await self.update(resume=True)
        # fetch_task = state.event_loop.run_in_executor(None, self.fetch)

    @keymap_command()
    async def update(self, force=False, resume=False):
        await self.provider.update(force=force, resume=resume)


    # @db_session(optimistic=False)
    # async def fetch(self):
    #     # self.provider.feed.update(resume=True)
    #     async def fetch_async():
    #         logger.debug("foo")
    #         await self.provider.update(resume=True)
    #         logger.debug("bar")
    #         # self.refresh()
    #         # logger.debug("baz")
    #         # await self.play_all()
    #         # logger.debug("qux")
    #     asyncio.run(fetch_async())
    #     # asyncio.run(self.play_all())

    @keymap_command()
    async def play_all(self):
        logger.info("play_all")

        ITEM_TEMPLATE="""#EXTINF:1,{title}
{url}
"""
        # raise Exception([
        #     source for (row_num, row, num, source) in [
        #             (row_num, row, num, source) for row_num, row in enumerate(self)
        #             for num, source in enumerate(row.data.content)
        #             # if not source.is_bad
        #     ]
        # ])

        items = [
            AttrDict(
                media_listing_id = row.data.media_listing_id,
                title = utils.sanitize_filename(row.data.title),
                created = row.data.created,
                feed = row.data.feed.name,
                locator = row.data.feed.locator,
                num = num+1,
                row_num = row_num,
                count = len(row.data.sources),
                url = source.locator or source.preview_locator
            )
            for (row_num, row, num, source) in [
                    (row_num, row, num, source) for row_num, row in enumerate(self)
                    for num, source in enumerate(row.data.sources)
                    if not source.is_bad
            ]
        ]

        self.play_items = items

        with tempfile.NamedTemporaryFile(suffix=".m3u8", delete=False) as m3u:
            m3u.write(f"#EXTM3U\n".encode("utf-8"))
            for item in items:
                m3u.write(ITEM_TEMPLATE.format(
                    title = item.title.strip() or "(no title)",
                    url=item.url
                ).encode("utf-8"))
            logger.info(m3u.name)

            listing = self.provider.new_listing(
                title=f"{self.provider.NAME} playlist" + (
                    f" ({self.provider.feed.name}/"
                    if self.provider.feed
                    else " ("
                ) + f"{self.provider.status})",
                sources = [
                    self.provider.new_media_source(
                        url = f"file://{m3u.name}",
                        media_type = "video"
                    )
                ],
                feed = self.provider.feed
            )

        if self.player_task:
            self.player = await self.player_task.program
            sources, kwargs = self.provider.play_args(listing)
            state.event_loop.create_task(self.player_task.load_sources(sources))
        else:
            self.player_task = self.provider.play(listing)
            logger.info(self.player_task)
            self.player = await self.player_task.program
            logger.info(self.player)
            await self.player_task.proc

            async def handle_mpv_key(key_state, key_name, key_string):
                logger.info(f"debug: {key_name}")
                key = self.player.key_to_urwid(key_name)
                logger.debug(f"key: {key_name}, {key}")
                if key in self.KEYMAP.get("any", {}):
                    command = self.KEYMAP["any"][key]
                    try:
                        key_func = getattr(self, command)
                    except (TypeError, AttributeError):
                        key_func = asyncio.coroutine(functools.partial(self.player.command, *command))
                    logger.info(f"command: {command}, key_func: {key_func}")
                    if asyncio.iscoroutinefunction(key_func):
                        await key_func()
                    else:
                        key_func()

            state.event_loop.create_task(
                self.player.controller.register_unbound_key_callback(handle_mpv_key)
            )
            # self.player_command("keybind", "UNMAPPED", "script-binding unampped-keypress")
            def on_player_done(f):
                logger.info("player done")
                self.player = None
                self.player_task = None

            self.player_task.result.add_done_callback(on_player_done)
            # logger.info(urls)


    async def download(self):

        # we could probably rely on focus position here, but let's be safe and
        # go with what MPV has for playlist position first

        if self.player:
            # index = self.player.controller.playlist_pos
            index = await self.player.command(
                    "get_property", "playlist-pos"
            )
            row_num = self.playlist_pos_to_row(index)
        else:
            row_num = self.focus_position

        listing = self[row_num].data_source
        # raise Exception(type(listing.feed), type(listing.attach()).feed)
        # logger.debug(listing)
        url = await self.player.command(
            "get_property", f"playlist/{index}/filename"
        )
        # source = next(
        #     s for s in listing.content
        #     if s.locator == url
        # )
        self.provider.download(listing, index = self.inner_focus or 0)

    def quit_player(self):
        try:
            state.event_loop.create_task(self.player.quit())
        except BrokenPipeError:
            pass


    # FIXME: move to base view
    @keymap_command
    def quit_app(self):
        self.view.quit_app()

    @db_session
    def kill_all(self):
        if not self.provider.feed:
            return
        logger.info(f"killing all messages for {self.provider.feed.locator}")
        self.provider.feed.reset()
        self.reset()

    def player_command(self, *command):
        state.event_loop.create_task(self.player.command(*command))

    def keypress(self, size, key):

        # if key == "meta r":
        #     state.event_loop.create_task(self.provider.update(force=True))
        # elif key == "meta p":
        #     state.event_loop.create_task(self.play_all())
        # elif key == "n":
        #     # self.next_unread()
        #     state.event_loop.create_task(self.next_unread())
        # elif key == "p":
        #     # self.prev_unread()
        #     state.event_loop.create_task(self.prev_unread())
        if key == "A":
            self.mark_all_read()
        elif key == "ctrl a":
            self.mark_visible_read()
        elif key == "meta a":
            self.mark_visible_read(direction=-1)
        elif key == "meta A":
            self.mark_visible_read(direction=1)
        elif key == "m":
            self.toggle_item_read(self.focus_position)
            self.ignore_blur = True
        elif key == "meta ctrl d":
            self.kill_all()
            self.mark_visible_read(direction=-1)
        elif key == "ctrl d":
            state.event_loop.create_task(self.download())
        elif key == "ctrl k":
            self.player_command("quit")
        elif key in self.KEYMAP.get("any", {}) and isinstance(self.KEYMAP.get("any", {})[key], list):
            command = self.KEYMAP["any"][key]
            state.event_loop.create_task(
                self.player.command(*command)
            )
        else:
            return super().keypress(size, key)


    def decorate(self, row, column, value):

        if column.name == "title":
            listing = row.data_source.attach()
            source_count = self.df[listing.media_listing_id, "source_count"]
            if source_count > 1:
                value = f"[{source_count}] {row.get('title')}"

        return super().decorate(row, column, value)



class FeedsFilter(ConfigFilter):

    key = "feeds"
    with_all = True


class ItemStatusFilter(ListingFilter):

    items = AttrDict([
        (s, s.lower().replace(" ", "_"))
        for s in ["All", "Unread", "Not Downloaded"]
    ])

class FeedProvider(BaseProvider):
    """
    A provider that offers multiple feeds to select from
    """

    FILTERS_BROWSE = AttrDict([
        ("feed", FeedsFilter),
    ])

    FILTERS_OPTIONS = AttrDict([
        ("status", ItemStatusFilter)
    ])


    REQUIRED_CONFIG = ["feeds"]

    @property
    def selected_feed_label(self):
        return self.filters.feed.selected_label

    @property
    def selected_feed(self):
        return self.filters.feed.value

    def parse_identifier(self, identifier):
        return (
            None,
            (identifier or self.provider_data.get("selected_feed", None),),
            {}
        )

    #     super().parse_identifier(identifier)

    #     if identifier:
    #         logger.info(f"identifier: {identifier}")
    #         try:
    #             self.filters.feed.selected_label = identifier
    #         except StopIteration:
    #             self.filters.feed.value = identifier

        raise SGIncompleteIdentifier

    def apply_options(self, options):
        if "status" not in options:
            options.status = self.provider_data.get("selected_status", None)
        super().apply_options(options)
        # try:
        #     self.filters.status.value = options.get(
        #         "status",
        #         self.provider_data.get("selected_status", None)
        #     )
        # except (StopIteration, SGException):
        #     pass



class CachedFeedProviderView(SimpleProviderView):

    PROVIDER_DATA_TABLE_CLASS = CachedFeedProviderDataTable


@with_view(CachedFeedProviderView)
class CachedFeedProvider(BackgroundTasksMixin, FeedProvider):

    UPDATE_INTERVAL = (60 * 60 * 4)

    RATE_LIMIT = 5
    BURST_LIMIT = 5

    TASKS = [
        # ("update", UPDATE_INTERVAL, [], {"force": True})
        ("update", UPDATE_INTERVAL)
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.items_query = None
        self.filters["feed"].connect("changed", self.on_feed_change)
        self.filters["status"].connect("changed", self.on_status_change)
        self.game_map = AttrDict()
        self.limiter = get_limiter(rate=self.RATE_LIMIT, capacity=self.BURST_LIMIT)

    def init_config(self):
        super().init_config()
        with db_session(optimistic=False):
            MediaFeed.purge_all(
                min_items = config.settings.profile.cache.min_items,
                max_items = config.settings.profile.cache.max_items,
                max_age = config.settings.profile.cache.max_age
            )



    @property
    def ATTRIBUTES(self):
        def format_feed(feed):
            return feed.name if hasattr(feed, "name") else ""

        return AttrDict(
            media_listing_id = {"hide": True},
            feed = {"width": 32, "format_fn": format_feed },
            created = {"width": 19},
            title = {"width": ("weight", 1), "truncate": False},
        )

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
        if feed:
            self.provider_data["selected_feed"] = feed.locator
        else:
            self.provider_data["selected_feed"] = None
        self.save_provider_data()
        self.view.table.translate_src = getattr(feed, "translate", None)

        if not self.is_active:
            return
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

    async def update(self, force=False, resume=False):
        logger.info(f"update: force={force} resume={resume}")
        self.create_feeds()
        self.open_popup("Updating feeds...")
        await self.update_feeds(force=force)
        self.close_popup()
        self.reset()
        # update_task = state.event_loop.run_in_executor(None, update_feeds)

    async def update_feeds(self, force=False):
        logger.info(f"update_feeds: {force}")
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
                        await feed.update()
                        # f.updated = datetime.now()
                    # commit()


    def refresh(self):
        logger.info("+feed provider refresh")
        self.update_query()
        self.view.table.refresh()
        # state.loop.draw_screen()
        logger.info("-feed provider refresh")

    def reset(self):
        logger.info("provider reset")
        self.update_query()
        self.view.reset()

    def on_activate(self):
        super().on_activate()
        self.refresh()

    def on_deactivate(self):
        if self.view.table.player:
            self.view.table.quit_player()

    @db_session
    def  update_query(self):

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
            self.LISTING_CLASS.select()
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

            for item in self.items_query[offset:offset+limit]:
                listing = item.detach()
                listing.feed = listing.feed.detach()
                listing.feed.items = None
                listing.sources = [ s.detach() for s in listing.sources ]
                # import ipdb; ipdb.set_trace()
                yield (listing, dict(source_count=len(listing.sources)))
                # raise Exception(item.to_dict(related_objects=True, with_collections=True))
                # raise Exception(type(item.detach().sources[0]))
                # listing = self.new_listing(
                #     feed = AttrDict(item.feed.to_dict()),
                #     # **self.LISTING_CLASS.attr_class.from_orm(item).__dict__
                #     **item.to_dict(
                #         exclude=["feed", "classtype"],
                #         related_objects=True
                #     )
                # )
                # listing.content = self.MEDIA_SOURCE_CLASS.schema().loads(listing["content"], many=True)
                # yield(listing)

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
