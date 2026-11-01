#!/usr/bin/env python3
"""Read-only Linux performance metrics for x86, ARM, Intel, AMD and NVIDIA."""
import os
from pathlib import Path
import platform
import pwd
from functools import lru_cache
import re
import shutil
import subprocess
import threading
import time
import xml.etree.ElementTree as ET


def read(path, default=''):
    try:
        return Path(path).read_text().strip()
    except (OSError, UnicodeError):
        return default

def number(value):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def gpu_value(node, path):
    text = node.findtext(path, default='N/A').strip()
    return number(text.split()[0]) if text else None


def memory_bytes(text):
    parts = text.split() if text else []
    value = number(parts[0]) if parts else None
    factors = {'B':1, 'KiB':1024, 'MiB':1024**2, 'GiB':1024**3, '':1}
    unit = parts[1] if len(parts) > 1 else ''
    return int(value*factors[unit]) if value is not None and unit in factors and value >= 0 else None


@lru_cache(maxsize=256)
def username(uid):
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)


def parse_pmon(text):
    """Read pmon's header so optional JPEG/OFA columns don't shift fields."""
    columns, rows = [], []
    for line in text.splitlines():
        if line.lstrip().startswith('#'):
            names = line.lstrip()[1:].split()
            if 'gpu' in names and 'pid' in names and 'sm' in names:
                columns = names
            continue
        if not columns or not line.strip():
            continue
        values = line.split(maxsplit=len(columns)-1)
        if len(values) != len(columns):
            continue
        fields = dict(zip(columns, values))
        if not fields.get('pid', '').isdigit() or not fields.get('gpu', '').isdigit():
            continue
        engines = {key:number(fields.get(source)) for key,source in
                   [('compute','sm'),('memory','mem'),('encode','enc'),('decode','dec'),('jpeg','jpg'),('ofa','ofa')]}
        rows.append({'index': fields['gpu'], 'pid': int(fields['pid']),
                     'name': fields.get('command', ''), 'type': fields.get('type', 'Unknown'),
                     'engines': engines, 'activity': engines['compute'], 'memory': None})
    return rows


def parse_gpu_xml(text):
    """Unsupported counters remain None, including separate 3D/copy engines."""
    root = ET.fromstring(text)
    devices = []
    for index, gpu in enumerate(root.findall('gpu')):
        utilization = gpu_value(gpu, 'utilization/gpu_util')
        power = gpu_value(gpu, 'gpu_power_readings/power_draw')
        if power is None:
            power = gpu_value(gpu, 'power_readings/power_draw')
        pci = normalize_pci(gpu.findtext('pci/pci_bus_id', gpu.get('id', '')))
        devices.append({
            'id': 'pci:'+pci if pci else 'nvidia:'+str(index), 'pci': pci, 'vendor': 'NVIDIA',
            'source': 'NVIDIA management telemetry', 'engine_scope': '', 'engine_sources': {},
            'processes': [{'pid': int(process.findtext('pid')),
                           'name': process.findtext('process_name', ''),
                           'type': process.findtext('type', 'Unknown'),
                           'memory': memory_bytes(process.findtext('used_memory')),
                           'engines': {}, 'activity': None}
                          for process in gpu.findall('processes/process_info')
                          if process.findtext('pid', '').isdigit()],
            'index': str(index), 'name': gpu.findtext('product_name', 'NVIDIA GPU'),
            'driver': root.findtext('driver_version', 'Unknown'),
            'utilization': utilization,
            'memory_used_mib': gpu_value(gpu, 'fb_memory_usage/used'),
            'memory_total_mib': gpu_value(gpu, 'fb_memory_usage/total'),
            'temperature': gpu_value(gpu, 'temperature/gpu_temp'),
            'power': power,
            'power_limit': gpu_value(gpu, 'power_readings/power_limit'),
            'clock_mhz': gpu_value(gpu, 'clocks/sm_clock'),
            'engines': {
                'compute': utilization,
                'memory': gpu_value(gpu, 'utilization/memory_util'),
                'encode': gpu_value(gpu, 'utilization/encoder_util'),
                'decode': gpu_value(gpu, 'utilization/decoder_util'),
                'jpeg': gpu_value(gpu, 'utilization/jpeg_util'),
                'ofa': gpu_value(gpu, 'utilization/ofa_util'),
            },
        })
    return devices


