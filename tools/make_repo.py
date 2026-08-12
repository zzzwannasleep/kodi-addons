# -*- coding: utf-8 -*-
"""Build the Kodi repository tree that gives in-Kodi automatic updates.

A zip attached to a GitHub Release can only be installed by hand. For Kodi to
offer updates on its own it needs a *repository* addon pointing at an index it
can poll, so this builds both:

    <out>/addons.xml                 index of every addon served here
    <out>/addons.xml.md5             checksum Kodi polls to detect changes
    <out>/<addon.id>/<addon.id>-<ver>.zip
    <out>/repository.kodiaddons/...  install this one once

Every top-level directory holding an addon.xml is picked up, so adding a new
addon to this repository means dropping it in and pushing -- no list to edit.

Older zips already in <out> are left alone, so previous versions stay
installable and the index keeps working while a release propagates.

Run: python tools/make_repo.py [--out repo] [--base-url https://...]
"""
import argparse
import hashlib
import os
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ID = "repository.kodiaddons"
# The repository addon's own version, deliberately independent of any plugin's.
# Tying them together made every plugin release bump the repository too, so Kodi
# spent one update cycle upgrading the repository and only offered the plugin on
# the next one. Bump this only when the URLs below change.
REPO_VERSION = "2.0.0"
DEFAULT_BASE = "https://zzzwannasleep.github.io/kodi-addons/"
SKIP_DIRS = {"__pycache__", ".git"}
SKIP_SUFFIX = (".pyc", ".pyo")


def find_addons(root=ROOT):
    """Any top-level directory with an addon.xml is an addon we publish."""
    return sorted(os.path.join(root, d) for d in os.listdir(root)
                  if os.path.isfile(os.path.join(root, d, "addon.xml")))


def addon_id(addon_dir):
    return ET.parse(os.path.join(addon_dir, "addon.xml")).getroot().get("id")


def addon_version(addon_dir):
    return ET.parse(os.path.join(addon_dir, "addon.xml")).getroot().get("version")


def zip_addon(addon_dir, out_dir, addon_name=None, version=None):
    """One directory named after the addon id at the top, as Kodi requires."""
    addon_name = addon_name or addon_id(addon_dir)
    version = version or addon_version(addon_dir)
    target = os.path.join(out_dir, addon_name)
    os.makedirs(target, exist_ok=True)
    path = os.path.join(target, "%s-%s.zip" % (addon_name, version))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for base, dirs, files in os.walk(addon_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if name.endswith(SKIP_SUFFIX):
                    continue
                full = os.path.join(base, name)
                z.write(full, os.path.join(addon_name,
                                           os.path.relpath(full, addon_dir)))
    # Kodi shows these while browsing the repository, before anything is installed.
    for extra in ("addon.xml", "icon.png", "fanart.jpg"):
        src = os.path.join(addon_dir, extra)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(target, extra))
    return path


def build_repository_addon(work_dir, base_url, version, icon=None):
    """Generate the repository addon rather than hand-maintaining a copy of
    the URLs in two places."""
    src = os.path.join(work_dir, REPO_ID)
    os.makedirs(src, exist_ok=True)
    addon_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<addon id="{id}" name="Kodi Addons" version="{ver}" provider-name="zzzwannasleep">
  <extension point="xbmc.addon.repository" name="Kodi Addons">
    <dir>
      <info compressed="false">{base}addons.xml</info>
      <checksum>{base}addons.xml.md5</checksum>
      <datadir zip="true">{base}</datadir>
    </dir>
  </extension>
  <extension point="xbmc.addon.metadata">
    <summary lang="en_GB">Update source for these Kodi addons</summary>
    <summary lang="zh_CN">这些 Kodi 插件的更新源</summary>
    <description lang="en_GB">Install once; Kodi then offers updates to every addon published here automatically.</description>
    <description lang="zh_CN">安装一次即可，之后这里发布的每个插件都由 Kodi 自动提示更新。</description>
    <platform>all</platform>
    <license>GPL-3.0-or-later</license>
    <assets>
      <icon>icon.png</icon>
    </assets>
  </extension>
