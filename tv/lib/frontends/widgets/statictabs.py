# Miro - an RSS based video player application
# Copyright (C) 2005, 2006, 2007, 2008, 2009, 2010, 2011
# Participatory Culture Foundation
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA
#
# In addition, as a special exception, the copyright holders give
# permission to link the code of portions of this program with the OpenSSL
# library.
#
# You must obey the GNU General Public License in all respects for all of
# the code used other than OpenSSL. If you modify file(s) with this
# exception, you may extend this exception to your version of the file(s),
# but you are not obligated to do so. If you do not wish to do so, delete
# this exception statement from your version. If you delete this exception
# statement from all source files in the program, then also delete it here.

"""statictabs.py -- Tabs that are always present."""

from miro import app
from miro.gtcache import gettext as _
from miro import prefs
from miro.frontends.widgets import browser
from miro.frontends.widgets import imagepool
from miro.frontends.widgets import widgetutil

class StaticTab(object):
    type = u'static'
    tall = True

    def __init__(self):
        self.unwatched = self.downloading = 0
        self.icon = widgetutil.make_surface(self.icon_name)
        self.active_icon = widgetutil.make_surface(
            self.icon_name + '_active')

# this maps guide urls to titles we'd rather they use.
_guide_url_to_title_map = {
    prefs.CHANNEL_GUIDE_URL.default: "Miro"
    }

# this maps guide urls to icons we'd rather they use.
_guide_url_to_icon_map = {
    prefs.CHANNEL_GUIDE_URL.default: 'icon-guide'
    }

class ChannelGuideTab(StaticTab):
    id = u'guide'
    name = u''
    icon_name = 'icon-guide'

    def __init__(self):
        StaticTab.__init__(self)
        self._set_from_info(app.tabs['site'].default_info)
        self.browser = browser.BrowserNav(app.tabs['site'].default_info)

    def update(self, guide_info):
        self._set_from_info(guide_info)
        self.browser.guide_info = guide_info

    def _set_from_info(self, guide_info):
        if guide_info is None:
            return

        # XXX This code is a bit ugly, because we want to use pretty defaults for
        # the Miro Guide, but still allow themes to override

        if guide_info.default and guide_info.url in _guide_url_to_title_map:
            self.name = _guide_url_to_title_map[guide_info.url]
        else:
            self.name = guide_info.name

        if guide_info.default and guide_info.url in _guide_url_to_icon_map:
            # one of our default guides
            icon_name = _guide_url_to_icon_map[guide_info.url]
            if icon_name != self.icon_name:
                self.icon_name = _guide_url_to_icon_map[guide_info.url]
                self.icon = widgetutil.make_surface(self.icon_name)
                del self.active_icon
        elif guide_info.faviconIsDefault:
            # theme guide that should use default favicon
            pass
        else:
            # theme guide with a favicon
            surface = imagepool.get_surface(guide_info.favicon)
            if surface.width != 23 or surface.height != 23:
                self.icon = imagepool.get_surface(guide_info.favicon,
                                                  size=(23, 23))
            else:
                self.icon = surface
            del self.active_icon

class SearchTab(StaticTab):
    type = u'search'
    id = u'search'
    name = _('Video Search')
    icon_name = 'icon-search'

    def __init__(self):
        StaticTab.__init__(self)
        # FIXME - we redo the translation here so we're doing it at
        # instantiation time and NOT at import time which is stupid.
        SearchTab.name = _("Video Search")

class VideoLibraryTab(StaticTab):
    type = u'videos'
    id = u'videos'
    name = _('Videos')
    icon_name = 'icon-video'
    media_type = u'video'

    def __init__(self):
        StaticTab.__init__(self)
        VideoLibraryTab.name = _("Videos")

class AudioLibraryTab(StaticTab):
    type = u'music'
    id = u'music'
    name = _('Music')
    icon_name = 'icon-audio'
    media_type = u'audio'

    def __init__(self):
        StaticTab.__init__(self)
        # FIXME - we redo the translation here so we're doing it at
        # instantiation time and NOT at import time which is stupid.
        AudioLibraryTab.name = _("Music")

class OthersTab(StaticTab):
    type = u'others'
    id = u'others'
    name = _('Misc')
    icon_name = 'icon-other'
    media_type = u'other'

    def __init__(self):
        StaticTab.__init__(self)
        # FIXME - we redo the translation here so we're doing it at
        # instantiation time and NOT at import time which is stupid.
        OthersTab.name = _("Misc")

class DownloadsTab(StaticTab):
    type = u'downloading'
    id = u'downloading'
    name = _('Downloading')
    icon_name = 'icon-downloading'

    def __init__(self):
        StaticTab.__init__(self)
        DownloadsTab.name = _("Downloading")

class ConvertingTab(StaticTab):
    type = u'converting'
    id = u'converting'
    name = _('Converting')
    icon_name = 'icon-converting'

    def __init__(self):
        StaticTab.__init__(self)
        # FIXME - we redo the translation here so we're doing it at
        # instantiation time and NOT at import time which is stupid.
        ConvertingTab.name = _("Converting")
