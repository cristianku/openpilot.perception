"""The comma side of the openpilot.perception app: the car's radar reading, live, while the radar screen is open.

The app sends this file over ssh as the standard input of

    cd /data/openpilot && exec nice -n 19 /usr/local/venv/bin/python -B -u -

so nothing is ever written to the comma's disk: the script is read from the standard input (the app sends it
and then closes its side, Python starts it at the end of the input), and -B, like the first line below (which
also covers a command without it), keeps Python from writing __pycache__ files for the openpilot modules it
imports. It only reads:
  - carState, through one openpilot SubMaster subscription (published only onroad, with the car switched on).
    Like any msgq subscriber it takes a reader slot in the shared memory of carState; openpilot frees the slots
    when card starts publishing again at the next ignition, and with every slot taken msgq makes all readers
    reconnect: start one listener per radar screen, never in a fast retry loop;
  - /data/params/d/IsOffroad, the onroad state that openpilot's manager writes from deviceState.started for
    the processes that do not subscribe to deviceState (a msgq reader slot of deviceState would stay taken
    until the comma reboots), and /data/params/d/GitCommit, GitBranch for the hello line;
  - the names of the entries of /data/media/0/realdata (the newest recording by route counter, never by
    date: the comma's clock can be months off).
No Params writes, no files, no subprocesses, no network, no PubMaster.

Standard output, one JSON object per line, flushed:
  {"type": "hello", "version": 1, "git_commit", "git_branch", "route", "segment", "python", "pid",
   "radar_fields", "max_seconds"}                                   first, once
  {"type": "radar", "mono_ns", "onroad": true, "v_ego", "valid", "distance_m", "rel_speed_mps",
   "rel_speed_valid", "measured_ns"}                                10 Hz while carState arrives
  {"type": "status", "onroad", "route", "segment"}                  right after hello, every second without
                                                                    carState, every 10 s anyway, and at once
                                                                    when carState starts or stops or the
                                                                    newest recording changes
  {"type": "bye", "reason": "time_limit"}                           after 20 minutes, then exit 0
  {"type": "error", "message"}                                      on an unexpected error, then exit 1
route is "<counter 8 hex>--<10 hex>" and segment "<route>--<n>" (its highest segment), or both null.
radar_fields is false when the carState schema of this openpilot has no stockRadar* fields: the radar lines
then carry valid false and nulls. Values that are not finite numbers are null.

It exits by itself after 20 minutes (a SIGALRM 30 s later ends it even if a write is stuck), when standard
output is closed (the ssh session ended), on SIGHUP and on SIGTERM. Between messages it sleeps in
SubMaster.update; onroad it sleeps to the next 0.1 s tick, and the conflated socket keeps only the newest of
the 100 Hz carState messages, so it wakes about 10 times a second.
"""
import sys

sys.dont_write_bytecode = True  # before any import from /data/openpilot: no __pycache__ written on the comma

import json  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import signal  # noqa: E402
import time  # noqa: E402

VERSION = 1
OPENPILOT = '/data/openpilot'
REALDATA = '/data/media/0/realdata'
PARAMS = '/data/params/d'
MAX_SECONDS = 20 * 60
HARD_STOP_MARGIN_S = 30
RADAR_PERIOD_S = 0.1
STATUS_OFFROAD_S = 1.0
STATUS_ANYWAY_S = 10.0
CAR_STATE_STALE_S = 1.0
ROUTE_REFRESH_S = 10.0
# Right after carState starts or stops the recording changes: loggerd opens the new route a moment later.
ROUTE_REFRESH_FAST_S = 2.0
ROUTE_FAST_WINDOW_S = 30.0
MAX_UPDATE_MS = 1000
MIN_UPDATE_MS = 10
SEGMENT_RE = re.compile(r'^([0-9a-f]{8})--([0-9a-f]{10})--([0-9]{1,4})$')
RADAR_FIELDS = ('stockRadarLeadDistance', 'stockRadarLeadRelativeSpeed', 'stockRadarLeadValid',
                'stockRadarMeasurementTimeNanos', 'stockRadarRelativeSpeedValid')
PARAM_BYTES = 256


def list_realdata(path=REALDATA):
    """The names in the recordings folder; none when it cannot be read."""
    try:
        return os.listdir(path)
    except OSError:
        return []


def read_param(name, folder=PARAMS):
    """A Params value read as a file (never through Params, never written); None when absent."""
    try:
        with open(os.path.join(folder, name), 'rb') as file:
            return file.read(PARAM_BYTES).decode('utf-8', 'replace').strip()
    except OSError:
        return None


def newest_recording(names):
    """(route, segment) of the newest recording by route counter, its highest segment; (None, None)."""
    best = None
    for name in names:
        match = SEGMENT_RE.match(name)
        if match is None:
            continue
        key = (int(match.group(1), 16), match.group(2), int(match.group(3)))
        if best is None or key > best:
            best = key
    if best is None:
        return None, None
    route = f'{best[0]:08x}--{best[1]}'
    return route, f'{route}--{best[2]}'


def radar_fields_available(car_state):
    """Whether this openpilot's carState schema has the stockRadar* fields."""
    try:
        names = set(car_state.schema.fieldnames)
    except Exception:
        names = {name for name in RADAR_FIELDS if hasattr(car_state, name)}
    return all(name in names for name in RADAR_FIELDS)


def number(value, digits=3):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, digits) if math.isfinite(value) else None


