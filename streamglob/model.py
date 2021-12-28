import logging
logger = logging.getLogger(__name__)

import os
from datetime import datetime, timedelta
import typing
import types
import re
import dateparser.search
import abc
import asyncio
import shutil
import unicodedata
from unidecode import unidecode
import tempfile
import traceback
import glob
from functools import lru_cache
import hashlib
from itertools import chain

import pony.options
pony.options.CUT_TRACEBACK = False
from pony.orm import *
from urlscan import urlscan, urlchoose
import pytz
from orderedattrdict import AttrDict
from pony.orm.core import EntityMeta
import pydantic
from pydantic import BaseModel, Field, validator

# monkey-patch
from marshmallow import fields as mm_fields

from . import config
from . import providers
from . import utils
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

            ns["orm_class"] = cls
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
                with db_session(optimistic=False):

                    keys = {
                        k.name: getattr(self, k.name, None)
                        for k in (self.orm_class._pk_
                                  if isinstance(cls._pk_, tuple)
                                  else (self.orm_class._pk_,))
                    }

                    attached = self.orm_class.get(**keys)

                    if not attached:
                        attached = self.orm_class(
                        **self.dict(exclude_unset = True, exclude_none = True)
                    )
                    return attached

            ns["attach"] = attach
            ns["detach"] = lambda self: self

            return ns


        bases = []
        # if there's an entity class in this entity class's hierarchy that has
        # an attr class, make our attr class a subclass of it
        for c in cls.mro():
            if hasattr(c, "attr_class"):
                bases.append(c.attr_class)
                break

        for c in cls.mro():
            if (c not in bases
                and c.__base__ == object
                and c not in [
                    pony.orm.core.Entity,
                    pydantic.utils.Representation
                ]):
                bases.append(c)
        else:
            bases.append(BaseModel)

        # if there's a base class we want to wedge into the class hierarchy of
        # both the entity class and the attr class (e.g. mixins with methods or
        # properties common to both) we do that here
        if self.common_base and self.common_base not in bases:
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
        cls.orm_class = cls
        return cls

class MediaChannelMixin(object):

    @property
    def provider(self):
        return providers.get(self.provider_id)

    @property
    def session(self):
        return self.provider.session

    @property
    def config(self):
        return self.provider.channels[self.locator]

    def __str__(self):
        return self.name

    @property
    def fetched_age(self):
        try:
            return self.fetched.replace(
                    tzinfo=pytz.timezone(config.settings.profile.time_zone)
                ) - datetime.now().replace(
                    tzinfo=pytz.timezone(config.settings.profile.time_zone)
                )
        except TypeError:
            return None


@attrclass(MediaChannelMixin)
class MediaChannel(MediaChannelMixin, db.Entity):
    """
    A streaming video channel, identified by some unique string (locator).  This
    may be a URL, username, or any other unique string, depending on the nature
    of the provider.

    If the provider is able to distinguish between specific broadcasts, episodes,
    videos, etc. in the channel with a unique identifer, the FeedMediaChannel entity
    defined below should be used instead.
    """

    DEFAULT_UPDATE_INTERVAL = 3600

    channel_id = PrimaryKey(int, auto=True)
    name = Optional(str, index=True)
    provider_id = Required(str, index=True)
    locator = Required(str)
    updated = Required(datetime, default=datetime.now)
    fetched = Required(datetime, default=datetime.now)
    last_seen = Optional(datetime)
    update_interval = Required(int, default=DEFAULT_UPDATE_INTERVAL)
    listings = Set(lambda: ChannelMediaListing, reverse="channel")
    attrs = Required(Json, default={})

class SafeDict(dict):
    def __missing__(self, key):
        return '{' + key + '}'

SUBJECT_MAP = dict()

