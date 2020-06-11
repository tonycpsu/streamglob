import warnings
from itertools import islice

from .feed import *
from ..exceptions import *
from ..state import *
from .. import config
from .. import model
from .. import session
from .filters import *

from orderedattrdict import AttrDict, DefaultAttrDict
from instaloader import Instaloader, Profile
from pony.orm import *
from limiter import get_limiter, limit


@dataclass
class InstagramMediaSource(model.MediaSource):

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

    @property
    def is_bad(self):
        return any(s in self.locator for s in ["0_0_0", "null.jpg"])


@dataclass
class InstagramMediaListing(FeedMediaListing):

    post_type: str = ""


class InstagramSession(session.StreamSession):

    MAX_BATCH_COUNT = 50
    DEFAULT_BATCH_COUNT = 10

    RATE = 25
    CACPACITY = 100
    CONSUME = 50

    def __init__(self, *args, **kwargs):
        super().__init__(
            *args, **kwargs
        )

        self.limiter = get_limiter(rate=self.RATE, capacity=self.CACPACITY)
        self.loader = None
        self.login()

    def login(self):
        self.loader = Instaloader(
            page_size=self.provider.config.get(
                "fetch_limit",
                self.DEFAULT_FETCH_LIMIT),
            sleep=False
        )

    def profile_from_username(self, user_name):
        return Profile.from_username(self.loader.context, user_name)


class InstagramItem(model.MediaItem):

    post_type = Required(str)

class InstagramFeed(model.MediaFeed):

    ITEM_CLASS = InstagramItem

    POST_TYPE_MAP = {
        "GraphImage": "image",
        "GraphVideo": "video",
        "GraphSidecar": "carousel"
    }

    def fetch(self, cursor=None):

        if self.locator.startswith("@"):
            user_name = self.locator[1:]
        else:
            logger.error(self.locator)
            raise NotImplementedError

        logger.info(f"fetching {self.locator}")

        limit = self.provider.config.get("fetch_limit", self.DEFAULT_FETCH_LIMIT)

        profile = self.session.profile_from_username(user_name)

        for post in islice(profile.get_posts(end_cursor=cursor), limit):
            try:
                post_type = self.POST_TYPE_MAP[post.typename]
            except:
                logger.warn("unknown post type: {post.typeame}")
                continue

            if post_type == "image":
                content = self.provider.new_media_source(
                    post.url, media_type = post_type
                )
            elif post_type == "video":
                content = self.provider.new_media_source(
                    post.video_url, media_type = post_type
                )
            elif post_type == "carousel":
                content = [
                    self.provider.new_media_source(
                        s.video_url or s.display_url,
                        media_type = "video" if s.is_video else "image"
                    )
                    for s in post.get_sidecar_nodes()
                ]
            else:
                logger.warn("unknown post type: {post.typeame}")
                continue

            i = self.items.select(lambda i: i.guid == post.shortcode).first()
            if not i:
                logger.info(f"new: {post.date_utc}")
                new_seen = True
                i = self.ITEM_CLASS(
                    feed = self,
                    guid = post.shortcode,
                    title = post.caption,
                    created = post.date_utc,
                    post_type = post_type,
                    content =  InstagramMediaSource.schema().dumps(
                        content
                        if isinstance(content, list)
                        else [content],
                        many=True
                    ),
                    attrs = dict(
                        cursor = post.end_cursor,
                        short_code = post.shortcode
                    )
                )
                yield i


class InstagramDataTable(CachedFeedProviderDataTable):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        urwid.connect_signal(
            self, "end",
            self.on_end
        )

    @db_session
    def fetch_more(self):
        feed = self.provider.feed
        if feed is None:
            return
        try:
            cursor = feed.items.select().order_by(
                self.provider.ITEM_CLASS.created
            ).first().attrs.get("cursor")
        except AttributeError:
            cursor = None
        feed.update(cursor=cursor)
        self.provider.update_query()
        self.refresh()

    @db_session
    def on_end(self, source, count):
        self.fetch_more()

    def keypress(self, size, key):

        if key == "meta f":
            self.fetch_more()
        elif key == "ctrl k":
            from collections import OrderedDict
            feed = self.provider.feed

            with db_session:
                cursors = (
                    i.attrs["cursor"]
                    for i in feed.items.select().order_by(
                            self.provider.ITEM_CLASS.created
                    ).limit(100)[:]
                )
                raise Exception(list(OrderedDict.fromkeys(cursors))[1])
                cursor = select(
                    (i.created, distinct(i.attrs["cursor"]))
                    for i in feed.items
                ).order_by(
                    lambda i: i[0]
                ).limit(1, 1).first()
                logger.info(f"cursor rec: {cursor}")
        else:
            return super().keypress(size, key)


class InstagramProviderView(SimpleProviderView):

    PROVIDER_DATA_TABLE_CLASS = InstagramDataTable


@with_view(InstagramProviderView)
class InstagramProvider(PaginatedProviderMixin, CachedFeedProvider):

    FEED_CLASS = InstagramFeed

    SUBJECT_LABEL = "caption"

    MEDIA_TYPES = {"image", "video"}

    SESSION_CLASS = InstagramSession

    # VIEW_CLASS = InstagramProviderView

    # def __init__(self, *args, **kwargs):
    #     self.web_api = Client(auto_patch=True, drop_incompat_keys=False)
    #     self.end_cursor = None
    #     super().__init__(*args, **kwargs)

    POST_TYPE_MAP = {
        "image": "img",
        "video": "vid",
        "carousel": "car"
    }
    @property
    def ATTRIBUTES(self):
        attrs = list(super().ATTRIBUTES.items())
        idx = next(i for i, a in enumerate(attrs) if a[0] == "title")
        return AttrDict(
            attrs[:idx]
            + [
                ("post_type", {
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
