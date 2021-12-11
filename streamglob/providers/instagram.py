import logging
logger = logging.getLogger(__name__)

import warnings
from itertools import islice
from datetime import datetime
from contextlib import contextmanager
import traceback
import asyncio
from datetime import datetime, timedelta

from .feed import *
from ..exceptions import *
from ..state import *
from .. import config
from .. import model
from .. import programs
from .. import session


from orderedattrdict import AttrDict, DefaultAttrDict
import instagrapi
from pony.orm import *
from aiolimiter import AsyncLimiter
import json


class InstagramSession(session.AsyncStreamSession):

    client_ : typing.Any = None

    DEFAULT_REQUESTS_PER_MINUTE = 60

    def __init__(
            self,
            provider_id,
            # requests_per_minute = DEFAULT_REQUESTS_PER_MINUTE
            *args, **kwargs
    ):
        self.settings = kwargs.get("settings")
        super().__init__(provider_id, *args, **kwargs)
        # self.requests_per_minute = requests_per_minute
        # self._limiter = AsyncLimiter(
        #     requests_per_minute, 60
        # )

    # @property
    # def limiter(self):
    #     return self._limiter

    @property
    def client(self):
        if not hasattr(self, "client_") or not self.client_:
            self.client_ = instagrapi.Client()
            if self.settings:
                logger.info("loading saved session settings")
                self.client_.set_settings(self.settings)
            self.client_.login(**self.provider.session_params)
            self._state.settings = self.settings
            self.save()
        return self.client_

    def __getattr__(self, attr):
        if attr in ["client"]:
            return object.__getattribute__(self, attr)
        return getattr(self.client, attr)


class InstagramMediaSourceMixin(object):

    EXTENSION_RE = re.compile("\.(\w+)\?")

    @property
    def locator_preview(self):
        return self.listing.cover

    @property
    def ext(self):
        try:
            return self.EXTENSION_RE.search(self.locator).groups()[0]
        except IndexError:
            return None
        except AttributeError:
            raise Exception(self.locator)

    @property
    def helper(self):
        return AttrDict([
            (None, "youtube-dl"),
            ("mpv", None),
        ])

    # @property
    # def is_bad(self):
    #     return any(s in (self.locator or self.locator_thumbnail) for s in ["0_0_0", "null.jpg"])

    async def check(self):
        # return True
        if self.created > datetime.now() - timedelta(hours=4):
            return True
        res = self.provider.session.public.head(self.locator)
        return res.status_code == 200

    @property
    def download_helper(self):
        return lambda d: d.is_simple


@model.attrclass()
class InstagramMediaSource(InstagramMediaSourceMixin, model.InflatableMediaSource, FeedMediaSource):

    shortcode = Optional(str)


class InstagramMediaListingMixin(object):

    @property
    def shortcode(self):
        return self.guid

    @property
    def cover(self):
        return f"https://www.instagram.com/p/{self.guid}/media/?size=l"

    async def inflate(self, force=False):
        if self.is_inflated and not force:
            return False
        logger.debug("inflate")
        # import ipdb; ipdb.set_trace()
        async with self.provider.listing_lock:
            with db_session(optimistic=False):
                # FIXME: for some reason I don't feel like digging into right now,
                # self.feed is of type FeedMediaChannel instead of InstagramFeedMediaChannel, so
                # we force the issue here
                feed = self.provider.FEED_CLASS[self.feed.channel_id]
                # with self.provider.session.limiter():
                async with self.provider.session.limiter:
                    post_info = feed.get_post_info(self.shortcode)
                    post = AttrDict(post_info)
                delete(s for s in self.sources)
                listing = self.provider.LISTING_CLASS[self.media_listing_id]
                for i, src in enumerate(feed.extract_content(post)):
                    source = listing.provider.new_media_source(rank=i, **src).attach()
                    listing.sources.add(source)
                listing.is_inflated = True
                commit()
        return True

    @property
    def should_inflate_on_focus(self):
        return self.media_type in ["carousel", "video"]

    def on_focus(self, source_count=None):
        with db_session:
            listing = self.attach() # FIXME
            if (
                    not listing.provider.config.display.get("inflate_on_focus", False)
                    or not listing.should_inflate_on_focus
                    or listing.is_inflated
            ):
                return False

            state.event_loop.create_task(listing.inflate())
            return True

    def refresh(self):
        # import ipdb; ipdb.set_trace()
        async def foo():
            async with self.provider.session.limiter:
                await self.attach().inflate(force=True)

        try:
            loop = asyncio.new_event_loop()
            return loop.run_until_complete(foo())
        finally:
            loop.close()





