from streamglob.providers.base import *

from ..exceptions import *
from ..state import *
from .. import config

from .filters import *

import youtube_dl

class YouTubeChannelsFilter(ListingFilter):

    @property
    def values(self):
        channels = [("Search", "search")]
        channels += list(state.provider_config.channels.items())
        return channels


class YouTubeProviderDataTable(ProviderDataTable):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.limit = state.provider_config.get("limit")


class YouTubeProviderView(SimpleProviderView):

    PROVIDER_DATA_TABLE_CLASS = YouTubeProviderDataTable


class YouTubeProvider(BaseProvider):

    FILTERS = AttrDict([
        ("channel", YouTubeChannelsFilter),
        ("search", TextFilter)
    ])

    ATTRIBUTES = AttrDict(
        title = {"width": ("weight", 1)},
        # description = {"width": 30},
        # duration = {"width": 10}
    )

    VIEW_CLASS = YouTubeProviderView

    DATA_TABLE_CLASS = YouTubeProviderDataTable

    def listings(self, offset=None, limit=None, *args, **kwargs):

        if self.filters.channel.value == "search":
            if len(self.filters.search.value):
                query = f"ytsearch{offset+self.view.table.limit}:{self.filters.search.value}"
            else:
                return AttrDict()
        else:
            query = self.filters.channel.value

        ytdl_opts = {
            "ignoreerrors": True,
            'quiet': True,
            'extract_flat': "in_playlist",
            "playlistend": self.view.table.limit
        }

        if offset:

            ytdl_opts["playliststart"] = offset+1
            ytdl_opts["playlistend"] = offset + self.view.table.limit

        with youtube_dl.YoutubeDL(ytdl_opts) as ydl:
            playlist_dict = ydl.extract_info(query, download=False)
            for video in playlist_dict['entries']:
                yield AttrDict(
                    title = video["title"],
                    url = f"https://youtu.be/{video['url']}",
                    # duration = video["duration"]
                )
