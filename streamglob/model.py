import logging
logger = logging.getLogger(__name__)

import os
from datetime import datetime, timedelta
import typing
import types
import re
import dateutil.parser
import abc
import asyncio
import shutil
import unicodedata
import tempfile

import pony.options
pony.options.CUT_TRACEBACK = False
from pony.orm import *

from orderedattrdict import AttrDict
from pony.orm.core import EntityMeta
from pydantic import BaseModel, Field, validator


# monkey-patch
from marshmallow import fields as mm_fields

from . import config
from . import providers
from .state import *
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


ATTRCLASS_TYPE_MAP = {
    Json: typing.Any,
    IntArray: typing.List[int]
}

def parse_attr(attr):

    validator_fn = None
    py_type = ATTRCLASS_TYPE_MAP.get(attr.py_type, attr.py_type)
    attr_type = typing.Optional[py_type]

    def pony_set_validator(cls, v):
        return list(v)

    if attr.is_discriminator:
        return (None, None, None)

    if attr.is_collection:
        # It's not always possible to use the type of the collection, which may
        # not be defined yet, in which case we settle for db.Entity
        rel_type = db.Entity if not isinstance(attr.py_type, type) else attr.py_type
        attr_type = typing.List[typing.Union[rel_type, BaseModel]]
        validator_fn = pony_set_validator

    elif attr.is_relation:
        attr_type = typing.Union[db.Entity, BaseModel]

    elif attr.is_required and not attr.auto and attr.default is None:
        attr_type = py_type

    return (attr_type, validator_fn, attr.default)


class attrclass(object):
    """
    Class decorator that uses pydantic's ORM mode functionality to create model
    classes that mirror those of Pony ORM for cases when we don't want to
    persist the objects or have to worry about a database session.  Adds an
    `attr_class` inner class that inherits from pydantic's `BaseModel`, which
    supports the following usage:

    >>> pony_entity = PonyEntityClass.get(123)
    >>> attr_object = PonyEntityClass.from_orm(pony_entity)
    """

    def __init__(self, common_base=None):
        self.common_base = common_base

    def __call__(self, cls):


        attr_class_name = f"{cls.__name__}_Attr"

        def attrclass_exec_body(ns):

            def config_exec_body(ns):

                ns["orm_mode"] = True
                ns["arbitrary_types_allowed"] = True
                return ns

            ns["Config"] = types.new_class(
                "Config",
                (object,),
                exec_body = config_exec_body
            )

            ns["__annotations__"] = {}

            ns["ormclass"] = cls
            for attr in cls._attrs_:
                attr_type, validator_fn, default = parse_attr(attr)
                if not attr_type:
                    continue
                # I don't know if there's a less hacky way to add type annotations
                # to dynamically-created classes, but this seems to work
                ns[attr.name] = default
                ns["__annotations__"][attr.name] = attr_type
                if validator_fn:
                    val_func_name = f"validate_{attr.name}"
                    ns[val_func_name] = validator(
                        attr.name, pre=True, check_fields=False, allow_reuse=True
                    )(validator_fn)


            # if there are type annotations for other class attributes that (a)
            # aren't entity attributes, (b) have type annotations, and (c)
            # aren't already members of the attr class, we copy these
            # attributes and annotations into the attr class

            for attr, annotation in getattr(cls, "__annotations__", {}).items():
                if attr in cls._attrs_ or attr in ns:
                    continue
                ns[attr] = getattr(cls, attr, None)
                ns["__annotations__"][attr] = annotation

            def attach(self):
                with db_session:

                    keys = {
                        k.name: getattr(self, k.name, None)
                        for k in (self.ormclass._pk_
                                  if isinstance(cls._pk_, tuple)
                                  else (self.ormclass._pk_,))
                    }

                    attached = self.ormclass.get(**keys)

                    if not attached:
                        attached = self.ormclass(
                        **self.dict(exclude_unset = True, exclude_none = True)
                    )
                    return attached

            ns["attach"] = attach
            ns["detach"] = lambda self: self

            return ns


        # if there's an entity class in this entity class's hierarchy that has
        # an attr class, make our attr class a subclass of it
        bases = []

        for c in cls.mro():
            if hasattr(c, "attr_class"):
                bases.append(c.attr_class)
                break
        else:
            bases.append(BaseModel)

        # if there's a base class we want to wedge into the class hierarchy of
        # both the entity class and the attr class (e.g. mixins with methods or
        # properties common to both) we do that here
        if self.common_base:
            bases.insert(0, self.common_base)
            # bases.append(self.common_base)

        attr_class = types.new_class(
            attr_class_name,
            tuple(bases),
            exec_body = attrclass_exec_body
        )
        cls.attr_class = attr_class
        cls.from_orm = attr_class.from_orm

        def detach(self):
            # FIXME
            return self.attr_class.from_orm(self)
            # for attr in dir(detached):
            #     if attr.startswith("_") or isinsance():
            #         continue
            #     if isinstance(getattr(detached, attr), db.Entity):
            #         setattr(detached, attr, None)
            # return detached
        cls.detach = detach
        cls.attach = lambda self: self
        return cls


