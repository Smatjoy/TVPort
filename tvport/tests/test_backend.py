import argparse
import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import backend  # noqa: E402

LOG = '''[dm] [nvn-mtl] memory up to frame 2700: resident 1867 MB, GPU allocated 1483 MB, deferred-release queue 10; headroom to the system memory limit 230 MB
[longframe] frame 1491 (27.11 s) whole frame 556.1 ms, submit+present 533.1 ms, GPU 238.4 ms; new pipelines 0 + shader libraries 0, 0.0 ms in all; texture uploads 264 first + 0 re-uploads, 61.4 MB / 284.6 ms; draws with a hybrid pipeline 0
[longframe] frame 1632 (29.47 s) whole frame 54.5 ms, submit+present 40.5 ms, GPU 9.5 ms; new pipelines 4 + shader libraries 0, 31.8 ms in all; texture uploads 0 first + 0 re-uploads, 0.0 MB / 0.0 ms; draws with a hybrid pipeline 0
[longframe] frame 1535 (27.84 s) whole frame 23.3 ms, submit+present 7.6 ms, GPU 20.5 ms; new pipelines 0 + shader libraries 0, 0.0 ms in all; texture uploads 0 first + 0 re-uploads, 0.0 MB / 0.0 ms; draws with a hybrid pipeline 0
[dm] [nvn] on-screen up to frame 3000: 300 frames, dropped 0 (discarded 0), p99 16.68 ms; footprint 1873 MiB; brightness -1.00; thermal 0
[dm] [nvn-mtl] parallel precompile: 363 draws left in this submission, 2 pipelines handed to the background
'''


class ReadLog(unittest.TestCase):
    def setUp(self):
        self.run_log = backend.read_log(LOG)

    def test_memory(self):
        self.assertEqual(self.run_log['peak_mb'], 1873)
        self.assertEqual(self.run_log['gpu_mb'], 1483)
        self.assertEqual(self.run_log['min_headroom_mb'], 230)
        self.assertEqual(self.run_log['last_frame'], 3000)

    def test_only_real_hitches_are_counted(self):
        self.assertEqual(self.run_log['hitches'], 2)
        self.assertEqual(self.run_log['worst_ms'], 556.1)
        self.assertEqual(self.run_log['texture_ms'], 284.6)
        self.assertEqual(self.run_log['pipeline_ms'], 31.8)

    def test_gpu_overruns(self):
        self.assertEqual(self.run_log['gpu_slow_frames'], 2)

    def test_last_line(self):
        self.assertTrue(self.run_log['ended_on_precompile'])

    def test_empty_log(self):
        empty = backend.read_log('')
        self.assertEqual((empty['peak_mb'], empty['hitches'], empty['min_headroom_mb']), (0, 0, None))


class Verdicts(unittest.TestCase):
    def test_textures_are_named_as_the_cause(self):
        text = ' '.join(backend.verdicts(backend.read_log(LOG), []))
        self.assertIn('mostly texture loading', text)

    def test_memory_kill_comes_first(self):
        kills = [{'time': '2026-11-02 11:52:39.00 -0700', 'reason': 'per-process-limit', 'mb': 2100}]
        out = backend.verdicts(backend.read_log(LOG), kills)
        self.assertIn('2,100 MB', out[0])

    def test_clean_run(self):
        out = backend.verdicts(backend.read_log('[dm] [nvn] on-screen up to frame 600: 300 frames, dropped 0 (discarded 0), p99 16.68 ms; footprint 900 MiB; thermal 0'), [])
        self.assertEqual(len(out), 1)
        self.assertIn('Nothing unusual', out[0])
        self.assertIn('closed by itself', out[0])


class Jetsam(unittest.TestCase):
    def test_reports_only_this_apps_kills(self):
        report = {'date': '2026-11-02 11:52:39', 'memoryStatus': {'pageSize': 16384}, 'processes': [
            {'name': 'DmIOS', 'rpages': 134400, 'reason': 'per-process-limit'}, {'name': 'cloudd', 'rpages': 100, 'reason': 'per-process-limit'}]}
        alive = {'date': '2026-11-02 11:53:13', 'memoryStatus': {'pageSize': 16384}, 'processes': [{'name': 'DmIOS', 'rpages': 110000}]}
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'JetsamEvent-1.ips').write_text('{"bug_type":"298"}\n' + json.dumps(report))
            (Path(folder) / 'JetsamEvent-2.ips').write_text('{"bug_type":"298"}\n' + json.dumps(alive))
            (Path(folder) / 'JetsamEvent-3.ips').write_text('not json')
            self.assertEqual(backend.read_jetsam(Path(folder)), [{'time': '2026-11-02 11:52:39', 'reason': 'per-process-limit', 'mb': 2100}])

    def test_newest_kill_last(self):
        def report(date): return '{"bug_type":"298"}\n' + json.dumps({'date': date, 'processes': [{'name': 'DmIOS', 'rpages': 134000, 'reason': 'per-process-limit'}]})
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'Retired').mkdir()
            (Path(folder) / 'JetsamEvent-2026-11-03-090346.ips').write_text(report('2026-11-03 09:03:46.65 -0700'))
            (Path(folder) / 'Retired/JetsamEvent-2026-11-02-135952.ips').write_text(report('2026-11-02 13:59:52.64 -0700'))
            self.assertEqual([kill['time'] for kill in backend.read_jetsam(Path(folder))], ['2026-11-02 13:59:52.64 -0700', '2026-11-03 09:03:46.65 -0700'])


