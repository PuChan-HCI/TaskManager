#!/usr/bin/python3
"""Install TaskManager for your user without root or a package manager."""
import argparse
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent
APP_ID = 'io.github.PuChanHCI.TaskManager'
SOURCES = ('native.py', 'metrics.py', 'README.md', 'LICENSE', 'install-user.py')


def desktop_quote(value):
    return '"'+str(value).replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')+'"'


def remove_legacy(prefix):
    """Remove only files belonging to the earlier Spark Task Manager installer."""
    legacy = prefix/'share/spark-taskmanager'
    launcher = prefix/'bin/spark-taskmanager'
    if launcher.is_file() and '/share/spark-taskmanager/native.py' in launcher.read_text():
        launcher.unlink()
    desktop = prefix/'share/applications/spark-taskmanager.desktop'
    if desktop.is_file() and 'Name=Spark Task Manager' in desktop.read_text():
        desktop.unlink()
        (prefix/'share/icons/hicolor/scalable/apps/io.spark.TaskManager.svg').unlink(missing_ok=True)
    if legacy.is_dir() and (legacy/'native.py').is_file() and 'class TaskManager(' in (legacy/'native.py').read_text():
        for name in (*SOURCES, 'server.py'):
            (legacy/name).unlink(missing_ok=True)
        cache = legacy/'__pycache__'
        if cache.is_dir():
            shutil.rmtree(cache)
        try:
            legacy.rmdir()
        except OSError:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix', type=Path, default=Path.home()/'.local', help='Installation prefix (default: ~/.local)')
    parser.add_argument('--uninstall', action='store_true', help='Remove your TaskManager installation')
    args = parser.parse_args()
    if sys.platform != 'linux':
        parser.error('TaskManager currently supports Linux on x86-64 and ARM64.')
    if os.geteuid() == 0:
        parser.error('Run as your normal user, without sudo.')
    prefix = args.prefix.expanduser().resolve()
    appdir = prefix/'share/taskmanager'
    launcher = prefix/'bin/taskmanager'
    desktop = prefix/f'share/applications/{APP_ID}.desktop'
    icon = prefix/f'share/icons/hicolor/scalable/apps/{APP_ID}.svg'
    if args.uninstall:
        for path in (launcher, desktop, icon, *[appdir/name for name in SOURCES]):
            path.unlink(missing_ok=True)
        cache = appdir/'__pycache__'
        if cache.is_dir():
            shutil.rmtree(cache)
        try:
            appdir.rmdir()
        except OSError:
            pass
        print('TaskManager has been removed from your account.')
    else:
        check = subprocess.run(['/usr/bin/python3', '-c', "import gi, cairo; gi.require_version('Gtk','4.0'); from gi.repository import Gtk"], capture_output=True, text=True)
        if check.returncode:
            sys.exit('TaskManager needs system Python with GTK4, PyGObject, Cairo and the Cairo GI bridge.\nOn Ubuntu/Debian: sudo apt install python3-gi python3-cairo python3-gi-cairo gir1.2-gtk-4.0\n'+check.stderr)
        for directory in (appdir, launcher.parent, desktop.parent, icon.parent):
            directory.mkdir(parents=True, exist_ok=True)
        for name in SOURCES:
            source, destination = ROOT/name, appdir/name
            if source.resolve() != destination.resolve():
                shutil.copyfile(source, destination)
        launcher.write_text('#!/bin/sh\nexec /usr/bin/python3 '+shlex.quote(str(appdir/'native.py'))+' "$@"\n')
        launcher.chmod(0o755)
        template = ROOT/f'packaging/{APP_ID}.desktop'
        if template.exists():
            text = template.read_text().replace('Exec=taskmanager', 'Exec='+desktop_quote(launcher)).replace('Icon='+APP_ID, 'Icon='+str(icon))
            desktop.write_text(text)
            shutil.copyfile(ROOT/f'packaging/{APP_ID}.svg', icon)
        elif not desktop.exists() or not icon.exists():
            sys.exit('Install from the complete source folder so the menu entry and icon are available.')
        remove_legacy(prefix)
        print(f'TaskManager installed for your user.\nRun: {launcher}\nOr open TaskManager from the applications menu.')
        print(f'Uninstall: /usr/bin/python3 {shlex.quote(str(appdir/"install-user.py"))} --prefix {shlex.quote(str(prefix))} --uninstall')
    updater = shutil.which('update-desktop-database')
    if updater and desktop.parent.exists():
        subprocess.run([updater, str(desktop.parent)], check=False, capture_output=True)


if __name__ == '__main__':
    main()
