from .feed import *
from .. import session
from .. import model

import dateparser
import pyperi

class PeriscopeSession(session.StreamSession):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.peri = pyperi.Peri(session=self)


@dataclass
class PeriscopeMediaListing(FeedMediaListing):

    is_live: bool = False

    @property
    def ext(self):
        return "mp4"

@dataclass
class PeriscopeMediaSource(model.MediaSource):

    @property
    def helper(self):
        return AttrDict([
            (None, "youtube-dl"),
            ("mpv", None),
        ])

class PeriscopeItem(model.MediaItem):

    is_live = Required(bool)

class PeriscopeFeed(model.MediaFeed):

    ITEM_CLASS = PeriscopeItem

    def update(self, limit=None):

        if not limit:
            limit = self.DEFAULT_ITEM_LIMIT

        try:
            for item in self.session.peri.get_user_broadcast_history(
                    username=self.locator
            ):
                guid = item["id"]
                with db_session:
                    i = self.ITEM_CLASS.upsert(
                        dict(
                            feed = self.channel_id,
                            guid = guid
                        ),
                        dict(
                            title = item["status"].strip() or "-",
                            content = PeriscopeMediaSource.schema().dumps(
                                [self.provider.new_media_source(
                                    f"https://pscp.tv/w/{item['id']}",
                                    media_type="video")],
                                many=True,
                            ),
                            created = dateparser.parse(item["created_at"]).replace(tzinfo=None),
                            is_live = item.get("state") == "RUNNING"

                        )
                    )
                    yield i

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
    def ATTRIBUTES(self):
        return AttrDict(
            super().ATTRIBUTES.items(),
            **AttrDict(
                is_live = {"label": "state", "width": 10,
                           "format_fn": lambda v: "live" if v else "archived"}
            )
        )


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
