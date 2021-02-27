import logging
logger = logging.getLogger(__name__)
import os
import re
import base64
import binascii
import json
import sqlite3
import pickle
import functools
from contextlib import contextmanager

from http.cookiejar import LWPCookieJar, Cookie
from io import StringIO
import requests
import asyncio
import aiohttp
import lxml
import lxml, lxml.etree
import yaml
from orderedattrdict import AttrDict
import orderedattrdict.yamlutils
from orderedattrdict.yamlutils import AttrDictYAMLLoader
import pytz
from datetime import datetime, timedelta
import dateutil.parser
from pony.orm import *

from . import config
from . import model
from . import providers
from .state import *
from .exceptions import *

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.13; rv:66.0) "
    "Gecko/20100101 Firefox/66.0"
)

class StreamSession(object):
    """
    Top-level stream session interface

    Individual stream providers can be implemented by inheriting from this class
    and implementing methods for login flow, getting streams, etc.
    """

    HEADERS = {
        "User-agent": USER_AGENT
    }

    SESSION_CLASS = requests.Session

    def __init__(
            self,
            provider_id,
            proxies=None,
            *args, **kwargs
    ):

        self.provider_id = provider_id
        self.session = self.SESSION_CLASS()
        self.cookies = LWPCookieJar()
        if not os.path.exists(self.COOKIES_FILE):
            self.cookies.save(self.COOKIES_FILE)
        self.cookies.load(self.COOKIES_FILE, ignore_discard=True)
        self.session.headers = self.HEADERS
        self._state = AttrDict([
            ("proxies", proxies)
        ])
        if proxies:
            self.proxies = proxies
        self._cache_responses = False


    @property
    def provider(self):
        return providers.get(self.provider_id)

    def login(self):
        pass

    @classmethod
    def session_type(cls):
        return cls.__name__.replace("StreamSession", "").lower()

    @classmethod
    def _COOKIES_FILE(cls):
        return os.path.join(config.settings.CONFIG_DIR, f"{cls.session_type()}.cookies")

    @property
    def COOKIES_FILE(self):
        return self._COOKIES_FILE()

    @classmethod
    def _SESSION_FILE(cls):
        return os.path.join(config.settings.CONFIG_DIR, f"{cls.session_type()}.session")

    @property
    def SESSION_FILE(self):
        return self._SESSION_FILE()

    @classmethod
    def new(cls, provider_id, *args, **kwargs):
        try:
            return cls.load(provider_id, **kwargs)
        except (FileNotFoundError, TypeError):
            logger.debug(f"creating new session: {args}, {kwargs}")
            return cls(provider_id, **kwargs)

    @property
    def cookies(self):
        return self.session.cookies

    @cookies.setter
    def cookies(self, value):
        self.session.cookies = value

    @classmethod
    def destroy(cls):
        if os.path.exists(cls.COOKIES_FILE):
            os.remove(cls.COOKIES_FILE)
        if os.path.exists(cls.SESSION_FILE):
            os.remove(cls.SESSION_FILE)

    @classmethod
    def load(cls, provider_id, **kwargs):
        state = yaml.load(open(cls._SESSION_FILE()), Loader=AttrDictYAMLLoader)
        logger.trace(f"load: {cls.__name__}, {state}")
        return cls(provider_id, **dict(kwargs, **state))

    def save(self):
        logger.trace(f"load: {self.__class__.__name__}, {self._state}")
        with open(self.SESSION_FILE, 'w') as outfile:
            yaml.dump(self._state, outfile, default_flow_style=False)
        self.cookies.save(self.COOKIES_FILE)


    def get_cookie(self, name):
        return requests.utils.dict_from_cookiejar(self.cookies).get(name)

    def __getattr__(self, attr):
        if attr in ["delete", "get", "head", "options", "post", "put", "patch"]:
            # return getattr(self.session, attr)
            session_method = getattr(self.session, attr)
            return functools.partial(self.request, session_method)
        # raise AttributeError(attr)

    @db_session
    def request(self, method, url, *args, **kwargs):

        response = None
        use_cache = not self.no_cache and self._cache_responses
        # print(self.proxies)
        if use_cache:
            logger.debug("getting cached response for %s" %(url))

            e = model.CacheEntry.get(url=url)

            if e:
                # (pickled_response, last_seen) = self.cursor.fetchone()

                td = datetime.now() - e.last_seen
                if td.seconds >= self._cache_responses:
                    logger.debug("cache expired for %s" %(url))
                else:
                    response = pickle.loads(e.response)
                    logger.debug("using cached response for %s" %(url))
            else:
                logger.debug("no cached response for %s" %(url))

        if not response:
            response = method(url, *args, **kwargs)
            # logger.trace(dump.dump_all(response).encode("utf-8"))

        if use_cache and not e:
            pickled_response = pickle.dumps(response)
            e = model.CacheEntry(
                url = url,
                response = pickled_response,
                last_seen = datetime.now()
            )

        return response


    @property
    def headers(self):
        return []

    @property
    def proxies(self):
        return self._state.proxies

    @proxies.setter
    def proxies(self, value):
        # Override proxy environment variables if proxies are defined on session
        if value is None:
            self.session.proxies = {}
        else:
            self.session.trust_env = (len(value) == 0)
            self._state.proxies = value
            self.session.proxies.update(value)

    @contextmanager
    def cache_responses(self, duration=model.CACHE_DURATION_DEFAULT):
        self._cache_responses = duration
        try:
            yield
        finally:
            self._cache_responses = False

    def cache_responses_short(self):
        return self.cache_responses(model.CACHE_DURATION_SHORT)

    def cache_responses_medium(self):
        return self.cache_responses(model.CACHE_DURATION_MEDIUM)

    def cache_responses_long(self):
        return self.cache_responses(model.CACHE_DURATION_LONG)


