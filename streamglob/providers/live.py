from .. import model

from .feed import *

class LiveStreamItem(model.Item):

    is_live = Required(bool)


class LiveStreamFeed(model.Feed):

    ITEM_CLASS = LiveStreamItem

    def update(self, limit = None):

        if not limit:
            limit = self.DEFAULT_ITEM_LIMIT

        for item in self.provider.check_feed(self.locator):
            i = self.items.select(lambda i: i.guid == item.guid).first()
            if not i:
                i = self.ITEM_CLASS(
                    feed = self,
                    **item
            )


class LiveStreamFilter(ListingFilter):

    values = AttrDict([
        (s, s.lower())
        for s in ["Any", "Live", "Archived"]
    ])


class LiveStreamProviderView(SimpleProviderView):

    PROVIDER_DATA_TABLE_CLASS = CachedFeedProviderDataTable


@with_view(LiveStreamProviderView)
class LiveStreamProvider(PaginatedProviderMixin, CachedFeedProvider, abc.ABC):

    FEED_CLASS = LiveStreamFeed

    FILTERS = AttrDict(
        CachedFeedProvider.FILTERS,
        **AttrDict([
            ("live", LiveStreamFilter)
        ])
    )

    @abc.abstractmethod
    def check_feed(self, feed):
        pass


    @db_session
    def update_query(self):
        super().update_query()

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
