# -*- coding: utf-8 -*-
"""plugin.video.uhd -- Kodi front-end for a UHD Media Server account."""
import os
import sys
import urllib.parse

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs

from resources.lib.api import ApiError, AuthError, TICKS_PER_SEC
from resources.lib.listing import (add_sort_methods, build_url, context_menu,
                                   make_listitem, MEDIA_TYPES)
from resources.lib import session

ADDON = xbmcaddon.Addon()
BASE = sys.argv[0]
HANDLE = int(sys.argv[1])
ARGS = dict(urllib.parse.parse_qsl(sys.argv[2][1:]))

PLAYABLE_TYPES = ("Movie", "Episode", "Video")
SORT_FIELDS = ("SortName", "DateCreated", "PremiereDate", "CommunityRating",
               "Random")


def L(sid, fallback=""):
    return ADDON.getLocalizedString(sid) or fallback


def page_size():
    return ADDON.getSettingInt("page_size") or 50


def default_sort():
    idx = ADDON.getSettingInt("sort_by")
    field = SORT_FIELDS[idx] if 0 <= idx < len(SORT_FIELDS) else "SortName"
    order = "Descending" if ADDON.getSettingBool("sort_desc") else "Ascending"
    return field, order


def add_dir(label, art=None, **params):
    li = xbmcgui.ListItem(label=label)
    li.setArt(art or {"icon": "DefaultFolder.png"})
    li.setIsFolder(True)
    xbmcplugin.addDirectoryItem(HANDLE, build_url(BASE, **params), li, True)


def finish(content=None, sort=True):
    if content:
        xbmcplugin.setContent(HANDLE, content)
    if sort:
        add_sort_methods(HANDLE)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)
    # The view mode is deliberately NOT set here. Calling Container.SetViewMode
    # from the plugin loses a race: Kodi applies its own remembered view state
    # for the path after the plugin process has already exited, so the first
    # visit to any path still comes up as a plain list. service.py applies it
    # once the container is actually loaded.


def render(api, items, content, playable_override=None, total=None,
           next_params=None, start=0, standalone=False):
    """Render a batch of items, appending a "next page" entry when needed.

    standalone marks lists that mix shows and films (Resume, Next up, Recently
    added, Favourites, Search) so episodes get labelled with their series name.
    """
    entries = []
    for item in items:
        kind = item.get("Type")
        playable = (kind in PLAYABLE_TYPES) if playable_override is None else playable_override
        li = make_listitem(api, item, playable, standalone)
        if playable:
            msid = (item.get("MediaSources") or [{}])[0].get("Id")
            url = build_url(BASE, action="play", id=item["Id"], msid=msid)
        elif kind == "Series":
            url = build_url(BASE, action="seasons", id=item["Id"])
        elif kind == "Season":
            url = build_url(BASE, action="episodes", id=item.get("SeriesId"),
                            season=item["Id"])
        else:
            url = build_url(BASE, action="items", parent=item["Id"])
        li.addContextMenuItems(context_menu(BASE, item))
        entries.append((url, li, not playable))
    xbmcplugin.addDirectoryItems(HANDLE, entries, len(entries))

    if next_params and total is not None:
        nxt = start + page_size()
        if nxt < total:
            add_dir("%s (%d/%d)" % (L(30320, "Next page"), nxt, total),
                    {"icon": "DefaultFolder.png"}, start=nxt, **next_params)
    finish(content)


# --------------------------------------------------------------------- routes
MEDIA = os.path.join(xbmcvfs.translatePath(ADDON.getAddonInfo("path")),
                     "resources", "media")


def tile_art(name):
    """Artwork for a root entry.

    Estuary's grid views print no per-item label, so the name has to live in
    the image -- which is exactly what the server does for its own libraries.
    Estuary picks poster first, then thumb, then icon.
    """
    path = os.path.join(MEDIA, "menu_%s.jpg" % name)
    return {"poster": path, "thumb": path, "icon": path}


