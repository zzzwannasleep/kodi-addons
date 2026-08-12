# -*- coding: utf-8 -*-
"""Zip the addon for Kodi's "install from zip" flow.

Kodi requires the archive to contain a single top-level directory named exactly
after the addon id.
"""
import os
import re
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
ADDON_ID = "plugin.video.uhd"
SRC = os.path.join(ROOT, ADDON_ID)
SKIP_DIRS = {"__pycache__"}
SKIP_SUFFIX = (".pyc", ".pyo")


def version():
    with open(os.path.join(SRC, "addon.xml"), encoding="utf-8") as f:
        return re.search(r'version="([^"]+)"\s+name=', f.read()).group(1)


def main():
    out = os.path.join(ROOT, "%s-%s.zip" % (ADDON_ID, version()))
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for base, dirs, files in os.walk(SRC):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if name.endswith(SKIP_SUFFIX):
                    continue
                path = os.path.join(base, name)
                z.write(path, os.path.join(ADDON_ID,
                                           os.path.relpath(path, SRC)))
                count += 1
    print("%s  (%d files, %.1f KB)" % (out, count, os.path.getsize(out) / 1024))


if __name__ == "__main__":
    main()
