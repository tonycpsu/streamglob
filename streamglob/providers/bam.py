import logging
logger = logging.getLogger(__name__)

import urwid
from orderedattrdict import AttrDict
from datetime import datetime
import dateutil.parser
import pytz

from .. import config

def parse_int(n):
    try:
        return int(n)
    except ValueError:
        return n
    except TypeError:
        return None


class MediaAttributes(AttrDict):

    def __repr__(self):
        state = "!" if self.state == "MEDIA_ON" else "."
        free = "_" if self.free else "$"
        return f"{state}{free}"

def format_start_time(d):
    s = datetime.strftime(d, "%I:%M%p").lower()[:-1]
    if s[0] == "0":
        s = s[1:]
    return s


class BAMProviderMixin(object):
    """
    StreamSession subclass for BAMTech Media stream providers, which currently
    includes MLB.tv and NHL.tv
    """
    sport_id = 1 # FIXME

    ATTRIBUTES = AttrDict(
        attrs = {"width": 6},
        start = {"width": 6, "format_fn": format_start_time},
        away = {"width": 16},
        home = {"width": 16},
        line = {}
    )

    # @memo(region="short")
    def schedule(
            self,
            # sport_id=None,
            start=None,
            end=None,
            game_type=None,
            team_id=None,
            game_id=None,
    ):

        logger.debug(
            "getting schedule: %s, %s, %s, %s, %s, %s" %(
                self.sport_id,
                start,
                end,
                game_type,
                team_id,
                game_id
            )
        )
        url = self.SCHEDULE_TEMPLATE.format(
            sport_id = self.sport_id,
            start = start.strftime("%Y-%m-%d") if start else "",
            end = end.strftime("%Y-%m-%d") if end else "",
            game_type = game_type if game_type else "",
            team_id = team_id if team_id else "",
            game_id = game_id if game_id else ""
        )
        # with self.cache_responses_short():
        return self.session.get(url).json()


    def listings(self):
        # return [ MediaItem(line=t) for t in ["a", "b" ,"c" ] ]

        # logger.info(f"listings: {self.filters.date.value}")
        j = self.schedule(
        #     # sport_id=self.sport_id,
            start = self.filters.date.value,
            end = self.filters.date.value,
            # game_type = "R"
        )
        # logger.info(j)

        for d in j["dates"]:
            games = sorted(d["games"], key= lambda g: g["gameDate"])
            for g in games:
                logger.info(g)
                game_pk = g["gamePk"]
                game_type = g["gameType"]
                status = g["status"]["statusCode"]
                away_team = g["teams"]["away"]["team"]["teamName"]
                home_team = g["teams"]["home"]["team"]["teamName"]
                away_abbrev = g["teams"]["away"]["team"]["abbreviation"]
                home_abbrev = g["teams"]["home"]["team"]["abbreviation"]
                start_time = dateutil.parser.parse(g["gameDate"])
                attrs = MediaAttributes()
                try:
                    item = free_game = g["content"]["media"]["epg"][0]["items"][0]
                    attrs.state = item["mediaState"]
                    attrs.free = item["freeGame"]
                except:
                    attrs.state = None
                    attrs.free = None

                if config.settings.profile.time_zone:
                    start_time = start_time.astimezone(
                        pytz.timezone(config.settings.profile.time_zone)
                    )

                hide_spoiler_teams = config.settings.profile.get("hide_spoiler_teams", [])
                if isinstance(hide_spoiler_teams, bool):
                    hide_spoilers = hide_spoiler_teams
                else:
                    hide_spoilers = set([away_abbrev, home_abbrev]).intersection(
                        set(hide_spoiler_teams))
                if "linescore" in g:
                    # line_score_cls = MLBLineScoreDataTable #globals().get(f"{self.provider.upper()}LineScoreDataTable")
                    self.line_score_table = self.DATA_TABLE_CLASS.from_json(
                            g["linescore"],
                            g["teams"]["away"]["team"]["abbreviation"],
                            g["teams"]["home"]["team"]["abbreviation"],
                            hide_spoilers
                    )
                    self.line_score = urwid.BoxAdapter(
                        self.line_score_table,
                        3
                    )
                else:
                    self.line_score = None

                yield(AttrDict(
                    game_id = game_pk,
                    game_type = game_type,
                    away = away_team,
                    home = home_team,
                    start = start_time,
                    line = self.line_score,
                    attrs = attrs
                ))


    # @memo(region="short")
    def get_epgs(self, game_id, title=None):

        schedule = self.schedule(game_id=game_id)
        try:
            # Get last date for games that have been rescheduled to a later date
            game = schedule["dates"][-1]["games"][0]
        except KeyError:
            logger.debug("no game data")
            return
        epgs = game["content"]["media"]["epg"]

        if not isinstance(epgs, list):
            epgs = [epgs]

        return [ e for e in epgs if (not title) or title == e["title"] ]

    def get_media(self,
                  game_id,
                  media_id=None,
                  title=None,
                  preferred_stream=None,
                  call_letters=None):

        logger.debug(f"geting media for game {game_id} ({media_id}, {title}, {call_letters})")

        epgs = self.get_epgs(game_id, title)
        for epg in epgs:
            for item in epg["items"]:
                if (not preferred_stream
                    or (item.get("mediaFeedType", "").lower() == preferred_stream)
                ) and (
                    not call_letters
                    or (item.get("callLetters", "").lower() == call_letters)
                ) and (
                    not media_id
                    or (item.get("mediaId", "").lower() == media_id)
                ):
                    logger.debug("found preferred stream")
                    yield Media(item)
            else:
                if len(epg["items"]):
                    logger.debug("using non-preferred stream")
                    yield Media(epg["items"][0])
        # raise StopIteration

    def get_url(self, game_specifier, resolution=None,
                offset=None,
                media_id = None,
                preferred_stream=None,
                call_letters=None,
                output=None,
                verbose=0):

        live = False
        team = None
        game_number = 1
        game_date = None
        # sport_code = "mlb" # default sport is MLB

        # media_title = "MLBTV"
        media_id = None
        allow_stdout=False

        if resolution is None:
            resolution = "best"

        if isinstance(game_specifier, int):
            game_id = game_specifier
            schedule = self.session.schedule(
                game_id = game_id
            )

        else:
            try:
                (game_date, team, game_number) = game_specifier.split(".")
            except ValueError:
                try:
                    (game_date, team) = game_specifier.split(".")
                except ValueError:
                    game_date = datetime.now().date()
                    team = game_specifier

            if "-" in team:
                (sport_code, team) = team.split("-")

            game_date = dateutil.parser.parse(game_date)
            game_number = int(game_number)
            teams =  self.session.teams(season=game_date.year)
            team_id = teams.get(team)

            if not team:
                msg = "'%s' not a valid team code, must be one of:\n%s" %(
                    game_specifier, " ".join(teams)
                )
                raise argparse.ArgumentTypeError(msg)

            schedule = self.session.schedule(
                start = game_date,
                end = game_date,
                # sport_id = sport["id"],
                team_id = team_id
            )
            # raise Exception(schedule)


        try:
            date = schedule["dates"][-1]
            game = date["games"][game_number-1]
            game_id = game["gamePk"]
        except IndexError:
            raise SGException("No game %d found for %s on %s" %(
                game_number, team, game_date)
            )

        logger.info("playing game %d at %s" %(
            game_id, resolution)
        )

        away_team_abbrev = game["teams"]["away"]["team"]["abbreviation"].lower()
        home_team_abbrev = game["teams"]["home"]["team"]["abbreviation"].lower()

        if not preferred_stream or call_letters:
            preferred_stream = (
                "away"
                if team == away_team_abbrev
                else "home"
            )

        try:
            media = next(self.session.get_media(
                game_id,
                media_id = media_id,
                # title=media_title,
                preferred_stream=preferred_stream,
                call_letters = call_letters
            ))
        except StopIteration:
            raise SGException("no matching media for game %d" %(game_id))

        # media_id = media["mediaId"] if "mediaId" in media else media["guid"]

        media_state = media["mediaState"]

        # Get any team-specific profile overrides, and apply settings for them
        profiles = tuple([ list(d.values())[0]
                     for d in config.settings.profile_map.get("team", {})
                     if list(d.keys())[0] in [
                             away_team_abbrev, home_team_abbrev
                     ] ])

        if len(profiles):
            # override proxies for team, if defined
            if len(config.settings.profiles[profiles].proxies):
                old_proxies = self.session.proxies
                self.session.proxies = config.settings.profiles[profiles].proxies
                # self.session.refresh_access_token(clear_token=True)
                self.session.proxies = old_proxies

        if "playbacks" in media:
            playback = media["playbacks"][0]
            media_url = playback["location"]
        else:
            stream = self.session.get_stream(media)

            try:
                # media_url = stream["stream"]["complete"]
                media_url = stream.url
            except (TypeError, AttributeError):
                raise SGException("no stream URL for game %d" %(game_id))

        return media_url

    def play(self, selection):

        game_id = selection.get("game_id")
        url = self.get_url(game_id)
        self.play_stream(url, resolution=self.filters.resolution.value)
