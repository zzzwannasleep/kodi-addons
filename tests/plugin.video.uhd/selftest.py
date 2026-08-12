# -*- coding: utf-8 -*-
"""Contract check for resources/lib/api.py against a live UHD server.

Reads server / username / password from 1.env (three lines, gitignored) so no
credentials live in the source tree. Run: python tests/plugin.video.uhd/selftest.py
"""
import os
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
# This directory is named after the addon it tests.
ROOT = os.path.dirname(os.path.dirname(HERE))
ADDON_DIR = os.path.join(ROOT, os.path.basename(HERE))
sys.path.insert(0, ADDON_DIR)
from resources.lib.api import UHD, TICKS_PER_SEC  # noqa: E402

ENV = os.path.join(ROOT, "1.env")


def creds():
    with open(ENV, encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    assert len(lines) >= 3, "1.env needs server / username / password on 3 lines"
    return lines[0], lines[1], lines[2]


def main():
    server, user, pw = creds()

    info = UHD.public_info(server)
    assert info.get("ProductName"), "no ProductName in /System/Info/Public"
    print("server      : %s %s" % (info.get("ProductName"), info.get("Version")))

    api = UHD(server, uuid.uuid4().hex, log=lambda m: print("  log:", m))
    token, user_id = api.authenticate(user, pw)
    assert token and user_id
    print("auth        : ok, user_id=%s" % user_id)

    views = api.views()
    assert views, "no libraries returned"
    movie_view = next((v for v in views if v.get("CollectionType") == "movies"), None)
    tv_view = next((v for v in views if v.get("CollectionType") == "tvshows"), None)
    assert movie_view and tv_view, "expected both movie and tvshow libraries"
    print("views       : %d libraries" % len(views))

    page = api.items(parent_id=movie_view["Id"], types="Movie", limit=5)
    movies = page["Items"]
    assert len(movies) == 5 and page["TotalRecordCount"] > 5
    assert movies[0].get("MediaSources"), "MediaSources missing from list query"
    print("items       : %d movies total, first=%s" %
          (page["TotalRecordCount"], movies[0]["Name"]))

    # Paging must actually move the window.
    page2 = api.items(parent_id=movie_view["Id"], types="Movie", start=5, limit=5)
    assert {i["Id"] for i in page2["Items"]}.isdisjoint({i["Id"] for i in movies})
    print("paging      : StartIndex advances the window")

    # Regression guard for the documented server quirk: these filters are
    # ignored. If this ever starts failing, the server gained real filtering
    # and the plugin can offer genre/year browsing.
    unfiltered = api.items(parent_id=movie_view["Id"], types="Movie", limit=1)
    filtered = api._get("/Users/%s/Items" % api.user_id, ParentId=movie_view["Id"],
                        IncludeItemTypes="Movie", Recursive="true", Limit=1,
                        Genres="动画", Years="1900")
    assert filtered["TotalRecordCount"] == unfiltered["TotalRecordCount"], \
        "server now honours Genres/Years filters -- update api.py docs and the UI"
    print("quirk       : Genres/Years still ignored (as documented)")

    found = api.items(types="Movie,Series", search=movies[0]["Name"][:2], limit=3)
    assert found["Items"], "search returned nothing"
    print("search      : %d hits" % found["TotalRecordCount"])

    series = api.items(parent_id=tv_view["Id"], types="Series", limit=1)["Items"][0]
    seasons = api.seasons(series["Id"])
    assert seasons, "no seasons for %s" % series["Name"]
    episodes = api.episodes(series["Id"], seasons[0]["Id"])
    assert episodes and episodes[0].get("MediaSources")
    print("tv          : %s -> %d seasons, %d episodes in S1" %
          (series["Name"], len(seasons), len(episodes)))

    movie = movies[0]
    msid = movie["MediaSources"][0]["Id"]
    url, play_session = api.stream_url(movie["Id"], msid)
    assert url.startswith("http"), url
    print("stream      : %s..." % url.split("?")[0])

    # Progress round-trip: write a position, read it back, then clear it.
    before = (api.item(movie["Id"]).get("UserData") or {}).get("PlaybackPositionTicks", 0)
    probe = 61 * TICKS_PER_SEC
    api.report_start(movie["Id"], msid, 0, play_session)
    api.report_progress(movie["Id"], msid, probe, session=play_session)
    read_back = (api.item(movie["Id"]).get("UserData") or {}).get("PlaybackPositionTicks")
    assert read_back == probe, "progress write-back failed: %r" % read_back
    api.report_stop(movie["Id"], msid, before, play_session)
    print("progress    : write-back verified and restored")

    fav_before = bool((api.item(movie["Id"]).get("UserData") or {}).get("IsFavorite"))
    api.set_favorite(movie["Id"], not fav_before)
    assert bool((api.item(movie["Id"]).get("UserData") or {}).get("IsFavorite")) != fav_before
    api.set_favorite(movie["Id"], fav_before)
    print("favourite   : toggle verified and restored")

    assert api.image_url(movie["Id"], "Primary").startswith(server)
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()
