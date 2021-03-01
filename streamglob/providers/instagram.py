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
from .. import player
from .. import session


from orderedattrdict import AttrDict, DefaultAttrDict
from instalooter.looters import ProfileLooter
from pony.orm import *
from aiolimiter import AsyncLimiter
import json


class InstagramSession(session.StreamSession):

    DEFAULT_REQUESTS_PER_MINUTE = 20

    def __init__(
            self,
            provider_id,
            requests_per_minute = DEFAULT_REQUESTS_PER_MINUTE,
            *args, **kwargs
    ):
        super().__init__(provider_id, *args, **kwargs)
        self.requests_per_minute = requests_per_minute
        self._limiter = AsyncLimiter(requests_per_minute, 60)

    @property
    def limiter(self):
        return self._limiter


class InstagramMediaSourceMixin(object):

    EXTENSION_RE = re.compile("\.(\w+)\?")

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

    def check(self):
        if self.media_type == "image" or self.created > datetime.now() - timedelta(hours=4):
            return True
        return self.provider.session.head(self.locator).status_code == 200

    @property
    def download_helper(self):
        return lambda d: d.is_simple


@model.attrclass(InstagramMediaSourceMixin)
class InstagramMediaSource(InstagramMediaSourceMixin, model.InflatableMediaSource, FeedMediaSource):

    shortcode = Optional(str)


