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

"""itemlistcontroller.py -- Controllers for item lists.

itemlist, itemlistcontroller and itemlistwidgets work together using
the MVC pattern.  itemlist handles the Model, itemlistwidgets handles
the View and itemlistcontroller handles the Controller.

This module contains the ItemListController base class along with
controllers that work for the static tabs which are pretty simple
cases.
"""

import logging
from urlparse import urljoin

from miro import app
from miro.errors import WidgetActionError
from miro import messages
from miro import prefs
from miro import subscription
from miro.errors import ActionUnavailableError
from miro.gtcache import gettext as _
from miro.frontends.widgets import dialogs
from miro.frontends.widgets import itemcontextmenu
from miro.frontends.widgets import itemlist
from miro.frontends.widgets import itemrenderer
from miro.frontends.widgets import itemtrack
from miro.frontends.widgets import itemlistwidgets
from miro.frontends.widgets import widgetutil
from miro.frontends.widgets import menus
from miro.frontends.widgets.widgetstatestore import WidgetStateStore
from miro.plat.frontends.widgets import timer
from miro.plat.frontends.widgets import widgetset

class ItemListDragHandler(object):
    def allowed_actions(self):
        return widgetset.DRAG_ACTION_COPY | widgetset.DRAG_ACTION_MOVE

    def allowed_types(self):
        return ('downloaded-item',)

    def begin_drag(self, tableview, rows):
        videos = set(row[0].id for row in rows if row[0].downloaded)
        if videos:
            return {'downloaded-item': repr(videos)}
        else:
            return None

class FilteredListMixin(object):
    """Track a filter switch attached to an ItemListController
    """
    def __init__(self):
        filters = app.widget_state.get_filters(self.type, self.id)
        self.update_filters(filters)

    def on_toggle_filter(self, button, filter_):
        """Handle the filter switch changing state."""
        self.update_filters(filter_)
        app.widget_state.toggle_filters(self.type, self.id, filter_)

    def update_filters(self, filters):
        """Update the display and toolbar filter switch state."""
        self.item_list_will_change()
        self.titlebar.toggle_filter(filters)
        self.widget.toggle_filter(filters)
        self.item_list.toggle_filter(filters)
        self.send_model_changed()
        self.check_for_empty_list()

class ProgressTrackingListMixin(object):
    """Controller that cares about item metadata extraction progress."""
    def __init__(self):
        self.postponed = None
        if self.type == 'music':
            self.mediatype = 'audio'
        elif self.type == 'videos':
            self.mediatype = 'video'
        else:
            self.mediatype = 'other'

    def update_metadata_progress(self, remaining, eta, total):
        meter = self.widget.get_progress_meter()
        if meter:
            meter.update(self.mediatype, remaining, eta, total)
            self.postponed = None
        else: # got progress before widget created
            self.postponed = (remaining, eta, total)

    def _init_widget(self):
        """Hook that handles any updates that were waiting for the widget."""
        super(ProgressTrackingListMixin, self)._init_widget()
        if self.postponed:
            self.update_metadata_progress(*self.postponed)

class AnimationManager(object):
    """Base class for animations on item lists."""

    def __init__(self, item_list, item_views):
        self.currently_animating = set()
        self.item_list = item_list
        self.item_views = item_views

    def start(self, item_info):
        if item_info.id not in self.currently_animating:
            self.currently_animating.add(item_info.id)
            self.start_animation(item_info)
            timer.add(self.initial_delay(item_info), self._do_iteration,
                    item_info.id, self.repeat_delay(item_info))

    def _do_iteration(self, item_id, repeat_delay):
        try:
            item_info = self.item_list.get_item(item_id)
        except KeyError:
            # item was deleted from model
            self.currently_animating.remove(item_id)
            return
        rv = self.continue_animation(item_info)
        if rv != False:
            timer.add(repeat_delay, self._do_iteration, item_id, repeat_delay)
        else:
            self.currently_animating.remove(item_id)
            self.finish_animation(item_info)
        for view in self.item_views:
            view.model_changed()

    def initial_delay(self, item_info):
        """Delay between calls to start_animation() and continue_animation()
        """
        raise NotImplementedError()

    def repeat_delay(self, item_info):
        """Delay between additional continue_animation() calls"""
        raise NotImplementedError()

    def start_animation(self, item_info):
        """Begin the animation for an item."""
        pass

    def continue_animation(self, item_info):
        """Continue the animation.  Return False to stop."""
        pass

    def finish_animation(self, item_info):
        """Finish the animation"""
        pass


class ThrobberAnimationManager(AnimationManager):
    def initial_delay(self, item_info):
        return 0.2

    def repeat_delay(self, item_info):
        return 0.2

    def continue_animation(self, item_info):
        if item_info.state == 'downloading':
            self.item_list.update_throbber(item_info.id)
        else:
            self.item_list.finish_throbber(item_info.id)
            return False

