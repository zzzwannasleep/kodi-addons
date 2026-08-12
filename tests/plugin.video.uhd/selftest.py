# -*- coding: utf-8 -*-
"""Contract check for resources/lib/api.py against a live server.

The addon talks to two implementations that disagree about parts of the API,
so this file asserts only what the addon actually relies on, and reports the
rest as measurements. An assertion here means "the addon breaks without this";
a printed line means "servers are allowed to differ, and the addon probes it".

Reads server / username / password from an env file (three lines, gitignored)
so no credentials live in the source tree.

  python tests/plugin.video.uhd/selftest.py [1.env]
"""
import os
import time
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
# This directory is named after the addon it tests.
ROOT = os.path.dirname(os.path.dirname(HERE))
ADDON_DIR = os.path.join(ROOT, os.path.basename(HERE))
sys.path.insert(0, ADDON_DIR)
from resources.lib.api import UHD, TICKS_PER_SEC  # noqa: E402


def creds(env_file):
    path = os.path.join(ROOT, env_file)
    with open(path, encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    assert len(lines) >= 3, "%s needs server / username / password on 3 lines" % env_file
    return lines[0], lines[1], lines[2]


def main(env_file="1.env"):
    server, user, pw = creds(env_file)

    info = UHD.public_info(server)
    assert info.get("Version"), "no Version in /System/Info/Public"
    print("server      : %s %s" % (info.get("ProductName") or "Emby-compatible",
                                   info.get("Version")))

    api = UHD(server, uuid.uuid4().hex, log=lambda m: print("  log:", m))
    token, user_id = api.authenticate(user, pw)
    assert token and user_id
    print("auth        : ok")

    views = api.views()
    assert views, "no libraries returned"
    movie_view = next((v for v in views if v.get("CollectionType") == "movies"), None)
    tv_view = next((v for v in views if v.get("CollectionType") == "tvshows"), None)
    assert movie_view and tv_view, "expected both movie and tvshow libraries"
    print("views       : %d libraries" % len(views))

    # Find a library with enough in it to page through.
    page, movies = None, []
    for view in [v for v in views if v.get("CollectionType") == "movies"]:
        page = api.items(parent_id=view["Id"], types="Movie", limit=5)
        if page.get("TotalRecordCount", 0) > 5:
            movie_view, movies = view, page["Items"]
            break
    assert movies, "no movie library with more than 5 items"
    print("items       : %d movies total" % page["TotalRecordCount"])

    page2 = api.items(parent_id=movie_view["Id"], types="Movie", start=5, limit=5)
    assert {i["Id"] for i in page2["Items"]}.isdisjoint({i["Id"] for i in movies}), \
        "StartIndex did not move the window"
    print("paging      : StartIndex advances the window")

    found = api.items(types="Movie,Series", search=movies[0]["Name"][:2], limit=3)
    assert found["Items"], "search returned nothing"
    print("search      : %d hits" % found["TotalRecordCount"])

    series = api.items(parent_id=tv_view["Id"], types="Series", limit=1)["Items"][0]
    seasons = api.seasons(series["Id"])
    assert seasons, "no seasons for the sampled series"
    episodes = api.episodes(series["Id"], seasons[0]["Id"])
    assert episodes, "no episodes in the first season"
    print("tv          : %d seasons, %d episodes in S1" % (len(seasons), len(episodes)))

    # Versions. Emby leaves MediaSources out of list queries whatever Fields
    # asks for, which is why the addon reads them from the item detail.
    movie = movies[0]
    in_list = bool(movie.get("MediaSources"))
    sources = api.media_sources(movie["Id"])
    assert sources, "the item detail carries no MediaSources; playback cannot pick a version"
    most = max((len(api.media_sources(m["Id"])) for m in movies), default=1)
    print("versions    : detail=%d, list query carries them=%s, busiest sample item=%d"
          % (len(sources), in_list, most))

    msid = sources[0]["Id"]
    url, play_session, chosen = api.stream_url(movie["Id"], msid)
    assert url.startswith("http"), url
    assert play_session, "no play session id; Emby rejects reports without one"
    print("stream      : %s" % url.split("?")[0].replace(api.server, ""))

    subs = api.subtitle_urls(movie["Id"], chosen or sources[0])
    tracks = [s for s in (sources[0].get("MediaStreams") or [])
              if s.get("Type") == "Subtitle"]
    print("subtitles   : %d tracks, %d offered to Kodi as sidecars"
          % (len(tracks), len(subs)))

    # The resume contract. Progress reports are not persisted by every server,
    # but the stop report has to be, or resume does not work at all.
    #
    # The position has to sit well inside the film: both servers discard a
    # resume point near the start, and one of them does so inconsistently right
    # around the boundary, so a fixed number of seconds makes this check flap.
    timed = next((m for m in movies if int(m.get("RunTimeTicks") or 0) > 0), None)
    if timed is None:
        print("resume      : no sampled item carries a runtime, check skipped")
    else:
        movie = timed
        sources = api.media_sources(movie["Id"]) or sources
        msid = sources[0]["Id"]
        _, play_session, _ = api.stream_url(movie["Id"], msid)
        _check_resume(api, movie, msid, play_session)

    def favourite():
        return bool((api.item(movie["Id"]).get("UserData") or {}).get("IsFavorite"))

    def await_favourite(want, tries=6):
        for _ in range(tries):
            if favourite() == want:
                return want
            time.sleep(2)
        return favourite()

    fav_before = favourite()
    api.set_favorite(movie["Id"], not fav_before)
    assert await_favourite(not fav_before) != fav_before, "favourite toggle had no effect"
    api.set_favorite(movie["Id"], fav_before)
    assert await_favourite(fav_before) == fav_before, "favourite was left flipped"
    print("favourite   : toggle verified and restored")

    # Whether genre browsing may be offered is a server property, not a
    # constant. The addon probes exactly this and caches the answer.
    works = api.genre_filter_works()
    print("genres      : %d listed, Genres= filter honoured=%s"
          % (len(api.genres(limit=5)), works))

    assert api.image_url(movie["Id"], "Primary").startswith(api.server)
    print("\nALL CHECKS PASSED")


def _position(api, item_id):
    return int((api.item(item_id).get("UserData") or {})
               .get("PlaybackPositionTicks") or 0)


def _await_position(api, item_id, want, tries=6):
    """Poll for a written position.

    Emby does not settle a user-data write immediately. Reading straight back
    returns the previous value, which reads exactly like the server having
    refused the write -- this cost an afternoon of chasing a server bug that
    was a missing wait.
    """
    for _ in range(tries):
        got = _position(api, item_id)
        if got == want:
            return got
        time.sleep(2)
    return _position(api, item_id)


def _check_resume(api, movie, msid, play_session):
    original = _position(api, movie["Id"])
    # The runtime from a list query can disagree with the detail on an item
    # that has several versions, so take it from the detail.
    runtime = int((api.item(movie["Id"]) or {}).get("RunTimeTicks")
                  or movie.get("RunTimeTicks") or 0)
    probe = int(runtime * 0.3)
    if probe == original:
        # Otherwise "the server stored what we sent" is true before we send it,
        # and the progress measurement below reports a success it never saw.
        probe = int(runtime * 0.45)
    assert probe and probe != original, "cannot test resume without a runtime"

    api.report_start(movie["Id"], msid, 0, play_session)
    api.report_progress(movie["Id"], msid, probe, session=play_session)
    time.sleep(3)
    after_progress = _position(api, movie["Id"])

    api.report_stop(movie["Id"], msid, probe, play_session)
    after_stop = _await_position(api, movie["Id"], probe)
    assert after_stop == probe, (
        "the stop report did not write a resume point (%s of %s); resume is "
        "broken on this server" % (after_stop, probe))

    # Put it back. A stop report at the original position is not enough on its
    # own: a position near the start is discarded rather than stored, so
    # restoring a zero that way silently leaves the probe's value behind.
    api.report_stop(movie["Id"], msid, original, play_session)
    restored = _await_position(api, movie["Id"], original, tries=2)
    for _ in range(3):
        if restored == original:
            break
        api.set_position(movie["Id"], original)
        restored = _await_position(api, movie["Id"], original, tries=3)
    assert restored == original, (
        "this check left a resume point behind: %s, was %s" % (restored, original))
    print("resume      : progress persists=%s, stop persists=yes, restored to %s"
          % (after_progress == probe, original))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "1.env")