@model.attrclass(InstagramMediaListingMixin)
class InstagramMediaListing(InstagramMediaListingMixin, FeedMediaListing, model.InflatableMediaListing):

    media_type = Required(str)


class InstagramFeedMediaChannelMixin(object):

    LISTING_CLASS = InstagramMediaListing

    # FIXME:
    # Photo - When media_type=1
    # Video - When media_type=2 and product_type=feed
    # IGTV - When media_type=2 and product_type=igtv
    # Reel - When media_type=2 and product_type=clips
    # Album - When media_type=8
    #
    POST_TYPE_MAP = {
        1: "image",
        2: "video",
        8: "carousel"
    }

    @property
    @db_session
    def end_cursor(self):
        return self.attrs.get("end_cursor", None)

    @db_session
    def save_end_cursor(self, timestamp, end_cursor):
        self.attrs["end_cursor"] = [timestamp, end_cursor]
        commit()

    @property
    def client(self):
        return self.session.client

    def get_post_info(self, shortcode):
        pk = self.client.media_pk_from_code(shortcode)
        try:
            return self.client.media_info(pk)
        except instagrapi.exceptions.LoginRequired:
            import ipdb; ipdb.set_trace()

    @property
    def posts(self):

        try:
            return self.client.user_info_by_username(
                self.locator[1:]
            ).media_count
        except Exception as e:
            logger.warning(e)
            return 0

    def extract_content(self, post):

        media_type = self.POST_TYPE_MAP[post.media_type]

        if media_type == "image":
            content = [
                dict(
                    url=post.thumbnail_url,
                    media_type=media_type,
                    shortcode=post.code
                )
            ]
        elif media_type == "video":
            if post.video_url:
                content = [
                    dict(
                        url=post.video_url,
                        url_thumbnail=post.thumbnail_url,
                        media_type=media_type,
                        shortcode=post.code
                    )
                ]
            else:
                content = [
                    dict(
                        url=None,
                        url_thumbnail=post.thumbnail_url,
                        media_type=media_type,
                        shortcode=post.code
                    )
                ]

        elif media_type == "carousel":
            if post.resources:
                content = [
                    dict(
                        url=r.video_url or r.thumbnail_url,
                        url_thumbnail=r.thumbnail_url,
                        media_type="video" if r.video_url else "image",
                        shortcode=post.code
                    )
                    for r in post.resources
                ]
            else:
                content = [
                    dict(
                        url=None,
                        url_thumbnail=post.thumbnail_url,
                        media_type=media_type
                    )
                ]

        else:
            raise Exception(f"invalid media type: {media_type}")

        return content


    async def fetch(self, limit=None, resume=False, replace=False):

        logger.info(f"fetching {self.locator} {resume}, {replace}")

        # update cached post count
        with db_session:
            self.attrs["posts"] = self.posts

        try:
            (_, end_cursor) = self.end_cursor if resume else None
        except TypeError:
            end_cursor = None

        logger.info(f"cursor: {end_cursor}")

        count = 0
        new_count = 0

        user_id = self.client.user_id_from_username(self.locator[1:])
        medias, end_cursor = self.client.user_medias_paginated(
            user_id, self.provider.config.get("fetch_limit", 50), end_cursor=end_cursor
        )
        # import ipdb; ipdb.set_trace()


        for post in medias:

            count += 1

            logger.info(f"cursor: {end_cursor}")

            logger.debug(f"{count} {new_count} {limit}")

            if new_count >= limit or new_count == 0 and count >= limit:
                break

            created_timestamp = int(post.taken_at.timestamp())

            if end_cursor and (self.end_cursor is None or created_timestamp < self.end_cursor[0]):
                logger.info(f"saving end_cursor: {created_timestamp}, {self.end_cursor[0] if self.end_cursor else None}")
                self.save_end_cursor(created_timestamp, end_cursor)

            created = datetime.utcfromtimestamp(created_timestamp)

            i = self.items.select(lambda i: i.guid == post.code).first()

            if i and not replace:
                logger.debug(f"old: {created}")
                return
            else:
                try:
                    media_type = self.POST_TYPE_MAP[post.media_type]
                except:
                    logger.warn(f"unknown post type: {post.media_type}")
                    continue

                logger.debug(f"new: {media_type} {created}")
                caption = post.caption_text
                content = self.extract_content(post)

                i = dict(
                    channel=self,
                    guid=post.code,
                    title=(caption or "(no caption)").replace("\n", " "),
                    created=created,
                    media_type=media_type,
                    sources=content,
                    attrs=dict(
                        short_code=post.code
                    ),
                    is_inflated=media_type == "image"
                )
                new_count += 1
                yield i

    @db_session
    def reset(self):
        super().reset()
        if "post_iter" in self.attrs:
            del self.attrs["post_iter"]
            commit()


