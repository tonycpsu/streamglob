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

Right now, the install process is a bit complicated due to some upstream
dependencies that need to be updated.  Furthermore, Python 3.6 is the minimum
supported version at this time.

If you're running Python 3.6+ the following should work:

    $ git clone https://github.com/tonycpsu/streamglob
    $ cd streamglob
    $ pip install .
    $ mkdir -p ~/.config/streamglob
    $ cp docs/config.yaml.sample ~/.config/streamglob/config.yaml
    $ vim ~/.config/streamglob/config.yaml

You should then be able to edit `~/.config/streamglob/config.yaml` with your
MLB.tv/NHL.tv credentials, then run `streamglob`.

Example of playing a single MLB game from the command-line:

    $ streamglob mlb/2019-03-25.phi

Run with `-v` if you run into issues.

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
detailed [here](https://github.com/tonycpsu/streamglob/graphs/contributors).

`streamglob` is a successor to
[mlbstreamer](https://github.com/tonycpsu/mlbstreamer), which was in turn
modeled after the `mlbviewer` project developed by Matthew (daftcat) of the
LinuxQuestions forums.

If you like this application and wish to support its continued development,
you can do so here:

<a href="https://www.patreon.com/tonycpsu">
<img src="https://c5.patreon.com/external/logo/become_a_patron_button@2x.png" width="100"/>
</a>
