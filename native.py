#!/usr/bin/python3
"""Native GTK4 desktop client. Collects telemetry directly; no web server."""
import os
import sys

if sys.platform != 'linux':
    raise SystemExit('TaskManager currently requires Linux (x86-64 or ARM64) and GTK4.')

# Configure GTK before importing it: a telemetry UI needs no GPU rendering.
# GTK 4.14 uses GDK_DEBUG; newer releases use GDK_DISABLE.
os.environ['GSK_RENDERER'] = 'cairo'
for variable, required in (
    ('GDK_DEBUG', ('gl-disable', 'vulkan-disable', 'dmabuf-disable')),
    ('GDK_DISABLE', ('gl', 'vulkan', 'dmabuf')),
):
    existing = [value for value in os.environ.get(variable, '').split(',') if value]
    os.environ[variable] = ','.join(dict.fromkeys(existing + list(required)))

import threading
import colorsys
import math
import shlex
from pathlib import Path
from collections import deque
import gi
gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gdk, Gio, GLib, GObject
from metrics import Monitor

APP_ID = 'io.github.PuChanHCI.TaskManager'
VERSION = '1.0.0'

def size(value):
    if value is None:
        return 'Unavailable'
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if abs(value) < 1024 or unit == 'TiB':
            return f'{value:.1f} {unit}'
        value /= 1024

def percent(value):
    return 'Unavailable' if value is None else f'{value:.1f}%'

def network_cells(item):
    return [item['name'], item['state'].capitalize(), size(item['rx']), size(item['tx']), size(item['received']), size(item['sent'])]


def filesystem_cells(item):
    return [item['device'], item['mount'], item['type']+(' (ro)' if item['readonly'] else ''),
            size(item['total']), size(item['used']), size(item['available']), percent(item['percent'])]


def gpu_process_cells(process, host):
    types = {'C':'Compute', 'G':'Graphics', 'C+G':'Compute + graphics', 'M':'Mixed'}
    values = [process['pid'], process['user'], process['gpu'], types.get(process['type'],process['type']),
              process.get('activity'), process.get('memory'), process.get('engines', {}).get('encode'),
              process.get('engines', {}).get('decode'), host.get('cpu'), host.get('memory'), process['name']]
    display = [str(value) if value is not None else '—' for value in values]
    for index in (4, 6, 7, 8):
        display[index] = percent(values[index]) if values[index] is not None else '—'
    for index in (5, 9):
        display[index] = size(values[index]) if values[index] is not None else '—'
    return display, values


class ResourceAlerts:
    """High-load status with separate enter/clear thresholds to avoid flicker."""
    ENTER = 90.0
    CLEAR = 85.0

    def __init__(self):
        self.states = {}

    def update(self, data, gpu):
        def peak(values):
            valid = [v for v in values if isinstance(v, (int, float)) and math.isfinite(v)]
            return max(valid, default=None)
        network = []
        for interface in data.get('network', []):
            speed = interface.get('speed_mbps')
            if speed is None or speed <= 0 or interface.get('state') != 'up':
                continue
            traffic = interface['rx']+interface['tx'] if interface.get('duplex') == 'half' else max(interface['rx'], interface['tx'])
            network.append(traffic*8/(speed*1e6)*100)
        gpu_levels = [gpu.get('utilization'), *gpu.get('engines', {}).values()]
        total, used = gpu.get('memory_total_mib'), gpu.get('memory_used_mib')
        if total is not None and total > 0 and used is not None:
            gpu_levels.append(used/total*100)
        levels = {'CPU':data['cpu']['percent'], 'Memory':data['memory']['percent'],
                  'GPU':peak(gpu_levels), 'Disk':peak([item['active'] for item in data.get('disks', [])]),
                  'Network':peak(network)}
        descriptions = {'CPU':'Total CPU utilization', 'Memory':'System memory in use',
                        'GPU':'Highest GPU utilization, engine activity or reported GPU memory usage',
                        'Disk':'Busiest disk active time', 'Network':'Busiest known physical link utilization'}
        results = {}
        for name, value in levels.items():
            key = 'GPU:'+gpu.get('id', 'none') if name == 'GPU' else name
            if value is None or not math.isfinite(value):
                high = False
                detail = descriptions[name]+': unavailable'
            else:
                high = value >= self.ENTER or (self.states.get(key, False) and value >= self.CLEAR)
                detail = descriptions[name]+f': {value:.1f}%'
            self.states[key] = high
            results[name] = (high, detail+f'. High at {self.ENTER:g}%; clears below {self.CLEAR:g}%.')
        return results


def label(text='', css=None):
    widget = Gtk.Label(label=text, xalign=0)
    if css:
        widget.add_css_class(css)
    return widget

def box(spacing=12, horizontal=False):
    return Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL if horizontal else Gtk.Orientation.VERTICAL, spacing=spacing)

def clear(widget):
    while widget.get_first_child():
        widget.remove(widget.get_first_child())