class ItemListController(object):
    """Base class for controllers that manage list of items.

    :attribute widget: Container widget used to display this controller
    :attribute views: The ListView and StandardView objects
    """

    def __init__(self, typ, id_):
        """Construct a ItemListController.

        type and id are the same as in the constructor to
        messages.TrackItems
        """
        self.type = typ
        self.id = id_
        self.views = {}
        self._search_text = ''
        self._got_initial_list = False
        self._needs_scroll = None
        self._playing_items = False
        self._selection_to_restore = None
        self.config_change_handle = None
        self.show_resume_playing_button = False
        self.item_tracker = self.build_item_tracker()
        self._init_widget()
        self._check_for_initial_items()

        self._init_sort()
        self._init_item_views()
        self.initialize_search()
        self._item_tracker_callbacks = []
        self._playback_callbacks = []
        self.throbber_manager = ThrobberAnimationManager(self.item_list,
                self.all_item_views())

    def get_item_list(self):
        return self.item_tracker.item_list
    item_list = property(get_item_list)

    def on_displayed(self):
        self.shuffle_handle = None
        self.repeat_handle = None
        #We only want to show the shuffle/repeat status of the 
        #currently displayed playlist if there is no playback in 
        #progress. If there currently is playback we want to 
        #continue showing the shuffle/repeat status of the currently 
        #playing playlist.
        if not app.playback_manager.playlist:
            shuffle = app.widget_state.get_shuffle(self.type, self.id)
            app.playback_manager.set_shuffle(shuffle)
            repeat = app.widget_state.get_repeat(self.type, self.id)
            app.playback_manager.set_repeat(repeat)
        self.shuffle_handle = app.playback_manager.connect('update-shuffle',
                    self._handle_shuffle_update)
        self.repeat_handle = app.playback_manager.connect('update-repeat',
                    self._handle_repeat_update)
        self.stop_handle = app.playback_manager.connect('did-stop',
                    self._handle_playback_did_stop)
        self.config_change_handle = app.frontend_config_watcher.connect(
                'changed', self.on_config_change)

    def on_config_change(self, obj, key, value):
        if (key == prefs.RESUME_VIDEOS_MODE.key or
                key == prefs.RESUME_MUSIC_MODE.key or
                key == prefs.RESUME_PODCASTS_MODE.key):
            for view in self.all_item_views():
                view.queue_redraw()

    def _handle_shuffle_update(self, playback_manager, *args):
        if app.item_list_controller_manager.displayed == self:
            app.widget_state.set_shuffle(self.type, self.id, 
                    app.playback_manager.shuffle)

    def _handle_repeat_update(self, playback_manager, *args):
        if app.item_list_controller_manager.displayed == self:
            app.widget_state.set_repeat(self.type, self.id, 
                    app.playback_manager.repeat)

    def _handle_playback_did_stop(self, playback_manager):
        if app.item_list_controller_manager.displayed == self:
            #if playback stops we always want to load the shuffle/repeat 
            #of the current playlist
            shuffle = app.widget_state.get_shuffle(self.type, self.id)
            app.playback_manager.set_shuffle(shuffle)
            repeat = app.widget_state.get_repeat(self.type, self.id)
            app.playback_manager.set_repeat(repeat)

    def _init_sort(self):
        sorter = self.get_sorter()
        self.change_sort_indicators(sorter.KEY, sorter.is_ascending())
        self.item_list.set_sort(sorter)

    def get_saved_search_text(self):
        """Get the text we would use to create a saved search.

        By default we return None, subclasses should override it if they
        support saving searching.
        """
        return None

    def get_saved_search_source(self):
        """Get info we should use to save a search for the current item list.

        By default we return None.  Subclasses that support saved searches
        should override it and return a tuple in the form of (type, id)
        """
        return None

    def get_sorter(self):
        sort_key = app.widget_state.get_sort_state(self.type, self.id)
        column, ascending = self.parse_sort_key(sort_key)
        return self.make_sorter(column, ascending)

    def parse_sort_key(self, key):
        if key.startswith('-'):
            column = key[1:]
            ascending = False
        else:
            column = key
            ascending = True
        return column, ascending

    def make_sorter(self, column, ascending):
        try:
            sorter = itemlist.SORT_KEY_MAP[column](ascending)
        except KeyError:
            column = WidgetStateStore.DEFAULT_SORT_COLUMN[self.type]
            column, ascending = self.parse_sort_key(column)
            sorter = itemlist.SORT_KEY_MAP[column](ascending)
        return sorter

    def make_sort_key(self, sorter):
        key = unicode(sorter.KEY)
        if sorter.is_ascending():
            state = key
        else:
            state = u'-' + key
        return state

    def change_sort_indicators(self, sort_key, ascending):
        self.list_item_view.change_sort_indicator(sort_key, ascending)
        self.widget.toolbar.change_sort_indicator(sort_key, ascending)

    def _init_widget(self):
        toolbar = self.build_header_toolbar()
        self.selected_view = app.widget_state.get_selected_view(self.type,
                                                                self.id)
        self.widget = itemlistwidgets.ItemContainerWidget(toolbar,
                self.selected_view)

        self.build_widget()

        list_view = WidgetStateStore.get_list_view_type()
        self.views[list_view] = self.build_list_view()

        self.expand_or_contract_item_details()

        standard_view = WidgetStateStore.get_standard_view_type()
        scroll_pos = app.widget_state.get_scroll_position(
            self.type, self.id, standard_view)
        standard_view_widget = itemlistwidgets.StandardView(self.item_list,
                scroll_pos, self.build_renderer())
        self.views[standard_view] = standard_view_widget
        standard_view_background = widgetset.SolidBackground(
                standard_view_widget.BACKGROUND_COLOR)
        standard_view_background.add(widgetutil.pad(standard_view_widget,
            top=10, bottom=10))

        # set up member attrs to easily get our list/standard view widgets
        self.list_item_view = self.views[list_view]
        self.standard_item_view = self.views[standard_view]
        
        standard_view_scroller = widgetset.Scroller(False, True)
        standard_view_scroller.add(standard_view_background)
        self.widget.vbox[standard_view].pack_start(
                standard_view_scroller, expand=True)
        self.views[standard_view].set_scroller(standard_view_scroller)

        toolbar.connect_weak('sort-changed',
            self.on_sort_changed, standard_view)
        self.widget.item_details.expander_button.connect_weak('clicked',
                self.on_item_details_expander_clicked)
        self.list_item_view.connect_weak('sort-changed',
            self.on_sort_changed, list_view)
        self.titlebar.connect_weak('list-view-clicked',
            self.set_view, list_view)
        self.titlebar.connect_weak('normal-view-clicked',
            self.set_view, standard_view)
        self.titlebar.connect_weak('resume-playing', self.on_resume_playing)
        self.list_item_view.connect_weak('columns-enabled-changed',
            self.on_columns_enabled_changed, list_view)
        self.list_item_view.connect_weak('column-widths-changed',
            self.on_column_widths_changed, list_view)
        self.list_item_view.connect_weak('scroll-position-changed',
            self.on_scroll_position_changed, list_view)
        self.standard_item_view.connect_weak('scroll-position-changed',
            self.on_scroll_position_changed, standard_view)
        self.standard_item_view.renderer.signals.connect_weak(
                'throbber-drawn', self.on_throbber_drawn)

    def set_view(self, _widget, view):
        if view == self.selected_view:
            return
        # set selection for the view we will switch to
        current_view = self.current_item_view
        try:
            current_selection = current_view.get_selection_as_strings()
        except WidgetActionError:
            pass # don't bother setting the selection if we can't get it
        else:
            self.views[view].set_selection_as_strings(current_selection)
        # do the switch
        self.selected_view = view
        self.widget.switch_to_view(view)
        self.current_item_view.focus()
        # perform finishing touches
        app.widget_state.set_selected_view(self.type, self.id,
                                           self.selected_view)
        app.menu_manager.update_menus()
        self.expand_or_contract_item_details()

    def get_current_item_view(self):
        return self.views[self.selected_view]
    current_item_view = property(get_current_item_view)

    def all_item_views(self):
        return self.views.values()

    def focus_view(self):
        self.current_item_view.focus()
        if len(self.get_selection()) == 0:
            first = self.current_item_view.model.get_first_info()
            if first is not None:
                iter_ = self.current_item_view.model.iter_for_id(first.id)
                self.current_item_view.select(iter_)

    def build_widget(self):
        """Build the container widget for this controller."""
        raise NotImplementedError()

    def build_renderer(self):
        return itemrenderer.ItemRenderer(display_channel=False)

    def build_list_view(self):
        """Build the list view widget for this controller."""
        list_view_type = WidgetStateStore.get_list_view_type()
        list_view_columns = app.widget_state.get_columns_enabled(
                self.type, self.id, list_view_type)
        list_view_widths = app.widget_state.get_column_widths(
                self.type, self.id, list_view_type)
        scroll_pos = app.widget_state.get_scroll_position(
            self.type, self.id, list_view_type)
        column_renderers = self.build_column_renderers()
        list_view = itemlistwidgets.ListView(self.item_list, column_renderers,
                list_view_columns, list_view_widths, scroll_pos)
        scroller = widgetset.Scroller(True, True)
        scroller.add(list_view)
        self.widget.vbox[list_view_type].pack_start(scroller, expand=True)
        return list_view

    def build_column_renderers(self):
        return itemlistwidgets.ListViewColumnRendererSet()

    def build_header_toolbar(self):
        return itemlistwidgets.HeaderToolbar()

    def build_item_tracker(self):
        return itemtrack.ItemListTracker.create(self.type, self.id)

    def _check_for_initial_items(self):
        """Check if our the ItemList from our itemtrack already has items

        If so, simulate the getting the initial-list signal.
        """
        initial_items = self.item_list.get_items()
        if len(initial_items) > 0:
            # simulate getting the initial-list signal
            self.handle_items_will_change(self.item_tracker, initial_items,
                    [], [])
            self.handle_item_list(self.item_tracker, initial_items)

    def expand_or_contract_item_details(self):
        expanded = app.widget_state.get_item_details_expanded(
                self.selected_view)
        self.widget.item_details.set_expanded(expanded)

    def update_columns_enabled(self):
        list_view = WidgetStateStore.get_list_view_type()
        list_view_columns = app.widget_state.get_columns_enabled(
                self.type, self.id, list_view)
        list_view_widths = app.widget_state.get_column_widths(
                self.type, self.id, list_view)
        self.list_item_view.update_columns(list_view_columns,
            list_view_widths)

    def _init_item_views(self):
        self.context_menu_handler = self.make_context_menu_handler()
        context_callback = self.context_menu_handler.callback
        for view_type, item_view in self.views.items():
            item_view.connect_weak('selection-changed',
                    self.on_selection_changed, view_type)
            item_view.connect_weak('hotspot-clicked', self.on_hotspot_clicked)
            item_view.connect_weak('key-press', self.on_key_press)
            item_view.connect_weak('row-double-clicked',
                    self.on_row_double_clicked)
            item_view.connect_weak('row-activated', self.on_row_activated)
            item_view.set_context_menu_callback(context_callback)
            item_view.set_drag_source(self.make_drag_handler())

    def initialize_search(self):
        search = app.inline_search_memory.get_search(self.type, self.id)
        if search != '':
            self.titlebar.set_search_text(search)
            self.set_search(search)

    def get_selection(self):
        """Get the currently selected items.  Returns a list of
        ItemInfos.
        """
        item_view = self.current_item_view
        return [item_view.model[i][0] for i in
                item_view.get_selection()]

    def resume_play_selection(self, presentation_mode='fit-to-bounds'):
        self.play_selection(presentation_mode, force_resume=True)

    def play_selection(self, presentation_mode='fit-to-bounds',
            force_resume=False):
        """Play the currently selected items."""
        selection = self.get_selection()
        if len(selection) == 0:
            start_id = None
        elif len(selection) == 1:
            start_id = selection[0].id
        else:
            selected_ids = [i.id for i in selection]
            selected_ids.sort(key=self.item_list.model.index_of_id)
            start_id = selected_ids[0]
        self._play_item_list(start_id, presentation_mode,
                force_resume=force_resume)

    def play_items(self, presentation_mode='fit-to-bounds',
                   force_resume=False):
        self._play_item_list(None, presentation_mode, force_resume)

    def can_play_items(self):
        for info in self.item_list.model.info_list():
            if info.is_playable:
                return True
        return False

    def _play_item_list(self, start_id, presentation_mode='fit-to-bounds',
            force_resume=False):
        if start_id is None:
            start_info = None
        else:
            start_info = self.item_list.model.get_info(start_id)
        if start_info is None and not self.can_play_items():
            return
        app.playback_manager.stop()
        if start_info is not None and start_info.is_container_item:
            # If we play a container item, then switch to displaying it's
            # contents before playing (see #16178)
            app.display_manager.push_folder_contents_display(start_info,
                    start_playing=True)
            return
        self._playing_items = True
        app.playback_manager.start(start_id, self.item_tracker,
                presentation_mode, force_resume)
        shuffle = app.widget_state.get_shuffle(self.type, self.id)
        app.playback_manager.set_shuffle(shuffle)
        repeat = app.widget_state.get_repeat(self.type, self.id)
        app.playback_manager.set_repeat(repeat)

    def set_search(self, search_text):
        """Set the search for all ItemViews managed by this controller.
        """
        self._search_text = search_text
        if self.item_tracker:
            self.item_tracker.set_search(search_text)
        app.inline_search_memory.set_search(self.type, self.id, search_text)

    def _trigger_item(self, item_view, info):
        if info.downloaded:
            self._play_item_list(info.id)
        elif info.state == 'downloading':
            messages.PauseDownload(info.id).send_to_backend()
        elif info.state == 'paused':
            messages.ResumeDownload(info.id).send_to_backend()
        elif info.download_info is None:
            messages.StartDownload(info.id).send_to_backend()


    def on_row_double_clicked(self, item_view, iter_):
        info = item_view.model[iter_][0]
        self._trigger_item(item_view, info)

    def on_row_activated(self, item_view, iter_):
        info = item_view.model[iter_][0]
        if app.playback_manager.is_playing:
            app.playback_manager.play_pause()
        else:
            self._trigger_item(item_view, info)

    def on_sort_changed(self, object, sort_key, ascending, view):
        self.item_list_will_change()
        sorter = self.make_sorter(sort_key, ascending)
        self.item_list.set_sort(sorter)
        self.send_model_changed()
        self.change_sort_indicators(sort_key, ascending)
        sort_key = self.make_sort_key(sorter)
        app.widget_state.set_sort_state(self.type, self.id, sort_key)

    def on_resume_playing(self, titlebar):
        last_played_id = app.widget_state.get_last_played_item_id(self.type,
                self.id)
        if last_played_id is None:
            logging.warn("Resume playing clicked, but last_played_id is None")
            return
        if last_played_id:
            try:
                self.item_list.model.get_info(last_played_id)
            except KeyError:
                logging.warn("Resume playing clicked, but last_played_info "
                        "not found")
                return
        self._play_item_list(last_played_id, force_resume=True)

    def on_item_details_expander_clicked(self, button):
        expand = button.click_should_expand()
        self.widget.item_details.set_expanded(expand)
        app.widget_state.set_item_details_expanded(self.selected_view, expand)

    def on_columns_enabled_changed(self, object, columns, view_type):
        app.widget_state.set_columns_enabled(
                self.type, self.id, view_type, columns)

    def on_column_widths_changed(self, object, widths, view_type):
        app.widget_state.update_column_widths(
                self.type, self.id, view_type, widths)

    def on_scroll_position_changed(self, object, scroll_pos, view_type):
        app.widget_state.set_scroll_position(
                self.type, self.id, view_type, scroll_pos)

    def on_throbber_drawn(self, signaler, item_info):
        self.throbber_manager.start(item_info)

    def on_key_press(self, view, key, mods):
        if key == menus.DELETE or key == menus.BKSPACE:
            return self.handle_delete()
        elif key == menus.ESCAPE:
            return self.handle_escape()
        elif key == menus.ENTER:
            self.play_selection()
            return True
        elif isinstance(key, basestring) and len(key) == 1 and key.isalnum():
            self.titlebar.start_editing_search(key)
            return True
        return False

    def handle_delete(self):
        app.widgetapp.remove_items(self.get_selection())
        return True

    def handle_escape(self):
        handled = False
        for info in self.get_selection():
            if info.state == 'downloading':
                messages.CancelDownload(info.id).send_to_backend()
                handled = True
            elif info.state == 'uploading':
                messages.StopUpload(info.id).send_to_backend()
                handled = True
        return handled

    def on_hotspot_clicked(self, itemview, name, iter_):
        """Hotspot handler for ItemViews."""

        item_info, attrs = itemview.model[iter_]
        if name == 'download':
            if item_info.remote:
                name = 'download-sharing-item'
            elif item_info.device:
                name = 'download-device-item'
        if name in ('download', 'thumbnail-download'):
            messages.StartDownload(item_info.id).send_to_backend()
        elif name == 'pause':
            messages.PauseDownload(item_info.id).send_to_backend()
        elif name == 'resume':
            messages.ResumeDownload(item_info.id).send_to_backend()
        elif name == 'cancel':
            messages.CancelDownload(item_info.id).send_to_backend()
        elif name == 'keep':
            messages.KeepVideo(item_info.id).send_to_backend()
            app.saved_items.add(item_info.id)
        elif name == 'stop_seeding':
            messages.StopUpload(item_info.id).send_to_backend()
        elif name == 'start_seeding':
            messages.StartUpload(item_info.id).send_to_backend()
        elif name == 'delete':
            app.widgetapp.remove_items(selection=[item_info])
        elif name == 'remove':
            messages.RemoveVideosFromPlaylist(
                itemview.playlist_id, [item_info.id]).send_to_backend()
        elif name == 'visit_webpage':
            app.widgetapp.open_url(item_info.permalink)
        elif name == 'visit_comments':
            app.widgetapp.open_url(item_info.commentslink)
        elif name == 'visit_filelink':
            app.widgetapp.open_url(item_info.file_url)
        elif name == 'visit_license':
            app.widgetapp.open_url(item_info.license)
        elif name == 'show_local_file':
            app.widgetapp.check_then_reveal_file(item_info.video_path)
        elif name == 'show_contents':
            app.display_manager.push_folder_contents_display(item_info)
        elif name == 'cancel_auto_download':
            messages.CancelAutoDownload(item_info.id).send_to_backend()
        elif name.startswith('description-link:'):
            url = name.split(':', 1)[1]
            try:
                base_href = widgetutil.get_feed_info(
                    item_info.feed_id).base_href
            except KeyError:
                logging.warn("Feed not present when clicking link (%s)",
                        item_info.feed_id)
                # Feed is not around anymore for some reason (#13310).
                # Try without base_href
            else:
                url = urljoin(base_href, url)
            if subscription.is_subscribe_link(url):
                messages.SubscriptionLinkClicked(url).send_to_backend()
            else:
                app.widgetapp.open_url(url)
        elif name in ('play', 'thumbnail-play'):
            self._play_item_list(item_info.id)
        elif name == 'play_pause':
            app.playback_manager.play_pause()
        elif name.startswith('rate:'):
            rating = int(name.split(':', 1)[1])
            messages.RateItem(item_info, rating).send_to_backend()
        elif name == 'download-device-item':
            messages.DownloadDeviceItems([item_info]).send_to_backend()
        elif name == 'download-sharing-item':
            messages.DownloadSharingItems([item_info]).send_to_backend()
        else:
            logging.debug("ItemView doesn't know how to handle hotspot %s.",
                name)

    def on_selection_changed(self, item_view, view_type):
        app.menu_manager.update_menus()
        self.update_item_details()

    def update_item_details(self):
        try:
            selection = self.get_selection()
        except ActionUnavailableError, error:
            logging.debug("no item details: %s", error.reason)
            self.widget.item_details.clear()
        else:
            if selection:
                # any selected info will do, just pick the first one in the list
                self.widget.item_details.set_info(selection[0])
            else:
                self.widget.item_details.clear()

    def get_selected_ids(self):
        return [info.id for info in self.get_selection()]

    def restore_selected_ids(self, selected_ids):
        iters = []
        for id_ in selected_ids:
            try:
                iters.append(self.item_list.get_iter(id_))
            except KeyError:
                # item was removed since we saved the selection, no big deal
                pass
        self.current_item_view.set_selection(iters)

    def save_selection(self):
        app.widget_state.set_selection(self.type, self.id,
                self.get_selected_ids())

    def restore_selection(self):
        selection = app.widget_state.get_selection(self.type, self.id)
        if selection:
            self.restore_selected_ids(selection)

    def start_tracking(self):
        """Send the message to start tracking items."""
        self.track_item_lists()
        self.track_playback()

    def stop_tracking(self):
        """Send the message to stop tracking items."""
        self.cancel_track_item_lists()
        self.cancel_track_playback()

    def track_item_lists(self):
        if self._item_tracker_callbacks:
            raise AssertionError("called track_item_lists() twice")
        self.item_tracker.set_search(self._search_text)
        self._item_tracker_callbacks = [
            self.item_tracker.connect("items-will-change",
                self.handle_items_will_change),
            self.item_tracker.connect("initial-list", self.handle_item_list),
            self.item_tracker.connect("items-changed",
                self.handle_items_changed),
        ]

    def cancel_track_item_lists(self):
        if self.item_tracker is None:
            return # never started tracking
        for handle in self._item_tracker_callbacks:
            self.item_tracker.disconnect(handle)
        self.item_tracker = None
        self._item_tracker_callbacks = []

    def track_playback(self):
        self._playback_callbacks.extend([
            app.playback_manager.connect('selecting-file',
                self._on_playback_change),
            app.playback_manager.connect('will-stop',
                self._playback_will_stop),
            app.playback_manager.connect('will-play',
                self._playback_will_play),
        ])

    def cancel_track_playback(self):
        for handle in self._playback_callbacks:
            app.playback_manager.disconnect(handle)
        self._playback_callbacks = []

    def _on_playback_change(self, playback_manager, *args):
        # The currently playing item has changed, redraw the view to
        # change which item gets the "currently playing" badge.
        if self._playing_items:
            for item_view in self.all_item_views():
                item_view.queue_redraw()

    def _playback_will_stop(self, playback_manager):
        self._on_playback_change(playback_manager)
        self._playing_items = False
        self.update_resume_button()

    def _playback_will_play(self, playback_manager, duration):
        if self._playing_items:
            item = playback_manager.get_playing_item()
            app.widget_state.set_last_played_item_id(self.type, self.id,
                    item.id)
            self.update_resume_button()

    def item_list_will_change(self):
        """Call this before making any changes to the item list.  """
        # Remember our current selection.  If we are adding/removing items
        # from the list, we may lose it.
        self._selection_to_restore = self.get_selected_ids()
        # forget the selection for now.  GTK has code that tries to preserve
        # the selection.  That's wasted effort since we do the same thing.
        self.current_item_view.unselect_all()

    def start_bulk_change(self):
        for item_view in self.all_item_views():
            item_view.start_bulk_change()

    def send_model_changed(self):
        for item_view in self.all_item_views():
            item_view.model_changed()
        if self._selection_to_restore is not None:
            self.restore_selected_ids(self._selection_to_restore)
            self._selection_to_restore = None
        else:
            app.widgetapp.handle_soft_failure('send_model_changed()',
                    "_selection_to_restore was not set", with_exception=False)

    def handle_items_will_change(self, obj, added, changed, removed):
        self.item_list_will_change()
        if len(added) + len(removed) > 100:
            # Lots of changes are happening, so call start_bulk_change() to
            # speed things up.  The reason we don't call this always is that
            # it looses the scroll position on GTK.  But when lots of rows are
            # changing, trying to keep the scroll position is pointless.
            self.start_bulk_change()
        self.on_items_will_change(added, changed, removed)

    def handle_item_list(self, obj, items):
        """Handle an ItemList message meant for this ItemContainer."""
        self.handle_item_list_changes()
        self._got_initial_list = True
        if self._needs_scroll:
            self.scroll_to_item(self._needs_scroll)
            self._needs_scroll = None
        self.restore_selection()
        self.on_initial_list()

    def handle_items_changed(self, obj, added, changed, removed):
        """Handle an ItemsChanged message meant for this ItemContainer."""
        self.handle_item_list_changes()
        self.on_items_changed()

    def handle_item_list_changes(self):
        """Called whenever our ItemList changes."""
        self.send_model_changed()
        self.update_resume_button()
        self.update_count_label()
        self.update_item_details()
        self.check_for_empty_list()

    def check_for_empty_list(self):
        list_empty = (self.item_list.get_count() == 0 and
                self.item_tracker.is_filtering())
        self.widget.set_list_empty_mode(list_empty)

    def update_resume_button(self):
        if not self.show_resume_playing_button:
            return
        last_played_id = app.widget_state.get_last_played_item_id(self.type,
                self.id)
        last_played = None
        if last_played_id:
            try:
                last_played = self.item_list.model.get_info(last_played_id)
            except KeyError:
                pass
        if (last_played is None or not last_played.is_playable or
                self._playing_items):
            self.titlebar.update_resume_button(None, None)
        else:
            self.titlebar.update_resume_button(last_played.name,
                    last_played.resume_time)

    def update_count_label(self):
        _("%(count)s items", {'count': self.item_list.get_count()})
        # FIXME: need to have a place to put this text

    def on_items_will_change(self, added, changed, removed):
        """Called before we change the list.

        Subclasses can override this method if they want.
        """
        pass

    def on_initial_list(self):
        """Called after we have receieved the initial list of items.

        Subclasses can override this method if they want.
        """
        pass

    def on_items_changed(self):
        """Called after we have changes to items

        Subclasses can override this method if they want.
        """
        pass

    def make_context_menu_handler(self):
        return itemcontextmenu.ItemContextMenuHandler()

    def make_drag_handler(self):
        return ItemListDragHandler()

    def no_longer_displayed(self):
        if self._got_initial_list:
            # rember our selection, but only if we had a chance to call
            # restore_selection() on the initial item list.
            self.save_selection()
        for view in self.views:
            self.views[view].on_undisplay()
        if self.shuffle_handle:
            app.playback_manager.disconnect(self.shuffle_handle)
        if self.repeat_handle:
            app.playback_manager.disconnect(self.repeat_handle)
        if self.stop_handle:
            app.playback_manager.disconnect(self.stop_handle)
        if self.config_change_handle:
            app.frontend_config_watcher.disconnect(self.config_change_handle)
            self.config_change_handle = None

    def scroll_to_item(self, item):
        if self._got_initial_list:
            iter = self.current_item_view.model.iter_for_id(item.id)
            try:
                self.current_item_view.scroll_to_iter(iter)
            except KeyError:
                # item no longer in the list, so we'll ignore it
                pass
        else:
            self._needs_scroll = item