class MediaChannelMixin(object):

    @property
    def provider(self):
        return providers.get(self.provider_id)

    @property
    def session(self):
        return self.provider.session



@attrclass(MediaChannelMixin)
class MediaChannel(MediaChannelMixin, db.Entity):
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


class MediaSourceMixin(object):

    TEMPLATE_RE=re.compile("\{((?!(index|num|listing|feed|uri))[^}]+)\}")

    @property
    def provider(self):
        return providers.get(self.provider_id)

    @property
    def is_inflated(self):
        logger.info("MediaSourceMixin.is_inflated")
        return True

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


    def download_filename(self, listing=None, index=None, num=None, **kwargs):

        if isinstance(index, int):
            index += 1

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
            template = self.TEMPLATE_RE.sub(r"{self.\1}", template)
            try:
                outfile = template.format(
                    self=self, listing=listing,
                    uri="uri=" + self.uri.replace("/", "+") +"=",
                    index=index, num=num
                )
            except Exception as e:
                logger.exception(e)
                raise SGInvalidFilenameTemplate
        else:
            template = "{listing.provider}.{self.default_name}.{self.timestamp}.{self.ext}"
            outfile = template.format(self=self)
        # logger.info(f"template: {template}, outfile: {outfile}")
        return os.path.join(outpath, outfile)

    def __str__(self):
        return self.locator


@attrclass(MediaSourceMixin)
class MediaSource(MediaSourceMixin, db.Entity):

    media_source_id = PrimaryKey(int, auto=True)
    provider_id = Required(str)
    listing = Optional(lambda: MultiSourceMediaListing, reverse="sources")
    url = Optional(str, nullable=True, default=None)
    media_type = Optional(str)
    rank = Required(int, default=0)
    task = Optional(lambda: MediaTask, reverse="sources")


    @property
    def locator(self):
        return self.url

class InflatableMediaSourceMixin(object):

    @property
    def preview_locator(self):
        return self.preview_url

    @property
    def is_inflated(self):
        return self.locator is not None

    def inflate(self):
        raise Exception("must override inflate method")


@attrclass(InflatableMediaSourceMixin)
class InflatableMediaSource(InflatableMediaSourceMixin, MediaSource):

    preview_url = Optional(str)


class MediaListingMixin(object):

    @property
    def provider(self):
        return providers.get(self.provider_id)
        # return self.provider.NAME.lower()


@attrclass(MediaListingMixin)
class MediaListing(MediaListingMixin, db.Entity):

    media_listing_id = PrimaryKey(int, auto=True)
    provider_id = Required(str, index=True)
    attrs = Required(Json, default={})
    task = Optional(lambda: MediaTask, reverse="listing")


@attrclass()
class MultiSourceMediaListing(MediaListing):

    sources = Set(MediaSource)


