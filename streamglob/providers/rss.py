from .feed import *

from ..exceptions import *
from ..state import *
from .. import config
from .. import model

from .filters import *

import feedparser
from datetime import datetime
from time import mktime
from pony.orm import *


class RSSItem(model.MediaItem):
    pass


class RSSFeed(model.MediaFeed):

    ITEM_CLASS = RSSItem

    def update(self, limit = None):

        if not limit:
            limit = self.DEFAULT_ITEM_LIMIT

        for item in feedparser.parse(self.locator).entries:
            guid = item.get("guid", item.get("link"))
            i = self.items.select(lambda i: i.guid == guid).first()
            if not i:
                i = self.ITEM_CLASS(
                    feed = self,
                    guid = guid,
                    subject = item.title,
                    created = datetime.fromtimestamp(
                        mktime(item.published_parsed)
                    ),
                    content = item.link
            )

class RSSProvider(PaginatedProviderMixin, CachedFeedProvider):

    MEDIA_TYPES = {"video"}

    FEED_CLASS = RSSFeed
