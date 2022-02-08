import logging
logger = logging.getLogger(__name__)

from .base import BaseScraper

from orderedattrdict import AttrDict

class SimpleScraper(BaseScraper):

    @property
    def selector(self):
        return self.config.get_value().selector

    def extract_field(self, element, cfg):

        source = cfg.get("source")
        if source == "attr":
            return element.attrs[cfg["attr"]]
        elif source == "text":
            return element.text
        else:
            return None


    def extract_fields(self, element):

        fields = self.attrs["fields"].copy()
        url_cfg = fields.pop("url")
        url = self.extract_field(element, url_cfg)

        guid_cfg = fields.pop("guid", {})
        guid = self.extract_field(element, guid_cfg) or url

        return dict(
            url=url,
            guid=guid,
            **{
                field: self.extract_field(element, cfg)
                for field, cfg in fields.items()
            }
        )

    async def scrape(self, limit=None, resume=False, reverse=False):

        html = self.session.get(self.locator).html
        logger.info(html)

        for a in html.find(self.attrs["selector"]):
            yield self.extract_fields(a)