class SimpleItemListController(ItemListController):
    def __init__(self):
        ItemListController.__init__(self, self.type, self.id)

    def build_widget(self):
        self.titlebar = self.make_titlebar()
        self.titlebar.switch_to_view(self.widget.selected_view)
        self.widget.titlebar_vbox.pack_start(self.titlebar)

    def make_titlebar(self):
        titlebar = itemlistwidgets.ItemListTitlebar()
        titlebar.connect('search-changed', self._on_search_changed)
        return titlebar

    def _on_search_changed(self, widget, search_text):
        self.set_search(search_text)

class SearchController(SimpleItemListController):
    type = u'search'
    id = u'search'

    def build_widget(self):
        SimpleItemListController.build_widget(self)
        text = _('No Results')
        self.widget.list_empty_mode_vbox.pack_start(
                itemlistwidgets.EmptyListHeader(text))

        text = _('To search for media on Internet media search sites, click '
                 'on the search box above, type in search terms and hit '
                 'the Enter key.  To switch search engines, click on the '
                 'icon to the left of the search box above and select the '
                 'search engine from the drop down.')
        self.widget.list_empty_mode_vbox.pack_start(
                itemlistwidgets.EmptyListDescription(text))


    def initialize_search(self):
        if app.search_manager.text != '':
            self.titlebar.set_search_text(app.search_manager.text)
        self.titlebar.set_search_engine(app.search_manager.engine)

    def on_initial_list(self):
        if ((not app.search_manager.searching
             and self.item_list.get_count() == 0)):
            self.widget.set_list_empty_mode(True)

    def on_items_changed(self):
        # Don't check for an empty list here.  Since items don't get
        # removed from the search feed, we don't need to do anything.
        # Also, it results in a false positive just after the search
        # starts when the items from the last search get removed
        # (#11255)
        pass

    def make_titlebar(self):
        titlebar = itemlistwidgets.SearchListTitlebar()
        titlebar.connect('save-search', self._on_save_search)
        return titlebar

    def get_saved_search_text(self):
        return self.titlebar.get_search_text()

    def get_saved_search_source(self):
        return 'search', self.titlebar.get_engine()

    def _on_save_search(self, widget, search_text):
        engine = self.titlebar.get_engine()
        app.search_manager.perform_search(engine, search_text)
        app.search_manager.save_search()

    def start_tracking(self):
        SimpleItemListController.start_tracking(self)
        self._started_handle = app.search_manager.connect('search-started',
                self._on_search_started)
        self._complete_handle = app.search_manager.connect('search-complete',
                self._on_search_complete)

    def stop_tracking(self):
        SimpleItemListController.stop_tracking(self)
        app.search_manager.disconnect(self._started_handle)
        app.search_manager.disconnect(self._complete_handle)

    def _on_search_started(self, search_manager):
        self.titlebar.set_search_text(search_manager.text)
        self.titlebar.set_search_engine(search_manager.engine)
        self.widget.set_list_empty_mode(False)

    def _on_search_complete(self, search_manager, result_count):
        if result_count == 0:
            self.widget.set_list_empty_mode(True)

