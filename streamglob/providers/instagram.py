import warnings

from .feed import *
from ..exceptions import *
from ..state import *
from .. import config
from .. import model
from .. import session
from .filters import *

from orderedattrdict import DefaultAttrDict
from instagram_web_api import Client, ClientCompatPatch, ClientError
from pony.orm import *

class InstagramMediaSource(model.MediaSource):

    @property
    def helper(self):
        if self.media_type == "image":
            return None
        return True

class InstagramMediaListing(MediaListing):
    pass

class InstagramSession(session.StreamSession):

    BATCH_COUNT = 25

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

    @memo(region="long")
    def user_name_to_id(self, user_name):
        try:
            user_id = self.web_api.user_info2(user_name)["id"]
        except:
            raise SGException(f"user id for {user_name} not found")
        return user_id

    def get_feed_items(self, user_name, count=BATCH_COUNT):

        feed = self.web_api.user_feed(
            self.user_name_to_id(user_name), count=self.BATCH_COUNT,
            end_cursor = self.end_cursors[user_name]
        )

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

            post_id = post["node"]["id"]

            try:
                subject = post["node"]["caption"]["text"].replace("\n", "")
            except TypeError:
                subject = "(no caption)"

            media_type = post["node"]["type"]
            if media_type == "video":
                content = InstagramMediaSource(post["node"]["link"], media_type="video")
            elif media_type == "image":
                if "carousel_media" in post["node"]:
                    content = [
                        InstagramMediaSource(m["images"]["standard_resolution"]["url"], media_type="image")
                        if m["type"] == "image"
                        else
                        InstagramMediaSource(m["video_url"], media_type="video")
                        if m["type"] == "video"
                        else None
                        for m in post["node"]["carousel_media"]
                    ]
                else:
                    content = InstagramMediaSource(post["node"]["images"]["standard_resolution"]["url"], media_type="image")
                    # raise Exception
            else:
                logger.warn(f"no content for post {post_id}")
                continue

            yield(
                InstagramMediaListing(
                    guid = post_id,
                    subject = subject,
                    created = datetime.fromtimestamp(
                        int(post["node"]["created_time"])
                    ),
                    media_type = media_type,
                    content = content
                )
            )


class InstagramItem(model.MediaItem):

    media_type = Required(str)

class InstagramFeed(model.MediaFeed):

    ITEM_CLASS = InstagramItem

    @db_session
    def update(self, limit = None):

        if self.locator.startswith("@"):
            user_name = self.name[1:]
        else:
            raise NotImplementedError

        if not limit:
            limit = self.DEFAULT_ITEM_LIMIT

        last_count = 0
        count = 0
        while(count < limit):
            # instagram API will sometimes give duplicates using end_cursor
            # for pagination, so instead of specifying how many posts to get,
            # we just get a batch of them at a time and break the loop after
            # we've gotten the desired number of posts, or after a batch
            # entirely comprised of duplicates
            for post in self.session.get_feed_items(user_name):
                i = self.items.select(lambda i: i.guid == post.guid).first()
                if not i:
                    i = self.ITEM_CLASS(
                        feed = self,
                        guid = post.guid,
                        subject = post.subject,
                        created = post.created,
                        media_type = post.media_type,
                        content =  InstagramMediaSource.schema().dumps(
                            post.content
                            if isinstance(post.content, list)
                            else [post.content],
                            many=True
                        )
                    )
                    count += 1

                if count == last_count:
                    logger.info(f"breaking after {count}")
                    return
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

    def play_args(self, selection, **kwargs):

        source, kwargs = super().play_args(selection, **kwargs)
        kwargs["media_type"] = selection.media_type
        return (source, kwargs)

    # def feed_attrs(self, feed_name):

    #     return dict(locator=self.filters.feed[feed_name])
