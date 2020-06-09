import warnings
from itertools import islice

from .feed import *
from ..exceptions import *
from ..state import *
from .. import config
from .. import model
from .. import session
from .filters import *

from orderedattrdict import AttrDict, DefaultAttrDict
from instaloader import Instaloader, Profile
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
        self.end_cursors = DefaultAttrDict(lambda: None)
        self.web_api = None
        self.login()

    def login(self):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                logger.info("login")
                self.web_api = Client(
                    proxy=self.proxies.get("https") if self.proxies else None,
                    auto_patch=True, drop_incompat_keys=False
                )
                logger.info(self.web_api)
            except ClientConnectionError as e:
                logger.error(e)
                return

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

        URL_PATTERN_BLACKLIST = [
            re.compile(p)
            for p in [
                "0_0_0",
                "null.jpg"
            ]
        ]

        limiter = get_limiter(rate=1, capacity=5)

        def url_is_blacklisted(u):
            return any(p.search(u) for p in URL_PATTERN_BLACKLIST)

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

            for retries in range(RETRIES):
                url = get_nested(node, keys)
                logger.debug(f"url: {url}")
                #   The string 0_0_0 seems to always appear in corrupted URLs
                if not url_is_blacklisted(url):
                    return url
                logger.info("oops")
                raise SGStreamSessionException
                with limit(limiter, consume=5):
                    try:
                        d = self.web_api.media_info2(node["shortcode"])
                    except ClientError:
                        continue
                    except json.decoder.JSONDecodeError:
                        logger.error(node)
                        continue

        def process_post(post, end_cursor):

            node = post["node"]

            post_type = None
            post_id = node["id"]
            short_code = node["shortcode"]

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
                content = self.provider.new_media_source(
                    url, media_type="video"
                )

            else:
                if "carousel_media" in node:
                    post_type = "carousel"

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

                    d = node
                    for retries in range(RETRIES):
                        urls = [
                            (m["type"],
                             m["images"]["standard_resolution"]["url"]
                             if m["type"] == "image"
                             else m["video_url"]
                            )
                            for m in d["carousel_media"]
                        ]
                        #   The string 0_0_0 seems to always appear in corrupted URLs
                        if not any([ url_is_blacklisted(u) for (t, u) in urls]):
                            break
                        logger.info("oops")
                        raise SGStreamSessionException # FIXME
                        with limit(limiter, consume=5):
                            try:
                                d = self.web_api.media_info2(node["shortcode"])
                            except ClientError: # FIXME
                                raise

                    else:
                        logger.warn(f"couldn't get URL(s) for {node['shortcode']}")
                        return # FIXME

                    content = [
                        self.provider.new_media_source(
                            u, media_type=t
                        )
                        for t, u in urls
                    ]

                    # title = f"[{len(content)}] {title} "

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
                        return # FIXME

                    content = self.provider.new_media_source(
                        url, media_type="image"
                    )
                    urls = [ ("video", url) ]

                else:
                    logger.warn(f"no content for post {post_id}")
                    return #FIXME

            created = datetime.fromtimestamp(int(node["created_time"]))
            # logger.info(f"post: {post_id}, {created.date()}, {end_cursor}")

            yield(
                AttrDict(
                    guid = post_id,
                    title = title.strip(),
                    post_type = post_type,
                    created = created,
                    content = content,
                    cursor = end_cursor,
                    short_code = short_code
                )
            )

        def process_feed(count):
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
                yield from process_post(post, end_cursor)

        for retries in range(RETRIES):
            try:
                # double count since we're walking backwards
                yield from process_feed(count if retries == 0 else count*2)
                break
            except SGStreamSessionException:
                logger.info(f"retry feed {retries+1} of {RETRIES}")
                self.login()
                continue



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

        profile = Profile.from_username(loader.context, user_name)

        for post in islice(profile.get_posts(), limit):
            if post.typename == "GraphImage":
                content = post.url
            elif post.typename == "GraphVideo":
                content = post.video_url
            elif post.typename == "GraphSidecar":
                content = [
                    s.video_url or s.display_url
                    for s in post.get_sidecar_nodes()
                ]

            print(post.shortcode, post.caption, post.date_utc, post.mediaid, content)
            raise NotImplementedError # to be continued...

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
                    attrs = dict(
                        cursor = post.cursor,
                        short_code = post.short_code
                    )
                )
                count += 1
                yield i

    # def fetch(self, cursor=None):
    #     if self.locator.startswith("@"):
    #         user_name = self.locator[1:]
    #     else:
    #         logger.error(self.locator)
    #         raise NotImplementedError

    #     logger.info(f"fetching {self.locator}")

    #     limit = self.provider.config.get("fetch_limit", self.DEFAULT_FETCH_LIMIT)
    #     # logger.info(f"uodate: {self}")
    #     last_count = 0
    #     count = 0
    #     while(count < limit):
    #         # instagram API will sometimes give duplicates using end_cursor
    #         # for pagination, so instead of specifying how many posts to get,
    #         # we just get a batch of them at a time and break the loop after
    #         # we've gotten the desired number of posts, or after a batch
    #         # entirely comprised of duplicates
    #         new_seen = False
    #         try:
    #             for post in self.session.get_feed_items(user_name, cursor):
    #                 # logger.info(post.cursor)
    #                 with db_session:
    #                     if post.cursor:
    #                         cursor = post.cursor
    #                         self.attrs["cursor"] = cursor
    #                     i = self.items.select(lambda i: i.guid == post.guid).first()
    #                     if not i:
    #                         logger.info(f"new: {post.created}")
    #                         new_seen = True
    #                         i = self.ITEM_CLASS(
    #                             feed = self,
    #                             guid = post.guid,
    #                             title = post.title,
    #                             created = post.created,
    #                             post_type = post.post_type,
    #                             content =  InstagramMediaSource.schema().dumps(
    #                                 post.content
    #                                 if isinstance(post.content, list)
    #                                 else [post.content],
    #                                 many=True
    #                             ),
    #                             attrs = dict(
    #                                 cursor = post.cursor,
    #                                 short_code = post.short_code
    #                             )
    #                         )
    #                         count += 1
    #                         yield i
    #                 if count >= limit:
    #                     return
    #         except SGClientThrottled:
    #             return

    #         except SGStreamSessionException:
    #             return

    #         if not new_seen:
    #             logger.info("fetch done")
    #             return
    #             last_count = count


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
            # cursor = feed.items.select().order_by(
            #     self.provider.ITEM_CLASS.created
            # ).first().attrs.get("cursor")
            cursors = (
                i.attrs["cursor"]
                for i in feed.items.select().order_by(
                        self.provider.ITEM_CLASS.created
                ).limit(100)[:]
            )
            cursor = list(AttrDict.fromkeys(cursors))[1]
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
        elif key == "ctrl k":
            from collections import OrderedDict
            feed = self.provider.feed

            with db_session:
                cursors = (
                    i.attrs["cursor"]
                    for i in feed.items.select().order_by(
                            self.provider.ITEM_CLASS.created
                    ).limit(100)[:]
                )
                raise Exception(list(OrderedDict.fromkeys(cursors))[1])
                cursor = select(
                    (i.created, distinct(i.attrs["cursor"]))
                    for i in feed.items
                ).order_by(
                    lambda i: i[0]
                ).limit(1, 1).first()
                logger.info(f"cursor rec: {cursor}")
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

    POST_TYPE_MAP = {
        "image": "img",
        "video": "vid",
        "carousel": "car"
    }
    @property
    def ATTRIBUTES(self):
        attrs = list(super().ATTRIBUTES.items())
        idx = next(i for i, a in enumerate(attrs) if a[0] == "title")
        return AttrDict(
            attrs[:idx]
            + [
                ("post_type", {
                    "label": "type",
                    "width": 4,
                    "format_fn": lambda t: self.POST_TYPE_MAP.get(t, t),
                    "align": "right",
                    "sort_icon": False,
                })
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
