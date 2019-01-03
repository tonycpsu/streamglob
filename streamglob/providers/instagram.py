from streamglob.providers.base import *

from ..exceptions import *
from ..state import *
from .. import config
from .. import model

from .filters import *

from instagram_web_api import Client, ClientCompatPatch, ClientError
from pony.orm import *

class InstagramItem(model.Item):

    media_type = Required(str)

class InstagramFeed(model.Feed):

    ITEM_CLASS = InstagramItem

    @db_session
    def update(self, limit = None):
        if not limit:
            limit = self.DEFAULT_ITEM_LIMIT

        logger.info("update_feed")
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
        count = 0
        while(count < limit):
            # instagram API will sometimes give duplicates using end_cursor
            # for pagination, so instead of specifying how many posts to get,
            # we just get 25 at a time and break the loop after we've gotten
            # the desired number of posts
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
                subject = post["node"]["caption"]["text"].replace("\n", "")
                if post_id not in select(i.guid for i in self.items.select())[:]:
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
            self.updated = datetime.now()



@with_view(SimpleProviderView)
class InstagramProvider(PaginatedProviderMixin, CachedFeedProvider):

    FEED_CLASS = InstagramFeed

    ATTRIBUTES = AttrDict(
        time = {"width": 19},
        type = {"width": 6},
        title = {"width": ("weight", 1)},
        id = {"hide": True}
    )

    MEDIA_TYPES = {"image", "video"}

    # VIEW_CLASS = InstagramProviderView

    # DATA_TABLE_CLASS = InstagramProviderDataTable

    def __init__(self, *args, **kwargs):
        self.web_api = Client(auto_patch=True, drop_incompat_keys=False)
        self.end_cursor = None
        super().__init__(*args, **kwargs)

    def play_args(self, selection, **kwargs):
        url = selection.url
        if not isinstance(url, list):
            url = [url]
        args = url
        kwargs["media_type"] = selection.type
        return (args, kwargs)
