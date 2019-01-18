from .live import *

from pyperi import Peri

class PeriscopeItem(LiveStreamItem):
    pass

class PeriscopeFeed(LiveStreamFeed):

    ITEM_CLASS = PeriscopeItem


class PeriscopeProvider(LiveStreamProvider):

    FEED_CLASS = PeriscopeFeed

    MEDIA_TYPES = {"video"}

    def __init__(self, *args, **kwargs):
        self.peri = Peri()
        super().__init__(*args, **kwargs)

    def check_feed(self, feed):

        for item in self.peri.get_user_broadcast_history(
                username=feed
        ):
            yield(
                FeedItem(
                    guid = item["id"],
                    subject = item["status"].strip() or "-",
                    content = f"https://pscp.tv/w/{item['id']}",
                    is_live = item.get("state") == "RUNNING"
                )
            )


    def feed_attrs(self, feed_name):
        return dict(locator=self.filters.feed[feed_name])

    # def check_stream(self, stream):
