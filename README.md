streamglob
==========

[![Build Status](https://travis-ci.org/tonycpsu/streamglob.svg?branch=master)](https://travis-ci.org/tonycpsu/streamglob )

`streamglob` facilitates live and time-shifted viewing of online content.

The current focus of this project is to provide a consistent console-based user
experience for browsing and watching live and on-demand streaming content from
various providers.  The project was originally released under the name
`mlbstreamer` with support for watching MLB.tv streams, but has expanded to
include modules for watching video from NHL.tv, YouTube, Instagram, and RSS
feeds, among others.

Installation
------------

    $ pip install git+https://github.com/tonycpsu/streamglob

Configuration
-------------

A sample configuration file is available in doc/config.yaml.sample.  Copy it to
`~/.config/streamglob/config.yaml` and modify it as needed.  A more
novice-friendly configuration mechanism is under development.

Using `streamglob`
------------------

Usage documentation to follow.

Credits
-------

Tony Cebzanov (<tonycpsu@gmail.com>) is the primary author and maintainer of
`streamglob`, but significant contributions have been made by others, as
detailed [here](https://github.com/tonycpsu/mlbstreamer/graphs/contributors).

`streamglob` is a successor to
[mlbstreamer](https://github.com/tonycpsu/mlbstreamer), which was in turn
modeled after the `mlbviewer` project developed by Matthew (daftcat) of the
LinuxQuestions forums.
