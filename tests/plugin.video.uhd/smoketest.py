# -*- coding: utf-8 -*-
"""Runs the Kodi-facing layer (main.py / listing.py) against real server data.

Kodi itself is not importable outside Kodi, so the xbmc* modules are stubbed.
This does not prove the Kodi API names are right -- it proves this addon's own
routing, URL building and metadata mapping do not blow up on real payloads.
Run: python tests/plugin.video.uhd/smoketest.py
"""
import os
import sys
import tempfile
import types
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
# This directory is named after the addon it tests.
ROOT = os.path.dirname(os.path.dirname(HERE))
ADDON_DIR = os.path.join(ROOT, os.path.basename(HERE))

CALLS = {"dirs": [], "content": [], "sort": [], "resolved": [], "props": {},
         "labels": {}}
SETTINGS = {"page_size": "50", "sort_by": "0", "sort_desc": "false",
            "view_mode": "54", "root_view_mode": "54", "timeout": "20", "verify_ssl": "true",
            "device_id": "smoketest", "ask_version": "true"}

# Which server to run against. The addon talks to two implementations that
# disagree about parts of the API, so both need to pass this file.
ENV_FILE = "1.env"


# ------------------------------------------------------------------- stubs
class _Tag:
    """Records InfoTag setter calls so we can assert the mapping ran."""

    def __init__(self):
        self.calls = {}

    def __getattr__(self, name):
        if not name.startswith("set"):
            raise AttributeError(name)
        return lambda *a: self.calls.__setitem__(name, a)


class _ListItem:
    def __init__(self, label="", path=""):
        self.label = label
        self.path = path
        self.art = {}
        self.props = {}
        self.ctx = []
        self.tag = _Tag()
        self.is_folder = False

    def setArt(self, art):
        self.art = art

    def setProperty(self, k, v):
        assert isinstance(v, str), "setProperty value must be str: %r" % (v,)
        self.props[k] = v

    def setIsFolder(self, v):
        self.is_folder = v

    def setPath(self, p):
        self.path = p

    def setContentLookup(self, v):
        pass

    def addContextMenuItems(self, items):
        self.ctx = items

    def getVideoInfoTag(self):
        return self.tag


def _install_stubs():
    xbmc = types.ModuleType("xbmc")
    xbmc.LOGINFO = 1
    xbmc.LOGWARNING = 2
    xbmc.LOGERROR = 3
    xbmc.log = lambda msg, level=1: None
    xbmc.executebuiltin = lambda cmd: CALLS.setdefault("builtin", []).append(cmd)
    xbmc.getCondVisibility = lambda cond: False
    xbmc.getInfoLabel = lambda name: CALLS["labels"].get(name, "")

    class Actor:
        def __init__(self, name="", role="", order=0, thumbnail=""):
            self.name, self.role = name, role

    class Monitor:
        def abortRequested(self):
            return True

        def waitForAbort(self, t=0):
            return True

    class Player:
        def isPlayingVideo(self):
            return False

        def getTime(self):
            raise RuntimeError("not playing")

    xbmc.Actor, xbmc.Monitor, xbmc.Player = Actor, Monitor, Player

    xbmcgui = types.ModuleType("xbmcgui")
    xbmcgui.ListItem = _ListItem
    xbmcgui.INPUT_ALPHANUM = 0
    xbmcgui.NOTIFICATION_WARNING = "warning"

    class _Window:
        def __init__(self, window_id=0):
            pass

        def setProperty(self, k, v):
            CALLS["props"][k] = v

        def getProperty(self, k):
            return CALLS["props"].get(k, "")

        def clearProperty(self, k):
            CALLS["props"].pop(k, None)

    class _Dialog:
        def notification(self, *a, **kw):
            pass

        def input(self, *a, **kw):
            return "test"

        def select(self, heading, items, **kw):
            CALLS["select"] = items
            return CALLS.get("select_index", -1)

    xbmcgui.Window, xbmcgui.Dialog = _Window, _Dialog

    xbmcplugin = types.ModuleType("xbmcplugin")
    for i, name in enumerate(("SORT_METHOD_NONE", "SORT_METHOD_LABEL",
                              "SORT_METHOD_VIDEO_YEAR", "SORT_METHOD_VIDEO_RATING",
                              "SORT_METHOD_DURATION")):
        setattr(xbmcplugin, name, i)
    xbmcplugin.addDirectoryItem = lambda h, url, li, folder=False: CALLS["dirs"].append((url, li, folder))
    xbmcplugin.addDirectoryItems = lambda h, items, total=0: CALLS["dirs"].extend(items)
    xbmcplugin.endOfDirectory = lambda h, succeeded=True, **kw: CALLS.setdefault(
        "end", []).append((succeeded, kw))
    xbmcplugin.setContent = lambda h, c: CALLS["content"].append(c)
    xbmcplugin.addSortMethod = lambda h, m: CALLS["sort"].append(m)
    xbmcplugin.setResolvedUrl = lambda h, ok, li: CALLS["resolved"].append((ok, li))

    xbmcaddon = types.ModuleType("xbmcaddon")

    class _Addon:
        def getAddonInfo(self, key):
            return {"id": "plugin.video.uhd", "name": "UHD",
                    "path": ADDON_DIR,
                    "profile": os.path.join(tempfile.gettempdir(), "uhd-smoketest")}[key]

        def getSetting(self, k):
            return SETTINGS.get(k, "")

        def getSettingInt(self, k):
            return int(SETTINGS.get(k) or 0)

        def getSettingBool(self, k):
            return SETTINGS.get(k) == "true"

        def setSetting(self, k, v):
            SETTINGS[k] = v

        def getLocalizedString(self, sid):
            return ""

        def openSettings(self):
            pass

    xbmcaddon.Addon = _Addon

    xbmcvfs = types.ModuleType("xbmcvfs")
    xbmcvfs.translatePath = lambda p: p

    for mod in (xbmc, xbmcgui, xbmcplugin, xbmcaddon, xbmcvfs):
        sys.modules[mod.__name__] = mod


