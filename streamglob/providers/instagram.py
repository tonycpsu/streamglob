import warnings

from .feed import *
from ..exceptions import *
from ..state import *
from .. import config
from .. import model
from .. import session
from .filters import *

from orderedattrdict import DefaultAttrDict
from instagram_web_api import Client, ClientCompatPatch, ClientError, ClientConnectionError, ClientThrottledError
from pony.orm import *
from limiter import get_limiter, limit


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
    DEFAULT_BATCH_COUNT = 10

    RATE = 25
    CACPACITY = 100
    CONSUME = 50

    def __init__(self, *args, **kwargs):
        super().__init__(
            *args, **kwargs
        )

        self.limiter = get_limiter(rate=self.RATE, capacity=self.CACPACITY)

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


    def get_feed_items(self, user_name, cursor=None, count=DEFAULT_BATCH_COUNT):

        logger.info(f"get_feed_items cursor: {cursor}")
        # number of times to retry bad URLs before giving up
        RETRIES = 3

        def get_nested(d, keys):
            v = d
            for k in keys:
                v = v.get(k, None)
                if v is None:
                    return None
            return v

        def get_url_workaround(node, keys):
            # URLs seem to get corrupted somewhat randomly by the API
            # so we try to get it using a different endpoint as a workaround,
            # retrying until we get a valid URL.

            retries = 0
            limiter = get_limiter(rate=1, capacity=2)
            d = node
            while retries <= RETRIES:
                retries += 1
                url = get_nested(d, keys)
                #   The string 0_0_0 seems to always appear in corrupted URLs
                if not "0_0_0" in url:
                    return url
                logger.info("oops")
                with limit(limiter, consume=2):
                    try:
                        d = self.web_api.media_info2(node["shortcode"])
                    except ClientError:
                        continue


        if count > self.MAX_BATCH_COUNT:
            count = self.MAX_BATCH_COUNT
        try:
            with limit(self.limiter, consume = self.CONSUME):
                feed = self.web_api.user_feed(
                    self.user_name_to_id(user_name), count=count,
                    end_cursor = cursor,
                    extract = False
                )
                end_cursor = feed.get('data', {}).get('user', {}).get(
                    'edge_owner_to_timeline_media', {}).get('page_info', {}).get('end_cursor')
                posts = feed.get('data', {}).get('user', {}).get(
                    'edge_owner_to_timeline_media', {}).get('edges', [])
                # posts = self.web_api.user_feed(
                #     self.user_name_to_id(user_name), count=count,
                #     end_cursor = cursor
                # )
        except ClientConnectionError as e:
            logger.warn(f"connection error: {e}")
            raise
        except ClientThrottledError:
            logger.info("throttled")
            raise SGClientThrottled

        for post in posts:
            node = post["node"]
            # try:
            #     cursor = (
            #         post["node"]["edge_media_to_comment"]
            #         ["page_info"]["end_cursor"]
            #     ) or cursor
            #     # if cursor:
            #     #     self.end_cursors[user_name] = cursor
            # except KeyError:
            #     pass

            post_type = None
            post_id = node["id"]

            try:
                title = node["caption"]["text"].replace("\n", "")
            except TypeError:
                title = "(no caption)"

            media_type = node["type"]

            if media_type == "video":
                post_type = "video"

                url = get_url_workaround(node, ["videos", "standard_resolution", "url"])
                if not url:
                    logger.warn(f"couldn't get URL for {node['shortcode']}")
                    continue
                content = self.provider.new_media_source(
                    url, media_type="video"
                )

            else:
                if "carousel_media" in node:
                    post_type = "story"

                    # content = [
                    #     self.provider.new_media_source(
                    #         m["images"]["standard_resolution"]["url"], media_type="image"
                    #     )
                    #     if m["type"] == "image"
                    #     else
                    #     self.provider.new_media_source(
                    #         m["video_url"], media_type="video"
                    #     )
                    #     if m["type"] == "video"
                    #     else None
                    #     for m in node["carousel_media"]
                    # ]

                    retries = 0
                    limiter = get_limiter(rate=1, capacity=2)
                    d = node
                    while retries <= RETRIES:
                        retries += 1
                        urls = [
                            (m["type"],
                             m["images"]["standard_resolution"]["url"]
                             if m["type"] == "image"
                             else m["video_url"]
                            )
                            for m in d["carousel_media"]
                        ]
                        #   The string 0_0_0 seems to always appear in corrupted URLs
                        if not any([ "0_0_0" in u for (t, u) in urls]):
                            break
                        logger.info("oops story")
                        with limit(limiter, consume=2):
                            try:
                                d = self.web_api.media_info2(node["shortcode"])
                            except ClientError:
                                continue

                    else:
                        logger.warn(f"couldn't get URL(s) for {node['shortcode']}")
                        continue

                    content = [
                        self.provider.new_media_source(
                            u, media_type=t
                        )
                        for t, u in urls
                    ]

                    title = f"[{len(content)}] {title} "

                elif media_type == "image":

                        # if any([ "0_0_0" in u for (t, u) in urls]):
                        #     limiter = get_limiter(rate=self.RATE, capacity=self.CACPACITY)
                        #     retries = 0
                        #     while retries < 5:
                        #         with limit(limiter, consume=5):
                        #             media = self.web_api.media_info2(node["shortcode"])

                        #             url = get_nested(media, keys)
                        #             if not "0_0_0" in url:
                        #                 break
                        #             logger.info("oops")
                        #         retries += 1


                    post_type = "image"

                    url = get_url_workaround(node, ["images", "standard_resolution", "url"])
                    if not url:
                        logger.warn(f"couldn't get URL for {node['shortcode']}")
                        continue

                    content = self.provider.new_media_source(
                        url, media_type="image"
                    )
                    urls = [ ("video", url) ]

                else:
                    logger.warn(f"no content for post {post_id}")
                    continue

            created = datetime.fromtimestamp(int(node["created_time"]))
            # logger.info(f"post: {post_id}, {created.date()}, {end_cursor}")

            yield(
                AttrDict(
                    guid = post_id,
                    title = title.strip(),
                    post_type = post_type,
                    created = created,
                    content = content,
                    cursor = end_cursor
                )
            )