CSS = b'''
window { background: #0d1117; color: #e6edf3; }
headerbar { background: #141b24; color: #e6edf3; border-bottom: 1px solid #263243; box-shadow: none; min-height: 48px; }
headerbar .title { font-size: 14px; }
.sidebar { background: #111821; padding: 24px 14px 18px; border-right: 1px solid #253042; }
.brand { font-size: 19px; font-weight: 700; letter-spacing: -0.5px; }
.brand-icon { color: #78b7ff; }
.section-label { color: #71839c; font-size: 10px; font-weight: 700; letter-spacing: 1.5px; }
.navigation { border: 0; background: transparent; border-radius: 8px; padding: 12px; color: #94a3b8; box-shadow: none; }
.navigation:hover { background: #1c2735; color: #e6edf3; }
.navigation:checked { background: #1b3552; color: #99caff; box-shadow: inset 3px 0 #73b6ff; }
.content { padding: 26px 30px; }
.title { font-size: 28px; font-weight: 650; letter-spacing: -0.7px; }
.subtitle { color: #91a2b8; font-size: 12px; }
.card { background: #151e2a; border: 1px solid #29384c; border-radius: 10px; padding: 14px; }
.value { font-size: 24px; font-weight: 600; color: #a5d0ff; letter-spacing: -0.5px; }
.resource { background: #151e2a; border: 1px solid #29384c; border-radius: 10px; padding: 13px 16px; box-shadow: none; }
.resource:hover { background: #1a293b; border-color: #4d6b91; }
.resource:checked { background: #19324c; border-color: #71b2ff; }
.resource-title { color: #9eafc4; font-size: 11px; font-weight: 600; }
.resource-number { font-size: 23px; font-weight: 600; color: #e6edf3; letter-spacing: -0.5px; }
.resource.high-load { background: #481e29; border-color: #ff6b78; }
.resource.high-load:hover { background: #592432; border-color: #ff9ba3; }
.resource.high-load:checked { background: #632733; border-color: #ffa0a8; box-shadow: inset 0 -3px #ff6b78; }
.resource.high-load .resource-title { color: #ffb1b8; }
.resource.high-load .resource-number { color: #ffe0e3; }
.widget-mode { border: 1px solid #33465f; }
.widget-content { padding: 10px; }
.widget-mode .resource { padding: 6px 9px; border-radius: 6px; }
.widget-mode .resource-title { font-size: 9px; }
.widget-mode .resource-number { font-size: 15px; letter-spacing: 0; }
.status { border-radius: 20px; background: #14352e; color: #82dbb1; padding: 5px 12px; font-size: 11px; }
.status.paused { background: #3b3020; color: #edc079; }
button { color: #dce6f3; border-radius: 7px; }
button:focus-visible, entry:focus-visible { outline: 2px solid #70adf5; outline-offset: 2px; }
.warning { color: #edbf79; }
columnview { background: #111a26; color: #dce6f3; border: 1px solid #29384c; border-radius: 8px; }
columnview header button { background: #1a2636; color: #a3b6ce; font-size: 11px; font-weight: 600; border: 0; border-bottom: 1px solid #33455d; padding: 10px; border-radius: 0; box-shadow: none; }
columnview row:nth-child(even) { background: #15202e; }
columnview row:hover { background: #20344c; }
columnview cell { padding: 9px 11px; font-size: 12px; }
entry, dropdown button { background: #182434; color: #e6edf3; border: 1px solid #34475e; border-radius: 8px; padding: 8px; }
progressbar trough { min-height: 8px; border-radius: 4px; background: #26394f; }
progressbar progress { background: #71b5ff; border-radius: 4px; }
scrollbar { background: transparent; }
scrollbar slider { background: #40566e; min-width: 6px; min-height: 30px; border-radius: 8px; }
'''

class TableRow(GObject.Object):
    def __init__(self, values, icon=None, sort_values=None):
        super().__init__()
        self.values = values
        self.icon = icon
        self.sort_values = sort_values if sort_values is not None else values

def compare_rows(left, right, index):
    a, b = left.sort_values[index], right.sort_values[index]
    if a is None or b is None:
        return Gtk.Ordering((a is not None) - (b is not None))
    if isinstance(a, str):
        a = a.casefold()
    if isinstance(b, str):
        b = b.casefold()
    return Gtk.Ordering((a > b) - (a < b))


def make_sortable(view, store, default_column=0, descending=False):
    columns = view.get_columns()
    for index in range(columns.get_n_items()):
        columns.get_item(index).set_sorter(Gtk.CustomSorter.new(lambda a, b, unused=None, n=index: compare_rows(a, b, n)))
    sorted_model = Gtk.SortListModel(model=store, sorter=view.get_sorter())
    view.set_model(Gtk.NoSelection(model=sorted_model))
    view.sort_by_column(columns.get_item(default_column), Gtk.SortType.DESCENDING if descending else Gtk.SortType.ASCENDING)


class ProcessIcons:
    """Resolve installed application icons without launching applications."""
    def __init__(self):
        self.by_executable = {}
        self.by_name = {}
        self.fallback = Gio.ThemedIcon.new('application-x-executable-symbolic')
        self.terminal = Gio.ThemedIcon.new('utilities-terminal-symbolic')
        self.service = Gio.ThemedIcon.new('system-run-symbolic')
        wrappers = {'env', 'sh', 'bash', 'python', 'python3', 'flatpak', 'snap'}
        for app in Gio.AppInfo.get_all():
            icon = app.get_icon()
            if icon is None:
                continue
            executable = app.get_executable() or ''
            try:
                executable = shlex.split(executable)[0] if executable else ''
            except ValueError:
                continue
            basename = Path(executable).name
            if basename and basename not in wrappers:
                self.by_executable.setdefault(basename.lower(), icon)
                self.by_executable.setdefault(executable, icon)
            desktop_id = (app.get_id() or '').removesuffix('.desktop')
            for name in (desktop_id, desktop_id.rsplit('.', 1)[-1], app.get_name()):
                if name:
                    self.by_name.setdefault(name.lower(), icon)

    def resolve(self, process):
        executable = process.get('executable', '')
        name = process['name'].lower()
        basename = Path(executable).name.lower() if executable else ''
        for key in (executable, basename, name):
            if key in self.by_executable:
                return self.by_executable[key]
            if key in self.by_name:
                return self.by_name[key]
        if name in {'bash', 'sh', 'zsh', 'fish', 'dash', 'sudo', 'ssh'}:
            return self.terminal
        if not executable or name.startswith(('systemd', 'kworker', 'rcu_', 'migration/')):
            return self.service
        return self.fallback


def setup_process_name(factory, item):
    row = box(10, True)
    image = Gtk.Image()
    image.set_pixel_size(20)
    row.append(image)
    row.append(label())
    item.set_child(row)


def bind_process_name(factory, item, index=0):
    row, process = item.get_child(), item.get_item()
    image = row.get_first_child()
    image.set_from_gicon(process.icon)
    image.get_next_sibling().set_label(process.values[index])


class HistoryChart(Gtk.DrawingArea):
    COLORS = {'CPU': (.40,.71,1), 'Memory': (.73,.59,.93), 'GPU': (.4,.82,.66), 'Disk': (.9,.72,.43), 'Network': (.94,.59,.72)}
    def __init__(self):
        super().__init__()
        self.samples = deque(maxlen=60)
        self.resource = 'CPU'
        self.set_content_height(240)
        self.set_hexpand(True)
        self.set_draw_func(self.draw)

    def draw(self, area, cr, width, height):
        cr.set_source_rgb(.07,.10,.14)
        cr.paint()
        cr.set_source_rgb(.16,.22,.29)
        cr.set_line_width(1)
        for i in range(13):
            cr.move_to(width*i/12, 0)
            cr.line_to(width*i/12, height)
        for i in range(5):
            cr.move_to(0, height*i/4)
            cr.line_to(width, height*i/4)
        cr.stroke()
        if self.resource == 'CPU' and getattr(self, 'cpu_mode', 'cores') == 'cores':
            draw_cpu_history(cr, width, height, self.samples)
            return
        keys = ['rx','tx'] if self.resource == 'Network' else [self.resource]
        values = [s.get(key) for s in self.samples for key in keys]
        scale = max(1024, *[v for v in values if v is not None])*1.1 if self.resource == 'Network' else 100
        for n,key in enumerate(keys):
            cr.set_source_rgb(*self.COLORS[self.resource])
            cr.set_line_width(2)
            cr.set_dash([5,4] if n else [])
            started = False
            for i,sample in enumerate(self.samples):
                value = sample.get(key)
                if value is None:
                    started = False
                    continue
                x = (60-len(self.samples)+i)*width/59
                y = height-min(1,max(0,value)/scale)*height
                if started:
                    cr.line_to(x,y)
                else:
                    cr.move_to(x,y)
                    started = True
            cr.stroke()
        cr.set_dash([])
        cr.set_source_rgb(.6,.69,.8)
        cr.set_font_size(11)
        cr.move_to(10,18)
        cr.show_text(size(scale)+'/s (receive / send)' if self.resource == 'Network' else '100%')
        if self.resource == 'GPU' and (not self.samples or self.samples[-1].get('GPU') is None):
            cr.move_to(16, height/2)
            cr.show_text('GPU utilization is unavailable')

