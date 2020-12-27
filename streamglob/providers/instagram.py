import warnings
from itertools import islice

from .feed import *
from ..exceptions import *
from ..state import *
from .. import config
from .. import model
from .. import player
from .. import session
from .filters import *

from orderedattrdict import AttrDict, DefaultAttrDict
from instaloader import Instaloader, Profile, FrozenNodeIterator
from pony.orm import *
from limiter import get_limiter, limit
import json

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

    @property
    def download_helper(self):
        return lambda d: d.is_simple


@dataclass
class InstagramMediaListing(FeedMediaListing):

    media_type: str = ""


class InstagramSession(session.AuthenticatedStreamSession):

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
        # self.loader = Instaloader(sleep=False)
        self.loader = Instaloader(
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
        )
        self.loader.login(self.username, self.password)
        self.loader.context.raise_all_errors = True

    def profile_from_username(self, user_name):
        return Profile.from_username(self.loader.context, user_name)


class InstagramItem(model.MediaItem):

    media_type = Required(str)

class InstagramFeed(model.MediaFeed):

    ITEM_CLASS = InstagramItem

    POST_TYPE_MAP = {
        "GraphImage": "image",
        "GraphVideo": "video",
        "GraphSidecar": "carousel"
    }

    @property
    def profile(self):

        if self.locator.startswith("@"):
            user_name = self.locator[1:]
        else:
            logger.error(self.locator)
            raise NotImplementedError

        return self.session.profile_from_username(user_name)

    @property
    @db_session
    def end_post_iter(self):
        it = self.profile.get_posts()
        try:
            frozen = json.loads(self.attrs.get("post_iter", None))
            it.thaw(FrozenNodeIterator(**frozen))
        except:
            pass
        return it

    @db_session
    def freeze_post_iter(self, post_iter):
        self.attrs["post_iter"] = json.dumps(post_iter.freeze()._asdict())
        commit()

    def fetch(self, resume=False):

        # if self.locator.startswith("@"):
        #     user_name = self.locator[1:]
        # else:
        #     logger.error(self.locator)
        #     raise NotImplementedError

        logger.info(f"fetching {self.locator}")

        limit = self.provider.config.get("fetch_limit", self.DEFAULT_FETCH_LIMIT)

        if resume:
            post_iter = self.end_post_iter
        else:
            post_iter = self.profile.get_posts()

        for post in islice(post_iter, limit):
            try:
                media_type = self.POST_TYPE_MAP[post.typename]
            except:
                logger.warn("unknown post type: {post.typeame}")
                continue

            if media_type == "image":
                content = self.provider.new_media_source(
                    post.url, media_type = media_type
                )
            elif media_type == "video":
                content = self.provider.new_media_source(
                    post.video_url, media_type = media_type
                )
            elif media_type == "carousel":
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
            # if not i:
            if i:
                continue
            else:
                logger.info(f"new: {post.date_utc}")
                new_seen = True
                i = self.ITEM_CLASS(
                    feed = self,
                    guid = post.shortcode,
                    title = (post.caption or "(no caption)").replace("\n", " "),
                    created = post.date_utc,
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
                yield i

        if resume or not self.attrs.get("post_iter"):
            logger.info("saving post_iter")
            self.freeze_post_iter(post_iter)

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

    @keymap_command()
    async def fetch_more(self):
        @db_session(optimistic=False)
        def fetch():
            feed = self.provider.feed
            if feed is None:
                return
            # try:
            #     cursor = feed.items.select().order_by(
            #         self.provider.ITEM_CLASS.created
            #     ).first().attrs.get("cursor")
            # except AttributeError:
            #     cursor = None

            logger.info("fetching more")
            # self.provider.open_popup("Fetching more posts...")
            feed.update(resume=True)
            self.provider.reset()
            self.provider.close_popup()

        update_task = state.event_loop.run_in_executor(None, fetch)

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
        return self.config.credentials
    
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