def _load_credentials():
    with open(os.path.join(ROOT, ENV_FILE), encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]
    SETTINGS["server"], SETTINGS["username"], SETTINGS["password"] = lines[:3]


def _pick(files, pred, what):
    """next() with a message: a bare StopIteration hides transient API errors."""
    hit = next((f for f in files if pred(f)), None)
    assert hit is not None, (
        "no %s in %d rendered entries -- usually a transient API timeout "
        "swallowed into an empty listing; rerun" % (what, len(files)))
    return hit


def _run(action=None, **params):
    """Import main.py fresh with a given plugin URL, then dispatch."""
    for name in [n for n in sys.modules if n.startswith(("main", "resources"))]:
        del sys.modules[name]
    query = urllib.parse.urlencode({k: v for k, v in
                                    dict(action=action, **params).items() if v})
    sys.argv = ["plugin://plugin.video.uhd/", "1", "?" + query]
    for key in ("dirs", "content", "sort", "resolved", "builtin"):
        CALLS[key] = []
    import main
    main.main()
    return CALLS


def _check_settings_schema():
    """Guard the settings.xml schema Kodi actually enforces.

    Both rules below were learned from Kodi rejecting the file at runtime:
    a default="..." attribute (instead of a <default> child) and an empty
    string default without <allowempty> each make Kodi drop every setting in
    the file, after which getSettingInt/Bool raise "Invalid setting type".
    Stubs cannot see any of that, so check the XML itself.
    """
    import re
    import xml.etree.ElementTree as ET

    path = os.path.join(ADDON_DIR, "resources", "settings.xml")
    root = ET.parse(path).getroot()
    assert root.get("version") == "1", "settings root must be <settings version=\"1\">"
    declared = set()
    for s in root.iter("setting"):
        sid = s.get("id")
        declared.add(sid)
        assert "default" not in s.attrib, \
            "%s: default must be a <default> child element, not an attribute" % sid
        default = s.find("default")
        assert default is not None, "%s: missing <default> element" % sid
        if s.get("type") == "string" and not (default.text or "").strip():
            allow = s.find("constraints/allowempty")
            assert allow is not None and allow.text == "true", \
                "%s: empty string default needs <constraints><allowempty>true" % sid

    used = set()
    for name in ("session.py", "listing.py"):
        src = open(os.path.join(ADDON_DIR, "resources", "lib", name), encoding="utf-8").read()
        used |= set(re.findall(r'[gs]etSetting(?:Int|Bool)?\(["\']([a-z_]+)["\']', src))
    src = open(os.path.join(ADDON_DIR, "main.py"), encoding="utf-8").read()
    used |= set(re.findall(r'[gs]etSetting(?:Int|Bool)?\(["\']([a-z_]+)["\']', src))
    missing = used - declared
    assert not missing, "code reads settings not declared in settings.xml: %s" % missing
    print("settings    : %d declared, %d read by code, schema ok"
          % (len(declared), len(used)))


