from streamglob.providers.base import *

from ..exceptions import *
from ..state import *
from .. import config

from .filters import *

import subprocess

import youtube_dl

class YoutubeChannelsFilter(ListingFilter):

    @property
    def values(self):
        return list(state.provider_config.channels.items())


class YouTubeProvider(BaseProvider):

    FILTERS = AttrDict([
        ("channel", YoutubeChannelsFilter),
    ])

    ATTRIBUTES = AttrDict(
        title = {"width": ("weight", 1)},
        # description = {"width": 30},
        # duration = {"width": 10}
    )

    @classproperty
    def NAME(cls):
        return "YouTube"

    def listings(self):

        url = self.filters.channel.value
        # # raise Exception(url)
        # try:
        #     proc = subprocess.Popen(
        #         ["youtube-dl",
        #          "-j",
        #          "--flat-playlist",
        #          "--playlist-end", "10",
        #          url
        #         ],
        #         stdout=subprocess.PIPE,
        #         # stderr=subprocess.PIPE,
        #         stderr=open(os.devnull, 'w')
        #     )
        # except SGException as e:
        #     logger.warning(e)

        # (out, err) = proc.communicate()
        # raise Exception(err)
        # for line in out.decode("utf-8").split("\n"):
        #     yield AttrDict(title=line)

        ydl_opts = {
            # "ignoreerrors": True,
            'quiet': True,
            'extract_flat': "in_playlist",
            # "get-description": True,
            # "get-duration": True,
            # "dump-json": True,
            "playlistend": state.provider_config.get("limit", 100)
        }

        #        url = "https://www.youtube.com/channel/" + self.filters.channel.value
        # url = "https://www.youtube.com/user/ContraPoints"

        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            playlist_dict = ydl.extract_info(url, download=False)
            for video in playlist_dict['entries']:
                yield AttrDict(
                    title = video["title"],
                    url = f"https://youtu.be/{video['url']}",
                    # duration = video["duration"]
                )
