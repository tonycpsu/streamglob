from streamglob.providers.base import *

from ..exceptions import *
from ..state import *
from .. import config

from .filters import *

from instagram_web_api import Client, ClientCompatPatch, ClientError

class InstagramFeedsFilter(ListingFilter):

    @property
    def values(self):
        return [ (i, i) for i in state.provider_config.feeds ]


class InstagramProviderDataTable(ProviderDataTable):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class InstagramProviderView(SimpleProviderView):

    PROVIDER_DATA_TABLE_CLASS = InstagramProviderDataTable


@with_view(InstagramProviderView)
class InstagramProvider(BaseProvider):

    FILTERS = AttrDict([
        ("feed", InstagramFeedsFilter),
        # ("search", TextFilter)
    ])

    ATTRIBUTES = AttrDict(
        time = {"width": 19},
        type = {"width": 6},
        title = {"width": ("weight", 1)},
        id = {"hide": True}
    )

    # VIEW_CLASS = InstagramProviderView

    DATA_TABLE_CLASS = InstagramProviderDataTable


    def __init__(self, *args, **kwargs):
        self.web_api = Client(auto_patch=True, drop_incompat_keys=False)
        self.end_cursor = None
        super().__init__(*args, **kwargs)


    def listings(self, offset=None, limit=None, *args, **kwargs):

        feed_name = self.filters.feed.value
        if feed_name.startswith("@"):
            feed_name = feed_name[1:]

        try:
            user_id = self.web_api.user_info2(feed_name)["id"]
        except:
            raise SGException(f"user id for {feed_name} not found")

        logger.info(self.end_cursor)
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
                    cursor = post["node"]["edge_media_to_comment"]["page_info"]["end_cursor"]
                    if cursor:
                        self.end_cursor = cursor
                except KeyError:
                    pass

                logger.info(post["node"]["edge_media_to_comment"]["page_info"]["end_cursor"])
                post_id = post["node"]["id"]

                if (not hasattr(self, "view")
                    or (post_id not in self.view.table.df.get(
                        columns="id", as_list=True))
                    ):
                    count += 1
                    yield AttrDict(
                        id = post_id,
                        time = datetime.fromtimestamp(int(post["node"]["created_time"])),
                        title = post["node"]["caption"]["text"].replace("\n", ""),
                        type = post["node"]["type"],
                        url = post["node"]["link"],
                        # end_cursor = post["node"]["edge_media_to_comment"]["page_info"]["end_cursor"]
                    )
                    if count >= self.limit:
                        break
