# -*- coding: utf-8 -*-
"""Shared between main.py and service.py: build an authenticated UHD client.

Kodi starts a fresh Python process for every navigation, so the access token
is cached on disk in the addon's profile directory rather than kept in memory.
Credentials themselves live only in Kodi's addon settings -- never in the
source tree.
"""
import json
import os
import uuid

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

from .api import UHD, ApiError, AuthError

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
TOKEN_FILE = os.path.join(PROFILE, "session.json")
SORT_FILE = os.path.join(PROFILE, "sort.json")


def log(msg, level=xbmc.LOGINFO):
    xbmc.log("[%s] %s" % (ADDON_ID, msg), level)


def notify(msg, heading=None):
    xbmcgui.Dialog().notification(heading or ADDON.getAddonInfo("name"), msg,
                                  xbmcgui.NOTIFICATION_WARNING, 5000)


def _device_id():
    """Stable per-installation id, so the server sees one device, not many."""
    did = ADDON.getSetting("device_id")
    if not did:
        did = uuid.uuid4().hex
        ADDON.setSetting("device_id", did)
    return did


def _read_cache():
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_cache(data):
    if not os.path.isdir(PROFILE):
        os.makedirs(PROFILE, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)


def clear_cache():
    try:
        os.remove(TOKEN_FILE)
    except OSError:
        pass


def get_client(interactive=True, force_login=False):
    """Return a UHD client, or None if it cannot authenticate.

    The cached token is used optimistically: verifying it would cost an extra
    round trip on every single navigation, and on a slow link that turns one
    timeout into a whole failed page. Callers handle AuthError instead and
    retry once with force_login=True.

    interactive=False suppresses the settings dialog / notifications, for use
    from the background service.
    """
    server = ADDON.getSetting("server").strip()
    username = ADDON.getSetting("username").strip()
    password = ADDON.getSetting("password")
    if not server or not username:
        if interactive:
            notify(ADDON.getLocalizedString(30901))
            ADDON.openSettings()
        return None
    if "://" not in server:
        server = "https://" + server

    cache = {} if force_login else _read_cache()
    fresh = cache.get("server") == server
    client = UHD(server, _device_id(),
                 token=cache.get("token") if fresh else None,
                 user_id=cache.get("user_id") if fresh else None,
                 timeout=ADDON.getSettingInt("timeout") or 20,
                 verify_ssl=ADDON.getSettingBool("verify_ssl"),
                 log=log)
    if client.token and client.user_id:
        return client

    try:
        token, user_id = client.authenticate(username, password)
    except AuthError as e:
        log("login failed: %s" % e, xbmc.LOGERROR)
        if interactive:
            notify(ADDON.getLocalizedString(30902))
        return None
    except ApiError as e:
        log("login error: %s" % e, xbmc.LOGERROR)
        if interactive:
            notify(str(e))
        return None
    _write_cache({"server": server, "token": token, "user_id": user_id})
    return client


# ---------------------------------------------------------------- server quirks
CAPS_FILE = os.path.join(PROFILE, "caps.json")


def capability(name, probe):
    """Remember what this server turned out to support.

    The two servers this addon talks to disagree about which query parameters
    do anything, and the only honest way to tell them apart is to ask. Probing
    costs two requests, so the answer is kept per server and per capability.
    """
    server = ADDON.getSetting("server").strip()
    key = "%s|%s" % (server, name)
    try:
        with open(CAPS_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    if key in cache:
        return cache[key]
    try:
        value = bool(probe())
    except Exception as e:
        log("capability probe %r failed: %s" % (name, e))
        return False
    cache[key] = value
    if not os.path.isdir(PROFILE):
        os.makedirs(PROFILE, exist_ok=True)
    with open(CAPS_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f)
    log("capability %r on this server: %s" % (name, value))
    return value


# ------------------------------------------------------------------ sort prefs
def _read_sorts():
    try:
        with open(SORT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def get_sort(key):
    """Per-library sort choice, or None to fall back to the addon default."""
    entry = _read_sorts().get(key)
    if isinstance(entry, dict) and entry.get("by"):
        return entry["by"], entry.get("order") or "Ascending"
    return None


def set_sort(key, by, order):
    data = _read_sorts()
    data[key] = {"by": by, "order": order}
    if not os.path.isdir(PROFILE):
        os.makedirs(PROFILE, exist_ok=True)
    with open(SORT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f)
