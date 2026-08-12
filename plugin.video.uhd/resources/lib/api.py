# -*- coding: utf-8 -*-
"""Client for UHD Media Server (Emby-compatible HTTP API, server 4.9.x).

Divergences from stock Emby, confirmed by probing the live server -- do not
"fix" these back to the Emby docs without re-probing:

  * Genres / GenreIds / Years / NameStartsWith query filters are silently
    IGNORED (the server returns the unfiltered set with the original
    TotalRecordCount). Never build UI on top of them.
  * Filters= only takes effect when ParentId is absent.
  * /Years and /Tags return 404. /Genres, /Studios, /Persons work.
  * SearchTerm, SortBy/SortOrder, StartIndex/Limit, IncludeItemTypes and
    ParentId all behave as documented.
  * /Users/{id}/Items/Latest returns a bare JSON array, not an Items envelope.
  * Playback is direct-play only (SupportsTranscoding is false everywhere);
    the stream URL 302-redirects to a CDN host.
"""
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

TICKS_PER_SEC = 10000000
CLIENT = "Kodi"
VERSION = "1.0.0"
# Cloudflare in front of the server 403s the default Python-urllib UA, so every
# request must carry an explicit one.
USER_AGENT = "Kodi/21.0 (plugin.video.uhd/%s)" % VERSION


class AuthError(Exception):
    pass


class ApiError(Exception):
    pass