def main():
    _install_stubs()
    _load_credentials()
    sys.path.insert(0, ADDON_DIR)
    _check_settings_schema()

    r = _run(None)
    assert r["content"] == ["videos"], (
        "home screen must declare a content type, otherwise Estuary offers "
        "only the icon wall and renders artwork in a 160x130 box: %r"
        % (r["content"],))
    labels = [li.label for _, li, _ in r["dirs"]]
    assert len(labels) >= 5 + 12, "root menu too short: %r" % labels
    blank = [li.label for _, li, _ in r["dirs"]
             if not li.art.get("poster") or li.art.get("poster").endswith(".png")]
    assert not blank, ("home entries without tile art would render as blank "
                       "squares in the icon wall: %r" % blank)
    import os as _os
    for _, li, _ in r["dirs"][:5]:
        assert _os.path.exists(li.art["poster"]),             "missing tile image: %s" % li.art["poster"]
    assert all(u.startswith("plugin://") for u, _, _ in r["dirs"])
    print("root        : %d entries" % len(labels))

    views = [dict(urllib.parse.parse_qsl(u.split("?", 1)[1]))
             for u, _, _ in r["dirs"] if "action=items" in u]
    # A server may expose many libraries and the first one of a kind can be
    # empty, so pick ones that actually have something in them.
    def _first_with_items(kind):
        """Prefer a library big enough to page, so paging gets exercised."""
        candidates = [v for v in views if v.get("content") == kind]
        assert candidates, "no %s library on this server" % kind
        fallback = None
        for view in candidates:
            got = _run("items", parent=view["parent"], types=view.get("types"),
                       content=view.get("content"))
            rendered = [li for _, li, folder in got["dirs"] if not folder]
            if len(rendered) >= int(SETTINGS["page_size"]):
                return view
            if rendered and fallback is None:
                fallback = view
        return fallback or candidates[0]

    q = _first_with_items("movies")
    tv = _first_with_items("tvshows")
    r = _run("items", parent=q["parent"], types=q.get("types"),
             content=q.get("content"))
    assert r["content"], "setContent was never called"
    playables = [li for _, li, folder in r["dirs"] if not folder]
    assert playables, "no playable items rendered"
    first = playables[0]
    assert first.props.get("IsPlayable") == "true"
    resumed = [li for _, li, folder in r["dirs"]
               if not folder and li.tag.calls.get("setResumePoint")]
    assert first.tag.calls.get("setTitle"), "InfoTag mapping did not run"
    assert first.art.get("poster"), "no poster art built"
    assert first.ctx and len(first.ctx) == 2, "context menu missing"
    # A next-page entry is only owed when the library is longer than one page.
    if len(playables) >= int(SETTINGS["page_size"]):
        assert any("action=items" in u and "start=" in u for u, _, _ in r["dirs"]), \
            "next-page entry missing on a full page"
    print("items       : %d rendered, content=%s, art+infotag ok"
          % (len(r["dirs"]), r["content"][0]))

    _check_sorting(q)

    # A series drills down to seasons or straight to episodes.
    r = _run("items", parent=tv["parent"], types="Series", content="tvshows")
    series_url = _pick(r["dirs"], lambda e: "action=seasons" in e[0], "series entry")[0]
    sid = dict(urllib.parse.parse_qsl(series_url.split("?", 1)[1]))["id"]
    r = _run("seasons", id=sid)
    assert r["content"] and r["content"][0] in ("seasons", "episodes"), (
        "series drill-down rendered nothing (%r) -- usually a transient API "
        "timeout swallowed into an empty listing; rerun" % (r["content"],))
    print("seasons     : content=%s, %d entries" % (r["content"][0], len(r["dirs"])))

    # Play resolves a real URL and hands the ids to the service.
    r = _run("items", parent=q["parent"], types="Movie", content="movies")
    play_url = _pick(r["dirs"], lambda e: not e[2], "playable movie")[0]
    pq = dict(urllib.parse.parse_qsl(play_url.split("?", 1)[1]))
    r = _run("play", id=pq["id"], msid=pq.get("msid"))
    ok, li = r["resolved"][0]
    assert ok and li.path.startswith("http"), li.path
    assert CALLS["props"].get("uhd.playing", "").startswith(pq["id"])
    # Only the path: this output ends up in CI logs, and the host is the one
    # thing that must never be written down.
    print("play        : resolved %s" % urllib.parse.urlsplit(li.path).path)

    # A direct client, for checks whose expectation has to come from the
    # server's own payload rather than from what the addon chose to render.
    from resources.lib import session as _session
    client = _session.get_client()
    assert client is not None, "could not build a client for the server checks"

    for action in ("resume", "nextup", "latest", "favorites"):
        r = _run(action)
        assert r.get("end"), "%s never called endOfDirectory" % action
        if action in ("resume", "nextup") and r["dirs"]:
            # Mixed lists must name the show, not just "第 35 集".
            import re as _re
            named = [li.label for _, li, _ in r["dirs"]
                     if _re.search(r"S\d\dE\d\d$", li.label)]
            assert named, ("episodes in %s are not labelled '<show> S01E08': %r"
                           % (action, [li.label for _, li, _ in r["dirs"]][:4]))
            assert not any(_re.match(r"^第\s*\d+\s*集$", li.label)
                           for _, li, _ in r["dirs"]), "bare episode names remain"
            # Estuary prints the info tag title, not the label, so the two must
            # agree or the grid shows a bare "第 5 集" again.
            for _, li, _ in r["dirs"]:
                title = (li.tag.calls.get("setTitle") or ("",))[0]
                assert title == li.label, (
                    "info tag title %r != label %r; Estuary would print the "
                    "title and lose the show name" % (title, li.label))
            sample = named[0]
        else:
            sample = None
        if action == "resume" and r["dirs"]:
            # Whatever the server says is partly watched must reach Kodi as a
            # resume point -- via the InfoTag, not the deprecated properties.
            # Emby also lists items whose stored position is zero, and those
            # correctly get no resume point, so the expectation comes from the
            # payload rather than from the mere presence of the entry.
            marked = [li for _, li, _ in r["dirs"]
                      if li.tag.calls.get("setResumePoint")]
            with_position = [i for i in (client.resume() or [])
                             if (i.get("UserData") or {}).get("PlaybackPositionTicks")]
            assert len(marked) >= min(1, len(with_position)), (
                "server reports %d partly-watched items but %d carry a resume "
                "point" % (len(with_position), len(marked)))
            assert not any(li.props.get("ResumeTime") for _, li, _ in r["dirs"]),                 "still using the deprecated ResumeTime property"
            print("%-12s: %d entries, %d with resume point, e.g. %s"
                  % (action, len(r["dirs"]), len(marked), sample))
            continue
        print("%-12s: %d entries%s"
              % (action, len(r["dirs"]), ", e.g. %s" % sample if sample else ""))

    r = _run("search", q="三体")
    assert r["dirs"], "search rendered nothing"
    print("search      : %d entries" % len(r["dirs"]))

    _check_service(pq["id"], pq.get("msid"))

    # These need no server, but they guard the two failure modes that made the
    # difference between the two implementations this addon talks to.
    _check_subtitle_choice()
    _check_retry()

    _check_versions(client, q["parent"])
    _check_genres(client, q)

    print("\nALL CHECKS PASSED")



