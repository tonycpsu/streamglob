import logging
logger = logging.getLogger(__name__)
import os

from .feed import *

from ..exceptions import *
from ..state import *
from .. import config
from .. import model
from .. import session

from .filters import *

import youtube_dl

class YoutubeMediaListing(MediaListing):

    @property
    def download_filename(self):

        path = self._provider.config.get_path("output.path")
        template = self._provider.config.get_path("output.template")
        if template:
            outfile = template.format_map(d)
            return os.path.join(path, template)
        elif path:
            return path
        else:
            # youtube-dl generates a sane filename from the metadata by default
            return None


class YouTubeMediaSource(model.MediaSource):
    pass

class SearchResult(AttrDict):
    pass

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
                    subject = item["title"],
                    url = f"https://youtu.be/{item['url']}"
                )

class YouTubeChannelsDropdown(urwid.WidgetWrap):

    signals = panwid.Dropdown.signals

    SEARCH_LABEL = "Search: "

    def __init__(self, items, *args, **kwargs):

        signals = ["change"]

        self.dropdown = panwid.Dropdown(items, *args, **kwargs)
        self.search_label = urwid.Text(self.SEARCH_LABEL)
        self.search_edit = TextFilterWidget("")
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
        urwid.connect_signal(self.dropdown, "change", self.on_channel_change)
        urwid.connect_signal(self.search_edit, "select", self.on_edit_select)
        self.hide_search()

    def __getattr__(self, attr):
        if attr in ["cycle", "select_label", "select_value", "selected_label", "items"]:
            return getattr(self.dropdown, attr)
        raise AttributeError

    def on_channel_change(self, source, dropdown, value):
        if value == "search":
            self.show_search()
        else:
            self.hide_search()
        self._emit("change", self.value)

    def on_edit_select(self, source, value):
        self._emit("change", self.value)

    def show_search(self):
        self.search_placeholder.original_widget = self.search_widget

    def hide_search(self):
        self.search_placeholder.original_widget = urwid.Text("")

    @property
    def search(self):
        return self.search_edit.get_text()[0]

    @property
    def channel(self):
        return self.dropdown.selected_value

    @property
    def value(self):
        return self.channel
        # if self.channel == "search":
        #     return ("search", self.search)
        # else:
        #     return self.channel


class YouTubeChannelsFilter(FeedsFilter):

    WIDGET_CLASS = YouTubeChannelsDropdown

    @property
    def values(self):
        # channels = [("Search", "search")]
        # channels += list(state.provider_config.feeds.items())
        # return channels
        return AttrDict(list(super().values.items()) + [("Search", "search")])
        return list(super().values.items())

    @property
    def value(self):
        return self.widget.value

    @property
    def search(self):
        return self.widget.search

    @property
    def channel(self):
        return self.widget.channel


class YouTubeItem(model.MediaItem):
    pass


class YouTubeFeed(model.MediaFeed):

    ITEM_CLASS = YouTubeItem

    def update(self, limit = None):

        if not limit:
            limit = self.DEFAULT_ITEM_LIMIT

        for item in self.session.youtube_dl_query(self.locator, limit=limit):
            i = self.items.select(lambda i: i.guid == item["guid"]).first()

            url = item.pop("url")
            if not i:
                i = self.ITEM_CLASS(
                    feed=self,
                    content = YouTubeMediaSource.schema().dumps(
                        [YouTubeMediaSource(url, media_type="video")],
                        many=True
                    ),
                    **item
                )

class TemplateIngoreMissingDict(dict):

     def __missing__(self, key):
         return '{' + key + '}'

class YouTubeProvider(PaginatedProviderMixin,
                      CachedFeedProvider):

    FILTERS = AttrDict([
        ("feed", YouTubeChannelsFilter),
        ("status", ItemStatusFilter)
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
            if len(self.filters.feed.search):
                query = f"ytsearch{offset+self.view.table.limit}:{self.filters.feed.search}"
                return [
                    SearchResult(r)
                    for r in self.session.youtube_dl_query(query, offset, limit)
                ]
            else:
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
