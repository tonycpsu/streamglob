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

try:
    import yt_dlp as youtube_dl
except ImportError:
    import youtube_dl

from pytube.innertube import InnerTube
from thumbframes_dl import YouTubeFrames
from youtubesearchpython import Search

class ChannelNotFoundError(Exception):
    pass

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
        # import ipdb; ipdb.set_trace()
        return thumbs[list(thumbs.keys())[-1]]

    @async_cached_property
    async def rich_thumbnail(self):
        # FIXME: `Video` class doesn't return richThumbnail, but `Search` does,
        # so we try to search by Video ID
        search = Search(self.guid).result()
        thumbnail = next(
            (
                s["richThumbnail"]
                for s in search["result"]
                if s["id"] == self.guid
            ),
            None
        )
        if not thumbnail:
            return None
        return thumbnail["url"]

@model.attrclass()
class YouTubeMediaListing(YouTubeMediaListingMixin, model.ContentMediaListing, FeedMediaListing):

    duration_seconds = Optional(int)
    definition = Optional(str) # move to attrs


class YouTubeMediaSourceMixin(object):

    KEY_ATTR = "locator"

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
            # "ignoreerrors": False,
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

    async def fetch(self, *args, **kwargs):
        if self.provider.config.fetch_method == "api_v3":
            if not self.provider.config.credentials.api_key:
                raise RuntimeError("must set api_key to use api fetch method")
            return await self.fetch_playlist_items_api_v3(*args, **kwargs)
        else:
            return await self.fetch_playlist_items_ytdl(*args, **kwargs)


    # async def get_page(self, offset, limit):
    async def fetch_playlist_items_ytdl(self, playlist_id, page_token=None, limit=50):

        if page_token is None:
            page_token = 0

        page = [
            item async for item in self.youtube_dl_query(
                playlist_id, offset=page_token, limit=limit
            )
        ]

        if self.provider.config.credentials.api_key:
            page = [
                l async for l in self.bulk_update(page)
            ]
        else:
            page = [
                await self.extract_video_info(entry)
                for entry in page
            ]
        return (page, page_token+limit, max(0, page_token-limit))


    async def fetch_playlist_items_api_v3(self, playlist_id, page_token=None, limit=50):

        url = (
            "https://www.googleapis.com/youtube/v3/playlistItems"
            f"?key={self.provider.config.credentials.api_key}"
            f"&playlistId={playlist_id}"
            f"&pageToken={page_token or ''}"
            f"&part=snippet&part=contentDetails"
            f"&maxResults={limit}"
        )

        # logger.info(url)
        res = await self.provider.session.get(url)
        j = await res.json()
        try:
            items = [
                item
                async for item in self.bulk_update([
                        AttrDict(
                            guid=item["snippet"]["resourceId"]["videoId"],
                            title=item["snippet"]["title"],
                            content=item["snippet"]["description"],
                            created=dateparser.parse(item["snippet"]["publishedAt"][:-1]),
                        )
                        for item in j["items"]
                ])
            ]
        except KeyError:
            import ipdb; ipdb.set_trace()
        return (items, j.get("nextPageToken"), j.get("prevPageToken"))

    async def fetch_video_data(self, video_ids):

        url = (
            f"https://www.googleapis.com/youtube/v3/videos"
            f"?key={self.provider.config.credentials.api_key}"
            f"&id={','.join(video_ids)}"
            f"&part=snippet&part=contentDetails"
        )
        res = await self.provider.session.get(url)
        return await res.json()


    async def bulk_update(self, entries):

        vids = [entry.guid for entry in entries]
        data = await self.fetch_video_data(vids)
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

    @property
    def url(self):
        return (
            self.CHANNEL_URL_TEMPLATE.format(locator=self.locator)
            if self.is_channel
            else self.locator
        )

    @async_cached_property
    async def rss_data(self):
        if self.locator.startswith("UC"):
            url = f"https://www.youtube.com/feeds/videos.xml?channel_id={self.locator}"
        elif self.locator.startswith("PL"):
            url = f"https://www.youtube.com/feeds/videos.xml?playlist_id={self.locator}"
        else:
            raise NotImplementedError
        res = await self.session.get(url)
        if res.status != 200:
            raise ChannelNotFoundError
        content = await res.text()
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
        return (self.oldest_timestamp, self.newest_timestamp, self.listing_offset)

    @property
    @db_session
    def listing_offset(self):
        return self.attrs.get("listing_offset", 0)

    @listing_offset.setter
    @db_session
    def listing_offset(self, value):
        self.attrs["listing_offset"] = value
        commit()

    @property
    @db_session
    def page_token(self):
        return self.attrs.get("page_token", {}).get(self.provider.config.fetch_method or "ytdl")

    @page_token.setter
    @db_session
    def page_token(self, value):
        if not "page_token" in self.attrs:
            self.attrs["page_token"] = {}
        self.attrs["page_token"][self.provider.config.fetch_method or "ytdl"] = value
        commit()

    @property
    @db_session
    def oldest_timestamp(self):
        oldest = self.attrs.get("oldest_timestamp", None)
        if not oldest:
            return None
        return dateparser.parse(oldest)

    @oldest_timestamp.setter
    @db_session
    def oldest_timestamp(self, value):
        self.attrs["oldest_timestamp"] = value.isoformat()
        commit()


    @property
    @db_session
    def newest_timestamp(self):
        newest = self.attrs.get("newest_timestamp", None)
        if not newest:
            return None
        return dateparser.parse(newest)

    @newest_timestamp.setter
    @db_session
    def newest_timestamp(self, value):
        self.attrs["newest_timestamp"] = value.isoformat()
        commit()

    @property
    def batch_size(self):
        return self.provider.config.get("fetch_limit", 50)

    @property
    def is_channel(self):
        return len(self.locator) == 24 and self.locator.startswith("UC")

    @property
    def is_playlist(self):
        return len(self.locator) == 34 and self.locator.startswith("PL")

    @async_cached_property
    async def uploads_playlist(self):
        if not self.attrs.get("uploads_playlist"):
            url = (
                "https://www.googleapis.com/youtube/v3/channels"
                f"?key={self.provider.config.credentials.api_key}"
                f"&id={self.locator}"
                f"&part=contentDetails"
            )
            logger.info(url)
            res = await self.provider.session.get(url)
            j = await res.json()
            logger.debug(j)
            details = j["items"][0]["contentDetails"]
            playlist_id = details["relatedPlaylists"]["uploads"]
            with db_session:
                self.attrs["uploads_playlist"] = playlist_id
                commit()

        return self.attrs["uploads_playlist"]

    @async_cached_property
    async def playlist_id(self):
        return self.locator if self.is_playlist else await self.uploads_playlist

    @db_session
    def save_last_offset(self, offset):
        if offset is None:
            offset = self.items.select().count()
        saved = self.attrs.get("listing_offset", 0)
        if offset >= saved:
            self.attrs["listing_offset"] = offset
            commit()

    async def fetch_newer(self):

        batch = []
        newest = self.newest_timestamp
        token = None
        while True:
            (page, next_token, prev_token) = (
                await self.session.fetch(
                    await self.playlist_id, token, limit=self.batch_size
                )
            )
            logger.debug(f"token: {token}, page: {len(page)}")
            if not len(page):
                break

            if token and newest and not any([
                # item.created <= newest
                item.created < newest
                for item in page
            ]):
                logger.debug("fast-forwarding")
                token = next_token
                continue

            batch += [
                item for item in page
                if item not in batch
                and (not newest or item.created > newest)
            ]

            if len(batch) >= self.batch_size or not prev_token:
                break
            else:
                logger.debug("rewinding")
                token = prev_token

        # logger.debug(f"batch: {batch}")
        # if not len(batch):
        #     return []

        try:
            last = next(
                n for n, item in enumerate(batch)
                if item.created <= newest
            ) if newest else None
        except StopIteration:
            last = None
        # logger.debug(f"last: {last}")
        batch = batch[:last][-self.batch_size:]
        return batch

    async def fetch_older(self):

        oldest = self.oldest_timestamp

        batch = []
        token = self.page_token
        rewound = False if token else True

        while True:
            logger.debug(f"fetch: token={token}, oldest={oldest}")
            (page, next_token, prev_token) = (
                await self.session.fetch(
                    await self.playlist_id, token, limit=self.batch_size
                )
            )
            # logger.debug(f"token: {token}, page: {len(page)}")
            if not len(page):
                break

            if not rewound:
                if prev_token and (
                        not oldest
                        or not any([
                            item.created > oldest
                            for item in page
                        ])
                ):
                    logger.debug("rewinding")
                    token = prev_token
                else:
                    logger.debug("rewound")
                    rewound = True
                continue


            batch[0:] += [
                item for item in page
                if item not in batch
                and (not oldest or item.created < oldest)
                and item.guid not in select(item.guid for item in self.items)
            ]

            if len(batch) >= self.batch_size or not next_token:
                token = next_token
                break
            else:
                logger.debug("fast-forwarding")
                token = next_token

        try:
            first = next(
                n for n, item in enumerate(batch)
                if item.created <= oldest
            ) if oldest else None
        except StopIteration:
            first = None
        batch = batch[first:][:self.batch_size]
        self.page_token = token
        return batch


    async def fetch(self, limit=None, resume=False, reverse=False, *args, **kwargs):

        try:
            data = await self.rss_data
            self.attrs["error"] = False
        except ChannelNotFoundError:
            with db_session:
                self.attrs["error"] = True
                return

        if resume:
            # logger.debug("fetch older")
            listings = await self.fetch_older()

        else:
            # logger.debug("fetch newer")
            listings = await self.fetch_newer()

        with db_session:

            for item in listings:

                logger.info(item["guid"])

                listing = AttrDict(
                    channel=self,
                    sources=[
                        AttrDict(media_type="video", url_thumbnail=item.thumbnail)
                    ],
                    **item
                )
                if resume:
                    if not self.oldest_timestamp or listing.created < self.oldest_timestamp:
                        self.oldest_timestamp = listing.created
                else:
                    if not self.newest_timestamp or listing.created > self.newest_timestamp:
                        self.newest_timestamp = listing.created
                yield listing



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

    @property
    def rich_thumbnails(self):
        if not hasattr(self, "_rich_thumbnails"):
            self._rich_thumbnails = AttrDict()
        return self._rich_thumbnails

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

    async def rich_thumbnail_for(self, listing):
        if listing.guid not in self.rich_thumbnails:
            self.rich_thumbnails[listing.guid] = await listing.rich_thumbnail
        return self.rich_thumbnails[listing.guid]

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

    async def preview_content_storyboard(self, cfg, listing, source):

        async def preview(listing):
            storyboard = await self.storyboard_for(listing, cfg)
            if not storyboard:
                return
            logger.info(storyboard)
            # await self.playlist_replace(storyboard.img_file, idx=position)
            state.loop.draw_screen()
            return storyboard.img_file

        if not await listing.storyboards:
            return

        # if getattr(self, "preview_task", False):
        #     self.preview_task.cancel()
        # self.preview_task = state.event_loop.create_task(preview(listing))

        return await preview(listing)

    async def preview_content_rich_thumbnail(self, cfg, listing, source):
        # import ipdb; ipdb.set_trace()
        return await self.rich_thumbnail_for(listing)


    async def preview_duration(self, cfg, listing):

        duration = await super().preview_duration(cfg, listing)
        if cfg.mode != "storyboard" or duration is None:
            return duration

        storyboard = await self.storyboard_for(listing, cfg)
        if not storyboard:
            return 0

        return int(storyboard.duration)


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

        # TILES_X = 5
        # TILES_Y = 5

        inset_scale = cfg.scale or 0.25
        inset_offset = cfg.offset or 0
        border_color = cfg.border.color or "black"
        border_width = cfg.border.width or 1
        tile_skip = cfg.skip or None

        board_files = []
        boards = await listing.storyboards

        for i, board in enumerate(boards):
            url = board.url
            board_file = os.path.join(self.tmp_dir, f"board.{listing.guid}.{i:02d}.jpg")
            try:
                try:
                    await self.download_file(url, board_file)
                except asyncio.CancelledError:
                    return
                board_files.append(board_file)
            except:
                # sometimes the last one doesn't exist
                if i == len(boards)-1:
                    pass
                else:
                    logger.error("".join(traceback.format_exc()))

        cols = boards[0].cols
        rows = boards[0].rows

        logger.info(board_files)
        thumbnail = await self.thumbnail_for(listing)

        thumbnail = wand.image.Image(filename=thumbnail)
        thumbnail.trim(fuzz=5)
        if thumbnail.width != PREVIEW_WIDTH:
            thumbnail.transform(resize=f"{PREVIEW_WIDTH}x{PREVIEW_HEIGHT}")
        i = 0
        n = 0
        tile_width = 0
        tile_height = 0
        for board_file in board_files:
            # logger.debug(board_file)
            with wand.image.Image(filename=board_file) as img:
                if i == 0:
                    # calculate tile width / height on first iteration since
                    # last board might not be full height
                    tile_width = img.width // cols
                    tile_height = img.height // rows
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
            duration = n/frame_rate
        elif "duration" in cfg:
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
                # ("guid", {
                #     "label": "id",
                #     "width": 11,
                #     "align": "right",
                #     "sort_icon": False,
                # })
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
