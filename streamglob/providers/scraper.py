import logging
logger = logging.getLogger(__name__)

from .. import scrapers
from .. import model
from ..state import *
from .base import *
from .feed import *
from .widgets import ProviderDataTable


class ScraperProviderDataTable(CachedFeedProviderDataTable):
    pass

class ScraperProviderView(SimpleProviderView):

    PROVIDER_BODY_CLASS = ScraperProviderDataTable

class ScraperProvider(PaginatedProviderMixin, CachedFeedProvider):

    FEED_CLASS = scrapers.base.BaseScraper

    @property
    def VIEW(self):
        return FeedProviderView(self, CachedFeedProviderBodyView(self, ScraperProviderDataTable(self)))

    def get_channel_class(self, cfg):
        scraper = cfg.scraper
        if scraper:
            return scrapers.get(scraper)
        else:
            raise Exception
