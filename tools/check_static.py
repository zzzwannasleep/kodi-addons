# -*- coding: utf-8 -*-
"""Checks that need no server account, so CI can run them.

Every addon in the repository is checked. Per-addon test suites (selftest.py,
smoketest.py) need real credentials and cannot run on a build machine; these
rules are static, and each one exists because the matching mistake actually
broke an addon at runtime once.

Run: python tools/check_static.py
"""
import contextlib
import hashlib
import io
import os
import re
import shutil
import sys
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_repo import find_addons, main as build_repo  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDONS = find_addons(ROOT)
LANGS = ("en_gb", "zh_cn")

failures = []


def check(name):
    def wrap(fn):
        try:
            detail = fn()
            print("  ok    %-26s %s" % (name, detail or ""))
        except AssertionError as e:
            failures.append((name, str(e)))
            print("  FAIL  %-26s %s" % (name, e))
        return fn
    return wrap


def rel(path):
    return os.path.relpath(path, ROOT).replace("\\", "/")


def each_addon(fn):
    """Run a per-addon rule over every addon, prefixing failures with its id."""
    notes = []
    for addon in ADDONS:
        try:
            note = fn(addon)
        except AssertionError as e:
            raise AssertionError("%s: %s" % (os.path.basename(addon), e))
        if note:
            notes.append("%s %s" % (os.path.basename(addon), note))
    return ", ".join(notes)


@check("python compiles")
def _compile():
    n = 0
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "repo")]
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(base, f)
                try:
                    compile(open(path, encoding="utf-8").read(), path, "exec")
                except SyntaxError as e:
                    raise AssertionError("%s: %s" % (rel(path), e))
                n += 1
    return "%d files on python %s" % (n, ".".join(map(str, sys.version_info[:2])))


@check("addon.xml")
def _addon_xml():
    assert ADDONS, "no addon directories found"

    def one(addon):
        root = ET.parse(os.path.join(addon, "addon.xml")).getroot()
        assert root.get("id") == os.path.basename(addon), \
            "id %r does not match its directory" % root.get("id")
        version = root.get("version")
        assert re.match(r"^\d+\.\d+\.\d+$", version or ""), "bad version %r" % version
        points = [e.get("point") for e in root.findall("extension")]
        assert "xbmc.addon.metadata" in points, "missing metadata extension point"
        # Kodi lists an addon with no icon as a blank tile.
        assert os.path.exists(os.path.join(addon, "icon.png")), "missing icon.png"
        for e in root.findall("extension"):
            lib = e.get("library")
            if lib:
                assert os.path.exists(os.path.join(addon, lib)), "missing %s" % lib
        return "v%s" % version
    return each_addon(one)


def _settings_root(addon):
    path = os.path.join(addon, "resources", "settings.xml")
    return ET.parse(path).getroot() if os.path.exists(path) else None


@check("settings schema")
def _settings_schema():
    def one(addon):
        root = _settings_root(addon)
        if root is None:
            return ""
        assert root.get("version") == "1", 'root must be <settings version="1">'
        n = 0
        for s in root.iter("setting"):
            sid = s.get("id")
            # A default="..." attribute makes Kodi drop every setting in the file;
            # getSettingInt then raises "Invalid setting type" at runtime.
            assert "default" not in s.attrib, "%s: default must be a child element" % sid
            default = s.find("default")
            assert default is not None, "%s: missing <default>" % sid
            # An empty string default without allowempty fails the same way.
            if s.get("type") == "string" and not (default.text or "").strip():
                allow = s.find("constraints/allowempty")
                assert allow is not None and allow.text == "true", (
                    "%s: empty string default needs <allowempty>true" % sid)
            n += 1
        return "%d settings" % n
    return each_addon(one)


def _po_ids(addon, lang):
    path = os.path.join(addon, "resources", "language",
                        "resource.language.%s" % lang, "strings.po")
    if not os.path.exists(path):
        return None
    return set(re.findall(r'msgctxt "#(\d+)"', open(path, encoding="utf-8").read()))


@check("string ids resolve")
def _strings():
    def one(addon):
        wanted = set()
        root = _settings_root(addon)
        if root is not None:
            for s in root.iter("setting"):
                wanted |= {s.get(a) for a in ("label", "help") if s.get(a)}
                wanted |= {o.get("label") for o in s.iter("option") if o.get("label")}
            for c in root.iter("category"):
                wanted |= {c.get(a) for a in ("label", "help") if c.get(a)}
            for g in root.iter("group"):
                wanted |= {g.get(a) for a in ("label",) if g.get(a)}
        # Ids the python code asks for by number.
        src = ""
        for base, dirs, files in os.walk(addon):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if f.endswith(".py"):
                    src += open(os.path.join(base, f), encoding="utf-8").read()
        wanted |= set(re.findall(r"\bL\((\d{5})", src))
        wanted |= set(re.findall(r"getLocalizedString\((\d{5})\)", src))
        if not wanted:
            return ""
        for lang in LANGS:
            ids = _po_ids(addon, lang)
            assert ids is not None, "no %s strings.po" % lang
            missing = sorted(wanted - ids)
            assert not missing, "%s misses %s" % (lang, missing)
        return "%d ids" % len(wanted)
    return each_addon(one)


