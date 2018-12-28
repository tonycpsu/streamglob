import logging
logger = logging.getLogger(__name__)

import os
import abc
from itertools import chain
from functools import reduce
import shlex
import subprocess
from datetime import timedelta


from orderedattrdict import AttrDict
import youtube_dl

from . import config
from .state import *
from .utils import *
from .exceptions import *

class Player(abc.ABC):

    SUBCLASSES = {}

    MEDIA_TYPES = []

    PLAYER_INTEGRATED=False

    def __init__(self, cfg, source=None):
        self.cfg = cfg
        self.extra_args_pre = []
        self.extra_args_post = []
        self.source = source
        self.stdin = None
        self.stdout = None
        self.stderr = None
        self.proc = None

    @classmethod
    def register_player_class(cls, cmd):
        def decorator(subclass):
            cls.SUBCLASSES[cmd] = subclass
            return subclass
        return decorator

    @classmethod
    def get(cls, cfg, *args, **kwargs):
        if isinstance(cfg, str):
            cfg = AttrDict(name=cfg, command=cfg)
        # cmd = os.path.split(cfg.command)[-1]
        if cfg.name in cls.SUBCLASSES:
            return cls.SUBCLASSES[cfg.name](cfg, *args, **kwargs)
        raise Exception
        return cls(cfg, *args, **kwargs)


    @property
    def executable(self):
        return self.cfg.command

    @property
    def args(self):
        return self.cfg.get("args", "").split()

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
        return [self.executable] + self.args

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
        logger.info(f"{self.__class__.__name__} playing {self.source}")

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

        logger.trace(f"{self.__class__.__name__} running {cmd}")

        # raise Exception(f"play: {' '.join(cmd)},"
        #       f"({(self.stdin, self.stdout, self.stderr)})")

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

    @property
    def executable(self):
        return "youtube-dl"

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


@Player.register_player_class("mpv")
class MPVPlayer(Player):
    pass

@Player.register_player_class("vlc")
class VLCPlayer(Player):
    pass


def main():

    from tonyc_utils import logging

    logging.setup_logging(2)
    config.settings.load()

    # y = Player.get(config.settings.profile.helpers.youtube_dl,
    #              "https://www.youtube.com/watch?v=5aVU_0a8-A4")
    # v = Player.get(config.settings.profile.players.vlc, y)
    # proc = v.play()
    # proc.wait()

    s = Player.get(config.settings.profile.helpers.streamlink,
                 ["https://www.youtube.com/watch?v=5aVU_0a8-A4", "720p"])
    m = Player.get(config.settings.profile.players.mpv, s)
    proc = m.play()
    proc.wait()

if __name__ == "__main__":
    main()
