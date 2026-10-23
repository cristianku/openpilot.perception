"""perception-web: the sign-in wall, the pages, the drives it finds, the experiments it starts and the
results it shows. Synthetic folders only; the jobs are tiny Python commands, never a real experiment."""
from __future__ import annotations

import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import web.auth
from web.actions import JobSpec, build_job
from web.auth import AdminAuth
from web.common import WebError
from web.jobs import JobManager, parse_progress
from web.server import App, create_server
from web.settings import Settings
from web.status import (ResultReader, picture_path, segment_rows, source_candidates, source_id, spread_sample)

TOKEN = 'TestToken_1234567890abcdef'
ADMIN = 'admin'
PASSWORD = 'correct horse battery'
DISTILL_PYPROJECT = '[project]\nname = "openpilot-distill"\n'


def make_segment(data: Path, name: str, localized: bool = True) -> None:
    folder = data / name
    folder.mkdir(parents=True)
    (folder / 'fcamera.hevc').write_bytes(b'video')
    if localized:
        (folder / 'localizer.safetensors').write_bytes(b'poses')


def make_distill(base: Path, name: str = 'openpilot.distill') -> Path:
    clone = base / name
    (clone / 'data').mkdir(parents=True)
    (clone / 'pyproject.toml').write_text(DISTILL_PYPROJECT)
    return clone


class Sampling(unittest.TestCase):
    def test_spread_over_every_drive(self):
        names = [f'{route:018x}{minute:02x}' for route in range(3) for minute in range(10)]
        picked = spread_sample(names, 4)
        self.assertEqual(len(picked), 4)
        self.assertEqual(picked[0], sorted(names)[0])
        self.assertEqual(picked[-1], sorted(names)[-1])
        self.assertEqual({name[:18] for name in picked}, {name[:18] for name in names})

    def test_small_lists(self):
        self.assertEqual(spread_sample(['b', 'a'], 10), ['a', 'b'])
        self.assertEqual(spread_sample(['b', 'a'], 1), ['a'])


class Drives(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name).resolve()
        self.repo = self.base / 'openpilot.perception'
        self.repo.mkdir()
        self.addCleanup(self.directory.cleanup)

    def test_finds_the_distill_clones_next_to_it(self):
        distill = make_distill(self.base)
        make_distill(self.base, 'other-copy')
        (self.base / 'unrelated' / 'data').mkdir(parents=True)
        found = source_candidates(self.repo)
        self.assertEqual([item['path'] for item in found], [distill / 'data', self.base / 'other-copy' / 'data'])
        self.assertEqual([item['name'] for item in found], ['openpilot.distill', 'openpilot.distill (other-copy)'])
        self.assertEqual(found[0]['id'], source_id(distill / 'data'))

    def test_as_a_submodule_of_distill(self):
        distill = make_distill(self.base, 'outer')
        inner = distill / 'perception_repo'
        inner.mkdir()
        found = source_candidates(inner)
        self.assertEqual(found[0]['path'], distill / 'data')
        self.assertIn('part of it', found[0]['name'])

    def test_configured_folder_first(self):
        make_distill(self.base)
        mine = self.base / 'my-drives'
        mine.mkdir()
        self.assertEqual(source_candidates(self.repo, str(mine))[0]['path'], mine)

    def test_segments_ready_only_when_localized(self):
        data = make_distill(self.base) / 'data'
        make_segment(data, '0000011e0c8825628a01')
        make_segment(data, '0000011e0c8825628a02', localized=False)
        (data / 'not-a-segment').mkdir()
        rows = segment_rows(data)
        self.assertEqual([(row['name'], row['ready']) for row in rows],
                         [('0000011e0c8825628a01', True), ('0000011e0c8825628a02', False)])

    def test_settings_choose_by_id_never_by_path(self):
        first = make_distill(self.base)
        second = make_distill(self.base, 'second')
        make_segment(first / 'data', '0000011e0c8825628a01')
        settings = Settings(self.repo)
        chosen, _ = settings.drives_folder()
        self.assertEqual(chosen['path'], first / 'data')  # the one with localized drives
        payload = settings.choose({'drives': source_id(second / 'data')})
        self.assertNotIn(str(self.base), json.dumps(payload))
        self.assertEqual(settings.drives_folder()[0]['path'], second / 'data')
        for bad in ({'drives': '/etc'}, {'drives': 'ffffffffffff'}, {'path': 'x'}, []):
            with self.assertRaises(WebError):
                settings.choose(bad)


