import abc

from orderedattrdict import AttrDict
from itertools import chain
import shlex
import subprocess

from ..session import *
from .widgets import *
from ..state import *

class MediaItem(AttrDict):

    def __repr__(self):
        s = ",".join(f"{k}={v}" for k, v in self.items() if k != "title")
        return f"<{self.__class__.__name__}: {self.title}{ ' (' + s if s else ''})>"


class MediaAttributes(AttrDict):

    def __repr__(self):
        state = "!" if self.state == "MEDIA_ON" else "."
        free = "_" if self.free else "$"
        return f"{state}{free}"


class BaseProvider(abc.ABC):

    SESSION_CLASS = StreamSession
    # FILTERS = AttrDict()
    ATTRIBUTES = ["title"]

    def __init__(self, *args, **kwargs):
        # self.session = self.SESSION_CLASS(*args, **kwargs)
        self.session = self.SESSION_CLASS.new(*args, **kwargs)
        self.filters = AttrDict({n: f() for n, f in self.FILTERS.items() })

    # @property
    # @abc.abstractmethod
    # def filters(self):
    #     pass

    @abc.abstractmethod
    def login(self):
        pass

    @abc.abstractmethod
    def listings(self, filters=None):
        pass

    @abc.abstractmethod
    def make_view(self):
        pass

    @abc.abstractmethod
    def update(self):
        pass


    def play_stream(self, url,
                    resolution=None,
                    offset=None,
                    output=None,
                    verbose=0):

        allow_stdout=False
        offset_timestamp = None
        offset_seconds = None

        if resolution is None:
            resolution = "best"

        if (offset is not False and offset is not None):

            timestamps = self.session.media_timestamps(game_id, media_id)

            if isinstance(offset, str):
                if not offset in timestamps:
                    raise SGException("Couldn't find inning %s" %(offset))
                offset = timestamps[offset] - timestamps["SO"]
                logger.debug("inning offset: %s" %(offset))

            if (media_state == "MEDIA_ON"): # live stream
                logger.debug("live stream")
                # calculate HLS offset, which is negative from end of stream
                # for live streams
                start_time = dateutil.parser.parse(timestamps["S"])
                offset_delta = (
                    datetime.now(pytz.utc)
                    - start_time.astimezone(pytz.utc)
                    + (timedelta(seconds=-offset))
                )
            else:
                logger.debug("recorded stream")
                offset_delta = timedelta(seconds=offset)

            offset_seconds = offset_delta.seconds
            offset_timestamp = str(offset_delta)
            logger.info("starting at time offset %s" %(offset))

        header_args = []
        cookie_args = []

        if self.session.headers:
            header_args = list(
                chain.from_iterable([
                    ("--http-header", f"{k}={v}")
                for k, v in self.session.headers.items()
            ]))

        if self.session.cookies:
            cookie_args = list(
                chain.from_iterable([
                    ("--http-cookie", f"{c.name}={c.value}")
                for c in self.session.cookies
            ]))

        cmd = [
            "streamlink",
            # "-l", "debug",
            "--player", config.settings.profile.player,
        ] + cookie_args + header_args + [
            url,
            resolution,
        ]

        if config.settings.profile.streamlink_args:
            cmd += shlex.split(config.settings.profile.streamlink_args)

        if offset_timestamp:
            cmd += ["--hls-start-offset", offset_timestamp]

        if verbose > 1:

            allow_stdout=True
            cmd += ["-l", "debug"]

            if verbose > 2:
                if not output:
                    cmd += ["-v"]
                cmd += ["--ffmpeg-verbose"]

        if output is not None:
            if output == True or os.path.isdir(output):
                outfile = get_output_filename(
                    game,
                    media["callLetters"],
                    resolution,
                    offset=str(offset_seconds)
                )
                if os.path.isdir(output):
                    outfile = os.path.join(output, outfile)
            else:
                outfile = output

            cmd += ["-o", outfile]

        logger.debug("Running cmd: %s" % " ".join(cmd))
        try:
            state.proc = subprocess.Popen(cmd, stdout=None if allow_stdout else open(os.devnull, 'w'))
        except SGException as e:
            logger.warning(e)





class SimpleProviderViewMixin(object):

    def make_view(self):

        self.toolbar = FilterToolbar(self.filters)
        self.table = ProviderDataTable(
            self.listings,
            [ panwid.DataTableColumn(k, **v if v else {}) for k, v in self.ATTRIBUTES.items() ]
        )
        urwid.connect_signal(self.toolbar, "filter_change", self.on_filter_change)
        urwid.connect_signal(self.table, "select", self.on_select)

        self.pile  = urwid.Pile([
            (3, self.toolbar),
            ("weight", 1, self.table)
        ])
        self.pile.focus_position = 0
        return self.pile

    def on_filter_change(self, source, widget, value):
        self.update()

    def on_select(self, widget, selection):
        self.play(selection)

    def update(self):

        self.table.reset()
        # self.table.requery()