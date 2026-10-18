import json
from pathlib import Path
import tempfile
import unittest

from serve_map import build_map


class MapBuildTests(unittest.TestCase):
    def test_build_preserves_exports_and_escapes_embedded_retailer_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'gasbuddy').mkdir()
            export = '{"stations": [], "collected_at": "2026-10-18T15:00:00Z"}'
            (root / 'gasbuddy' / 'stations.json').write_text(export)
            retailers = {'stations': [], 'coverage': '</script><script>alert(1)</script>'}
            (root / 'stations.json').write_text(json.dumps(retailers))
            build_map(root)
            self.assertEqual((root / 'gasbuddy' / 'stations.json').read_text(), export)
            html = (root / 'index.html').read_text()
            self.assertNotIn(retailers['coverage'], html)
            self.assertNotIn('__ROUTE_DATA__', html)
            for asset in ('map.js', 'data.js', 'style.css'):
                self.assertTrue((root / asset).is_file())

    def test_gasbuddy_only_build_works_without_a_retailer_export(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build_map(root)
            self.assertIn('type="application/json">null</script>', (root / 'index.html').read_text())


if __name__ == '__main__':
    unittest.main()
