import logging
logger = logging.getLogger(__name__)

import os
from datetime import datetime, timedelta
from dataclasses import *
import typing
import re
import dataclasses_json
from dataclasses_json import dataclass_json
import dateutil.parser
import abc

from orderedattrdict import AttrDict
from pony.orm import *
from pony.orm.core import EntityMeta


# monkey-patch
from marshmallow import fields as mm_fields
dataclasses_json.mm.TYPES.update({
    typing.Any: mm_fields.Raw
})

from . import config
from . import providers
from .exceptions import *

CACHE_DURATION_SHORT = 60 # 60 seconds
CACHE_DURATION_MEDIUM = 60*60*24 # 1 day
CACHE_DURATION_LONG = 60*60*24*30  # 30 days
CACHE_DURATION_DEFAULT = CACHE_DURATION_SHORT

db = Database()

# Monkey-patch "upsert"-ish functionality into the Pony ORM db.Entity class.
# via: https://github.com/ponyorm/pony/issues/131
@db_session
def upsert(cls, keys, values=None):
    """
    Update

    :param cls: The entity class
    :param get: dict identifying the object to be created/updated
    :param set: dict identifying the values
    :return:
    """
    values = values or {}

    if not cls.exists(**keys):
        # logger.info(f"insert: {keys}")
        # make new object
        return cls(**keys, **values)
    else:
        # logger.info(f"update: {keys}, {values}")
        # get the existing object
        obj = cls.get(**keys)
        obj.set(**values)
        return obj

db.Entity.upsert = classmethod(upsert)


@dataclass
class BaseDataClass:

    def keys(self):
        return self.__dataclass_fields__.keys()

    def get(self, key, default=None):

        return getattr(self, key, default)

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def __delitem__(self, key):
        delattr(self, key)

    def __iter__(self):
        return iter(self.keys())

    def __len__(self):
        return len(self.keys())


@dataclass
class MediaListing(BaseDataClass):

    provider_id: str
    _attrs: AttrDict = field(default_factory=AttrDict)

    # def __init__(self, provider_id, *args, **kwargs):
    #     self.provider_id = provider_id
    #     super().__init__()

    def __getattr__(self, name, default=None):
        if name != "_attrs":
            return self._attrs.get(name, default)

    TEMPLATE_RE=re.compile("\{((?!index)[^}]+)\}")

    @property
    def provider(self):
        return providers.get(self.provider_id)
        # return self.provider.NAME.lower()

    @property
    def default_name(self):
        import time

        if len(self.content) > 1:
            raise NotImplementedError

        for s in reversed(self.content[0].locator.split("/")):
            if not len(s): continue
            return "".join(
                [c for c in s if c.isalpha() or c.isdigit() or c in [" ", "-"]]
            ).rstrip()
        return "untitled"

    @property
    def timestamp(self):
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    @property
    def ext(self):
        return f"{self.provider_id}_dl" # *shrug*


    def download_filename(self, index=None, feed=None, **kwargs):

        if "outfile" in kwargs:
            return kwargs.get("outfile")

        outpath = (
            self.provider.config.get_path("output.path")
            or
            config.settings.profile.get_path("output.path")
            or
            "."
        )

        template = (
            self.provider.config.get_path("output.template")
            or
            config.settings.profile.get_path("output.template")
        )

        if template:
            # template = template.replace("{", "{self."
            template = self.TEMPLATE_RE.sub(r"{self.\1}", template)
            try:
                outfile = template.format(self=self, index=index)
            except Exception as e:
                logger.exception(e)
                raise SGInvalidFilenameTemplate
        else:
            # template = "{self.provider.name.lower()}.{self.default_name}.{self.timestamp}.{self.ext}"
            # template = "{self.provider}.{self.ext}"
            template = "{self.provider}.{self.default_name}.{self.timestamp}.{self.ext}"
            outfile = template.format(self=self)
        # logger.info(f"template: {template}, outfile: {outfile}")
        return os.path.join(outpath, outfile)


@dataclass
class ContentMediaListing(MediaListing):

    content: typing.Any = None
    title: str = None
    created: datetime = None

@dataclass_json
@dataclass
class MediaSource(BaseDataClass):

    # listing: MediaListing
    # locator: str
    # provider: typing.Any = None
    provider_id: str
    url: typing.Optional[str] = None # Pony also uses Optional
    media_type: typing.Optional[str] = None

    @property
    def provider(self):
        return providers.get(self.provider_id)

    @property
    def helper(self):
        return None

    @property
    def download_helper(self):
        return None

    @property
    def locator(self):
        return self.url

    @property
    def is_bad(self):
        """
        Subclasses can override this to check the validity of a source's URL
        and return True if the source should be filtered or marked as such.
        """
        return False

    # def __str__(self):
    #     return self.locator


@dataclass
class MediaTask(BaseDataClass):

    provider: str
    title: str
    sources: typing.List[MediaSource]
    task_id: typing.Optional[int] = None
    args: typing.List[str] = field(default_factory=list)
    kwargs: typing.Dict[str, str] = field(default_factory=AttrDict)
    # _details_open: bool = False

@dataclass
class ProgramMediaTask(MediaTask):

    program: typing.Optional[typing.Any] = None
    proc: typing.Optional[typing.Any] = None
    pid: typing.Optional[int] = None
    started: typing.Optional[datetime] = None
    elapsed: typing.Optional[timedelta] = None

@dataclass
class PlayMediaTask(ProgramMediaTask):
    pass

@dataclass
class DownloadMediaTask(ProgramMediaTask):

    dest: typing.Optional[str] = None

