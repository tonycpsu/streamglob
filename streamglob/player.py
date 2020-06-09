import logging
logger = logging.getLogger(__name__)

import os
import abc
from itertools import chain
from functools import reduce
import shlex
import subprocess
import asyncio
from datetime import timedelta
import distutils.spawn
import argparse
import re
from dataclasses import *
import typing
from collections.abc import MutableMapping
import select
import signal
import platform
import tempfile
import shutil
import json
import time
from python_mpv_jsonipc import MPV
if platform.system() != "Windows":
    import termios, fcntl, struct, pty

from orderedattrdict import AttrDict, Tree
import bitmath
import youtube_dl
import streamlink

from . import config
from . import model
from .state import *
from .utils import *
from .exceptions import *

PROGRAMS = Tree()

bitmath.format_string = "{value:.1f}{unit}"
bitmath.bestprefix = True

@dataclass
class ProgramDef:

    cls: type
    name: str
    path: str
    cfg: dict

    @property
    def media_types(self):
        return self.cls.MEDIA_TYPES - set(getattr(self.cfg, "exclude_types", []))

@dataclass
class ProgressStats:

    dled: typing.Optional[bitmath.Byte] = None
    total: typing.Optional[bitmath.Byte] = None
    remaining: typing.Optional[bitmath.Byte] = None
    pct: typing.Optional[float] = None
    rate: typing.Optional[bitmath.Byte] = None
    eta: typing.Optional[timedelta] = None

    @property
    def size_downloaded(self):
        if not self.size_total:
            return self.dled

        # ensure downloaded size is expressed in the same units as total
        total_cls = type(self.size_total)
        if self.dled:
            return total_cls.from_other(self.dled)
        elif self.total and self.pct:
            return total_cls.from_other((self.total * self.pct))
        return None

    @property
    def size_remaining(self):
        if self.remaining:
            return self.remaining
        if self.total and self.pct:
            return self.total * (1.0-self.pct)
        return None

    @property
    def size_total(self):
        return self.total.best_prefix(system=bitmath.SI) if self.total else None

    @property
    def percent_downloaded(self):
        return self.pct*100 if self.pct else None

    @property
    def transfer_rate(self):
        return self.rate.best_prefix(system=bitmath.SI) if self.rate else None