</addon>
""".format(id=REPO_ID, ver=version, base=base_url)
    with open(os.path.join(src, "addon.xml"), "w", encoding="utf-8") as f:
        f.write(addon_xml)
    if icon and os.path.exists(icon):
        shutil.copy2(icon, os.path.join(src, "icon.png"))
    return src


def write_index(out_dir, addon_dirs):
    root = ET.Element("addons")
    for d in addon_dirs:
        root.append(ET.parse(os.path.join(d, "addon.xml")).getroot())
    body = ET.tostring(root, encoding="unicode")
    xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + body + "\n"
    # newline="" keeps the bytes on disk identical to what is hashed below;
    # Windows would otherwise write CRLF and every checksum would mismatch.
    with open(os.path.join(out_dir, "addons.xml"), "w", encoding="utf-8",
              newline="") as f:
        f.write(xml)
    digest = hashlib.md5(xml.encode("utf-8")).hexdigest()
    with open(os.path.join(out_dir, "addons.xml.md5"), "w", encoding="utf-8") as f:
        f.write(digest)
    return digest


def write_landing(out_dir, base, addons):
    """Pages serves a bare directory otherwise; say what the URL is for."""
    rows = "\n".join(
        '<tr><td>{name}</td><td>{ver}</td>'
        '<td><a href="{id}/{id}-{ver}.zip">{id}-{ver}.zip</a></td></tr>'.format(
            id=a["id"], ver=a["version"], name=a["name"]) for a in addons)
    html = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<title>Kodi 插件库</title>
<style>body{{font:16px/1.7 system-ui,sans-serif;max-width:44rem;margin:3rem auto;padding:0 1rem}}
code{{background:#f2f2f2;padding:.15em .4em;border-radius:3px}}
table{{border-collapse:collapse;width:100%}}
td,th{{border-bottom:1px solid #ddd;padding:.4rem .6rem;text-align:left}}</style>
<h1>Kodi 插件库</h1>
<h2>自动更新（推荐）</h2>
<ol>
<li>下载 <a href="{repoid}/{repoid}-{repover}.zip">{repoid}-{repover}.zip</a></li>
<li>Kodi → 插件 → 从 zip 文件安装 → 选中它</li>
<li>之后在「从存储库安装」里装插件，日后更新由 Kodi 自动提示</li>
</ol>
<h2>直接下载</h2>
<table><tr><th>插件</th><th>版本</th><th>zip</th></tr>
{rows}
</table>
<p>手动装的插件不会自动更新，升级要自己重装。插件里的地址与账号在各自的设置中填写，本仓库不含任何地址或凭据。</p>
<p><code>{base}</code></p>
</html>
""".format(base=base, rows=rows, repoid=REPO_ID, repover=REPO_VERSION)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(ROOT, "repo"))
    ap.add_argument("--base-url", default=DEFAULT_BASE)
    args = ap.parse_args()
    base = args.base_url if args.base_url.endswith("/") else args.base_url + "/"
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)

    addons = []
    for d in find_addons():
        meta = ET.parse(os.path.join(d, "addon.xml")).getroot()
        addons.append({"dir": d, "id": meta.get("id"),
                       "version": meta.get("version"), "name": meta.get("name")})
        zip_addon(d, out, meta.get("id"), meta.get("version"))

    work = os.path.join(out, ".build")
    icon = os.path.join(addons[0]["dir"], "icon.png") if addons else None
    repo_src = build_repository_addon(work, base, REPO_VERSION, icon)
    zip_addon(repo_src, out, REPO_ID, REPO_VERSION)
    shutil.rmtree(work, ignore_errors=True)

    digest = write_index(out, [a["dir"] for a in addons] +
                         [os.path.join(out, REPO_ID)])
    write_landing(out, base, addons)
    print("repository built in %s" % out)
    print("  base url  %s" % base)
    print("  md5       %s" % digest)
    for a in addons:
        print("  addon     %s %s" % (a["id"], a["version"]))
    print("  addon     %s %s" % (REPO_ID, REPO_VERSION))
    return 0


if __name__ == "__main__":
    sys.exit(main())
