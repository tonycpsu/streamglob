streamglob
==========

[![Build Status](https://travis-ci.org/tonycpsu/streamglob.svg?branch=master)](https://travis-ci.org/tonycpsu/streamglob )

`streamglob` facilitates live and time-shifted viewing of streaming video.

The current focus of this project is to provide a consistent console-based user
experience for browsing and watching live and on-demand content from various
providers.  The project was originally released under the name `mlbstreamer`
with support for watching MLB.tv streams, but has expanded to include modules
for watching video from NHL.tv, YouTube, Instagram, and RSS feeds, among others.

The `streamglob` package consists of the following programs:

* `streamglob` - a full-featured TUI (terminal user interface) that allows
you to browse schedules and open game streams

* `sgplay` - a simple command-line program for watching a single game

Registered accounts are required in order to stream content from some of these
providers, and some providers require a paid subscription to access some or all
content.

Installation
------------

The easiest way to install is using pip:

    pip install streamglob

To upgrade, just add `-U`:

    pip install -U streamglob

This installation method should pull down all the necessary dependencies from
PyPI.

Configuration
-------------

**FIXME: THIS IS BROKEN -- MUST EDIT CONFIG FILE MANUALLY FOR NOW**

The first thing you'll need to do is configure your MLB.tv username, password,
etc. To do that, run:

    sgplay --init-config

The program should ask you for your username and password, then try to find your
media player (it just looks for mpv or vlc right now.). If it doesn't find it,
you can enter the full path to whatever you're using. If your player worked with
mlbviewer, it should work with streamglob. It'll also ask you for your time
zone so that game times are displayed properly.

Using `streamglob`
------------------

When you run `streamglob`, you should see a list of today's MLB games.  Use
the left/right arrows to browse days, "t" to go to today's games, and "w" to
watch the currently selected game. The log window at the bottom should tell you
if there are any errors, like if the game doesn't have a stream, if it's blacked
out, etc. The toolbar at the top allows you to select the output resolution and
some other options.

Using `sgplay`
--------------

If you just want a simple command-line interface and know the game you want to
watch, you can use `sgplay`.  The simplest syntax to watch today's game
from your favorite team is:

    sgplay [TEAM]

where `[TEAM]` is a three-letter team code, e.g. `phi`.  If you're
unsure of the team code, run sgplay with a bogus team code.

To stream at a different resolution, use the `-r` option:

    sgplay -r 360p phi

If you want to watch a game for a different date, run with the -d option, e.g:

    sgplay -d 2018-04-03 phi

You can also save the stream to disk with the -s option, e.g:

    sgplay -s ~/Movies/mlb phi

The `-b` (`begin`) option can be used to begin playback at a specified time.

* The `-b` option with no arguments causes a live stream to be played back from
the beginning of the broadcast.
* If you'd like to start somewhere other than the beginning of the broadcast,
the `-b` option can take an argument of the following forms:
    * an integer number of seconds from the start of the broadcast
    * a time string in mm:ss or h:mm:ss format
    * a string like "T3" (top of the third) or "B1" (bottom of the first),
      indicating that playback should begin at the start of the specified half
      inning

For a list of additional options, run

    sgplay --help

Caveats
-------

* MLB.tv: Right now, only major league games are supported, though support for
  MiLB (minor league) games is planned.
* NHL.tv: NHL support is very new and very lightly tested.

Credits
-------

Tony Cebzanov <tonycpsu@gmail.com> is the primary author and maintainer of
`streamglob`, but significant contributions have been made by others, as
detailed [here](https://github.com/tonycpsu/mlbstreamer/graphs/contributors).

`streamglob` is a successor to
[mlbstreamer](https://github.com/tonycpsu/mlbstreamer), which was in turn
modeled after the `mlbviewer` project developed by Matthew (daftcat) of the
LinuxQuestions forums.
