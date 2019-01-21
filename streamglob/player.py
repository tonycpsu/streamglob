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

from orderedattrdict import AttrDict
import youtube_dl

from . import config
from .state import *
from .utils import *
from .exceptions import *

PLAYERS = AttrDict()

class Player(abc.ABC):

    SUBCLASSES = {}

    MEDIA_TYPES = set()

    PLAYER_INTEGRATED=False

    def __init__(self, name, path=None, args=[], exclude_types=None):
        self.name = name
        self.path = path or self.name
        if isinstance(args, str):
            self.args = args.split()
        else:
            self.args = args
        self.exclude_types = exclude_types or set()

        self.extra_args_pre = []
        self.extra_args_post = []

        self.source = None
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self.proc = None

    @classmethod
    def register_player_class(cls, cmd, media_types=None):
        def decorator(subclass):
            cls.SUBCLASSES[cmd] = subclass
            cls.SUBCLASSES[cmd].MEDIA_TYPES = media_types or set()
            return subclass
        return decorator

    @classmethod
    def get(cls, spec=None, *args, **kwargs):

        global PLAYERS

        if isinstance(spec, str):
            # get the player by name
            try:
                p = PLAYERS[spec]
                return p.cls(p.name, p.path)
            except KeyError:
                raise SGException(f"Player {spec} not found")

        elif isinstance(spec, set):
            try:
                p = next(
                    p for p in PLAYERS.values()
                    if spec.intersection(
                        p.cls.MEDIA_TYPES - set(getattr(p.cls, "exclude_types", []))
                    ) == spec
                )
                return p.cls(p.name, p.path)
            except StopIteration:
                raise SGException(
                    f"Player for media types {spec} not found"
                )
        else:
            raise Exception
        raise SGException(f"Player for {spec} not found")


    @classmethod
    def from_config(cls, cfg):
        klass = cls.SUBCLASSES.get(cfg.name, cls)
        # return klass(cfg.name, cfg.command, cfg.get("args", []))
        # return klass(*kargs, **kwargs)
        return klass(**cfg)

    @classmethod
    def load(cls):

        global PLAYERS

        PLAYERS = AttrDict()

        # Add configured players
        for name, cfg in config.settings.profile.players.items():
            path = cfg.get(
                "command",
                distutils.spawn.find_executable(name)
            )
            if not path:
                logger.warn(f"path for player {name} not found")
                continue
            # PLAYERS[name] = Player.from_config(cfg)
            klass = cls.SUBCLASSES.get(cfg.name, cls)
            PLAYERS[name] = AttrDict(
                dict(
                    cls=klass,
                    name=name,
                    path=path
                )
            )

        # Try to find any players not configured
        for name, klass in cls.SUBCLASSES.items():
            if name in PLAYERS:
                continue
            path = distutils.spawn.find_executable(name)
            if path:
                # PLAYERS[name] = klass(name, path, [])
                PLAYERS[name] = AttrDict(
                    dict(
                        cls=klass,
                        name=name,
                        path=path
                    )
                )


    # @property
    # def path(self):
    #     return self.cfg.command

    # @property
    # def args(self):
    #     return self.cfg.get("args", "").split()


    # @property
    # def path(self):
    #     return self.cfg.command

    # @property
    # def args(self):
    #     return self.cfg.get("args", "").split()

    @property
    def source(self):
        return self._source

    @source.setter
    def source(self, value):
        self._source = value
        if isinstance(self.source, Player):
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
        return isinstance(self.source, Player)

    @property
    def source_integrated(self):
        return self.source_is_player and self.source.PLAYER_INTEGRATED

    def process_kwargs(self, kwargs):
        pass

    def play(self, source=None, **kwargs):

        if source:
            self.source = source
        # logger.info(f"{self.__class__.__name__} playing {self.source}")

        self.process_kwargs(kwargs)

        cmd = self.command + self.extra_args_pre
        if self.source_is_player:
            self.source.stdout = subprocess.PIPE
            self.proc = self.source.play(**kwargs)
            self.stdin = self.proc.stdout
        elif isinstance(self.source, list):
            cmd += self.source
        else:
            cmd += [self.source]
        cmd += self.extra_args_post
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


@Player.register_player_class("youtube-dl")
class YoutubeDLPlayer(Player):

    # @property
    # def path(self):
    #     return "youtube-dl"

    def pipe_to_dst(self):
        self.extra_args_post += ["-o", "-"]


@Player.register_player_class("streamlink")
class StreamlinkPlayer(Player):

    PLAYER_INTEGRATED=True

    def integrate_player(self, dst):
        self.extra_args_pre += ["--player"] + [" ".join(dst.command)]

    def process_kwargs(self, kwargs):

        resolution = kwargs.pop("resolution", None)
        if resolution:
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


@Player.register_player_class("mpv", media_types={"image", "video"})
class MPVPlayer(Player):
    pass

@Player.register_player_class("vlc", media_types={"image", "video"})
class VLCPlayer(Player):
    pass

@Player.register_player_class("feh", media_types={"image"})
class FEHPlayer(Player):
    pass

def main():

    from tonyc_utils import logging

    logging.setup_logging(2)
    config.settings.load()

    Player.load()
    raise Exception(Player.get({"image"}))

    # raise Exception(MPVPlayer.MEDIA_TYPES)
    # raise Exception(Player.get({"image"]))

    # y = Player.get(config.settings.profile.helpers.youtube_dl,
    #              "https://www.youtube.com/watch?v=5aVU_0a8-A4")
    # v = Player.get(config.settings.profile.players.vlc, y)
    # proc = v.play()
    # proc.wait()

    s = Player.get("streamlink")
    m = Player.get("mpv")
    m.source = s
    proc = m.play(["https://www.youtube.com/watch?v=5aVU_0a8-A4", "720p"])
    proc.wait()

if __name__ == "__main__":
    main()