class AsyncStreamSession(StreamSession):

    SESSION_CLASS = aiohttp.ClientSession

    # # FIXME: caching?
    # async def request(self, method, url, *args, **kwargs):
    #     return await method(url, *args, **kwargs)

    # async def get(self, *args, **kwargs):
    #     # method = getattr(self.session, "get")
    #     return await self.session.get(*args, **kwargs)

    def __getattr__(self, attr):
        if attr in ["delete", "get", "head", "options", "post", "put", "patch"]:
            session_method = getattr(self.session, attr)
            return session_method


class AuthenticatedStreamSession(StreamSession):

    def __init__(
            self,
            provider_id,
            username, password,
            *args, **kwargs
    ):
        super(AuthenticatedStreamSession, self).__init__(
            provider_id,
            *args, **kwargs
        )
        self._state.username = username
        self._state.password = password

    @property
    def username(self):
        return self._state.username

    @property
    def password(self):
        return self._state.password



def new(provider, *args, **kwargs):
    # session_class = globals().get(f"{provider.upper()}StreamSession")
    # return session_class.new(provider, *args, **kwargs)
    return provider.SESSION_CLASS.new(*args, **kwargs)

def main():

    # from .state import *
    from . import utils
    import argparse

    global options

    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-v", "--verbose", action="count", default=0,
                        help="verbose logging")
    group.add_argument("-q", "--quiet", action="count", default=0,
                        help="quiet logging")
    options, args = parser.parse_known_args()

    utils.setup_logging(options.verbose - options.quiet)

    # state.session = MLBStreamSession.new()
    # raise Exception(state.session.token)
    raise Exception(PROVIDERS)

    # state.session = NHLStreamSession.new()
    # raise Exception(state.session.session_key)


    # schedule = state.session.schedule(game_id=2018020020)
    # media = state.session.get_epgs(game_id=2018020020)
    # print(json.dumps(list(media), sort_keys=True,
    #                  indent=4, separators=(',', ': ')))


if __name__ == "__main__":
    main()