def sort_label_of(field):
    import main
    return main.sort_label(field)


def _check_sorting(view):
    """Sort must be server-side, remembered, and must not fork the library URL.

    An earlier design navigated to "<library>&sort=X". That put the library
    behind two addresses: Back returned to the sort menu, and Kodi served the
    plain address from its disc cache, still in the old order.
    """
    import urllib.parse as _up

    parent = view["parent"]
    # The choice is stored on disk and survives between runs, so start from a
    # known state instead of inheriting the previous run's pick.
    _run("items", parent=parent)          # import the package
    from resources.lib import session as _sess
    _sess.set_sort(parent, "SortName", "Ascending")

    r = _run("items", parent=parent, types=view.get("types"),
             content=view.get("content"))
    sort_url = r["dirs"][0][0]
    assert "action=sortmenu" in sort_url, (
        "library listing has no sort entry: %r" % (sort_url,))
    assert "sort=" not in sort_url and "order=" not in sort_url, (
        "the sort row must not carry the sort in its URL: %r" % (sort_url,))
    assert r["end"][-1][1].get("cacheToDisc") is False, (
        "listings must not be cached to disc, or Back shows the old order: %r"
        % (r["end"][-1],))
    names_before = [li.label for _, li, _ in r["dirs"][1:6]]

    menu = {k: v for k, v in _up.parse_qsl(sort_url.split("?", 1)[1])
            if k != "action"}

    CALLS["select_index"] = -1                      # user cancels
    CALLS["builtin"] = []
    r = _run("sortmenu", **menu)
    assert not r["dirs"], "the sort menu must not render a directory"
    assert r["end"][-1][0] is False, (
        "a cancelled sort must fail the navigation so Kodi stays put: %r"
        % (r["end"][-1],))
    assert not CALLS["builtin"], "cancelling must not reload anything"
    options = CALLS["select"]
    assert len(options) == 9, "expected a direction toggle plus 8 fields: %r" % (options,)
    for bad in ("DateLastMediaAdded", "CriticRating", "OfficialRating"):
        assert not any(bad in o for o in options), bad

    target = sort_label_of("DateCreated")
    CALLS["select_index"] = next(i for i, o in enumerate(options) if o.endswith(target))
    CALLS["builtin"] = []
    _run("sortmenu", **menu)
    update = [c for c in CALLS["builtin"] if c.startswith("Container.Update")]
    assert len(update) == 1, "should reload the library once: %r" % (CALLS["builtin"],)
    assert ",replace)" in update[0], (
        "must replace the container, otherwise Back returns to the sort menu: %r"
        % (update[0],))
    assert "sort=" not in update[0], (
        "the reloaded library must keep its plain URL: %r" % (update[0],))

    r = _run("items", parent=parent, types=view.get("types"),
             content=view.get("content"))
    names_after = [li.label for _, li, _ in r["dirs"][1:6]]
    assert names_after != names_before, (
        "sort had no effect: %r vs %r" % (names_before, names_after))
    assert target in r["dirs"][0][1].label, (
        "sort row does not show the remembered field: %r" % (r["dirs"][0][1].label,))

    # Option 0 flips the direction and must keep the field.
    CALLS["select_index"] = 0
    CALLS["builtin"] = []
    _run("sortmenu", **menu)
    from resources.lib import session as _s2
    got = _s2.get_sort(parent)
    assert got == ("DateCreated", "Descending"), (
        "direction toggle did not flip and keep the field: %r" % (got,))
    r = _run("items", parent=parent, types=view.get("types"),
             content=view.get("content"))
    assert "↓" in r["dirs"][0][1].label, (
        "sort row still shows ascending: %r" % (r["dirs"][0][1].label,))
    CALLS["select_index"] = 0
    _run("sortmenu", **menu)
    assert _s2.get_sort(parent) == ("DateCreated", "Ascending"), "toggle is not symmetric"

    page2 = _run("items", parent=parent, types=view.get("types"),
                 content=view.get("content"), start="50")
    if page2["dirs"]:
        assert "action=sortmenu" not in page2["dirs"][0][0], (
            "the sort row should only head the first page")
    else:
        # A library shorter than one page has no second page to check.
        assert len(r["dirs"]) - 1 < 50, "page two empty on a full library"
    print("sorting     : %d options, one URL, remembered, no disc cache"
          % len(options))