def integer(value):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def radar_message(car_state, mono_ns, available):
    message = {'type': 'radar', 'mono_ns': integer(mono_ns), 'onroad': True, 'v_ego': number(car_state.vEgo),
               'valid': False, 'distance_m': None, 'rel_speed_mps': None, 'rel_speed_valid': False,
               'measured_ns': None}
    if available:
        message.update(valid=bool(car_state.stockRadarLeadValid),
                       distance_m=number(car_state.stockRadarLeadDistance),
                       rel_speed_mps=number(car_state.stockRadarLeadRelativeSpeed),
                       rel_speed_valid=bool(car_state.stockRadarRelativeSpeedValid),
                       measured_ns=integer(car_state.stockRadarMeasurementTimeNanos))
    return message


class StdoutClosed(Exception):
    pass


class Listener:
    def __init__(self, messaging, out, clock=time.monotonic, sleep=time.sleep, list_names=list_realdata,
                 param=read_param, max_seconds=MAX_SECONDS):
        self.messaging = messaging
        self.out = out
        self.clock = clock
        self.sleep = sleep
        self.list_names = list_names
        self.param = param
        self.max_seconds = max_seconds
        self.route = self.segment = None

    def emit(self, message):
        line = json.dumps(message, separators=(',', ':')) + '\n'
        try:
            self.out.write(line)
            self.out.flush()
        except (OSError, ValueError) as error:  # BrokenPipeError; ValueError: the stream is already closed
            raise StdoutClosed() from error

    def refresh_route(self):
        """True when the newest recording changed."""
        recording = newest_recording(self.list_names())
        changed = recording != (self.route, self.segment)
        self.route, self.segment = recording
        return changed

    def offroad_param_says_onroad(self):
        return self.param('IsOffroad') == '0'

    def status(self, onroad):
        return {'type': 'status', 'onroad': onroad, 'route': self.route, 'segment': self.segment}

    def run(self):
        """0 at the time limit or when standard output is closed, 1 after an unexpected error."""
        try:
            return self._run()
        except StdoutClosed:
            return 0
        except Exception as error:
            try:
                self.emit({'type': 'error', 'message': f'{type(error).__name__}: {error}'})
            except StdoutClosed:
                pass
            return 1

    def _run(self):
        start = self.clock()
        deadline = start + self.max_seconds
        sm = self.messaging.SubMaster(['carState'])
        available = radar_fields_available(sm['carState'])
        self.refresh_route()
        self.emit({'type': 'hello', 'version': VERSION, 'git_commit': self.param('GitCommit'),
                   'git_branch': self.param('GitBranch'), 'route': self.route, 'segment': self.segment,
                   'python': sys.version.split()[0], 'pid': os.getpid(), 'radar_fields': available,
                   'max_seconds': self.max_seconds})
        last_car_state = None   # clock time of the newest carState
        flowing = False         # carState arrived less than CAR_STATE_STALE_S ago
        flow_changed_at = None  # clock time carState last started or stopped
        next_radar = start
        self.emit(self.status(self.offroad_param_says_onroad()))
        last_status = start
        next_route = start + ROUTE_REFRESH_S
        while True:
            now = self.clock()
            if now >= deadline:
                self.emit({'type': 'bye', 'reason': 'time_limit'})
                return 0
            if flowing and next_radar > now:
                # carState comes at 100 Hz: sleep to the next tick, the conflated socket keeps the newest.
                self.sleep(min(next_radar, deadline) - now)
                now = self.clock()
            status_due = last_status + (STATUS_ANYWAY_S if flowing else STATUS_OFFROAD_S)
            wait_s = min(status_due, next_route, deadline) - now
            sm.update(int(max(MIN_UPDATE_MS, min(MAX_UPDATE_MS, wait_s * 1000))))
            now = self.clock()

            if sm.updated['carState']:
                last_car_state = now
                if now >= next_radar - 0.005:
                    self.emit(radar_message(sm['carState'], sm.logMonoTime['carState'], available))
                    next_radar += RADAR_PERIOD_S
                    if next_radar <= now:
                        next_radar = now + RADAR_PERIOD_S
            was_flowing = flowing
            flowing = last_car_state is not None and now - last_car_state < CAR_STATE_STALE_S
            changed = flowing != was_flowing
            if changed:
                flow_changed_at = now
                next_route = now
            if now >= next_route:
                changed = self.refresh_route() or changed
                fast = flow_changed_at is not None and now - flow_changed_at < ROUTE_FAST_WINDOW_S
                next_route = now + (ROUTE_REFRESH_FAST_S if fast else ROUTE_REFRESH_S)
            period = STATUS_ANYWAY_S if flowing else STATUS_OFFROAD_S
            if changed or now - last_status >= period - 0.005:
                self.emit(self.status(True if flowing else self.offroad_param_says_onroad()))
                last_status = now


def main():
    # Default actions: SIGHUP and SIGTERM end the process at once (nothing to clean up: nothing was written);
    # SIGALRM ends it even if a write to a stalled ssh session blocks the loop past the time limit.
    for name in ('SIGHUP', 'SIGTERM', 'SIGALRM'):
        signal.signal(getattr(signal, name), signal.SIG_DFL)
    signal.alarm(MAX_SECONDS + HARD_STOP_MARGIN_S)
    try:
        if OPENPILOT not in sys.path:
            sys.path.insert(0, OPENPILOT)
        from openpilot.cereal import messaging
        signal.alarm(MAX_SECONDS + HARD_STOP_MARGIN_S)  # again: the time limit counts from here
        code = Listener(messaging, sys.stdout).run()
    except KeyboardInterrupt:
        code = 0
    except Exception as error:  # openpilot could not be imported
        code = 1
        try:
            Listener(None, sys.stdout).emit({'type': 'error',
                                             'message': f'openpilot messaging: {type(error).__name__}: {error}'})
        except StdoutClosed:
            pass
    # Every line was flushed; skip the interpreter's teardown (a flush of a closed pipe would print to stderr).
    os._exit(code)


if __name__ == '__main__':
    main()