def route_root(api):
    add_dir(L(30301, "Resume"), tile_art("resume"), action="resume")
    add_dir(L(30302, "Next up"), tile_art("nextup"), action="nextup")
    add_dir(L(30303, "Recently added"), tile_art("latest"), action="latest")
    add_dir(L(30304, "Favourites"), tile_art("favorites"), action="favorites")
    add_dir(L(30305, "Search"), tile_art("search"), action="search")
    for view in api.views():
        art = {}
        tags = view.get("ImageTags") or {}
        if "Primary" in tags:
            # The server's library art is 16:9 with the name already on it, so
            # it belongs in the same slots as the tiles above.
            url = api.image_url(view["Id"], "Primary", tags["Primary"])
            art = {"poster": url, "thumb": url, "icon": url}
        collection = view.get("CollectionType")
        types = {"movies": "Movie", "tvshows": "Series"}.get(collection)
        add_dir(view.get("Name", ""), art or None, action="items",
                parent=view["Id"], types=types, content=collection)
    # "videos" is what unlocks the big grid views for the home screen: with no
    # content type Estuary only offers the icon wall, whose image slot is a
    # 160x130 box meant for studio logos -- artwork renders postage-stamp size
    # in it. With videos content the wall tiles are 300x301 and the image fills
    # them. The names are baked into the tile images, so the grid views not
    # printing labels does not matter here.
    finish(content="videos", sort=False)


# Sort fields this server actually honours. Verified by checking that ascending
# and descending really reverse the result -- the server silently ignores
# DateLastMediaAdded, CriticRating and OfficialRating, and treats
# DateLastContentAdded as an alias of DateCreated, so none of those are offered.
SORT_OPTIONS = (
    ("SortName", 30401),
    ("DateCreated", 30402),
    ("PremiereDate", 30403),
    ("CommunityRating", 30404),
    ("Runtime", 30405),
    ("DatePlayed", 30406),
    ("PlayCount", 30407),
    ("Random", 30408),
)
SORT_NAMES = dict(SORT_OPTIONS)
SORT_FALLBACK = {30401: "Name", 30402: "Date added", 30403: "Release date",
                 30404: "Rating", 30405: "Duration", 30406: "Last played",
                 30407: "Play count", 30408: "Random"}


def sort_label(field):
    sid = SORT_NAMES.get(field)
    return L(sid, SORT_FALLBACK.get(sid, field)) if sid else field


def resolve_sort(parent):
    """The library's remembered choice, else the addon-wide default.

    Deliberately not read from the URL: a sorted library must stay at the same
    address as the unsorted one, or Back lands on a stale cached copy.
    """
    saved = session.get_sort(parent) if parent else None
    return saved or default_sort()


def route_items(api):
    parent = ARGS.get("parent")
    types = ARGS.get("types") or None
    start = int(ARGS.get("start") or 0)
    sort_by, sort_order = resolve_sort(parent)
    data = api.items(parent_id=parent, types=types, start=start,
                     limit=page_size(), sort_by=sort_by, sort_order=sort_order)
    content = {"movies": "movies", "tvshows": "tvshows"}.get(ARGS.get("content"))
    if start == 0:
        arrow = "↓" if sort_order == "Descending" else "↑"
        add_dir("%s: %s %s" % (L(30400, "Sort"), sort_label(sort_by), arrow),
                None, action="sortmenu", parent=parent, types=types,
                content=ARGS.get("content"))
    render(api, data.get("Items", []), content,
           total=data.get("TotalRecordCount"), start=start,
           next_params={"action": "items", "parent": parent, "types": types,
                        "content": ARGS.get("content")})


def route_sortmenu(api):
    """Pick a sort, then reload the library in place.

    This deliberately does not render a directory. A sort menu as its own
    folder puts the library behind two different URLs -- the plain one and one
    carrying sort parameters -- so Back lands on the menu again, and Kodi's
    cached copy of the plain URL still shows the old order. A dialog plus
    Container.Update(replace) keeps the library at exactly one URL, with the
    chosen sort read from storage.
    """
    parent, types = ARGS.get("parent"), ARGS.get("types") or None
    content = ARGS.get("content")
    current, order = resolve_sort(parent)
    flipped = "Ascending" if order == "Descending" else "Descending"

    labels = ["⇅ " + (L(30411, "Switch to ascending") if flipped == "Ascending"
                      else L(30412, "Switch to descending"))]
    labels += ["%s%s" % ("• " if f == current else "", sort_label(f))
               for f, _ in SORT_OPTIONS]
    choice = xbmcgui.Dialog().select(L(30400, "Sort"), labels)

    # Fail the navigation so Kodi stays on the library instead of pushing an
    # empty container onto the history.
    xbmcplugin.endOfDirectory(HANDLE, succeeded=False, cacheToDisc=False)
    if choice < 0:
        return
    if choice == 0:
        by, new_order = current, flipped
    else:
        by, new_order = SORT_OPTIONS[choice - 1][0], order
    if parent:
        session.set_sort(parent, by, new_order)
    xbmc.executebuiltin("Container.Update(%s,replace)" % build_url(
        BASE, action="items", parent=parent, types=types, content=content))


