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


@model.attrclass()
class RSSMediaSource(model.MediaSource):
    pass
    # @property
    # def helper(self):
    #     return True


class RssMediaListingMixin(object):

    @property
    def body(self):
        return self.description or self.title

@model.attrclass(RssMediaListingMixin)
class RSSMediaListing(RssMediaListingMixin, FeedMediaListing):

    description = Optional(str, index=True)


class RSSSession(session.StreamSession):

    def get_rss_link(item):

        try:
            return next( (e.url for e in item.enclosures) )
        except StopIteration:
            return item.link

    def get_atom_link(item):

        try:
            return next( (l.href for l in item.links) )
        except StopIteration:
            return item.id_

    PARSE_FUNCS = [
        (atoma.parse_rss_bytes, "items", "guid", "pub_date", "description",
         lambda i: i.title,
         get_rss_link
         ),
        (atoma.parse_atom_bytes, "entries", "id_", "published", "content",
         lambda i: i.title.value,
         get_atom_link
         )
    ]

    def parse(self, url):
        try:
            res = self.session.get(url)
            content = res.content
        except requests.exceptions.ConnectionError as e:
            logger.exception(e)
            raise SGFeedUpdateFailedException

        for (parse_func, collection, guid_attr, pub_attr, desc_attr,
             title_func, link_func) in self.PARSE_FUNCS:
            try:
                parsed_feed = parse_func(content)
                for item in getattr(parsed_feed, collection):
                    guid = getattr(item, guid_attr)
                    yield AttrDict(
                        guid=guid,
                        link=link_func(item),
                        title=title_func(item),
                        description=getattr(item, desc_attr),
                        pub_date=getattr(item, pub_attr)
                    )
            except atoma.exceptions.FeedParseError:
                # try next parse function
                continue
            except atoma.exceptions.FeedXMLError as e:
                logger.error(f"{e}: {content}")
                raise SGFeedUpdateFailedException

# class RSSListing(model.TitledMediaListing):
#     pass

class RSSFeed(FeedMediaChannel):

    # @db_session
    async def fetch(self, limit = None, **kwargs):

        if not limit:
            limit = self.DEFAULT_FETCH_LIMIT

        try:
            for item in self.session.parse(self.locator):
                with db_session:
                    guid = getattr(item, "guid", item.link) or item.link
                    i = self.items.select(lambda i: i.guid == guid).first()
                    if not i:
                        source = AttrDict(
                            url=item.link,
                            media_type="video" # FIXME: could be something else
                        )
                        i = AttrDict(
                            channel = self,
                            guid = guid,
                            title = item.title,
                            description = item.description,
                            created = item.pub_date.replace(tzinfo=None),
                            # created = datetime.fromtimestamp(
                            #     mktime(item.published_parsed)
                            # ),
                            sources = [source]
                        )
                        yield i
        except SGFeedUpdateFailedException:
            logger.warn(f"couldn't update feed {self.name}")



class RSSProvider(PaginatedProviderMixin,
                  CachedFeedProvider):

    MEDIA_TYPES = {"video"}

    FEED_CLASS = RSSFeed

    SESSION_CLASS = RSSSession
