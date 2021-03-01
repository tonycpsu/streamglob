#!/usr/bin/env python
# -*- coding: utf-8 -*-

from setuptools import setup, find_packages
import sys
from os import path
from glob import glob

name = "streamglob"
setup(name=name,
      version="0.0.11.dev0",
      description="Streaming video browser and player frontend",
      author="Tony Cebzanov",
      author_email="tonycpsu@gmail.com",
      url="https://github.com/tonycpsu/streamglob",
      python_requires='>=3.7',
      classifiers=[
          "Environment :: Console",
          "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
          "Intended Audience :: End Users/Desktop"
      ],
      license = "GPLv2",
      packages=find_packages(),# + ["streamglob.providers.contrib.foo"],
      data_files=[
          ('share/doc/%s' % name, ["docs/config.yaml.sample"]),
      ],
      include_package_data=True,
      package_data = {
          "streamglob": ["data/*"]
      },
      install_requires = [
          "aio-mpv-jsonipc",
          "aiofiles",
          "aiohttp",
          "aiohttp-json-rpc",
          "aiolimiter",
          "async_property",
          "atoma",
          "bitmath",
          "ffmpeg-python @ git+https://github.com/hdk5/ffmpeg-python@asyncio_support#egg=ffmpeg-python",
          "googletransx",
          "html2text",
          "instaloader == 4.5.5",
          "isodate",
          "lxml",
          "mistune",
          "orderedattrdict",
          "panwid>=0.3.0.dev15",
          "pathvalidate",
          "pony",
          "py-dateutil",
          "pygoogletranslation @ git+https://github.com/jonata/py-googletranslation@main#egg=pygoogletranslation",
          "pymemoize",
          "python-twitch-client",
          "pytz",
          "pyyaml",
          "pyyaml-include",
          "requests",
          "stevedore",
          "streamlink>=0.11.0",
          "timeago",
          "tonyc_utils==1.0.1",
          "tzlocal",
          "urlscan",
          "urwid @ git+https://github.com/urwid/urwid",
          "urwid_utils==0.1.3.dev0",
          "urwidtrees",
          "watchdog==1.0.2",
          "wand",
          "xdg",
          "youtube_dl",
          # see https://github.com/ping/instagram_private_api/pull/269/commits
      ],
      test_suite="test",
      entry_points = {
          "console_scripts": [
              "streamglob=streamglob.__main__:main"
          ],
          "streamglob.providers": [
              "instagram = streamglob.providers.instagram:InstagramProvider",
              "mlb = streamglob.providers.mlb:MLBProvider",
              "nhl = streamglob.providers.nhl:NHLProvider",
              "rss = streamglob.providers.rss:RSSProvider",
              "twitch = streamglob.providers.twitch:TwitchProvider",
              "youtube = streamglob.providers.youtube:YouTubeProvider",
          ]
      },
      zip_safe=False
     )