@model.attrclass(InstagramFeedMediaChannelMixin)
class InstagramFeedMediaChannel(InstagramFeedMediaChannelMixin, FeedMediaChannel):
    pass


@keymapped()
class InstagramDataTable(MultiSourceListingMixin, CachedFeedProviderDataTable):

    DETAIL_BOX_CLASS = CachedFeedProviderDetailBox

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # urwid.connect_signal(
        #     self, "end",
        #     self.on_end
        # )

    def keypress(self, size, key):
        return super().keypress(size, key)


    # @db_session
    # def on_end(self, source, count):
    #     logger.info("on_end")
    #     # self.fetch_more()
    #     state.event_loop.create_task(self.fetch_more())




class InstagramProviderBodyView(CachedFeedProviderBodyView):

    @property
    def footer_attrs(self):

        if not len(self.provider.feeds) == 1:
            return super().footer_attrs

        feed = self.provider.feeds[0]

        return AttrDict(super().footer_attrs, **AttrDict([
            ("total", lambda: feed.attrs.get("posts", 0))
        ]))


    @property
    def indicator_bars(self):
        if not len(self.provider.feeds) == 1:
            return super().indicator_bars
        feed = self.provider.feeds[0]
        if not feed:
            return super().indicator_bars

        return super().indicator_bars + [
            ("total", "🌐", "dark gray",
             lambda: (
                 self.footer_attrs["total"]()
                 - self.footer_attrs["fetched"]()
                 # - self.footer_attrs["matching"]()
                 )
             )
        ]


class InstagramProvider(PaginatedProviderMixin, CachedFeedProvider):

    FEED_CLASS = InstagramFeedMediaChannel

    SUBJECT_LABEL = "caption"

    MEDIA_TYPES = {"image", "video"}

    SESSION_CLASS = InstagramSession

    HELPER = "youtube-dl"

    POST_TYPE_MAP = {
        "image": "img",
        "video": "vid",
        "carousel": "car"
    }

    @property
    def VIEW(self):
        return FeedProviderView(self, InstagramProviderBodyView(self, InstagramDataTable(self)))

    @property
    def session_params(self):
        return dict(
            self.config.get("credentials", {}),
            # **self.config.get("rate_limit", {})
        )

    @property
    def ATTRIBUTES(self):
        # attrs = list(super().ATTRIBUTES.items())
        attrs = [
            (k, v.copy())
            for k, v in super().ATTRIBUTES.items()
        ]
        idx, attr = next(  (i, a ) for i, a in enumerate(attrs) if a[0] == "title")
        attr[1]["label"] = "caption"
        # attr[1]["truncate"] = True
        return AttrDict(
            attrs[:idx]
            + [
                ("media_type", {
                    "label": "type",
                    "width": 4,
                    "format_fn": lambda t: self.POST_TYPE_MAP.get(t, t),
                    "align": "right",
                    "sort_icon": False,
                })
            ]
            + attrs[idx:]
        )


    def init_config(self):
        super().init_config()
        if not "user_map" in self.provider_data:
            self.provider_data["user_map"] = {}
            self.save_provider_data()

    def play_args(self, selection, **kwargs):

        source, kwargs = super().play_args(selection, **kwargs)
        # kwargs["media_type"] = selection.media_type
        return (source, kwargs)

    @property
    def PREVIEW_TYPES(self):
        return ["thumbnail", "default", "full"]


    # @property
    # def auto_preview(self):
    #     return True
