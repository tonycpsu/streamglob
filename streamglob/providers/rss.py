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

# FIXME: maybe distribute as resources?
NO_PREVIEW_URI="""\
data:image/png;base64,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\
kD8Agc1jBgaE4SgAAAAASUVORK5CYII=
"""
class RSSMediaSourceMixin(object):

    @property
    def locator(self):
        try:
            return self.listing.body_urls[0]
        except IndexError:
            return NO_PREVIEW_URI

@model.attrclass()
class RSSMediaSource(RSSMediaSourceMixin, FeedMediaSource):
    pass

@model.attrclass()
class RSSMediaListing(model.ContentMediaListing, FeedMediaListing):
    pass

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
                        content=getattr(item, desc_attr),
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
    async def fetch(self, limit=None, **kwargs):
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
                            content = item.content,
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

    CHANNELS_LABEL = "feeds"

    @property
    def FILTERS_OPTIONS(self):
        return super().FILTERS_OPTIONS
