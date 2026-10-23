"""The radar's calibration on the site: its status, where it comes from, its versions and their history, the tests
it follows, the drafts waiting for a recording, the next step on Home, the order of the drives and why a drive
has no readings. Synthetic series and tests only; nothing talks to a comma or starts a job."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from test_web import TOKEN, FakeJobs, ServerTestCase
from test_web_radar import write_series

from perception.radar.index import cached, stamp_of
from web.common import WebError
from web.radar import (MAX_DRAFTS, RadarDrafts, RadarIndex, RadarTests, calibration_view, drive_detail, next_step,
                       radar_overview, segment_folders)
from web.server import App
from web.settings import Settings

WALL = '00000134--a5c8197883--0'      # steady 2.25 m in front of a wall
WALL2 = '00000132--d64642fa1f--0'     # steady 1.75 m
CAR = '00000135--0123456789--0'       # steady 11.00 m behind a parked car
STILL = {'00000134--a5c8197883--0': 2.25, '00000132--d64642fa1f--0': 1.75, '00000135--0123456789--0': 11.0,
         '00000136--0123456789--0': 2.0}
CAL_KEYS = {'radar_to_camera_m', 'radar_correction_m', 'radar_to_reference_m', 'source', 'version', 'status',
            'far_test_done', 'tests_disagree', 'uncertainty_m', 'spread_m', 'tested_m', 'label_range_m',
            'tests_give'}
TEST_KEYS = {'id', 'segment', 'route', 'recording_number', 'target', 'from_s', 'to_s', 'reading_m', 'tape_radar_m',
             'tape_comma_m', 'correction_m', 'kept', 'created'}


def series_of(name):
    if name not in STILL:
        return None
    return {'standstills': [{'from_s': 2.5, 'to_s': 20.0, 'reading_m': STILL[name]}]}


def values(to_camera=1.5, correction=-0.89, source='tests', version=1):
    total = None if to_camera is None else round(to_camera + correction, 2)
    return {'radar_to_camera_m': to_camera, 'radar_correction_m': correction, 'radar_to_reference_m': total,
            'source': source, 'version': version}


def add(tests: RadarTests, segment, radar, comma, target='wall'):
    return tests.add({'segment': segment, 'standstill': 0, 'tape_radar_m': radar, 'tape_comma_m': comma,
                      'target': target}, series_of)


def write_recording(repo: Path, name: str, reading: float, **fields) -> None:
    folder = repo / 'outputs' / 'recordings' / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'rlog.zst').write_bytes(b'log')
    write_series(repo, name, readings=[reading] * 200,
                 standstills=[{'from_s': 2.5, 'to_s': 20.0, 'reading_m': reading, 'target_share': 0.7,
                               'steady_share': 1.0}], **fields)


class Status(unittest.TestCase):
    """docs/design-radar-objects.md 4.2: not_set, typed, ready, close_only, and the flags that go with them."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.tests = RadarTests(Path(self.directory.name))

    def view(self, **kwargs):
        return calibration_view(values(**kwargs), self.tests.summary())

    def test_not_set_without_the_distance(self):
        add(self.tests, CAR, 10.2, 11.7, 'car')
        calibration = self.view(to_camera=None, correction=0.0, source=None, version=0)
        self.assertEqual(set(calibration), CAL_KEYS)
        self.assertEqual((calibration['status'], calibration['far_test_done']), ('not_set', False))

    def test_no_radar_is_not_the_same_as_not_set(self):
        calibration = self.view(to_camera=None, correction=0.0, source='none', version=2)
        self.assertEqual((calibration['status'], calibration['far_test_done'], calibration['source']),
                         ('no_radar', False, 'none'))

    def test_one_test_is_good_to_13_cm(self):
        # Half a 0.25 m step, rounded half up as the page rounds it (round() would give 0.12).
        add(self.tests, WALL, 1.32, 2.82)
        self.assertEqual(self.tests.summary()['uncertainty_m'], 0.13)

    def test_typed_whatever_the_tests(self):
        add(self.tests, CAR, 10.2, 11.7, 'car')
        calibration = self.view(source='typed')
        self.assertEqual(calibration['status'], 'typed')
        self.assertIsNone(calibration['uncertainty_m'])   # not the tests' uncertainty: these numbers were typed
        self.assertEqual(calibration['tests_give'], {'radar_to_camera_m': 1.5, 'radar_correction_m': -0.8})

    def test_close_only_then_ready_with_a_far_test(self):
        add(self.tests, WALL, 1.32, 2.82)
        add(self.tests, WALL2, 0.90, 2.40)
        close = self.view()
        self.assertEqual((close['status'], close['tests_disagree'], close['tested_m']), ('close_only', False,
                                                                                         [0.9, 1.32]))
        self.assertEqual((close['uncertainty_m'], close['spread_m'], close['label_range_m']), (0.09, 0.08,
                                                                                              [5.0, 30.0]))
        far = add(self.tests, CAR, 10.2, 11.7, 'car')
        ready = self.view()
        self.assertEqual((ready['status'], ready['far_test_done'], ready['tested_m']), ('ready', True,
                                                                                        [0.9, 1.32, 10.2]))
        # A far test left out does not count.
        self.tests.change(far['id'], {'kept': False})
        self.assertEqual(self.view()['status'], 'close_only')

    def test_tests_that_disagree_are_never_ready(self):
        add(self.tests, CAR, 10.2, 11.7, 'car')                     # correction -0.80
        add(self.tests, '00000136--0123456789--0', 1.5, 3.0)        # correction -0.50: 0.30 apart
        self.assertEqual(self.view()['status'], 'ready')
        add(self.tests, WALL, 1.32, 2.82)                           # correction -0.93: 0.43 apart
        add(self.tests, WALL2, 0.90, 2.40)                          # -0.85
        self.assertEqual(self.view()['status'], 'ready')
        tests = RadarTests(Path(self.directory.name) / 'other')
        add(tests, CAR, 10.2, 11.7, 'car')                          # -0.80
        add(tests, '00000136--0123456789--0', 2.2, 3.7)             # +0.20: 1.00 apart
        calibration = calibration_view(values(), tests.summary())
        self.assertEqual((calibration['status'], calibration['tests_disagree'], calibration['spread_m']),
                         ('close_only', True, 1.0))

    def test_test_fields(self):
        test = add(self.tests, WALL, 1.32, 2.82)
        self.assertEqual(set(test), TEST_KEYS)
        self.assertEqual((test['recording_number'], test['target'], test['correction_m'], test['kept']),
                         (0x134, 'wall', -0.93, True))
        # Saved before the target existed: a wall.
        stored = json.loads(self.tests.path.read_text())
        del stored[0]['target']
        self.tests.path.write_text(json.dumps(stored))
        self.assertEqual(self.tests.summary()['tests'][0]['target'], 'wall')
        # An entry edited by hand without its numbers is skipped, and kept through later writes.
        self.tests.path.write_text(json.dumps([*stored, {'id': 'broken', 'note': 'by hand'}]))
        self.tests.change(stored[0]['id'], {'kept': False})
        self.assertEqual(json.loads(self.tests.path.read_text())[-1], {'id': 'broken', 'note': 'by hand'})
        self.assertEqual(len(self.tests.summary()['tests']), 1)

    def test_refused_tests(self):
        for body in ({'segment': WALL, 'standstill': 0, 'tape_radar_m': 1.32, 'tape_comma_m': 2.82, 'target': 'tree'},
                     {'segment': WALL, 'standstill': 0, 'tape_radar_m': 1.32, 'tape_comma_m': 7.0},
                     # 2.25 m read in front of a wall taped at 3.50 m: more than 1 m apart.
                     {'segment': WALL, 'standstill': 0, 'tape_radar_m': 3.5, 'tape_comma_m': 5.0},
                     {'segment': WALL, 'standstill': 0, 'tape_radar_m': 1.32, 'tape_comma_m': 2.82, 'extra': 1}):
            with self.assertRaises(WebError):
                self.tests.add(body, series_of)
        self.assertEqual(self.tests.summary()['tests'], [])