class MediaSourceMixin(object):

    TEMPLATE_RE = re.compile("\{((?!(index|num|listing|feed|uri))[^}]+)\}")

    KEY_ATTR = "url"

    @property
    def key(self):
        return hashlib.md5(getattr(self, self.KEY_ATTR).encode("utf-8")).hexdigest()

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
    def locator_preview(self):
        return self.url_preview or self.listing.cover

    @property
    def locator_blank(self):
        return utils.BLANK_IMAGE_URI

    @property
    def locator_default(self):
        return self.locator or self.locator_blank

    def locator_for_preview(self, preview_mode):
        attr_name = f"locator_{preview_mode}"
        attr = getattr(self.__class__, attr_name, "locator")
        if callable(attr):
            logger.info(f"locator_for_preview: {attr}")
            return attr(self)
        elif isinstance(attr, property):
            return attr.fget(self)
        else:
            raise NotImplementedError

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


    def download_filename(
            self, listing=None, group=None,
            index=0, num=0,
            match_glob=False, **kwargs
    ):

        if not self.provider:
            return None

        if listing is None:
            listing=self.listing

        if group is None:
           group = f"{listing.group}" if listing.group else ""

        subjects = listing.subjects

        if isinstance(index, int):
            index += 1

        if "outfile" in kwargs:
            return kwargs.get("outfile")

        template = (
            self.provider.config.get_path("output.template")
            or
            config.settings.profile.get_path("output.template")
        )

        group_by = (
            self.provider.config.get_path("output.group_by")
            or
            config.settings.profile.get_path("output.group_by")
        )

        def expand_template(s, safe=False, **tokens):

            if safe:
                s = re.sub(r"{listing.title\b", "{listing.safe_title", s)
            try:
                title = listing.safe_title
                # group_title = f"""{"[%s] " %(group) if group else ""}{title}"""
                # if match_glob:
                #     group_title = glob.escape(group_title)
                if self.provider.config.output.title_prefix:
                    if self.provider.config.output.title_prefix == "group":
                        title = f"""{"[%s] " %(group) if group else ""}{title}"""
                    if self.provider.config.output.title_prefix == "subjects":
                        title = f"""{"[%s] " %(", ".join(subjects)) if subjects else ""}{title}"""
                    else:
                        raise NotImplementedError

                if match_glob:
                    title = glob.escape(title)
                outfile = s.format_map(
                    SafeDict(
                        self=self, listing=listing, # FIXME
                        uri="uri=" + self.uri.replace("/", "+") +"=" if not match_glob else "*",
                        index=self.rank+1,
                        num=num or len(listing.sources) if listing else 0,
                        subject=",".join(subjects) if subjects else None,
                        title=title,
                        group=group,
                        subjects=subjects,
                        **tokens
                        # subject_dir=subject_dir
                    )
                )

                if not match_glob:
                    outfile = outfile.format_map(SafeDict(ext=self.ext))
                    outfile = self.provider.translate_template(outfile)
                if config.settings.profile.unicode_normalization:
                    outfile = unicodedata.normalize(config.settings.profile.unicode_normalization, outfile)
            except Exception as e:
                logger.exception("".join(traceback.format_exc()))
                raise SGInvalidFilenameTemplate(str(e))

            return outfile

        if template:

            (template_dir, template_file) = os.path.split(template)

            path_list = []

            if template_dir:
                path_list.append(expand_template(template_dir))
            else:
                template_dir = "."

            if group_by == "subject":
                try:
                    if group and not group in SUBJECT_MAP:
                        SUBJECT_MAP[group] = next(
                        e.name for e in os.scandir(
                            os.path.join(
                                self.provider.output_path,
                                template_dir
                                )
                            )
                        if e.is_dir()
                        and (
                            e.name == group
                            or unidecode(e.name) == unidecode(group)
                        )
                    )
                    if SUBJECT_MAP.get(group):
                        path_list.append(SUBJECT_MAP[group])

                except StopIteration:
                    SUBJECT_MAP[group] = None

            path_list.append(expand_template(template_file, safe=True))

            # import ipdb; ipdb.set_trace()
            outfile = os.path.join(*path_list)

        else:
            template = "{listing.provider}.{self.default_name}.{self.timestamp}.{self.ext}"
            outfile = template.format(self=self)
        if match_glob:
            outfile = re.sub("({[^}]+})", "*", outfile)

        # import ipdb; ipdb.set_trace()

        return os.path.join(self.provider.output_path, outfile)

    def __str__(self):
        return self.locator

    def __hash__(self):
        if isinstance(self, db.Entity):
            return super().__hash__()
        return hash(self.locator)

    @property
    # @lru_cache(256)
    def local_path(self):
        try:
            return self.get_local_path()[0]
        except (TypeError, IndexError):
            return None

    def get_local_path(self, match_types=None):

        if match_types == None:
            try:
                match_types = self.provider.config.output.match_types or None
            except AttributeError:
                match_types = ["exact"]

        listing = self.listing
        with db_session:
            # FIXME: so hacky
            if hasattr(listing, "media_listing_id"):
                listing = (
                    self.provider.LISTING_CLASS.orm_class[self.listing.media_listing_id]
                    if self.provider and self.listing
                    else None
                )
            try:
                # FIXME
                filename = self.download_filename(
                    listing=listing, num=len(listing.sources) if listing else 1,
                    match_glob=True
                )
            except SGInvalidFilenameTemplate as e:
                logger.error(e)

            if not filename:
                return None

            dirname = os.path.dirname(filename)

            for match_type in match_types:
                if match_type == "exact":
                    path = filename
                elif match_type == "uri" and getattr(self, "uri", None):
                    path = os.path.join(dirname, f"*{self.uri.replace('/', '+') +'='}*")
                elif match_type == "guid" and getattr(listing, "guid", None):
                    path = os.path.join(dirname, f"*{listing.guid}*")
                elif match_type == "date_group":
                    path = os.path.join(dirname, f"*{listing.content_date}*{listing.group}*")
                else:
                    raise NotImplementedError

                ret = next(glob.iglob(path), None)
                if ret:
                    return (ret, match_type)

            return None

    @property
    def subjects(self):
        return self.listing.subjects

    @property
    def group(self):
        return self.listing.group


