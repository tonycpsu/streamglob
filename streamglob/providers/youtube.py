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
import itertools
import traceback

from pony.orm import *
import aiohttp
import aiofiles
import dateparser
import isodate
import wand.image
import wand.drawing
import ffmpeg
from orderedattrdict import AttrDict
from async_property import async_property, async_cached_property

from .feed import *

from ..exceptions import *
from ..state import *
from ..widgets import *
from .. import config
from .. import model
from .. import session

from .filters import *

import youtube_dl
from pytube.innertube import InnerTube
from thumbframes_dl import YouTubeFrames

class YouTubeMediaListingMixin(object):


    @property
    def duration(self):
        return (
            str(timedelta(seconds=self.duration_seconds)).lstrip("0:")
            if self.duration_seconds is not None
            else None
        )

    @async_cached_property
    async def video_info(self):
        return await self.provider.session.get_video_info(self.guid)

    @async_cached_property
    async def storyboards(self):

        vi = await self.video_info

        try:
            spec = vi["storyboards"]["playerStoryboardSpecRenderer"]["spec"]
        except KeyError:
            return None

        thumbs = YouTubeFrames._get_storyboards_from_spec(
            None,
            self.guid,
            vi["storyboards"]["playerStoryboardSpecRenderer"]["spec"]
        )
        return [ t.url for t in thumbs[list(thumbs.keys())[-1]] ]


@model.attrclass()
class YouTubeMediaListing(YouTubeMediaListingMixin, model.ContentMediaListing, FeedMediaListing):

    duration_seconds = Optional(int)
    definition = Optional(str) # move to attrs


class YouTubeMediaSourceMixin(object):

    @property
    def locator(self):
        return f"https://youtu.be/{self.listing.guid}"

    @property
    def ext(self):
        # FIXME
        return "mp4"


    @property
    def helper(self):
        return AttrDict([
            (None, "youtube-dl"),
            ("mpv", None),
        ])

@model.attrclass()
class YouTubeMediaSource(YouTubeMediaSourceMixin, FeedMediaSource, model.InflatableMediaSource):
    pass

