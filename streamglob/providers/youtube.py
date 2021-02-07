import logging
logger = logging.getLogger(__name__)
import os

from datetime import timedelta
import xml.etree.ElementTree as ET
from urllib.parse import parse_qs
import json
import math
import shutil
import pathlib
import tempfile
import itertools

from pony.orm import *
import dateparser
import isodate
import wand.image
import wand.drawing
import ffmpeg


from .feed import *

from ..exceptions import *
from ..state import *
from ..widgets import *
from .. import config
from .. import model
from .. import session

from .filters import *

import youtube_dl

class YouTubeMediaListingMixin(object):


    @property
    def duration(self):
        return (
            str(timedelta(seconds=self.duration_seconds))
            if self.duration_seconds is not None
            else None
        )

    @property
    def video_info(self):
        return parse_qs(
            self.provider.session.get(
                "https://www.youtube.com/get_video_info"
                f"?video_id={self.guid}&asv=3&el=detailpage&hl=en_US"
            ).text
        )

    @property
    def storyboards(self):

        pr = json.loads(self.video_info["player_response"][0])
        try:
            spec = pr["storyboards"]["playerStoryboardSpecRenderer"]["spec"]
        except KeyError:
            return None
        duration = int(pr["videoDetails"]["lengthSeconds"])

        spec_parts = spec.split('|')
        base_url = spec_parts[0].split('$')[0] + "2/"

        sgp_part = spec_parts[0].split('$N')[1]

        if(len(spec_parts) == 3):
            sigh = spec_parts[2].split('M#')[1]
        elif (len(spec_parts) == 2):
            sigh = spec_parts[1].split('t#')[1]
        else:
            sigh = spec_parts[3].split('M#')[1]

        num_boards = 0
        num_tiles = 25

        if duration < 250:
            num_boards = math.ceil((duration / 2) / num_tiles)
        elif duration > 250 and duration < 1000:
            num_boards = math.ceil((duration / 4) / num_tiles)
        elif duration > 1000:
            num_boards = math.ceil((duration / 10) / num_tiles)

        return [
            f"{base_url}M{i}{sgp_part}&sigh={sigh}"
            for i in range(num_boards)
        ]



@model.attrclass()
class YouTubeMediaListing(YouTubeMediaListingMixin, ContentFeedMediaListing):

    duration_seconds = Optional(int)
    definition = Optional(str)


class YouTubeMediaSourceMixin(object):

    @property
    def locator(self):
        return f"https://youtu.be/{self.listing.guid}"

    # @property
    # def preview_locator(self):
    #     return f"https://i.ytimg.com/vi/{self.listing.guid}/maxresdefault.jpg"

    @property
    def helper(self):
        return AttrDict([
            (None, "youtube-dl"),
            ("mpv", None),
        ])

@model.attrclass()
class YouTubeMediaSource(YouTubeMediaSourceMixin, FeedMediaSource, model.InflatableMediaSource):
    pass