@attrclass()
class MediaSource(MediaSourceMixin, db.Entity):

    media_source_id = PrimaryKey(int, auto=True)
    provider_id = Required(str)
    listing = Optional(lambda: MultiSourceMediaListing, reverse="sources")
    url = Optional(str, nullable=True, default=None)
    url_preview = Optional(str, nullable=True, default=None)
    media_type = Optional(str)
    rank = Required(int, default=0)
    task = Optional(lambda: MediaTask, reverse="sources")
    downloaded = Optional(datetime)
    viewed = Optional(datetime)


class InflatableMediaSourceMixin(object):

    @property
    def locator_default(self):
        return self.locator_thumbnail or self.locator_preview

    @property
    def locator_thumbnail(self):
        return self.url_thumbnail

    @property
    def locator_preview(self):
        return self.url_thumbnail or super().locator_preview

    @property
    def is_inflated(self):
        return self.locator is not None

    def inflate(self):
        raise Exception("must override inflate method")


class InflatableMediaSource(InflatableMediaSourceMixin, MediaSource):

    url_thumbnail = Optional(str)


class MediaListingMixin(object):

    @property
    def key(self):
        return self.media_listing_id

    @property
    def locators(self):
        return [self.locator] + [
            s.locator
            for s in self.sources
        ]

    @property
    def provider(self):
        return providers.get(self.provider_id)
        # return self.provider.NAME.lower()

    @property
    def cover(self):
        return self.cover_locator or utils.BLANK_IMAGE_URI

    @property
    def locator_download(self):
        return self.locator

    @property
    def tokens(self):

        tokens = []
        default_subjects = self.provider.conf_rules.get(
            "defaults", {}
        ).get("subjects", {})
        channel_cfg = self.channel.config.get_value()
        if isinstance(channel_cfg, dict):
            subject_cfg = channel_cfg.get("subjects", {})
        else:
            subject_cfg = {}
        cfg = AttrDict(
            default_subjects,
            **subject_cfg
        )

        if cfg:
            if "fixed" in cfg:
                tokens += cfg["fixed"]

            if "match" in cfg:
                match_cfg = cfg["match"]
                for field in match_cfg["fields"]:
                    try:
                        tokens += list(chain.from_iterable([
                            [s.strip() for s in match.split(",")]
                            for pattern in match_cfg.get("patterns") or []
                            for match in re.findall(
                                    pattern,
                                    getattr(self, field)
                            )
                        ]))
                    except (AttributeError, IndexError):
                        raise

            if "find" in cfg:
                find = cfg["find"]
                # import ipdb; ipdb.set_trace()
                tokens += list(chain.from_iterable(
                    self.provider.rule_config.get_tokens(
                        content,
                        aliases=self.token_aliases
                    )
                    for content in [
                            getattr(self, field)
                            for field in find["fields"]
                    ]
                    if content
                ))

        return tokens

    @property
    def token_aliases(self):
        cfg = self.channel.config.get_value()
        if not isinstance(cfg, dict):
            return {}
        subject_cfg = cfg.get("subjects", {}).get("find", None)
        if not subject_cfg:
            return {}
        return {
            name: aliases
            for name, aliases in subject_cfg["values"].items()
            if aliases
        }

    @property
    def group(self):

        # import ipdb; ipdb.set_trace()
        group_config = self.channel.config.get_value()
        if not isinstance(group_config, dict):
            group_config = {}
        return next(
            (
                r.group
                for r in self.subject_rules
                if r.group
            ),
            None
        ) or (
            group_config.get("group", None)
        ) or (
            next(
                (
                    (
                        group for group in (
                            n.get_value().get("group")
                            for n in self.channel.config.get_parents()
                        )
                        if group
                    )
                ),
                None
            )
        ) or (
            self.provider.conf_rules.get(
                "defaults", {}
            ).get("group", "").format_map(
                SafeDict(
                    feed=self.feed
                )
            )
        )

    @property
    def subject_rules(self):
        try:
            return [
                rule for label, rule in [
                    self.provider.rule_config.rule_for_token(token)
                    for token in self.tokens
                ]
                if rule
            ]

        except TypeError:
            raise
            import ipdb; ipdb.set_trace()

    @property
    def subjects(self):
        # import ipdb; ipdb.set_trace()
        try:
            return list(
                dict.fromkeys(
                    r.subject for r in self.subject_rules
                )
            )
        except (AttributeError, IndexError):
            return None

    @property
    def highlight_map(self):
        return AttrDict([
            (re.compile("Show"),
             dict(
                 attr="red",
                 patterns=["Show"]
             ))
        ])


