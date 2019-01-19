from ..session import *
from .live import *

# there's also a TwitchHelix client, but most of that isn't implemented yet
from twitch import TwitchClient

class TwitchSession(StreamSession):

    CLIENT_ID = "v5ccc0n21jf0b5nsrblxwszpg3zntd"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.client = TwitchClient(client_id=self.CLIENT_ID)


    @memo(region="long")
    def user_name_to_id(self, user_name):
        res = self.client.users.translate_usernames_to_ids([user_name])[0]
        return res.get("id")

    def check_stream(self, username):

        user_id = self.user_name_to_id(username)
        stream = AttrDict(self.client.streams.get_stream_by_user(user_id) or {})
        if stream:# and "channel" in stream:
            return MediaItem(
                stream = username,
                url = stream.channel.url,
                description = stream.channel.description,
                created = stream.created_at
            )
        else:
            return None


class TwitchProvider(LiveStreamProvider):

    SESSION_CLASS = TwitchSession

    MEDIA_TYPES = {"video"}

    def check_stream(self, locator):
        return self.session.check_stream(locator)
