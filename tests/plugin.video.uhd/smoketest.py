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
            "device_id": "smoketest"}


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
    with open(os.path.join(ROOT, "1.env"), encoding="utf-8") as f:
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
    q = next(v for v in views if v.get("content") == "movies")
    tv = next(v for v in views if v.get("content") == "tvshows")
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
    assert any("action=items" in u and "start=" in u for u, _, _ in r["dirs"]), \
        "next-page entry missing"
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
    print("play        : resolved %s..." % li.path.split("?")[0])

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
            # Everything in Resume is partly watched, so the resume point must
            # reach Kodi -- via the InfoTag, not the deprecated properties.
            marked = [li for _, li, _ in r["dirs"]
                      if li.tag.calls.get("setResumePoint")]
            assert marked, "no item in Resume carries setResumePoint"
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
    assert page2["dirs"], "page two empty"
    assert "action=sortmenu" not in page2["dirs"][0][0], (
        "the sort row should only head the first page")
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


if __name__ == "__main__":
    main()
