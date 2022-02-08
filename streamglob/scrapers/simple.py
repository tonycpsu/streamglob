import logging
logger = logging.getLogger(__name__)

from .base import BaseScraper

from orderedattrdict import AttrDict

class SimpleScraper(BaseScraper):

    @property
    def selector(self):
        return self.config.get_value().selector

    def extract_field(self, element, cfg):

        source = cfg["source"]
        if source == "attr":
            return element.attrs[cfg["attr"]]
        elif source == "text":
            return element.text
        else:
            raise NotImplementedError


    def extract_fields(self, element):

        return {
            field: self.extract_field(element, cfg)
            for field, cfg in self.attrs["fields"].items()
        }

    async def scrape(self, limit=None, resume=False, reverse=False):

        html = self.session.get(self.locator).html
        logger.info(html)

        for a in html.find(self.attrs["selector"]):

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