class Program(abc.ABC):

    SUBCLASSES = Tree()

    PLAYER_INTEGRATED=False

    INTEGRATED_HELPERS = []

    MEDIA_TYPES = set()

    FOREGROUND = False

    PROGRAM_CMD_RE = re.compile(
        '.+?(?:(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|$)'
    )

    def __init__(self, path, args=[],
                 exclude_types=None, no_progress=False,
                 stdin=None, stdout=None, stderr=None,
                 **kwargs):
        self.path = path
        if isinstance(args, str):
            self.args = args.split()
        else:
            self.args = args
        self.exclude_types = set(exclude_types) if exclude_types else set()
        # FIXME: Windows doesn't have necessary modules (pty, termios, fnctl,
        # etc. to get output from the child process for progress display.  Until
        # we have a cross-platform solution, force no_progress to False if
        # running on Windows
        self.no_progress = True if platform.system() == "Windows" else no_progress

        self.extra_args_pre = []
        self.extra_args_post = []

        self.source = None
        self.stdin = stdin
        if not self.no_progress:
            self.stdout = subprocess.PIPE
        else:
            self.stdout = stdout
        self.stderr = stderr
        self.proc = None

        self.progress = ProgressStats()
        self.progress_stream = None


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
    def get(cls, spec, *args, **kwargs):

        global PROGRAMS

        if spec is None:
            return None
        elif spec is True:
            # get all known programs
            return (
                p.cls(p.path, **dict(p.cfg, **kwargs))
                for n, p in PROGRAMS[cls].items()
            )

        elif isinstance(spec, str):
            # get a program by name
            try:
                p = PROGRAMS[cls][spec]
                return iter([p.cls(p.path, **dict(p.cfg, **kwargs))])
            except KeyError:
                raise SGException(f"Program {spec} not found")

        elif isinstance(spec, list):
            # get the listed programs by name
            return [ cls.get(p) for p in spec ]

        elif isinstance(spec, dict):
            # get a program with a given configuration
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
                p.cls(p.path, **dict(p.cfg, **kwargs))
                for p in PROGRAMS[cls].values()
                if not spec or all([
                    check_cfg_key(getattr(p, k, None), v)
                    for k, v in spec.items()
                ])
            )

        else:
            raise Exception(f"invalid program spec: {spec}")
        raise SGException(f"Program for {spec} not found")

    @classmethod
    async def play(cls, task, player_spec=True, helper_spec=None, **kwargs):

        source = task.sources
        logger.debug(f"source: {source}, player: {player_spec}, helper: {helper_spec}")

        player = next(cls.get(player_spec, no_progress=True))
        if isinstance(helper_spec, MutableMapping):
            # if helper spec is a dict, it maps players to helper programs
            if player.cmd in helper_spec:
                helper_spec = helper_spec[player.cmd]
            else:
                helper_spec= helper_spec.get(None, None)
        # else:
        #     # if helper is something else, resolve it, and get the default
        #     # player for audio and video
        #     player = next(cls.get(dict(media_types={"audio", "video"}),
        #                           no_progress=True))

        # FIXME: assumption if helper supports first source, it supports the rest
        try:
            helper = Helper.get(helper_spec, task.sources[0].locator)
        except SGStreamNotFound as e:
            logger.warn(e)
            return

        if helper and helper.cmd in player.INTEGRATED_HELPERS:
            # if player integrates helper, use it instead of spawning
            helper = None
        # if helper_spec:
        #     if isinstance(helper_spec, str):
        #         helper = next(Helper.get(helper_spec))
        #     elif isinstance(helper_spec, dict):
        #         if player.cmd in helper_spec:
        #             helper_name = helper_spec[player.cmd]
        #         else:
        #             helper_name = helper_spec.get(None, None)
        #         if helper_name:
        #             helper = next(Helper.get(helper_name))

        if helper:
            helper.source = source
            source = helper

        player.source = source
        logger.info(f"player: {player.cmd}: helper={helper.cmd if helper else helper}, playing {source}")
        task = player.run(**kwargs)
        await state.asyncio_loop.create_task(task)
        return player

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
                if not cfg:
                    cfg = AttrDict()
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
                if cfg.get("disabled") == True:
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
    def source_is_program(self):
        return isinstance(self.source, Program)

    @property
    def source_integrated(self):
        return self.source_is_program and self.source.PLAYER_INTEGRATED

    def process_kwargs(self, kwargs):
        pass

    async def get_output(self):
        # yield os.read(self.progress_stream, 1024).decode("utf-8")
        r, w, e = select.select([ self.progress_stream ], [], [], 0)
        if self.progress_stream in r:
            for line in os.read(self.progress_stream, 1024).decode("utf-8").split("\n"):
                yield line
        else:
            raise StopIteration

    async def run(self, source=None, **kwargs):

        if source:
            self.source = source

        self.process_kwargs(kwargs)

        cmd = self.command + self.extra_args_pre
        if self.source_is_program:
            read, write = os.pipe()
            # self.source.stdout = asyncio.subprocess.PIPE#subprocess.PIPE
            self.source.stdout = write
            # self.source.stdout = subprocess.PIPE
            self.proc = await self.source.run(**kwargs)
            os.close(write)
            self.stdin = read
            self.stdout = subprocess.PIPE
            # self.stdin = self.proc.stdout
            # self.stdin = self.proc._transport._proc.stdout
            # os.close(read)
            # self.proc.stdout = await self.source.stdout.read()
        elif isinstance(self.source, model.MediaTask):
            cmd += [s.locator for s in self.source.sources]

        elif isinstance(self.source, list):
            # cmd += self.source
            cmd += [s.locator for s in self.source]
        else:
            # raise Exception
            # cmd += [self.source]
            cmd += [self.source.locator]
        cmd += self.extra_args_post


        if not self.source_integrated:

            pty_stream = None
            logger.debug(f"cmd: {' '.join(cmd)}")
            spawn_func = asyncio.create_subprocess_exec
            # if self.FOREGROUND:
            #     spawn_func = subprocess.call
            # else:
            if not self.FOREGROUND:
                # spawn_func = asyncio.create_subprocess_exec
                if not self.no_progress:

                    self.progress_stream, pty_stream = pty.openpty()
                    # set width of console to 100 so we get full progress output
                    fcntl.ioctl(pty_stream, termios.TIOCSWINSZ,
                                struct.pack('HHHH', 50, 100, 0, 0)
                    )
                    self.stdin = pty_stream
                    self.stdout = pty_stream
                    self.stderr = pty_stream
                    # self.stdout = subprocess.PIPE
                    # self.stderr = subprocess.PIPE

                if self.stdin is None:
                    self.stdin = open(os.devnull, 'w')
                if self.stdout is None:
                    self.stdout = open(os.devnull, 'w')
                if self.stderr is None:
                    self.stderr = open(os.devnull, 'w')
            else:
                raise NotImplementedError
            try:

                self.proc = await spawn_func(
                    *cmd,
                    stdin = self.stdin,
                    stdout = self.stdout,
                    stderr = self.stderr,
                    # preexec_fn = pre,
                    # start_new_session = True
                )
                if pty_stream:
                    os.close(pty_stream)
                # self.progress_stream = self.proc.stdout
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
    # def update_progress(self):

    #     if self.source_is_program and hasattr(self.source, "update_progress"):
    #         return self.source.update_progress()

    #     return



