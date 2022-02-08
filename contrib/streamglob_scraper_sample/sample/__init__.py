from streamglob.scrapers.base import BaseScraper

class SampleScraper(BaseScraper):

    """
    Example scraper plugin for streamglob's web scraping provider

    This plugin extracts video links from any site with video links that have
    a path of `/video` in them (e.g. https://www.mlb.com/video)

    Note that this is only meant as a minimal example of how to write plugins
    for scrapers that require more advanced handling. Sites that consist of a
    simple list of HTML links would be more easily handled using the `simple`
    scraper included with streamglob.
    """

    async def scrape(self, limit=None, resume=False, reverse=False):

        html = self.session.get(self.locator).html
        for a in html.find("a[href*='video/']"):
            yield dict(
                url=html._make_absolute(a.attrs["href"]),
                title=a.text or "(untitled)"
            )