class History(unittest.TestCase):
    """The source, the version in settings.json and the append-only history next to it."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name)
        self.settings = Settings(self.repo)

    def saved(self):
        return json.loads(self.settings.path.read_text())

    def test_values_saved_before_the_source_were_typed(self):
        self.settings.path.parent.mkdir(parents=True)
        self.settings.path.write_text(json.dumps({'radar_to_camera_m': 1.5, 'radar_correction_m': -0.89}))
        state = self.settings.radar_state()
        self.assertEqual((state['source'], state['version'], state['radar_to_reference_m']), ('typed', 0, 0.61))
        self.assertEqual(Settings(Path(self.directory.name) / 'empty').radar_state()['source'], None)

    def test_every_change_is_a_version(self):
        self.settings.set_radar({'radar_to_camera_m': 1.5, 'radar_correction_m': -0.89})
        self.assertEqual((self.saved()['radar_source'], self.saved()['radar_calibration_version']), ('typed', 1))
        # The same numbers again change nothing.
        self.settings.set_radar({'radar_to_camera_m': 1.5})
        self.assertEqual(self.settings.radar_state()['version'], 1)
        self.assertTrue(self.settings.set_radar_from_tests(1.5, -0.89, ['aaaaaaaaaaaa']))
        self.assertFalse(self.settings.set_radar_from_tests(1.5, -0.89, ['aaaaaaaaaaaa']))
        # Same means from another set of tests: a new version, it records which tests it came from.
        self.assertTrue(self.settings.set_radar_from_tests(1.5, -0.89, ['aaaaaaaaaaaa', 'bbbbbbbbbbbb']))
        # Typing over numbers from the tests makes them typed; one value alone keeps the other.
        self.settings.set_radar({'radar_correction_m': -0.9})
        # "My car has no radar": both cleared on purpose, remembered as such.
        self.settings.set_radar({'radar_to_camera_m': None, 'radar_correction_m': None})
        state = self.settings.radar_state()
        self.assertEqual((state['source'], state['version'], state['radar_to_camera_m']), ('none', 5, None))
        self.assertEqual(self.saved()['radar_source'], 'none')
        # The tests clearing it (no test kept any more): not set, no source.
        self.assertTrue(self.settings.set_radar_from_tests(1.5, -0.89, ['aaaaaaaaaaaa']))
        self.assertTrue(self.settings.set_radar_from_tests(None, None, []))
        self.assertEqual(self.settings.radar_state()['source'], None)
        self.assertNotIn('radar_source', self.saved())
        history = self.settings.radar_history()
        self.assertEqual([(e['version'], e['source'], e['test_ids']) for e in history],
                         [(1, 'typed', []), (2, 'tests', ['aaaaaaaaaaaa']),
                          (3, 'tests', ['aaaaaaaaaaaa', 'bbbbbbbbbbbb']), (4, 'typed', []), (5, 'none', []),
                          (6, 'tests', ['aaaaaaaaaaaa']), (7, None, [])])
        self.assertEqual(set(history[0]), {'version', 'at', 'radar_to_camera_m', 'radar_correction_m', 'source',
                                           'test_ids'})
        self.assertEqual((history[0]['radar_to_camera_m'], history[0]['radar_correction_m']), (1.5, -0.89))
        self.assertEqual((history[3]['radar_to_camera_m'], history[3]['radar_correction_m']), (1.5, -0.9))
        self.assertEqual((history[4]['radar_to_camera_m'], history[4]['radar_correction_m']), (None, 0.0))
        self.assertEqual(self.settings.history_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.settings.path.stat().st_mode & 0o777, 0o600)

    def test_an_unreadable_history_is_never_written_over(self):
        self.settings.history_path.parent.mkdir(parents=True)
        self.settings.history_path.write_text('{"not": "a list"')
        with self.assertRaises(WebError):
            self.settings.set_radar({'radar_to_camera_m': 1.5})
        self.assertEqual(self.settings.history_path.read_text(), '{"not": "a list"')
        self.assertIsNone(self.settings.radar_to_camera_m())

    def test_a_version_never_repeats(self):
        # A history entry whose settings.json write was lost: the next version comes after it.
        self.settings.history_path.parent.mkdir(parents=True)
        self.settings.history_path.write_text(json.dumps([{'version': 3, 'source': 'typed', 'test_ids': []}]))
        self.settings.set_radar({'radar_to_camera_m': 1.4})
        self.assertEqual(self.settings.radar_state()['version'], 4)


class FollowTheTests(unittest.TestCase):
    """App: a test added, left out, used again or deleted re-applies the means when the source is the tests (or
    there is none), never over numbers typed by hand."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name)
        for name, reading in STILL.items():
            write_recording(self.repo, name, reading)
        self.app = App(self.repo, setup_code=TOKEN, jobs=FakeJobs(), gpu_check=lambda: '')

    def add(self, segment, radar, comma, **extra):
        return self.app.add_radar_test({'segment': segment, 'standstill': 0, 'tape_radar_m': radar,
                                        'tape_comma_m': comma, **extra})

    def test_from_nothing_the_tests_apply_at_once(self):
        answer = self.add(WALL, 1.32, 2.82)
        calibration = answer['calibration']
        self.assertEqual((calibration['source'], calibration['status'], calibration['version']),
                         ('tests', 'close_only', 1))
        self.assertEqual((calibration['radar_to_camera_m'], calibration['radar_correction_m']), (1.5, -0.93))
        second = self.add(WALL2, 0.90, 2.40)['test']
        self.assertEqual(self.app.calibration()['radar_correction_m'], -0.89)
        # Left out: the first test alone again; used again: both.
        left = self.app.change_radar_test(second['id'], {'kept': False})
        self.assertEqual((left['calibration']['radar_correction_m'], left['calibration']['version']), (-0.93, 3))
        self.assertEqual(self.app.change_radar_test(second['id'], {'kept': True})['calibration']['radar_correction_m'],
                         -0.89)
        self.assertEqual(self.app.change_radar_test(second['id'], {'delete': True})['calibration']
                         ['radar_correction_m'], -0.93)
        history = self.app.settings.radar_history()
        self.assertEqual(history[-1]['test_ids'], [answer['test']['id']])
        # The last kept test left out: nothing to calibrate with any more.
        cleared = self.app.change_radar_test(answer['test']['id'], {'kept': False})['calibration']
        self.assertEqual((cleared['status'], cleared['source'], cleared['radar_to_camera_m']), ('not_set', None, None))
        # And back.
        self.assertEqual(self.app.change_radar_test(answer['test']['id'], {'kept': True})['calibration']['source'],
                         'tests')

    def test_typed_numbers_stay(self):
        self.app.set_radar({'radar_to_camera_m': 1.45, 'radar_correction_m': -0.5})
        answer = self.add(WALL, 1.32, 2.82)
        calibration = answer['calibration']
        self.assertEqual((calibration['source'], calibration['status'], calibration['radar_correction_m'],
                          calibration['version']), ('typed', 'typed', -0.5, 1))
        self.assertEqual(calibration['tests_give'], {'radar_to_camera_m': 1.5, 'radar_correction_m': -0.93})
        self.app.change_radar_test(answer['test']['id'], {'kept': False})
        self.assertEqual(self.app.calibration()['radar_correction_m'], -0.5)
        self.app.change_radar_test(answer['test']['id'], {'kept': True})
        # "Use the tests".
        applied = self.app.apply_radar_tests({})['calibration']
        self.assertEqual((applied['source'], applied['radar_correction_m'], applied['version']), ('tests', -0.93, 2))
        # Typed again over them.
        self.assertEqual(self.app.set_radar({'radar_correction_m': -0.9})['calibration']['source'], 'typed')

    def test_no_radar_until_a_test_is_kept(self):
        self.app.set_radar({'radar_to_camera_m': None, 'radar_correction_m': None})
        self.assertEqual(self.app.calibration()['status'], 'no_radar')
        # A test kept after all: the car has a radar, the tests apply.
        test = self.add(WALL, 1.32, 2.82)['test']
        calibration = self.app.calibration()
        self.assertEqual((calibration['source'], calibration['status'], calibration['radar_correction_m']),
                         ('tests', 'close_only', -0.93))
        self.app.change_radar_test(test['id'], {'kept': False})
        self.assertEqual(self.app.calibration()['status'], 'not_set')

    def test_a_draft_finished_by_its_test(self):
        draft = self.app.add_radar_draft({'target': 'wall', 'tape_radar_m': 1.32, 'tape_comma_m': 2.82,
                                          'marker_counter': 0x133})['draft']
        self.add(WALL, 1.32, 2.82, draft_id=draft['id'])
        self.assertEqual(self.app.radar_drafts.drafts(), [])
        # Twice for the same draft: the second is refused and saves nothing.
        with self.assertRaises(WebError) as refused:
            self.add(WALL, 1.32, 2.82, draft_id=draft['id'])
        self.assertEqual(refused.exception.reason, 'unknown_draft')
        self.assertEqual(len(self.app.radar_tests.summary()['tests']), 1)
        # A draft is kept when its test is refused.
        other = self.app.add_radar_draft({'target': 'car', 'tape_radar_m': 10.2, 'tape_comma_m': 11.7})['draft']
        with self.assertRaises(WebError):
            self.add(WALL, 4.0, 5.5, draft_id=other['id'])
        self.assertEqual([d['id'] for d in self.app.radar_drafts.drafts()], [other['id']])
        for bad in ('nothere', 42):
            with self.assertRaises(WebError):
                self.add(WALL, 1.32, 2.82, draft_id=bad)