def _check_service(item_id, msid):
    """Drive service.py's reporting loop for a few simulated seconds."""
    import xbmc
    import service

    # Model the real sequence: Kodi needs a few seconds to open the stream, so
    # the player reports "not playing" AFTER the plugin has handed over the
    # ids. Treating that as end-of-playback reported position 0 and abandoned
    # the rest of the movie -- a stub that plays from tick 0 hides the bug.
    OPEN_DELAY = 5
    PLAY_UNTIL = 22
    clock = {"t": 0}
    ticks = {"t": 0.0}

    class FakePlayer:
        def isPlayingVideo(self):
            return OPEN_DELAY <= clock["t"] < PLAY_UNTIL

        def getTime(self):
            return ticks["t"]

    sent = []

    class FakeApi:
        def report_start(self, *a):
            sent.append(("start",) + a)

        def report_progress(self, *a, **kw):
            sent.append(("progress",) + a)

        def report_stop(self, *a):
            sent.append(("stop",) + a)

    fake_api = FakeApi()

    class TestMonitor(service.Reporter):
        def __init__(self):
            super().__init__()
            self.player = FakePlayer()
            self.ticks_left = 30

        def _client(self):
            # The service deliberately drops self.api on each new playback to
            # pick up a refreshed token, so the seam has to be _client().
            return fake_api

        def abortRequested(self):
            return self.ticks_left <= 0

        def waitForAbort(self, timeout=0):
            self.ticks_left -= 1
            clock["t"] += 1
            if OPEN_DELAY <= clock["t"] < PLAY_UNTIL:
                ticks["t"] += 1
            return self.ticks_left <= 0

    # The view applier lives in the same loop; drive it over a root visit and
    # a content visit and check only the content listing gets a wall.
    CALLS["builtin"] = []
    CALLS["labels"]["Container.Viewmode"] = "List"
    CALLS["labels"]["Container.FolderPath"] = "plugin://plugin.video.uhd/"
    mon = TestMonitor()
    mon._apply_view()
    assert CALLS["builtin"] == ["Container.SetViewMode(54)"],         "home screen must get its own view: %r" % (CALLS["builtin"],)
    CALLS["builtin"] = []
    CALLS["labels"]["Container.FolderPath"] = (
        "plugin://plugin.video.uhd/?action=items&parent=x&content=movies")
    mon._apply_view()
    assert CALLS["builtin"] == ["Container.SetViewMode(54)"],         "service did not apply the view to a content listing: %r" % (
            CALLS["builtin"],)
    mon._apply_view()
    assert len(CALLS["builtin"]) == 1,         "view re-applied on every tick, that fights manual changes: %r" % (
            CALLS["builtin"],)

    # A container that has not finished building reports no view mode, and a
    # SetViewMode issued then is silently dropped by Kodi -- the applier has to
    # wait for the next tick instead of burning its single attempt.
    CALLS["builtin"] = []
    CALLS["labels"]["Container.Viewmode"] = ""
    CALLS["labels"]["Container.FolderPath"] = (
        "plugin://plugin.video.uhd/?action=items&parent=y&content=tvshows")
    mon._apply_view()
    assert not CALLS["builtin"],         "applied the view while the container was still loading: %r" % (
            CALLS["builtin"],)
    CALLS["labels"]["Container.Viewmode"] = "List"
    mon._apply_view()
    assert CALLS["builtin"] == ["Container.SetViewMode(54)"],         "did not retry once the container was ready: %r" % (CALLS["builtin"],)

    # The sort menu is a list of text choices, not a picture wall.
    CALLS["builtin"] = []
    CALLS["labels"]["Container.FolderPath"] = (
        "plugin://plugin.video.uhd/?action=sortmenu&parent=y")
    mon._apply_view()
    assert CALLS["builtin"] == ["Container.SetViewMode(55)"], (
        "sort menu should use the wide list, the only list Estuary offers "
        "without a content type: %r" % (CALLS["builtin"],))
    print("view        : home=infowall(54) w/ videos content, listing=infowall(54)")

    CALLS["props"]["uhd.playing"] = "%s|%s|sess1" % (item_id, msid or "")
    TestMonitor().run()

    kinds = [s[0] for s in sent]
    assert kinds[0] == "start", kinds
    assert kinds.count("start") == 1, "start reported more than once: %r" % kinds
    assert "progress" in kinds, "no progress report while playing"
    assert kinds[-1] == "stop", kinds
    assert kinds.count("stop") == 1, "stop reported more than once: %r" % kinds
    stop_ticks = sent[-1][3]
    expected = int(ticks["t"] * 10000000)  # where the fake player actually got to
    assert stop_ticks == expected, (
        "stop reported %.0fs, expected %.0fs -- the service stopped before "
        "Kodi finished opening the stream, so the resume point is lost"
        % (stop_ticks / 10000000, expected / 10000000))
    print("service     : %s -> stop at %.0fs (opened after %ds)"
          % ("/".join(dict.fromkeys(kinds)), stop_ticks / 10000000, OPEN_DELAY))


