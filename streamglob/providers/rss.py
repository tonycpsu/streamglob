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

class SGFeedUpdateFailedException(Exception):
    pass


class RSSMediaSource(model.MediaSource):

    @property
    def helper(self):
        return True

class RSSMediaListing(FeedMediaListing):
    pass

class RSSSession(session.StreamSession):

    def parse(self, url):
        try:
            content = self.session.get(url).content
        except requests.exceptions.ConnectionError as e:
            logger.exception(e)
            raise SGFeedUpdateFailedException
        # print(content)
        try:
            return atoma.parse_rss_bytes(content)
        except atoma.exceptions.FeedXMLError as e:
            logger.error(f"{e}: {content}")
            raise SGFeedUpdateFailedException

class RSSListing(model.TitledMediaListing):
    pass

class RSSFeed(MediaFeed):

    LISTING_CLASS = RSSListing

    # @db_session
    def fetch(self, limit = None):

        if not limit:
            limit = self.DEFAULT_FETCH_LIMIT

        try:
            for item in self.session.parse(self.locator).items:
                with db_session:
                    guid = getattr(item, "guid", item.link) or item.link
                    i = self.items.select(lambda i: i.guid == guid).first()
                    if not i:
                        source = self.provider.new_media_source(
                            url=item.link,
                            media_type="video" # FIXME: could be something else
                        )
                        i = self.LISTING_CLASS(
                            feed = self,
                            guid = guid,
                            title = item.title,
                            created = item.pub_date.replace(tzinfo=None),
                            # created = datetime.fromtimestamp(
                            #     mktime(item.published_parsed)
                            # ),
                            content = RSSMediaSource.schema().dumps(
                                [source],
                                many=True
                            )
                        )
                        yield i
        except SGFeedUpdateFailedException:
            logger.warn(f"couldn't update feed {self.name}")



class RSSProvider(PaginatedProviderMixin,
                  CachedFeedProvider):

    MEDIA_TYPES = {"video"}

    FEED_CLASS = RSSFeed

    SESSION_CLASS = RSSSession
