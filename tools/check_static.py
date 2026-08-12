# -*- coding: utf-8 -*-
"""Checks that need no server account, so CI can run them.

selftest.py and smoketest.py both talk to a real UHD account and cannot run on
a build machine. Everything here is static, and every rule exists because the
matching mistake actually broke the addon at runtime once.

Run: python tools/check_static.py
"""
import os

import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ADDON_ID = "plugin.video.uhd"
ADDON = os.path.join(ROOT, ADDON_ID)
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


@check("python compiles")
def _compile():
    n = 0
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(base, f)
                try:
                    compile(open(path, encoding="utf-8").read(), path, "exec")
                except SyntaxError as e:
                    raise AssertionError("%s: %s" % (os.path.relpath(path, ROOT), e))
                n += 1
    return "%d files on python %s" % (n, ".".join(map(str, sys.version_info[:2])))


@check("addon.xml")
def _addon_xml():
    root = ET.parse(os.path.join(ADDON, "addon.xml")).getroot()
    assert root.get("id") == ADDON_ID, root.get("id")
    version = root.get("version")
    assert re.match(r"^\d+\.\d+\.\d+$", version or ""), "bad version %r" % version
    points = [e.get("point") for e in root.findall("extension")]
    for needed in ("xbmc.python.pluginsource", "xbmc.service", "xbmc.addon.metadata"):
        assert needed in points, "missing extension point %s" % needed
    for entry in ("main.py", "service.py", "icon.png", "fanart.jpg"):
        assert os.path.exists(os.path.join(ADDON, entry)), "missing %s" % entry
    return "v%s" % version


def _settings_root():
    return ET.parse(os.path.join(ADDON, "resources", "settings.xml")).getroot()


@check("settings schema")
def _settings_schema():
    root = _settings_root()
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


def _po_ids(lang):
    path = os.path.join(ADDON, "resources", "language",
                        "resource.language.%s" % lang, "strings.po")
    return set(re.findall(r'msgctxt "#(\d+)"', open(path, encoding="utf-8").read()))


@check("string ids resolve")
def _strings():
    root = _settings_root()
    wanted = set()
    for s in root.iter("setting"):
        wanted |= {s.get(a) for a in ("label", "help") if s.get(a)}
        wanted |= {o.get("label") for o in s.iter("option") if o.get("label")}
    for c in root.iter("category"):
        wanted |= {c.get(a) for a in ("label", "help") if c.get(a)}
    for g in root.iter("group"):
        wanted |= {g.get(a) for a in ("label",) if g.get(a)}
    # Ids the python code asks for by number.
    src = ""
    for base, _dirs, files in os.walk(ADDON):
        for f in files:
            if f.endswith(".py"):
                src += open(os.path.join(base, f), encoding="utf-8").read()
    wanted |= set(re.findall(r"\bL\((\d{5})", src))
    wanted |= set(re.findall(r"getLocalizedString\((\d{5})\)", src))
    for lang in LANGS:
        missing = sorted(wanted - _po_ids(lang))
        assert not missing, "%s misses %s" % (lang, missing)
    return "%d ids in %s" % (len(wanted), "/".join(LANGS))


@check("settings used exist")
def _settings_used():
    declared = {s.get("id") for s in _settings_root().iter("setting")}
    used = set()
    for base, dirs, files in os.walk(ADDON):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                text = open(os.path.join(base, f), encoding="utf-8").read()
                used |= set(re.findall(
                    r'[gs]etSetting(?:Int|Bool)?\(["\']([a-z_]+)["\']', text))
    missing = used - declared
    assert not missing, "code reads undeclared settings: %s" % sorted(missing)
    return "%d declared, %d read" % (len(declared), len(used))


@check("home tile art")
def _tiles():
    main = open(os.path.join(ADDON, "main.py"), encoding="utf-8").read()
    names = re.findall(r'tile_art\("(\w+)"\)', main)
    assert names, "no tile_art calls found in main.py"
    media = os.path.join(ADDON, "resources", "media")
    for name in names:
        path = os.path.join(media, "menu_%s.jpg" % name)
        # Estuary's grid views print no per-item label, so a missing tile shows
        # as a blank square with no way to tell what it is.
        assert os.path.exists(path), "missing %s" % os.path.basename(path)
    return "%d tiles" % len(set(names))


@check("no credentials")
def _no_credentials():
    bad = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git")]
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
                bad.append("%s: %s" % (os.path.relpath(path, ROOT), hit[:48]))
    assert not bad, "possible credentials or hard-coded server: %s" % bad
    return "clean"


@check("zip layout")
def _zip():
    out = subprocess.run([sys.executable, os.path.join(ROOT, "package.py")],
                         capture_output=True, text=True, cwd=ROOT)
    assert out.returncode == 0, out.stderr.strip()
    version = ET.parse(os.path.join(ADDON, "addon.xml")).getroot().get("version")
    path = os.path.join(ROOT, "%s-%s.zip" % (ADDON_ID, version))
    assert os.path.exists(path), "package.py did not produce %s" % path
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
    # Kodi refuses an archive that is not a single directory named after the id.
    assert all(n.startswith(ADDON_ID + "/") for n in names), "bad top-level layout"
    assert not [n for n in names if "__pycache__" in n or n.endswith(".pyc")], \
        "compiled artefacts in the zip"
    return "%d files" % len(names)


if __name__ == "__main__":
    print("static checks (no server account needed)")
    if failures:
        print("\n%d check(s) failed" % len(failures))
        sys.exit(1)
    print("\nall static checks passed")