def _check_subtitle_choice():
    """Only text subtitles may be offered to Kodi as sidecar files.

    Asking the server for a graphic track as text gets an empty 200 back, not
    an error, so a client that trusts the status code attaches a subtitle that
    exists in the menu and never draws a character on screen.
    """
    from resources.lib.api import UHD

    api = UHD("https://example.invalid", "dev", token="t", user_id="u")
    source = {"Id": "src1", "MediaStreams": [
        {"Type": "Video", "Codec": "hevc"},
        {"Type": "Subtitle", "Codec": "PGSSUB", "Index": 2, "IsExternal": False},
        {"Type": "Subtitle", "Codec": "PGSSUB", "Index": 3, "IsExternal": True},
        {"Type": "Subtitle", "Codec": "subrip", "Index": 4, "IsExternal": False},
        {"Type": "Subtitle", "Codec": "subrip", "Index": 5, "IsExternal": True},
        {"Type": "Subtitle", "Codec": "ass", "Index": 6, "IsExternal": False,
         "DeliveryUrl": "/emby/Videos/1/src1/Subtitles/6/Stream.ass"},
    ]}
    urls = api.subtitle_urls("1", source)
    assert len(urls) == 2, "expected the external srt and the delivered ass: %r" % urls
    assert all("api_key=t" in u for u in urls), urls
    assert any("/5/" in u for u in urls), "external text subtitle missing: %r" % urls
    assert any(u.endswith(".ass?api_key=t") or ".ass" in u for u in urls), urls
    for bad in ("/2/", "/3/"):
        assert not any(bad in u for u in urls), (
            "a graphic subtitle was offered as a sidecar: %r" % urls)
    # An embedded text track needs no sidecar either; Kodi reads the container.
    assert not any("/4/" in u for u in urls), (
        "embedded text subtitle should come from the container: %r" % urls)
    assert api.subtitle_urls("1", None) == []
    print("subtitles   : %d sidecars from 5 tracks, graphics excluded" % len(urls))