def core_color(index):
    return colorsys.hsv_to_rgb((index * .61803398875) % 1, .52, .96)


def draw_cpu_history(cr, width, height, samples, core=None):
    """Draw a fixed 0–100% history, optionally for a single logical CPU."""
    cr.set_source_rgb(.07, .10, .14)
    cr.paint()
    cr.set_source_rgb(.16, .22, .29)
    cr.set_line_width(1)
    for i in range(5):
        cr.move_to(0, height*i/4)
        cr.line_to(width, height*i/4)
    for i in range(7):
        cr.move_to(width*i/6, 0)
        cr.line_to(width*i/6, height)
    cr.stroke()
    indices = [core] if core is not None else range(max((len(s.get('cores', [])) for s in samples), default=0))
    for index in indices:
        points = []
        for i, sample in enumerate(samples):
            values = sample.get('cores', [])
            if index >= len(values):
                if points:
                    cr.stroke()
                    points = []
                continue
            x = (60-len(samples)+i)*width/59
            y = height-max(0, min(100, values[index]))*height/100
            if points:
                cr.line_to(x, y)
            else:
                cr.move_to(x, y)
            points.append((x, y))
        cr.set_source_rgb(*core_color(index))
        cr.set_line_width(1.8 if core is not None else 1.4)
        cr.stroke()
        if core is not None and len(points) > 1:
            cr.move_to(points[0][0], height)
            for x, y in points:
                cr.line_to(x, y)
            cr.line_to(points[-1][0], height)
            cr.close_path()
            cr.set_source_rgba(*core_color(index), .14)
            cr.fill()


GPU_ENGINES = (
    ('compute', 'Compute / graphics', 'GPU compute activity; NVIDIA reports combined kernel utilization.'),
    ('3d', '3D / render', 'Driver render/gfx engine counters. May include compute submitted to that engine. DRM values cover readable clients only.'),
    ('copy', 'Copy', 'Driver copy/DMA engine counters, when exposed. DRM values cover readable clients only.'),
    ('encode', 'Video encode', 'NVIDIA encoder utilization, when supported.'),
    ('decode', 'Video decode', 'NVIDIA decoder utilization, when supported.'),
    ('memory', 'Memory controller', 'Time device memory was being read or written; not memory capacity or copy-engine utilization.'),
    ('jpeg', 'JPEG', 'NVIDIA JPEG engine utilization, when supported.'),
    ('ofa', 'Optical flow', 'NVIDIA optical flow accelerator utilization, when supported.'),
    ('video', 'Video engine', 'Shared video engine activity; not separate encode/decode counters.'),
    ('enhance', 'Video enhancement', 'Driver video enhancement engine counters.'),
)


def draw_engine_history(cr, width, height, samples, key):
    # Adapt the existing chart renderer while keeping unsupported samples as gaps.
    class Series:
        resource = 'GPU'
        COLORS = HistoryChart.COLORS
    series = Series()
    series.samples = [{'GPU': sample.get('engines', {}).get(key)} for sample in samples]
    HistoryChart.draw(series, None, cr, width, height)
    if not series.samples or series.samples[-1]['GPU'] is None:
        cr.set_source_rgb(.6, .69, .8)
        cr.set_font_size(12)
        cr.move_to(12, height/2)
        cr.show_text('Unavailable')


