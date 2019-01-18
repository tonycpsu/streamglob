import logging
logger = logging.getLogger(__name__)

import os
from datetime import datetime, timedelta

from pony.orm import *

from . import config
from . import providers

DB_FILE=os.path.join(config.CONFIG_DIR, "streamglob.sqlite")

CACHE_DURATION_SHORT = 60 # 60 seconds
CACHE_DURATION_MEDIUM = 60*60*24 # 1 day
CACHE_DURATION_LONG = 60*60*24*30  # 30 days
CACHE_DURATION_DEFAULT = CACHE_DURATION_SHORT

db = Database()

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


class Feed(db.Entity):

    DEFAULT_UPDATE_INTERVAL = 3600
    DEFAULT_ITEM_LIMIT = 100

    DEFAULT_MIN_ITEMS=10
    DEFAULT_MAX_ITEMS=500
    DEFAULT_MAX_AGE=90

    feed_id = PrimaryKey(int, auto=True)
    name = Optional(str, index=True)
    provider_name = Required(str, index=True)
    locator = Required(str)
    updated = Optional(datetime)
    update_interval = Required(int, default=DEFAULT_UPDATE_INTERVAL)
    items = Set(lambda: Item)

    @property
    def provider(self):
        return providers.get(self.provider_name)

    @db_session
    def mark_all_read(self):
        for i in self.items.select():
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



class Item(db.Entity):

    item_id = PrimaryKey(int, auto=True)
    feed = Required(lambda: Feed)
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

    @property
    def age(self):
        return datetime.now() - self.created

    @property
    def url(self):
        return self.content

    def to_dict(self, *args, **kwargs):
        d = super().to_dict(*args, **kwargs)
        d.update(url=d["content"])
        return d


class ProviderData(db.Entity):
    # Providers inherit from this to define their own fields
    classtype = Discriminator(str)




def init(*args, **kwargs):

    db.bind("sqlite", create_db=True, filename=DB_FILE, *args, **kwargs)
    db.generate_mapping(create_tables=True)
    CacheEntry.purge()

def main():

    init()
    Feed.purge()

if __name__ == "__main__":
    main()
