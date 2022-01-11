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
      package_data={
          "streamglob": ["data/*"]
      },
      install_requires=[
          "aio-mpv-jsonipc @ git+https://github.com/tonycpsu/aio-mpv-jsonipc@master#egg=aio-mpv-jsonipc",
          "aiofiles ~= 0.7.0",
          "aiohttp ~= 3.7.4.post0",
          "aiohttp-rpc ~= 1.0.0",
          "aiolimiter ~= 1.0.0",
          "async_property ~= 0.2.1",
          "atoma ~= 0.0.17",
          "bitmath ~= 1.3.3.1",
          "browser_cookie3 ~= 0.13.0",
          "dateparser ~= 1.1.0",
          "ffmpeg-python @ git+https://github.com/hdk5/ffmpeg-python@asyncio_support#egg=ffmpeg-python",
          # "googletransx",
          # "googletrans == 4.0.0-rc1",
          "googletrans @ git+https://github.com/tonycpsu/py-googletrans@feature/fix-httpx-dependency #egg=py-googletrans",
          "html2text ~= 2020.1.16",
          "instagrapi ~= 1.15.7", # FIXME
          "isodate ~= 0.6.0",
          "limiter ~= 0.1.2",
          "lxml ~= 4.6.3",
          "mergedeep ~= 1.3.4",
          "mistune ~= 0.8.4",
          "nest_asyncio ~= 1.5.1",
          "orderedattrdict ~= 1.6.0",
          "panwid >= 0.3.5",
          "pathvalidate ~= 2.5.0",
          "Pillow ~= 8.4.0",
          "pony == 0.7.14",
          "py-dateutil ~= 2.2",
          "pydantic ~= 1.8.2",
          "pygoogletranslation ~= 2.0.6",
          "PyMemoize ~= 1.0.3",
          "pymediainfo ~= 5.1.0",
          "python-Levenshtein ~= 0.12.2",
          "python-twitch-client",
          "pytube >= 11.0.0",
          "pytz ~= 2021.3",
          "pyyaml ~= 5.4.1",
          "pyyaml-include ~= 1.2.post2",
          "requests ~= 2.26.0",
          "requests_html ~= 0.10.0",
          "selenium ~= 4.1.0",
          "stevedore ~= 3.5.0",
          "streamlink>=0.11.0",
          "thefuzz ~= 0.19.0",
          "thumbframes_dl >= 0.11.0",
          "timeago ~= 1.0.15",
          "tonyc_utils ~= 1.0.2",
          "unidecode ~= 1.3.2",
          "urlscan ~= 0.9.7",
          "urwid ~= 2.1.2",
          "urwid-readline >= 0.13",
          "urwid_utils==0.1.3.dev0",
          "watchdog==1.0.2",
          "Wand ~= 0.6.7",
          "xdg ~= 5.1.1",
          "youtube-search-python ~= 1.5.3",
          "yt-dlp >= 2021.09.02",
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
