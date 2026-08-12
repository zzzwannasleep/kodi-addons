# -*- coding: utf-8 -*-
"""Build the Kodi repository tree that gives in-Kodi automatic updates.

A zip attached to a GitHub Release can only be installed by hand. For Kodi to
offer updates on its own it needs a *repository* addon pointing at an index it
can poll, so this builds both:

    <out>/addons.xml                 index of every addon served here
    <out>/addons.xml.md5             checksum Kodi polls to detect changes
    <out>/plugin.video.uhd/plugin.video.uhd-<ver>.zip
    <out>/repository.uhd/repository.uhd-<ver>.zip   install this one once

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
PLUGIN_ID = "plugin.video.uhd"
REPO_ID = "repository.uhd"
DEFAULT_BASE = "https://zzzwannasleep.github.io/plugin.video.uhd/"
SKIP_DIRS = {"__pycache__", ".git"}
SKIP_SUFFIX = (".pyc", ".pyo")


def addon_version(addon_dir):
    return ET.parse(os.path.join(addon_dir, "addon.xml")).getroot().get("version")


def zip_addon(addon_dir, addon_id, out_dir, version):
    """One directory named after the addon id at the top, as Kodi requires."""
    target = os.path.join(out_dir, addon_id)
    os.makedirs(target, exist_ok=True)
    path = os.path.join(target, "%s-%s.zip" % (addon_id, version))
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for base, dirs, files in os.walk(addon_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for name in files:
                if name.endswith(SKIP_SUFFIX):
                    continue
                full = os.path.join(base, name)
                z.write(full, os.path.join(addon_id,
                                           os.path.relpath(full, addon_dir)))
    # Kodi shows these while browsing the repository, before anything is installed.
    for extra in ("addon.xml", "icon.png", "fanart.jpg"):
        src = os.path.join(addon_dir, extra)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(target, extra))
    return path


def build_repository_addon(work_dir, base_url, version):
    """Generate the repository addon rather than hand-maintaining a copy of
    the URLs in two places."""
    src = os.path.join(work_dir, REPO_ID)
    os.makedirs(src, exist_ok=True)
    addon_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<addon id="{id}" name="UHD Repository" version="{ver}" provider-name="uhd-kodi">
  <extension point="xbmc.addon.repository" name="UHD Repository">
    <dir>
      <info compressed="false">{base}addons.xml</info>
      <checksum>{base}addons.xml.md5</checksum>
      <datadir zip="true">{base}</datadir>
    </dir>
  </extension>
  <extension point="xbmc.addon.metadata">
    <summary lang="en_GB">Update source for the UHD addon</summary>
    <summary lang="zh_CN">UHD 插件的更新源</summary>
    <description lang="en_GB">Install once; Kodi then offers updates to the UHD addon automatically.</description>
    <description lang="zh_CN">安装一次即可，之后 Kodi 会自动提示 UHD 插件的更新。</description>
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
    icon = os.path.join(ROOT, PLUGIN_ID, "icon.png")
    if os.path.exists(icon):
        shutil.copy2(icon, os.path.join(src, "icon.png"))
    return src


def write_index(out_dir, addon_dirs):
    root = ET.Element("addons")
    for d in addon_dirs:
        root.append(ET.parse(os.path.join(d, "addon.xml")).getroot())
    body = ET.tostring(root, encoding="unicode")
    xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + body + "\n"
    with open(os.path.join(out_dir, "addons.xml"), "w", encoding="utf-8") as f:
        f.write(xml)
    digest = hashlib.md5(xml.encode("utf-8")).hexdigest()
    with open(os.path.join(out_dir, "addons.xml.md5"), "w", encoding="utf-8") as f:
        f.write(digest)
    return digest


def write_landing(out_dir, base, version):
    """Pages serves a bare directory otherwise; say what the URL is for."""
    html = """<!doctype html>
<html lang="zh-CN"><meta charset="utf-8">
<title>UHD Kodi 仓库</title>
<style>body{{font:16px/1.7 system-ui,sans-serif;max-width:44rem;margin:3rem auto;padding:0 1rem}}
code{{background:#f2f2f2;padding:.15em .4em;border-radius:3px}}</style>
<h1>UHD Kodi 仓库</h1>
<p>当前版本：<b>{ver}</b></p>
<h2>自动更新（推荐）</h2>
<ol>
<li>下载 <a href="repository.uhd/repository.uhd-{ver}.zip">repository.uhd-{ver}.zip</a></li>
<li>Kodi → 插件 → 从 zip 文件安装 → 选中它</li>
<li>之后在「从存储库安装」里找到 UHD，日后更新由 Kodi 自动提示</li>
</ol>
<h2>手动安装</h2>
<p>直接装 <a href="plugin.video.uhd/plugin.video.uhd-{ver}.zip">plugin.video.uhd-{ver}.zip</a>，以后需要自己重装新版。</p>
<p>装完在插件设置里填服务器地址与账号。仓库不包含任何地址或凭据。</p>
<p><code>{base}</code></p>
</html>
""".format(ver=version, base=base)
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

    plugin_dir = os.path.join(ROOT, PLUGIN_ID)
    version = addon_version(plugin_dir)
    zip_addon(plugin_dir, PLUGIN_ID, out, version)

    # The repository addon carries the plugin's version so a bump propagates.
    work = os.path.join(out, ".build")
    repo_src = build_repository_addon(work, base, version)
    zip_addon(repo_src, REPO_ID, out, version)
    shutil.copy2(os.path.join(repo_src, "addon.xml"),
                 os.path.join(out, REPO_ID, "addon.xml"))
    shutil.rmtree(work, ignore_errors=True)

    digest = write_index(out, [plugin_dir, os.path.join(out, REPO_ID)])
    write_landing(out, base, version)
    print("repository built in %s" % out)
    print("  version   %s" % version)
    print("  base url  %s" % base)
    print("  md5       %s" % digest)
    for base_dir, _d, files in os.walk(out):
        for f in sorted(files):
            rel = os.path.relpath(os.path.join(base_dir, f), out)
            print("  %s" % rel.replace("\\", "/"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
