from .. import model
from ..state import *

from .base import *
from .feed import *
from .widgets import ProviderDataTable

class SimpleScraperFeedMediaChannelMixin(object):

    @property
    def URL_BASE(self):
        return self.config.get_value().url
        # raise NotImplementedError

    @property
    def selector(self):
        return self.config.get_value().selector

    def extract_field(self, element, cfg):

        source = cfg.source
        if source == "attr":
            return element.attrs[cfg.attr]
        elif source == "text":
            return element.text
        else:
            raise NotImplementedError


    def extract_fields(self, element):

        return {
            field: self.extract_field(element, cfg)
            for field, cfg in self.config.get_value().fields.items()
        }


    async def fetch(self, limit=None, resume=False, reverse=False, *args, **kwargs):

        html = self.provider.session.get(self.URL_BASE).html
        logger.info(html)

        for a in html.find(self.selector):

            logger.info(a)
            fields = self.extract_fields(a)
            url = fields.pop("url")
            # guid = self.get_guid(a)
            # title = self.get_title(a)
            # url = self.get_url(a)

            listing = AttrDict(
                channel=self,
                sources=[
                    AttrDict(
                        url=url,
                        media_type="video"
                    )
                ],
                **fields
            )

            logger.info(listing)
            yield listing


class ScraperFeedMediaChannelMixin(object):
    pass


@model.attrclass()
class ScraperFeedMediaChannel(ScraperFeedMediaChannelMixin, FeedMediaChannel):
    pass

class SimpleScraperFeedMediaChannel(SimpleScraperFeedMediaChannelMixin, ScraperFeedMediaChannel):
    pass


class ScraperProviderDataTable(CachedFeedProviderDataTable):
    pass

class ScraperProviderView(SimpleProviderView):

    PROVIDER_BODY_CLASS = ScraperProviderDataTable

class ScraperProvider(PaginatedProviderMixin, CachedFeedProvider):

    FEED_CLASS = ScraperFeedMediaChannel

    @property
    def VIEW(self):
        return FeedProviderView(self, CachedFeedProviderBodyView(self, ScraperProviderDataTable(self)))
