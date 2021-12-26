from .feed import *

from ..exceptions import *
from ..state import *
from .. import config
from .. import model
from .. import session
from .. import utils

from .filters import *

import atoma

from datetime import datetime
from time import mktime
from pony.orm import *

class SGFeedUpdateFailedException(Exception):
    pass

class RSSMediaSourceMixin(object):

    @property
    def locator(self):
        return utils.BLANK_IMAGE_URI
        # try:
        #     return self.listing.body_urls[0]
        # except IndexError:
        #     return utils.BLANK_IMAGE_URI

    @property
    def locator_thumbnail(self):
        try:
            return self.listing.body_urls[0]
        except IndexError:
            return utils.BLANK_IMAGE_URI


@model.attrclass()
class RSSMediaSource(RSSMediaSourceMixin, FeedMediaSource):
    pass

@model.attrclass()
class RSSMediaListing(model.ContentMediaListing, FeedMediaListing):
    pass

class RSSSession(BrowserCookieStreamSessionMixin, session.StreamSession):

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
                        sources = [
                            AttrDict(
                                url=item.link,
                                url_preview=body_url,
                                media_type="video" # FIXME: could be something else
                            )
                            for body_url in (
                                    (
                                        RSSMediaListing.extract_urls(item.content)
                                        or
                                        [None]
                                    )
                                    if item.content
                                    else [None]
                                )
                        ]
                        logger.info(sources)
                        # source = AttrDict(
                        #     url=item.link,
                        #     media_type="video" # FIXME: could be something else
                        # )
                        i = AttrDict(
                            channel = self,
                            guid = guid,
                            title = item.title,
                            content = item.content,
                            created = item.pub_date.replace(tzinfo=None),
                            # created = datetime.fromtimestamp(
                            #     mktime(item.published_parsed)
                            # ),
                            # sources = [source]
                            sources = sources
                        )
                        yield i
        except SGFeedUpdateFailedException:
            logger.warn(f"couldn't update feed {self.name}")

@keymapped()
class RSSDataTable(MultiSourceListingMixin, CachedFeedProviderDataTable):

    DETAIL_BOX_CLASS = CachedFeedProviderDetailBox

    # def __init__(self, *args, **kwargs):
    #     super().__init__(*args, **kwargs)
    #     # urwid.connect_signal(
    #     #     self, "end",
    #     #     self.on_end
    #     # )

    # def keypress(self, size, key):
    #     return super().keypress(size, key)



class RSSProviderBodyView(CachedFeedProviderBodyView):
    pass

class RSSProvider(PaginatedProviderMixin,
                  CachedFeedProvider):

    MEDIA_TYPES = {"video"}

    FEED_CLASS = RSSFeed

    SESSION_CLASS = RSSSession

    CHANNELS_LABEL = "feeds"

    @property
    def VIEW(self):
        return FeedProviderView(self, RSSProviderBodyView(self, RSSDataTable(self)))


    @property
    def FILTERS_OPTIONS(self):
        return super().FILTERS_OPTIONS
