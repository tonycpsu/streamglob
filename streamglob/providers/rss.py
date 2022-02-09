from datetime import datetime
from time import mktime
import http.cookiejar

import atoma
from pony.orm import *
from mergedeep import merge, Strategy

from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.by import By
from selenium.common.exceptions import TimeoutException


from .feed import *
from ..exceptions import *
from ..state import *
from .. import config
from .. import model
from .. import session
from .. import utils

from .filters import *

class WebHelper(object):

    def __init__(self, profile_path=None, show_browser=False):
        self.profile_path = profile_path
        self.show_browser = show_browser

    @property
    def driver(self):
        if not getattr(self, "_driver", None):
            options = Options()
            if not self.show_browser:
                options.headless = True
            if self.profile_path:
                options.set_preference('profile', self.profile_path)
            self._driver = webdriver.Firefox(options=options)
        return self._driver

    def run_script(self, url, script, params):

        self.driver.get(url)

        for command in script:
            print(command)
            action = command.action
            target = next(
                (getattr(By, k.upper()), v)
                for k, v in command.target.items()
            )
            if action == "wait":
                timeout = command.get("timeout", 1)
                try:
                    myElem = WebDriverWait(self.driver, timeout).until(
                        EC.presence_of_element_located(target)
                    )
                except TimeoutException:
                    print("Loading took too much time!")

            elif action == "send_keys":
                self.driver.find_element(*target).send_keys(
                    command.value.format_map(params)
                )
            elif action == "click":
                self.driver.find_element(*target).click()
            else:
                raise NotImplementedError(action)

    def fetch_cookies(self, cookies=None):

        return [
            self.to_cookielib_cookie(c)
            for c in self.driver.get_cookies()
            if cookies is None or c.get("name") in cookies
        ]

    def quit(self):
        self.driver.quit()

    @staticmethod
    def to_cookielib_cookie(selenium_cookie):
        return http.cookiejar.Cookie(
            version=0,
            name=selenium_cookie['name'],
            value=selenium_cookie['value'],
            port='80',
            port_specified=False,
            domain=selenium_cookie['domain'],
            domain_specified=True,
            domain_initial_dot=False,
            path=selenium_cookie['path'],
            path_specified=True,
            secure=selenium_cookie['secure'],
            expires=selenium_cookie['expiry'],
            discard=False,
            comment=None,
            comment_url=None,
            rest={},
            rfc2109=False
        )


class SGFeedUpdateFailedException(Exception):
    pass

class RSSMediaSourceMixin(object):

    @property
    def download_helper(self):
        return self.listing.feed.config.get_value().get("helper")

    @property
    def locator_preview(self):
        return self.locator

    @property
    def locator_thumbnail(self):
        return self.locator
        # try:
        #     return self.listing.body_urls[0]
        # except IndexError:
        #     return utils.BLANK_IMAGE_URI

    @property
    def locator_play(self):
        if self.play_listing:
            return self.listing.locator_play
        else:
            return self.locator

    @property
    def locator_download(self):
        return self.listing.locator_download


@model.attrclass()
class RSSMediaSource(RSSMediaSourceMixin, FeedMediaSource):

    play_listing = Optional(bool)


