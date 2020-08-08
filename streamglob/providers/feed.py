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
from fysom import Fysom

from .. import model

from .base import *

from .widgets import *
from .filters import *

@dataclass
class FeedMediaListing(model.ContentMediaListing):

    media_item_id: int = None
    guid: str = None
    media_type: str = None
    feed: model.MediaFeed = None
    read: bool = False
    fetched: bool = False
    downloaded: bool = False
    watched: bool = False
    attrs: dict = field(default_factory=dict)

    # @property
    # def locator(self):
    #     return self.content

    @property
    def feed_name(self):
        return self.feed.name

    @property
    def feed_locator(self):
        return self.feed.locator

    @property
    def timestamp(self):
        return self.created.strftime("%Y%m%d_%H%M%S")


# class URLFeed(model.MediaFeed):

#     url = Required(str)


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
                c,
                **dict(
                    title=f"[{i+1}/{len(data.content)}] {data.title}",
                    # title = f"[{i+1}] {data.title}",
                    feed = data.feed,
                    created = data.created,
                    read = data.attrs.get("parts_read", {}).get(i, False)
                ))
                  for i, c in enumerate(data.content)
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
    index = "media_item_id"
    # no_load_on_init = True
    detail_auto_open = True
    detail_replace = True
    detail_selectable=True

    KEYMAP = {
        "any": {
            "ctrl r": "reset",
            "ctrl d": "download",
            "n": "next_unread",
            "p": "prev_unread",
            "meta f": "fetch_more",
            "f": ["cycle", "fullscreen"]
        }
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ignore_blur = False
        self.mark_read_on_focus = False
        self.mark_read_task = None
        self.update_count = True
        self.player = None
        self.player_state_task = None
        self.player_state = Fysom(
            events=[
                ("file_loaded", "init", "ready"),
                ("wait", "ready", "waiting"),
                ("reset", "waiting", "ready")
            ], initial="init"
        )
        self.reset_player_state_task = None
        self.pending_event_task = None
        self.change_playlist_pos_on_focus = True
        self.change_focus_on_playlist_pos = True
        urwid.connect_signal(self, "focus", self.on_focus)
        def on_requery(source, count):
            # logger.info("foo: %s" %(c))
            if not self.player:
                return
            state.event_loop.create_task(self.play_all())
        urwid.connect_signal(self, "requery", on_requery)
        # urwid.connect_signal(self, "blur", self.on_blur)

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
        if not (row.get("read") or list(self.check_parts(row))):
            return "unread"
        return None

    def detail_fn(self, data):

        if len(data.content) <= 1:
            return

        columns = self.columns.copy()
        next(c for c in columns if c.name=="title").truncate = True

        box = self.DetailBox(columns, data)
        urwid.connect_signal(box.table, "focus", lambda s, i: self.on_focus(s, self.focus_position))

        # table = self.DetailTable(
        #     columns=columns,
        #     data=[dict(
        #         c,
        #         **dict(
        #             # title=f"[{i+1}/{len(data.content)}] {data.title}"
        #             title = f"[{i+1}] {data.title}",
        #             feed = data.feed,
        #             created = data.created,
        #             read = data.attrs.get("parts_read", {}).get(i, False)
        #         ))
        #           for i, c in enumerate(data.content)
        #     ])

        # box = urwid.BoxAdapter(urwid.Filler(table, valign="bottom"), len(data.content)+2)
        def on_inner_focus(source, position):
            logger.info(position)

        return box

    def playlist_pos_to_row(self, pos):
        return self.play_items[pos].row_num

    def row_to_playlist_pos(self, row):
        try:
            media_item_id = self[row].data.media_item_id
        except IndexError:
            return None
        try:
            return next(
                n for n, i in enumerate(self.play_items)
                if i.media_item_id == media_item_id
            )
        except StopIteration:
            return None

    async def set_playlist_pos(self, pos):
        await self.player.controller.command(
            "set_property", "playlist-pos", pos
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

    def reset_player_state(self):
        if self.player_state.can("reset"):
            logger.info("reset")
            self.player_state.reset()
        if self.pending_event_task:
            state.event_loop.create_task(self.pending_event_task())
            self.pending_event_task = None

    @db_session
    def on_focus(self, source, position):
        if not self._initialized:
            return

        if self.player and len(self):
            try:
                index = self.row_to_playlist_pos(position) + self.inner_focus
            except (StopIteration, AttributeError, TypeError):
                logger.info("on_focus no inner_focus")
                index = self.row_to_playlist_pos(position)
            logger.info(f"on_focus: {self.player_state.current}, {position}, {index}")

            if index is None:
                return
            if self.player_state.current == "ready":
                try:
                    self.player_state.wait()
                    state.event_loop.create_task(
                        self.set_playlist_pos(index)
                    )
                    self.reset_player_state()
                    # if self.reset_player_state_task:
                    #     self.reset_player_state_task.cancel()
                    # self.reset_player_state_task = state.event_loop.call_later(
                    #     5,
                    #     self.reset_player_state
                    # )

                except BrokenPipeError:
                    return
                except StopIteration:
                    return
            else:
                self.pending_event_task = lambda: self.set_playlist_pos(index)

        if self.mark_read_on_focus:
            self.mark_read_on_focus = False
            if self.mark_read_task:
                self.mark_read_task.cancel()
            self.mark_read_task = state.event_loop.call_later(
                self.HOVER_DELAY,
                lambda: self.mark_item_read(position)
            )

        # if len(self[position].data.content) > 1:
        #     self[position].open_details()


    async def on_playlist_pos(self, name, value):
        async def set_focus_position(index):
            self.focus_position = index

        row = self.playlist_pos_to_row(value)
        try:
            index = row + self.inner_focus
        except AttributeError:
            logger.info("on_playlist_pos no inner_focus")
            index = row
        logger.info(f"on_playlist_pos: {value}, {index}")
        if self.player:
            if self.player_state.can("file_loaded"):
                self.player_state.file_loaded()

            if self.player_state.current == "ready":
                if self.focus_position != row:
                    state.event_loop.create_task(set_focus_position(index))
            else:
                self.pending_event_task = lambda: set_focus_position(index)


    def on_blur(self, source, position):
        if not self._initialized or self.ignore_blur:
            return
        # if len(self[position].data.content) <= 1:
        #     self[position].close_details()


    @db_session
    def mark_item_read(self, position):
        logger.info(f"mark_item_read: {position}")
        try:
            if not isinstance(self[position].data, model.MediaListing):
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
            logger.info("mark part read")
            pos = self.inner_focus
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
            self.invalidate_rows([self[position].data.media_item_id])
        return partial

    @db_session
    def mark_item_unread(self, position):
        logger.info(f"mark_item_unread: {position}")
        if not isinstance(self[position].data, model.MediaListing):
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
        self.invalidate_rows([self[position].data.media_item_id])


    @db_session
    def toggle_item_read(self, position):
        if not isinstance(self[position].data, model.MediaListing):
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
    async def next_unread(self):
        rc = self.mark_item_read(self.focus_position)
        logger.info(rc)
        if rc:
            logger.info("mark was partial")
            return
        while True:
            try:
                idx = next(
                    r.data.media_item_id
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
        pos = self.index_to_position(idx)
        logger.info(pos)
        self.focus_position = pos
        self.mark_read_on_focus = True
        self._modified()


    @keymap_command
    async def prev_unread(self):
        try:
            idx = next(
                r.data.media_item_id
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
        return self.provider.ITEM_CLASS.get(
            guid=self[position].data.get("guid")
        )

    @keymap_command("reset")
    def reset(self, *args, **kwargs):
        # if self.player:
        #     self.quit_player()
        super().reset()
        if state.event_loop.is_running():
            asyncio.create_task(self.play_all())


    # Feed providers that can fetch older items can implement this
    @keymap_command()
    async def fetch_more(self):
        pass

    async def play_all(self):
        logger.info("play_all")

        ITEM_TEMPLATE="""#EXTINF:1,{title}
{url}
"""
        # items = list(chain.from_iterable(
        #     item.data
        #     for item in self
        # ))
        items = [
            AttrDict(
                media_item_id=row.data.media_item_id,
                title=row.data.title.replace("\n", " "),
                created=row.data.created,
                feed=row.data.feed.name,
                locator=row.data.feed.locator,
                num=num+1,
                row_num=row_num,
                count=len(row.data.content),
                url=source.url
            )
            for (row_num, row, num, source) in [
                    (row_num, row, num, source) for row_num, row in enumerate(self)
                    for num, source in enumerate(row.data.content)
                    if not source.is_bad
            ]
        ]

        if not len(items):
            return

        self.play_items = items
        # raise Exception(items)

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
                content=self.provider.new_media_source(
                    f"file://{m3u.name}",
                    media_type = "video"
                ),
                feed = self.provider.feed
            )
        if self.player_state_task:
            sources, kwargs = self.provider.play_args(listing)
            asyncio.create_task(self.player_state_task.load_sources(sources))
        else:
            self.player_state_task = self.provider.play(listing)
            logger.info(self.player_state_task)
            self.player = await self.player_state_task.program
            logger.info(self.player)
            await self.player_state_task.proc
            # FIXME: MPV-only
            self.player_state.current = "init"
            # self.player.controller.listen_for("file-loaded", self.on_file_loaded)
            async def bind_mpv_key(key, fname):
                await self.player.bind_key_press(key, getattr(self, fname))

            # self.player.bind_property_observer("playlist-pos", self.on_playlist_pos)
            # for key, fname in self.KEYMAP["any"].items():
            #     mpkey = key.replace("ctrl ", "ctrl+").replace("meta ", "alt+")
            #     state.event_loop.create_task(bind_mpv_key(mpkey, fname))

            # self.player.bind_key_press("ctrl+d", self.download)

            async def handle_mpv_key(key_state, key_name, key_string):
                logger.info(f"handle: {key_name}")
                if key_name in self.KEYMAP.get("any", {}):
                    command = self.KEYMAP["any"][key_name]
                    try:
                        key_func = getattr(self,command)
                    except (TypeError, AttributeError):
                        key_func = functools.partial(self.player.controller.command, *command)
                    await key_func()

            state.event_loop.create_task(
                self.player.controller.register_unbound_key_callback(handle_mpv_key)
            )
            # self.player_command("keybind", "UNMAPPED", "script-binding unampped-keypress")
            def on_player_done(f):
                logger.info("player done")
                self.player = None
                self.player_state_task = None
                self.player_state.current = "init"

            self.player_state_task.result.add_done_callback(on_player_done)
            # logger.info(urls)

    async def download(self):

        # we could probably rely on focus position here, but let's be safe and
        # go with what MPV has for playlist position first

        if self.player:
            # index = self.player.controller.playlist_pos
            index = await self.player.controller.command(
                    "get_property", "playlist-pos"
            )
            row_num = self.playlist_pos_to_row(index)
        else:
            row_num = self.focus_position

        listing = self[row_num].data
        logger.info(listing)
        url = await self.player.controller.command(
            "get_property", f"playlist/{index}/filename"
        )
        source = next(
            s for s in listing.content
            if s.locator == url
        )
        self.provider.download(listing)

    def quit_player(self):
        try:
            self.player.quit()
        except BrokenPipeError:
            pass

    @db_session
    def kill_all(self):
        if not self.provider.feed:
            return
        logger.info(f"killing all messages for {self.provider.feed.locator}")
        self.provider.feed.reset()
        self.reset()

    def player_command(self, *command):
        state.event_loop.create_task(self.player.controller.command(*command))

    def keypress(self, size, key):

        if key == "meta r":
            asyncio.create_task(self.provider.update(force=True))
        elif key == "meta p":
            asyncio.create_task(self.play_all())
        # elif key == "n":
        #     # self.next_unread()
        #     state.event_loop.create_task(self.next_unread())
        # elif key == "p":
        #     # self.prev_unread()
        #     state.event_loop.create_task(self.prev_unread())
        elif key == "A":
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
        else:
            return super().keypress(size, key)
        return key


    def decorate(self, row, column, value):
        if column.name == "title" and len(row.get("content")) > 1:
            value = f"[{len(row.get('content'))}] {row.get('title')}"

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
        if identifier:
            try:
                self.filters.feed.selected_label = identifier
            except StopIteration:
                self.filters.feed.value = identifier

        elif self.provider_data["selected_feed"]:
            self.filters.feed.value = self.provider_data["selected_feed"]

        raise SGIncompleteIdentifier


class CachedFeedProviderView(SimpleProviderView):

    PROVIDER_DATA_TABLE_CLASS = CachedFeedProviderDataTable


@with_view(CachedFeedProviderView)
class CachedFeedProvider(BackgroundTasksMixin, FeedProvider):

    UPDATE_INTERVAL = (60 * 60 * 4)

    RATE_LIMIT = 5
    BURST_LIMIT = 5

    TASKS = [
        # ("update", UPDATE_INTERVAL, [], {"force": True})
        ("update", UPDATE_INTERVAL, [])
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.items_query = None
        self.filters["feed"].connect("changed", self.on_feed_change)
        self.filters["status"].connect("changed", self.on_status_change)
        self.game_map = AttrDict()
        self.limiter = get_limiter(rate=self.RATE_LIMIT, capacity=self.BURST_LIMIT)

    @property
    def ITEM_CLASS(self):
        return self.FEED_CLASS.ITEM_CLASS

    @property
    def ATTRIBUTES(self):
        return AttrDict(
            media_item_id = {"hide": True},
            feed = {"width": 32, "format_fn": lambda f: f.name if hasattr(f, "name") else ""},
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

    @db_session
    def create_feeds(self):
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

    @db_session
    def update_feeds(self, force=False):
        logger.info(f"update_feeds: {force}")
        if not self.feed:
            feeds = self.FEED_CLASS.select()
        else:
            feeds = [self.feed]

        for f in feeds:
            if (force
                or
                f.updated is None
                or
                datetime.now() - f.updated > timedelta(seconds=f.update_interval)
            ):
                logger.info(f"updating {f.locator}")
                with limit(self.limiter):
                    f.update()
                    f.updated = datetime.now()
                    commit()
                    # logger.info(f"update {f}")
                    # for item in f.update():
                    #     # listing = self.item_to_listing(item)
                    #     # print(item)
                    #     listing = self.new_listing(
                    #         # feed = f.to_dict(),
                    #         **item.to_dict(
                    #             exclude=["media_item_id", "feed", "classtype"],
                    #             related_objects=True
                    #         )
                    #     )
                    #     listing.content = self.MEDIA_SOURCE_CLASS.schema().loads(listing["content"], many=True)

                    #     self.on_new_listing(listing)
                    #     # raise Exception(listing)

    @property
    def feed_filters(self):
        return None

    def on_feed_change(self, *args):
        if self.feed:
            self.provider_data["selected_feed"] = self.feed.locator
        else:
            self.provider_data["selected_feed"] = None

        self.save_provider_data()
        self.view.table.translate_src = getattr(args[0], "translate", None)
        self.reset()

    def on_status_change(self, *args):
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

    # @db_session
    async def update(self, force=False):
        logger.info(f"update: {force}")
        self.refresh()
        self.create_feeds()
        # state.loop.draw_screen()
        def update_feeds():
            self.open_popup("Updating feeds...")
            self.update_feeds(force=force)
            self.refresh()
            self.close_popup()
        update_task = state.event_loop.run_in_executor(None, update_feeds)
        # logger.info("-update bar")
        # state.loop.draw_screen()
        logger.info("-update")
        # state.loop.draw_screen()

    # def update2(self, force=False):
    #     logger.info(f"update: {force}")
    #     # self.refresh()
    #     # self.create_feeds()
    #     # state.loop.draw_screen()
    #     self.update_feeds(force=True)
    #     self.refresh()

    def refresh(self):
        logger.info("+refresh")
        self.update_query()
        self.view.table.refresh()
        # state.loop.draw_screen()
        logger.info("-refresh")

    def reset(self):
        self.update_query()
        self.view.reset()

    def on_activate(self):
        super().on_activate()
        # asyncio.create_task(self.view.table.play_all())
        # self.refresh()
        # self.update()

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

            for item in self.items_query[offset:offset+limit]:
                # listing = self.item_to_listing(item)
                listing = self.new_listing(
                    feed = AttrDict(item.feed.to_dict()),
                    **item.to_dict(
                        exclude=["feed", "classtype"],
                        related_objects=True
                    )
                )
                listing.content = self.MEDIA_SOURCE_CLASS.schema().loads(listing["content"], many=True)
                yield(listing)

    @db_session
    def mark_items_read(self, request):
        media_item_ids = list(set(request.params))
        logger.info(f"mark_items_read: {media_item_ids}")
        with db_session:
            try:
                for item in self.ITEM_CLASS.select(
                    lambda i: i.media_item_id in media_item_ids
                ):
                    item.read = datetime.now()
                commit()
                self.reset()
            except pony.orm.core.ObjectNotFound:
                logger.info(f("mark_item_read: item {media_item_id} not found"))