class Helper(Program):

    @classmethod
    def get(cls, spec, url=None, **kwargs):

        if not spec:
            return None

        try:
            return next(iter(
                sorted((
                    h for h in super().get(spec, **kwargs)
                    if h.supports_url(url)),
                    key = lambda h: spec.index(h.cmd)
                       if h.cmd in spec else len(spec)+1
                )
            ))
        except (TypeError, StopIteration):
            return next(iter(super().get(spec, **kwargs)))



class Downloader(Program):

    @classmethod
    async def download(cls, task, outfile, helper_spec=None, **kwargs):

        if os.path.exists(outfile):
            raise SGFileExists(f"File {outfile} already exists")
        source = task.sources[0]

        if isinstance(helper_spec, MutableMapping):
            helper_spec = helper_spec.get(None, helper_spec, **kwargs)

        try:
            downloader = Helper.get(helper_spec, source.locator, **kwargs)
        except SGStreamNotFound as e:
            logger.warn(e)
            return

        logger.info(f"downloader: {downloader}")

        # if helper_spec is None:
        #     helper_spec = {}

        # if isinstance(helper_spec, str):
        #     downloader = next(Helper.get(helper_spec))
        # elif isinstance(helper_spec, dict):
        #     helper_spec = [
        #         h for h in list(AttrDict.fromkeys(helper_spec.values()))
        #         if h
        #     ]

        # # else:
        # #     raise NotImplementedError
        # try:
        #     downloader = next(iter(
        #         sorted((
        #             h for h in Helper.get()
        #             if h.supports_url(source.locator)),
        #             key = lambda h: helper_spec.index(h.cmd)
        #                if h.cmd in helper_spec else len(helper_spec)+1
        #         )
        #     ))
        # except (TypeError, StopIteration):
        #     downloader = next(cls.get())

        logger.info(f"{downloader} downloading {source.locator} to {outfile}")
        downloader.source = task
        downloader.extra_args_post += ["-o", outfile]
        # downloader.run(**kwargs)
        # state.asyncio_loop.create_task(downloader.run(**kwargs))
        await(downloader.run(**kwargs))
        return downloader

    # async def get_lines(self):
    #     for line in iter(self.progress_stream.readline, ""):
    #         yield (await line).decode("utf-8")