class Failures(unittest.TestCase):
    def test_the_reason_survives_a_quiet_ending(self):
        script = "echo '[RED] This game needs a device'; echo noise; echo '** BUILD FAILED **'; exit 65"
        with self.assertRaises(backend.Failure) as caught:
            backend.stream(['/bin/sh', '-c', script], log_filter=lambda line: False)
        self.assertIn('needs a device', str(caught.exception))
        self.assertNotIn('noise', str(caught.exception))

    def test_a_failure_without_a_reason_shows_the_last_lines(self):
        with self.assertRaises(backend.Failure) as caught:
            backend.stream(['/bin/sh', '-c', 'echo last words; exit 1'], log_filter=lambda line: False)
        self.assertIn('last words', str(caught.exception))


class OneBuildAtATime(unittest.TestCase):
    def test_a_second_build_is_refused_and_the_lock_comes_back(self):
        with tempfile.TemporaryDirectory() as folder:
            original, backend.DATA = backend.DATA, Path(folder)
            try:
                first = backend.one_build_at_a_time()
                with self.assertRaises(backend.Failure):
                    backend.one_build_at_a_time()
                first.close()
                backend.one_build_at_a_time().close()
            finally:
                backend.DATA = original


class Standalone(unittest.TestCase):
    def make_bundle(self, folder, archive=None):
        bundle, source = Path(folder) / 'Resources', Path(folder) / 'source'
        (source / 'dm').mkdir(parents=True)
        bundle.mkdir()
        (source / 'dm/tool.py').write_text('print(1)\n')
        (bundle / 'engine.json').write_text(json.dumps({'id': 'abc123'}))
        if archive is None:
            subprocess.run(['/usr/bin/tar', '-cJf', bundle / 'engine.tar.xz', '-C', source, 'dm'], check=True)
        else:
            (bundle / 'engine.tar.xz').write_bytes(archive)
        return bundle

    def test_the_engine_is_unpacked_once_and_earlier_ones_go(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle, data = self.make_bundle(folder), Path(folder) / 'data'
            engine = backend.unpack_engine(bundle, data)
            self.assertEqual((engine / 'dm/tool.py').read_text(), 'print(1)\n')
            self.assertEqual(json.loads((engine / 'engine.json').read_text())['id'], 'abc123')
            (engine / 'dm/tool.py').write_text('changed by a build')
            self.assertEqual(backend.unpack_engine(bundle, data), engine)
            self.assertEqual((engine / 'dm/tool.py').read_text(), 'changed by a build')
            (data / 'engine/older').mkdir()
            backend.prune_engines(engine)
            self.assertEqual(sorted(p.name for p in (data / 'engine').iterdir() if p.is_dir()), ['abc123'])

    def test_a_broken_archive_leaves_no_engine(self):
        with tempfile.TemporaryDirectory() as folder:
            bundle, data = self.make_bundle(folder, archive=b'not an archive'), Path(folder) / 'data'
            with self.assertRaises(backend.Failure):
                backend.unpack_engine(bundle, data)
            self.assertEqual([p.name for p in (data / 'engine').iterdir() if p.is_dir()], [])

    def test_python_is_made_again_when_its_requirements_change(self):
        with tempfile.TemporaryDirectory() as folder:
            originals = backend.DATA, backend.REQUIREMENTS
            backend.DATA, backend.REQUIREMENTS = Path(folder), Path(folder) / 'requirements.txt'
            try:
                backend.REQUIREMENTS.write_text('lz4==1\n')
                self.assertFalse(backend.venv_ready())
                (backend.DATA / 'venv/bin').mkdir(parents=True)
                (backend.DATA / 'venv/bin/python3').write_text('')
                (backend.DATA / 'venv/tvport-requirements.txt').write_text(backend.python_stamp())
                self.assertTrue(backend.venv_ready())
                backend.REQUIREMENTS.write_text('lz4==2\n')
                self.assertFalse(backend.venv_ready())
            finally:
                backend.DATA, backend.REQUIREMENTS = originals

    def test_the_personal_team_of_a_signed_in_apple_id_is_found(self):
        prefs = {'IDEProvisioningTeamByIdentifier': {'account': [{'teamID': 'PAID123456', 'isFreeProvisioningTeam': False}, {'teamID': 'FREE123456', 'isFreeProvisioningTeam': True}]}}
        self.assertEqual(backend.xcode_teams(prefs), ['FREE123456', 'PAID123456'])
        self.assertEqual(backend.xcode_teams({}), [])

    def test_the_app_never_carries_game_files(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
        import package_tvport
        bad = ['G/0100000000010000_build/extract/x.tsv', 'G/prod.keys', 'G/Game.nsp', 'dm/games/0100000000010000/c1_full.tsv', 'x/DmIOS.app/Info.plist', 'G/RomFS.img']
        good = ['dm/games/0100000000010000/game.tsv', 'dm/tools/nacp.py', 'scripts/build_ipa_from_nsp.py', 'LICENSE']
        self.assertEqual(package_tvport.unsafe(bad + good), bad)


class ShaderList(unittest.TestCase):
    def test_rows_merge_once_in_first_seen_order(self):
        self.assertEqual(backend.merged_rows('# header\nS\ta\t0\nR\tb\n', 'R\tb\nC\tc\n\n'), ['S\ta\t0', 'R\tb', 'C\tc'])

    def test_saved_rows_join_the_manifest_after_its_header(self):
        with tempfile.TemporaryDirectory() as folder:
            build, project = Path(folder), Path(folder) / 'project'
            (project / 'Gen/dm').mkdir(parents=True)
            (project / 'Gen/dm/pso_manifest.tsv').write_text('# header\nS\ta\t0\n')
            (build / backend.SHADER_LIST).write_text('S\ta\t0\nR\tb\n')
            game = type('G', (), {'build': build})()
            backend.bundle_shaders(game, project)
            self.assertEqual((project / 'Gen/dm/pso_manifest.tsv').read_text(), '# header\nS\ta\t0\nR\tb\n')

    def test_no_saved_list_leaves_the_manifest_alone(self):
        with tempfile.TemporaryDirectory() as folder:
            build, project = Path(folder), Path(folder) / 'project'
            (project / 'Gen/dm').mkdir(parents=True)
            (project / 'Gen/dm/pso_manifest.tsv').write_text('# header\n')
            backend.bundle_shaders(type('G', (), {'build': build})(), project)
            self.assertEqual((project / 'Gen/dm/pso_manifest.tsv').read_text(), '# header\n')


def listing(files):
    """What the TV lists for these saves: a change of content changes the size here."""
    return {name: [len(data), '2026-11-02T12:00:00.000Z'] for name, data in files.items()}


class Saves(unittest.TestCase):
    def setUp(self):
        self.real_icloud = backend.ICLOUD
        backend.ICLOUD = Path(tempfile.gettempdir()) / 'tvport-tests-no-icloud'   # iCloud Drive off, whatever this Mac has

    def tearDown(self):
        backend.ICLOUD = self.real_icloud

    def test_only_committed_saves_count(self):
        keep = ['save/File1.bin', 'save/sub/a.bin', 'save_device/x.bin', 'save.p-sam/File1.bin', 'save.p-sam-2_device/x.bin']
        drop = ['save.work/File1.bin', 'save.new/File1.bin', 'save.sync', 'save.sync-tmp/out/x', 'save-backups/2026/save/File1.bin', 'save.p-sam.work/File1.bin', 'romfs.img', 'save/']
        self.assertEqual([n for n in keep + drop if backend.SAVE_FILE.match(n)], keep)

    def make_game(self, folder):
        build = Path(folder) / '0100000000010000_build'
        build.mkdir()
        return backend.Game(Path(folder))

    def fake_device(self, files, copied=None):
        names, copied = list(files), copied if copied is not None else []

        def fake_run(argv, **kwargs):
            argv = [str(a) for a in argv]
            if 'from' in argv:
                source = argv[argv.index('--source') + 1].replace('Library/Caches/', '')
                target = Path(argv[argv.index('--destination') + 1])
                target.write_bytes(files[source])
            else:
                copied.append(argv[argv.index('--destination') + 1])
            return type('R', (), {'returncode': 0, 'stderr': '', 'stdout': ''})()
        return names, fake_run, copied

    def test_backup_then_identical_backup_then_change(self):
        files = {'save/File1.bin': b'one', 'save.p-sam/File1.bin': b'two'}
        copies = []
        _, fake_run, _ = self.fake_device(files)
        real_run, real_list = backend.run, backend.save_listing
        backend.run, backend.save_listing = lambda argv, **kw: copies.append(argv) or fake_run(argv, **kw), lambda d, b: listing(files)
        try:
            with tempfile.TemporaryDirectory() as folder:
                game = self.make_game(folder)
                first = backend.backup_saves(game, 'd', 'b')
                self.assertTrue(first['changed'])
                self.assertEqual((Path(first['folder']) / 'save.p-sam/File1.bin').read_bytes(), b'two')
                second = backend.backup_saves(game, 'd', 'b')
                self.assertEqual((second['changed'], second['state'], len(copies)), (False, 'unchanged', 2))   # the same listing: nothing copied
                self.assertEqual(len(list((game.build / 'save-backups').iterdir())), 1)
                files['save/File1.bin'] = b'three'
                self.assertTrue(backend.backup_saves(game, 'd', 'b')['changed'])   # in the same second as the first: it must not land on top of it
                self.assertEqual(len(list((game.build / 'save-backups').iterdir())), 2)
        finally:
            backend.run, backend.save_listing = real_run, real_list

    def test_a_failed_copy_leaves_no_backup(self):
        real_run, real_list = backend.run, backend.save_listing
        backend.run = lambda argv, **kwargs: type('R', (), {'returncode': 1, 'stderr': '', 'stdout': ''})()
        backend.save_listing = lambda d, b: listing({'save/File1.bin': b'x'})
        try:
            with tempfile.TemporaryDirectory() as folder:
                game = self.make_game(folder)
                with self.assertRaises(backend.Failure):
                    backend.backup_saves(game, 'd', 'b')
                self.assertIsNone(backend.latest_backup(game))
        finally:
            backend.run, backend.save_listing = real_run, real_list

    def test_old_backups_are_trimmed(self):
        with tempfile.TemporaryDirectory() as folder:
            game = self.make_game(folder)
            for i in range(backend.KEEP_BACKUPS):
                old = game.build / f'save-backups/2020-01-{i:02d}/save'
                old.mkdir(parents=True)
                (old / 'File1.bin').write_bytes(b'old')
            _, fake_run, _ = self.fake_device({'save/File1.bin': b'new'})
            real_run, real_list = backend.run, backend.save_listing
            backend.run, backend.save_listing = fake_run, lambda d, b: listing({'save/File1.bin': b'new'})
            try:
                backend.backup_saves(game, 'd', 'b')
            finally:
                backend.run, backend.save_listing = real_run, real_list
            kept = sorted(p.name for p in (game.build / 'save-backups').iterdir())
            self.assertEqual(len(kept), backend.KEEP_BACKUPS)
            self.assertNotIn('2020-01-00', kept)

    def test_no_saves_on_the_tv_means_no_backup(self):
        real = backend.save_listing
        try:
            with tempfile.TemporaryDirectory() as folder:
                game = self.make_game(folder)
                backend.save_listing = lambda d, b: {}
                self.assertEqual(backend.backup_saves(game, 'd', 'b')['state'], 'none')
                backend.save_listing = lambda d, b: None
                self.assertEqual(backend.backup_saves(game, 'd', 'b')['state'], 'unavailable')   # a TV that did not answer
        finally:
            backend.save_listing = real

    def test_a_save_during_the_copy_is_thrown_away(self):
        files = {'save/File1.bin': b'one'}
        answers = [listing(files), listing({'save/File1.bin': b'newer'})]   # the game committed between the two listings
        _, fake_run, _ = self.fake_device(files)
        real_run, real_list = backend.run, backend.save_listing
        backend.run, backend.save_listing = fake_run, lambda d, b: answers.pop(0) if answers else listing(files)
        try:
            with tempfile.TemporaryDirectory() as folder:
                game = self.make_game(folder)
                result = backend.backup_saves(game, 'd', 'b', attempts=1)
                self.assertEqual(result['state'], 'busy')
                self.assertIsNone(backend.latest_backup(game))
                self.assertEqual([p.name for p in game.build.iterdir() if p.name.startswith('.save-backup-incoming')], [])
        finally:
            backend.run, backend.save_listing = real_run, real_list

    def test_manifests_compare_size_and_time(self):
        files = {'save/File1.bin': b'one'}
        _, fake_run, _ = self.fake_device(files)
        stamp = ['2026-11-02T12:00:00.000Z']
        real_run, real_list = backend.run, backend.save_listing
        backend.run, backend.save_listing = fake_run, lambda d, b: {'save/File1.bin': [len(files['save/File1.bin']), stamp[0]]}
        try:
            with tempfile.TemporaryDirectory() as folder:
                game = self.make_game(folder)
                first = backend.backup_saves(game, 'd', 'b')
                self.assertEqual(backend.read_manifest(Path(first['folder'])), {'save/File1.bin': [3, stamp[0]]})
                stamp[0] = '2026-11-02T12:05:00.000Z'   # written again with the same bytes: copied, compared, and no new backup
                self.assertEqual(backend.backup_saves(game, 'd', 'b')['state'], 'unchanged')
                self.assertEqual(backend.read_manifest(Path(first['folder'])), {'save/File1.bin': [3, stamp[0]]})
                files['save/File1.bin'] = b'two'   # same size, new time, new bytes
                stamp[0] = '2026-11-02T12:10:00.000Z'
                self.assertEqual(backend.backup_saves(game, 'd', 'b')['state'], 'backed-up')
        finally:
            backend.run, backend.save_listing = real_run, real_list

    def test_retention_keeps_twenty_and_one_a_day(self):
        today = backend.datetime(2026, 11, 2).date()
        recent = [f'2026-11-02-{h:02d}{m:02d}00' for h in range(10, 12) for m in range(0, 60, 5)]   # 24 rounds this morning
        daily = [f'2026-10-{d:02d}-{t}' for d in range(5, 32) for t in ('080000', '200000')]
        old = ['2026-10-03-200000', '2025-01-01-120000']
        kept = backend.kept_backups(recent + daily + old + ['notes'], today)
        self.assertEqual(sorted(kept)[-20:], recent[-20:])
        self.assertEqual({n for n in kept if not n.startswith('2026-11-02')}, {f'2026-10-{d:02d}-200000' for d in range(5, 32)})   # 30 days back: the newest of each day
        self.assertEqual(len(kept), 20 + 27)

    def test_the_newest_backup_of_both_places_wins(self):
        with tempfile.TemporaryDirectory() as folder:
            game = self.make_game(folder)
            mac, cloud = game.build / 'save-backups', Path(folder) / 'iCloud/TVPort Saves' / game.title_id
            for place, name in ((mac, '2026-11-02-100000'), (mac, '2026-11-02-120000'), (cloud, '2026-11-02-110000'), (cloud, '2026-11-02-120000-2'), (cloud, '.2026-11-02-130000.partial')):
                (place / name / 'save').mkdir(parents=True)
            self.assertEqual(backend.latest_backup(game), mac / '2026-11-02-120000')   # iCloud Drive off: only this Mac's
            backend.ICLOUD = Path(folder) / 'iCloud'
            self.assertEqual(backend.latest_backup(game), cloud / '2026-11-02-120000-2')
            self.assertEqual(backend.backup_time(backend.latest_backup(game)), backend.datetime(2026, 11, 2, 12).timestamp())

    def test_icloud_backups_go_to_icloud_drive(self):
        files = {'save/File1.bin': b'one'}
        _, fake_run, _ = self.fake_device(files)
        real_run, real_list = backend.run, backend.save_listing
        backend.run, backend.save_listing = fake_run, lambda d, b: listing(files)
        try:
            with tempfile.TemporaryDirectory() as folder:
                game = self.make_game(folder)
                backend.ICLOUD = Path(folder) / 'iCloud'
                backend.ICLOUD.mkdir()
                result = backend.backup_saves(game, 'd', 'b', icloud=True)
                self.assertEqual(Path(result['folder']).parent, backend.ICLOUD / 'TVPort Saves/0100000000010000')
                self.assertFalse((game.build / 'save-backups').exists())
        finally:
            backend.run, backend.save_listing = real_run, real_list

    def test_restore_only_when_the_tv_has_lost_its_saves(self):
        copied = []
        _, fake_run, _ = self.fake_device({}, copied)
        real_run, real_list = backend.run, backend.save_files
        backend.run = fake_run
        try:
            with tempfile.TemporaryDirectory() as folder:
                game = self.make_game(folder)
                saved = game.build / 'save-backups/2026-11-02-150000/save'
                saved.mkdir(parents=True)
                (saved / 'File1.bin').write_bytes(b'x')
                backend.save_files = lambda d, b: ['save/File1.bin']
                self.assertEqual(backend.restore_saves(game, 'd', 'b'), 0)
                self.assertEqual(copied, [])
                backend.save_files = lambda d, b: []
                self.assertEqual(backend.restore_saves(game, 'd', 'b'), 1)
                self.assertEqual(copied, ['Library/Caches/save/File1.bin'])
        finally:
            backend.run, backend.save_files = real_run, real_list

    def test_players_reach_the_app(self):
        with tempfile.TemporaryDirectory() as folder:
            game = self.make_game(folder)
            self.assertIn('DM_TV_PLAYERS=Taylor, Sam\n', backend.write_env(game, 'handheld', 512, ' Taylor,  Sam ').read_text())
            self.assertNotIn('DM_TV_PLAYERS', backend.write_env(game, 'handheld', 512, '').read_text())

    def test_render_scale_reaches_the_app(self):
        with tempfile.TemporaryDirectory() as folder:
            game = self.make_game(folder)
            self.assertIn('DM_RENDER_SCALE=0.75\n', backend.write_env(game, 'handheld', 512, '', 0.75).read_text())
            self.assertNotIn('DM_RENDER_SCALE', backend.write_env(game, 'handheld', 512, '').read_text())   # full size: the game's own, dynamic resolution included
            self.assertEqual(backend.render_scale_arg('0.6'), 0.6)
            with self.assertRaises(Exception): backend.render_scale_arg('0.4')

    def test_render_scale_offered_only_with_the_stub(self):
        with tempfile.TemporaryDirectory() as folder:
            game = self.make_game(folder)
            self.assertFalse(game.render_scale_supported())   # not rewritten yet
            patched = game.build / 'patched_v720896'; patched.mkdir()
            (patched / 'main.fixups.tsv').write_text('# FPS_STATE_OFF\t0x3ad4000\n')
            self.assertFalse(backend.Game(Path(folder)).render_scale_supported())
            (patched / 'main.fixups.tsv').write_text('# FPS_STATE_OFF\t0x3ad4000\n# RENDER_SCALE\t1\n')
            self.assertTrue(backend.Game(Path(folder)).render_scale_supported())
            self.assertTrue(backend.Game(Path(folder)).frame_step_supported())

    def test_frame_rate_target_reaches_the_app(self):
        with tempfile.TemporaryDirectory() as folder:
            game = self.make_game(folder)
            self.assertIn('DM_FPS_TARGET=30\n', backend.write_env(game, 'handheld', 512, '', 1.0, True, 30).read_text())
            self.assertNotIn('DM_FPS_TARGET', backend.write_env(game, 'handheld', 512, '').read_text())   # the profile's own
            self.assertEqual(game.profile_value('fps_target'), '')   # Odyssey's profile has no frame-rate target
            (game.build / 'profile/0100000000010000').mkdir(parents=True)
            (game.build / 'profile/0100000000010000/game.tsv').write_text('title_id\t0100000000010000\nfps_target\t60\n')
            self.assertEqual(backend.Game(Path(folder)).profile_value('fps_target'), '60')   # a derived profile wins

    def test_thermal_cap_can_be_left_off(self):
        with tempfile.TemporaryDirectory() as folder:
            game = self.make_game(folder)
            self.assertIn('DM_THERMAL_CAP=0\n', backend.write_env(game, 'handheld', 512, '', 1.0, False).read_text())
            self.assertNotIn('DM_THERMAL_CAP', backend.write_env(game, 'handheld', 512, '').read_text())

    def test_archive_turns_on_the_metal_4_path(self):
        with tempfile.TemporaryDirectory() as folder:
            game = self.make_game(folder)
            self.assertIn('DM_PSO_MTL4=1\n', backend.write_env(game, 'docked', 0, archive=True).read_text())
            self.assertNotIn('DM_PSO_MTL4', backend.write_env(game, 'handheld', 512).read_text())


class FindKeys(unittest.TestCase):
    def test_keys_beside_the_game_win(self):
        with tempfile.TemporaryDirectory() as folder:
            game = Path(folder) / 'Game'
            game.mkdir()
            (Path(folder) / 'prod.keys').write_text('x')
            self.assertEqual(backend.find_keys(backend.Game(game)).name, 'prod.keys')


class Launching(unittest.TestCase):
    def test_finds_only_this_game(self):
        listing = {'result': {'runningProcesses': [
            {'processIdentifier': 7, 'executable': 'file:///private/var/containers/Bundle/Application/X/DmIOS.app/DmIOS'},
            {'processIdentifier': 8, 'executable': 'file:///Applications/HeadBoard.app/HeadBoard'},
            {'processIdentifier': 9}]}}
        self.assertEqual(backend.running_pids(listing), [7])


class Devices(unittest.TestCase):
    def test_keeps_real_apple_tvs_only(self):
        listing = {'result': {'devices': [
            {'hardwareProperties': {'deviceType': 'appleTV', 'reality': 'simulated', 'udid': 'SIM'}},
            {'hardwareProperties': {'deviceType': 'iPhone', 'reality': 'physical', 'udid': 'PHONE'}},
            {'hardwareProperties': {'deviceType': 'appleTV', 'reality': 'physical', 'udid': 'TV', 'marketingName': 'Apple TV 4K (3rd generation)'},
             'deviceProperties': {'name': 'Basement', 'osVersionNumber': '27.2', 'developerModeStatus': 'enabled'}, 'connectionProperties': {'tunnelState': 'connected'}}]}}
        tvs = backend.apple_tvs(listing)
        self.assertEqual([t['id'] for t in tvs], ['TV'])
        self.assertTrue(tvs[0]['connected'] and tvs[0]['developer_mode'])

    def test_knows_the_chip(self):
        def listing(product):
            return {'result': {'devices': [{'hardwareProperties': {'deviceType': 'appleTV', 'reality': 'physical', 'udid': 'TV', 'productType': product}}]}}
        self.assertEqual(backend.apple_tvs(listing('AppleTV18,1'))[0]['chip'], 'A19')
        self.assertEqual(backend.apple_tvs(listing('AppleTV14,1'))[0]['chip'], 'A15')
        self.assertEqual(backend.apple_tvs(listing('AppleTV99,9'))[0]['chip'], '')   # a model TVPort does not know gets today's defaults
        self.assertEqual(backend.TV_MODELS['AppleTV18,1']['gpu'], 'g18p')
        self.assertEqual(backend.TV_MODELS['AppleTV14,1']['gpu'], '')   # the A15 keeps the MTLDevice path, without an archive
        self.assertEqual(backend.tv_model(None), {})


class GameFiles(unittest.TestCase):
    """install() keeps the TV's romfs.img only when a finished copy left its mark, not on its size alone."""
    def install(self, tv: dict) -> list:
        sent = []
        with tempfile.TemporaryDirectory() as folder:
            app = Path(folder) / 'DmIOS.app'; app.mkdir()
            (app / 'embedded.mobileprovision').write_text('x'); (app / 'romfs.id').write_text('DMROMFSID1 size=4 index=00\n')
            image = Path(folder) / 'RomFS.img'; image.write_bytes(b'1234')
            game = argparse.Namespace(app=app, romfs=image)

            def run(argv, **_):
                argv = [str(a) for a in argv]
                if argv[3:5] == ['copy', 'to'] and argv[-1].endswith('romfs.sent'):
                    tv['romfs.sent'] = Path(argv[argv.index('--source') + 1]).read_text()
                if argv[3:5] == ['copy', 'to'] and argv[-1].endswith('romfs.img'):
                    tv['romfs.img'] = Path(argv[argv.index('--source') + 1]).stat().st_size
                    tv['placeholder'] = True
                if argv[3:5] == ['copy', 'from'] and argv[argv.index('--source') + 1].endswith('romfs.sent') and 'romfs.sent' in tv:
                    Path(argv[-1]).write_text(tv['romfs.sent'])
                return subprocess.CompletedProcess(argv, 0, '', '')

            def stream(argv, **_):
                sent.append(Path(str(argv[argv.index('--source') + 1])).name)
                assert tv.pop('placeholder', False), 'the old image must be replaced first: devicectl skips a file of the same size and date'
                if tv.get('cut'): raise backend.Failure('copy cut off')
                tv['romfs.img'] = 4
            saved = backend.run, backend.stream, backend.installed_files, backend.emit, backend.stage
            backend.run, backend.stream, backend.emit, backend.stage = run, stream, lambda *a, **k: None, lambda *a: None
            backend.installed_files = lambda *_: {k: v for k, v in tv.items() if k == 'romfs.img'}
            try:
                backend.install(game, 'TV', 'b')
            except backend.Failure:
                pass
            finally:
                backend.run, backend.stream, backend.installed_files, backend.emit, backend.stage = saved
        return sent

    def test_full_size_without_mark_is_sent_again(self):
        tv = {'romfs.img': 4}
        self.assertEqual(self.install(tv), ['RomFS.img'])
        self.assertEqual(tv['romfs.sent'], 'DMROMFSID1 size=4 index=00\n')
        self.assertEqual(self.install(tv), [])   # marked now: kept

    def test_a_cut_off_copy_leaves_no_mark(self):
        tv = {'romfs.img': 4, 'romfs.sent': 'DMROMFSID1 size=4 index=00\n', 'cut': True}
        tv['romfs.img'] = 3   # an earlier image damaged: sent again, and that copy stops partway
        self.assertEqual(self.install(tv), ['RomFS.img'])
        self.assertEqual(tv['romfs.sent'], '')
        tv.update({'romfs.img': 4, 'cut': False})   # the cut off copy reached its full size
        self.assertEqual(self.install(tv), ['RomFS.img'])


class GameFolder(unittest.TestCase):
    def make(self, folder, *paths):
        for path in paths:
            target = Path(folder) / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('')

    def test_new_game_is_not_prepared(self):
        with tempfile.TemporaryDirectory() as folder:
            self.make(folder, 'Game.nsp', '._Game.nsp', 'cover.png')   # ._Game.nsp: macOS's AppleDouble file on an exFAT drive
            game = backend.Game(Path(folder))
            self.assertEqual(game.sources, ['Game.nsp'])
            self.assertFalse(game.prepared)

    def test_built_game_is_found(self):
        chain = backend.chain()
        with tempfile.TemporaryDirectory() as folder:
            self.make(folder, '0100000000010000_build/patched_v1/main.patched.bin', '0100000000010000_build/extract/v1/RomFS.img', f'0100000000010000_build/shader/ios/b-{chain}/inputs.tsv')
            game = backend.Game(Path(folder))
            self.assertEqual((game.title_id, game.package), ('0100000000010000', 'v1'))
            self.assertTrue(game.prepared)

    def test_shaders_of_another_chain_do_not_count(self):
        with tempfile.TemporaryDirectory() as folder:
            self.make(folder, '0100000000010000_build/patched_v1/main.patched.bin', '0100000000010000_build/extract/v1/RomFS.img', '0100000000010000_build/shader/ios/b-000000000000/inputs.tsv')
            self.assertFalse(backend.Game(Path(folder)).prepared)

    def test_mods_folder_and_its_digest(self):
        with tempfile.TemporaryDirectory() as folder:
            self.make(folder, 'Game.nsp')
            game = backend.Game(Path(folder))
            self.assertIsNone(game.mods); self.assertEqual(game.mods_digest(), '')
            self.make(folder, 'mods/[1080p]/exefs/a.pchtxt', 'mods/[1080p]/exefs/._a.pchtxt')
            self.assertEqual(game.sources, ['Game.nsp'])   # the mods are not games
            first = game.mods_digest(); self.assertTrue(first)
            (Path(folder) / 'mods/[1080p]/exefs/._a.pchtxt').write_text('AppleDouble')   # macOS's own files do not count
            self.assertEqual(game.mods_digest(), first)
            (Path(folder) / 'mods/[1080p]/exefs/a.pchtxt').write_text('@enabled')
            self.assertNotEqual(game.mods_digest(), first)

    def test_new_bundle_id_comes_from_team_and_title(self):
        with tempfile.TemporaryDirectory() as folder:
            self.make(folder, '0100ABCD00000000_build/x')
            self.assertEqual(backend.Game(Path(folder)).bundle_id('TEAM123'), 'dev.tvport.team123.g0100abcd00000000')


MAIN_BID = 'B424BE150A8E7D78701CBE7A439D9EBF'
CHEATS = '''[Credits: someone]

{Master code}
580F0000 01234567

[--SectionStart:Coins--]
[999 coins]
04000000 00112233 000003E7
[--SectionEnd:Coins--]
[Moon jump] 80000001
04000000 00445566 00000001
20000000
[999 coins]
04000000 00112234 000003E7
'''


class ModsAndCheats(unittest.TestCase):
    """The app's Mods and Cheats sections: which mods apply to the packed version, the cheat file's entries and their switches, and what a build carries"""

    def game(self, folder):
        exefs = Path(folder) / '0100000000010000_build/extract/v393216/ExeFS'
        exefs.mkdir(parents=True)
        (Path(folder) / '0100000000010000_build/patched_v393216').mkdir()
        head = bytearray(0x100); head[:4] = b'NSO0'; head[0x40:0x50] = bytes.fromhex(MAIN_BID)
        (exefs / 'main').write_bytes(bytes(head))
        (exefs / 'main.npdm').write_bytes(b'META')
        return backend.Game(Path(folder))

    def mod(self, folder, name, filename, text):
        d = Path(folder) / 'mods' / name / 'exefs'
        d.mkdir(parents=True, exist_ok=True)
        (d / filename).write_text(text)

    def test_mods_that_apply_and_their_resolution(self):
        with tempfile.TemporaryDirectory() as folder:
            game = self.game(folder)
            self.assertEqual(game.module_builds(), {'main': MAIN_BID + '0' * 32})   # the header's 32 bytes
            self.mod(folder, '[1920x1080 v1.4.1]', '1.4.1.pchtxt', f'@nsobid-{MAIN_BID}\n@enabled\n00000110 AABB\n@stop\n')
            self.mod(folder, '[1920x1080 v1.4.1]', '1.0.0.pchtxt', '@nsobid-3CA12DFAAF9C82DA064D1698DF79CDA1\n')
            self.mod(folder, '-[2560x1440 v1.4.1]', 'a.pchtxt', f'@nsobid-{MAIN_BID}\n')
            self.mod(folder, '[Old]', '3CA12DFAAF9C82DA.ips', '')
            (Path(folder) / 'mods/[Textures]/romfs').mkdir(parents=True)
            mods = {m['name']: m for m in backend.mod_entries(game)}
            self.assertEqual(list(mods), ['[1920x1080 v1.4.1]', '[2560x1440 v1.4.1]', '[Old]', '[Textures]'])
            self.assertEqual((mods['[1920x1080 v1.4.1]']['state'], mods['[1920x1080 v1.4.1]']['resolution']), ('applies', '1920x1080'))
            self.assertFalse(mods['[2560x1440 v1.4.1]']['on'])
            self.assertEqual(mods['[Old]']['state'], 'other-version')
            self.assertEqual(mods['[Textures]']['state'], 'no-code-patches'); self.assertTrue(mods['[Textures]']['romfs'])
            self.assertEqual(backend.mod_resolution(list(mods.values())), '1920x1080')   # the one that is off and the one for another version do not count

    def test_mod_switch_renames_its_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            self.game(folder)
            self.mod(folder, '[LOD]', 'a.pchtxt', f'@nsobid-{MAIN_BID}\n')
            with contextlib.redirect_stdout(io.StringIO()):
                backend.cmd_mod(argparse.Namespace(folder=Path(folder), add=None, name='[LOD]', on=False))
            self.assertTrue((Path(folder) / 'mods/-[LOD]').is_dir())
            with contextlib.redirect_stdout(io.StringIO()):
                backend.cmd_mod(argparse.Namespace(folder=Path(folder), add=None, name='[LOD]', on=True))
            self.assertTrue((Path(folder) / 'mods/[LOD]').is_dir())

    def test_adding_a_downloaded_mods_folder_adds_each_mod(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as download:
            self.game(folder)
            for name in ('[A]', '[B]'):
                (Path(download) / name / 'exefs').mkdir(parents=True)
                (Path(download) / name / 'exefs/x.pchtxt').write_text(f'@nsobid-{MAIN_BID}\n')
            (Path(download) / '[A]/exefs/._x.pchtxt').write_text('AppleDouble')
            with contextlib.redirect_stdout(io.StringIO()):
                backend.cmd_mod(argparse.Namespace(folder=Path(folder), add=Path(download), name=None, on=True))
            self.assertEqual(sorted(p.name for p in (Path(folder) / 'mods').iterdir()), ['[A]', '[B]'])
            self.assertFalse((Path(folder) / 'mods/[A]/exefs/._x.pchtxt').exists())
            with self.assertRaises(backend.Failure):   # the same mod again
                backend.cmd_mod(argparse.Namespace(folder=Path(folder), add=Path(download) / '[A]', name=None, on=True))

    def test_cheat_file_entries(self):
        cheats = backend.read_cheats(CHEATS)
        self.assertEqual(cheats, [('Master code', True), ('999 coins', False), ('Moon jump', False), ('999 coins', False)])   # labels and section markers are not cheats
        self.assertEqual(backend.cheat_keys(cheats), ['Master code', '999 coins', 'Moon jump', '999 coins #2'])

    def test_switches_and_what_a_build_carries(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as download:
            game = self.game(folder)
            self.assertIsNone(backend.stage_cheats(game))
            other = Path(download) / '3CA12DFAAF9C82DA.txt'; other.write_text(CHEATS)
            with self.assertRaises(backend.Failure):   # named after another version
                backend.cmd_cheat(argparse.Namespace(folder=Path(folder), add=other, any_build=False, name=[], on=True))
            mine = Path(download) / 'Odyssey cheats.txt'; mine.write_text(CHEATS)
            with contextlib.redirect_stdout(io.StringIO()):
                backend.cmd_cheat(argparse.Namespace(folder=Path(folder), add=mine, any_build=False, name=[], on=True))
            entries = backend.cheat_entries(game)
            self.assertEqual(entries['file'], str(game.folder / 'cheats/B424BE150A8E7D78.txt'))
            self.assertEqual([c['on'] for c in entries['cheats']], [True, False, False, False])   # the master is always on
            self.assertIsNone(backend.stage_cheats(game))   # nothing on yet: the build carries no cheats
            with contextlib.redirect_stdout(io.StringIO()):
                backend.cmd_cheat(argparse.Namespace(folder=Path(folder), add=None, any_build=False, name=['999 coins #2', 'Moon jump'], on=True))
            self.assertEqual((Path(folder) / 'cheats/B424BE150A8E7D78.enabled').read_text(), 'Moon jump\n999 coins #2\n')
            staged = backend.stage_cheats(game)
            self.assertEqual(sorted(p.name for p in staged.iterdir()), ['B424BE150A8E7D78.enabled', 'B424BE150A8E7D78.txt'])
            with contextlib.redirect_stdout(io.StringIO()):
                backend.cmd_cheat(argparse.Namespace(folder=Path(folder), add=None, any_build=False, name=['Moon jump'], on=False))
            self.assertEqual([c['name'] for c in backend.cheat_entries(game)['cheats'] if c['on'] and not c['master']], ['999 coins #2'])


if __name__ == '__main__':
    unittest.main()
