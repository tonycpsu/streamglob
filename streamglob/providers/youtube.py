from streamglob.providers.base import *

from ..exceptions import *
from ..state import *
from .. import config

from .filters import *

import youtube_dl

def youtube_dl_query(query, offset=None, limit=None):

    ytdl_opts = {
        "ignoreerrors": True,
        'quiet': True,
        'extract_flat': "in_playlist",
        "playlistend": limit
    }

    if offset:
        ytdl_opts["playliststart"] = offset+1
        ytdl_opts["playlistend"] = offset + limit

    with youtube_dl.YoutubeDL(ytdl_opts) as ydl:
        playlist_dict = ydl.extract_info(query, download=False)
        for item in playlist_dict['entries']:
            # print(item)
            yield item


class YouTubeChannelsFilter(FeedsFilter):

    @property
    def values(self):
        # channels = [("Search", "search")]
        # channels += list(state.provider_config.feeds.items())
        # return channels
        return AttrDict([("Search", "search")] + list(super().values.items()))


class YouTubeItem(model.Item):

    pass

class YouTubeFeed(model.Feed):

    ITEM_CLASS = YouTubeItem

    def update(self, limit = None):

        if not limit:
            limit = self.DEFAULT_ITEM_LIMIT

        logger.info(f"YoutubeFeed: {self.name}")
        for item in youtube_dl_query(self.name, limit=limit):
            # logger.info(item)
            i = self.items.select(lambda i: i.guid == item["id"]).first()

            if not i:
                i = self.ITEM_CLASS(
                    feed = self,
                    guid = item["id"],
                    subject = item["title"],
                    content = f"https://youtu.be/{item['url']}"
            )
            self.updated = datetime.now()

        # if not limit:
        #     limit = self.DEFAULT_ITEM_LIMIT



# class YouTubeProviderDataTable(ProviderDataTable):

#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)


class YouTubeProviderView(SimpleProviderView):

    PROVIDER_DATA_TABLE_CLASS = CachedFeedProviderDataTable


@with_view(YouTubeProviderView)
class YouTubeProvider(PaginatedProviderMixin,
                      CachedFeedProvider):

    FILTERS = AttrDict([
        ("feed", YouTubeChannelsFilter),
        ("search", TextFilter)
    ])

    ATTRIBUTES = AttrDict(
        created = {"width": 19},
        subject = {"label": "title", "width": ("weight", 1)},
        # description = {"width": 30},
        # duration = {"width": 10}
    )

    FEED_CLASS = YouTubeFeed

    MEDIA_TYPES = {"video"}

    # DATA_TABLE_CLASS = YouTubeProviderDataTable

    def listings(self, offset=None, limit=None, *args, **kwargs):
        if self.filters.feed.value == "search":
            if len(self.filters.search.value):
                query = f"ytsearch{offset+self.view.table.limit}:{self.filters.search.value}"
                return youtube_dl_query(query, offset, limit)
            else:
                return AttrDict()
        else:
            logger.info("calling super listings")
            return super().listings(
                offset=offset, limit=limit, *args, **kwargs
            )


    #     if self.filters.feed.value == "search":
    #         if len(self.filters.search.value):
    #             query = f"ytsearch{offset+self.view.table.limit}:{self.filters.search.value}"
    #         else:
    #             return AttrDict()
    #     else:
    #         query = self.filters.feed.value

    #     ytdl_opts = {
    #         "ignoreerrors": True,
    #         'quiet': True,
    #         'extract_flat': "in_playlist",
    #         "playlistend": self.view.table.limit
    #     }

    #     if offset:

    #         ytdl_opts["playliststart"] = offset+1
    #         ytdl_opts["playlistend"] = offset + self.view.table.limit

    #     with youtube_dl.YoutubeDL(ytdl_opts) as ydl:
    #         playlist_dict = ydl.extract_info(query, download=False)
    #         for video in playlist_dict['entries']:
    #             yield AttrDict(
    #                 title = video["title"],
    #                 url = f"https://youtu.be/{video['url']}",
    #                 # duration = video["duration"]
    #             )
