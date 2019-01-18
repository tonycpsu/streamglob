from .feed import *

from ..exceptions import *
from ..state import *
from .. import config
from .. import model
from .. import session

from .filters import *

import atoma

from datetime import datetime
from time import mktime
from pony.orm import *

class RSSSession(session.StreamSession):

    def parse(self, url):
        content = self.session.get(url).content
        # print(content)
        return atoma.parse_rss_bytes(content)

class RSSItem(model.MediaItem):
    pass

class RSSFeed(model.MediaFeed):

    ITEM_CLASS = RSSItem

    def update(self, limit = None):

        if not limit:
            limit = self.DEFAULT_ITEM_LIMIT

        for item in self.session.parse(self.locator).items:
            guid = getattr(item, "guid", item.link)
            i = self.items.select(lambda i: i.guid == guid).first()
            if not i:
                i = self.ITEM_CLASS(
                    feed = self,
                    guid = guid,
                    subject = item.title,
                    created = item.pub_date.replace(tzinfo=None),
                    # created = datetime.fromtimestamp(
                    #     mktime(item.published_parsed)
                    # ),
                    content = item.link
            )

class RSSProvider(PaginatedProviderMixin, CachedFeedProvider):

    MEDIA_TYPES = {"video"}

    FEED_CLASS = RSSFeed

    SESSION_CLASS = RSSSession
