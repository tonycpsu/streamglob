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

    @property
    @db_session
    def page_token(self):
        return self.attrs.get("page_token", None)

    @page_token.setter
    @db_session
    def page_token(self, value):
        self.attrs["page_token"] = value
        commit()

    async def fetch(
            self,
            limit=None, resume=False,
            reverse=False, replace=False
    ):

        for item in await self.scrape(
                resume=resume, reverse=reverse,
                limit=limit, page_token=self.page_token
        ):
            if not "url" in item:
                raise RuntimeError("scraper item missing required attribute: url")
            url = item["url"]
            guid = item.pop("guid", url)
            content = (
                item["content_format"].format_map(item)
                if "content_format" in item
                else item.pop("content", None)
            )
            listing = AttrDict(
                channel=self,
                guid=guid,
                content=content,
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

        self.page_token = guid

    async def scrape(self, limit=None, resume=False, reverse=False):
        raise NotImplementedError


@model.attrclass()
class BaseScraper(BaseScraperMixin, FeedMediaChannel):
    pass

class PaginatedScraperMixin(object):
    pass
