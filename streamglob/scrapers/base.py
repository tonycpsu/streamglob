import logging
logger = logging.getLogger(__name__)

from .. import session
from .. import config
from .. import model
from ..utils import classproperty
from ..providers.feed import FeedMediaChannel

from pony.orm import *
from orderedattrdict import AttrDict

import types


class BaseScraperMixin(object):

    async def fetch(self, limit=None, resume=False, reverse=False, replace=False):
        async for attrs in self.scrape(limit=limit, resume=resume, reverse=reverse):
            if not "url" in attrs:
                raise RuntimeError("scraper item missing required attribute: url")
            url = attrs["url"]
            guid = attrs.pop("guid", url)
            listing = AttrDict(
                channel=self,
                url=url,
                guid=guid,
                sources=[
                    AttrDict(
                        url=attrs.pop("url"),
                        media_type="video"
                    )
                ],
                **attrs
            )
            yield listing

    async def scrape(self, limit=None, resume=False, reverse=False):
        raise NotImplementedError


@model.attrclass()
class BaseScraper(BaseScraperMixin, FeedMediaChannel):
    pass

