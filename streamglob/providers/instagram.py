from streamglob.providers.base import *

from ..exceptions import *
from ..state import *
from .. import config

from .filters import *

from instagram_web_api import Client, ClientCompatPatch, ClientError


# class InstagramProviderDataTable(ProviderDataTable):

#     def __init__(self, *args, **kwargs):
#         super().__init__(*args, **kwargs)


# class InstagramProviderView(SimpleProviderView):

#     PROVIDER_DATA_TABLE_CLASS = InstagramProviderDataTable


@with_view(SimpleProviderView)
class InstagramProvider(FeedProvider):

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


    def listings(self, offset=None, limit=None, *args, **kwargs):

        feed_name = self.selected_feed
        if feed_name.startswith("@"):
            feed_name = feed_name[1:]

        try:
            user_id = self.web_api.user_info2(feed_name)["id"]
        except:
            print("fail")
            import time
            time.sleep(5)
            raise SGException(f"user id for {feed_name} not found")

        if not offset:
            self.end_cursor = None

        # logger.info(self.end_cursor)
        count = 0
        while(count < self.limit):
            # instagram API will sometimes give duplicates using end_cursor
            # for pagination, so instead of specifying how many posts to get,
            # we just get 25 at a time and break the loop after we've gotten
            # the desired number of posts
            feed = self.web_api.user_feed(
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

                if (self._view is None
                    or (post_id not in self.view.table.df.get(
                        columns="id", as_list=True))
                    ):
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
                    yield AttrDict(
                        id = post_id,
                        time = datetime.fromtimestamp(
                            int(post["node"]["created_time"])
                        ),
                        title = post["node"]["caption"]["text"].replace("\n", ""),
                        type = media_type,
                        url = url,
                        # end_cursor = post["node"]["edge_media_to_comment"]["page_info"]["end_cursor"]
                    )
                    if count >= self.limit:
                        break

    def play_args(self, selection, **kwargs):
        url = selection.url
        if not isinstance(url, list):
            url = [url]
        args = url
        kwargs["media_type"] = selection.type
        return (args, kwargs)
