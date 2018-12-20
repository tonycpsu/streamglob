import logging
logger = logging.getLogger(__name__)

import os
from datetime import datetime, timedelta

from pony.orm import *

from . import config

DB_FILE=os.path.join(config.CONFIG_DIR, "streamglob.sqlite")

CACHE_DURATION_SHORT = 60 # 60 seconds
CACHE_DURATION_MEDIUM = 60*60*24 # 1 day
CACHE_DURATION_LONG = 60*60*24*30  # 30 days
CACHE_DURATION_DEFAULT = CACHE_DURATION_SHORT

db = Database()

class CacheEntry(db.Entity):

    url = Required(str, unique=True)
    response = Required(bytes)
    last_seen = Required(datetime, default=datetime.now())

    @classmethod
    @db_session
    def purge(cls, age=CACHE_DURATION_LONG):

        cls.select(
            lambda e: e.last_seen < datetime.now() - timedelta(seconds=age)
        ).delete()



def init(*args, **kwargs):

    db.bind("sqlite", create_db=True, filename=DB_FILE, *args, **kwargs)
    db.generate_mapping(create_tables=True)
    CacheEntry.purge()

def main():

    init()

if __name__ == "__main__":
    main()