@attrclass()
class MediaListing(MediaListingMixin, db.Entity):

    media_listing_id = PrimaryKey(int, auto=True)
    provider_id = Required(str, index=True)
    attrs = Required(Json, default={})
    task = Optional(lambda: MediaTask, reverse="listing")
    download = Optional(lambda: MediaDownload, reverse="media_listing")
    downloaded = Optional(datetime)
    viewed = Optional(datetime)
    locator = Optional(str)
    cover_locator = Optional(str)


class ContentMediaListingMixin(object):

    @property
    def key(self):
        return hashlib.md5(self.content.encode("utf-8")).hexdigest()

    @property
    def body(self):
        return self.content or ""

    @classmethod
    def extract_urls(cls, content):

        if not content:
            return []
        extracted_urls = (
            urlscan.extracthtmlurls(content)
            or urlscan.extracturls(content)
        )

        urls = []
        dedupe = True
        for group, usedfirst, usedlast in extracted_urls:
            if dedupe is True:
                # If no unique URLs exist, then skip the group completely
                if not [chunk for chunks in group for chunk in chunks
                        if chunk.url is not None and chunk.url not in urls]:
                    continue
            groupurls = []
            markup = []
            for chunks in group:
                i = 0
                while i < len(chunks):
                    chunk = chunks[i]
                    i += 1
                    if chunk.url is not None:
                        if (dedupe is True and chunk.url not in urls) \
                                or dedupe is False:
                            urls.append(chunk.url)
                            groupurls.append(chunk.url)
        return urls


    @property
    def body_urls(self):

        if not self.content:
            return []

        return self.extract_urls(self.content)

