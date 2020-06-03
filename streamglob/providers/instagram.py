import warnings

from .feed import *
from ..exceptions import *
from ..state import *
from .. import config
from .. import model
from .. import session
from .filters import *

from orderedattrdict import DefaultAttrDict
from instagram_web_api import Client, ClientCompatPatch, ClientConnectionError
from pony.orm import *


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

@dataclass
class InstagramMediaListing(FeedMediaListing):

    post_type: str = ""

class InstagramSession(session.StreamSession):

    MAX_BATCH_COUNT = 50
    DEFAULT_BATCH_COUNT = 50

    def __init__(self, *args, **kwargs):
        super().__init__(
            *args, **kwargs
        )

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self.web_api = Client(
                proxy=self.proxies.get("https") if self.proxies else None,
                auto_patch=True, drop_incompat_keys=False
            )
        self.end_cursors = DefaultAttrDict(lambda: None)

    # @memo(region="long")
    @db_session
    def user_name_to_id(self, user_name):
        try:
            user_id = self.provider.provider_data["user_map"][user_name]
        except KeyError:
            user_id = self.web_api.user_info2(user_name)["id"]
            self.provider.provider_data["user_map"][user_name] = user_id
            self.provider.save_provider_data()
        except:
            raise SGException(f"user id for {user_name} not found")
        return user_id

    def get_feed_items(self, user_name, count=DEFAULT_BATCH_COUNT):

        if count > self.MAX_BATCH_COUNT:
            count = self.MAX_BATCH_COUNT
        try:
            feed = self.web_api.user_feed(
                self.user_name_to_id(user_name), count=count,
                end_cursor = self.end_cursors[user_name]
            )
        except ClientConnectionError as e:
            logger.warn(f"connection error: {e}")
            raise

        for post in feed:
            try:
                cursor = (
                    post["node"]["edge_media_to_comment"]
                    ["page_info"]["end_cursor"]
                )
                if cursor:
                    self.end_cursors[user_name] = cursor
            except KeyError:
                pass

            post_type = None
            post_id = post["node"]["id"]

            try:
                title = post["node"]["caption"]["text"].replace("\n", "")
            except TypeError:
                title = "(no caption)"

            media_type = post["node"]["type"]
            if media_type == "video":
                post_type = "video"
                content = self.provider.new_media_source(
                    post["node"]["videos"]["standard_resolution"]["url"], media_type="video"
                )

            elif media_type == "image":
                if "carousel_media" in post["node"]:
                    post_type = "story"
                    content = [
                        # InstagramMediaSource(m["images"]["standard_resolution"]["url"], media_type="image")
                        self.provider.new_media_source(
                            m["images"]["standard_resolution"]["url"], media_type="image"
                        )
                        if m["type"] == "image"
                        else
                        # InstagramMediaSource(m["video_url"], media_type="video")
                        self.provider.new_media_source(
                            m["video_url"], media_type="video"
                        )
                        if m["type"] == "video"
                        else None
                        for m in post["node"]["carousel_media"]
                    ]
                    title = f"[{len(content)}] {title} "
                else:
                    post_type = "image"
                    # content = InstagramMediaSource(post["node"]["images"]["standard_resolution"]["url"], media_type="image")
                    content = self.provider.new_media_source(
                        post["node"]["images"]["standard_resolution"]["url"], media_type="image"
                    )
                    # raise Exception
            else:
                logger.warn(f"no content for post {post_id}")
                continue

            yield(
                AttrDict(
                    guid = post_id,
                    title = title.strip(),
                    post_type = post_type,
                    created = datetime.fromtimestamp(
                        int(post["node"]["created_time"])
                    ),
                    content = content
                )
            )


class InstagramItem(model.MediaItem):

    post_type = Required(str)

class InstagramFeed(model.MediaFeed):

    ITEM_CLASS = InstagramItem

    def update(self):
        logger.info(self.locator)
        if self.locator.startswith("@"):
            user_name = self.locator[1:]
        else:
            raise NotImplementedError

        limit = self.provider.config.get("limit", self.DEFAULT_ITEM_LIMIT)
        logger.info(f"limit: {limit}")
        last_count = 0
        count = 0
        while(count < limit):
            # instagram API will sometimes give duplicates using end_cursor
            # for pagination, so instead of specifying how many posts to get,
            # we just get a batch of them at a time and break the loop after
            # we've gotten the desired number of posts, or after a batch
            # entirely comprised of duplicates
            for post in self.session.get_feed_items(user_name):
                with db_session:
                    i = self.items.select(lambda i: i.guid == post.guid).first()
                    if not i:
                        i = self.ITEM_CLASS(
                            feed = self,
                            guid = post.guid,
                            title = post.title,
                            created = post.created,
                            post_type = post.post_type,
                            content =  InstagramMediaSource.schema().dumps(
                                post.content
                                if isinstance(post.content, list)
                                else [post.content],
                                many=True
                            )
                        )
                        count += 1
                        yield i
                if count >= limit:
                    break

                # if count == last_count:
                #     logger.info(f"breaking after {count}")
                #     return
                last_count = count


        # logger.info(self.end_cursor)


# class InstagramProviderView(SimpleProviderView):

#     PROVIDER_DATA_TABLE_CLASS = CachedFeedProviderDataTable


# @with_view(InstagramProviderView)
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

    @property
    def ATTRIBUTES(self):
        attrs = list(super().ATTRIBUTES.items())
        idx = next(i for i, a in enumerate(attrs) if a[0] == "title")
        return AttrDict(
            attrs[:idx]
            + [
                ("post_type", {"label": "type", "width": 5})
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