class YouTubeSession(session.StreamSession):

    THUMBNAIL_RESOLUTIONS = ["maxres", "standard", "high", "medium", "default"]

    async def youtube_dl_query(self, query, offset=None, limit=None):

        logger.debug(f"youtube_dl_query: {query} {offset}, {limit}")
        ytdl_opts = {
            "ignoreerrors": True,
            'quiet': True,
            'no_color': True,
            'extract_flat': "in_playlist",
            "playlistend": limit,
            'proxy': self.proxies.get("https", None) if self.proxies else None,
            'logger': logger
        }

        if offset:
            ytdl_opts["playliststart"] = offset+1
            ytdl_opts["playlistend"] = offset + limit

        #     ytdl_opts["daterange"] = youtube_dl.DateRange(end=)

        with youtube_dl.YoutubeDL(ytdl_opts) as ydl:
            playlist_dict = ydl.extract_info(query, download=False)

            if not playlist_dict:
                logger.warn("youtube_dl returned no data")
                return
            for item in playlist_dict['entries']:
                yield AttrDict(
                    guid = item["id"],
                    title = item["title"],
                    duration_seconds = self.parse_duration(item["duration"]),
                    # url = f"https://youtu.be/{item['url']}",
                    # preview_url = f"http://img.youtube.com/vi/{item['url']}/0.jpg"
                )


    async def fetch_google_data(self, video_ids):
        url = (
            f"https://www.googleapis.com/youtube/v3/videos?"
            f"id={','.join(video_ids)}"
            f"&part=snippet,contentDetails"
            f"&key={self.provider.config.credentials.api_key}"
        )
        return self.provider.session.get(url).json()

    async def bulk_update(self, entries):



        vids = [entry.guid for entry in entries]
        data = await self.fetch_google_data(vids)
        logger.debug(f"bulk_update: {vids}")

        for entry in entries:
            item = next(
                item for item in data["items"]
                if item["id"] == entry.guid
            )

            entry.created = dateparser.parse(item["snippet"]["publishedAt"][:-1]) # FIXME: Time zone convert from UTC
            entry.content = item["snippet"]["description"]
            entry.duration_seconds = int(
                isodate.parse_duration(
                    item["contentDetails"]["duration"]
                ).total_seconds()
            )
            entry.definition = item["contentDetails"]["definition"]
            entry.thumbnail = next(
                item["snippet"]["thumbnails"][res]["url"]
                for res in self.THUMBNAIL_RESOLUTIONS
                if res in item["snippet"]["thumbnails"]
            )
            yield entry

    async def fetch(self, query, offset=None, limit=None):

        async for entry in self.youtube_dl_query(query, offset=offset, limit=limit):
            yield entry

    @classmethod
    def parse_duration(cls, s):
        if not s:
            return None
        return (dateparser.parse(s) - datetime.now().replace(
                hour=0,minute=0,second=0)
             ).seconds



class YouTubeChannelsDropdown(Observable, urwid.WidgetWrap):

    signals = BaseDropdown.signals

    SEARCH_LABEL = "Search: "

    def __init__(self, items, *args, **kwargs):

        signals = ["change"]

        self.dropdown = BaseDropdown(items, *args, **kwargs)
        self.dropdown.connect("changed", self.on_channel_change)
        self.search_label = urwid.Text(self.SEARCH_LABEL)
        self.search_edit = TextFilterWidget("")
        self.search_edit.connect("selected", self.on_edit_select)
        self.search_widget = urwid.Columns([
            ("pack", self.search_label),
            ("weight", 1, urwid.AttrMap(self.search_edit, "dropdown_text")),
        ])
        self.search_placeholder = urwid.WidgetPlaceholder(self.search_widget)
        self.columns = urwid.Columns([
            (self.dropdown.width, self.dropdown),
            ("weight", 1, urwid.Padding(self.search_placeholder))
        ], dividechars=1)
        super().__init__(self.columns)
        # urwid.connect_signal(self.dropdown, "change", self.on_channel_change)
        # urwid.connect_signal(self.search_edit, "select", self.on_edit_select)
        self.hide_search()

    def __getattr__(self, attr):
        if attr in ["cycle", "select_label", "select_value", "selected_label", "items"]:
            return getattr(self.dropdown, attr)

    def on_channel_change(self, value):
        if value == "search":
            self.show_search()
        else:
            self.hide_search()
        self.changed()
        # self._emit("change", self.value)

    def on_edit_select(self):
        self.changed()

    def show_search(self):
        self.search_placeholder.original_widget = self.search_widget

    def hide_search(self):
        self.search_placeholder.original_widget = urwid.Text("")

    @property
    def search(self):
        return self.search_edit.value

    @property
    def channel(self):
        return self.dropdown.selected_value

    @channel.setter
    def channel(self, value):
        self.dropdown.selected_value = value

    @property
    def value(self):
        return self.channel
        # if self.channel == "search":
        #     return ("search", self.search)
        # else:
        #     return self.channel

    @value.setter
    def value(self, value):
        self.channel = value


