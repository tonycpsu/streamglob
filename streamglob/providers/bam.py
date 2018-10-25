
class BAMProviderMixin(object):
    """
    StreamSession subclass for BAMTech Media stream providers, which currently
    includes MLB.tv and NHL.tv
    """
    sport_id = 1 # FIXME

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
        with self.cache_responses_short():
            return self.get(url).json()

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