class InstagramMediaListingMixin(object):

    @property
    def shortcode(self):
        return self.guid

    async def inflate(self, force=False):
        if self.is_inflated and not force:
            return False
        logger.debug("inflate")
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
            logger.info("on_focus")
            if (
                    not listing.provider.config.view.get("inflate_on_focus", False)
                    or not listing.should_inflate_on_focus
                    or listing.is_inflated
            ):
                return False

            state.event_loop.create_task(listing.inflate())
            return True

    def refresh(self):
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

    POST_TYPE_MAP = {
        "GraphImage": "image",
        "GraphVideo": "video",
        "GraphSidecar": "carousel"
    }

    looter_ : typing.Any = None

    @property
    @db_session
    def end_cursor(self):
        return self.attrs.get("end_cursor", None)

    @db_session
    def save_end_cursor(self, timestamp, end_cursor):
        self.attrs["end_cursor"] = [timestamp, end_cursor]
        commit()

    @property
    def looter(self):
        if not hasattr(self, "looter_") or not self.looter_ or self.looter_._username != self.locator[1:]:
            self.looter_ = ProfileLooter(self.locator[1:])
            if self.provider.config.credentials and not self.looter_.logged_in:
                self.looter_.login(**self.provider.session_params)
        return self.looter_


    def get_post_info(self, shortcode):

        return self.looter.get_post_info(shortcode)

    @property
    def posts(self):

        url = f"https://www.instagram.com/{self.locator[1:]}/?__a=1"
        data = self.looter.session.get(url).json()
        return data["graphql"]["user"]["edge_owner_to_timeline_media"]["count"]

    def extract_content(self, post):

        media_type = self.POST_TYPE_MAP[post["__typename"]]

        if media_type == "image":
            content = [
                dict(
                    url = post.display_url,
                    media_type = media_type,
                    shortcode = post.shortcode
                )
            ]
        elif media_type == "video":
            if post.get("video_url"):
                content = [
                    dict(
                        url = post.video_url,
                        url_thumbnail = post.display_url,
                        media_type = media_type,
                        shortcode = post.shortcode
                    )
                ]
            else:
                content = [
                    dict(
                        url = None,
                        url_thumbnail = post.display_url,
                        media_type = media_type,
                        shortcode = post.shortcode
                    )
                ]

        elif media_type == "carousel":
            if post.get('edge_sidecar_to_children'):
                content = [
                    dict(
                        url = s.video_url if s.is_video else s.display_url,
                        url_thumbnail = s.display_url,
                        media_type = "video" if s.is_video else "image",
                        shortcode = post.shortcode
                    )
                    for s in [AttrDict(e['node']) for e in post['edge_sidecar_to_children']['edges']]
                ]
            else:
                content = [
                    dict(
                        url = None,
                        url_thumbnail = post.display_url,
                        media_type = media_type
                    )
                ]

        else:
            raise Exception(f"invalid media type: {media_type}")

        return content


    async def fetch(self, limit=None, resume=False, replace=False):

        logger.info(f"fetching {self.locator}")

        # update cached post count
        with db_session:
            self.attrs["posts"] = self.posts

        try:
            (_, end_cursor) = self.end_cursor if resume else None
        except TypeError:
            end_cursor = None

        logger.info(f"cursor: {end_cursor}")
        try:
            self.pages = self.looter.pages(cursor=end_cursor)
        except ValueError:
            self.looter_.logout()
            self.looter_.login(
                username=self.provider.session_params["username"],
                password=self.provider.session_params["password"],
            )
            self.pages = self.looter.pages(cursor=end_cursor)

        # def get_posts(pages):
        #     posts = list()
        #     for page in pages:
        #         cursor = page["edge_owner_to_timeline_media"]["page_info"]["end_cursor"]
        #         for media in self.looter._medias(iter([page])):
        #             posts.append((cursor, AttrDict(media)))
        #     return posts
        #
        def get_posts(pages):
            try:
                for page in pages:
                    cursor = page["edge_owner_to_timeline_media"]["page_info"]["end_cursor"]
                    for media in self.looter._medias(iter([page])):
                        yield (cursor, AttrDict(media))
            except json.decoder.JSONDecodeError:
                logger.error("".join(traceback.format_exc()))
                raise StopIteration

        count = 0
        new_count = 0

        posts = state.event_loop.run_in_executor(
            None, get_posts, self.pages
        )

        for end_cursor, post in await posts:

            count += 1

            logger.info(f"cursor: {end_cursor}")

            logger.debug(f"{count} {new_count} {limit}")

            if new_count >= limit or new_count == 0 and count >= limit:
                break

            created_timestamp = post.get(
                "date", post.get("taken_at_timestamp")
            )

            if end_cursor and (self.end_cursor is None or created_timestamp < self.end_cursor[0]):
                logger.info(f"saving end_cursor: {created_timestamp}, {self.end_cursor[0] if self.end_cursor else None}")
                self.save_end_cursor(created_timestamp, end_cursor)

            created = datetime.utcfromtimestamp(created_timestamp)

            i = self.items.select(lambda i: i.guid == post.shortcode).first()

            if i and not replace:
                logger.debug(f"old: {created}")
                return
            else:
                logger.debug(f"new: {created}")
                caption = (
                    post["edge_media_to_caption"]["edges"][0]["node"]["text"]
                    if "edge_media_to_caption" in post and post["edge_media_to_caption"]["edges"]
                    else  post["caption"]
                    if "caption" in post
                    else None
                )

                try:
                    media_type = self.POST_TYPE_MAP[post["__typename"]]
                except:
                    logger.warn(f"unknown post type: {post.__typename}")
                    continue

                content = self.extract_content(post)

                i = dict(
                    channel = self,
                    guid = post.shortcode,
                    title = (caption or "(no caption)").replace("\n", " "),
                    created = created,
                    media_type = media_type,
                    sources =  content,
                    attrs = dict(
                        short_code = post.shortcode
                    ),
                    is_inflated = media_type == "image"
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
        return AttrDict(super().footer_attrs, **AttrDict([
            ("total", lambda: self.provider.feed.attrs.get("posts", 0))
        ]))

    @property
    def indicator_bars(self):
        return super().indicator_bars + [
            ("total", "🌐", "dark gray",
             lambda: (
                 self.footer_attrs["total"]()
                 - self.footer_attrs["fetched"]()
                 - self.footer_attrs["matching"]())
             )
        ]


class InstagramProviderView(CachedFeedProviderView):

    PROVIDER_BODY_CLASS = InstagramDataTable

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
        return CachedFeedProviderView(self, InstagramProviderBodyView(self, InstagramDataTable(self)))


    @property
    def session_params(self):
        return dict(
            self.config.get("credentials", {}),
            **self.config.get("rate_limit", {})
        )


    def init_config(self):
        super().init_config()
        attrs = list(self.ATTRIBUTES.items())
        idx, attr = next(  (i, a ) for i, a in enumerate(attrs) if a[0] == "title")
        attr[1]["label"] = "caption"
        # attr[1]["truncate"] = True
        self.ATTRIBUTES = AttrDict(
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
