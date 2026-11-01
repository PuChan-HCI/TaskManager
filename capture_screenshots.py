#!/usr/bin/python3
"""Capture real TaskManager windows from a Linux graphical desktop session."""
import argparse
import ctypes
from pathlib import Path
import sys
from native import TaskManager, Gtk, Gdk, GLib


def save_window(window, destination):
    """Snapshot the actual GTK widget tree using its active renderer."""
    library = ctypes.CDLL('libgtk-4.so.1')
    gobject = ctypes.CDLL('libgobject-2.0.so.0')
    capsule_pointer = ctypes.pythonapi.PyCapsule_GetPointer
    capsule_pointer.argtypes = [ctypes.py_object, ctypes.c_char_p]
    capsule_pointer.restype = ctypes.c_void_p
    def pointer(obj):
        return capsule_pointer(obj.__gpointer__, None)
    signatures = {
        'gtk_snapshot_new': ([], ctypes.c_void_p),
        'gdk_paintable_snapshot': ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_double, ctypes.c_double], None),
        'gtk_snapshot_free_to_node': ([ctypes.c_void_p], ctypes.c_void_p),
        'gsk_renderer_render_texture': ([ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p], ctypes.c_void_p),
        'gdk_texture_save_to_png': ([ctypes.c_void_p, ctypes.c_char_p], ctypes.c_int),
        'gsk_render_node_unref': ([ctypes.c_void_p], None),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(library, name)
        function.argtypes, function.restype = arguments, result
    gobject.g_object_unref.argtypes = [ctypes.c_void_p]
    paintable = Gtk.WidgetPaintable.new(window)
    snapshot = library.gtk_snapshot_new()
    library.gdk_paintable_snapshot(pointer(paintable), snapshot, window.get_width(), window.get_height())
    node = library.gtk_snapshot_free_to_node(snapshot)
    if not node:
        raise RuntimeError('Window is not ready to capture')
    texture = None
    try:
        texture = library.gsk_renderer_render_texture(pointer(window.get_renderer()), node, None)
        if not texture or not library.gdk_texture_save_to_png(texture, str(destination).encode()):
            raise RuntimeError('Could not save '+str(destination))
    finally:
        if texture:
            gobject.g_object_unref(texture)
        library.gsk_render_node_unref(node)


class CaptureApp(TaskManager):
    def __init__(self, output):
        super().__init__()
        # Separate application ID avoids redirecting capture into an existing window.
        self.set_application_id('io.github.PuChanHCI.TaskManager.Capture')
        self.output = output
        self.targets = iter([
            ('cpu', 'Performance', 'CPU', False),
            ('memory', 'Performance', 'Memory', False),
            ('gpu', 'Performance', 'GPU', False),
            ('gpu-processes', 'Performance', 'GPU', True),
            ('disk', 'Performance', 'Disk', True),
            ('network', 'Performance', 'Network', True),
            ('processes', 'Processes', None, False),
            ('system', 'System', None, False),
            ('widget', 'Performance', 'CPU', False),
        ])
        self.failed = False

    def activate(self, app):
        super().activate(app)
        self.window.set_default_size(1440, 1080)
        GLib.timeout_add_seconds(8, self.advance)

    def advance(self):
        try:
            name, page, resource, bottom = next(self.targets)
        except StopIteration:
            print(f'Captured 9 native screenshots in {self.output}', flush=True)
            self.quit()
            return False
        self.change_page(page)
        if resource:
            self.resource_buttons[resource].set_active(True)
        if name == 'widget':
            self.set_widget_mode(True)
        def scroll_and_capture():
            widget = self.stack.get_visible_child()
            if not self.widget_mode and isinstance(widget, Gtk.ScrolledWindow):
                adjustment = widget.get_vadjustment()
                adjustment.set_value(max(0, adjustment.get_upper()-adjustment.get_page_size()) if bottom else 0)
            GLib.timeout_add(600, capture)
            return False
        def capture():
            try:
                save_window(self.compact_window if self.widget_mode else self.window, self.output/(name+'.png'))
                print('Saved '+name+'.png', flush=True)
            except Exception as exc:
                print('Capture failed: '+str(exc), file=sys.stderr)
                self.failed = True
                self.quit()
                return False
            GLib.timeout_add(300, self.advance)
            return False
        GLib.timeout_add(600, scroll_and_capture)
        return False


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parent/'docs/screenshots')
    args = parser.parse_args()
    if not Gtk.init_check() or Gdk.Display.get_default() is None:
        sys.exit('A graphical desktop session is required. Run this command from the terminal on your Linux desktop.')
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    app = CaptureApp(output)
    app.run([])
    sys.exit(1 if app.failed else 0)