# Put image-only viewers first so they're selected for image links by default
class FEHPlayer(Player, MEDIA_TYPES={"image"}):
    pass

class MPVPlayer(Player, MEDIA_TYPES={"audio", "image", "video"}):

    INTEGRATED_HELPERS = ["youtube-dl"]

    def __init__(self, *args, **kwargs):
        self._initialized = False
        super().__init__(*args, **kwargs)
        self.ipc_socket_name = None
        self.tmp_dir = None
        self._ipc_socket = None
        self.controller = None
        self._initialized = True

    async def run(self, *args, **kwargs):
        self.tmp_dir = tempfile.mkdtemp()
        self.ipc_socket_name = os.path.join(self.tmp_dir, "mpv_socket")
        # logger.info(f"mpv socket: {self.ipc_socket_name}")
        self.extra_args_pre += [f"--input-ipc-server={self.ipc_socket_name}"]
        await super().run(*args, **kwargs)
        await self.wait_for_socket()
        # logger.info("starting controller")
        self.controller = MPV(start_mpv=False, ipc_socket=self.ipc_socket_name)
        # state.asyncio_loop.call_later(5, self.test)

    async def wait_for_socket(self):

        while not os.path.exists(self.ipc_socket_name):
            time.sleep(0.5)

    def __getattr__(self, attr):
        if attr in ["_initialized"] or not self._initialized:
            return object.__getattribute__(self, attr)
        return getattr(self.controller, attr)

    def __setattr__(self, attr, value):
        if attr in ["_initialized"] or not self._initialized or not hasattr(self.controller, attr):
            return object.__setattr__(self, attr, value)
        return setattr(self.controller, attr, value)

    def __del__(self):
        if self.tmp_dir:
            shutil.rmtree(self.tmp_dir)


class VLCPlayer(Player, MEDIA_TYPES={"audio", "image", "video"}):
    pass

class ElinksPlayer(Player, cmd="elinks", MEDIA_TYPES={"text"}, FOREGROUND=True):
    pass



class YouTubeDLHelper(Helper, Downloader):

    CMD = "youtube-dl"
    PROGRESS_RE = re.compile(
        r"(\d+\.\d+)% of ~?(\d+.\d+\S+)(?: at\s+(\d+\.\d{2}\d*\S+) ETA (\d+:\d+))?"
    )

    def __init__(self, path, no_progress=False, *args, **kwargs):
        super().__init__(path, *args, **kwargs)
        if not self.no_progress:
            self.extra_args_pre += ["--newline"]


    def process_kwargs(self, kwargs):
        if "format" in kwargs:
            self.extra_args_post += ["-f", str(kwargs["format"])]

    def pipe_to_dst(self):
        self.extra_args_post += ["-o", "-"]

    @classmethod
    def supports_url(cls, url):
        ies = youtube_dl.extractor.gen_extractors()
        for ie in ies:
            if ie.suitable(url) and ie.IE_NAME != 'generic':
                # Site has dedicated extractor
                return True
        return False

    async def update_progress(self):

        async def process_lines():
            async for line in self.get_output():
                if not line:
                    continue
                # logger.info(line)
                if "[download] Destination:" in line:
                    self.source.dest = line.split(":")[1].strip()
                    continue
                try:
                    (pct, total, rate, eta) = self.PROGRESS_RE.search(line).groups()
                    self.progress.pct = float(pct)/100
                    self.progress.total = bitmath.parse_string(
                            total
                    )
                    self.progress.dled = (self.progress.pct * self.progress.total)
                    self.progress.rate = bitmath.parse_string(rate.split("/")[0]) if rate else None
                    self.progress.eta = eta
                except AttributeError:
                    pass

        t = asyncio.create_task(process_lines())
        await asyncio.sleep(1)
        t.cancel()