@attrclass()
class ContentMediaListing(ContentMediaListingMixin, MediaListing):

    content = Optional(str)


@attrclass()
class ChannelMediaListing(MediaListing):

    channel = Required(lambda: MediaChannel)


@attrclass()
class MultiSourceMediaListing(MediaListing):

    sources = Set(MediaSource)


DATE_CONFIG_MAP = {
    "order": "DATE_ORDER"
}

RE_DOTTED_DATE=re.compile(
    r"(\d+)\.(\d+)\.(\d+)"
)

class TitledMediaListingMixin(object):

    @property
    def safe_title(self):
        return utils.sanitize_filename(self.title)

    @property
    def content_date(self):
        return self.title_date

    @property
    def title_date(self):

        default_config = {
            "DATE_ORDER": "YMD"
        }

        configs = [
            {} # default
        ]

        td = self.attrs.get("title_date", None)
        if td == "":
            return None
        elif td is not None:
            td = datetime.strptime(td, "%Y-%m-%d").date()
        else:
            datetime_config = {
                k: v for x in [
                    config for config in (
                        (
                            n.get_value().get("datetime")
                            if isinstance(n.get_value(), dict)
                            else None
                        )
                        for n in reversed(
                                [self.channel.config] + list(self.channel.config.get_parents())
                        )
                    )
                    if config
                ]
                for k, v in x.items()
            }

            if datetime_config:
                datetime_config = {
                    DATE_CONFIG_MAP.get(k, k): v
                    for k, v in datetime_config.items()
                }
                configs.insert(0, datetime_config)

            s = self.title.replace("_", "-").replace("/", " ")
            # workaround for datepaser.search not handling periods
            # in dates
            s = RE_DOTTED_DATE.sub(r"\1-\2-\3", s)

            for config in configs:
                try:
                    d = next(
                        d for d in
                        dateparser.search.search_dates(
                            s,
                            settings=dict(default_config, **config),
                        )
                        if any(c.isdigit() for c in d[0])
                    )
                    td = d[1].date()
                except (TypeError, StopIteration):
                    continue

        with db_session:
            listing = self.attach()
            if td:
                listing.attrs["title_date"] = td.strftime("%Y-%m-%d")
            else:
                # empty string prevents trying again
                listing.attrs["title_date"] = ""

        return td

@attrclass()
class TitledMediaListing(TitledMediaListingMixin, MultiSourceMediaListing):

    title = Required(str)

    @property
    def labels(self):
        return self.provider.rule_cfg.get_labels(self.title)


class InflatableMediaListingMixin(object):

    def inflate(self):
        pass


@attrclass(InflatableMediaListingMixin)
class InflatableMediaListing(InflatableMediaListingMixin, MediaListing):

    is_inflated = Required(bool, default=False)


@attrclass()
class MediaDownload(MediaListingMixin, db.Entity):

    media_download_id = PrimaryKey(int, auto=True)
    media_listing = Required(lambda: MediaListing, reverse="download")
    retries = Required(int, default=0)
    done = Required(bool, default=False)
    source_index = Required(int, default=0)

    @classmethod
    @db_session
    def purge(cls, age=CACHE_DURATION_LONG):
        cls.select(
            lambda e: e.media_listing.downloaded < datetime.now() - timedelta(seconds=age)
        ).delete()


