import logging
logger = logging.getLogger(__name__)
import os

from .feed import *

from ..exceptions import *
from ..state import *
from ..widgets import *
from .. import config
from .. import model
from .. import session

from .filters import *

import youtube_dl


@model.attrclass()
class YouTubeMediaSource(model.MediaSource):

    @property
    def helper(self):
        return AttrDict([
            (None, "youtube-dl"),
            ("mpv", None),
        ])

class SearchResult(AttrDict):

    @property
    def content(self):
        return AttrDict(url=self.url, media_type="video")

class YouTubeSession(session.StreamSession):

    def youtube_dl_query(self, query, offset=None, limit=None):

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

        with youtube_dl.YoutubeDL(ytdl_opts) as ydl:
            playlist_dict = ydl.extract_info(query, download=False)
            if not playlist_dict:
                logger.warn("youtube_dl returned no data")
                return
            for item in playlist_dict['entries']:
                yield SearchResult(
                    guid = item["id"],
                    title = item["title"],
                    url = f"https://youtu.be/{item['url']}"
                )

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
        # channels = [("Search", "search")]
        # channels += list(state.provider_config.feeds.items())
        # return channels
        # raise Exception(list(super().items.items()))
        return AttrDict(super().items, **{"Search": "search"})
        # return list(super().items)

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


@model.attrclass()
class YouTubeMediaListing(FeedMediaListing):
    pass


class YouTubeFeed(MediaFeed):

    LISTING_CLASS = YouTubeMediaListing

    async def fetch(self, limit = None, *args, **kwargs):

        if not limit:
            limit = self.DEFAULT_FETCH_LIMIT

        url = self.locator

        if not url.endswith("/videos"):
            url += "/videos"

        for item in self.session.youtube_dl_query(url, limit=limit):
            with db_session:
                logger.info(item["guid"])
                i = self.items.select(lambda i: i.guid == item["guid"]).first()

                url = item.pop("url")
                if not i:
                    i = AttrDict(
                        feed = self,
                        sources = [
                            AttrDict(url=url, media_type="video")
                        ],
                        **item
                    )
                    logger.info(i)
                    yield i

class TemplateIngoreMissingDict(dict):

     def __missing__(self, key):
         return '{' + key + '}'

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
                        sources = [ AttrDict(locator=r.url, media_type="video") ]
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

    @property
    def auto_preview(self):
        return True