class CacheEntry(db.Entity):

    url = Required(str, unique=True)
    response = Required(bytes)
    last_seen = Required(datetime, default=datetime.now)

    @classmethod
    @db_session
    def purge(cls, age=CACHE_DURATION_LONG):

        cls.select(
            lambda e: e.last_seen < datetime.now() - timedelta(seconds=age)
        ).delete()

class MediaChannel(db.Entity):
    """
    A streaming video channel, identified by some unique string (locator).  This
    may be a URL, username, or any other unique string, depending on the nature
    of the provider.

    If the provider is able to distinguish between specific broadcasts, episodes,
    videos, etc. in the channel with a unique identifer, the MediaFeed entity
    defined below should be used instead.
    """

    DEFAULT_UPDATE_INTERVAL = 3600

    channel_id = PrimaryKey(int, auto=True)
    name = Optional(str, index=True)
    provider_id = Required(str, index=True)
    locator = Required(str)
    updated = Required(datetime, default=datetime.now)
    last_seen = Optional(datetime)
    update_interval = Required(int, default=DEFAULT_UPDATE_INTERVAL)
    attrs = Required(Json, default={})

    @property
    def provider(self):
        return providers.get(self.provider_id)

    @property
    def session(self):
        return self.provider.session


class MediaFeed(MediaChannel):
    """
    A subclass of MediaChannel for providers that can distinguish between
    individual broadcasts / episodes / events, perhaps with the abilit to watch
    on demand.
    """

    # FIXME: move to feed.py?

    DEFAULT_FETCH_LIMIT = 100

    DEFAULT_MIN_ITEMS=10
    DEFAULT_MAX_ITEMS=500
    DEFAULT_MAX_AGE=90

    items = Set(lambda: MediaItem)

    @abc.abstractmethod
    def fetch(self):
        pass

    def update(self, *args, **kwargs):
        for item in self.fetch(*args, **kwargs):
            listing = self.provider.new_listing(
                # feed = f.to_dict(),
                **item.to_dict(
                    exclude=["media_item_id", "feed", "classtype"],
                    related_objects=True
                )
            )
            listing.content = self.provider.MEDIA_SOURCE_CLASS.schema().loads(listing["content"], many=True)

            self.provider.on_new_listing(listing)
            self.updated = datetime.now()

    @db_session
    def mark_all_items_read(self):
        for i in self.items.select():
            i.read = datetime.now()

    @classmethod
    @db_session
    def mark_all_feeds_read(cls):
        for f in cls.select():
            for i in f.items.select():
                i.read = datetime.now()

    @classmethod
    @db_session
    def purge_all(cls,
                  min_items = DEFAULT_MIN_ITEMS,
                  max_items = DEFAULT_MAX_ITEMS,
                  max_age = DEFAULT_MAX_AGE):
        for f in cls.select():
            f.purge(min_items = min_items,
                    max_items = max_items,
                    max_age = max_age)

    @db_session
    def purge(self,
              min_items = DEFAULT_MIN_ITEMS,
              max_items = DEFAULT_MAX_ITEMS,
              max_age = DEFAULT_MAX_AGE):
        """
        Delete items older than "max_age" days, keeping no fewer than
        "min_items" and no more than "max_items"
        """
        for n, i in enumerate(
                self.items.select().order_by(
                    lambda i: desc(i.fetched)
                )[min_items:]
        ):
            if (min_items + n >= max_items
                or
                i.time_since_fetched >= timedelta(days=max_age)):
                i.delete()
        commit()


class MediaItem(db.Entity):
    """
    An individual media clip, broadcast, episode, etc. within a particular
    MediaFeed.
    """

    media_item_id = PrimaryKey(int, auto=True)
    feed = Required(lambda: MediaFeed)
    guid = Required(str, index=True)
    title = Required(str)
    content = Required(Json)
    created = Required(datetime, default=datetime.now)
    fetched = Required(datetime, default=datetime.now)
    read = Optional(datetime)
    watched = Optional(datetime)
    downloaded = Optional(datetime)
    attrs = Required(Json, default={})
    # was_downloaded = Required(bool, default=False)

    @db_session
    def mark_read(self):
        self.read = datetime.now()

    @db_session
    def mark_unread(self):
        self.read = None

    def created_date(self):
        return datetime.now().strftime("%Y%m%d_%H%M%S")

    @property
    def age(self):
        return datetime.now() - self.created

    @property
    def time_since_fetched(self):
        # return datetime.now() - dateutil.parser.parse(self.fetched)
        return datetime.now() - self.fetched

    @property
    def locator(self):
        return self.content

    # def to_dict(self, *args, **kwargs):
    #     d = super().to_dict(*args, **kwargs)
    #     # d.update(url=d["content"])
    #     return d


# class ProviderData(db.Entity):
#     # Providers inherit from this to define their own fields
#     classtype = Discriminator(str)


class ProviderData(db.Entity):
    """
    Providers can use this entity to cache data that doesn't belong in the
    configuration file or deserve a separate entity in the data model
    """
    name = Required(str, unique=True)
    settings = Required(Json, default={})


def init(filename=None, *args, **kwargs):

    if not filename:
        filename = os.path.join(config.settings.CONFIG_DIR, f"{config.PACKAGE_NAME}.sqlite")
    db.bind("sqlite", filename, create_db=True, *args, **kwargs)
    db.generate_mapping(create_tables=True)
    CacheEntry.purge()

def main():

    init()
    config.load(merge_default=True)

    MediaFeed.purge_all(
        min_items = config.settings.profile.cache.min_items,
        max_items = config.settings.profile.cache.max_items,
        max_age = config.settings.profile.cache.max_age
    )


if __name__ == "__main__":
    main()
