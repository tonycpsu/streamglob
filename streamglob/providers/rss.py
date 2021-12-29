from datetime import datetime
from time import mktime

import atoma
from pony.orm import *
from mergedeep import merge, Strategy

from .feed import *
from ..exceptions import *
from ..state import *
from .. import config
from .. import model
from .. import session
from .. import utils

from .filters import *


class SGFeedUpdateFailedException(Exception):
    pass

class RSSMediaSourceMixin(object):

    @property
    def locator_preview(self):
        return utils.BLANK_IMAGE_URI
        # try:
        #     return self.listing.body_urls[0]
        # except IndexError:
        #     return utils.BLANK_IMAGE_URI

    @property
    def locator_thumbnail(self):
        return self.url
        # try:
        #     return self.listing.body_urls[0]
        # except IndexError:
        #     return utils.BLANK_IMAGE_URI

    @property
    def download_helper(self):
        return self.listing.feed.config.get_value().get("helper")

    @property
    def locator_play(self):
        return self.listing.locator_play

    @property
    def locator_download(self):
        return self.listing.locator_download


@model.attrclass()
class RSSMediaSource(RSSMediaSourceMixin, FeedMediaSource):

    pass


class RSSMediaListingMixin(object):

    @property
    def locator_download(self):
        with db_session:
            listing = self.attach()
            channel_config = listing.channel.config.get_value()
            link_attr = channel_config.get("download_link")
            if not link_attr:
                return listing.locator
            elif link_attr == "enclosure":
                try:
                    return listing.enclosures[0]
                except IndexError:
                    return listing.locator
            else:

                html = listing.provider.session.get(listing.locator).html

                try:
                    url = html._make_absolute(
                        html.find(link_attr, first=True).attrs["href"]
                    )
                except (KeyError, AttributeError):
                    logger.warning(f"couldn't find link using CSS selector {link_attr}")
                    return listing.locator

                if channel_config.get("fetch_download_link"):
                    res = listing.provider.session.get(url)
                    disposition = res.headers['content-disposition']
                    filename = re.findall("""filename="?([^"]+)"?""", disposition)[0]
                    local_file = os.path.join(listing.provider.tmp_dir, filename)
                    with open(local_file, "wb") as f:
                        f.write(res.content)
                    return local_file
                else:
                    return url

    @property
    def full_content(self):
        return self.provider.session.get(self.locator).text

    @property
    def links(self):
        urls = []
        cfg = self.channel.content_config.links
        if "feed" in cfg.sources:
            urls += super().links

        if "fetch" in cfg.sources:

            link_include_patterns = [
                re.compile(pattern)
                for pattern in cfg.include
            ]
            link_ignore_patterns = [
                re.compile(pattern)
                for pattern in cfg.ignore
            ]

            html = self.provider.session.get(self.locator).html
            urls += html.xpath(
                "|".join(
                    f".//{expr}"
                    for expr in (
                            cfg.fetch.match
                            or [".//a/@href"]
                    )
                )
            )

        urls = [
            u for u in urls
            if (
                not link_include_patterns or any([
                    p.search(u)
                    for p in link_include_patterns
                ])
            ) and (
                not link_ignore_patterns or not any([
                    p.search(u)
                    for p in link_ignore_patterns

                ])
            )
        ][:cfg.get("max", DEFAULT_MAX_LINKS)]

        return urls


@model.attrclass()
class RSSMediaListing(RSSMediaListingMixin, model.ContentMediaListing, FeedMediaListing):

    enclosures = Required(Json, default=[])


class RSSSession(BrowserCookieStreamSessionMixin, session.StreamSession):

    def get_rss_link(item):

        try:
            return item.link
        except StopIteration:
            return next(e.url for e in item.enclosures)

    def get_atom_link(item):

        try:
            return next(l.href for l in item.links)
        except StopIteration:
            return item.id_# ???

    PARSE_FUNCS = [
        (atoma.parse_rss_bytes, "items", "guid", "pub_date", "description",
         lambda i: i.title,
         get_rss_link
         ),
        (atoma.parse_atom_bytes, "entries", "id", "published", "content",
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
                        pub_date=getattr(item, pub_attr),
                        enclosures=[
                            e.url for e in getattr(item, "enclosures", [])
                        ]
                    )
            except atoma.exceptions.FeedParseError:
                # try next parse function
                continue
            except atoma.exceptions.FeedXMLError as e:
                logger.error(f"{e}: {content}")
                raise SGFeedUpdateFailedException

# class RSSListing(model.TitledMediaListing):
#     pass

DEFAULT_MAX_LINKS = 10
class RSSFeed(FeedMediaChannel):

    # @db_session
    async def fetch(self, limit=None, **kwargs):
        n = 0
        include_patterns = [
            re.compile(pattern)
            for pattern in self.content_config.include
        ]
        ignore_patterns = [
            re.compile(pattern)
            for pattern in self.content_config.ignore
        ]

        try:
            for item in self.session.parse(self.locator):
                with db_session:
                    guid = getattr(item, "guid", item.link) or item.link

                    if (
                        include_patterns and not any([
                            p.search(item.link)
                            for p in include_patterns
                        ])
                    ) or (
                        ignore_patterns and any([
                            p.search(item.link)
                            for p in ignore_patterns
                        ])
                    ):
                        continue

                    i = self.items.select(lambda i: i.guid == guid).first()
                    if not i:

                        if not item.link:
                            import ipdb; ipdb.set_trace()
                        item = self.provider.new_listing(
                            channel=self,
                            guid=guid,
                            title=item.title,
                            url=item.link,
                            content=item.content,
                            created=item.pub_date.replace(tzinfo=None),
                            # sources=sources,
                            enclosures=item.enclosures,
                            fetched=None # FIXME
                        )
                        item.sources = [
                            self.provider.new_media_source(
                                # url=item.link,
                                url=body_url,
                                media_type="video" # FIXME: could be something else
                            )
                            for body_url in item.links or [item.link]
                        ]

                        n += 1
                        yield item
                        if n >= limit:
                            return
        except SGFeedUpdateFailedException:
            logger.warn(f"couldn't update feed {self.name}")

    @property
    def content_config(self):
        return config.ConfigTree(
            merge(
                config.ConfigTree(),
                self.provider.config.content,
                self.config.get_value().content,
                # strategy=Strategy.ADDITIVE
            )
        )


@keymapped()
class RSSDataTable(MultiSourceListingMixin, CachedFeedProviderDataTable):

    DETAIL_BOX_CLASS = CachedFeedProviderDetailBox

    # FIXME: sources all use the same link, so we just grab the first.  A more
    # complete fix would address this with provider properties or a separate
    # mixin for multi source listings that share a single link
    def extract_sources(self, listing, **kwargs):
        sources, kwargs = super().extract_sources(listing, **kwargs)
        return ([sources[0]], kwargs)


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