class RadarSetting(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = Settings(Path(self.directory.name))

    def test_set_round_and_clear(self):
        self.assertIsNone(self.settings.radar_to_camera_m())
        self.assertEqual(self.settings.set_radar({'radar_to_camera_m': 1.604})['radar_to_camera_m'], 1.6)
        self.assertEqual(Settings(Path(self.directory.name)).radar_to_camera_m(), 1.6)
        self.assertIsNone(self.settings.set_radar({'radar_to_camera_m': None})['radar_to_camera_m'])

    def test_refused(self):
        for body in ({'radar_to_camera_m': -0.1}, {'radar_to_camera_m': 9}, {'radar_to_camera_m': True},
                     {'radar_to_camera_m': '1.6'}, {'offset': 1.6}, []):
            with self.assertRaises(WebError):
                self.settings.set_radar(body)
        self.assertIsNone(self.settings.radar_to_camera_m())


class Results(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Path(self.directory.name).resolve()
        self.addCleanup(self.directory.cleanup)
        self.run = self.repo / 'outputs' / 'experiments' / '20261023-120000-mono_baseline'
        (self.run / 'pictures').mkdir(parents=True)
        (self.run / 'experiment.json').write_text(json.dumps({'kind': 'mono_baseline', 'segments': 3}))
        (self.run / 'pictures' / '0000011e0c8825628a01-0015.jpg').write_bytes(b'jpeg')
        (self.run / 'pictures' / 'notes.txt').write_text('ignored')

    def test_running_then_finished(self):
        reader = ResultReader(self.repo)
        self.assertIsNone(reader.results()[0]['summary'])
        (self.run / 'report.json').write_text(json.dumps({'summary': {'B0_all': {'absrel': 0.3}}, 'segments': []}))
        result = reader.results()[0]
        self.assertEqual(result['summary']['B0_all']['absrel'], 0.3)
        self.assertEqual(result['pictures'], ['0000011e0c8825628a01-0015.jpg'])

    def test_pictures_cannot_leave_the_results(self):
        result_id = '20261023-120000-mono_baseline'
        self.assertIsNotNone(picture_path(self.repo, result_id, '0000011e0c8825628a01-0015.jpg'))
        for name in ('../experiment.json', 'notes.txt', '0000011e0c8825628a01-0015.jpg/..'):
            self.assertIsNone(picture_path(self.repo, result_id, name))
        self.assertIsNone(picture_path(self.repo, '../../etc', '0000011e0c8825628a01-0015.jpg'))


class Actions(unittest.TestCase):
    def test_experiment_command_lines(self):
        folders = [Path('/d/0000011e0c8825628a01'), Path('/d/0000011e0c8825628a02')]
        audit = build_job('geometry_audit', {'size': 'quick'}, folders, python='py')
        self.assertEqual(audit.argv[:3], ['py', '-m', 'perception.audit.geometry_audit'])
        self.assertIn('{run_dir}/report.json', audit.argv)
        self.assertFalse(audit.needs_gpu)
        self.assertIn('2 segments from 1 drive', audit.summary)
        baseline = build_job('mono_baseline', {'size': 'all'}, folders, python='py')
        self.assertEqual(baseline.argv[:3], ['flock', '-n', '/run/lock/v100-gpu.lock'])
        self.assertTrue(baseline.needs_gpu)

    def test_bad_parameters(self):
        for action, params in (('geometry_audit', {'size': 'huge'}), ('geometry_audit', {'size': 'quick', 'x': 1}),
                               ('update', {'size': 'quick'}), ('rm', {})):
            with self.assertRaises(WebError):
                build_job(action, params, [])

    def test_progress(self):
        self.assertEqual(parse_progress('a\nPROGRESS 1/4\nb\nPROGRESS 3/4\n'),
                         {'done': 3, 'total': 4, 'percent': 75.0})
        self.assertIsNone(parse_progress('nothing'))


class Jobs(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.repo = Path(self.directory.name).resolve()
        self.addCleanup(self.directory.cleanup)
        self.jobs = JobManager(self.repo, code_root=Path(web.auth.__file__).resolve().parent.parent,
                               gpu_check=lambda: 'the GPU is busy')

    def wait(self, job_id):
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            view = self.jobs.get(job_id)
            if view['state'] != 'running':
                return view
            time.sleep(0.05)
        self.fail('the job did not end')

    def test_a_job_writes_its_result(self):
        prepared = []
        script = ('import sys, pathlib; out = pathlib.Path(sys.argv[1]); print("PROGRESS 1/1", flush=True); '
                  'out.write_text("{\\"summary\\": {}}")')
        spec = JobSpec('geometry_audit', 'Geometry audit', 'geometry_audit',
                       [sys.executable, '-c', script, '{run_dir}/report.json'],
                       prepare=lambda run_dir, job_id: prepared.append((run_dir, job_id)))
        job = self.jobs.start(spec)
        view = self.wait(job['id'])
        self.assertEqual(view['state'], 'ok', self.jobs.log(job['id'], 0)['text'])
        self.assertEqual(view['result'], job['id'])
        self.assertEqual(prepared[0][1], job['id'])
        self.assertIn('PROGRESS 1/1', self.jobs.log(job['id'], 0)['text'])

    def test_one_per_group_and_the_gpu(self):
        with self.assertRaises(WebError) as refused:
            self.jobs.start(JobSpec('mono_baseline', 'Depth baseline', 'mono_baseline', [sys.executable, '-c', ''],
                                    needs_gpu=True))
        self.assertEqual(refused.exception.reason, 'gpu_busy')
        self.assertIn('nothing else', JobManager.conflict_reason('system', {'geometry_audit'}))
        self.assertIn('already', JobManager.conflict_reason('geometry_audit', {'geometry_audit'}))
        self.assertEqual(JobManager.conflict_reason('mono_baseline', {'geometry_audit'}), '')


class FakeJobs:
    """Records what the site would start."""

    def __init__(self):
        self.started = []

    def list_jobs(self):
        return []

    @staticmethod
    def conflict_reason(group, running):
        return ''

    def start(self, spec):
        run_dir = Path(tempfile.mkdtemp())
        if spec.prepare:
            spec.prepare(run_dir, '20261023-120000-' + spec.action)
        self.started.append((spec, run_dir))
        return {'id': '20261023-120000-' + spec.action, 'label': spec.label}


class ServerTestCase(unittest.TestCase):
    with_admin = True

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name).resolve()
        self.repo = base / 'openpilot.perception'
        self.repo.mkdir()
        data = make_distill(base) / 'data'
        for minute in range(1, 6):
            make_segment(data, f'0000011e0c88256289{minute:02x}')
        make_segment(data, '0000011f77d17f9ee301')
        patcher = mock.patch.object(web.auth, 'SCRYPT_N', 2 ** 10)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.auth = AdminAuth(self.repo / 'outputs' / 'web' / 'auth', TOKEN)
        self.session = self.auth.setup(TOKEN, ADMIN, PASSWORD, 'test') if self.with_admin else None
        self.jobs = FakeJobs()
        app = App(self.repo, jobs=self.jobs, auth=self.auth, gpu_check=lambda: '')
        self.server, self.app = create_server(self.repo, host='127.0.0.1', port=0, setup_code=TOKEN, app=app)
        self.port = self.server.server_port
        self.cookie = 'perception_web_' + str(self.port) + '=' + str(self.session)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, headers=None, auth=True):
        connection = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10)
        all_headers = dict(headers or {})
        if auth:
            all_headers['Cookie'] = self.cookie
        connection.request(method, path, body=body, headers=all_headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response, data

    def post(self, path, payload, headers=None, auth=True):
        all_headers = {'Content-Type': 'application/json', **(headers or {})}
        return self.request('POST', path, json.dumps(payload).encode(), all_headers, auth)


class SignInWall(ServerTestCase):
    def test_only_the_sign_in_page_without_a_session(self):
        response, page = self.request('GET', '/', auth=False)
        self.assertEqual(response.status, 200)
        self.assertIn(b'Perception', page)
        self.assertNotIn(b'__PERCEPTION_NONCE__', page)
        self.assertIn("script-src 'nonce-", response.getheader('Content-Security-Policy'))
        for path in ('/api/status', '/api/results', '/static/app.js', '/picture/x/y.jpg'):
            self.assertEqual(self.request('GET', path, auth=False)[0].status, 401, path)
        self.assertEqual(self.post('/api/run/geometry_audit', {}, auth=False)[0].status, 401)

    def test_sign_in_sets_the_cookie_of_this_port(self):
        response, _ = self.post('/api/auth/login', {'user': ADMIN, 'password': PASSWORD}, auth=False)
        self.assertEqual(response.status, 200)
        cookie = response.getheader('Set-Cookie')
        self.assertTrue(cookie.startswith('perception_web_' + str(self.port) + '='))
        self.assertIn('HttpOnly', cookie)
        self.assertIn('SameSite=Strict', cookie)

    def test_other_origins_and_forms_are_refused(self):
        response, _ = self.post('/api/run/geometry_audit', {'size': 'quick'}, {'Origin': 'http://evil.example'})
        self.assertEqual(response.status, 403)
        response, _ = self.request('POST', '/api/run/geometry_audit', b'size=quick',
                                   {'Content-Type': 'application/x-www-form-urlencoded'})
        self.assertEqual(response.status, 415)
        self.assertEqual(self.jobs.started, [])


class FirstVisit(ServerTestCase):
    with_admin = False

    def test_setup_code_then_account(self):
        self.assertEqual(json.loads(self.request('GET', '/api/auth/state', auth=False)[1])['configured'], False)
        self.assertEqual(self.post('/api/auth/setup/check', {'code': 'wrong-code-0000000'}, auth=False)[0].status, 403)
        response, _ = self.post('/api/auth/setup', {'code': TOKEN, 'user': ADMIN, 'password': PASSWORD}, auth=False)
        self.assertEqual(response.status, 200)
        self.assertTrue(self.auth.configured())


class Pages(ServerTestCase):
    def test_page_and_static_files(self):
        response, page = self.request('GET', '/')
        self.assertEqual(response.status, 200)
        for marker in (b'id="panel-overview"', b'id="panel-experiments"', b'id="panel-results"', b'class="gear"'):
            self.assertIn(marker, page)
        for name in ('app.js', 'app.css', 'login.js', 'favicon.svg'):
            self.assertEqual(self.request('GET', '/static/' + name)[0].status, 200, name)
        self.assertEqual(self.request('GET', '/static/server.py')[0].status, 404)

    def test_status_names_the_drives_never_their_path(self):
        response, body = self.request('GET', '/api/status')
        self.assertEqual(response.status, 200)
        status = json.loads(body)
        self.assertEqual(status['drives']['counts']['ready'], 6)
        self.assertEqual(status['drives']['counts']['ready_routes'], 2)
        self.assertEqual(status['drives']['sizes'], {'quick': 6, 'standard': 6, 'all': 6})
        self.assertEqual(status['drives']['source']['name'], 'openpilot.distill')
        self.assertNotIn(self.directory.name, body.decode())
        self.assertTrue(status['actions']['geometry_audit']['allowed'])
        settings = self.request('GET', '/api/settings')[1].decode()
        self.assertNotIn(self.directory.name, settings)

    def test_start_an_experiment(self):
        response, body = self.post('/api/run/geometry_audit', {'size': 'quick'})
        self.assertEqual(response.status, 200, body)
        spec, run_dir = self.jobs.started[0]
        folders = [item for item in spec.argv if '/openpilot.distill/data/' in item]
        self.assertEqual(len(folders), 6)
        meta = json.loads((run_dir / 'experiment.json').read_text())
        self.assertEqual((meta['kind'], meta['segments'], meta['routes']), ('geometry_audit', 6, 2))

    def test_radar_setting_from_the_page(self):
        response, body = self.post('/api/settings/radar', {'radar_to_camera_m': 1.6})
        self.assertEqual(response.status, 200, body)
        self.assertEqual(json.loads(self.request('GET', '/api/status')[1])['radar_to_camera_m'], 1.6)
        self.assertEqual(self.post('/api/settings/radar', {'radar_to_camera_m': 1.6},
                                   {'Origin': 'http://evil.example'})[0].status, 403)
        self.assertEqual(self.post('/api/settings/radar', {'radar_to_camera_m': 1.6}, auth=False)[0].status, 401)

    def test_refused_without_drives(self):
        source = self.app.settings.drives_folder()[0]['path']
        for folder in source.iterdir():
            (folder / 'localizer.safetensors').unlink()
        self.app._drives.clear()
        status = json.loads(self.request('GET', '/api/status')[1])
        self.assertFalse(status['actions']['mono_baseline']['allowed'])
        response, body = self.post('/api/run/mono_baseline', {'size': 'quick'})
        self.assertEqual(response.status, 409)
        self.assertIn('localize', json.loads(body)['message'])


if __name__ == '__main__':
    unittest.main()
