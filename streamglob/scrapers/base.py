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
from aiostream import stream

class BaseScraperMixin(object):

    async def fetch(self, limit=None, resume=False, reverse=False, replace=False):

        async def fetch_new():
            async for item in self.scrape(resume=resume, reverse=reverse):
                if not self.items.select(lambda i: i.guid == item["guid"]).first():
                    yield item

        async for item in stream.take(fetch_new(), limit):
            if not "url" in item:
                raise RuntimeError("scraper item missing required attribute: url")
            url = item["url"]
            guid = item.pop("guid", url)
            listing = AttrDict(
                channel=self,
                url=url,
                guid=guid,
                sources=[
                    AttrDict(
                        url=item.pop("url"),
                        url_preview=item.pop("url_preview", None),
                        media_type="video"
                    )
                ],
                **item
            )
            yield listing

    async def scrape(self, limit=None, resume=False, reverse=False):
        raise NotImplementedError


@model.attrclass()
class BaseScraper(BaseScraperMixin, FeedMediaChannel):
    pass

