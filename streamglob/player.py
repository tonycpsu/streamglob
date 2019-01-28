import logging
logger = logging.getLogger(__name__)

import os
import abc
from itertools import chain
from functools import reduce
import shlex
import subprocess
from datetime import timedelta
import distutils.spawn
import argparse
import re
from dataclasses import *

from orderedattrdict import AttrDict, Tree
import youtube_dl
import streamlink

from . import config
from . import model
from .state import *
from .utils import *
from .exceptions import *

PROGRAMS = Tree()

@dataclass
class ProgramDef(object):

    cls: type
    name: str
    path: str
    cfg: dict

    @property
    def media_types(self):
        return self.cls.MEDIA_TYPES - set(getattr(self.cfg, "exclude_types", []))


class Program(abc.ABC):

    SUBCLASSES = Tree()

    PLAYER_INTEGRATED=False

    MEDIA_TYPES = set()

    FOREGROUND = False

    PROGRAM_CMD_RE = re.compile(
        '.+?(?:(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|$)'
    )

    def __init__(self, path, args=[], exclude_types=None, **kwargs):
        self.path = path
        if isinstance(args, str):
            self.args = args.split()
        else:
            self.args = args
        self.exclude_types = set(exclude_types) if exclude_types else set()

        self.extra_args_pre = []
        self.extra_args_post = []

        self.source = None
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self.proc = None


    @classproperty
    def cmd(cls):
        # If player class doesn't have a CMD attribute, we generate the command
        # name from the class name, e.g. MPVPlayer -> "mpv"
        return getattr(cls, "CMD", None) or "".join([
            x.group(0) for x in
            cls.PROGRAM_CMD_RE.finditer(
                cls.__name__
            )
        ][:-1]).lower()

    @classmethod
    def __init_subclass__(cls, **kwargs):
        if cls.__base__ != Program:
            cls.SUBCLASSES[cls.__base__][cls.cmd] = cls
            for k, v in kwargs.items():
                setattr(cls, k, v)
        super().__init_subclass__()


    @classmethod
    def get(cls, spec=None, *args, **kwargs):

        global PROGRAMS

        if isinstance(spec, str):
            # get the player by name
            try:
                p = PROGRAMS[cls][spec]
                return iter([p.cls(p.path, **p.cfg)])
            except KeyError:
                raise SGException(f"Program {spec} not found")

        elif isinstance(spec, dict):

            def check_cfg_key(cfg, v):
                if not v:
                    return True
                if isinstance(cfg, list):
                    cfg = set(cfg)
                if isinstance(cfg, set):
                    if isinstance(v, set):
                        return v.issubset(cfg)
                    else:
                        return v in cfg
                else:
                    return cfg == v
            return (
                p.cls(p.path, **p.cfg)
                for p in PROGRAMS[cls].values()
                if not spec or all([
                    check_cfg_key(getattr(p, k, None), v)
                    for k, v in spec.items()
                ])
            )

        elif spec is None:
            return (
                p.cls(p.path, **p.cfg)
                for n, p in PROGRAMS[cls].items()
            )
        else:
            raise Exception
        raise SGException(f"Program for {spec} not found")

    @classmethod
    def play(cls, source, player_spec=True, helper_spec=None, **kwargs):

        logger.debug(f"source: {source}, player: {player_spec}, helper: {helper_spec}")
        helper = None
        player = next(cls.get(player_spec))
        if helper_spec:
            if isinstance(helper_spec, str):
                helper = next(Helper.get(helper_spec))
            elif isinstance(helper_spec, dict):
                if player.cmd in helper_spec:
                    helper_name = helper_spec[player.cmd]
                else:
                    helper_name = helper_spec.get(None, None)
                if helper_name:
                    helper = next(Helper.get(helper_name))

        if helper:
            helper.source = source
            source = helper
            player.source = source

        player.source = source
        logger.info(f"playing {source}: player={player}, helper={helper}")
        return player.run(**kwargs)

    @classmethod
    def from_config(cls, cfg):
        klass = cls.SUBCLASSES.get(cfg.name, cls)
        # return klass(cfg.name, cfg.command, cfg.get("args", []))
        # return klass(*kargs, **kwargs)
        return klass(**cfg)

    @classmethod
    def load(cls):

        global PROGRAMS

        # Add configured players

        for ptype in [Player, Helper, Downloader]:
            cfgkey = ptype.__name__.lower() + "s"
            for name, cfg in config.settings.profile[cfgkey].items():
                path = cfg.pop("path", None) or cfg.get(
                    "command",
                    distutils.spawn.find_executable(name)
                )
                try:
                    # raise Exception(cls.SUBCLASSES[ptype])
                    klass = next(
                        c for c in cls.SUBCLASSES[ptype].values()
                        if c.cmd == name
                    )
                except StopIteration:
                    klass = ptype
                if cfg.disabled == True:
                    logger.info(f"player {name} is disabled")
                    continue

                PROGRAMS[ptype][name] = ProgramDef(
                    cls=klass,
                    name=name,
                    path=path,
                    cfg = AttrDict(cfg)
                )

        # Try to find any players not configured

        for ptype in cls.SUBCLASSES.keys():
            cfgkey = ptype.__name__.lower() + "s"
            for name, klass in cls.SUBCLASSES[ptype].items():
                cfg = config.settings.profile[cfgkey][name]
                if name in PROGRAMS[ptype] or cfg.disabled == True:
                    continue
                path = distutils.spawn.find_executable(name)
                if path:
                    PROGRAMS[ptype][name] = ProgramDef(
                        cls=klass,
                        name=name,
                        path=path,
                        cfg = AttrDict()
                    )

    @property
    def source(self):
        return self._source

    @source.setter
    def source(self, value):
        self._source = value
        if isinstance(self.source, Program):
            if self.source.PLAYER_INTEGRATED:
                self.source.integrate_player(self)
            else:
                self.pipe_from_source()
                self.source.pipe_to_dst()

    def pipe_from_source(self):
        self.extra_args_pre += ["-"]

    def pipe_to_dst(self):
        self.extra_args_post += ["-"]

    def integrate_player(self, dst):
        raise NotImplementedError

    @property
    def command(self):
        return [self.path] + self.args

    @property
    def source_is_player(self):
        return isinstance(self.source, Program)

    @property
    def source_integrated(self):
        return self.source_is_player and self.source.PLAYER_INTEGRATED

    def process_kwargs(self, kwargs):
        pass

    def run(self, source=None, **kwargs):

        if source:
            self.source = source
        # logger.info(f"{self.__class__.__name__} playing {self.source}")

        self.process_kwargs(kwargs)

        cmd = self.command + self.extra_args_pre
        if self.source_is_player:
            self.source.stdout = subprocess.PIPE
            self.proc = self.source.run(**kwargs)
            self.stdin = self.proc.stdout
        elif isinstance(self.source, list):
            # cmd += self.source
            cmd += [s.locator for s in self.source]
        else:
            # cmd += [self.source]
            cmd += [self.source.locator]
        cmd += self.extra_args_post
        # raise Exception(" ".join(cmd))
        logger.debug(f"cmd: {cmd}")

        if not self.source_integrated:
            try:
                self.proc = subprocess.Popen(
                    cmd,
                    stdin = self.stdin,
                    stdout = self.stdout or open(os.devnull, 'w'),
                    stderr = self.stderr or open(os.devnull, 'w'),
                    # stderr = self.stderr or open(os.devnull, 'w'),
                )
            except SGException as e:
                logger.warning(e)
        return self.proc

    @classmethod
    def supports_url(cls, url):
        return False

    def __repr__(self):
        return "<%s: %s %s>" %(self.__class__.__name__, self.cmd, self.args)