class DamagedFiles(unittest.TestCase):
    """Files edited by hand or cut short: a bad entry is skipped (Home and Radar keep working), a file that cannot be
    read is never written over."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name)
        write_recording(self.repo, WALL, 2.25)
        self.app = App(self.repo, setup_code=TOKEN, jobs=FakeJobs(), gpu_check=lambda: '')

    def test_bad_entries_are_skipped(self):
        good = self.app.add_radar_test({'segment': WALL, 'standstill': 0, 'tape_radar_m': 1.32,
                                        'tape_comma_m': 2.82})['test']
        path = self.app.radar_tests.path
        stored = json.loads(path.read_text())
        bad_route = dict(stored[0], id='bbbbbbbbbbbb', route='zz')
        # json.loads reads NaN: as a number it is skipped, as a date it is left out.
        text = json.dumps([dict(stored[0], created=float('nan')), bad_route,
                           dict(stored[0], id='cccccccccccc', reading_m=float('nan'))])
        self.assertIn('NaN', text)
        path.write_text(text)
        drafts = self.app.radar_drafts.path
        drafts.write_text('[{"id": "dddddddddddd", "target": "wall", "tape_radar_m": NaN, "tape_comma_m": 2.8},'
                          ' {"id": "eeeeeeeeeeee", "target": "wall", "tape_radar_m": 1.3, "tape_comma_m": 2.8,'
                          ' "created": Infinity}]')
        status = self.app.status_payload()
        json.dumps(status, allow_nan=False)
        radar = self.app.radar_payload()
        json.dumps(radar, allow_nan=False)
        self.assertEqual([test['id'] for test in radar['tests']['tests']], [good['id']])
        self.assertIsNone(radar['tests']['tests'][0]['created'])
        self.assertEqual([(d['id'], d['created']) for d in radar['drafts']], [('eeeeeeeeeeee', None)])
        self.assertEqual(status['next']['case'], 'test_waiting')

    def test_unreadable_files_are_never_written_over(self):
        tests = self.app.radar_tests.path
        tests.parent.mkdir(parents=True, exist_ok=True)
        cut = '[{"id": "bbbbbbbbbbbb", "segment": "' + WALL + '", "reading_m": 2.25'
        tests.write_text(cut)
        with self.assertRaises(WebError) as refused:
            self.app.add_radar_test({'segment': WALL, 'standstill': 0, 'tape_radar_m': 1.32, 'tape_comma_m': 2.82})
        self.assertEqual((refused.exception.status, refused.exception.reason), (500, 'unreadable'))
        with self.assertRaises(WebError):
            self.app.change_radar_test('bbbbbbbbbbbb', {'kept': False})
        self.assertEqual(tests.read_text(), cut)
        # Shown as no tests meanwhile, and the calibration is left alone.
        self.assertEqual(self.app.radar_payload()['tests']['tests'], [])
        self.assertEqual(self.app.calibration()['version'], 0)
        drafts = self.app.radar_drafts.path
        drafts.write_text('{"not": "a list"}')
        for call in (lambda: self.app.add_radar_draft({'target': 'wall', 'tape_radar_m': 1.3, 'tape_comma_m': 2.8}),
                     lambda: self.app.change_radar_draft('aaaaaaaaaaaa', {'delete': True})):
            with self.assertRaises(WebError) as refused:
                call()
            self.assertEqual(refused.exception.reason, 'unreadable')
        self.assertEqual(drafts.read_text(), '{"not": "a list"}')


class Drafts(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.drafts = RadarDrafts(Path(self.directory.name))

    def test_at_most_five_newest_first(self):
        made = [self.drafts.add({'target': 'wall', 'tape_radar_m': 1.0 + i / 10, 'tape_comma_m': 2.5,
                                 'marker_counter': 0x130 + i})
                for i in range(MAX_DRAFTS)]
        self.assertEqual(set(made[0]), {'id', 'created', 'target', 'tape_radar_m', 'tape_comma_m', 'marker_counter'})
        with self.assertRaises(WebError) as refused:
            self.drafts.add({'target': 'car', 'tape_radar_m': 10.0, 'tape_comma_m': 11.5, 'marker_counter': None})
        self.assertEqual((refused.exception.status, refused.exception.reason), (409, 'too_many_drafts'))
        self.assertEqual([d['id'] for d in self.drafts.drafts()], [d['id'] for d in reversed(made)])
        self.drafts.change(made[0]['id'], {'delete': True})
        self.assertEqual(len(self.drafts.drafts()), MAX_DRAFTS - 1)
        with self.assertRaises(WebError) as gone:
            self.drafts.change(made[0]['id'], {'delete': True})
        self.assertEqual(gone.exception.status, 404)
        self.assertEqual(self.drafts.path.stat().st_mode & 0o777, 0o600)
        # One edited by hand is skipped (not counted), and kept.
        stored = json.loads(self.drafts.path.read_text())
        self.drafts.path.write_text(json.dumps([{'id': 'broken'}, *stored]))
        self.drafts.change(made[1]['id'], {'delete': True})
        self.assertEqual(json.loads(self.drafts.path.read_text())[0], {'id': 'broken'})
        # marker_counter may be left out: the comma was not reachable.
        self.assertIsNone(self.drafts.add({'target': 'car', 'tape_radar_m': 10.0, 'tape_comma_m': 11.5})
                          ['marker_counter'])

    def test_refused(self):
        for body in ({'target': 'tree', 'tape_radar_m': 1.3, 'tape_comma_m': 2.8},
                     {'tape_radar_m': 1.3, 'tape_comma_m': 2.8},
                     {'target': 'wall', 'tape_radar_m': 2.8, 'tape_comma_m': 1.3},
                     {'target': 'wall', 'tape_radar_m': 0.1, 'tape_comma_m': 1.3},
                     {'target': 'wall', 'tape_radar_m': 1.3, 'tape_comma_m': 2.8, 'marker_counter': -1},
                     {'target': 'wall', 'tape_radar_m': 1.3, 'tape_comma_m': 2.8, 'marker_counter': True},
                     {'target': 'wall', 'tape_radar_m': 1.3, 'tape_comma_m': 2.8, 'segment': WALL}, []):
            with self.assertRaises(WebError):
                self.drafts.add(body)
        for draft_id, body in (('../x', {'delete': True}), ('aaaaaaaaaaaa', {'delete': False}),
                               ('aaaaaaaaaaaa', {})):
            with self.assertRaises(WebError):
                self.drafts.change(draft_id, body)
        self.assertEqual(self.drafts.drafts(), [])


def summary_of(*entries):
    """A drive summary of (kind, route, has_radar), newest first as RadarIndex sorts it."""
    drives = [{'kind': kind, 'route': route, 'has_radar': radar} for kind, route, radar in entries]
    return {'drives': drives, 'segments': len(drives), 'read': len(drives), 'with_radar': 0}


class NextStep(unittest.TestCase):
    CLOSE = {'status': 'close_only', 'tested_m': [0.9, 1.32]}
    READY = {'status': 'ready', 'tested_m': [0.9, 10.2]}
    DRAFT = [{'id': 'aaaaaaaaaaaa', 'created': 1_792_780_000.0}]
    RUNNING = [{'action': 'radar_index', 'state': 'running'}]
    DRIVES = summary_of(('drive', '00000135aaaaaaaaaa', True), ('drive', '00000130aaaaaaaaaa', True),
                        ('recording', '00000134--a5c8197883', True))

    def test_priority_and_also(self):
        nothing = summary_of(('drive', '0000012e7dcc3fee25', False))
        step = next_step({'status': 'not_set', 'tested_m': []}, self.DRAFT, nothing, self.RUNNING)
        self.assertEqual((step['case'], step['also']), ('radar_not_set', ['test_waiting', 'radar_reading']))
        self.assertEqual((step['draft_created'], step['drives_with_radar']), (1_792_780_000.0, 0))
        self.assertNotIn('newest_radar_drive', step)
        step = next_step(self.CLOSE, self.DRAFT, nothing, self.RUNNING)
        self.assertEqual((step['case'], step['also']), ('test_waiting', ['radar_reading', 'no_radar_drive']))
        step = next_step(self.CLOSE, [], nothing, self.RUNNING)
        self.assertEqual((step['case'], step['also']), ('radar_reading', ['no_radar_drive', 'far_test']))
        step = next_step(self.CLOSE, [], nothing, [{'action': 'fetch_recordings', 'state': 'running'}])
        self.assertEqual(step['case'], 'radar_reading')
        step = next_step(self.CLOSE, [], nothing, [{'action': 'radar_index', 'state': 'ok'}])
        self.assertEqual((step['case'], step['also']), ('no_radar_drive', ['far_test']))
        self.assertNotIn('draft_created', step)

    def test_far_test_then_caught_up(self):
        step = next_step(self.CLOSE, [], self.DRIVES, [])
        self.assertEqual((step['case'], step['also'], step['drives_with_radar'], step['newest_radar_drive']),
                         ('far_test', [], 2, '00000135aaaaaaaaaa'))
        self.assertEqual(next_step({'status': 'typed', 'tested_m': []}, [], self.DRIVES, [])['case'], 'far_test')
        # Typed numbers with a far test among the kept ones: nothing left to test, but only numbers from the tests
        # make confirmed pairs count as labels.
        give = {'radar_to_camera_m': 1.5, 'radar_correction_m': -0.8}
        typed_far = {'status': 'typed', 'tested_m': [10.2], 'tests_give': give}
        self.assertEqual(next_step(typed_far, [], self.DRIVES, [])['case'], 'use_tests')
        typed_close = {'status': 'typed', 'tested_m': [1.32], 'tests_give': give}
        step = next_step(typed_close, [], self.DRIVES, [])
        self.assertEqual((step['case'], step['also']), ('far_test', ['use_tests']))
        step = next_step(self.READY, [], self.DRIVES, [])
        self.assertEqual((step['case'], step['also']), ('caught_up', []))

    def test_close_only_is_never_caught_up(self):
        # Tests from 1.30 m to 9.10 m whose corrections are 1.05 m apart: a far test, and still close only.
        disagree = {'status': 'close_only', 'tested_m': [1.3, 1.32, 9.1], 'tests_disagree': True, 'spread_m': 1.05}
        self.assertEqual(next_step(disagree, [], self.DRIVES, [])['case'], 'tests_disagree')
        step = next_step(disagree, [], summary_of(), [])
        self.assertEqual((step['case'], step['also']), ('no_radar_drive', ['tests_disagree']))
        close_and_disagree = dict(disagree, tested_m=[0.9, 1.32])
        self.assertEqual(next_step(close_and_disagree, [], self.DRIVES, [])['also'], ['tests_disagree'])

    def test_a_car_without_radar(self):
        # Set as having no radar: no "set it up", no "record a drive with the radar".
        nothing = summary_of(('drive', '0000012e7dcc3fee25', False))
        step = next_step({'status': 'no_radar', 'tested_m': []}, [], nothing, [])
        self.assertEqual((step['case'], step['also']), ('no_radar', []))
        step = next_step({'status': 'no_radar', 'tested_m': []}, self.DRAFT, nothing, [])
        self.assertEqual((step['case'], step['also']), ('test_waiting', ['no_radar']))

    def test_recordings_are_not_drives(self):
        only_recordings = summary_of(('recording', '00000134--a5c8197883', True))
        self.assertEqual(next_step(self.READY, [], only_recordings, [])['case'], 'no_radar_drive')
        self.assertEqual(radar_overview(only_recordings), {'drives_with_radar': 0, 'recordings_with_radar': 1,
                                                           'read': 1})
        self.assertIsNone(radar_overview({'drives': [], 'segments': 3, 'read': 0, 'with_radar': 0}))


class Order(unittest.TestCase):
    """Every list by route counter, newest first; why a drive has no readings."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.repo = Path(self.directory.name) / 'repo'
        self.drives = Path(self.directory.name) / 'data'

    def drive(self, name, commit, has_radar, *, start, read=True):
        (self.drives / name).mkdir(parents=True)
        (self.drives / name / 'rlog.zst').write_bytes(b'log')
        if read:
            write_series(self.repo, name, has_radar=has_radar, start=start, start_from='gps', duration=60.0)
            path = self.repo / 'outputs' / 'radar' / (name + '.json')
            path.write_text(json.dumps(dict(json.loads(path.read_text()), commit=commit)))

    def test_by_counter_and_the_reason(self):
        # The comma's clock is wrong on the newest one: the counter decides.
        self.drive('0000012e7dcc3fee2500', 'old00000', False, start=1_792_770_000.0)
        self.drive('00000130aaaaaaaaaa00', 'new00000', False, start=1_792_780_000.0)
        self.drive('00000135bbbbbbbbbb00', 'new00000', True, start=1_700_000_000.0)
        self.drive('00000136cccccccccc00', None, False, start=None, read=False)
        write_recording(self.repo, '00000134--a5c8197883--0', 2.25, start=1_785_251_511.0, start_from='comma clock')
        write_recording(self.repo, '0000012e--7dcc3fee25--50', 2.0, start=1_792_770_000.0, start_from='gps')
        summary = RadarIndex(self.repo).drives(segment_folders(self.repo, self.drives), values())
        rows = [(d['number'], d['kind'], d['no_radar_reason']) for d in summary['drives']]
        self.assertEqual(rows, [(0x136, 'drive', None), (0x135, 'drive', None), (0x134, 'recording', None),
                                (0x130, 'drive', 'same_software'), (0x12e, 'drive', 'old_software'),
                                (0x12e, 'recording', None)])

    def test_detail_names_its_number_and_kind(self):
        write_recording(self.repo, WALL, 2.25)
        detail = drive_detail(self.repo, segment_folders(self.repo, None), '00000134--a5c8197883',
                              calibration_view(values(), RadarTests(self.repo).summary()))
        self.assertEqual((detail['number'], detail['kind'], detail['calibration']['status']),
                         (0x134, 'recording', 'close_only'))