@attrclass()
class MediaTask(db.Entity):

    title =  Required(str)
    sources = Set(lambda: MediaSource, reverse="task")
    listing = Optional(lambda: MediaListing)
    provider = Optional(str)
    task_id =  Optional(int)
    args = Required(Json, default=[])
    kwargs = Required(Json, default={})


class ProgramMediaTaskMixin(object):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.program = asyncio.get_event_loop().create_future()
        self.proc = asyncio.get_event_loop().create_future()
        self.result = asyncio.get_event_loop().create_future()

    def reset(self):
        # self.program = asyncio.get_event_loop().create_future()
        self.proc = asyncio.get_event_loop().create_future()

    def finalize(self):
        logger.debug(f"finalize program: {self.result} {self.proc} {self.proc.result().returncode}")
        self.result.set_result(self.proc.result().returncode)
        logger.debug("-finalize program")

    def stop(self):
        if not self.proc.done():
            return
        try:
            self.proc.result().terminate()
        except ProcessLookupError:
            pass
        # self.reset()


@attrclass()
class ProgramMediaTask(ProgramMediaTaskMixin, MediaTask):

    pid = Optional(int)
    started = Optional(datetime)
    elapsed = Optional(timedelta)
    last_progress = Optional(datetime)

    program: typing.Optional[typing.Awaitable] = None
    proc: typing.Optional[typing.Awaitable] = None
    result: typing.Optional[typing.Awaitable] = None

class PlayMediaTaskMixin(object):

    async def load_sources(self, sources, **options):
        await self.program
        proc = await self.program.result().load_source(sources, **options)
        self.proc = asyncio.get_event_loop().create_future()
        self.proc.set_result(proc)

    def finalize(self):
        logger.info("finalize")
        with db_session:
            now = datetime.now()
            for s in self.sources:
                if isinstance(s, db.Entity):
                    s.attach().viewed = now
            if self.listing:
                if isinstance(s, db.Entity):
                    self.listing.attach().viewed = now
        super().finalize()

@attrclass()
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
        logger.info("finalize")
        if len(self.stage_results) and self.stage_results[-1] != self.dest:
            logger.debug(f"moving {self.stage_results[-1]} => {self.dest}")
            if config.settings.profile.unicode_normalization:
                self.dest = unicodedata.normalize(config.settings.profile.unicode_normalization, self.dest)
            d = os.path.dirname(self.dest)
            if not os.path.isdir(d):
                os.makedirs(d)
            shutil.move(self.stage_results[-1], self.dest)
        try:
            shutil.rmtree(self.tempdir)
        except FileNotFoundError:
            pass
        with db_session:
            now = datetime.now()
            for s in self.sources:
                s = MediaSource[s.media_source_id]
                s.attach().downloaded = now
            if self.listing:
                listing = MediaListing[self.listing.media_listing_id]
                listing.downloaded = now
                # self.listing.attach().downloaded = now
                if listing.download:
                    listing.download.delete()
        super().finalize()


    def stop(self):
        super().stop()
        if self.dest and os.path.isfile(self.dest):
            os.remove(self.dest)


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

class ApplicationData(db.Entity):
    """
    Providers can use this entity to cache data that doesn't belong in the
    configuration file or deserve a separate entity in the data model
    """
    settings = Required(Json, default={})


class ProviderData(db.Entity):
    """
    Providers can use this entity to cache data that doesn't belong in the
    configuration file or deserve a separate entity in the data model
    """
    name = Required(str, unique=True)
    settings = Required(Json, default={})


@db.on_connect(provider="sqlite")
def sqlite_regexp_search(db, conn):

    def regexp(expr, item):
        reg = re.compile(expr)
        return reg.search(item) is not None

    conn.create_function("REGEXP", 2, regexp)

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
