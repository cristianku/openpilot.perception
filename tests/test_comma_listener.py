"""app/comma_listener.py, the script the app runs on the comma: its lines, its pace, when it stops, and that it
only reads. openpilot's messaging is faked (a scripted 100 Hz carState on a fake clock); the last tests run the
script the way the app does, from standard input, against a fake openpilot package. Nothing talks to a comma."""
from __future__ import annotations

import ast
import importlib.util
import json
import math
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

SCRIPT = Path(__file__).resolve().parent.parent / 'app' / 'comma_listener.py'


def load_listener():
    previous = sys.dont_write_bytecode  # the script turns it on for the comma; not for this test run
    sys.dont_write_bytecode = True       # and no app/__pycache__ from loading it here
    spec = importlib.util.spec_from_file_location('comma_listener', SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sys.dont_write_bytecode = previous
    return module


listener = load_listener()

RADAR_FIELDS = ('stockRadarLeadDistance', 'stockRadarLeadRelativeSpeed', 'stockRadarLeadValid',
                'stockRadarMeasurementTimeNanos', 'stockRadarRelativeSpeedValid')
START = 5000.0
TICK = 0.01  # carState at 100 Hz


def car_state(t: float, with_radar: bool = True, distance: float = 2.25):
    fields = ('vEgo',) + (RADAR_FIELDS if with_radar else ())
    state = SimpleNamespace(schema=SimpleNamespace(fieldnames=fields), vEgo=0.0123456)
    if with_radar:
        state.stockRadarLeadDistance = distance
        state.stockRadarLeadRelativeSpeed = 0.0
        state.stockRadarLeadValid = True
        state.stockRadarMeasurementTimeNanos = int((t - 0.02) * 1e9)
        state.stockRadarRelativeSpeedValid = False
    return state


class World:
    """A fake clock and a car switched on during [on, off): carState every TICK, conflated like SubMaster's."""

    def __init__(self, on: float | None = None, off: float | None = None, with_radar: bool = True,
                 distance: float = 2.25, names=None):
        self.now = START
        self.on, self.off = on, off
        self.with_radar = with_radar
        self.distance = distance
        self.names = names or (lambda now: ['00000134--a5c8197883--0', '00000131--65b1397d4b--0', 'boot'])
        self.updates = []    # (clock time, timeout ms) of every SubMaster.update
        self.sleeps = []
        self.update_error = None

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        assert seconds >= 0
        self.sleeps.append(seconds)
        self.now += seconds

    def param(self, name: str):
        car_on = self.on is not None and self.on <= self.now < self.off
        return {'GitCommit': '1b6a472ae61d2d61866902cb48a5b3ab8bd11d07', 'GitBranch': 'psa-torque-sunny-testing',
                'IsOffroad': '0' if car_on else '1'}.get(name)

    def list_names(self):
        return self.names(self.now)

    def message_times(self, after: float, until: float) -> list[float]:
        """Times of the carState messages in (after, until]."""
        if self.on is None:
            return []
        first = 0 if after < self.on else math.floor((after - self.on) / TICK + 1e-9) + 1
        times = []
        k = first
        while True:
            t = self.on + k * TICK
            if t > until + 1e-9 or t >= self.off - 1e-9:
                return times
            times.append(t)
            k += 1


class FakeSubMaster:
    def __init__(self, world: World, services):
        assert services == ['carState']
        self.world = world
        self.last_read = -math.inf
        self.updated = {'carState': False}
        self.logMonoTime = {'carState': 0}
        self.data = {'carState': car_state(0.0, world.with_radar)}

    def __getitem__(self, service):
        return self.data[service]

    def update(self, timeout: int = 100) -> None:
        world = self.world
        world.updates.append((world.now, timeout))
        if world.update_error:
            raise world.update_error
        self.updated = {'carState': False}
        ready = world.message_times(self.last_read, world.now)
        if not ready:
            coming = world.message_times(world.now, world.now + timeout / 1000)
            if coming:
                world.now = coming[0]
                ready = coming[:1]
            else:
                world.now += timeout / 1000
                return
        t = ready[-1]  # conflated: only the newest
        self.last_read = t
        self.updated['carState'] = True
        self.logMonoTime['carState'] = int(t * 1e9)
        self.data['carState'] = car_state(t, world.with_radar, world.distance)


def run(world: World, max_seconds: float = 30.0, out=None):
    out = out if out is not None else Lines()
    messaging = SimpleNamespace(SubMaster=lambda services: FakeSubMaster(world, services))
    code = listener.Listener(messaging, out, clock=world.clock, sleep=world.sleep, list_names=world.list_names,
                             param=world.param, max_seconds=max_seconds).run()
    return code, out


class Lines:
    """Standard output: one JSON object per line, each flushed."""

    def __init__(self, world: World | None = None, fail_after: int | None = None):
        self.text = ''
        self.flushes = 0
        self.fail_after = fail_after
        self.world = world
        self.times = []

    def write(self, text: str) -> None:
        if self.fail_after is not None and self.text.count('\n') >= self.fail_after:
            raise BrokenPipeError(32, 'Broken pipe')
        self.text += text

    def flush(self) -> None:
        self.flushes += 1

    @property
    def messages(self) -> list[dict]:
        return [json.loads(line) for line in self.text.splitlines()]

    def of(self, kind: str) -> list[dict]:
        return [m for m in self.messages if m['type'] == kind]


class TimedLines(Lines):
    def write(self, text: str) -> None:
        super().write(text)
        self.times.append(self.world.now)


class Recordings(unittest.TestCase):
    def test_newest_by_route_counter_then_highest_segment(self):
        names = ['boot', 'crash', '00000099--ffffffffff--0', '00000134--a5c8197883--2', '00000134--a5c8197883--10',
                 '00000134--a5c8197883--9', '00000133--43b9cd9c08--0', '0000013--a5c8197883--0',
                 '00000135--A5C8197883--0', '00000135--a5c8197883--0.part']
        self.assertEqual(listener.newest_recording(names), ('00000134--a5c8197883', '00000134--a5c8197883--10'))
        self.assertEqual(listener.newest_recording(['boot', 'params']), (None, None))
        self.assertEqual(listener.newest_recording([]), (None, None))

    def test_missing_folder_and_param_read_as_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(listener.list_realdata(os.path.join(directory, 'absent')), [])
            self.assertIsNone(listener.read_param('GitCommit', folder=directory))
            Path(directory, 'GitCommit').write_bytes(b'1b6a472a\n')
            self.assertEqual(listener.read_param('GitCommit', folder=directory), '1b6a472a')


class Offroad(unittest.TestCase):
    def test_hello_then_a_status_a_second_then_bye(self):
        world = World()
        code, out = run(world, max_seconds=5)
        self.assertEqual(code, 0)
        messages = out.messages
        hello = messages[0]
        self.assertEqual(hello['type'], 'hello')
        self.assertEqual(hello['version'], 1)
        self.assertEqual(hello['git_commit'], '1b6a472ae61d2d61866902cb48a5b3ab8bd11d07')
        self.assertEqual(hello['git_branch'], 'psa-torque-sunny-testing')
        self.assertEqual((hello['route'], hello['segment']), ('00000134--a5c8197883', '00000134--a5c8197883--0'))
        self.assertEqual(hello['python'], sys.version.split()[0])
        self.assertTrue(hello['radar_fields'])
        self.assertEqual(hello['max_seconds'], 5)
        self.assertEqual(messages[-1], {'type': 'bye', 'reason': 'time_limit'})
        statuses = out.of('status')
        self.assertIn(len(statuses), (5, 6))
        self.assertTrue(all(s == {'type': 'status', 'onroad': False, 'route': '00000134--a5c8197883',
                                  'segment': '00000134--a5c8197883--0'} for s in statuses))
        self.assertEqual(out.of('radar'), [])
        self.assertEqual(out.flushes, len(messages))
        # It sleeps in SubMaster.update, about once a second, never a busy loop.
        self.assertLessEqual(len(world.updates), 7)
        self.assertTrue(all(timeout <= 1000 for _, timeout in world.updates))

    def test_route_refreshed_every_ten_seconds(self):
        world = World(names=lambda now: ['00000134--a5c8197883--0'] + (
            ['00000135--0011223344--0'] if now >= START + 12 else []))
        out = TimedLines(world)
        run(world, max_seconds=25, out=out)
        statuses = [(t - START, m['route']) for t, m in zip(out.times, out.messages, strict=True)
                    if m['type'] == 'status']
        self.assertEqual(statuses[0][1], '00000134--a5c8197883')
        self.assertEqual(statuses[-1][1], '00000135--0011223344')
        # Seen at the first refresh after it appeared (10 s after the previous one, at 20 s), not before.
        first_seen = next(t for t, route in statuses if route == '00000135--0011223344')
        self.assertGreaterEqual(first_seen, 19.9)
        self.assertLessEqual(first_seen, 20.1)


class Onroad(unittest.TestCase):
    def test_radar_at_10_hz_while_carstate_arrives(self):
        world = World(on=START + 2, off=START + 6)
        out = TimedLines(world)
        code, _ = run(world, max_seconds=10, out=out)
        self.assertEqual(code, 0)
        radar = out.of('radar')
        self.assertIn(len(radar), range(38, 42))
        first = radar[0]
        self.assertEqual(set(first), {'type', 'mono_ns', 'onroad', 'v_ego', 'valid', 'distance_m', 'rel_speed_mps',
                                      'rel_speed_valid', 'measured_ns'})
        self.assertEqual((first['onroad'], first['valid'], first['distance_m']), (True, True, 2.25))
        self.assertEqual((first['rel_speed_mps'], first['rel_speed_valid']), (0.0, False))
        self.assertEqual(first['v_ego'], 0.012)
        self.assertEqual(first['mono_ns'] - first['measured_ns'], 20_000_000)
        gaps = [(b['mono_ns'] - a['mono_ns']) / 1e9 for a, b in zip(radar, radar[1:], strict=False)]
        self.assertTrue(all(0.09 <= gap <= 0.111 for gap in gaps), gaps)
        # About 10 wake-ups a second while the car is on, not 100: it sleeps to the next tick.
        on_updates = [t for t, _ in world.updates if START + 2 <= t < START + 6]
        self.assertLessEqual(len(on_updates), 45)

    def test_status_at_the_start_and_end_of_carstate_and_every_ten_seconds(self):
        world = World(on=START + 2, off=START + 25)
        out = TimedLines(world)
        run(world, max_seconds=30, out=out)
        statuses = [(round(t - START, 2), m['onroad']) for t, m in zip(out.times, out.messages, strict=True)
                    if m['type'] == 'status']
        onroad = [t for t, on in statuses if on]
        self.assertEqual(onroad[0], 2.0)                  # at once with the first carState
        self.assertEqual(len([t for t in onroad if 2 <= t < 25]), 3)  # 2, 12, 22: every 10 s anyway
        offroad_after = [t for t, on in statuses if not on and t >= 25]
        self.assertLessEqual(offroad_after[0], 26.1)     # a second after the last carState
        self.assertIn(len(offroad_after), (4, 5, 6))      # then once a second

    def test_new_recording_seen_soon_after_the_car_starts(self):
        world = World(on=START + 2, off=START + 20, names=lambda now: ['00000134--a5c8197883--0'] + (
            ['00000135--0011223344--0', '00000135--0011223344--1'] if now >= START + 4 else []))
        out = TimedLines(world)
        run(world, max_seconds=12, out=out)
        new = next(t for t, m in zip(out.times, out.messages, strict=True)
                   if m['type'] == 'status' and m['route'] == '00000135--0011223344')
        self.assertLessEqual(new - START, 6.1)            # refreshed every 2 s in the first 30 s

    def test_schema_without_radar_fields(self):
        world = World(on=START, off=START + 3, with_radar=False)
        _, out = run(world, max_seconds=2)
        self.assertFalse(out.messages[0]['radar_fields'])
        radar = out.of('radar')[0]
        self.assertEqual((radar['valid'], radar['distance_m'], radar['measured_ns']), (False, None, None))

    def test_values_that_are_not_numbers_are_null(self):
        world = World(on=START, off=START + 3, distance=float('nan'))
        _, out = run(world, max_seconds=1)
        self.assertNotIn('NaN', out.text)
        self.assertIsNone(out.of('radar')[0]['distance_m'])


class Stops(unittest.TestCase):
    def test_closed_stdout_ends_it(self):
        world = World(on=START, off=START + 60)
        out = Lines(fail_after=3)
        code, _ = run(world, max_seconds=60, out=out)
        self.assertEqual(code, 0)
        self.assertEqual(len(out.messages), 3)
        self.assertLess(world.now - START, 1)

    def test_unexpected_error_is_reported_then_it_ends(self):
        world = World()
        world.update_error = RuntimeError('msgq went away')
        code, out = run(world)
        self.assertEqual(code, 1)
        self.assertEqual(out.messages[-1], {'type': 'error', 'message': 'RuntimeError: msgq went away'})
        self.assertEqual([m['type'] for m in out.messages], ['hello', 'status', 'error'])


ALLOWED_IMPORTS = {'sys', 'json', 'math', 'os', 're', 'signal', 'time', 'openpilot.cereal'}
ALLOWED_OS = {'listdir', 'path', 'getpid', '_exit'}
FORBIDDEN_NAMES = {'Params', 'PubMaster', 'pub_sock', 'subprocess', 'socket', 'popen', 'system', 'remove',
                   'unlink', 'rename', 'replace', 'mkdir', 'makedirs', 'rmdir', 'chmod', 'kill', 'exec', 'eval',
                   'write_text', 'write_bytes', 'urlopen'}


class OnlyReads(unittest.TestCase):
    """The comma runs this file: a check on its source that it can only read."""

    def setUp(self):
        self.tree = ast.parse(SCRIPT.read_text(encoding='utf-8'))

    def test_imports_and_os_calls(self):
        imported = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module)
                if node.module == 'openpilot.cereal':
                    self.assertEqual([alias.name for alias in node.names], ['messaging'])
        self.assertEqual(imported - ALLOWED_IMPORTS, set())
        used_os = {node.attr for node in ast.walk(self.tree) if isinstance(node, ast.Attribute)
                   and isinstance(node.value, ast.Name) and node.value.id == 'os'}
        self.assertEqual(used_os - ALLOWED_OS, set())

    def test_no_writing_call(self):
        names = {node.attr if isinstance(node, ast.Attribute) else node.id for node in ast.walk(self.tree)
                 if isinstance(node, ast.Attribute | ast.Name)}
        self.assertEqual(names & FORBIDDEN_NAMES, set())
        opens = [node for node in ast.walk(self.tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == 'open']
        self.assertEqual(len(opens), 1)
        self.assertEqual([ast.literal_eval(arg) for arg in opens[0].args[1:]], ['rb'])
        subscriptions = [node for node in ast.walk(self.tree) if isinstance(node, ast.Call)
                         and isinstance(node.func, ast.Attribute) and node.func.attr == 'SubMaster']
        self.assertEqual([ast.literal_eval(node.args[0]) for node in subscriptions], [['carState']])


FAKE_MESSAGING = '''
import time
from types import SimpleNamespace

FIELDS = ('vEgo', 'stockRadarLeadDistance', 'stockRadarLeadRelativeSpeed', 'stockRadarLeadValid',
          'stockRadarMeasurementTimeNanos', 'stockRadarRelativeSpeedValid')


class SubMaster:
    def __init__(self, services):
        self.updated = {s: False for s in services}
        self.logMonoTime = {s: 0 for s in services}
        self.data = {s: SimpleNamespace(schema=SimpleNamespace(fieldnames=FIELDS)) for s in services}

    def __getitem__(self, s):
        return self.data[s]

    def update(self, timeout=100):
        time.sleep(timeout / 1000)  # offroad: no carState ever
'''


class FromStdin(unittest.TestCase):
    """The script as the app runs it: python -u - with the script on standard input, from the openpilot folder
    (here a fake openpilot package in a temporary folder)."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.cwd = Path(self.folder.name)
        self.env = {key: value for key, value in os.environ.items()
                    if key not in ('PYTHONPATH', 'PYTHONSAFEPATH', 'PYTHONDONTWRITEBYTECODE')}

    def fake_openpilot(self):
        cereal = self.cwd / 'openpilot' / 'cereal'
        cereal.mkdir(parents=True)
        (self.cwd / 'openpilot' / '__init__.py').write_text('')
        (cereal / '__init__.py').write_text('')
        (cereal / 'messaging.py').write_text(FAKE_MESSAGING)

    def start(self):
        process = subprocess.Popen([sys.executable, '-u', '-'], cwd=self.cwd, env=self.env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: process.poll() is None and process.kill())
        self.addCleanup(lambda: (process.stdout.closed or process.stdout.close(), process.stderr.close()))
        process.stdin.write(SCRIPT.read_bytes())
        process.stdin.close()  # Python runs the script at the end of its input
        return process

    def read_line(self, process, timeout=10.0) -> dict:
        ready, _, _ = select.select([process.stdout], [], [], timeout)
        self.assertTrue(ready, 'no line from the listener')
        return json.loads(process.stdout.readline())

    def test_without_openpilot_an_error_line(self):
        process = self.start()
        line = self.read_line(process)
        self.assertEqual(line['type'], 'error')
        self.assertIn('openpilot messaging', line['message'])
        self.assertEqual(process.wait(timeout=10), 1)

    def test_hello_status_and_the_signals_that_end_it(self):
        self.fake_openpilot()
        for stop, expected in ((signal.SIGTERM, -signal.SIGTERM), (signal.SIGHUP, -signal.SIGHUP)):
            process = self.start()
            hello = self.read_line(process)
            self.assertEqual((hello['type'], hello['route'], hello['git_commit']), ('hello', None, None))
            self.assertEqual(hello['pid'], process.pid)
            status = self.read_line(process)
            self.assertEqual(status, {'type': 'status', 'onroad': False, 'route': None, 'segment': None})
            process.send_signal(stop)
            self.assertEqual(process.wait(timeout=5), expected)
        # sys.dont_write_bytecode: no __pycache__ next to the modules it imported.
        self.assertFalse((self.cwd / 'openpilot' / 'cereal' / '__pycache__').exists())

    def test_closed_stdout_ends_it(self):
        self.fake_openpilot()
        process = self.start()
        self.read_line(process)
        process.stdout.close()  # the ssh session went away
        started = time.monotonic()
        self.assertEqual(process.wait(timeout=10), 0)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(process.stderr.read(), b'')


if __name__ == '__main__':
    unittest.main()