def _check_retry():
    """A flapping gateway must not surface as a failed navigation.

    Measured against the live server: three 502s in a row, then success. Before
    this, each one became an empty library or a "login failed" toast.
    """
    import urllib.error
    import urllib.request
    from resources.lib import api as api_mod

    attempts = {"n": 0}
    real_urlopen = urllib.request.urlopen
    real_sleep = api_mod.time.sleep

    class _Body:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return self.payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None, context=None):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, None)
        return _Body(b'{"ok": true}')

    urllib.request.urlopen = fake_urlopen
    api_mod.time.sleep = lambda s: None
    try:
        client = api_mod.UHD("https://example.invalid", "dev", token="t", user_id="u")
        assert client._get("/System/Info") == {"ok": True}
        assert attempts["n"] == 3, "expected two retries then success: %r" % attempts

        # A failure that outlives the retries still has to be reported.
        attempts["n"] = 0

        def always_502(req, timeout=None, context=None):
            attempts["n"] += 1
            raise urllib.error.HTTPError(req.full_url, 502, "Bad Gateway", {}, None)

        urllib.request.urlopen = always_502
        try:
            client._get("/System/Info")
            raise AssertionError("a permanently failing endpoint must raise")
        except api_mod.ApiError:
            pass
        assert attempts["n"] == api_mod.RETRIES, (
            "gave up after %d tries, expected %d" % (attempts["n"], api_mod.RETRIES))

        # 401 must not be retried: it means the token is stale, and retrying
        # only delays the re-login.
        attempts["n"] = 0

        def unauthorised(req, timeout=None, context=None):
            attempts["n"] += 1
            raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)

        urllib.request.urlopen = unauthorised
        try:
            client._get("/System/Info")
            raise AssertionError("401 must raise AuthError")
        except api_mod.AuthError:
            pass
        assert attempts["n"] == 1, "401 was retried %d times" % attempts["n"]
    finally:
        urllib.request.urlopen = real_urlopen
        api_mod.time.sleep = real_sleep
    print("retry       : 502 retried %dx, 401 not retried, exhaustion raises"
          % (api_mod.RETRIES - 1))


