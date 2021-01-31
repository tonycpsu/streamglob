from .. import model
from ..session import *
from .live import *

from dataclasses import *

# there's also a TwitchHelix client, but most of that isn't implemented yet
from twitch import TwitchClient

@model.attrclass()
class TwitchMediaListing(LiveStreamMediaListing):

    @property
    def ext(self):
        return "mp4"


class TwitchMediaSourceMixin(object):

    @property
    def helper(self):
        return AttrDict([
            # (None, "streamlink"),
            ("mpv", None),

        ])

@model.attrclass(TwitchMediaSourceMixin)
class TwitchMediaSource(TwitchMediaSourceMixin, model.MediaSource):
    pass

@model.attrclass()
class TwitchChannel(model.MediaChannel):
    pass


class TwitchSession(StreamSession):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = TwitchClient(client_id=self.client_id)


    @property
    def client_id(self):
        return "v5ccc0n21jf0b5nsrblxwszpg3zntd"

    @memo(region="long")
    def get_client_id(self, channel):
        html = requests.get("https://www.twitch.tv/twitch").content.decode('utf-8')
        client_id = re.search('"Client-ID":"(.*?)"', html).groups()[0]
        if not client_id:
            raise Exception
        return client_id


    @memo(region="long")
    def user_name_to_id(self, user_name):
        logger.info(user_name)
        res = self.client.users.translate_usernames_to_ids([user_name])[0]
        return res.get("id")


    def get_playlist(self, channel):

        query = (
            'query PlaybackAccessToken_Template('
            '$login: String!, $isLive: Boolean!, '
            '$vodID: ID!, $isVod: Boolean!, '
            '$playerType: String!) {  streamPlaybackAccessToken('
            'channelName: $login, params: {'
            'platform: "web", playerBackend: '
            '"mediaplayer", playerType: $playerType}'
            ') @include(if: $isLive) {    value    signature    __typename  }  '
            'videoPlaybackAccessToken(id: $vodID, params: {'
            'platform: "web", playerBackend: "mediaplayer", '
            'playerType: $playerType}) @include(if: $isVod) {'
            '    value    signature    __typename  }}'
        )

        data = {
            "operationName": "PlaybackAccessToken_Template",
            "query": query,
            "variables": {
                "isLive": True,
                "login": channel,
                "isVod": False,
                "vodID": "",
                "playerType": "site"
            }
        }
        import requests
        import urllib.parse
        access = requests.post(
            "https://gql.twitch.tv/gql",
            headers={"client-id": self.get_client_id(channel)},
            json=data
        ).json()

        logger.info(access)
        sig = access['data']['streamPlaybackAccessToken']['signature']
        token = access['data']['streamPlaybackAccessToken']['value']

        return f"https://usher.ttvnw.net/api/channel/hls/{channel}.m3u8?sig={sig}&token={urllib.parse.quote(token)}"


    def check_channel(self, username):

        user_id = self.user_name_to_id(username)
        stream = AttrDict(self.client.streams.get_stream_by_user(user_id) or {})
        logger.error(stream)
        if stream:
            return self.provider.new_listing(
                sources = [
                    self.provider.new_media_source(
                        url=self.get_playlist(username),
                        media_type="video"
                    )
                ],
                title = stream.channel.status or stream.channel.description,
                created = stream.created_at
            )
        else:
            return None


class TwitchProvider(LiveStreamProvider):

    SESSION_CLASS = TwitchSession

    MEDIA_TYPES = {"video"}

    def check_channel(self, locator):
        return self.session.check_channel(locator)

    def on_channel_change(self, *args):
        self.refresh()

    # @property
    # def auto_preview(self):
    #     return True
