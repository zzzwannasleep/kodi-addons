# -*- coding: utf-8 -*-
"""Turning UHD/Emby item dicts into Kodi ListItems."""
import re
import urllib.parse

import xbmc
import xbmcgui
import xbmcplugin

from .api import TICKS_PER_SEC

# Emby item Type -> (Kodi media type, Kodi content type for the container)
MEDIA_TYPES = {
    "Movie": ("movie", "movies"),
    "Series": ("tvshow", "tvshows"),
    "Season": ("season", "seasons"),
    "Episode": ("episode", "episodes"),
    "Video": ("video", "videos"),
}

ART_MAP = (("Primary", "poster"), ("Thumb", "thumb"), ("Banner", "banner"),
           ("Logo", "clearlogo"), ("Art", "clearart"), ("Disc", "discart"))


def build_url(base, **kwargs):
    clean = {k: v for k, v in kwargs.items() if v is not None}
    return base + "?" + urllib.parse.urlencode(clean)


def _seconds(ticks):
    return int(ticks // TICKS_PER_SEC) if ticks else 0


def _art(api, item):
    art = {}
    tags = item.get("ImageTags") or {}
    for emby_kind, kodi_key in ART_MAP:
        if emby_kind in tags:
            art[kodi_key] = api.image_url(item["Id"], emby_kind, tags[emby_kind])
    if item.get("BackdropImageTags"):
        art["fanart"] = api.image_url(item["Id"], "Backdrop",
                                      item["BackdropImageTags"][0])
    elif item.get("ParentBackdropImageTags") and item.get("ParentBackdropItemId"):
        art["fanart"] = api.image_url(item["ParentBackdropItemId"], "Backdrop",
                                      item["ParentBackdropImageTags"][0])
    # Episodes have no poster of their own; fall back to the still, then to the
    # series poster, so list views are never blank.
    if "poster" not in art and item.get("SeriesPrimaryImageTag") and item.get("SeriesId"):
        art["poster"] = api.image_url(item["SeriesId"], "Primary",
                                      item["SeriesPrimaryImageTag"])
    if "thumb" not in art:
        art["thumb"] = art.get("poster", "")
    return art


def _episode_code(item):
    """S01E08, or "" when the server did not number the episode."""
    season, episode = item.get("ParentIndexNumber"), item.get("IndexNumber")
    if episode is None:
        return ""
    return "S%02dE%02d" % (season or 0, episode)


# Episode names that carry no information beyond the number already in the code.
_NUMERIC_NAME = re.compile(r"^第\s*\d+\s*[集话話]$|^Episode\s*\d+$", re.I)


def _label(item, standalone=False):
    """standalone=True for mixed lists (Resume, Next up, Recently added) where
    the series name is not implied by the surrounding folder."""
    kind = item.get("Type")
    name = item.get("Name") or ""
    if kind == "Episode":
        code = _episode_code(item)
        if standalone:
            series = item.get("SeriesName") or ""
            # "葬送的芙莉莲 S01E08" -- the episode's own name is dropped here on
            # purpose; in a mixed list the show and number are what identify it.
            return " ".join(p for p in (series, code) if p) or name
        if not code:
            return name
        # "S01E08" alone when the name is just "第 8 集" and would repeat it.
        return code if _NUMERIC_NAME.match(name.strip()) else "%s %s" % (code, name)
    year = item.get("ProductionYear")
    if kind in ("Movie", "Series") and year:
        return "%s (%s)" % (name, year)
    return name or item.get("SeriesName") or ""


def make_listitem(api, item, playable, standalone=False):
    label = _label(item, standalone)
    li = xbmcgui.ListItem(label=label)
    li.setArt(_art(api, item))

    tag = li.getVideoInfoTag()
    media_type = MEDIA_TYPES.get(item.get("Type"), ("video", "videos"))[0]
    tag.setMediaType(media_type)
    # Estuary's grid views print the info tag's title, not the list item label,
    # so setting the raw server name here would undo the labelling above and
    # show a bare "第 5 集" in Resume and Next up.
    tag.setTitle(label)
    if item.get("OriginalTitle"):
        tag.setOriginalTitle(item["OriginalTitle"])
    if item.get("Overview"):
        tag.setPlot(item["Overview"])
    if item.get("ProductionYear"):
        tag.setYear(int(item["ProductionYear"]))
    if item.get("Genres"):
        tag.setGenres(list(item["Genres"]))
    if item.get("Studios"):
        tag.setStudios([s.get("Name", "") for s in item["Studios"]])
    if item.get("OfficialRating"):
        tag.setMpaa(item["OfficialRating"])
    if item.get("CommunityRating"):
        tag.setRating(float(item["CommunityRating"]))
    if item.get("Taglines"):
        tag.setTagLine(item["Taglines"][0])
    if item.get("PremiereDate"):
        tag.setPremiered(item["PremiereDate"][:10])
    duration = _seconds(item.get("RunTimeTicks"))
    if duration:
        tag.setDuration(duration)
    if item.get("IndexNumber") is not None and item.get("Type") in ("Episode", "Season"):
        if item.get("Type") == "Episode":
            tag.setEpisode(int(item["IndexNumber"]))
            if item.get("ParentIndexNumber") is not None:
                tag.setSeason(int(item["ParentIndexNumber"]))
        else:
            tag.setSeason(int(item["IndexNumber"]))
    if item.get("SeriesName"):
        tag.setTvShowTitle(item["SeriesName"])

    people = item.get("People") or []
    cast = [xbmc.Actor(p.get("Name", ""), p.get("Role", "") or "", i,
                       api.image_url(p["Id"], "Primary", p.get("PrimaryImageTag"))
                       if p.get("PrimaryImageTag") and p.get("Id") else "")
            for i, p in enumerate(people) if p.get("Type") == "Actor"]
    if cast:
        tag.setCast(cast)
    directors = [p.get("Name", "") for p in people if p.get("Type") == "Director"]
    if directors:
        tag.setDirectors(directors)
    writers = [p.get("Name", "") for p in people if p.get("Type") == "Writer"]
    if writers:
        tag.setWriters(writers)

    ids = item.get("ProviderIds") or {}
    if ids:
        try:
            tag.setUniqueIDs({k.lower(): str(v) for k, v in ids.items()},
                             "imdb" if "Imdb" in ids else "tmdb")
        except (AttributeError, TypeError):
            pass  # older Kodi: unique ids are cosmetic here, skip silently

    user = item.get("UserData") or {}
    tag.setPlaycount(int(user.get("PlayCount") or 0))
    if playable:
        li.setProperty("IsPlayable", "true")
        resume = _seconds(user.get("PlaybackPositionTicks"))
        if resume and duration:
            # Kodi 21 warns that the ResumeTime/TotalTime properties are
            # deprecated in favour of this.
            tag.setResumePoint(resume, duration)
    return li


def context_menu(base, item):
    user = item.get("UserData") or {}
    fav = bool(user.get("IsFavorite"))
    played = bool(user.get("Played"))
    return [
        (_ctx_label(30310 if fav else 30311),
         "RunPlugin(%s)" % build_url(base, action="favorite", id=item["Id"],
                                     on="0" if fav else "1")),
        (_ctx_label(30312 if played else 30313),
         "RunPlugin(%s)" % build_url(base, action="played", id=item["Id"],
                                     on="0" if played else "1")),
    ]


_STRINGS = {30310: "Remove from favourites", 30311: "Add to favourites",
            30312: "Mark as unwatched", 30313: "Mark as watched"}


def _ctx_label(sid):
    import xbmcaddon
    text = xbmcaddon.Addon().getLocalizedString(sid)
    return text or _STRINGS[sid]


def add_sort_methods(handle):
    for method in (xbmcplugin.SORT_METHOD_NONE,
                   xbmcplugin.SORT_METHOD_LABEL,
                   xbmcplugin.SORT_METHOD_VIDEO_YEAR,
                   xbmcplugin.SORT_METHOD_VIDEO_RATING,
                   xbmcplugin.SORT_METHOD_DURATION):
        xbmcplugin.addSortMethod(handle, method)
