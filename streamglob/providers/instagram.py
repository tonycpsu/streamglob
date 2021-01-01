import logging
logger = logging.getLogger(__name__)

import warnings
from itertools import islice
from datetime import datetime
from contextlib import contextmanager

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
import limiter
import json

@dataclass
class InstagramMediaSource(model.InflatableMediaSource):

    EXTENSION_RE = re.compile("\.(\w+)\?")

    shortcode: typing.Optional[str] = None
    media_type: str = ""

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

    @property
    def is_bad(self):
        return any(s in (self.locator or self.preview_locator) for s in ["0_0_0", "null.jpg"])

    @property
    def download_helper(self):
        return lambda d: d.is_simple

    def inflate(self):
        post = self.feed.get_post_info(self.shortcode)
        self.content = self.feed.extract_content(post)


@dataclass
class InstagramMediaListing(FeedMediaListing):

    media_type: str = ""


class InstagramSession(session.StreamSession):

    DEFAULT_CACPACITY = 100
    DEFAULT_CONSUME = 50
    DEFAULT_RATE = 10

    def __init__(
            self,
            provider_id,
            capacity = DEFAULT_CACPACITY,
            consume = DEFAULT_CONSUME,
            rate = DEFAULT_RATE,
            *args, **kwargs
    ):
        super().__init__(provider_id, *args, **kwargs)
        self.capacity = capacity
        self.consume = consume
        self.rate = rate
        self._limiter = limiter.get_limiter(
            rate = self.rate, capacity=self.capacity
        )

    @contextmanager
    def limiter(self):
        try:
            with limiter.limit(self._limiter, consume = self.consume):
                yield
        finally:
            pass

class InstagramListing(model.TitledMediaListing):

    media_type = Required(str)

class InstagramFeed(MediaFeed):

    LISTING_CLASS = InstagramListing

    POST_TYPE_MAP = {
        "GraphImage": "image",
        "GraphVideo": "video",
        "GraphSidecar": "carousel"
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
    def looter(self):
        if not hasattr(self, "_looter") or not self._looter or self._looter._username != self.locator[1:]:
            self._looter = ProfileLooter(self.locator[1:])
            if self.provider.config.credentials and not self._looter.logged_in:
                self._looter.login(**self.provider.session_params)
        return self._looter


    def get_post_info(self, shortcode):

        return self.looter.get_post_info(shortcode)

    
    def extract_content(self, post):

        media_type = self.POST_TYPE_MAP[post["__typename"]]

        if media_type == "image":
            content = self.provider.new_media_source(
                url = post.display_url,
                media_type = media_type,
                shortcode = post.shortcode
            )
        elif media_type == "video":
            if post.get("video_url"):
                content = self.provider.new_media_source(
                    url = post.video_url,
                    media_type = media_type,
                    shortcode = post.shortcode
                )
            else:
                content = self.provider.new_media_source(
                    url = None,
                    preview_url = post.display_url,
                    media_type = media_type,
                    shortcode = post.shortcode
                )

        elif media_type == "carousel":
            if post.get('edge_sidecar_to_children'):
                content = [
                    self.provider.new_media_source(
                        url = s.video_url if s.is_video else s.display_url,
                        media_type = "video" if s.is_video else "image",
                        shortcode = post.shortcode
                    )
                    for s in [AttrDict(e['node']) for e in post['edge_sidecar_to_children']['edges']]
                ]
            else:
                content = self.provider.new_media_source(
                    url = None,
                    preview_url = post.display_url,
                    media_type = media_type
                )

        else:
            raise Exception(f"invalid media type: {media_type}")

        return content


    def fetch(self, resume=False):

        logger.info(f"fetching {self.locator}")

        limit = self.provider.config.get("fetch_limit", self.DEFAULT_FETCH_LIMIT)

        try:
            (_, end_cursor) = self.end_cursor if resume else None
        except TypeError:
            end_cursor = None

        logger.info(f"cursor: {end_cursor}")
        self.pages = self.looter.pages(cursor = end_cursor)

        def get_posts(pages):
            for page in pages:
                cursor = page["edge_owner_to_timeline_media"]["page_info"]["end_cursor"]
                for media in self.looter._medias(iter([page])):
                    yield (cursor, AttrDict(media))
                    # code = media["shortcode"]
                    # with self.session.limiter():
                    #     post = self.get_post_info(code)
                    #     yield (cursor, AttrDict(post))

        count = 0
        new_count = 0

        for end_cursor, post in get_posts(self.pages):

            # raise Exception(post)
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

            if i:
                logger.debug(f"old: {created}")
                continue
            else:
                new_seen = True
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

                i = self.LISTING_CLASS(
                    feed = self,
                    guid = post.shortcode,
                    title = (caption or "(no caption)").replace("\n", " "),
                    created = created,
                    media_type = media_type,
                    content =  InstagramMediaSource.schema().dumps(
                        content
                        if isinstance(content, list)
                        else [content],
                        many=True
                    ),
                    attrs = dict(
                        short_code = post.shortcode
                    )
                )
                new_count += 1
                yield i

    @db_session
    def reset(self):
        super().reset()
        if "post_iter" in self.attrs:
            del self.attrs["post_iter"]
            commit()


class InstagramDataTable(CachedFeedProviderDataTable):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        urwid.connect_signal(
            self, "end",
            self.on_end
        )


    @db_session
    def on_end(self, source, count):
        logger.info("on_end")
        # self.fetch_more()
        state.event_loop.create_task(self.fetch_more())


class InstagramProviderView(SimpleProviderView):

    PROVIDER_DATA_TABLE_CLASS = InstagramDataTable


@with_view(InstagramProviderView)
class InstagramProvider(PaginatedProviderMixin, CachedFeedProvider):

    FEED_CLASS = InstagramFeed

    SUBJECT_LABEL = "caption"

    MEDIA_TYPES = {"image", "video"}

    SESSION_CLASS = InstagramSession

    HELPER = "youtube-dl"

    # VIEW_CLASS = InstagramProviderView

    POST_TYPE_MAP = {
        "image": "img",
        "video": "vid",
        "carousel": "car"
    }

    @property
    def session_params(self):
        return dict(
            self.config.get("credentials", {}),
            **self.config.get("rate_limit", {})
        )
    
    @property
    def ATTRIBUTES(self):
        attrs = list(super().ATTRIBUTES.items())
        idx = next(i for i, a in enumerate(attrs) if a[0] == "title")
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
        kwargs["media_type"] = selection.media_type
        return (source, kwargs)

    # def feed_attrs(self, feed_name):

    #     return dict(locator=self.filters.feed[feed_name])
