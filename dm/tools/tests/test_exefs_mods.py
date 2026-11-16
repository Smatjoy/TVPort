#!/usr/bin/env python3
"""dm/tools/exefs_mods.py: the IPSwitch text and IPS formats, the build ID match, mod folders that are off, conflicts, and the patched module written as an NSO that nso.py reads back"""
import contextlib, io, sys, tempfile, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import exefs_mods as em
import nso

BID = bytes.fromhex('B424BE150A8E7D78701CBE7A439D9EBF') + bytes(16)
OTHER = bytes.fromhex('FCF93A41B08C5193A1410D262602DC62') + bytes(16)


def module(build_id=BID):
    img = bytearray(0x2800)   # the image ends with the data segment, as nso.load makes it
    img[0:0x1000] = bytes(range(256)) * 16
    return nso.Module(img, ((0, 0x1000), (0x1000, 0x1000), (0x2000, 0x800)), 0x400, build_id)


PCHTXT = '''@nsobid-B424BE150A8E7D78701CBE7A439D9EBF

# a comment
@flag print_values
@flag offset_shift 0x100

// a block
@enabled
00000010 09F080D2 // mov
00000014 "Hi\\n"
@stop

@disabled
00000020 FFFFFFFF
@stop

@author
'''


class ExefsMods(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def mod(self, name, filename, body):
        d = self.root / name / 'exefs'; d.mkdir(parents=True, exist_ok=True)
        (d / filename).write_bytes(body if isinstance(body, bytes) else body.encode())

    def test_pchtxt_blocks_strings_and_shift(self):
        bid, ps = em.read_pchtxt(PCHTXT, 'x.pchtxt')
        self.assertEqual(bid, 'B424BE150A8E7D78701CBE7A439D9EBF')
        self.assertEqual([(p.offset, p.data) for p in ps], [(0x10, bytes.fromhex('09F080D2')), (0x14, b'Hi\n')])   # the @disabled block is skipped
        _, ps = em.read_pchtxt('@nsobid-B424BE150A8E7D78\n@enabled\n00000110 AABB\n', 'y')   # no offset_shift: offsets count the NSO header
        self.assertEqual(ps[0].offset, 0x10)
        with self.assertRaises(em.Error): em.read_pchtxt('@enabled\n0010 XYZ\n', 'z')

    def test_ips_and_ips32(self):
        ips = b'PATCH' + (0x110).to_bytes(3, 'big') + b'\x00\x02' + b'\xAA\xBB' + (0x120).to_bytes(3, 'big') + b'\x00\x00' + b'\x00\x03' + b'\xCC' + b'EOF'
        self.assertEqual([(p.offset, p.data) for p in em.read_ips(ips, 'a.ips')], [(0x10, b'\xAA\xBB'), (0x20, b'\xCC\xCC\xCC')])
        ips32 = b'IPS32' + (0x200).to_bytes(4, 'big') + b'\x00\x01' + b'\x11' + b'EEOF'
        self.assertEqual([(p.offset, p.data) for p in em.read_ips(ips32, 'b.ips')], [(0x100, b'\x11')])
        with self.assertRaises(em.Error): em.read_ips(b'PATCH' + (0x10).to_bytes(3, 'big') + b'\x00\x01\x00EOF', 'header.ips')   # writes the NSO header
        with self.assertRaises(em.Error): em.read_ips(b'PATCH\x00\x01', 'short.ips')

    def test_build_id_match(self):
        self.assertTrue(em.matches('B424BE150A8E7D78', BID))
        self.assertTrue(em.matches('B424BE150A8E7D78701CBE7A439D9EBF0000000000000000', BID))
        self.assertFalse(em.matches('B424BE15', BID))   # too short to name a build
        self.assertFalse(em.matches('FCF93A41B08C5193', BID))

    def test_selects_this_build_and_skips_mods_that_are_off(self):
        self.mod('[Res]', 'patch.pchtxt', PCHTXT)
        self.mod('[Other build]', 'patch.pchtxt', PCHTXT.replace('B424BE150A8E7D78701CBE7A439D9EBF', 'FCF93A41B08C5193A1410D262602DC62'))
        self.mod('-[Off]', 'patch.pchtxt', PCHTXT)
        self.mod('[Ips]', 'B424BE150A8E7D78.ips', b'IPS32' + (0x1100).to_bytes(4, 'big') + b'\x00\x01\x7F' + b'EEOF')
        (self.root / '[Res]' / 'exefs' / '._patch.pchtxt').write_bytes(b'\x00\x05\x16\x07')   # AppleDouble
        found = em.patches_for(self.root, BID)
        self.assertEqual([n for n, _, _ in found], ['[Ips]', '[Res]'])
        self.assertEqual([n for n, _, _ in em.patches_for(self.root, OTHER)], ['[Other build]'])

    def test_apply_writes_a_module_that_reads_back(self):
        self.mod('[Res]', 'patch.pchtxt', PCHTXT)
        m = module(); found = em.patches_for(self.root, BID)
        em.apply(m, found)
        out = self.root / 'main'; em.write_nso(m, out)
        back = nso.read(out)
        self.assertEqual(back.image, m.image); self.assertEqual(back.segments, m.segments); self.assertEqual(back.bss, 0x400); self.assertEqual(back.build_id, BID)
        self.assertEqual(bytes(back.image[0x10:0x17]), bytes.fromhex('09F080D2') + b'Hi\n')

    def test_outside_segments_and_conflicts_are_errors(self):
        m = module()
        with self.assertRaises(em.Error): em.apply(m, [('[Cave]', None, [em.Patch(0x2800, b'\x00' * 4, 'cave')])])   # in bss, past data
        m = module()
        with self.assertRaises(em.Error): em.apply(m, [('[A]', None, [em.Patch(0x10, b'\x01\x02', 'a')]), ('[B]', None, [em.Patch(0x11, b'\x09', 'b')])])
        m = module()
        em.apply(m, [('[A]', None, [em.Patch(0x10, b'\x01\x02', 'a')]), ('[B]', None, [em.Patch(0x11, b'\x02', 'b')])])   # the same bytes: no conflict
        self.assertEqual(bytes(m.image[0x10:0x12]), b'\x01\x02')

    def test_cli_writes_nothing_without_a_matching_mod(self):
        src = self.root / 'in'; em.write_nso(module(OTHER), src)
        self.mod('[Res]', 'patch.pchtxt', PCHTXT)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(em.main([str(self.root), str(src), str(self.root / 'out')]), 0)
        self.assertFalse((self.root / 'out').exists()); self.assertIn('no mod names build FCF93A41B08C5193', out.getvalue())
        src2 = self.root / 'in2'; em.write_nso(module(), src2)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(em.main([str(self.root), str(src2), str(self.root / 'out')]), 0)
        self.assertEqual(bytes(nso.read(self.root / 'out').image[0x10:0x14]), bytes.fromhex('09F080D2'))


if __name__ == '__main__':
    unittest.main()