def normalize_pci(value):
    parts = value.lower().split(':')
    if len(parts) == 3:
        try:
            parts[0] = f'{int(parts[0], 16):04x}'
        except ValueError:
            pass
    return ':'.join(parts)


def engine_class(name):
    name = re.sub(r'[-_]?\d+$', '', name.lower())
    return {'render': '3d', 'gfx': '3d', 'graphics': '3d', '3d': '3d',
            'copy': 'copy', 'dma': 'copy', 'sdma': 'copy', 'blitter': 'copy',
            'compute': 'compute', 'enc': 'encode', 'encode': 'encode',
            'dec': 'decode', 'decode': 'decode', 'jpeg': 'jpeg',
            'vcn_dec': 'decode', 'vcn_enc': 'encode', 'uvd': 'decode', 'vce': 'encode', 'vpe': 'enhance',
            'video': 'video', 'video-enhance': 'enhance', 'video_enhance': 'enhance'}.get(name)


def parse_fdinfo(text):
    fields = dict(line.split(':', 1) for line in text.splitlines() if ':' in line)
    fields = {key: value.strip() for key, value in fields.items()}
    if 'drm-client-id' not in fields or 'drm-driver' not in fields:
        return None
    counters = {}
    for key, value in fields.items():
        engine = None
        kind = None
        if key.startswith('drm-engine-') and not key.startswith('drm-engine-capacity-'):
            engine, kind = key.removeprefix('drm-engine-'), 'ns'
        elif key.startswith('drm-cycles-'):
            engine, kind = key.removeprefix('drm-cycles-'), 'cycles'
        if engine is None or engine_class(engine) is None:
            continue
        try:
            counter = int(value.split()[0])
            capacity = max(1, int(fields.get('drm-engine-capacity-'+engine, '1')))
            total = int(fields['drm-total-cycles-'+engine].split()[0]) if kind == 'cycles' else None
        except (ValueError, IndexError, KeyError):
            continue
        # Prefer nanoseconds when the driver also supplies cycle counters.
        if engine not in counters or kind == 'ns':
            counters[engine] = (counter, total, capacity, kind)
    resident = {key.removeprefix('drm-resident-'):memory_bytes(value) for key,value in fields.items() if key.startswith('drm-resident-')}
    if not resident:
        resident = {key.removeprefix('drm-memory-'):memory_bytes(value) for key,value in fields.items() if key.startswith('drm-memory-')}
    local = [value for key,value in resident.items() if value is not None and re.fullmatch(r'(vram|local)\d*', key)]
    return {'pci': normalize_pci(fields.get('drm-pdev', '')), 'client': fields['drm-client-id'],
            'driver': fields['drm-driver'], 'counters': counters,
            'memory': sum(local) if local else None}