class TaskManager(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)
        GLib.set_application_name("TaskManager")
        self.monitor = Monitor()
        self.process_icons = ProcessIcons()
        self.data = None
        self.last_timestamp = None
        self.selected_gpu_id = None
        self.gpu_device_ids = []
        self.gpu_device_labels = []
        self.syncing_gpu_selector = False
        self.paused = False
        self.resource = 'CPU'
        self.load_alerts = ResourceAlerts()
        self.widget_mode = False
        self.compact_window = None
        self.mode_transition_pending = False
        self.connect('activate', self.activate)

    def activate(self, app):
        if self.get_active_window():
            self.get_active_window().present()
            return
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        window = Gtk.ApplicationWindow(application=self, title='TaskManager', default_width=1320, default_height=920)
        window.set_size_request(980, 680)
        window.set_icon_name(APP_ID)
        self.window = window
        header = Gtk.HeaderBar()
        header.set_title_widget(label('TaskManager', 'brand'))
        self.status = label('Starting telemetry', 'status')
        header.pack_start(self.status)
        self.pause_button = Gtk.Button(label='Pause')
        self.pause_button.set_tooltip_text('Pause live updates (Ctrl+P)')
        self.pause_button.connect('clicked', self.toggle_pause)
        header.pack_end(self.pause_button)
        about = Gtk.Button(icon_name='help-about-symbolic')
        about.set_tooltip_text('About TaskManager')
        about.connect('clicked', self.show_about)
        header.pack_end(about)
        window.set_titlebar(header)
        for name, callback, accelerator in (
            ('quit', lambda *_: self.quit(), '<Primary>q'),
            ('pause', lambda *_: self.toggle_pause(self.pause_button), '<Primary>p'),
            ('search', lambda *_: self.focus_search(), '<Primary>f'),
            ('widget', lambda *_: self.set_widget_mode(not self.widget_mode), '<Primary>m'),
            ('fullview', lambda *_: self.set_widget_mode(False), 'Escape'),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect('activate', callback)
            self.add_action(action)
            self.set_accels_for_action('app.'+name, [accelerator])
        shell = box(0, True)
        window.set_child(shell)
        sidebar = box(8)
        sidebar.add_css_class('sidebar')
        sidebar.set_size_request(200, -1)
        brand_row = box(10, True)
        brand_image = Gtk.Image.new_from_icon_name('utilities-system-monitor-symbolic')
        brand_image.set_pixel_size(25)
        brand_image.add_css_class('brand-icon')
        brand_row.append(brand_image)
        brand_row.append(label('TaskManager', 'brand'))
        sidebar.append(brand_row)
        sidebar.append(label('SYSTEM WORKSPACE', 'section-label'))
        self.stack = Gtk.Stack()
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        self.navigation = {}
        first_nav = None
        for name, icon in [('Performance', 'utilities-system-monitor-symbolic'), ('Processes', 'view-list-symbolic'), ('System', 'computer-symbolic')]:
            button = Gtk.ToggleButton()
            button.add_css_class('navigation')
            row = box(12, True)
            row.append(Gtk.Image.new_from_icon_name(icon))
            row.append(label(name))
            button.set_child(row)
            if first_nav:
                button.set_group(first_nav)
            else:
                first_nav = button
            button.connect('toggled', lambda b, n=name: self.change_page(n) if b.get_active() else None)
            self.navigation[name] = button
            sidebar.append(button)
        spacer = box()
        spacer.set_vexpand(True)
        sidebar.append(spacer)
        sidebar.append(label('LOCAL MONITORING', 'section-label'))
        sidebar.append(label('Read-only telemetry', 'subtitle'))
        sidebar.append(label('Refreshes every second', 'subtitle'))
        sidebar.append(label('v'+VERSION, 'subtitle'))
        shell.append(sidebar)
        shell.append(self.stack)
        self.build_performance()
        self.build_processes()
        self.system_content = box(16)
        self.system_content.add_css_class('content')
        system_scroll = Gtk.ScrolledWindow()
        system_scroll.set_child(self.system_content)
        self.stack.add_named(system_scroll, 'System')
        self.navigation['Performance'].set_active(True)
        threading.Thread(target=self.monitor.loop, daemon=True).start()
        threading.Thread(target=self.monitor.gpu_loop, daemon=True).start()
        threading.Thread(target=self.monitor.filesystem_loop, daemon=True).start()
        GLib.timeout_add(1000, self.refresh)
        self.refresh()
        window.present()

    def change_page(self, name):
        if self.widget_mode:
            self.set_widget_mode(False)
        if not self.navigation[name].get_active():
            self.navigation[name].set_active(True)
        self.stack.set_visible_child_name(name)
        if self.data:
            self.render()

    def toggle_pause(self, button):
        self.paused = not self.paused
        button.set_label('Resume' if self.paused else 'Pause')
        self.status.set_label('Paused' if self.paused else 'Live · 1 second')
        if self.paused:
            self.status.add_css_class('paused')
        else:
            self.status.remove_css_class('paused')

    def graph_pressed(self, gesture, presses, x, y):
        if presses == 2:
            # Claim before the surrounding native window handle can maximize.
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            if not self.mode_transition_pending:
                self.mode_transition_pending = True
                GLib.idle_add(self.toggle_widget_idle)

    def toggle_widget_idle(self):
        self.mode_transition_pending = False
        self.set_widget_mode(not self.widget_mode)
        return False

    def ensure_compact_window(self):
        if self.compact_window is not None:
            return
        # Configure decorations before mapping: more reliable across window managers.
        window = Gtk.ApplicationWindow(application=self, title='TaskManager', default_width=720, default_height=340)
        window.set_decorated(False)
        window.set_size_request(500, 220)
        window.set_icon_name(APP_ID)
        window.add_css_class('widget-mode')
        window.connect('close-request', lambda *_: self.close_widget())
        content = box(8)
        content.add_css_class('widget-content')
        self.widget_handle = Gtk.WindowHandle()
        self.widget_handle.set_hexpand(True)
        self.widget_handle.set_vexpand(True)
        window.set_child(content)
        self.compact_window, self.compact_content = window, content

    def close_widget(self):
        self.quit()
        return False

    def set_widget_mode(self, enabled):
        if self.widget_mode == enabled:
            return
        if enabled:
            self.ensure_compact_window()
            self.normal_window_size = (self.window.get_width(), self.window.get_height())
            self.widget_mode = True
            self.performance_content.remove(self.resource_row)
            self.performance_content.remove(self.chart)
            self.compact_content.append(self.resource_row)
            self.widget_handle.set_child(self.chart)
            self.compact_content.append(self.widget_handle)
            self.chart.set_content_height(150)
            self.chart.set_vexpand(True)
            self.window.set_visible(False)
            self.compact_window.present()
        else:
            self.widget_mode = False
            self.compact_window.set_visible(False)
            self.compact_content.remove(self.resource_row)
            self.widget_handle.set_child(None)
            self.compact_content.remove(self.widget_handle)
            self.performance_content.insert_child_after(self.resource_row, self.host_label)
            self.performance_content.insert_child_after(self.chart, self.chart_title)
            self.chart.set_content_height(240)
            self.chart.set_vexpand(False)
            self.window.set_default_size(*self.normal_window_size)
            self.window.present()
        if self.data:
            self.render_performance()
        self.chart.queue_draw()

    def show_about(self, button):
        dialog = Gtk.AboutDialog(transient_for=self.window, modal=True, program_name='TaskManager', version=VERSION,
                                 logo_icon_name=APP_ID, website='https://github.com/PuChan-HCI/TaskManager',
                                 comments='A clear view of your Linux system.\nCPU, memory, GPU, storage and network telemetry.',
                                 license_type=Gtk.License.MIT_X11)
        dialog.present()

    def focus_search(self):
        self.change_page('Processes')
        self.search.grab_focus()

    def build_performance(self):
        scroll = Gtk.ScrolledWindow()
        content = box(18)
        self.performance_content = content
        content.add_css_class('content')
        scroll.set_child(content)
        content.append(label('Performance', 'title'))
        self.host_label = label('', 'subtitle')
        content.append(self.host_label)
        resources = box(10, True)
        self.resource_row = resources
        self.resource_buttons = {}
        self.resource_values = {}
        self.resource_titles = {}
        first = None
        for name in ('CPU','Memory','GPU','Disk','Network'):
            button = Gtk.ToggleButton()
            tile = box(5)
            title_label = label(name.upper(), 'resource-title')
            tile.append(title_label)
            self.resource_titles[name] = title_label
            value_label = label('—', 'resource-number')
            tile.append(value_label)
            button.set_child(tile)
            button.set_tooltip_text('View '+name.lower()+' metrics')
            self.resource_values[name] = value_label
            button.add_css_class('resource')
            button.set_hexpand(True)
            if first:
                button.set_group(first)
            else:
                first = button
            button.connect('toggled', self.select_resource, name)
            self.resource_buttons[name] = button
            resources.append(button)
        first.set_active(True)
        content.append(resources)
        self.gpu_selector = Gtk.DropDown.new_from_strings([])
        self.gpu_selector.set_tooltip_text('Select a GPU')
        self.gpu_selector.connect('notify::selected', self.change_gpu)
        content.append(self.gpu_selector)
        self.chart_title = label('CPU', 'value')
        content.append(self.chart_title)
        self.chart = HistoryChart()
        self.chart.set_tooltip_text('Double-click for widget mode (Ctrl+M). Escape restores the full view.')
        graph_click = Gtk.GestureClick.new()
        graph_click.set_button(Gdk.BUTTON_PRIMARY)
        graph_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        graph_click.connect('pressed', self.graph_pressed)
        self.chart.add_controller(graph_click)
        content.append(self.chart)
        self.cpu_controls = box(12, True)
        self.cpu_controls.append(label('CPU history', 'subtitle'))
        modes = Gtk.DropDown.new_from_strings(['All logical CPUs', 'Total utilization'])
        modes.connect('notify::selected', self.change_cpu_mode)
        self.cpu_controls.append(modes)
        self.cpu_controls.append(label('Fixed scale: 0–100% per CPU', 'subtitle'))
        content.append(self.cpu_controls)
        self.core_section = box(10)
        self.core_section.append(label('LOGICAL PROCESSORS · 60 SECOND HISTORY', 'subtitle'))
        self.core_grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=10, row_spacing=10, homogeneous=True, max_children_per_line=5, min_children_per_line=2)
        self.core_widgets = []
        self.core_scroll = Gtk.ScrolledWindow()
        self.core_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.ALWAYS)
        self.core_scroll.set_min_content_height(260)
        self.core_scroll.set_max_content_height(260)
        self.core_scroll.set_propagate_natural_height(True)
        self.core_scroll.set_overlay_scrolling(False)
        self.core_scroll.set_child(self.core_grid)
        self.core_section.append(self.core_scroll)
        self.history_caption = label('Last 60 samples                                       Now', 'subtitle')
        content.append(self.history_caption)
        self.stats = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=12, row_spacing=12, homogeneous=True, max_children_per_line=4, min_children_per_line=2)
        content.append(self.stats)
        self.detail = label()
        self.detail.set_wrap(True)
        self.detail.set_selectable(True)
        self.detail.add_css_class('subtitle')
        content.append(self.detail)
        content.append(self.core_section)
        self.gpu_section = box(10)
        self.gpu_section.append(label('GPU ENGINES · FIXED 0–100% SCALE', 'subtitle'))
        engine_grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=12, row_spacing=12, homogeneous=True, max_children_per_line=2, min_children_per_line=1)
        self.engine_widgets = {}
        for key, title, description in GPU_ENGINES:
            card = box(8)
            card.add_css_class('card')
            card.set_tooltip_text(description)
            heading = label(title)
            card.append(heading)
            drawing = Gtk.DrawingArea()
            drawing.set_content_width(270)
            drawing.set_content_height(110)
            drawing.set_hexpand(True)
            drawing.set_draw_func(lambda area, cr, w, h, k=key: draw_engine_history(cr, w, h, self.chart.samples, k))
            card.append(drawing)
            engine_grid.append(card)
            self.engine_widgets[key] = (card.get_parent(), heading, drawing)
        self.gpu_section.append(engine_grid)
        content.append(self.gpu_section)
        self.gpu_process_section = box(10)
        process_toolbar = box(16, True)
        self.gpu_process_count = label('GPU PROCESSES', 'subtitle')
        process_toolbar.append(self.gpu_process_count)
        self.all_gpus = Gtk.CheckButton(label='All GPUs', active=True)
        self.all_gpus.connect('toggled', lambda button: self.render_gpu_processes() if self.data else None)
        process_toolbar.append(self.all_gpus)
        self.gpu_process_section.append(process_toolbar)
        self.gpu_process_model = Gio.ListStore.new(TableRow)
        gpu_process_view = Gtk.ColumnView()
        gpu_process_view.set_show_column_separators(True)
        for index, title in enumerate(('PID', 'User', 'GPU', 'Type', 'GPU / engine %', 'GPU memory', 'Encode %', 'Decode %', 'CPU %', 'Host RAM', 'Process')):
            factory = Gtk.SignalListItemFactory()
            if index == 10:
                factory.connect('setup', setup_process_name)
                factory.connect('bind', bind_process_name, index)
            else:
                factory.connect('setup', lambda f, item: item.set_child(label()))
                factory.connect('bind', lambda f, item, n=index: item.get_child().set_label(item.get_item().values[n]))
            column = Gtk.ColumnViewColumn(title=title, factory=factory)
            column.set_expand(index == 10)
            column.set_resizable(True)
            gpu_process_view.append_column(column)
        make_sortable(gpu_process_view, self.gpu_process_model, default_column=4, descending=True)
        gpu_process_scroll = Gtk.ScrolledWindow()
        gpu_process_scroll.set_min_content_height(240)
        gpu_process_scroll.set_child(gpu_process_view)
        self.gpu_process_section.append(gpu_process_scroll)
        self.gpu_process_note = label('', 'subtitle')
        self.gpu_process_note.set_wrap(True)
        self.gpu_process_section.append(self.gpu_process_note)
        content.append(self.gpu_process_section)
        self.extra = box(8)
        self.extra.append(label('NETWORK INTERFACES', 'subtitle'))
        self.network_model = Gio.ListStore.new(TableRow)
        network_view = Gtk.ColumnView()
        network_view.set_show_column_separators(True)
        for index, title in enumerate(('Interface', 'Status', 'Receive /s', 'Send /s', 'Total received', 'Total sent')):
            factory = Gtk.SignalListItemFactory()
            factory.connect('setup', lambda f, item: item.set_child(label()))
            factory.connect('bind', lambda f, item, n=index: item.get_child().set_label(item.get_item().values[n]))
            column = Gtk.ColumnViewColumn(title=title, factory=factory)
            column.set_expand(index == 0)
            column.set_resizable(True)
            network_view.append_column(column)
        make_sortable(network_view, self.network_model)
        network_scroll = Gtk.ScrolledWindow()
        network_scroll.set_min_content_height(280)
        network_scroll.set_child(network_view)
        self.extra.append(network_scroll)
        self.extra.append(label('Totals since boot or interface creation · Includes virtual interfaces', 'subtitle'))
        content.append(self.extra)
        self.filesystem_section = box(8)
        self.filesystem_section.append(label('FILE SYSTEMS', 'subtitle'))
        self.filesystem_model = Gio.ListStore.new(TableRow)
        filesystem_view = Gtk.ColumnView()
        filesystem_view.set_show_column_separators(True)
        for index, title in enumerate(('Device', 'Mount point', 'Type', 'Total', 'Used', 'Available', 'Usage')):
            factory = Gtk.SignalListItemFactory()
            if index == 6:
                factory.connect('setup', lambda f, item: item.set_child(Gtk.ProgressBar(show_text=True)))
                def bind_usage(factory, item):
                    value = item.get_item().values[6]
                    item.get_child().set_fraction(float(value.rstrip('%'))/100)
                    item.get_child().set_text(value)
                factory.connect('bind', bind_usage)
            else:
                factory.connect('setup', lambda f, item: item.set_child(label()))
                factory.connect('bind', lambda f, item, n=index: item.get_child().set_label(item.get_item().values[n]))
            column = Gtk.ColumnViewColumn(title=title, factory=factory)
            column.set_expand(index in (0, 1))
            column.set_resizable(True)
            filesystem_view.append_column(column)
        make_sortable(filesystem_view, self.filesystem_model)
        filesystem_scroll = Gtk.ScrolledWindow()
        filesystem_scroll.set_min_content_height(240)
        filesystem_scroll.set_child(filesystem_view)
        self.filesystem_section.append(filesystem_scroll)
        self.filesystem_section.append(label('Refreshes every 5 seconds · Kernel, RAM and Snap mounts are hidden · Available excludes reserved blocks', 'subtitle'))
        content.append(self.filesystem_section)
        self.stack.add_named(scroll, 'Performance')

    def selected_gpu(self):
        devices = self.data['gpu']['devices'] if self.data else []
        return next((gpu for gpu in devices if gpu['id'] == self.selected_gpu_id), devices[0] if devices else {})

    def sync_gpu_selector(self):
        devices = self.data['gpu']['devices']
        ids = [gpu['id'] for gpu in devices]
        labels = [gpu['name']+' · '+gpu.get('pci', gpu['id']) for gpu in devices]
        if ids != self.gpu_device_ids or labels != self.gpu_device_labels:
            self.syncing_gpu_selector = True
            self.gpu_device_ids, self.gpu_device_labels = ids, labels
            if self.selected_gpu_id not in ids:
                self.selected_gpu_id = ids[0] if ids else None
            self.gpu_selector.set_model(Gtk.StringList.new(labels))
            if ids:
                self.gpu_selector.set_selected(ids.index(self.selected_gpu_id))
            self.syncing_gpu_selector = False
        self.gpu_selector.set_visible(self.resource == 'GPU' and len(ids) > 1)

    def change_gpu(self, selector, param):
        if self.syncing_gpu_selector:
            return
        index = selector.get_selected()
        if index < len(self.gpu_device_ids):
            self.selected_gpu_id = self.gpu_device_ids[index]
            self.update_gpu_history()
            if self.data:
                self.render_performance()

    def update_gpu_history(self):
        for sample in self.chart.samples:
            gpu = sample.get('gpu_devices', {}).get(self.selected_gpu_id, {})
            sample['GPU'] = gpu.get('utilization')
            sample['engines'] = gpu.get('engines', {})

    def change_cpu_mode(self, dropdown, param):
        self.chart.cpu_mode = 'cores' if dropdown.get_selected() == 0 else 'total'
        self.chart.queue_draw()

    def update_core_charts(self):
        values = self.data['cpu']['cores']
        while len(self.core_widgets) < len(values):
            index = len(self.core_widgets)
            card = box(6)
            card.add_css_class('card')
            heading = label()
            heading.set_use_markup(True)
            card.append(heading)
            drawing = Gtk.DrawingArea()
            drawing.set_content_width(140)
            drawing.set_content_height(65)
            drawing.set_hexpand(True)
            drawing.set_draw_func(lambda area, cr, w, h, n=index: draw_cpu_history(cr, w, h, self.chart.samples, n))
            card.append(drawing)
            self.core_grid.append(card)
            self.core_widgets.append((card, heading, drawing))
        for index, (card, heading, drawing) in enumerate(self.core_widgets):
            card.get_parent().set_visible(index < len(values))
            if index < len(values):
                color = '#'+''.join(f'{round(c*255):02x}' for c in core_color(index))
                heading.set_markup(f'<span foreground="{color}">● CPU {index + 1}</span>  <b>{values[index]:.1f}%</b>')
                card.set_tooltip_text(f'Logical CPU {index + 1} (Linux cpu{index}) · 0–100% · last 60 samples')
                drawing.queue_draw()

    def select_resource(self, button, name):
        if button.get_active():
            self.resource = name
            if self.data:
                self.render_performance()

    def build_processes(self):
        content = box(16)
        content.add_css_class('content')
        content.append(label('Processes', 'title'))
        content.append(label('Explore running applications and services. Click a column to sort.', 'subtitle'))
        toolbar = box(12, True)
        self.search = Gtk.SearchEntry(placeholder_text='Search name or PID')
        self.search.set_hexpand(True)
        self.search.connect('search-changed', lambda _: self.render_processes() if self.data else None)
        toolbar.append(self.search)
        content.append(toolbar)
        self.process_count = label('CPU: 100% = one logical core. Memory: resident set size.', 'subtitle')
        content.append(self.process_count)
        self.model = Gio.ListStore.new(TableRow)
        view = Gtk.ColumnView()
        for i,title in enumerate(('Name','PID','CPU','Memory','Threads','State')):
            factory = Gtk.SignalListItemFactory()
            if i == 0:
                factory.connect('setup', setup_process_name)
                factory.connect('bind', bind_process_name)
            else:
                factory.connect('setup', lambda f,item: item.set_child(label()))
                factory.connect('bind', lambda f,item,n=i: item.get_child().set_label(item.get_item().values[n]))
            column = Gtk.ColumnViewColumn(title=title, factory=factory)
            column.set_expand(i == 0)
            column.set_resizable(True)
            view.append_column(column)
        make_sortable(view, self.model, default_column=2, descending=True)
        scroll = Gtk.ScrolledWindow(vexpand=True)
        scroll.set_child(view)
        content.append(scroll)
        self.stack.add_named(content, 'Processes')

    def refresh(self):
        if not self.paused:
            with self.monitor.lock:
                snapshot = self.monitor.snapshot
            if snapshot and snapshot['timestamp'] != self.last_timestamp:
                self.data = snapshot
                self.last_timestamp = snapshot['timestamp']
                self.sync_gpu_selector()
                gpu = self.selected_gpu()
                self.update_load_alerts(gpu)
                self.chart.samples.append({'gpu_devices':{device['id']:device for device in snapshot['gpu']['devices']}, 'engines':gpu.get('engines', {}), 'cores':snapshot['cpu']['cores'], 'CPU':snapshot['cpu']['percent'], 'Memory':snapshot['memory']['percent'], 'GPU':gpu.get('utilization'), 'Disk':max([d['active'] for d in snapshot['disks']],default=0), 'rx':sum(n['rx'] for n in snapshot['network']), 'tx':sum(n['tx'] for n in snapshot['network'])})
                self.status.set_label('Live · 1 second')
                self.render()
        return True

    def render(self):
        name = self.stack.get_visible_child_name()
        if self.widget_mode or name == 'Performance':
            self.render_performance()
        elif name == 'Processes':
            self.render_processes()
        elif name == 'System':
            self.render_system()

    def update_load_alerts(self, gpu):
        for name, (high, description) in self.load_alerts.update(self.data, gpu).items():
            button = self.resource_buttons[name]
            if high:
                button.add_css_class('high-load')
            else:
                button.remove_css_class('high-load')
            self.resource_titles[name].set_label(name.upper()+(' · HIGH' if high else ''))
            button.set_tooltip_text(description)

    def render_performance(self):
        d = self.data
        self.sync_gpu_selector()
        self.update_gpu_history()
        gpu = self.selected_gpu()
        self.update_load_alerts(gpu)
        rx,tx = sum(n['rx'] for n in d['network']),sum(n['tx'] for n in d['network'])
        self.host_label.set_label(f"{d['host']}  /  {d['arch']}  /  {d['cpu']['count']} logical processors  ·  Live resource overview")
        values = {'CPU':percent(d['cpu']['percent']), 'Memory':percent(d['memory']['percent']), 'GPU':percent(gpu.get('utilization')) if gpu.get('utilization') is not None else ('Detected' if gpu else 'Not detected'), 'Disk':percent(max([x['active'] for x in d['disks']],default=0)), 'Network':size(rx+tx)+'/s'}
        for name,button in self.resource_buttons.items():
            self.resource_values[name].set_label(values[name])
        self.chart_title.set_label(self.resource+'  ·  '+values[self.resource])
        self.chart.resource = self.resource
        self.chart.queue_draw()
        self.cpu_controls.set_visible(self.resource == 'CPU')
        self.core_section.set_visible(self.resource == 'CPU')
        chart_available = self.resource != 'GPU' or gpu.get('utilization') is not None
        self.chart.set_visible(chart_available or self.widget_mode)
        self.history_caption.set_visible(chart_available)
        if not chart_available:
            self.chart_title.set_label('GPU')
        engine_values = gpu.get('engines', {})
        self.gpu_section.set_visible(self.resource == 'GPU' and any(engine_values.get(key) is not None for key, _, _ in GPU_ENGINES))
        if self.resource == 'GPU':
            for key, title, description in GPU_ENGINES:
                container, heading, drawing = self.engine_widgets[key]
                value = engine_values.get(key)
                container.set_visible(value is not None)
                if value is not None:
                    heading.set_label(title+'  ·  '+percent(value))
                    container.set_tooltip_text(description+' '+gpu.get('engine_sources', {}).get(key, ''))
                    drawing.queue_draw()
        if self.resource == 'CPU':
            self.update_core_charts()
        mem = d['memory']
        extra = ''
        if self.resource == 'CPU':
            stats = [('Processes',str(len(d['processes']))),('Threads',str(d['threads'])),('Average clock',f"{d['cpu']['frequency_mhz']/1000:.2f} GHz" if d['cpu']['frequency_mhz'] else 'Unavailable'),('Uptime',f"{d['uptime']/3600:.1f} hours")]
            extra = 'Load average (1 / 5 / 15 min): '+' / '.join(f'{v:.2f}' for v in d['cpu']['load'])+f" · {d['cpu']['count']} logical processors · Each color identifies one CPU."
        elif self.resource == 'Memory':
            stats = [('In use',size(mem['used'])),('Available',size(mem['available'])),('Cached',size(mem['cached'])),('Swap',size(mem['swap_used'])+' / '+size(mem['swap_total']))]
            extra = 'System memory in use = total − available. Integrated GPUs may share this memory.'
        elif self.resource == 'GPU':
            stats = [(title, formatter(gpu[key])) for key, title, formatter in (
                ('temperature', 'Temperature', lambda v: f'{v:g} °C'),
                ('power', 'Power', lambda v: f'{v:g} W'),
                ('clock_mhz', 'Clock', lambda v: f'{v:g} MHz'),
                ('memory_used_mib', 'GPU memory', lambda v: size(v*1048576)),
            ) if gpu.get(key) is not None]
            extra = (gpu.get('name', '')+' · '+gpu.get('source', '')+' · Only confirmed metrics are shown. '+gpu.get('engine_scope', '')) if gpu else 'GPU telemetry unavailable. Retrying every 3 seconds.'
            if d['gpu'].get('warning') or d['gpu'].get('error'):
                extra += '\n'+str(d['gpu'].get('warning') or d['gpu'].get('error'))
        elif self.resource == 'Disk':
            stats = [('Read',size(sum(x['read'] for x in d['disks']))+'/s'),('Write',size(sum(x['write'] for x in d['disks']))+'/s'),('Root used',size(d['storage']['used'])),('Root free',size(d['storage']['free']))]
            extra = '\n'.join(f"{x['name']}: {percent(x['active'])} active · Read {size(x['read'])}/s · Write {size(x['write'])}/s" for x in d['disks'])
        else:
            stats = [('Receive',size(rx)+'/s'),('Send',size(tx)+'/s'),('Interfaces',str(len(d['network']))),('Active links',str(sum(x['state']=='up' for x in d['network']))) ]
            extra = 'Receive is solid; send is dashed. Totals include virtual interfaces and may count traffic at multiple layers.'
        child = self.stats.get_first_child()
        while child:
            following = child.get_next_sibling()
            self.stats.remove(child)
            child = following
        self.stats.set_visible(bool(stats))
        for title,value in stats:
            card = box(8)
            card.add_css_class('card')
            card.append(label(title, 'subtitle'))
            card.append(label(value, 'value'))
            self.stats.append(card)
        self.detail.set_label(extra)
        self.gpu_process_section.set_visible(self.resource == 'GPU')
        if self.resource == 'GPU':
            self.render_gpu_processes()
        self.filesystem_section.set_visible(self.resource == 'Disk')
        if self.resource == 'Disk':
            rows = [TableRow(filesystem_cells(item), sort_values=[item['device'], item['mount'], item['type'], item['total'], item['used'], item['available'], item['percent']]) for item in d.get('filesystems', [])]
            self.filesystem_model.splice(0, self.filesystem_model.get_n_items(), rows)
        self.extra.set_visible(self.resource == 'Network')
        if self.resource == 'Network':
            rows = sorted(d['network'], key=lambda item: item['name'])
            self.network_model.splice(0, self.network_model.get_n_items(), [TableRow(network_cells(item), sort_values=[item['name'], item['state'], item['rx'], item['tx'], item['received'], item['sent']]) for item in rows])

    def render_gpu_processes(self):
        processes = self.data['gpu'].get('processes', [])
        if not self.all_gpus.get_active():
            processes = [p for p in processes if p['gpu_id'] == self.selected_gpu_id]
        host = {p['pid']:p for p in self.data['processes']}
        rows = []
        for process in processes:
            cpu_process = host.get(process['pid'], {})
            display, values = gpu_process_cells(process, cpu_process)
            icon = self.process_icons.resolve({**cpu_process, 'name':process['name']})
            rows.append(TableRow(display, icon, values))
        self.gpu_process_model.splice(0, self.gpu_process_model.get_n_items(), rows)
        self.gpu_process_count.set_label(f'GPU PROCESSES · {len(rows)}')
        note = 'GPU %: NVIDIA SM activity or busiest DRM engine. — = not reported. CPU: 100% per core. DRM covers readable processes; shared client/memory values are not additive.'
        if not rows:
            note = 'No GPU processes were reported. Idle GPUs or restricted driver/process access can leave this table empty. '+note
        if self.data['gpu'].get('process_warning'):
            note += ' '+self.data['gpu']['process_warning']
        self.gpu_process_note.set_label(note)

    def render_processes(self):
        query = self.search.get_text().lower()
        rows = [p for p in self.data['processes'] if query in p['name'].lower() or query in str(p['pid'])]
        self.process_count.set_label(f'{len(rows)} processes · CPU: 100% = one logical core · Memory: RSS')
        states = {'R':'Running','S':'Sleeping','I':'Idle','D':'Disk wait','T':'Stopped','Z':'Zombie'}
        self.model.splice(0,self.model.get_n_items(),[TableRow([p['name'],str(p['pid']),percent(p['cpu']),size(p['memory']),str(p['threads']),states.get(p['state'],p['state'])], self.process_icons.resolve(p), [p['name'], p['pid'], p['cpu'], p['memory'], p['threads'], states.get(p['state'],p['state'])]) for p in rows])

    def render_system(self):
        d = self.data
        clear(self.system_content)
        self.system_content.append(label('System', 'title'))
        self.system_content.append(label('Hardware, operating system and sensor information', 'subtitle'))
        grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=14, row_spacing=14, homogeneous=True, min_children_per_line=2, max_children_per_line=3)
        self.system_content.append(grid)
        entries = [('Host',d['host']),('Architecture',d['arch']),('Kernel',d['kernel']),('Logical CPUs',str(d['cpu']['count'])),('Total memory',size(d['memory']['total'])),('Root capacity',size(d['storage']['total'])),('Load averages',' / '.join(f'{n:.2f}' for n in d['cpu']['load']))]
        entries += [(t['name'],f"{t['value']:.1f} °C") for t in d['temperatures']]
        entries += [('GPU '+g['index'],g['name']+' · Driver '+g['driver']) for g in d['gpu']['devices']]
        for title,value in entries:
            row = box(6)
            row.add_css_class('card')
            row.append(label(title,'subtitle'))
            text = label(value)
            text.set_selectable(True)
            text.set_wrap(True)
            row.append(text)
            grid.append(row)