class AudioVideoItemsController(SimpleItemListController, FilteredListMixin,
        ProgressTrackingListMixin):
    def __init__(self):
        SimpleItemListController.__init__(self)
        FilteredListMixin.__init__(self)
        ProgressTrackingListMixin.__init__(self)

    def build_widget(self):
        SimpleItemListController.build_widget(self)

        # this only gets shown when the user is searching for things
        # in the feed and there are no results.
        text = _('No Results')
        self.widget.list_empty_mode_vbox.pack_start(
                itemlistwidgets.EmptyListHeader(text))

    def make_titlebar(self):
        titlebar = self.titlebar_class()
        titlebar.connect('search-changed', self._on_search_changed)
        titlebar.connect('toggle-filter', self.on_toggle_filter)
        titlebar.connect('save-search', self._on_save_search)
        return titlebar

    def _on_save_search(self, titlebar, query):
        items = self.item_list.get_items()
        ids = [s.id for s in items if s.downloaded]
        title = _('Create Playlist')
        description = _('Enter a name for the new playlist')
        playlist_name = dialogs.ask_for_string(title, description,
                initial_text=query)
        if not playlist_name:
            return
        messages.NewPlaylist(playlist_name, ids).send_to_backend()

    def _on_search_changed(self, widget, search_text):
        self.set_search(search_text)

        # if the search has no results, we show the empty_mode
        # which says "no results"
        if self.item_list.get_count() == 0 and search_text:
            self.widget.set_list_empty_mode(True)
        else:
            self.widget.set_list_empty_mode(False)