class GPUCollector:
    """NVIDIA management telemetry plus Linux DRM/sysfs support for other GPUs."""
    def __init__(self, sysroot='/sys/class/drm', procroot='/proc'):
        self.sysroot, self.procroot = Path(sysroot), Path(procroot)
        self.previous = {}
        self.timestamp = None
        self.processes = []

    def drm_devices(self):
        devices = []
        for card in sorted(self.sysroot.glob('card[0-9]*')):
            if not re.fullmatch(r'card\d+', card.name) or not (card/'device').exists():
                continue
            path = card/'device'
            vendor = {'0x8086':'Intel', '0x1002':'AMD', '0x10de':'NVIDIA'}.get(read(path/'vendor'))
            if vendor is None:
                continue
            pci = normalize_pci(path.resolve().name)
            driver = (path/'driver').resolve().name if (path/'driver').exists() else vendor
            def sensor(filename, divisor=1):
                value = number(read(path/filename))
                return value/divisor if value is not None else None
            hwmons = sorted((path/'hwmon').glob('hwmon*'))
            def hwmon(names, divisor):
                for hw in hwmons:
                    for name in names:
                        value = number(read(hw/name))
                        if value is not None:
                            return value/divisor
                return None
            busy, memory = sensor('gpu_busy_percent'), sensor('mem_busy_percent')
            devices.append({'id': 'pci:'+pci, 'pci': pci, 'index': card.name, 'vendor': vendor,
                            'name': read(path/'product_name') or f'{vendor} GPU ({read(path/"device", card.name)})',
                            'driver': driver, 'utilization': busy,
                            'memory_used_mib': sensor('mem_info_vram_used', 1048576),
                            'memory_total_mib': sensor('mem_info_vram_total', 1048576),
                            'temperature': hwmon(['temp1_input'], 1000),
                            'power': hwmon(['power1_average', 'power1_input'], 1000000),
                            'clock_mhz': hwmon(['freq1_input'], 1000000),
                            'engines': {'memory': memory}, 'engine_sources': {},
                            'source': 'Linux DRM/sysfs', 'engine_scope': ''})
        return devices

    def client_engines(self, devices, now):
        """Sum unique readable clients; cannot include inaccessible processes."""
        by_pci = {d['pci']:d for d in devices if d.get('pci')}
        by_driver = {}
        for device in devices:
            by_driver.setdefault(device['driver'], []).append(device)
        clients, owners, memory = {}, {}, {}
        try:
            processes = list(self.procroot.iterdir())
        except OSError:
            processes = []
        for process in processes:
            if not process.name.isdigit():
                continue
            try:
                files = list((process/'fdinfo').iterdir())
            except OSError:
                continue
            for file in files:
                text = read(file)
                if 'drm-driver:' not in text:
                    continue
                client = parse_fdinfo(text)
                if not client:
                    continue
                device = by_pci.get(client['pci'])
                if device is None and not client['pci']:
                    matches = by_driver.get(client['driver'], [])
                    device = matches[0] if len(matches) == 1 else None
                if device is None:
                    continue
                identity = (device['id'], client['client'])
                owners.setdefault(identity, set()).add(int(process.name))
                if client['memory'] is not None:
                    memory[identity] = max(memory.get(identity, 0), client['memory'])
                # Shared/duplicated fds describe the same client. Keep latest maxima.
                current = clients.setdefault(identity, {})
                for engine, value in client['counters'].items():
                    if engine not in current or value[0] > current[engine][0]:
                        current[engine] = value
        elapsed = now-self.timestamp if self.timestamp is not None else None
        updated, raw_rates, client_rates = {}, {}, {}
        for identity, counters in clients.items():
            for engine, (value, total, capacity, kind) in counters.items():
                key = (*identity, engine, kind)
                old = self.previous.get(key)
                # DRM specifies retaining the high-water mark for decreasing counters.
                value = max(value, old[0]) if old else value
                total = max(total, old[1]) if old and total is not None and old[1] is not None else total
                updated[key] = (value, total)
                if old is None or elapsed is None or elapsed <= 0:
                    continue
                denominator = elapsed*1e9 if kind == 'ns' else total-old[1]
                if denominator <= 0:
                    continue
                rate = max(0, (value-old[0])/denominator/capacity*100)
                client_rates.setdefault(identity, {})[engine] = rate
                raw_key = (identity[0], engine)
                raw_rates[raw_key] = raw_rates.get(raw_key, 0)+rate
        self.previous, self.timestamp = updated, now
        output = {}
        for (device_id, engine), value in raw_rates.items():
            key = engine_class(engine)
            # Multiple instances in the same class: report the busiest instance.
            group = output.setdefault(device_id, {})
            group[key] = max(group.get(key, 0), min(100, value))
        process_rows = {}
        for identity, pids in owners.items():
            for pid in pids:
                key = (identity[0], pid)
                row = process_rows.setdefault(key, {'gpu_id':identity[0], 'pid':pid, 'name':'', 'type':'DRM',
                                                    'memory':None, 'engines':{}, 'activity':None, '_rates':{}})
                if len(pids) > 1:
                    row['type'] = 'DRM (shared)'
                if identity in memory:
                    row['memory'] = (row['memory'] or 0)+memory[identity]
                for engine, rate in client_rates.get(identity, {}).items():
                    row['_rates'][engine] = row['_rates'].get(engine, 0)+rate
        for row in process_rows.values():
            for engine, rate in row.pop('_rates').items():
                group = engine_class(engine)
                row['engines'][group] = max(row['engines'].get(group, 0), min(100, rate))
            row['activity'] = max(row['engines'].values(), default=None)
        self.processes = list(process_rows.values())
        return output

    def collect(self):
        now = time.monotonic()
        devices = self.drm_devices()
        error, process_warning, pmon_rows = None, None, []
        if (not devices or any(d['vendor'] == 'NVIDIA' for d in devices)) and shutil.which('nvidia-smi'):
            try:
                result = subprocess.run(['nvidia-smi', '-q', '-x'], capture_output=True, text=True, timeout=3)
                if result.returncode:
                    raise RuntimeError((result.stderr or result.stdout).strip()[:600])
                nvidia = parse_gpu_xml(result.stdout)
                if nvidia:
                    try:
                        process_result = subprocess.run(['nvidia-smi', 'pmon', '-s', 'u', '-c', '1'], capture_output=True, text=True, timeout=3)
                        if process_result.returncode == 0:
                            pmon_rows = parse_pmon(process_result.stdout)
                        else:
                            process_warning = 'Per-process NVIDIA activity is unavailable; process identities and memory may still be reported.'
                    except (OSError, subprocess.TimeoutExpired):
                        process_warning = 'Per-process NVIDIA activity timed out or is unavailable.'
                for device in nvidia:
                    matching = next((i for i,d in enumerate(devices) if d['id'] == device['id']), None)
                    if matching is None:
                        devices.append(device)
                    else:
                        base = devices[matching]
                        for key, value in device.items():
                            if key == 'engines':
                                base['engines'].update({k:v for k,v in value.items() if v is not None})
                            elif value is not None:
                                base[key] = value
            except (OSError, subprocess.TimeoutExpired, RuntimeError, ET.ParseError) as exc:
                error = str(exc)
        engines = self.client_engines(devices, now) if devices else {}
        if not devices:
            self.previous, self.timestamp, self.processes = {}, now, []
        for device in devices:
            for key, value in engines.get(device['id'], {}).items():
                if device['engines'].get(key) is None:
                    device['engines'][key] = value
                    device.setdefault('engine_sources', {})[key] = 'DRM readable clients; busiest instance'
                    device['engine_scope'] = 'DRM engine charts cover readable processes only; inaccessible clients are excluded.'
        # Merge management and DRM rows by GPU+PID; management readings take precedence.
        rows = {}
        for device in devices:
            for process in device.pop('processes', []):
                key = (device['id'], process['pid'])
                previous = rows.get(key)
                if previous is None:
                    rows[key] = {**process, 'gpu_id':device['id']}
                else:
                    if process['memory'] is not None:
                        previous['memory'] = max(previous['memory'] or 0, process['memory'])
                    if process['type'] != previous['type']:
                        previous['type'] = 'C+G'
        for process in pmon_rows:
            device = next((d for d in devices if d['vendor'] == 'NVIDIA' and d['index'] == process['index']), None)
            if device is None:
                continue
            key = (device['id'], process['pid'])
            row = rows.setdefault(key, {**process, 'gpu_id':device['id']})
            row['engines'] = process['engines']
            row['activity'] = process['activity']
        for process in self.processes:
            key = (process['gpu_id'], process['pid'])
            if key not in rows:
                rows[key] = process
            else:
                row = rows[key]
                if row['memory'] is None:
                    row['memory'] = process['memory']
                if row['activity'] is None:
                    row['activity'] = process['activity']
                for engine, value in process['engines'].items():
                    if row['engines'].get(engine) is None:
                        row['engines'][engine] = value
        by_id = {d['id']:d for d in devices}
        for row in rows.values():
            path = self.procroot/str(row['pid'])
            try:
                uid_line = next((line.split() for line in read(path/'status').splitlines() if line.startswith('Uid:')), [])
                uid = int(uid_line[2]) if len(uid_line) >= 3 else path.stat().st_uid
                row['user'] = username(uid)
            except (OSError, ValueError):
                row['user'] = 'Unknown'
            row['name'] = read(path/'comm') or row.get('name') or 'Unknown'
            row['gpu'] = by_id[row['gpu_id']]['index']+' · '+by_id[row['gpu_id']]['vendor']
        return {'available': bool(devices), 'error': error if not devices else None,
                'devices': devices, 'warning': error if devices else None,
                'processes': list(rows.values()), 'process_warning': process_warning}