class RSSMediaListingMixin(object):
    # FIXME: way too much fetching from DB here

    @property
    def channel_config(self):
        with db_session:
            listing = self.attach()
            return listing.channel.config.get_value()


    @property
    def locator_download(self):

        with db_session:
            listing = self.attach()
            link_attr = self.channel_config.get("download_link")
            login_cfg = self.channel_config.login

            if not link_attr:
                return listing.locator

            elif link_attr == "enclosure":
                try:
                    return listing.enclosures[0]
                except IndexError:
                    return listing.locator

            else:

                html = listing.provider.session.get(listing.locator).html

                try:
                    url = html._make_absolute(
                        html.find(link_attr, first=True).attrs["href"]
                    )
                except (KeyError, AttributeError):
                    if login_cfg:
                        if login_cfg.method == "session":
                            self.session_login()
                        elif login_cfg.method == "browser":
                            self.browser_login()
                        else:
                            raise NotImplementedError

                    html = listing.provider.session.get(listing.locator).html

                    try:
                        url = html._make_absolute(
                            html.find(link_attr, first=True).attrs["href"]
                        )
                    except (KeyError, AttributeError):
                        logger.warning(f"couldn't find link using CSS selector {link_attr}")
                        return listing.locator


            if self.channel_config.get("fetch_download_link"):
                res = listing.provider.session.get(url)
                disposition = res.headers['content-disposition']
                filename = re.findall("""filename="?([^"]+)"?""", disposition)[0]
                local_file = os.path.join(listing.provider.tmp_dir, filename)
                with open(local_file, "wb") as f:
                    f.write(res.content)
                return local_file
            else:
                return url

    def session_login(self):

        cfg = self.channel_config.login
        url = cfg.url
        credentials = cfg.credentials
        params = cfg.params.copy()
        extract = cfg.extract
        values = {}

        if extract:
            url = cfg.extract.url or cfg.url
            res = self.provider.session.get(url)
            values = {
                param: res.html.xpath(expr, first=True)
                for param, expr in extract.params.items()
            }

        data = {
            k: v.format_map(dict(credentials, **values))
            for k, v in params.items()
        }

        res = self.provider.session.post(
            url,
            data=data
        )

        res.raise_for_status()
        self.provider.session.save_cookies()
        return

    def browser_login(self):

        cfg = self.channel_config.login

        self.provider.web_helper.run_script(
            cfg.url,
            cfg.script,
            cfg.credentials
        )
        cookies = self.provider.web_helper.fetch_cookies(
            cfg.cookies or None
        )

        for c in cookies:
            self.provider.session.cookies.set_cookie(c)

        self.provider.session.save_cookies()


    @property
    def full_content(self):
        return self.provider.session.get(self.locator).text

    @property
    def links(self):

        urls = []
        cfg = self.channel.content_config.links

        if "feed" in cfg.sources:
            urls += super().links

        if "fetch" in cfg.sources:

            link_include_patterns = [
                re.compile(pattern)
                for pattern in cfg.include
            ]
            link_ignore_patterns = [
                re.compile(pattern)
                for pattern in cfg.ignore
            ]

            html = self.provider.session.get(self.locator).html

            if cfg.fetch.session_test:
                expr = cfg.fetch.session_test
                found = html.find(expr, first=True)
                if not found:
                    self.session_login()
                    html = self.provider.session.get(self.locator).html
                    found = html.find(expr, first=True)
                    if not found:
                        logger.warning(f"couldn't find {expr} after login")

            urls += html.xpath(
                "|".join(
                    f".//{expr}"
                    for expr in (
                            cfg.fetch.match
                            or [
                                ".//a/@href",
                                ".//img/@src"
                            ]
                    )
                )
            )

        if len(self.subject_rules):
            max_links = cfg.max.get("subjects", DEFAULT_MAX_LINKS)
        else:
            max_links = cfg.max.get("others", DEFAULT_MAX_LINKS)

        urls = [
            u for u in dict.fromkeys(urls)
            if (
                not link_include_patterns or any([
                    p.search(u)
                    for p in link_include_patterns
                ])
            ) and (
                not link_ignore_patterns or not any([
                    p.search(u)
                    for p in link_ignore_patterns

                ])
            )
        ][:max_links]

        return urls


@model.attrclass()
class RSSMediaListing(RSSMediaListingMixin, FeedMediaListing):

    enclosures = Required(Json, default=[])


