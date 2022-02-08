import logging
logger = logging.getLogger(__name__)

from .simple import SimpleScraper

from stevedore import extension
from orderedattrdict import AttrDict

import traceback
import types

SCRAPERS = AttrDict()

def log_plugin_exception(manager, entrypoint, exception):
    logger.error("Failed to load {entrypoint}")
    logger.error(
        "".join(
            traceback.format_exception(
                type(exception), exception, exception.__traceback__
            )
        )
    )

def load():

    global SCRAPERS

    logger.info("loading scrapers")

    def make_scraper_channel(scraper_class):
        identifier = scraper_class.__name__.replace("Scraper", "")
        logger.info(f"loading scraper: {identifier}")
        class_name = f"{identifier}ScraperFeedMediaChannel"
        return types.new_class(
            class_name,
            (scraper_class,),
        )

    SCRAPERS["simple"] = make_scraper_channel(SimpleScraper)

    mgr = extension.ExtensionManager(
        namespace='streamglob.scrapers',
        on_load_failure_callback=log_plugin_exception,
    )

    SCRAPERS.update({
        x.name: make_scraper_channel(x.plugin)
        for x in mgr
    })

def get(name):

    try:
        return SCRAPERS.get(name)
    except KeyError:
        raise RuntimeError(f"scraper {name} not found")