class UHD(object):
    # Requested on every item query. Kept in one place so list and detail
    # views always carry the same metadata.
    FIELDS = ("Overview,Genres,ProductionYear,CommunityRating,OfficialRating,"
              "RunTimeTicks,People,Studios,Taglines,ProviderIds,PremiereDate,"
              "MediaSources,ChildCount")

    def __init__(self, server, device_id, token=None, user_id=None, timeout=20,
                 verify_ssl=True, log=None):
        self.server = (server or "").rstrip("/")
        self.device_id = device_id
        self.token = token
        self.user_id = user_id
        self.timeout = timeout
        self._log = log or (lambda m: None)
        self._ctx = None if verify_ssl else ssl._create_unverified_context()

    # ---------------------------------------------------------------- plumbing
    def _auth_header(self):
        return ('MediaBrowser Client="%s", Device="%s", DeviceId="%s", '
                'Version="%s"' % (CLIENT, CLIENT, self.device_id, VERSION))

    def _request(self, method, path, params=None, body=None):
        url = self.server + "/emby" + path
        if params:
            clean = {k: v for k, v in params.items() if v is not None and v != ""}
            if clean:
                url += "?" + urllib.parse.urlencode(clean)
        data = None
        headers = {"Accept": "application/json",
                   "User-Agent": USER_AGENT,
                   "X-Emby-Authorization": self._auth_header()}
        if self.token:
            headers["X-Emby-Token"] = self.token
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ctx) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            if e.code == 401:
                raise AuthError("401 unauthorized on " + path)
            raise ApiError("HTTP %s on %s" % (e.code, path))
        except Exception as e:
            raise ApiError("%s on %s" % (e, path))
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return None

    def _get(self, path, **params):
        return self._request("GET", path, params=params)

    # ------------------------------------------------------------------- auth
    @staticmethod
    def public_info(server, timeout=15):
        req = urllib.request.Request(server.rstrip("/") + "/emby/System/Info/Public",
                                     headers={"Accept": "application/json",
                                              "User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def authenticate(self, username, password):
        d = self._request("POST", "/Users/AuthenticateByName",
                          body={"Username": username, "Pw": password})
        if not d or not d.get("AccessToken"):
            raise AuthError("server returned no access token")
        self.token = d["AccessToken"]
        self.user_id = d["User"]["Id"]
        return self.token, self.user_id

    # ---------------------------------------------------------------- browsing
    def views(self):
        return (self._get("/Users/%s/Views" % self.user_id) or {}).get("Items", [])

    def items(self, parent_id=None, types=None, start=0, limit=50,
              sort_by="SortName", sort_order="Ascending", search=None, filters=None):
        return self._get(
            "/Users/%s/Items" % self.user_id,
            ParentId=parent_id, IncludeItemTypes=types, Recursive="true",
            StartIndex=start, Limit=limit, SortBy=sort_by, SortOrder=sort_order,
            SearchTerm=search, Filters=filters, Fields=self.FIELDS,
        ) or {"Items": [], "TotalRecordCount": 0}

    def item(self, item_id):
        return self._get("/Users/%s/Items/%s" % (self.user_id, item_id),
                         Fields=self.FIELDS)

    def seasons(self, series_id):
        return (self._get("/Shows/%s/Seasons" % series_id, UserId=self.user_id,
                          Fields=self.FIELDS) or {}).get("Items", [])

    def episodes(self, series_id, season_id=None):
        return (self._get("/Shows/%s/Episodes" % series_id, UserId=self.user_id,
                          SeasonId=season_id, Fields=self.FIELDS) or {}).get("Items", [])

    def resume(self, limit=40):
        return (self._get("/Users/%s/Items/Resume" % self.user_id, Limit=limit,
                          MediaTypes="Video", Recursive="true",
                          Fields=self.FIELDS) or {}).get("Items", [])

    def next_up(self, limit=40):
        return (self._get("/Shows/NextUp", UserId=self.user_id, Limit=limit,
                          Fields=self.FIELDS) or {}).get("Items", [])

    def latest(self, parent_id=None, types=None, limit=40):
        return self._get("/Users/%s/Items/Latest" % self.user_id,
                         ParentId=parent_id, IncludeItemTypes=types,
                         Limit=limit, Fields=self.FIELDS) or []

    def favorites(self, types=None, start=0, limit=50):
        # ParentId must stay None here: the server drops Filters= when a parent
        # is supplied.
        return self.items(parent_id=None, types=types, start=start, limit=limit,
                          filters="IsFavorite")

    # -------------------------------------------------------------- user state
    def set_favorite(self, item_id, on):
        self._request("POST" if on else "DELETE",
                      "/Users/%s/FavoriteItems/%s" % (self.user_id, item_id))

    def set_played(self, item_id, on):
        self._request("POST" if on else "DELETE",
                      "/Users/%s/PlayedItems/%s" % (self.user_id, item_id))

    # ---------------------------------------------------------------- playback
    def stream_url(self, item_id, media_source_id=None):
        """Return (url, play_session_id) for direct play."""
        try:
            info = self._request(
                "POST", "/Items/%s/PlaybackInfo" % item_id,
                params={"UserId": self.user_id},
                body={"IsPlayback": True, "AutoOpenLiveStream": True,
                      "MaxStreamingBitrate": 120000000,
                      "MediaSourceId": media_source_id},
            ) or {}
            for ms in info.get("MediaSources") or []:
                if media_source_id and ms.get("Id") != media_source_id:
                    continue
                url = ms.get("DirectStreamUrl")
                if url:
                    if url.startswith("/"):
                        url = self.server + url
                    return url, info.get("PlaySessionId")
        except (ApiError, AuthError) as e:
            self._log("PlaybackInfo failed, using /Videos/stream: %s" % e)
        # Fallback: this endpoint 302s to the CDN and is what the web player
        # falls back to as well.
        return ("%s/emby/Videos/%s/stream?%s" % (
            self.server, item_id,
            urllib.parse.urlencode({"MediaSourceId": media_source_id or item_id,
                                    "Static": "true", "api_key": self.token})), None)

    def _report(self, path, item_id, media_source_id, ticks, extra=None):
        body = {"ItemId": item_id, "MediaSourceId": media_source_id,
                "PositionTicks": int(ticks), "PlayMethod": "DirectStream",
                "CanSeek": True}
        if extra:
            body.update(extra)
        try:
            self._request("POST", path, body=body)
        except (ApiError, AuthError) as e:
            self._log("report %s failed: %s" % (path, e))

    def report_start(self, item_id, msid, ticks=0, session=None):
        self._report("/Sessions/Playing", item_id, msid, ticks,
                     {"PlaySessionId": session} if session else None)

    def report_progress(self, item_id, msid, ticks, paused=False, session=None):
        extra = {"IsPaused": paused}
        if session:
            extra["PlaySessionId"] = session
        self._report("/Sessions/Playing/Progress", item_id, msid, ticks, extra)

    def report_stop(self, item_id, msid, ticks, session=None):
        self._report("/Sessions/Playing/Stopped", item_id, msid, ticks,
                     {"PlaySessionId": session} if session else None)

    # ------------------------------------------------------------------ images
    def image_url(self, item_id, kind="Primary", tag=None, max_width=None):
        q = {"api_key": self.token}
        if tag:
            q["tag"] = tag
        if max_width:
            q["maxWidth"] = max_width
        return "%s/emby/Items/%s/Images/%s?%s" % (
            self.server, item_id, kind, urllib.parse.urlencode(q))
