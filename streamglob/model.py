import logging
logger = logging.getLogger(__name__)

import os
from datetime import datetime, timedelta
from dataclasses import *
import typing

from pony.orm import *
from pony.orm.core import EntityMeta

from dataclasses_json import dataclass_json

from . import config
from . import providers

DB_FILE=os.path.join(config.CONFIG_DIR, "streamglob.sqlite")

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


@dataclass_json
@dataclass
class MediaSource(object):

    locator: str
    media_type: typing.Optional[str] = None # Pony also uses Optional

    @property
    def helper(self):
        return None


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
    provider_name = Required(str, index=True)
    locator = Required(str)
    updated = Required(datetime, default=datetime.now)
    last_seen = Optional(datetime)
    update_interval = Required(int, default=DEFAULT_UPDATE_INTERVAL)

    @property
    def provider(self):
        return providers.get(self.provider_name)

    @property
    def session(self):
        return self.provider.session


class MediaFeed(MediaChannel):
    """
    A subclass of MediaChannel for providers that can distinguish between
    individual broadcasts / episodes / events, perhaps with the abilit to watch
    on demand.  Providers using MediaFeed
    """

    DEFAULT_ITEM_LIMIT = 100
    DEFAULT_MIN_ITEMS=10
    DEFAULT_MAX_ITEMS=500
    DEFAULT_MAX_AGE=90

    items = Set(lambda: MediaItem)

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
                    lambda i: desc(i.created)
                )[min_items:]
        ):
            if (min_items + n >= max_items
                or
                i.age >= timedelta(days=max_age)):
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
    subject = Required(str)
    content = Required(Json)
    created = Required(datetime, default=datetime.now)
    read = Optional(datetime)
    watched = Optional(datetime)
    downloaded = Optional(datetime)
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
    def locator(self):
        return self.content

    # def to_dict(self, *args, **kwargs):
    #     d = super().to_dict(*args, **kwargs)
    #     # d.update(url=d["content"])
    #     return d


class ProviderData(db.Entity):
    # Providers inherit from this to define their own fields
    classtype = Discriminator(str)




def init(*args, **kwargs):

    db.bind("sqlite", create_db=True, filename=DB_FILE, *args, **kwargs)
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
