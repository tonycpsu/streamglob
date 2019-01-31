from .feed import *
from .. import session
from .. import model

import pyperi

class PeriscopeSession(session.StreamSession):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.peri = pyperi.Peri(session=self)


class PeriscopeMediaListing(MediaListing):

    @property
    def ext(self):
        return "mp4"


class PeriscopeMediaSource(model.MediaSource):

    @property
    def helper(self):
        return {
            "mpv": None,
            None: "youtube-dl",
        }

class PeriscopeItem(model.MediaItem):

    is_live = Required(bool)

class PeriscopeFeed(model.MediaFeed):

    ITEM_CLASS = PeriscopeItem

    @db_session
    def update(self, limit=None):

        if not limit:
            limit = self.DEFAULT_ITEM_LIMIT

        try:
            for item in self.session.peri.get_user_broadcast_history(
                    username=self.locator
            ):
                guid = item["id"]
                i = self.items.select(lambda i: i.guid == guid).first()
                if not i:
                    i = self.ITEM_CLASS(
                        feed = self,
                        guid = guid,
                        subject = item["status"].strip() or "-",
                        content = PeriscopeMediaSource.schema().dumps(
                            [PeriscopeMediaSource(
                                f"https://pscp.tv/w/{item['id']}",
                                media_type="video")],
                            many=True
                        ),
                        is_live = item.get("state") == "RUNNING"
                )
        except pyperi.PyPeriConnectionError as e:
            logger.warning(e)

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

    SESSION_CLASS = PeriscopeSession

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