def mount_unescape(value):
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match.group(1), 8)), value)


def filesystem_usage():
    """Mounted data filesystems, excluding kernel, RAM and Snap image mounts."""
    virtual = {'proc', 'sysfs', 'devtmpfs', 'devpts', 'tmpfs', 'cgroup', 'cgroup2',
               'securityfs', 'pstore', 'debugfs', 'tracefs', 'configfs', 'fusectl',
               'mqueue', 'hugetlbfs', 'autofs', 'binfmt_misc', 'rpc_pipefs',
               'squashfs', 'nsfs', 'efivarfs', 'bpf'}
    mounts = {}
    for line in read('/proc/self/mountinfo').splitlines():
        try:
            before, after = line.split(' - ', 1)
            fields, device = before.split(), after.split()
            mount, fstype, source = mount_unescape(fields[4]), device[0], mount_unescape(device[1])
            if fstype in virtual or (fstype == 'overlay' and mount != '/'):
                continue
            mounts[mount] = (source, fstype, 'ro' in fields[5].split(','))
        except (ValueError, IndexError):
            continue
    results = []
    for mount, (source, fstype, readonly) in sorted(mounts.items()):
        try:
            # Ignore bind-mounted files, while preserving mounted directories.
            if not os.path.isdir(mount):
                continue
            v = os.statvfs(mount)
            total = v.f_blocks*v.f_frsize
            used = (v.f_blocks-v.f_bfree)*v.f_frsize
            available = max(0, v.f_bavail*v.f_frsize)
            if total <= 0:
                continue
            results.append({'device': source, 'mount': mount, 'type': fstype,
                            'total': total, 'used': used, 'available': available,
                            'percent': used/(used+available)*100 if used+available else 0,
                            'readonly': readonly})
        except OSError:
            continue
    return results