def self_test():
    """Exercise the live collector and real chart renderer without a display."""
    import cairo
    monitor = Monitor()
    monitor.sample()
    assert monitor.snapshot['cpu']['count'] > 0
    assert size(1024) == '1.0 KiB'
    assert percent(None) == 'Unavailable'
    from metrics import parse_fdinfo, parse_gpu_xml, parse_pmon
    assert parse_pmon('# gpu pid type sm mem enc dec command\n0 100 C 0 - - - python\n')[0]['activity'] == 0
    assert parse_fdinfo('drm-driver: i915\ndrm-client-id: 1\ndrm-engine-render: 0 ns')['counters']['render'][0] == 0
    assert parse_gpu_xml('<nvidia_smi_log><gpu><utilization><gpu_util>0 %</gpu_util></utilization></gpu></nvidia_smi_log>')[0]['utilization'] == 0
    store = Gio.ListStore.new(TableRow)
    store.splice(0, 0, [TableRow(['10 GiB'], sort_values=[10]), TableRow(['2 GiB'], sort_values=[2])])
    sorter = Gtk.CustomSorter.new(lambda a, b, unused=None: compare_rows(a, b, 0))
    ordered = Gtk.SortListModel(model=store, sorter=sorter)
    assert ordered.get_item(0).sort_values[0] == 2
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 600, 240)
    cr = cairo.Context(surface)
    class Chart:
        COLORS = HistoryChart.COLORS
        samples = [{'cores':[20, 70, 0, 100], 'CPU':20,'Memory':40,'GPU':None,'Disk':1,'rx':2048,'tx':1024}]*60
    for resource in HistoryChart.COLORS:
        chart = Chart()
        chart.resource = resource
        HistoryChart.draw(chart, None, cr, 600, 240)
    for core in (None, 0, 3):
        draw_cpu_history(cr, 600, 240, Chart.samples, core)
    draw_cpu_history(cr, 600, 240, [], None)
    chart = Chart()
    chart.resource = 'CPU'
    chart.cpu_mode = 'total'
    HistoryChart.draw(chart, None, cr, 600, 240)
    for key, title, description in GPU_ENGINES:
        draw_engine_history(cr, 300, 110, [{'engines': {key: 40}}, {'engines': {key: None}}, {'engines': {key: 0}}], key)
        draw_engine_history(cr, 300, 110, [], key)
    print('Native self-test passed: live telemetry, charts, GPU parsers and native numeric sorting.')

if __name__ == '__main__':
    if '--version' in sys.argv:
        print('TaskManager '+VERSION)
    elif '--self-test' in sys.argv:
        self_test()
    else:
        if not Gtk.init_check() or Gdk.Display.get_default() is None:
            sys.exit('No graphical display available. Run TaskManager from your desktop session.')
        sys.exit(TaskManager().run(sys.argv))
