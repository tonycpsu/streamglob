from .feed import *

from ..exceptions import *
from ..state import *
from .. import config
from .. import model

from .filters import *

from instagram_web_api import Client, ClientCompatPatch, ClientError
from pony.orm import *

class InstagramItem(model.MediaItem):

    media_type = Required(str)

class InstagramFeed(model.MediaFeed):

    ITEM_CLASS = InstagramItem

    def update(self, limit = None):

        if not limit:
            limit = self.DEFAULT_ITEM_LIMIT

        self.end_cursor = None

        if self.name.startswith("@"):
            user_name = self.name[1:]
        else:
            raise NotImplementedError

        try:
            user_id = self.provider.web_api.user_info2(user_name)["id"]
        except:
            raise SGException(f"user id for {user_name} not found")


        # logger.info(self.end_cursor)
        last_count = 0
        count = 0

        while(count < limit):
            # instagram API will sometimes give duplicates using end_cursor
            # for pagination, so instead of specifying how many posts to get,
            # we just get 25 at a time and break the loop after we've gotten
            # the desired number of posts, or after a batch entirely comprised
            # of duplicates
            feed = self.provider.web_api.user_feed(
                user_id, count=25,
                end_cursor = self.end_cursor
            )
            for post in feed:
                try:
                    cursor = (
                        post["node"]["edge_media_to_comment"]
                        ["page_info"]["end_cursor"]
                    )
                    if cursor:
                        self.end_cursor = cursor
                except KeyError:
                    pass

                post_id = post["node"]["id"]
                if post_id not in select(i.guid for i in self.items.select())[:]:

                    try:
                        subject = post["node"]["caption"]["text"].replace("\n", "")
                    except TypeError:
                        subject = "(no caption)"

                    count += 1
                    media_type = post["node"]["type"]
                    if media_type == "video":
                        url = post["node"]["link"]
                    elif media_type == "image":
                        if "carousel_media" in post["node"]:
                            url = [ m["images"]["standard_resolution"]["url"]
                                    for m in post["node"]["carousel_media"] ]
                        else:
                            url = post["node"]["images"]["standard_resolution"]["url"]
                    else:
                        logger.warn(f"no content for post {post_id}")
                        continue

                    i = self.items.select(lambda i: i.guid == post_id).first()
                    if not i:
                        i = self.ITEM_CLASS(
                            feed = self,
                            guid = post_id,
                            subject = subject,
                            created = datetime.fromtimestamp(
                                int(post["node"]["created_time"])
                            ),
                            media_type = media_type,
                            content = url
                    )
            if count == last_count:
                logger.info(f"breaking after {count}")
                return
            last_count = count


# class InstagramProviderView(SimpleProviderView):

#     PROVIDER_DATA_TABLE_CLASS = CachedFeedProviderDataTable


# @with_view(InstagramProviderView)
class InstagramProvider(PaginatedProviderMixin, CachedFeedProvider):

    FEED_CLASS = InstagramFeed

    SUBJECT_LABEL = "caption"

    MEDIA_TYPES = {"image", "video"}

    # VIEW_CLASS = InstagramProviderView

    def __init__(self, *args, **kwargs):
        self.web_api = Client(auto_patch=True, drop_incompat_keys=False)
        self.end_cursor = None
        super().__init__(*args, **kwargs)

    def play_args(self, selection, **kwargs):

        source, kwargs = super().play_args(selection, **kwargs)
        kwargs["media_type"] = selection.media_type
        return (source, kwargs)

    # def feed_attrs(self, feed_name):

    #     return dict(locator=self.filters.feed[feed_name])
