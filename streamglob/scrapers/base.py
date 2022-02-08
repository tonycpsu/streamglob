import logging
logger = logging.getLogger(__name__)

from .. import session
from .. import config
from .. import model
from ..utils import classproperty
from ..providers.feed import FeedMediaChannel

from pony.orm import *

import types


class BaseScraperMixin(object):

    async def fetch(self, limit=None, resume=False, reverse=False, replace=False):
        logger.info(self.scrape.__code__)
        async for listing in self.scrape(limit=limit, resume=resume, reverse=reverse):
            yield listing

    async def scrape(self, limit=None, resume=False, reverse=False):
        raise NotImplementedError


@model.attrclass()
class BaseScraper(BaseScraperMixin, FeedMediaChannel):
    pass

