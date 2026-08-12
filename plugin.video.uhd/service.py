# -*- coding: utf-8 -*-
"""Background service: reports playback position back to UHD.

The plugin process exits right after setResolvedUrl, so it cannot watch the
player. It leaves the item ids on a window property and this service picks
them up, then reports start/progress/stop so resume points and watched state
stay in sync with the server (and with the web player).
"""
import xbmc
import xbmcgui

from resources.lib.api import TICKS_PER_SEC
from resources.lib import session

PROGRESS_INTERVAL = 10  # seconds between position reports
START_GRACE = 60        # seconds Kodi may spend opening the stream
PLUGIN_ROOT = "plugin://%s/" % session.ADDON_ID
HOME = xbmcgui.Window(10000)


class Reporter(xbmc.Monitor):
    def __init__(self):
        super().__init__()
        self.player = xbmc.Player()
        self.api = None
        self.current = None  # (item_id, media_source_id, play_session_id)
        self.last_ticks = 0
        # main.py hands over the ids during setResolvedUrl, seconds before Kodi
        # has actually opened the stream. Until playback is seen running, a
        # not-playing player means "not started yet", not "finished" -- treating
        # it as finished reports position 0 and abandons the rest of the movie.
        self.started = False
        self.waiting = 0
        self.viewed = None

    def _apply_view(self):
        """Force the configured view once a listing of ours is on screen.

        This cannot be done from the plugin: Kodi restores its own remembered
        view state for the path after the plugin process exits, so a
        Container.SetViewMode issued there is overwritten and the first visit
        to any path shows a plain list. Here the container already exists.
        The root menu gets its own setting: its entries are 16:9 images with
        the name baked in, which suits the icon wall, while content pages want
        the poster grid.
        """
        path = xbmc.getInfoLabel("Container.FolderPath")
        if not path.startswith(PLUGIN_ROOT):
            self.viewed = None
            return
        if path == self.viewed:
            return
        # An empty Container.Viewmode means the container is still being built;
        # SetViewMode issued then is silently dropped. Wait for the next tick.
        if not xbmc.getInfoLabel("Container.Viewmode"):
            return
        if "action=sortmenu" in path:
            # A menu of nine text choices reads as a list, not as a wall of
            # identical placeholder icons. The sort menu declares no content
            # type, and with none Estuary offers only the icon wall (52) and
            # the wide list (55) -- asking for plain List (50) here silently
            # lands on 55 anyway, so name it.
            view = 55
        else:
            setting = "root_view_mode" if path == PLUGIN_ROOT else "view_mode"
            view = session.ADDON.getSettingInt(setting)
        self.viewed = path
        if view:
            xbmc.executebuiltin("Container.SetViewMode(%d)" % view)

    def _client(self):
        if self.api is None:
            self.api = session.get_client(interactive=False)
        return self.api

    def _claim(self):
        """Pick up the ids main.py left for us, if any."""
        raw = HOME.getProperty("uhd.playing")
        if not raw:
            return None
        HOME.clearProperty("uhd.playing")
        parts = raw.split("|")
        return (parts[0], parts[1] or None, parts[2] or None)

    def _ticks(self):
        try:
            return int(self.player.getTime() * TICKS_PER_SEC)
        except RuntimeError:
            return self.last_ticks

    def _stop(self):
        if not self.current:
            return
        api = self._client()
        if api:
            api.report_stop(self.current[0], self.current[1], self.last_ticks,
                            self.current[2])
        self._reset()

    def _reset(self):
        self.current = None
        self.last_ticks = 0
        self.started = False
        self.waiting = 0

    def run(self):
        elapsed = 0
        while not self.abortRequested():
            self._apply_view()

            pending = self._claim()
            if pending:
                self._stop()
                # Drop the client so a token refreshed by main.py is picked up;
                # this service outlives any single token.
                self.api = None
                self.current = pending
                api = self._client()
                if api:
                    api.report_start(pending[0], pending[1], 0, pending[2])
                elapsed = 0

            if self.current:
                if self.player.isPlayingVideo():
                    self.started = True
                    self.last_ticks = self._ticks()
                    elapsed += 1
                    if elapsed >= PROGRESS_INTERVAL:
                        elapsed = 0
                        api = self._client()
                        if api:
                            api.report_progress(self.current[0], self.current[1],
                                                self.last_ticks,
                                                bool(xbmc.getCondVisibility(
                                                    "Player.Paused")),
                                                self.current[2])
                elif self.started:
                    self._stop()
                else:
                    self.waiting += 1
                    if self.waiting > START_GRACE:
                        session.log("playback never started for %s, dropping"
                                    % self.current[0], xbmc.LOGWARNING)
                        self._reset()

            if self.waitForAbort(1):
                break
        self._stop()


if __name__ == "__main__":
    session.log("service started")
    Reporter().run()
    session.log("service stopped")