@attrclass()
class TitledMediaListing(MultiSourceMediaListing):

    title = Required(str)


class InflatableMediaListingMixin(object):

    def inflate(self):
        pass


@attrclass(InflatableMediaListingMixin)
class InflatableMediaListing(InflatableMediaListingMixin, MediaListing):

    is_inflated = Required(bool, default=False)


@attrclass()
class MediaTask(db.Entity):

    title =  Required(str)
    sources = Set(lambda: MediaSource, reverse="task")
    listing = Optional(lambda: MediaListing)
    provider = Optional(str)
    task_id =  Optional(int)
    args = Required(Json, default=[])
    kwargs = Required(Json, default={})

    def finalize(self):
        pass


class ProgramMediaTaskMixin(object):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.program = state.event_loop.create_future()
        self.proc = state.event_loop.create_future()
        self.result = state.event_loop.create_future()

    def reset(self):
        self.program = state.event_loop.create_future()
        self.proc = state.event_loop.create_future()

    def finalize(self):
        logger.debug(f"finalize program: {self.result} {self.proc} {self.proc.result().returncode}")
        self.result.set_result(self.proc.result().returncode)
        logger.debug("-finalize program")


@attrclass(ProgramMediaTaskMixin)
class ProgramMediaTask(ProgramMediaTaskMixin, MediaTask):

    pid = Optional(int)
    started = Optional(datetime)
    elapsed = Optional(timedelta)

    program: typing.Optional[typing.Awaitable] = None
    proc: typing.Optional[typing.Awaitable] = None
    result: typing.Optional[typing.Awaitable] = None

class PlayMediaTaskMixin(object):

    async def load_sources(self, sources, **options):
        await self.program
        proc = await self.program.result().load_source(sources, **options)
        self.proc = state.event_loop.create_future()
        self.proc.set_result(proc)


@attrclass(PlayMediaTaskMixin)
class PlayMediaTask(PlayMediaTaskMixin, ProgramMediaTask):
    pass


class DownloadMediaTaskMixin(object):

    tempdir_ :typing.Optional[str] = None

    @property
    def tempdir(self):
        if not self.tempdir_:
            self.tempdir_ = tempfile.mkdtemp(prefix="streamglob")
        return self.tempdir_

    @property
    def stage(self):
        return len(self.stage_results)

    @property
    def stage_infile(self):
        if len(self.stage_results):
            return self.stage_results[-1]
        else:
            return self.sources

    @property
    def stage_outfile(self):
        if len(self.postprocessors):
            return os.path.join(self.tempdir, f"{self.stage}.tmp")
        else:
            return self.dest

    def finalize(self):
        if len(self.stage_results) and self.stage_results[-1] != self.dest:
            logger.debug(f"moving {self.stage_results[-1]} => {self.dest}")
            if config.settings.profile.unicode_normalization:
                self.dest = unicodedata.normalize(config.settings.profile.unicode_normalization, self.dest)
            d = os.path.dirname(self.dest)
            if not os.path.isdir(d):
                os.makedirs(d)
            shutil.move(self.stage_results[-1], self.dest)
        shutil.rmtree(self.tempdir)
        super().finalize()


@attrclass(DownloadMediaTaskMixin)
class DownloadMediaTask(DownloadMediaTaskMixin, ProgramMediaTask):

    dest = Optional(str)
    postprocessors = Required(Json, default=[])
    stage_results = Required(Json, default=[])


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
    try:
        db.generate_mapping(create_tables=True)
    except pony.orm.dbapiprovider.OperationalError:
        logger.warn(f"database file {filename} is using an old schema, creating a new one...")
        new_name = f"{filename}.{datetime.now().isoformat().replace(':','').replace('-', '').split()[0]}"
        shutil.move(filename, new_name)
        db.generate_mapping(create_tables=True)

    CacheEntry.purge()

def main():

    foo = MediaSource.attr_class()
    config.init()
    raise Exception(foo.helper)


if __name__ == "__main__":
    main()
