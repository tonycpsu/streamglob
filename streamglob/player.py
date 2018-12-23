import logging
logger = logging.getLogger(__name__)

import os
import abc
from itertools import chain
import shlex
import subprocess
from datetime import timedelta

from . import config
from .state import *
from .exceptions import *

class Player(abc.ABC):

    @abc.abstractmethod
    def play(self, url, *args, **kwargs):
        pass

class BasicPlayer(abc.ABC):

    def play(self, url, *args, **kwargs):


        cmd = config.settings.profile.player.split(" ") + [url]
        logger.debug("Running cmd: %s" % " ".join(cmd))
        try:
            state.proc = subprocess.Popen(
                cmd,
                stdout=open(os.devnull, 'w'),
                stderr=open(os.devnull, 'w'),
            )
        except SGException as e:
            logger.warning(e)


class StreamlinkPlayer(Player):

    def play(self, url,
             resolution=None,
             offset=None,
             output=None,
             headers=None,
             cookies=None,
             verbose=0):

        allow_stdout=False
        offset_timestamp = None
        offset_seconds = None

        if resolution is None:
            resolution = "best"



        if (offset is not False and offset is not None):

            # # timestamps = self.session.media_timestamps(game_id, media_id)

            # # if isinstance(offset, str):
            # #     if not offset in timestamps:
            # #         raise SGException("Couldn't find inning %s" %(offset))
            # #     offset = timestamps[offset] - timestamps["SO"]
            # #     logger.debug("inning offset: %s" %(offset))

            # if (media_state == "MEDIA_ON"): # live stream
            #     logger.debug("live stream")
            #     # calculate HLS offset, which is negative from end of stream
            #     # for live streams
            #     start_time = dateutil.parser.parse(timestamps["S"])
            #     offset_delta = (
            #         datetime.now(pytz.utc)
            #         - start_time.astimezone(pytz.utc)
            #         + (timedelta(seconds=-offset))
            #     )
            # else:
            #     logger.debug("recorded stream")
            #     offset_delta = timedelta(seconds=offset)
            offset_delta = timedelta(seconds=offset)
            # offset_seconds = offset_delta.seconds
            offset_timestamp = str(offset_delta)
            logger.info("starting at time offset %s" %(offset))

        header_args = []
        cookie_args = []

        if headers:
            header_args = list(
                chain.from_iterable([
                    ("--http-header", f"{k}={v}")
                for k, v in headers.items()
            ]))

        if cookies:
            cookie_args = list(
                chain.from_iterable([
                    ("--http-cookie", f"{c.name}={c.value}")
                for c in cookies
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

        logger.info("playing url %s at %s (offset %s)" %(
            url, resolution, offset)
        )

        logger.debug("Running cmd: %s" % " ".join(cmd))
        try:
            state.proc = subprocess.Popen(
                cmd,
                stdout=None if allow_stdout else open(os.devnull, 'w')
            )
        except SGException as e:
            logger.warning(e)
