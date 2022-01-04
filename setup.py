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
      packages=find_packages(), # + ["streamglob.providers.contrib.foo"],
      data_files=[
          ('share/doc/%s' % name, ["docs/config.yaml.sample"]),
      ],
      include_package_data=True,
      package_data = {
          "streamglob": ["data/*"]
      },
      install_requires = [
          "aio-mpv-jsonipc @ git+https://github.com/tonycpsu/aio-mpv-jsonipc@master#egg=aio-mpv-jsonipc",
          "aiofiles",
          "aiohttp",
          "aiohttp-rpc",
          "aiolimiter",
          "async_property",
          "atoma",
          "bitmath",
          "browser_cookie3 ~= 0.13.0"
          "dateparser",
          "ffmpeg-python @ git+https://github.com/hdk5/ffmpeg-python@asyncio_support#egg=ffmpeg-python",
          # "googletransx",
          # "googletrans == 4.0.0-rc1",
          "googletrans == 3.1.0a0",
          "html2text",
          "instagrapi", # FIXME
          "isodate",
          "limiter",
          "lxml",
          "marshmallow",
          "mergedeep ~= 1.3.4",
          "mistune",
          "nest_asyncio",
          "orderedattrdict",
          "panwid>=0.3.3",
          "pathvalidate",
          "pydantic",
          "pony",
          "py-dateutil",
          "pygoogletranslation ~= 2.0.6",
          "pymemoize",
          "pymediainfo ~= 5.1.0",
          "python-Levenshtein ~= 0.12.2",
          "python-twitch-client",
          "pytube >= 11.0.0",
          "pytz",
          "pyyaml",
          "pyyaml-include",
          "requests",
          "requests_html ~= 0.10.0",
          "selenium ~= 4.1.0",
          "stevedore",
          "streamlink>=0.11.0",
          "thefuzz ~= 0.19.0",
          "thumbframes_dl >= 0.11.0",
          "timeago",
          "tonyc_utils==1.0.1",
          "tzlocal",
          "unidecode",
          "urlscan",
          "urwid @ git+https://github.com/urwid/urwid",
          "urwid-readline >= 0.13",
          "urwid_utils==0.1.3.dev0",
          "urwidtrees",
          "watchdog==1.0.2",
          "wand",
          "xdg",
          "youtube-search-python ~= 1.5.3",
          "yt-dlp >= 2021.09.02",
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
