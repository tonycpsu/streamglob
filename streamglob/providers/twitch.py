from .. import model
from ..session import *
from .live import *


# there's also a TwitchHelix client, but most of that isn't implemented yet
from twitch import TwitchClient

class TwitchMediaListing(LiveStreamMediaListing):

    @property
    def ext(self):
        return "mp4"

class TwitchMediaSource(model.MediaSource):
    pass


class TwitchSession(StreamSession):

    CLIENT_ID = "v5ccc0n21jf0b5nsrblxwszpg3zntd"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = TwitchClient(client_id=self.CLIENT_ID)


    @memo(region="long")
    def user_name_to_id(self, user_name):
        res = self.client.users.translate_usernames_to_ids([user_name])[0]
        return res.get("id")

    def check_channel(self, username):

        user_id = self.user_name_to_id(username)
        channel = AttrDict(self.client.streams.get_stream_by_user(user_id) or {})
        if channel:# and "channel" in channel:
            return TwitchMediaListing(
                channel = username,
                content = [TwitchMediaSource(channel.channel.url, media_type="video")],
                description = channel.channel.description,
                created = channel.created_at
            )
        else:
            return None

class TwitchChannel(model.MediaChannel):
    pass


class TwitchProvider(LiveStreamProvider):

    SESSION_CLASS = TwitchSession

    MEDIA_TYPES = {"video"}

    def check_channel(self, locator):
        return self.session.check_channel(locator)