class VideoItemsController(AudioVideoItemsController):
    type = u'videos'
    id = u'videos'
    unwatched_label =  _('Unwatched')
    titlebar_class = itemlistwidgets.VideosTitlebar

    def build_renderer(self):
        return itemrenderer.ItemRenderer(display_channel=True, wide_image=True)

    def build_header_toolbar(self):
        return itemlistwidgets.VideosHeaderToolbar()

class AudioItemsController(AudioVideoItemsController):
    type = u'music'
    id = u'music'
    unwatched_label = _('Unplayed')
    titlebar_class = itemlistwidgets.MusicTitlebar

    def build_renderer(self):
        return itemrenderer.ItemRenderer(display_channel=True)

class OtherItemsController(SimpleItemListController):
    type = u'others'
    id = u'others'

    def build_widget(self):
        SimpleItemListController.build_widget(self)

        # this only gets shown when the user is searching for things
        # in the feed and there are no results.
        text = _('No Results')
        self.widget.list_empty_mode_vbox.pack_start(
                itemlistwidgets.EmptyListHeader(text))

    def build_renderer(self):
        return itemrenderer.ItemRenderer(display_channel=True)

    def _on_search_changed(self, widget, search_text):
        self.set_search(search_text)

        # if the search has no results, we show the empty_mode
        # which says "no results"
        if self.item_list.get_count() == 0 and search_text:
            self.widget.set_list_empty_mode(True)
        else:
            self.widget.set_list_empty_mode(False)