class YouTubeSession(session.AsyncStreamSession):

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
                    duration_seconds=item["duration"],
                )


    async def get_video_info(self, vid):
        # FIXME: innertube isn't async
        vi = InnerTube().player(vid)
        return vi

    async def extract_video_info(self, entry):

        vi = await self.get_video_info(entry["guid"])

        entry.created = dateparser.parse(
            vi["microformat"]["playerMicroformatRenderer"]["publishDate"]
        )
        entry.duration = int(vi["videoDetails"]["lengthSeconds"])

        try:
            entry.thumbnail = sorted(
                [ (t["url"], t["height"]) for t in vi["videoDetails"]["thumbnail"]["thumbnails"] ],
                key=lambda t: -t[1]
            )[0][0]
        except (IndexError, AttributeError):
            pass

        entry.content = (
            vi["microformat"]
            ["playerMicroformatRenderer"]
            .get("description", {})
            .get("simpleText")
        )
        return entry


    async def fetch_google_data(self, video_ids):
        url = (
            f"https://www.googleapis.com/youtube/v3/videos?"
            f"id={','.join(video_ids)}"
            f"&part=snippet,contentDetails"
            f"&key={self.provider.config.credentials.api_key}"
        )
        res = await self.provider.session.get(url)
        return await res.json()


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
        return (dateparser.parse(str(s)) - datetime.now().replace(
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

    @value.setter
    def value(self, value):
        self.channel = value


class YouTubeChannelsFilter(FeedsFilter):

    WIDGET_CLASS = YouTubeChannelsDropdown

    @property
    def items(self):
        return AttrDict(super().items, **{"Search": "search"})

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

    @async_cached_property
    async def rss_data(self):
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={self.locator}"
        res = await self.session.get(url)
        content = await res.content
        tree = ET.fromstring(content)
        entries = tree.findall(
            ".//{http://www.w3.org/2005/Atom}entry"
        )

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
        try:
            oldest, guid = select(
                (l.created, l.guid)
                for l in  self.provider.LISTING_CLASS
                if l.channel == self
            ).order_by(lambda c, g: c).first()
        except TypeError:
            oldest, guid = (None, None)

        return (oldest, self.last_offset, guid)

    @property
    @db_session
    def last_offset(self):
        return self.attrs.get("last_offset", 0)

    @db_session
    def save_last_offset(self, offset):
        if offset > self.attrs.get("last_offset", 0):
            self.attrs["last_offset"] = offset
        commit()


    @property
    def is_channel(self):
        return len(self.locator) == 24 and self.locator.startswith("UC")

    async def fetch(self, limit=None, resume=False, reverse=False, *args, **kwargs):

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

                logger.info(f"fetch: resume={resume}, offset={offset}, limit={limit}, cursor={self.end_cursor}")
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
                    logger.debug("nothing new")
                    break

                if self.provider.config.credentials.api_key:
                    batch = [
                        l async for l in self.session.bulk_update(batch)
                    ]
                else:
                    batch = [
                        await self.session.extract_video_info(entry)
                        for entry in batch
                    ]

            logger.debug(batch)
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
                logger.info(listings)


            except StopIteration:
                start = None
                # these are all newer, so advance and continue
                offset += limit
                if resume:
                    logger.debug("all newer, getting next batch")
                    continue
                else:
                    listings += batch

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
                    continue
                    # raise RuntimeError(f"old listing retrieved: {i}")

                listing = AttrDict(
                    channel = self,
                    sources = [
                        AttrDict(media_type="video", url_thumbnail=item.thumbnail)
                    ],
                    **item
                )
                yield listing

        self.save_last_offset(offset)


@keymapped()
class YouTubeDataTable(MultiSourceListingMixin, CachedFeedProviderDataTable):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.storyboard_lock = asyncio.Lock()
        self.storyboard_tasks = []

    @property
    def thumbnails(self):
        if not hasattr(self, "_thumbnails"):
            self._thumbnails = AttrDict()
        return self._thumbnails

    async def thumbnail_for(self, listing):
        if listing.guid not in self.thumbnails:
            thumbnail = os.path.join(self.tmp_dir, f"thumbnail.{listing.guid}.jpg")
            await self.download_file(listing.sources[0].locator_thumbnail, thumbnail)
            self.thumbnails[listing.guid] = thumbnail
        return self.thumbnails[listing.guid]

    @property
    def storyboards(self):
        if not hasattr(self, "_storyboards"):
            self._storyboards = AttrDict()
        return self._storyboards

    async def storyboard_for(self, listing, cfg):
        if not await listing.storyboards:
            return await self.thumbnail_for(listing)

        async with self.storyboard_lock:
            if listing.guid not in self.storyboards:
                self.storyboards[listing.guid] = await self.make_preview_storyboard(listing, cfg)
        return self.storyboards[listing.guid]

    def keypress(self, size, key):
        return super().keypress(size, key)

    def reset(self, *args, **kwargs):
        self.cancel_pending_tasks()
        super().reset(*args, **kwargs)

    def cancel_pending_tasks(self):
        while len(self.storyboard_tasks):
            task = self.storyboard_tasks.pop()
            if not task.cancelled:
                task.cancel()

    def on_activate(self):
        self.reset()
        super().on_activate()

    def on_deactivate(self):
        self.cancel_pending_tasks()
        super().on_deactivate()

    async def preview_content_storyboard(self, cfg, position, listing, source):

        async def preview(listing):
            storyboard = await self.storyboard_for(listing, cfg)
            if not storyboard:
                return
            logger.info(storyboard)
            await self.playlist_replace(storyboard.img_file, idx=position)
            state.loop.draw_screen()

        if not await listing.storyboards:
            return

        # if getattr(self, "preview_task", False):
        #     self.preview_task.cancel()
        # self.preview_task = state.event_loop.create_task(preview(listing))

        await preview(listing)
        # await self.preview_task

    async def preview_duration(self, cfg, listing):
        if cfg.mode == "storyboard":
            storyboard = await self.storyboard_for(listing, cfg)
            if not storyboard:
                return None
            return storyboard.duration
        else:
            return await super().preview_duration(cfg, listing)

    # FIXME: shared utility module if reused?
    async def download_file(self, url, path):
        if os.path.isdir(path):
            dest = os.path.join(path, url.split('/')[-1])
        else:
            dest = path

        async with self.provider.session.get(url) as res:
            try:
                res.raise_for_status()
            except:
                raise
            async with aiofiles.open(dest, mode="wb") as f:
                while True:
                    chunk = await res.content.read(1024*1024)
                    if not chunk:
                        await f.flush()
                        break
                    await f.write(chunk)

    async def make_preview_storyboard(self, listing, cfg):

        PREVIEW_WIDTH = 1280
        PREVIEW_HEIGHT = 720

        TILES_X = 5
        TILES_Y = 5

        inset_scale = cfg.scale or 0.25
        inset_offset = cfg.offset or 0
        border_color = cfg.border.color or "black"
        border_width = cfg.border.width or 1
        tile_skip = cfg.skip or None


        board_files = []
        boards = await listing.storyboards
        for i, board in enumerate(boards):
            board_file = os.path.join(self.tmp_dir, f"board.{listing.guid}.{i:02d}.jpg")
            try:
                try:
                    await self.download_file(board, board_file)
                except asyncio.CancelledError:
                    return
                board_files.append(board_file)
            except:
                # sometimes the last one doesn't exist
                if i == len(boards)-1:
                    pass
                else:
                    logger.error("".join(traceback.format_exc()))

        logger.info(board_files)
        thumbnail = await self.thumbnail_for(listing)

        thumbnail = wand.image.Image(filename=thumbnail)
        thumbnail.trim(fuzz=5)
        if thumbnail.width != PREVIEW_WIDTH:
            thumbnail.transform(resize=f"{PREVIEW_WIDTH}x{PREVIEW_HEIGHT}")
        i = 0
        n = 0
        for board_file in board_files:
            # logger.debug(board_file)
            with wand.image.Image(filename=board_file) as img:
                tile_height = img.height // TILES_Y
                tile_width = img.width // TILES_X
                for h in range(0, img.height, tile_height):
                    for w in range(0, img.width, tile_width):
                        i += 1
                        if tile_skip and i % tile_skip:
                            continue
                        n += 1
                        w_end = w + tile_width
                        h_end = h + tile_height
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
                            tile_file=os.path.join(self.tmp_dir, f"tile.{listing.guid}.{n:04d}.jpg")
                            thumbnail.save(filename=tile_file)

        if cfg.frame_rate:
            frame_rate = cfg.frame_rate
            duration = i/frame_rate
        elif cfg.duration:
            duration = cfg.duration
            frame_rate = n/duration
        else:
            duration = n
            frame_rate = 1

        inputs = ffmpeg.concat(
            ffmpeg.input(os.path.join(self.tmp_dir, f"tile.{listing.guid}.*.jpg"),
                                      pattern_type="glob",framerate=frame_rate)
        )
        storyboard_file=os.path.join(self.tmp_dir, f"storyboard.{listing.guid}.mp4")
        proc = await inputs.output(storyboard_file).run_asyncio(overwrite_output=True, quiet=True)
        await proc.wait()

        for p in itertools.chain(
            pathlib.Path(self.tmp_dir).glob(f"board.{listing.guid}.*"),
            pathlib.Path(self.tmp_dir).glob(f"tile.{listing.guid}.*")
        ):
            p.unlink()

        # return storyboard_file
        return AttrDict(
            img_file=storyboard_file,
            duration=duration
        )


class YouTubeProvider(PaginatedProviderMixin,
                      CachedFeedProvider):

    # FILTERS_BROWSE = AttrDict([
    #     ("feed", YouTubeChannelsFilter),
    # ])

    FEED_CLASS = YouTubeFeed

    MEDIA_TYPES = {"video"}

    SESSION_CLASS = YouTubeSession

    DOWNLOADER = "youtube-dl"

    @property
    def VIEW(self):
        return FeedProviderView(self, CachedFeedProviderBodyView(self, YouTubeDataTable(self)))

    @property
    def ATTRIBUTES(self):
        attrs = list(super().ATTRIBUTES.items())
        idx, attr = next(  (i, a ) for i, a in enumerate(attrs) if a[0] == "title")
        return AttrDict(
            attrs[:idx]
            + [
                ("duration", {
                    "label": "duration",
                    "width": 8,
                    "align": "right",
                    "sort_icon": False,
                }),
                ("guid", {
                    "label": "id",
                    "width": 11,
                    "align": "right",
                    "sort_icon": False,
                })
            ]
            + attrs[idx:]
        )
    @property
    def PREVIEW_TYPES(self):
        return ["default", "storyboard", "full"]

    def translate_template(self, template):

        TAG_MAP={
            "listing.title": "title",
            "listing.created_date": "upload_date",
            "listing.guid": "id",
            "listing.feed_locator": "channel_id"
        }
        SRC_TAG_RE=re.compile(r"{([^}]+)}")

        for s, d in TAG_MAP.items():
            template = template.replace(s, d)
        return SRC_TAG_RE.sub(r"%(\1)s", template)

    @property
    def selected_feed(self):

        if self.filters.feed.channel in [None, "search"]:
            return None
        return self.filters.feed.value


    def feed_attrs(self, feed_name):
        return dict(locator=self.filters.feed[feed_name])

    def play_args(self, selection, **kwargs):
        source, kwargs = super().play_args(selection, **kwargs)
        fmt = self.config.get_path("output.format")
        if fmt:
            kwargs["format"] = fmt
        return (source, kwargs)