class RSSSession(session.StreamSession):

    def get_rss_link(item):

        try:
            return item.link
        except StopIteration:
            return next(e.url for e in item.enclosures)

    def get_atom_link(item):

        try:
            return next(l.href for l in item.links)
        except StopIteration:
            return item.id_# ???

    PARSE_FUNCS = [
        (atoma.parse_rss_bytes, "items", "guid", "pub_date", "description",
         lambda i: i.title,
         get_rss_link
         ),
        (atoma.parse_atom_bytes, "entries", "id", "published", "content",
         lambda i: i.title.value,
         get_atom_link
         )
    ]

    def parse(self, url):
        try:
            res = self.session.get(url)
            content = res.content
        except requests.exceptions.ConnectionError as e:
            logger.exception(e)
            raise SGFeedUpdateFailedException

        for (parse_func, collection, guid_attr, pub_attr, desc_attr,
             title_func, link_func) in self.PARSE_FUNCS:
            try:
                parsed_feed = parse_func(content)
                for item in getattr(parsed_feed, collection):
                    guid = getattr(item, guid_attr)
                    yield AttrDict(
                        guid=guid,
                        link=link_func(item),
                        title=title_func(item),
                        content=getattr(item, desc_attr),
                        pub_date=getattr(item, pub_attr),
                        enclosures=[
                            e.url for e in getattr(item, "enclosures", [])
                        ]
                    )
            except atoma.exceptions.FeedParseError:
                # try next parse function
                continue
            except atoma.exceptions.FeedXMLError as e:
                logger.error(f"{e}: {content}")
                raise SGFeedUpdateFailedException

# class RSSListing(model.TitledMediaListing):
#     pass

DEFAULT_MAX_LINKS = 10
class RSSFeed(FeedMediaChannel):

    # @db_session
    async def fetch(self, limit=None, **kwargs):
        n = 0
        include_patterns = [
            re.compile(pattern)
            for pattern in self.content_config.include
        ]
        ignore_patterns = [
            re.compile(pattern)
            for pattern in self.content_config.ignore
        ]

        try:
            for item in self.session.parse(self.locator):
                with db_session:
                    guid = getattr(item, "guid", item.link) or item.link

                    if (
                        include_patterns and not any([
                            p.search(item.link)
                            for p in include_patterns
                        ])
                    ) or (
                        ignore_patterns and any([
                            p.search(item.link)
                            for p in ignore_patterns
                        ])
                    ):
                        continue

                    i = self.items.select(lambda i: i.guid == guid).first()
                    if not i:

                        if not item.link:
                            import ipdb; ipdb.set_trace()
                        item = self.provider.new_listing(
                            channel=self,
                            guid=guid,
                            title=item.title,
                            url=item.link,
                            locator=item.link, # FIXME: have to specify twice because pydantic doesn't handle properties
                            content=item.content,
                            created=item.pub_date.replace(tzinfo=None),
                            # sources=sources,
                            enclosures=item.enclosures,
                            fetched=None # FIXME
                        )
                        item.sources = [
                            self.provider.new_media_source(
                                # url=item.link,
                                url=body_url,
                                media_type="video", # FIXME: could be something else
                                play_listing=self.content_config.play_listing or False
                            )
                            for body_url in item.links or [item.locator]
                        ]

                        n += 1
                        yield item
                        if n >= limit:
                            return
        except SGFeedUpdateFailedException:
            logger.warn(f"couldn't update feed {self.name}")

    @property
    def content_config(self):
        return config.ConfigTree(
            merge(
                config.ConfigTree(),
                self.provider.config.content,
                self.config.get_value().content,
                # strategy=Strategy.ADDITIVE
            )
        )


@keymapped()
class RSSDataTable(MultiSourceListingMixin, CachedFeedProviderDataTable):

    # DETAIL_BOX_CLASS = CachedFeedProviderDetailBox

    # FIXME: sources all use the same link, so we just grab the first.  A more
    # complete fix would address this with provider properties or a separate
    # mixin for multi source listings that share a single link
    def extract_sources(self, listing, **kwargs):
        sources, kwargs = super().extract_sources(listing, **kwargs)
        return ([sources[0]], kwargs)


class RSSProviderBodyView(CachedFeedProviderBodyView):
    pass

class RSSProvider(PaginatedProviderMixin,
                  CachedFeedProvider):

    MEDIA_TYPES = {"video"}

    FEED_CLASS = RSSFeed

    SESSION_CLASS = RSSSession

    CHANNELS_LABEL = "feeds"

    @property
    def VIEW(self):
        return FeedProviderView(self, RSSProviderBodyView(self, RSSDataTable(self)))

    @property
    def FILTERS_OPTIONS(self):
        return super().FILTERS_OPTIONS

    @property
    def web_helper(self):
        if not getattr(self, "_web_helper", None):
            self._web_helper = WebHelper(
                profile_path=self.config.browser_profile or None,
                show_browser=self.config.show_browser or None,
            )
        return self._web_helper