class Player(Program):
    pass

class Helper(Program):
    pass

class Downloader(Program):

    def download(self, outfile, **kwargs):
        self.extra_args_post += ["-o", outfile]
        self.run(**kwargs) # FIXME


class YouTubeDLHelper(Helper):

    CMD = "youtube-dl"

    def process_kwargs(self, kwargs):
        if "format" in kwargs:
            self.extra_args_post += ["-f", str(kwargs["format"])]

    def pipe_to_dst(self):
        self.extra_args_post += ["-o", "-"]

    def download(self, outfile, **kwargs):
        if outfile:
            self.extra_args_post += ["-o", outfile]
        self.play(**kwargs) # FIXME

    @classmethod
    def supports_url(cls, url):
        ies = youtube_dl.extractor.gen_extractors()
        for ie in ies:
            if ie.suitable(url) and ie.IE_NAME != 'generic':
                # Site has dedicated extractor
                return True
        return False

class StreamlinkHelper(Helper):

    PLAYER_INTEGRATED=True

    def integrate_player(self, dst):
        self.extra_args_pre += ["--player"] + [" ".join(dst.command)]

    def process_kwargs(self, kwargs):

        resolution = kwargs.pop("resolution", "best")
        # if resolution:
        self.extra_args_post.insert(0, resolution)

        offset = kwargs.pop("offset", None)

        if (offset is not False and offset is not None):
            offset_delta = timedelta(seconds=offset)
            offset_timestamp = str(offset_delta)
            logger.info("starting at time offset %s" %(offset))
            self.extra_args_pre += ["--hls-start-offset", offset_timestamp]

        headers = kwargs.pop("headers", None)
        if headers:
            self.extra_args_pre += list(
                chain.from_iterable([
                    ("--http-header", f"{k}={v}")
                for k, v in headers.items()
            ]))

        cookies = kwargs.pop("cookies", None)
        if cookies:
            self.extra_args_pre += list(
                chain.from_iterable([
                    ("--http-cookie", f"{c.name}={c.value}")
                for c in cookies
            ]))
        # super().process_kwargs(kwargs)

    @classmethod
    def supports_url(cls, url):
        try:
            return streamlink.api.Streamlink().resolve_url(url) is not None
        except streamlink.exceptions.NoPluginError:
            return False

