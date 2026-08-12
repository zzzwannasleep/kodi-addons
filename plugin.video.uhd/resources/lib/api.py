# -*- coding: utf-8 -*-
"""Client for Emby-compatible servers (Emby 4.9.x and UHD Media Server).

Both servers speak the same API and disagree about parts of it, so anything
that differs is probed at runtime rather than assumed. Measured on one of each:

                                  UHD 4.9.3        Emby 4.9.5
  Genres= filter                  ignored          works
  Years= filter                   ignored          works
  NameStartsWith / HasSubtitles   ignored          ignored
  Filters= with ParentId          ignored          works
  MediaSources in list queries    included         omitted (fetch the detail)
  PlaySessionId on reports        optional         required (400 without one)
  undeclared image type           returns Primary  404
  /Search/Hints                   -                returns nothing, use Items
  SupportsTranscoding             false            false

Shared behaviour worth knowing:

  * /Users/{id}/Items/Latest returns a bare JSON array, not an Items envelope.
  * Chapters come back only from the item detail, never from a list query.
  * /Sessions/Playing/Progress does NOT persist a resume point. Only
    /Sessions/Playing/Stopped writes one, so the stop report is what makes
    resume work at all.
  * Playback is direct play; no transcoding path exists on either server.
"""
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

TICKS_PER_SEC = 10000000
CLIENT = "Kodi"
VERSION = "1.0.0"
# Retried rather than surfaced: the gateway produces these on its own.
TRANSIENT = (502, 503, 504)
RETRIES = 3
RETRY_WAIT = 2
# Subtitle codecs that are pictures, not text. Kodi renders them from the
# container during direct play, but they cannot be fetched as a sidecar file:
# the server answers such a request with an empty 200, which would attach a
# subtitle track that exists and never displays.
GRAPHIC_SUBTITLES = ("pgssub", "pgs", "dvdsub", "dvbsub", "vobsub", "hdmv_pgs_subtitle")
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
        # A gateway in front of the server hands out sporadic 502/504s and
        # dropped reads: measured three failures in a row followed by success on
        # the fourth try. Without this every one of them surfaces to the user as
        # a failed login or an empty library.
        raw = None
        for attempt in range(RETRIES):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout,
                                            context=self._ctx) as r:
                    raw = r.read()
                break
            except urllib.error.HTTPError as e:
                if e.code == 401:
                    raise AuthError("401 unauthorized on " + path)
                if e.code in TRANSIENT and attempt < RETRIES - 1:
                    time.sleep(RETRY_WAIT)
                    continue
                raise ApiError("HTTP %s on %s" % (e.code, path))
            except Exception as e:
                if attempt < RETRIES - 1:
                    time.sleep(RETRY_WAIT)
                    continue
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

    # -------------------------------------------------------------- discovery
    def genres(self, limit=300):
        """The genre list.

        Asked for globally: Emby returns nothing at all when this endpoint is
        given a ParentId, while the genre names themselves are shared across
        libraries anyway.
        """
        data = self._get("/Genres", UserId=self.user_id, SortBy="SortName",
                         Limit=limit)
        if isinstance(data, list):
            return data
        return (data or {}).get("Items", [])

    def genre_filter_works(self):
        """Does Genres= actually narrow a query on this server?

        UHD accepts the parameter and returns the unfiltered set, so a genre
        menu there would offer categories that every one of them shows the same
        library behind. One comparison settles it; callers cache the answer.
        """
        names = [g.get("Name") for g in self.genres(limit=1) if g.get("Name")]
        if not names:
            return False
        plain = self._get("/Users/%s/Items" % self.user_id,
                          IncludeItemTypes="Movie", Recursive="true", Limit=1)
        narrowed = self._get("/Users/%s/Items" % self.user_id,
                             IncludeItemTypes="Movie", Recursive="true", Limit=1,
                             Genres=names[0])
        total = (plain or {}).get("TotalRecordCount")
        return bool(total) and (narrowed or {}).get("TotalRecordCount") != total

    def by_genre(self, genre, types=None, start=0, limit=50,
                 sort_by="SortName", sort_order="Ascending"):
        return self._get(
            "/Users/%s/Items" % self.user_id, Genres=genre,
            IncludeItemTypes=types, Recursive="true", StartIndex=start,
            Limit=limit, SortBy=sort_by, SortOrder=sort_order, Fields=self.FIELDS,
        ) or {"Items": [], "TotalRecordCount": 0}

    # -------------------------------------------------------------- user state
    def set_favorite(self, item_id, on):
        self._request("POST" if on else "DELETE",
                      "/Users/%s/FavoriteItems/%s" % (self.user_id, item_id))

    def set_played(self, item_id, on):
        self._request("POST" if on else "DELETE",
                      "/Users/%s/PlayedItems/%s" % (self.user_id, item_id))

    def set_position(self, item_id, ticks):
        """Write a resume point directly.

        Reliable for clearing one, and on UHD for setting any value. Emby
        accepted some positions and quietly kept the old value for others, so
        this is not a substitute for the stop report -- it is here to undo a
        position, which it does do consistently.
        """
        self._request("POST", "/Users/%s/Items/%s/UserData" % (self.user_id, item_id),
                      body={"PlaybackPositionTicks": int(ticks)})

    # ---------------------------------------------------------------- playback
    def media_sources(self, item_id):
        """Every version of an item, newest metadata straight from the server.

        Emby leaves MediaSources out of list queries however many Fields are
        asked for, so the only reliable place to count an item's versions is
        its detail. One film on the test server has nineteen of them, ranging
        from an 8 GB x265 encode to a 37 GB remux, and picking blindly means
        the viewer gets whichever one happens to be first.
        """
        detail = self._get("/Users/%s/Items/%s" % (self.user_id, item_id),
                           Fields="MediaSources,MediaStreams") or {}
        return detail.get("MediaSources") or []

    def stream_url(self, item_id, media_source_id=None):
        """Return (url, play_session_id, media_source) for direct play.

        The play session id is never None: Emby rejects a playback report that
        carries no PlaySessionId with a 400, and accepts any string, so one is
        invented when the server does not supply it.
        """
        session = uuid.uuid4().hex
        try:
            info = self._request(
                "POST", "/Items/%s/PlaybackInfo" % item_id,
                params={"UserId": self.user_id},
                body={"IsPlayback": True, "AutoOpenLiveStream": True,
                      "MaxStreamingBitrate": 120000000,
                      "MediaSourceId": media_source_id},
            ) or {}
            session = info.get("PlaySessionId") or session
            for ms in info.get("MediaSources") or []:
                if media_source_id and ms.get("Id") != media_source_id:
                    continue
                url = ms.get("DirectStreamUrl")
                if url:
                    if url.startswith("/"):
                        url = self.server + url
                    return url, session, ms
        except (ApiError, AuthError) as e:
            self._log("PlaybackInfo failed, using /Videos/stream: %s" % e)
        # Fallback: this endpoint 302s to the CDN and is what the web player
        # falls back to as well.
        return ("%s/emby/Videos/%s/stream?%s" % (
            self.server, item_id,
            urllib.parse.urlencode({"MediaSourceId": media_source_id or item_id,
                                    "Static": "true", "api_key": self.token})),
            session, None)

    def subtitle_urls(self, item_id, source):
        """Sidecar URLs for the text subtitles of a source, newest first.

        Only text tracks are offered. A request for a graphic track returns an
        empty 200 rather than an error, so trusting the status code attaches a
        subtitle that can never render.
        """
        urls = []
        if not source:
            return urls
        msid = source.get("Id") or item_id
        for s in source.get("MediaStreams") or []:
            if s.get("Type") != "Subtitle":
                continue
            if (s.get("Codec") or "").lower() in GRAPHIC_SUBTITLES:
                continue
            if not s.get("IsExternal") and not s.get("DeliveryUrl"):
                # Embedded text: Kodi reads it out of the container itself
                # during direct play, no sidecar needed.
                continue
            url = s.get("DeliveryUrl")
            if url:
                url = self.server + url if url.startswith("/") else url
                sep = "&" if "?" in url else "?"
                urls.append(url + sep + "api_key=" + (self.token or ""))
            else:
                urls.append("%s/emby/Videos/%s/%s/Subtitles/%s/Stream.srt?%s" % (
                    self.server, item_id, msid, s.get("Index"),
                    urllib.parse.urlencode({"api_key": self.token})))
        return urls

    def _report(self, path, item_id, media_source_id, ticks, session, extra=None):
        body = {"ItemId": item_id, "MediaSourceId": media_source_id,
                "PositionTicks": int(ticks), "PlayMethod": "DirectStream",
                "CanSeek": True, "PlaySessionId": session or uuid.uuid4().hex}
        if extra:
            body.update(extra)
        try:
            self._request("POST", path, body=body)
        except (ApiError, AuthError) as e:
            self._log("report %s failed: %s" % (path, e))

    def report_start(self, item_id, msid, ticks=0, session=None):
        self._report("/Sessions/Playing", item_id, msid, ticks, session)

    def report_progress(self, item_id, msid, ticks, paused=False, session=None):
        self._report("/Sessions/Playing/Progress", item_id, msid, ticks, session,
                     {"IsPaused": paused})

    def report_stop(self, item_id, msid, ticks, session=None):
        """The only report that writes a resume point.

        Progress reports keep the server's "now playing" display current but
        are not persisted -- measured across fifty seconds of waiting, the
        stored position stayed at zero until a stop arrived. Losing this call
        loses the whole viewing.
        """
        self._report("/Sessions/Playing/Stopped", item_id, msid, ticks, session)

    # ------------------------------------------------------------------ images
    def image_url(self, item_id, kind="Primary", tag=None, max_width=None):
        q = {"api_key": self.token}
        if tag:
            q["tag"] = tag
        if max_width:
            q["maxWidth"] = max_width
        return "%s/emby/Items/%s/Images/%s?%s" % (
            self.server, item_id, kind, urllib.parse.urlencode(q))