class Monitor:
    def __init__(self):
        self.lock = threading.Lock()
        self.snapshot = {}
        self.previous = {}
        self.process_previous = {}
        self.gpu = {'available': False, 'error': 'Waiting for GPU telemetry', 'devices': []}
        self.gpu_collector = GPUCollector()
        self.filesystems = []
        self.ticks = os.sysconf('SC_CLK_TCK')
        self.page_size = os.sysconf('SC_PAGE_SIZE')

    def filesystem_loop(self):
        # Separate worker: slow remote mounts must not stall CPU/network sampling.
        while True:
            try:
                self.filesystems = filesystem_usage()
            except OSError:
                pass
            time.sleep(5)

    def gpu_loop(self):
        while True:
            try:
                self.gpu = self.gpu_collector.collect()
            except Exception as exc:
                self.gpu = {'available': False, 'error': str(exc), 'devices': []}
            time.sleep(3)

    def sample(self):
        now = time.monotonic()
        elapsed = now - self.previous.get('time', now - 1)
        cpu_raw = {}
        for line in read('/proc/stat').splitlines():
            parts = line.split()
            if parts and parts[0].startswith('cpu'):
                vals = list(map(int, parts[1:9]))
                cpu_raw[parts[0]] = (sum(vals), vals[3] + vals[4])
        cpu = {}
        for key, (total, idle) in cpu_raw.items():
            old_total, old_idle = self.previous.get('cpu', {}).get(key, (total, idle))
            delta = total - old_total
            cpu[key] = max(0, min(100, 100 * (1 - (idle-old_idle)/delta))) if delta else 0
        mem = {}
        for line in read('/proc/meminfo').splitlines():
            key, value = line.split(':', 1)
            mem[key] = int(value.split()[0]) * 1024
        total = mem.get('MemTotal', 1)
        available = mem.get('MemAvailable', 0)
        networks, net_raw = [], {}
        for line in read('/proc/net/dev').splitlines()[2:]:
            name, values = line.split(':', 1)
            name = name.strip()
            if name == 'lo':
                continue
            values = values.split()
            received, sent = int(values[0]), int(values[8])
            net_raw[name] = (received, sent)
            old = self.previous.get('net', {}).get(name, (received, sent))
            netpath = Path('/sys/class/net')/name
            speed = number(read(netpath/'speed')) if (netpath/'device').exists() else None
            speed = speed if speed is not None and speed > 0 else None
            networks.append({'name': name, 'received': received, 'sent': sent,
                             'rx': max(0, (received-old[0])/elapsed), 'tx': max(0, (sent-old[1])/elapsed),
                             'state': read(netpath/'operstate', 'unknown'), 'speed_mbps': speed,
                             'duplex': read(netpath/'duplex', 'unknown')})
        disks, disk_raw = [], {}
        for line in read('/proc/diskstats').splitlines():
            v = line.split()
            name = v[2]
            if not Path('/sys/block', name).exists() or name.startswith(('loop', 'ram')):
                continue
            counters = (int(v[5])*512, int(v[9])*512, int(v[12]))
            disk_raw[name] = counters
            old = self.previous.get('disk', {}).get(name, counters)
            disks.append({'name': name, 'read': max(0, (counters[0]-old[0])/elapsed), 'write': max(0, (counters[1]-old[1])/elapsed), 'active': min(100, max(0, (counters[2]-old[2])/(elapsed*10)))})
        processes, proc_raw = [], {}
        threads = 0
        for path in Path('/proc').iterdir():
            if not path.name.isdigit():
                continue
            try:
                stat = (path/'stat').read_text()
                end = stat.rindex(')')
                vals = stat[end+2:].split()
                ticks = int(vals[11]) + int(vals[12])
                identity = (int(path.name), vals[19])
                proc_raw[identity] = ticks
                old = self.process_previous.get(identity, ticks)
                rss = max(0, int(vals[21])) * self.page_size
                threads += int(vals[17])
                try:
                    executable = os.readlink(path/'exe').removesuffix(' (deleted)')
                except OSError:
                    executable = ''
                processes.append({'executable': executable, 'pid': int(path.name), 'name': stat[stat.index('(')+1:end], 'state': vals[0], 'cpu': max(0, (ticks-old)/self.ticks/elapsed*100), 'memory': rss, 'memory_percent': rss/total*100, 'threads': int(vals[17])})
            except (OSError, ValueError, IndexError, ProcessLookupError):
                continue
        self.process_previous = proc_raw
        temps = []
        for path in Path('/sys/class/thermal').glob('thermal_zone*'):
            value = number(read(path/'temp'))
            if value is not None:
                temps.append({'name': read(path/'type', path.name), 'value': value/1000})
        frequencies = [number(read(p)) for p in Path('/sys/devices/system/cpu').glob('cpu[0-9]*/cpufreq/scaling_cur_freq')]
        frequencies = [v/1000 for v in frequencies if v is not None]
        usage = shutil.disk_usage('/')
        snapshot = {
            'timestamp': time.time(), 'host': platform.node(), 'system': platform.system(), 'kernel': platform.release(), 'arch': platform.machine(),
            'uptime': number(read('/proc/uptime').split(' ')[0]),
            'cpu': {'percent': cpu.get('cpu', 0), 'cores': [v for k,v in cpu.items() if k != 'cpu'], 'count': os.cpu_count(), 'frequency_mhz': sum(frequencies)/len(frequencies) if frequencies else None, 'load': os.getloadavg()},
            'memory': {'total': total, 'available': available, 'used': total-available, 'percent': (total-available)/total*100, 'cached': mem.get('Cached',0), 'buffers': mem.get('Buffers',0), 'swap_total': mem.get('SwapTotal',0), 'swap_used': mem.get('SwapTotal',0)-mem.get('SwapFree',0)},
            'filesystems': self.filesystems, 'network': networks, 'disks': disks, 'storage': {'total': usage.total, 'used': usage.used, 'free': usage.free},
            'processes': sorted(processes, key=lambda p:p['cpu'], reverse=True), 'threads': threads, 'temperatures': temps, 'gpu': self.gpu,
        }
        self.previous = {'time': now, 'cpu': cpu_raw, 'net': net_raw, 'disk': disk_raw}
        with self.lock:
            self.snapshot = snapshot

    def loop(self):
        while True:
            start = time.monotonic()
            try:
                self.sample()
            except Exception as exc:
                print(f'Sampling error: {exc}', flush=True)
            time.sleep(max(.05, 1-(time.monotonic()-start)))
