from streamglob.providers.base import *

from ..exceptions import *
from ..state import *
from .. import config

from .filters import *

import youtube_dl

class YouTubeChannelsFilter(FeedsFilter):

    @property
    def values(self):
        # channels = [("Search", "search")]
        # channels += list(state.provider_config.feeds.items())
        # return channels
        return AttrDict([("Search", "search")] + list(super().values.items()))


class YouTubeProviderDataTable(ProviderDataTable):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class YouTubeProviderView(SimpleProviderView):

    PROVIDER_DATA_TABLE_CLASS = YouTubeProviderDataTable


class FakeProviderMixin(abc.ABC):
    pass

@with_view(YouTubeProviderView)
class YouTubeProvider(PaginatedProviderMixin,
                      FakeProviderMixin,
                      FeedProvider):

    FILTERS = AttrDict([
        ("feed", YouTubeChannelsFilter),
        ("search", TextFilter)
    ])

    ATTRIBUTES = AttrDict(
        title = {"width": ("weight", 1)},
        # description = {"width": 30},
        # duration = {"width": 10}
    )

    MEDIA_TYPES = {"video"}

    DATA_TABLE_CLASS = YouTubeProviderDataTable

    def listings(self, offset=None, limit=None, *args, **kwargs):

        if self.filters.feed.value == "search":
            if len(self.filters.search.value):
                query = f"ytsearch{offset+self.view.table.limit}:{self.filters.search.value}"
            else:
                return AttrDict()
        else:
            query = self.filters.feed.value

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