class InstagramItem(model.MediaItem):

    post_type = Required(str)

class InstagramFeed(model.MediaFeed):

    ITEM_CLASS = InstagramItem

    def fetch(self, cursor=None):
        if self.locator.startswith("@"):
            user_name = self.locator[1:]
        else:
            logger.error(self.locator)
            raise NotImplementedError

        logger.info(f"fetching {self.locator}")

        limit = self.provider.config.get("fetch_limit", self.DEFAULT_FETCH_LIMIT)
        # logger.info(f"uodate: {self}")
        last_count = 0
        count = 0
        while(count < limit):
            # instagram API will sometimes give duplicates using end_cursor
            # for pagination, so instead of specifying how many posts to get,
            # we just get a batch of them at a time and break the loop after
            # we've gotten the desired number of posts, or after a batch
            # entirely comprised of duplicates
            new_seen = False
            try:
                for post in self.session.get_feed_items(user_name, cursor):
                    # logger.info(post.cursor)
                    with db_session:
                        if post.cursor:
                            cursor = post.cursor
                            self.attrs["cursor"] = cursor
                        i = self.items.select(lambda i: i.guid == post.guid).first()
                        if not i:
                            logger.info(f"new: {post.created}")
                            new_seen = True
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
                                ),
                                attrs = dict(cursor=post.cursor)
                            )
                            count += 1
                            yield i
                    if count >= limit:
                        return
            except SGClientThrottled:
                return

            if not new_seen:
                logger.info("fetch done")
                return
                last_count = count


class InstagramDataTable(CachedFeedProviderDataTable):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        urwid.connect_signal(
            self, "end",
            self.on_end
        )

    @db_session
    def fetch_more(self):
        feed = self.provider.feed
        logger.info(f"end: {count}")
        if feed is None:
            return
        try:
            cursor = feed.items.select().order_by(
                self.provider.ITEM_CLASS.created
            ).first().attrs.get("cursor")
        except AttributeError:
            cursor = None
        feed.update(cursor=cursor)
        self.provider.update_query()
        self.refresh()

    @db_session
    def on_end(self, source, count):
        self.fetch_more()

    def keypress(self, size, key):

        if key == "meta f":
            self.fetch_more()
        else:
            return super().keypress(size, key)


class InstagramProviderView(SimpleProviderView):

    PROVIDER_DATA_TABLE_CLASS = InstagramDataTable


@with_view(InstagramProviderView)
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
