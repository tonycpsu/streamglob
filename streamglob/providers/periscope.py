from .feed import *

from pyperi import Peri

class PeriscopeItem(model.Item):

    is_live = Required(bool)

class PeriscopeFeed(model.Feed):

    ITEM_CLASS = PeriscopeItem

    def update(self, limit=None):

        if not limit:
            limit = self.DEFAULT_ITEM_LIMIT

        for item in Peri().get_user_broadcast_history(
                username=self.locator
        ):
            guid = item["id"]
            i = self.items.select(lambda i: i.guid == guid).first()
            if not i:
                i = self.ITEM_CLASS(
                    feed = self,
                    guid = guid,
                    subject = item["status"].strip() or "-",
                    content = f"https://pscp.tv/w/{item['id']}",
                    is_live = item.get("state") == "RUNNING"
            )

class PeriscopeLiveStreamFilter(ListingFilter):

    values = AttrDict([
        (s, s.lower())
        for s in ["Any", "Live", "Archived"]
    ])

class PeriscopeProvider(PaginatedProviderMixin, CachedFeedProvider):

    FEED_CLASS = PeriscopeFeed

    FILTERS = AttrDict(
        CachedFeedProvider.FILTERS,
        **dict(live = PeriscopeLiveStreamFilter)
    )

    MEDIA_TYPES = {"video"}


    @property
    def feed_filters(self):
        live_filters =  {
            "any": lambda: True,
            "live": lambda i: i.is_live,
            "archived": lambda i: not i.is_live,
        }

        return [
            live_filters[self.filters.live.value]
        ]