class YouTubeChannelsFilter(FeedsFilter):

    WIDGET_CLASS = YouTubeChannelsDropdown

    @property
    def items(self):
        return AttrDict(super().items, **{"Search": "search"})

    @property
    def value(self):
        return self.widget.value

    @value.setter
    def value(self, value):
        self.widget.value = value

    @property
    def search(self):
        return self.widget.search

    @property
    def channel(self):
        return self.widget.channel

    @property
    def widget_sizing(self):
        return lambda w: ("given", 30)



class YouTubeFeed(FeedMediaChannel):

    LISTING_CLASS = YouTubeMediaListing

    CHANNEL_URL_TEMPLATE = "https://youtube.com/channel/{locator}/videos"

    @property
    @memo(region="long")
    def rss_data(self):
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={self.locator}"
        res = self.session.get(url)
        content = res.content
        tree = ET.fromstring(content)
        entries = tree.findall(
            ".//{http://www.w3.org/2005/Atom}entry"
        )
        # raise Exception(entries[0].find("./{http://www.w3.org/2005/Atom}title"))

        res = {
            tag: entry.find(ns+tag).text
            for entry in entries
            for ns, tag in [
                    ("{http://www.w3.org/2005/Atom}", "title"),
                    ("{http://www.w3.org/2005/Atom}", "published"),
                    (".//{http://search.yahoo.com/mrss/}", "description")

            ]
        }
        return res

    @property
    @db_session
    def end_cursor(self):
        num_listings = self.provider.LISTING_CLASS.select(
            lambda l: l.channel == self
        ).count()
        try:
            oldest, guid = select(
                (l.created, l.guid)
                for l in  self.provider.LISTING_CLASS
                if l.channel == self
            ).order_by(lambda c, g: c).first()
        except TypeError:
            oldest, guid = (None, None)

        return (oldest, num_listings, guid)


    @property
    def is_channel(self):
        return len(self.locator) == 24 and self.locator.startswith("UC")

    async def fetch(self, resume=False, *args, **kwargs):

        limit = self.provider.config.get("fetch_limit", self.DEFAULT_FETCH_LIMIT)

        url = (
            self.CHANNEL_URL_TEMPLATE.format(locator=self.locator)
            if self.is_channel
            else self.locator
        )

        listings = []

        (oldest, num_listings, oldest_guid) = self.end_cursor
        offset = num_listings if resume else 0
        new = 0

        while offset >= 0:

            with db_session:

                logger.info(f"fetch: offset={offset}, limit={limit}")
                batch = [
                    item async for item in self.session.fetch(
                        url, offset=offset, limit=limit
                    )
                ]

                if not len(batch):
                    break

                # if we're not resuming and we already have the first item,
                # assume there's nothing new and return early
                if (
                        not resume
                        and self.items.select(
                            lambda i: i.guid == batch[0]["guid"]
                    ).first()
                ):
                    break

                # add in data from Google API
                batch = [ l async for l in self.session.bulk_update(batch) ]

            try:
                start = next(
                    i for i, item in enumerate(batch)
                    if (
                        (oldest is None or item.created <= oldest)
                        and item.guid != oldest_guid
                    )
                )

                if start and len(listings):
                    listings[:0] += batch[start:]
                else:
                    listings += [l for l in batch[start:]
                                 if (oldest is None
                                     or l.created <= oldest)
                                 and l.guid != oldest_guid]

            except StopIteration:
                start = None
                # these are all newer, so advance and continue
                offset += limit
                if resume:
                    continue

            if len(listings) >= limit:
                # there's an item in this batch that's newer, so we don't need
                # to go back any further
                break
            elif not resume:
                break
            else:
                # advance forward to get one more page
                offset += limit
                continue

            # go back a page
            offset -= min(offset, limit)

        with db_session:

            for item in listings:

                logger.info(item["guid"])

                i = self.items.select(lambda i: i.guid == item["guid"]).first()

                if i:
                    raise RuntimeError(f"old listing retrieved: {i}")

                listing = AttrDict(
                    channel = self,
                    sources = [
                        AttrDict(media_type="video", preview_url=item.thumbnail)
                    ],
                    **item
                )
                yield listing


