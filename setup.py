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
      classifiers=[
          "Environment :: Console",
          "License :: OSI Approved :: GNU General Public License v2 (GPLv2)",
          "Intended Audience :: End Users/Desktop"
      ],
      license = "GPLv2",
      packages=find_packages(),
      data_files=[
          ('share/doc/%s' % name, ["docs/config.yaml.sample"]),
      ],
      include_package_data=True,
      install_requires = [
          "six",
          "requests",
          "lxml",
          "pytz",
          "tzlocal",
          "pymemoize",
          "orderedattrdict",
          "pyyaml",
          "py-dateutil",
          "streamlink>=0.11.0",
          "prompt_toolkit",
          "urwid",
          "urwid_utils>=0.1.2",
          "panwid>=0.2.5",
          "pony",
          "stevedore"
      ],
      test_suite="test",
      entry_points = {
          "console_scripts": [
              "streamglob=streamglob.__main__:main",
              "sgplay=streamglob.play:main"
          ],
          "streamglob.providers": [
              "mlb = streamglob.providers.mlb:MLBProvider"
          ]
      }
     )