class FolderContentsController(SimpleItemListController):
    """Controller object for feeds."""

    def __init__(self, folder_info, play_initial_list):
        self.type = u'folder-contents'
        self.id = folder_info.id
        self.info = folder_info
        self.play_initial_list = play_initial_list
        SimpleItemListController.__init__(self)

    def make_titlebar(self):
        titlebar = itemlistwidgets.FolderContentsTitlebar()
        titlebar.connect('search-changed', self._on_search_changed)
        titlebar.connect('podcast-clicked', self._on_podcast_clicked)
        return titlebar

    def _on_podcast_clicked(self, titlebar, button):
        app.display_manager.pop_display()

    def on_initial_list(self):
        SimpleItemListController.on_initial_list(self)
        if self.play_initial_list:
            self.play_items()

class ItemListControllerManager(object):
    """Manages ItemListController objects.

    Attributes:

    :attribute displayed: Currently displayed ItemListController or
        None (this one is currently being displayed in the right-hand
        side)
    :attribute controllers: Mapping of controller identifiers to controllers.
        (these are somewhere in the display stack, but not necessarily
        displayed currently).
    """

    def __init__(self):
        self.displayed = None
        self.controllers = {}

    def focus_view(self):
        """Focus the currently displayed item list.

        :returns: True if we successfully focused the item list.
        """
        if self.displayed:
            self.displayed.focus_view()
            return True
        else:
            return False

    def get_view(self):
        """Get the currently displayed item list widget.

        :returns: A Widget or None if no item lists are displayed
        """
        if self.displayed:
            return self.displayed.current_item_view
        else:
            return None

    def get_searchbox_view(self):
        """Get the search entry for the currently displayed item list.

        :returns: A Widget or None if no item lists are displayed
        """
        if self.displayed:
            return self.displayed.titlebar.searchbox
        else:
            return None

    def get_saved_search_text(self):
        """Get the saved search text for the current item list.
        """
        if self.displayed:
            return self.displayed.get_saved_search_text()
        else:
            return None

    def get_saved_search_source(self):
        """Get the saved search source info for the current item list
        """
        if self.displayed:
            return self.displayed.get_saved_search_source()
        else:
            return None

    def controller_displayed(self, item_list_controller):
        self.displayed = item_list_controller
        self.displayed.on_displayed()

    def controller_no_longer_displayed(self, item_list_controller):
        if item_list_controller is not self.displayed:
            logging.warn("controller is not displayed in "
                    "controller_no_longer_displayed()")
        else:
            self.displayed.no_longer_displayed()
        self.displayed = None

    @staticmethod
    def _key_for_controller(controller):
        if controller.type == 'music':
            key = ('library', 'audio')
        elif controller.type == 'videos':
            key = ('library', 'video')
        elif controller.type.startswith('device-'):
            key = ('device', controller.device.id)
        else:
            # there are others, but none of them need special handling
            key = (None, controller.id)
        return key

    def controller_created(self, item_list_controller):
        key = self._key_for_controller(item_list_controller)
        self.controllers[key] = item_list_controller

    def controller_destroyed(self, item_list_controller):
        key = self._key_for_controller(item_list_controller)
        del self.controllers[key]

    def play_selection(self, presentation_mode='fit-to-bounds'):
        if self.displayed is not None:
            self.displayed.play_selection(presentation_mode)

    def get_selection(self):
        if self.displayed is None:
            return []
        else:
            return self.displayed.get_selection()

    def can_play_items(self):
        """Can we play any items currently?"""
        return self.displayed and self.displayed.can_play_items()

    def undisplay_controller(self):
        if self.displayed:
            self.controller_no_longer_displayed(self.displayed)

    def update_metadata_progress(self, target, remaining, eta, total):
        if target not in self.controllers:
            # devices can have this process started without a controller
            return
        self.controllers[target].update_metadata_progress(remaining, eta, total)