@keymapped()
class YouTubeDataTable(MultiSourceListingMixin, CachedFeedProviderDataTable):

    @property
    def thumbnails(self):
        if not hasattr(self, "_thumbnails"):
            self._thumbnails = AttrDict()
        return self._thumbnails

    async def thumbnail_for(self, listing):
        if listing.guid not in self.thumbnails:
            thumbnail = os.path.join(self.tmp_dir, f"thumbnail.{listing.guid}.jpg")
            await self.download_file(listing.sources[0].preview_locator, thumbnail)
            self.thumbnails[listing.guid] = thumbnail
        return self.thumbnails[listing.guid]

    @property
    def storyboards(self):
        if not hasattr(self, "_storyboards"):
            self._storyboards = AttrDict()
        return self._storyboards

    async def storyboard_for(self, listing):
        if not listing.storyboards:
            return await self.thumbnail_for(listing)

        if listing.guid not in self.storyboards:
            self.storyboards[listing.guid] = await self.make_storyboard_preview(listing)
        return self.storyboards[listing.guid]

    def keypress(self, size, key):
        return super().keypress(size, key)

    def reset(self, *args, **kwargs):
        super().reset(*args, **kwargs)
        for pos, row in enumerate(self):
            listing = row.data_source
            if listing.guid in self.storyboards:
                storyboard = self.storyboards[listing.guid]
                state.event_loop.create_task(self.playlist_replace(storyboard, pos=pos))


    def on_focus(self, source, position):

        super().on_focus(source, position)

        if state.listings_view.preview_mode == "storyboard":
            # for pos in range(position, min(len(self)-1, position+2)):
            state.event_loop.create_task(self.storyboard_preview(position))


    async def storyboard_preview(self, position):

        async def preview(listing):
            # logger.info(f"preview {position}")
            storyboard = await self.storyboard_for(listing)
            await self.playlist_replace(storyboard, pos=position)

        row = self[position]
        listing = row.data_source
        if not listing.storyboards or listing.guid in self.storyboards:
            return
        if getattr(self, "preview_task", False):
            # logger.info("canceling")
            self.preview_task.cancel()
        self.preview_task = state.event_loop.create_task(preview(listing))
        # await self.preview_task

    # FIXME: share temp dir with player and other modules
    @property
    def tmp_dir(self):
        if not getattr(self, "_tmp_dir", False):
            self._tmp_dir = tempfile.mkdtemp()
        return self._tmp_dir

    # FIXME: shared utility module if reused?
    async def download_file(self, url, path):
        if os.path.isdir(path):
            dest = os.path.join(path, url.split('/')[-1])
        else:
            dest = path

        with requests.get(url, stream=True) as res:
            if res.status_code != 200:
                return None
            with open(dest, 'wb') as f:
                shutil.copyfileobj(res.raw, f)

    async def make_storyboard_preview(self, listing):

        TILE_WIDTH = 160
        TILE_HEIGHT = 90

        PREVIEW_WIDTH = 1280
        PREVIEW_HEIGHT = 720

        inset_scale = self.provider.config.get_path("auto_preview.storyboard.scale") or 0.25
        inset_offset = self.provider.config.get_path("auto_preview.storyboard.offset") or 0
        frame_rate = self.provider.config.get_path("auto_preview.storyboard.frame_rate") or 1
        border_color = self.provider.config.get_path(
            "auto_preview.storyboard.border.color"
        ) or "black"
        border_width = self.provider.config.get_path("auto_preview.storyboard.border.width") or 1
        tile_skip = self.provider.config.get_path("auto_preview.storyboard.skip") or None

        thumbnail = await self.thumbnail_for(listing)

        board_files = []
        for i, board in enumerate(listing.storyboards):
            board_file = os.path.join(self.tmp_dir, f"board.{listing.guid}.{i:02d}.jpg")
            board_files.append(board_file)
            await self.download_file(board, board_file)

        thumbnail = wand.image.Image(filename=thumbnail)
        thumbnail.trim(fuzz=5)
        if thumbnail.width != PREVIEW_WIDTH:
            thumbnail.transform(resize=f"{PREVIEW_WIDTH}x{PREVIEW_HEIGHT}")
        i = 0
        for board_file in board_files:
            with wand.image.Image(filename=board_file) as img:
                for h in range(0, img.height, TILE_HEIGHT):
                    for w in range(0, img.width, TILE_WIDTH):
                        i += 1
                        if tile_skip and i % tile_skip:
                            continue
                        w_end = w + TILE_WIDTH
                        h_end = h + TILE_HEIGHT
                        with img[w:w_end, h:h_end] as tile:
                            clone = thumbnail.clone()
                            tile.resize(int(thumbnail.width * inset_scale),
                                         int(thumbnail.height * inset_scale))
                            tile.border(border_color, border_width, border_width)
                            thumbnail.composite(
                                tile,
                                left=thumbnail.width-tile.width-inset_offset,
                                top=thumbnail.height-tile.height-inset_offset
                            )
                            tile_file=os.path.join(self.tmp_dir, f"tile.{listing.guid}.{i:04d}.jpg")
                            thumbnail.save(filename=tile_file)

        inputs = ffmpeg.concat(
            ffmpeg.input(os.path.join(self.tmp_dir, f"tile.{listing.guid}.*.jpg"),
                                      pattern_type="glob",framerate=frame_rate)
        )
        storyboard_file=os.path.join(self.tmp_dir, f"storyboard.{listing.guid}.mp4")
        logger.info(storyboard_file)
        proc = await inputs.output(storyboard_file).run_asyncio(overwrite_output=True, quiet=True)
        await proc.wait()
        for p in itertools.chain(
            pathlib.Path(self.tmp_dir).glob(f"board.{listing.guid}.*"),
            pathlib.Path(self.tmp_dir).glob(f"tile.{listing.guid}.*")
        ):
            p.unlink()
        return storyboard_file