def _check_versions(api, movie_parent):
    """Multi-version items must be offered, not silently picked for you."""
    import main as main_mod

    multi = None
    data = api.items(parent_id=movie_parent, types="Movie", limit=40)
    for item in data.get("Items", []):
        if len(api.media_sources(item["Id"])) > 1:
            multi = item
            break
    if multi is None:
        print("versions    : no multi-version item in the sample, skipped")
        return

    sources = api.media_sources(multi["Id"])
    labels = [main_mod._source_label(s, i) for i, s in enumerate(sources)]
    assert len(set(labels)) == len(labels) or len(sources) > 8, (
        "version labels must tell the versions apart: %r" % labels[:4])
    assert all(lbl.strip() for lbl in labels), labels

    # Picking the second one must actually play the second one.
    CALLS["select_index"] = 1
    r = _run("play", id=multi["Id"])
    assert CALLS.get("select"), "a multi-version item did not ask"
    ok, li = r["resolved"][0]
    assert ok, "playback was refused after choosing a version"
    handed = CALLS["props"]["uhd.playing"].split("|")
    assert handed[1] == sources[1].get("Id"), (
        "chose version 2 but handed %r to the service" % handed[1])

    # Dismissing the dialog must not open a player.
    CALLS["select_index"] = -1
    r = _run("play", id=multi["Id"])
    assert r["resolved"][-1][0] is False, (
        "a dismissed version dialog still resolved a URL")

    # With the setting off, no question is asked.
    SETTINGS["ask_version"] = "false"
    CALLS["select"] = None
    try:
        r = _run("play", id=multi["Id"])
        assert not CALLS.get("select"), "asked despite ask_version being off"
        assert r["resolved"][-1][0], "silent pick failed to resolve"
    finally:
        SETTINGS["ask_version"] = "true"
    print("versions    : %d sources, dialog picks, cancel aborts, toggle obeyed"
          % len(sources))


def _check_genres(api, view):
    """The genre menu may only appear where the server honours Genres=.

    UHD accepts the parameter and returns the whole library, so a genre list
    there would be a menu of categories that all open the same thing.
    """
    from resources.lib import session as sess

    works = sess.capability("genre_filter", api.genre_filter_works)
    r = _run("items", parent=view["parent"], types=view.get("types"),
             content=view.get("content"))
    offered = [u for u, _, _ in r["dirs"] if "action=genres" in u]
    assert bool(offered) == works, (
        "genre menu offered=%s but the filter works=%s" % (bool(offered), works))
    if not works:
        print("genres      : server ignores Genres=, menu correctly hidden")
        return

    r = _run("genres", types=view.get("types"), content=view.get("content"))
    names = [li.label for _, li, _ in r["dirs"]]
    assert names, "genre menu rendered nothing"
    first = dict(urllib.parse.parse_qsl(r["dirs"][0][0].split("?", 1)[1]))
    r = _run("genre", genre=first["genre"], types=view.get("types"),
             content=view.get("content"))
    assert r["dirs"], "genre %r rendered no items" % first["genre"]
    total = api.by_genre(first["genre"], types=view.get("types"),
                         limit=1).get("TotalRecordCount")
    everything = api.items(types=view.get("types"), limit=1).get("TotalRecordCount")
    assert total != everything, (
        "genre %r returned the whole library (%s); the capability probe lied"
        % (first["genre"], total))
    print("genres      : %d genres, %r narrows to %s of %s"
          % (len(names), first["genre"], total, everything))


if __name__ == "__main__":
    if len(sys.argv) > 1:
        ENV_FILE = sys.argv[1]
    print("server file : %s" % ENV_FILE)
    main()
