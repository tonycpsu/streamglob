import logging
logger = logging.getLogger(__name__)

import sys
import os
from itertools import chain
import functools
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
# from python_mpv_jsonipc import MPV
from aio_mpv_jsonipc import MPV
if platform.system() != "Windows":
    import termios, fcntl, struct, pty

from orderedattrdict import AttrDict, Tree
import bitmath
import youtube_dl
import streamlink

from . import config
from . import model
from . import tasks
from .state import *
from .utils import *
from .exceptions import *

PACKAGE_NAME=__name__.split('.')[0]

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

class Program(object):

    SUBCLASSES = Tree()

    PLAYER_INTEGRATED=False

    INTEGRATED_DOWNLOADERS = []

    MEDIA_TYPES = set()

    FOREGROUND = False

    PROGRAM_CMD_RE = re.compile(
        '.+?(?:(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])|$)'
    )

    with_progress = False

    def __init__(self, path, args=[], output_args=None,
                 exclude_types=None, with_progress=None,
                 stdin=None, stdout=None, stderr=None,
                 ssh_host=None,
                 **kwargs):

        self.path = path
        if isinstance(args, str):
            self.args = args.split()
        else:
            self.args = args

        # FIXME: only relevant for downloader/postprocessor
        if isinstance(output_args, str):
            self.output_args = output_args.split()
        else:
            self.output_args = output_args

        self.exclude_types = set(exclude_types) if exclude_types else set()
        # FIXME: Windows doesn't have necessary modules (pty, termios, fnctl,
        # etc. to get output from the child process for progress display.  Until
        # we have a cross-platform solution, force with_progress to False if
        # running on Windows

        if with_progress is None:
            self.with_progress = False if platform.system() == "Windows" else with_progress

        self.extra_args_pre = []
        self.extra_args_post = []

        self.source = None
        self.listing = None
        self.stdin = stdin
        if self.with_progress:
            self.stdout = subprocess.PIPE
        else:
            self.stdout = stdout
        self.stderr = stderr
        self.ssh_host = ssh_host
        self.proc = None

        self.progress = ProgressStats()
        self.progress_stream = None
        self.progress_queue = asyncio.Queue()


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
            cls.SUBCLASSES[cls.__base__.__name__.lower()][cls.cmd] = cls
            for k, v in kwargs.items():
                setattr(cls, k, v)
        super().__init_subclass__()


    @classmethod
    def get(cls, spec, *args, **kwargs):

        logger.info(f"get: {spec}")
        ptype = cls.__name__.lower()
        if spec is None:
            return None
        elif spec is True:
            # get all known programs
            return (
                p.cls(p.path, **dict(p.cfg, **kwargs))
                for n, p in state.PROGRAMS[ptype].items()
            )

        elif callable(spec):
            return (
                p.cls(p.path, **dict(p.cfg, **kwargs))
                for n, p in state.PROGRAMS[ptype].items()
                if spec(p.cls)
            )

        elif isinstance(spec, str):
            # get a program by name
            try:
                p = state.PROGRAMS[ptype][spec]
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
                for p in state.PROGRAMS[ptype].values()
                if not spec or all([
                    check_cfg_key(getattr(p, k, None), v)
                    for k, v in spec.items()
                ])
            )

        else:
            raise Exception(f"invalid program spec: {spec}")
        raise SGException(f"Program for {spec} not found")

    @classmethod
    def from_config(cls, cfg):
        klass = cls.SUBCLASSES.get(cfg.name, cls)
        return klass(**cfg)

    @classmethod
    def load(cls):

        state.PROGRAMS = Tree()

        # Add configured players

        for pcls in [Player, Downloader, Postprocessor]:

            ptype = pcls.__name__.lower()
            cfgkey = ptype + "s"
            for name, cfg in config.settings.profile[cfgkey].items():
                if not cfg:
                    cfg = AttrDict()
                path = cfg.pop("path", None) or cfg.get(
                    "command",
                    distutils.spawn.find_executable(name)
                )
                if not path:
                    logger.warning(f"couldn't find command for {name}")
                    continue
                # First, try to find by "type" config value, if present
                try:
                    klass = next(
                        c for c in cls.SUBCLASSES[ptype].values()
                        if c.__name__.lower().replace(ptype, "")
                        == cfg.get("type", "").replace("-", "").lower()
                    )
                except StopIteration:
                    # Next, try to find by config name matching class name
                    try:
                        klass = next(
                            c for c in cls.SUBCLASSES[ptype].values()
                            if c.cmd == name
                        )
                    except StopIteration:
                        # Give up and make it a generic program
                        klass = pcls
                if cfg.get("disabled") == True:
                    logger.info(f"player {name} is disabled")
                    continue
                state.PROGRAMS[ptype][name] = ProgramDef(
                    cls=klass,
                    name=name,
                    path=path,
                    cfg = AttrDict(cfg)
                )
        # Try to find any players not configured
        for ptype in cls.SUBCLASSES.keys():
            cfgkey = ptype + "s"
            for name, klass in cls.SUBCLASSES[ptype].items():
                cfg = config.settings.profile[cfgkey][name]
                if name in state.PROGRAMS[ptype] or (cfg and cfg.disabled == True):
                    continue
                path = distutils.spawn.find_executable(name)
                if path:
                    state.PROGRAMS[ptype][name] = ProgramDef(
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
    def expanded_args(self):
        source = self.source[0] if isinstance(self.source, list) else self.source
        return [
            # FIXME: only works for single source
            a.format(source=source, listing=self.listing)
            for a in self.args
        ]

    @property
    def executable_path(self):
        return [self.path] + self.expanded_args

    @property
    def source_is_program(self):
        return isinstance(self.source, Program)

    @property
    def source_integrated(self):
        return self.source_is_program and self.source.PLAYER_INTEGRATED

    def process_kwargs(self, kwargs):
        pass


    async def get_output(self):

        # FIXME: use loop.add_reader() instead of select

        # state.event_loop.add_reader(
        #     postprocessor.progress_stream,
        #     postprocessor.get_result
        # )
        # res = state.event_loop.create_future()
        # task.stage_results[-1].set_result( (await postprocessor.progress_queue.get()).split(":")[1].strip())
        # res.set_result(await postprocessor.progress_queue.get())
        # task.stage_results.append(res)
        # state.event_loop.remove_reader(postprocessor.progress_stream)

        # async def update_progress(self):
        #     async for line in self.get_output():
        #         if line:
        #             print(f"line1: {line}")
        #             return line


        # async def get_output(self):
        #     for line in os.read(self.progress_stream, 1024).decode("utf-8").split("\n"):
        #         if line:
        #             await self.progress_queue.put(line.rstrip())
        # yield os.read(self.progress_stream, 1024).decode("utf-8")
        r, w, e = select.select([ self.progress_stream ], [], [], 0)
        if self.progress_stream in r:
            for line in os.read(self.progress_stream, 1024).decode("utf-8").split("\n"):
                yield line

    @property
    def source_args(self):

        if self.source_is_program:
            return [repr(self.source)]
        elif isinstance(self.source[0], (model.MediaSource, model.MediaSource.attr_class)):
            return [s.locator for s in self.source]
        elif isinstance(self.source[0], (model.MediaTask, model.MediaTask.attr_class)):
            return [s.locator for s in self.source.sources]
        elif isinstance(self.source[0], str):
            return self.source
        else:
            raise RuntimeError(f"unsupported source: {self.source}")

    @property
    def full_command(self):

        if not self.source:
            raise RuntimeError("source not available")

        if not isinstance(self.source, list):
            self.source = [self.source]

        cmd = (
            self.executable_path
            + self.extra_args_pre
            + self.source_args
            + self.extra_args_post
        )
        if self.ssh_host:
            cmd = ["/usr/bin/ssh", self.ssh_host] + cmd

        return cmd


    async def run(self, source=None, **kwargs):
        if source:
            self.source = source

        self.process_kwargs(kwargs)

        if self.source_is_program:
            read, write = os.pipe()
            self.source.stdout = write
            self.proc = await self.source.run(**kwargs)
            os.close(write)
            self.stdin = read
            self.stdout = subprocess.PIPE

        else:
            logger.info(f"full cmd: {' '.join(self.full_command)}")

        if not self.source_integrated:

            pty_stream = None
            spawn_func = asyncio.create_subprocess_exec

            if not self.FOREGROUND:

                if self.with_progress:
                    logger.info(f"opening progress stream: {self.__class__.__name__}")
                    self.progress_stream, pty_stream = pty.openpty()
                    fcntl.ioctl(pty_stream, termios.TIOCSWINSZ,
                                struct.pack('HHHH', 50, 100, 0, 0)
                    )
                    if self.stdout is not None:
                        logger.info(f"stdout: {self.stdout}")
                    self.stdout = pty_stream

                if self.stdin is None:
                    self.stdin = subprocess.DEVNULL #open(os.devnull, 'w')
                if self.stdout is None:
                    self.stdout = subprocess.DEVNULL # open(os.devnull, 'w')
                if self.stderr is None:
                    self.stderr = subprocess.DEVNULL # open(os.devnull, 'w')
            else:
                raise NotImplementedError
            try:
                self.proc = await spawn_func(
                    *self.full_command,
                    stdin = self.stdin,
                    stdout = self.stdout,
                    stderr = self.stderr,
                )
            except SGException as e:
                logger.warning(e)
            finally:
                if pty_stream is not None:
                    os.close(pty_stream)

        return self.proc

    async def kill(self):
        self.proc.kill()

    @classmethod
    def supports_url(cls, url):
        return False


    def __repr__(self):
        return "<%s: %s %s>" %(self.__class__.__name__, self.cmd, self.args)


class Player(Program):

    @classmethod
    async def play(cls, task, player_spec=True, downloader_spec=None, **kwargs):
        # FIXME: remove task arg an just pass in sources
        downloader = None
        source = task.sources
        logger.debug(f"source: {source}, player: {player_spec}, downloader: {downloader_spec}")

        player = next(cls.get(player_spec))
        if isinstance(downloader_spec, MutableMapping):
            # if downloader spec is a dict, it maps players to downloader programs
            if player.cmd in downloader_spec:
                downloader_spec = downloader_spec[player.cmd]
            else:
                downloader_spec= downloader_spec.get(None, None)

        logger.info(f"player: {player}")
        if downloader_spec:
            # FIXME: assumption if downloader supports first source, it supports the rest
            try:
                downloader = Downloader.get(downloader_spec, task.sources[0].locator)
            except SGStreamNotFound as e:
                logger.warn(e)
                return

            if downloader:
                if downloader.cmd in player.INTEGRATED_DOWNLOADERS:
                    downloader = None
                else:
                    downloader.source = source
                    source = downloader

        task.program.set_result(player)
        player.source = source
        logger.info(f"player: {player.cmd}: downloader={downloader.cmd if downloader else downloader}, playing {source}")
        proc = await player.run(**kwargs)
        return proc

    async def load_source(self, sources):
        self.kill()
        self.source =  sources
        proc = await self.run()
        return proc


# Put image-only viewers first so they're selected for image links by default
class FEHPlayer(Player, MEDIA_TYPES={"image"}):
    pass


class MPVPlayer(Player, MEDIA_TYPES={"audio", "image", "video"}):

    INTEGRATED_DOWNLOADERS = ["youtube-dl"]

    URWID_KEY_MAPPING = {
        "UP": "cursor up",
        "DOWN": "cursor down",
        "LEFT": "cursor left",
        "RIGHT": "cursor right"
    }

    def __init__(self, *args, **kwargs):
        self._initialized = False
        self.ready = asyncio.Future()
        super().__init__(*args, **kwargs)
        self.ipc_socket_name = None
        self.tmp_dir = None
        self._ipc_socket = None
        self.extra_args_pre += ["--title=streamglob: ${media-title}"]
        self.create_socket()

    @property
    def controller(self):
        if not self.ready.done():
            return None
        return self.ready.result()

    def create_socket(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.ipc_socket_name = os.path.join(self.tmp_dir, "mpv_socket")
        self.extra_args_pre += [f"--input-ipc-server={self.ipc_socket_name}"]

    async def run(self, *args, **kwargs):
        logger.info("starting controller")
        rc = await super().run(*args, **kwargs)
        await self.wait_for_socket()
        # self.controller = MPV(start_mpv=False, ipc_socket=self.ipc_socket_name)
        controller = MPV(socket=self.ipc_socket_name)
        await controller.start()
        self.ready.set_result(controller)
        # self._initialized = True
        return rc
        # state.event_loop.call_later(5, self.test)

    async def command(self, *args, **kwargs):
        try:
            # logger.debug(f"player command: {args} {kwargs}")
            return await self.controller.command(*args, **kwargs)
        except AttributeError:
            pass
        except ConnectionResetError:
            logger.warn("player connection reset")
        except BrokenPipeError:
            logger.warn("player broken pipe")

    async def quit(self):
        await self.command("quit")

    async def wait_for_socket(self):

        while not os.path.exists(self.ipc_socket_name):
            time.sleep(0.5)

    async def load_source(self, sources):
        await self.ready
        self.source = sources
        for i, s in enumerate(self.source_args):
            await self.command("loadfile", s, "replace" if i==0 else "append")
        return self.proc

    def key_to_urwid(self, key):
        return self.URWID_KEY_MAPPING.get(key, key).lower().replace("alt+", "meta ").replace("ctrl+", "ctrl ")

    def __getattr__(self, attr):
        if attr in ["_initialized"] or not self._initialized:
            return object.__getattribute__(self, attr)
        return getattr(self.controller, attr)

    # def __setattr__(self, attr, value):
    #     if attr in ["_initialized"] or not self._initialized or attr not in self.controller.properties:
    #         return object.__setattr__(self, attr, value)
    #     return setattr(self.controller, attr, value)

    def __del__(self):
        if self.tmp_dir:
            shutil.rmtree(self.tmp_dir)


class VLCPlayer(Player, MEDIA_TYPES={"audio", "image", "video"}):
    pass

class ElinksPlayer(Player, cmd="elinks", MEDIA_TYPES={"text"}, FOREGROUND=True):
    pass



class Downloader(Program):

    @classmethod
    async def download(cls, task, outfile, downloader_spec=None, **kwargs):
        # FIXME: downloader may handle file naming
        if os.path.exists(outfile):
            raise SGFileExists(f"File {outfile} already exists")
        source = task.sources[0] # FIXME

        if isinstance(downloader_spec, MutableMapping):
            downloader_spec = downloader_spec.get(None, downloader_spec, **kwargs)
        try:
            downloader = Downloader.get(downloader_spec, source.locator, **kwargs)
        except SGStreamNotFound as e:
            downloader = next(Downloader.get(downloader_spec, **kwargs))
            logger.warn(e)
            return

        downloader.process_args(task, outfile, **kwargs)
        downloader.source = source
        downloader.listing = task.listing

        task.program.set_result(downloader)
        logger.info(f"downloader: {downloader.cmd}, downloading {source} to {outfile}")
        proc = await downloader.run(**kwargs)
        return proc


    @classmethod
    def get(cls, spec, url=None, **kwargs):
        def sort_key(p):
            if isinstance(spec, MutableMapping):
                return spec.index(h.cmd) if h.cmd in spec else len(spec)+1
            else:
                return 0

        # logger.error(
        #     [ d for d in super().get(spec, **kwargs)
        #       if d.supports_url(url)
        # ])
        if not spec:
            spec = True
        try:
            return next(iter(
                sorted((
                    h for h in super().get(spec, **kwargs)
                    if h.supports_url(url)),
                    key = sort_key
            )))
        except (TypeError, StopIteration) as e:
            logger.error(e)
            return next(iter(super().get(spec, **kwargs)))

    @property
    def is_simple(self):
        raise NotImplementedError

    def process_args(self, task, outfile, **kwargs):
        pass


class YouTubeDLDownloader(Downloader):

    CMD = "youtube-dl"
    PROGRESS_RE = re.compile(
        r"(\d+\.\d+)% of ~?(\d+.\d+\S+)(?: at\s+(\d+\.\d{2}\d*\S+) ETA (\d+:\d+))?"
    )

    with_progress = True

    def __init__(self, path, *args, **kwargs):
        super().__init__(path, *args, **kwargs)
        if self.with_progress:
            self.extra_args_pre += ["--newline"]

    @property
    def is_simple(self):
        return False

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
                logger.debug(line)
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
                except AttributeError as e:
                    logger.error(e)

        t = asyncio.create_task(process_lines())
        await asyncio.sleep(1)
        t.cancel()


class StreamlinkDownloader(Downloader):

    PLAYER_INTEGRATED=True

    PROGRESS_RE = re.compile(
        r"Written (\d+.\d+ \S+) \((\d+\S+) @ (\d+.\d+ \S+)\)"
    )

    with_progress = True

    @property
    def is_simple(self):
        return False

    def integrate_player(self, dst):
        self.extra_args_pre += ["--player"] + [" ".join(dst.executable_path + dst.extra_args_pre)]

    def process_args(self, task, outfile, **kwargs):
        self.extra_args_post += ["-o", outfile]

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
                logger.debug(line)
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

    @property
    def is_simple(self):
        return True


    @classmethod
    def supports_url(cls, url):
        return True

    def process_args(self, task, outfile, **kwargs):
        self.extra_args_post += ["-O", outfile]

class CurlDownloader(Downloader):

    @property
    def is_simple(self):
        return True

    @classmethod
    def supports_url(cls, url):
        return True

    def process_args(self, task, outfile, **kwargs):
        self.extra_args_post += ["-o", outfile]



class Postprocessor(Program):

    def get_result(self):
        asyncio.create_task(self.get_output())

    @classmethod
    async def process(cls, task, postprocessor_spec, infile, outfile, **kwargs):

        postprocessor = next(Postprocessor.get(postprocessor_spec))
        postprocessor.source = infile
        postprocessor.listing = task.listing
        postprocessor.process_args(task, outfile, **kwargs)
        logger.debug(f"postprocessor: {id(postprocessor):x} {postprocessor.cmd}, processing {infile} => {outfile}")
        task.program.set_result(postprocessor)
        proc = await postprocessor.run(**kwargs)

        return proc

    def process_args(self, task, outfile, **kwargs):
        self.extra_args_post += self.expanded_output_args(task, outfile)

    def expanded_output_args(self, task, outfile):
        return [
            a.format(source=self.source[0], listing=self.listing, task=task, outfile=outfile)
            for a in (self.output_args or [outfile])
        ]

    # def process(self, infile):
    #     logger.info("process")
    #     outfile = f"{infile}.moved"
    #     shutil.move(infile, outfile)
    #     return outfile


async def get():
    return await(Downloader.download(
        model.MediaSource("https://www.youtube.com/watch?v=5aVU_0a8-A4"),
        "foo.mp4",
        "youtube-dl"
    ))

async def check_progress(program):
    while True:
        await asyncio.sleep(2)
        # r = await program.proc.stdout.read()
        await program.update_progress()
        print(program.progress)
        # print(program.progress.size)
        # print(r)

def play_test():
    task = model.PlayMediaTask(
        provider="rss",
        title= "foo",
        sources = [
            model.MediaSource("youtube", "https://www.youtube.com/watch?v=5aVU_0a8-A4")
        ]
    )

    result = asyncio.run(
        state.task_manager.play(
            task,
            with_progress=False,
            stdout=sys.stdout, stderr=sys.stderr,
            player_spec="mpv",
            downloader_spec=None
        ).result
    )
    return result


def download_test():

    task = model.DownloadMediaTask(
        provider="rss",
        title= "foo",
        sources = [
            model.MediaSource(
                "rss",
                "https://www.nasa.gov/sites/all/themes/custom/nasatwo/images/nasa-logo.svg",
                media_type="image")
        ],
        postprocessors = ["test", "test"],
        dest="foo.svg",
    )


    result = asyncio.run(
        state.task_manager.download(
            task,
            downloader_spec = lambda d: d.is_simple,
        ).result
    )
    logger.info(f"result: {result}")
    return result


def postprocessor_test():

    # p = next(Postprocessor.get("test"))

    # proc = asyncio.run(
    #     p.process(
    #         "foo.svg"
    #     )
    # )
    # asyncio.run(proc.wait())

    proc = asyncio.run(Postprocessor.process("test", "foo.svg"))
    asyncio.run(proc.wait())



def main():

    global options
    global logger

    from tonyc_utils.logging import setup_logging, add_log_handler

    parser = argparse.ArgumentParser()
    parser.add_argument("-c", "--config-dir", help="use alternate config directory")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("-v", "--verbose", action="count", default=0,
                        help="verbose logging")
    group.add_argument("-q", "--quiet", action="count", default=0,
                        help="quiet logging")
    options, args = parser.parse_known_args()

    state.options = AttrDict(vars(options))

    logger = logging.getLogger()

    config.load(options.config_dir, merge_default=True)

    Player.load()

    # providers.load()

    model.init()

    sh = logging.StreamHandler()
    state.logger = setup_logging(options.verbose - options.quiet, quiet_stdout=False)

    state.event_loop = asyncio.get_event_loop()
    state.task_manager = tasks.TaskManager()

    state.task_manager_task = state.event_loop.create_task(state.task_manager.start())

    # log_file = os.path.join(config.settings.CONFIG_DIR, f"{PACKAGE_NAME}.log")
    # fh = logging.FileHandler(log_file)
    # add_log_handler(fh)

    download_test()

    state.event_loop.create_task(state.task_manager.stop())
    state.task_manager_task.cancel()
    # postprocessor_test()

if __name__ == "__main__":
    main()