# Put image-only viewers first so they're selected for image links by default
class FEHPlayer(Player, MEDIA_TYPES={"image"}):
    pass

class MPVPlayer(Player, MEDIA_TYPES={"audio", "image", "video"}):
    pass

class VLCPlayer(Player, MEDIA_TYPES={"audio", "image", "video"}):
    pass

class ElinksPlayer(Player, cmd="elinks", MEDIA_TYPES={"text"}, FOREGROUND=True):
    pass

class WgetDownloader(Downloader):

    def download(self, outfile, **kwargs):
        self.extra_args_post += ["-O", outfile]
        self.play(**kwargs) # FIXME

    @classmethod
    def supports_url(cls, url):
        return True

class CurlDownloader(Downloader):

    def download(self, outfile, **kwargs):
        self.extra_args_post += ["-o", outfile]
        self.play(**kwargs) # FIXME

    @classmethod
    def supports_url(cls, url):
        return True

def main():

    from tonyc_utils import logging

    logging.setup_logging(2)
    config.load(merge_default=True)
    config.settings.load()
    Program.load()

    # global PROGRAMS
    # from pprint import pprint
    # pprint(PROGRAMS)
    # raise Exception

    parser = argparse.ArgumentParser()
    options, args = parser.parse_known_args()

    # for p in [
    #         next(Program.get("streamlink")),
    #         next(Program.get("youtube-dl"))
    # ]:
    #     print(p.supports_url(args[0]))

    # streamlink = next(Helper.get("streamlink"))
    # streamlink.source = MediaSource("http://foo.com")

    # mpv = next(Player.get("mpv"))
    # mpv.source = streamlink
    # mpv.play()

    # streamlink = next(Helper.get("streamlink"))
    # streamlink.source = model.MediaSource("http://foo.com")

    # p = next(Player.get({"media_types": {"text"}}))
    # p, h = Player.get_with_helper(
    #     {"media_types": {"video"}},
    #     {
    #         "mpv": None,
    #         None: "youtube-dl",
    #     }
    # )

    # raise Exception(p, h)


    # y = Program.get(config.settings.profile.helpers.youtube_dl,
    #              "https://www.youtube.com/watch?v=5aVU_0a8-A4")
    # v = Program.get(config.settings.profile.players.vlc, y)
    # proc = v.play()
    # proc.wait()

if __name__ == "__main__":
    main()