def route_seasons(api):
    series_id = ARGS["id"]
    seasons = api.seasons(series_id)
    if len(seasons) == 1:
        # Single-season shows: skip the pointless intermediate level.
        return render(api, api.episodes(series_id, seasons[0]["Id"]), "episodes")
    render(api, seasons, "seasons", playable_override=False)


def route_episodes(api):
    render(api, api.episodes(ARGS["id"], ARGS.get("season")), "episodes")


def route_resume(api):
    render(api, api.resume(), "episodes", standalone=True)


def route_nextup(api):
    render(api, api.next_up(), "episodes", standalone=True)


def route_latest(api):
    items = api.latest(limit=page_size())
    render(api, items, "movies", standalone=True)


def route_favorites(api):
    start = int(ARGS.get("start") or 0)
    data = api.favorites(start=start, limit=page_size())
    render(api, data.get("Items", []), "movies", standalone=True,
           total=data.get("TotalRecordCount"), start=start,
           next_params={"action": "favorites"})


def route_search(api):
    term = ARGS.get("q")
    if not term:
        term = xbmcgui.Dialog().input(L(30305, "Search"), type=xbmcgui.INPUT_ALPHANUM)
        if not term:
            return finish(sort=False)
    start = int(ARGS.get("start") or 0)
    data = api.items(types="Movie,Series", search=term, start=start,
                     limit=page_size(), sort_by="SortName")
    render(api, data.get("Items", []), "movies", standalone=True,
           total=data.get("TotalRecordCount"), start=start,
           next_params={"action": "search", "q": term})


def route_play(api):
    item_id = ARGS["id"]
    msid = ARGS.get("msid") or None
    if not msid:
        detail = api.item(item_id) or {}
        msid = (detail.get("MediaSources") or [{}])[0].get("Id")
    url, play_session = api.stream_url(item_id, msid)
    li = xbmcgui.ListItem(path=url)
    li.setContentLookup(False)
    xbmcplugin.setResolvedUrl(HANDLE, True, li)
    # Hand off to service.py, which owns progress reporting for the whole
    # playback; this process is about to exit.
    win = xbmcgui.Window(10000)
    win.setProperty("uhd.playing", "%s|%s|%s" % (item_id, msid or "",
                                                 play_session or ""))


def route_favorite(api):
    api.set_favorite(ARGS["id"], ARGS.get("on") == "1")
    xbmc.executebuiltin("Container.Refresh")


def route_played(api):
    api.set_played(ARGS["id"], ARGS.get("on") == "1")
    xbmc.executebuiltin("Container.Refresh")


ROUTES = {
    None: route_root, "root": route_root, "items": route_items,
    "seasons": route_seasons, "episodes": route_episodes, "resume": route_resume,
    "nextup": route_nextup, "latest": route_latest, "favorites": route_favorites,
    "search": route_search, "sortmenu": route_sortmenu, "play": route_play, "favorite": route_favorite,
    "played": route_played,
}


def main():
    action = ARGS.get("action")
    handler = ROUTES.get(action)
    if handler is None:
        session.log("unknown action %r" % action, xbmc.LOGERROR)
        return xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
    api = session.get_client()
    if api is None:
        if HANDLE >= 0:
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
        return
    try:
        try:
            handler(api)
        except AuthError:
            # The cached token is used without checking it first, so a stale one
            # only surfaces here. Log in again and retry the navigation once.
            session.log("cached token rejected, re-authenticating")
            session.clear_cache()
            api = session.get_client(force_login=True)
            if api is None:
                if HANDLE >= 0:
                    xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
                return
            handler(api)
    except AuthError:
        session.clear_cache()
        session.notify(L(30902, "Login failed"))
        if HANDLE >= 0:
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)
    except ApiError as e:
        session.log("api error: %s" % e, xbmc.LOGERROR)
        session.notify(str(e))
        if HANDLE >= 0:
            xbmcplugin.endOfDirectory(HANDLE, succeeded=False)


if __name__ == "__main__":
    main()