class StreamlinkHelper(Helper, Downloader):

    PLAYER_INTEGRATED=True

    PROGRESS_RE = re.compile(
        r"Written (\d+.\d+ \S+) \((\d+\S+) @ (\d+.\d+ \S+)\)"
    )

    # def __init__(self, path, no_progress=False, *args, **kwargs):
    #     super().__init__(path, *args, **kwargs)


    def integrate_player(self, dst):
        logger.debug(f"dst: {dst}")
        self.extra_args_pre += ["--player"] + [" ".join(dst.command)]

    def process_kwargs(self, kwargs):

        resolution = kwargs.pop("resolution", "best")
        logger.info("resolution: %s" %(resolution))
        # if resolution:
        self.extra_args_post.insert(0, resolution)

        offset = kwargs.pop("offset", None)

        if (offset is not False and offset is not None):
            # offset_delta = timedelta(seconds=offset)
            # offset_timestamp = str(offset_delta)
            offset_seconds = int(offset.total_seconds())
            logger.info("time offset: %s" %(offset_seconds))
            self.extra_args_pre += ["--hls-start-offset", str(offset_seconds)]

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


    async def update_progress(self):

        async def process_lines():
            async for line in self.get_output():
                logger.info(line)
                if not line:
                    return
                try:
                    (dled, elapsed, rate) = self.PROGRESS_RE.search(line).groups()
                except AttributeError:
                    return
                self.progress.dled = bitmath.parse_string(dled)
                self.progress.rate = bitmath.parse_string(rate.split("/")[0]) if rate else None
                    # pass


                # logger.info(line)

        t = asyncio.create_task(process_lines())
        await asyncio.sleep(1)
        t.cancel()


class WgetDownloader(Downloader):

    def download(self, outfile, **kwargs):
        self.source = source
        self.extra_args_post += ["-O", outfile]
        self.run(**kwargs) # FIXME
        return self # FIXME x2

    @classmethod
    def supports_url(cls, url):
        return True

class CurlDownloader(Downloader):

    @classmethod
    def supports_url(cls, url):
        return True



async def get():
    return await(Downloader.download(
        model.MediaSource("https://www.youtube.com/watch?v=5aVU_0a8-A4"),
        "foo.mp4",
        "streamlink"
    ))

async def check_progress(program):
    while True:
        await asyncio.sleep(2)
        # r = await program.proc.stdout.read()
        await program.update_progress()
        print(program.progress)
        # print(program.progress.size)
        # print(r)

async def go():
    task = model.PlayMediaTask(
        provider="rss",
        title= "foo",
        sources = [
            model.MediaSource("youtube", "https://pscp.tv/w/1lDGLXnEeBPGm")
            # model.MediaSource("twitch", "https://steamcommunity.com/sharedfiles/filedetails/?id=1672526416")
        ]
    )

    # prog = await Player.play(task, {"media_types": {"video"}}, "streamlink")
    prog = await Downloader.download(task, "foo.mp4", "youtube-dl")
    # prog = await Downloader.download(task, "foo.mp4", "streamlink")
    state.asyncio_loop.create_task(check_progress(prog))

def main():

    from tonyc_utils import logging

    logging.setup_logging(2)
    config.load(merge_default=True)
    config.settings.load()
    Program.load()
    state.asyncio_loop = asyncio.get_event_loop()

    # global PROGRAMS
    # from pprint import pprint
    # pprint(PROGRAMS)
    # raise Exception

    parser = argparse.ArgumentParser()
    options, args = parser.parse_known_args()

    p = Helper.get("streamlink")

    state.asyncio_loop.create_task(go())
    state.asyncio_loop.run_forever()
    # for line in iter(downloader.proc.stdout.readline, b""):
    #     print(line)

    # import time; time.sleep(5)

    # raise Exception(list(Helper.get()))
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