class KeptSeries(unittest.TestCase):
    """perception.radar.index: series kept before the video was part of their stamp are still theirs."""

    def test_old_stamp(self):
        with tempfile.TemporaryDirectory() as directory:
            folder, out = Path(directory) / '0000011e0c8825628a01', Path(directory) / 'radar'
            folder.mkdir()
            out.mkdir()
            log = folder / 'rlog.zst'
            log.write_bytes(b'log')
            (folder / 'fcamera.hevc').write_bytes(b'video')
            old = {key: value for key, value in stamp_of(log).items() if key != 'video'}
            (out / (folder.name + '.json')).write_text(json.dumps({'stamp': old, 'video': 'fcamera.hevc'}))
            self.assertIsNotNone(cached(out, folder))
            # Read without a video, and one arrived since: read again.
            (out / (folder.name + '.json')).write_text(json.dumps({'stamp': old, 'video': None}))
            self.assertIsNone(cached(out, folder))


class Endpoints(ServerTestCase):
    """The page's calls: shapes, same origin, JSON only, POST only."""

    def setUp(self):
        super().setUp()
        data = self.app.settings.drives_folder()[0]['path']
        for folder in data.iterdir():
            (folder / 'rlog.zst').write_bytes(b'log')
            write_series(self.repo, folder.name, has_radar=False, start=1_792_499_741.0, start_from='gps',
                         duration=60.0)
        write_recording(self.repo, WALL, 2.25, start=1_785_251_511.0, start_from='comma clock')
        self.app._drives.clear()

    def json(self, method, path, payload=None, status=200, **kwargs):
        if method == 'GET':
            response, body = self.request('GET', path, **kwargs)
        else:
            response, body = self.post(path, payload, **kwargs)
        self.assertEqual(response.status, status, body)
        self.assertTrue(response.getheader('Content-Type').startswith('application/json'))
        return json.loads(body)

    def test_radar_page(self):
        radar = self.json('GET', '/api/radar')
        self.assertEqual(set(radar), {'calibration', 'tests', 'drafts', 'summary', 'pending', 'job'})
        self.assertEqual(set(radar['calibration']), CAL_KEYS)
        self.assertEqual((radar['calibration']['status'], radar['calibration']['source'],
                          radar['calibration']['version']), ('not_set', None, 0))
        self.assertEqual(set(radar['tests']), {'tests', 'kept', 'correction_m', 'radar_to_camera_m', 'spread_m',
                                               'tested_from_m', 'tested_to_m', 'uncertainty_m'})
        self.assertEqual(radar['drafts'], [])
        # The synthetic series all carry the recording's commit: nothing was ahead on those drives.
        rows = [(d['number'], d['kind'], d['no_radar_reason']) for d in radar['summary']['drives']]
        self.assertEqual(rows, [(0x134, 'recording', None), (0x11f, 'drive', 'same_software'),
                                (0x11e, 'drive', 'same_software')])
        self.assertNotIn(self.directory.name, json.dumps(radar))
        detail = self.json('GET', '/api/radar/drives/00000134--a5c8197883')
        self.assertEqual((detail['number'], detail['kind'], set(detail['calibration'])),
                         (0x134, 'recording', CAL_KEYS))

    def test_status_names_the_next_step_without_starting_anything(self):
        status = self.json('GET', '/api/status')
        self.assertEqual(set(status['radar']), CAL_KEYS)
        self.assertEqual(status['next'], {'case': 'radar_not_set', 'also': ['no_radar_drive'],
                                          'drives_with_radar': 0})
        self.assertEqual(status['radar_summary'], {'drives_with_radar': 0, 'recordings_with_radar': 1, 'read': 7})
        self.assertEqual(self.jobs.started, [])

    def test_a_test_through_its_draft(self):
        tapes = {'target': 'wall', 'tape_radar_m': 1.32, 'tape_comma_m': 2.82}
        answer = self.json('POST', '/api/radar/drafts', {**tapes, 'marker_counter': 0x133})
        draft = answer['draft']
        self.assertEqual(answer['drafts'], [draft])
        step = self.json('GET', '/api/status')['next']
        self.assertEqual((step['case'], step['also'], step['draft_created']),
                         ('radar_not_set', ['test_waiting', 'no_radar_drive'], draft['created']))
        answer = self.json('POST', '/api/radar/tests', {'segment': WALL, 'standstill': 0, 'tape_radar_m': 1.32,
                                                        'tape_comma_m': 2.82, 'target': 'wall',
                                                        'draft_id': draft['id']})
        self.assertEqual(set(answer), {'ok', 'test', 'tests', 'calibration'})
        self.assertEqual(set(answer['test']), TEST_KEYS)
        self.assertEqual((answer['calibration']['source'], answer['calibration']['status'],
                          answer['calibration']['radar_to_reference_m']), ('tests', 'close_only', 0.57))
        self.assertEqual(self.json('GET', '/api/radar')['drafts'], [])
        status = self.json('GET', '/api/status')
        self.assertEqual((status['next']['case'], status['next']['also']), ('no_radar_drive', ['far_test']))
        test_id = answer['test']['id']
        answer = self.json('POST', '/api/radar/tests/' + test_id, {'kept': False})
        self.assertEqual(set(answer), {'ok', 'tests', 'calibration'})
        self.assertEqual(answer['calibration']['status'], 'not_set')
        self.json('POST', '/api/radar/tests/' + test_id, {'kept': True})
        self.assertEqual(self.json('POST', '/api/radar/tests/' + test_id, {'delete': True})['tests']['tests'], [])

    def test_typed_then_the_tests(self):
        answer = self.json('POST', '/api/settings/radar', {'radar_to_camera_m': 1.5, 'radar_correction_m': -0.89})
        self.assertEqual(set(answer), {'ok', 'drives', 'calibration'})
        self.assertIn('sources', answer['drives'])
        self.assertEqual((answer['calibration']['source'], answer['calibration']['status']), ('typed', 'typed'))
        self.json('POST', '/api/radar/tests', {'segment': WALL, 'standstill': 0, 'tape_radar_m': 1.32,
                                               'tape_comma_m': 2.82})
        self.assertEqual(self.json('GET', '/api/radar')['calibration']['radar_correction_m'], -0.89)
        answer = self.json('POST', '/api/radar/calibration/apply', {})
        self.assertEqual(set(answer), {'ok', 'calibration'})
        self.assertEqual((answer['calibration']['source'], answer['calibration']['radar_correction_m'],
                          answer['calibration']['version']), ('tests', -0.93, 2))
        # "My car has no radar": remembered, and Home stops asking to set it up.
        cleared = self.json('POST', '/api/settings/radar', {'radar_to_camera_m': None, 'radar_correction_m': None})
        self.assertEqual((cleared['calibration']['status'], cleared['calibration']['source']), ('no_radar', 'none'))
        status = self.json('GET', '/api/status')
        self.assertEqual((status['radar']['status'], status['next']['case'], status['next']['also']),
                         ('no_radar', 'no_radar', []))

    def test_drafts_refused(self):
        tapes = {'target': 'wall', 'tape_radar_m': 1.32, 'tape_comma_m': 2.82, 'marker_counter': None}
        self.assertEqual(self.json('POST', '/api/radar/drafts', tapes, 403,
                                   headers={'Origin': 'http://evil.example'})['error'], 'cross_origin')
        response, _ = self.request('POST', '/api/radar/drafts', b'target=wall',
                                   {'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual(response.status, 415)
        self.assertEqual(self.json('POST', '/api/radar/drafts', tapes, 401, auth=False)['error'], 'login_required')
        self.assertEqual(self.json('GET', '/api/radar/drafts', status=405)['error'], 'method_not_allowed')
        self.assertEqual(self.json('POST', '/api/radar/drafts', {**tapes, 'tape_comma_m': 0.5}, 400)['error'],
                         'invalid_param')
        self.assertEqual(self.json('POST', '/api/radar/drafts/aaaaaaaaaaaa', {'delete': True}, 404)['error'],
                         'unknown_draft')
        self.assertEqual(self.json('POST', '/api/radar/drafts/AAA', {'delete': True}, 404)['error'], 'not_found')
        for _ in range(MAX_DRAFTS):
            draft = self.json('POST', '/api/radar/drafts', tapes)['draft']
        self.assertEqual(self.json('POST', '/api/radar/drafts', tapes, 409)['error'], 'too_many_drafts')
        answer = self.json('POST', '/api/radar/drafts/' + draft['id'], {'delete': True})
        self.assertEqual(set(answer), {'ok', 'drafts'})
        self.assertEqual(len(answer['drafts']), MAX_DRAFTS - 1)
        self.assertEqual(self.json('POST', '/api/radar/calibration/apply', {}, 409)['error'], 'no_tests')
        self.assertEqual(self.json('POST', '/api/radar/tests', {'segment': WALL, 'standstill': 0,
                                                                'tape_radar_m': 1.32, 'tape_comma_m': 2.82,
                                                                'draft_id': 'aaaaaaaaaaaa'}, 404)['error'],
                         'unknown_draft')


if __name__ == '__main__':
    unittest.main()