class YouTubeProvider(PaginatedProviderMixin,
                      CachedFeedProvider):

    FILTERS_BROWSE = AttrDict([
        ("feed", YouTubeChannelsFilter),
    ])

    FEED_CLASS = YouTubeFeed

    MEDIA_TYPES = {"video"}

    SESSION_CLASS = YouTubeSession

    DOWNLOADER = "youtube-dl"

    @property
    def VIEW(self):
        return SimpleProviderView(self, CachedFeedProviderView(self, YouTubeDataTable(self)))


    def init_config(self):
        super().init_config()
        attrs = list(self.ATTRIBUTES.items())
        idx, attr = next(  (i, a ) for i, a in enumerate(attrs) if a[0] == "title")
        self.ATTRIBUTES = AttrDict(
            attrs[:idx]
            + [
                ("duration", {
                    "width": 8,
                    "align": "right",
                })
            ]
            + attrs[idx:]
        )


    @property
    def selected_feed(self):

        if self.filters.feed.channel in [None, "search"]:
            return None
        return self.filters.feed.value

    def listings(self, offset=None, limit=None, *args, **kwargs):
        # if self.filters.feed.value == "search":
        if self.filters.feed.channel == "search":
            if self.filters.feed.search:
                query = f"ytsearch{offset+self.view.table.limit}:{self.filters.feed.search}"
                return [
                    # SearchResult(r)
                    AttrDict(
                        title = r.title,
                        guid = r.guid,
                        sources = [
                            AttrDict(
                                locator=r.url, media_type="video"
                            )
                        ]
                    )
                    for r in self.session.youtube_dl_query(query, offset, limit)
                ]
            else:
                logger.info("no search")
                return AttrDict()
        else:
            return super().listings(
                offset=offset, limit=limit, *args, **kwargs
            )

    def feed_attrs(self, feed_name):
        return dict(locator=self.filters.feed[feed_name])

    def play_args(self, selection, **kwargs):
        source, kwargs = super().play_args(selection, **kwargs)
        fmt = self.config.get_path("output.format")
        if fmt:
            kwargs["format"] = fmt
        return (source, kwargs)
