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

# class RSSFeedsFilter(ListingFilter):


class RSSItem(model.Item):
    pass


class RSSFeed(URLFeed):

    ITEM_CLASS = RSSItem

    def update(self, limit = None):

        if not limit:
            limit = self.DEFAULT_ITEM_LIMIT

        for item in feedparser.parse(self.url).entries:
            # yield AttrDict(
            #     time =  datetime.fromtimestamp(
            #         mktime(item.published_parsed)
            #     ),
            #     title = item.title,
            #     url = item.link
            # )
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
        # self.updated = datetime.now()

class RSSProviderView(SimpleProviderView):

    PROVIDER_DATA_TABLE_CLASS = CachedFeedProviderDataTable


@with_view(RSSProviderView)
class RSSProvider(PaginatedProviderMixin, CachedFeedProvider):

    # ATTRIBUTES = AttrDict(
    #     CachedFeedProvider.ATTRIBUTES,
    #     **AttrDict(
    #         title = {"width": ("weight", 1)},
    #     )
    # )

    MEDIA_TYPES = {"video"}

    FEED_CLASS = RSSFeed

    def feed_attrs(self, feed_name):

        return dict(url=self.filters.feed[feed_name])