@check("settings used exist")
def _settings_used():
    def one(addon):
        root = _settings_root(addon)
        declared = {s.get("id") for s in root.iter("setting")} if root is not None else set()
        used = set()
        for base, dirs, files in os.walk(addon):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in files:
                if f.endswith(".py"):
                    text = open(os.path.join(base, f), encoding="utf-8").read()
                    used |= set(re.findall(
                        r'[gs]etSetting(?:Int|Bool)?\(["\']([a-z_]+)["\']', text))
        missing = used - declared
        assert not missing, "code reads undeclared settings: %s" % sorted(missing)
        return "%d declared" % len(declared) if declared else ""
    return each_addon(one)


@check("home tile art")
def _tiles():
    def one(addon):
        main = os.path.join(addon, "main.py")
        if not os.path.exists(main):
            return ""
        names = re.findall(r'tile_art\("(\w+)"\)', open(main, encoding="utf-8").read())
        media = os.path.join(addon, "resources", "media")
        for name in names:
            # Estuary's grid views print no per-item label, so a missing tile
            # shows as a blank square with no way to tell what it is.
            assert os.path.exists(os.path.join(media, "menu_%s.jpg" % name)), \
                "missing menu_%s.jpg" % name
        return "%d tiles" % len(set(names)) if names else ""
    return each_addon(one)


@check("no credentials")
def _no_credentials():
    bad = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "repo")]
        for f in files:
            if not f.endswith((".py", ".xml", ".po", ".md", ".yml", ".yaml")):
                continue
            path = os.path.join(base, f)
            text = open(path, encoding="utf-8", errors="replace").read()
            for m in re.finditer(r'https?://[\w.-]*uhd[\w.-]*|"Pw"\s*:\s*"[^"]+"'
                                 r'|api_key=[A-Za-z0-9]{16,}', text):
                hit = m.group(0)
                if hit.startswith('"Pw"') and '"Pw": password' in text:
                    continue
                bad.append("%s: %s" % (rel(path), hit[:48]))
    assert not bad, "possible credentials or hard-coded server: %s" % bad
    return "clean"


BUILT = None


def built_tree():
    """Build the publishable tree once; the layout checks below all read it."""
    global BUILT
    if BUILT is None:
        out = tempfile.mkdtemp(prefix="kodi-repo-")
        argv, sys.argv = sys.argv, ["make_repo.py", "--out", out]
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                rc = build_repo()
        finally:
            sys.argv = argv
        assert rc == 0, "make_repo.py failed"
        BUILT = out
    return BUILT


@check("zip layout")
def _zip():
    out = built_tree()
    zips = [os.path.join(b, f) for b, _d, fs in os.walk(out)
            for f in fs if f.endswith(".zip")]
    assert len(zips) >= len(ADDONS) + 1, "expected one zip per addon plus the repository"
    for path in zips:
        addon = os.path.basename(os.path.dirname(path))
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
        # Kodi refuses an archive that is not a single directory named after the id.
        assert all(n.startswith(addon + "/") for n in names), \
            "%s: bad top-level layout" % addon
        assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")], \
            "%s: compiled artefacts in the zip" % addon
    # Kodi rejects the index if the checksum does not match the bytes on disk,
    # which is what a CRLF-translating text write silently causes.
    want = open(os.path.join(out, "addons.xml.md5"), encoding="utf-8").read().strip()
    got = hashlib.md5(open(os.path.join(out, "addons.xml"), "rb").read()).hexdigest()
    assert got == want, "addons.xml.md5 does not match addons.xml"
    return "%d zips" % len(zips)


# Kodi's HTTPDirectory scrapes links with this and then drops every entry whose
# text differs from its href, so a prettified index silently lists nothing.
KODI_LINK = re.compile(r'<a href="([^"]*)"[^>]*>\s*(.*?)\s*</a>')


def kodi_entries(path):
    html = open(path, encoding="utf-8").read()
    return {href for href, text in KODI_LINK.findall(html)
            if urllib.parse.unquote(href) == text}


@check("browsable in kodi")
def _browsable():
    out = built_tree()
    root = os.path.join(out, "index.html")
    assert os.path.exists(root), "no landing page"
    listed = kodi_entries(root)
    n = 0
    for name in sorted(os.listdir(out)):
        full = os.path.join(out, name)
        if not os.path.isdir(full):
            continue
        assert name + "/" in listed, "%s is not reachable from the landing page" % name
        index = os.path.join(full, "index.html")
        assert os.path.exists(index), "%s: no directory index" % name
        entries = kodi_entries(index)
        for zip_name in (f for f in os.listdir(full) if f.endswith(".zip")):
            assert zip_name in entries, "%s: %s not listed" % (name, zip_name)
            n += 1
    return "%d zips reachable as a file source" % n


if __name__ == "__main__":
    print("static checks (no server account needed)")
    if BUILT:
        shutil.rmtree(BUILT, ignore_errors=True)
    if failures:
        print("\n%d check(s) failed" % len(failures))
        sys.exit(1)
    print("\nall static checks passed")
