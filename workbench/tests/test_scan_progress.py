import unittest
from workbench.sdpc.progress import scan_progress


class ScanProgressTests(unittest.TestCase):
    def test_resume_uses_only_new_tiles_for_rate(self):
        p = scan_progress(1100, 2000, 1000, 20)
        self.assertEqual(p['tiles_per_second'], 5)
        self.assertEqual(p['estimated_remaining_seconds'], 180)

    def test_startup_has_no_premature_estimate(self):
        self.assertIsNone(scan_progress(0, 100, 0, 0)['estimated_remaining_seconds'])
        self.assertIsNone(scan_progress(10, 100, 0, 2)['estimated_remaining_seconds'])

    def test_completion_is_zero_remaining(self):
        self.assertEqual(scan_progress(100, 100, 0, 20)['estimated_remaining_seconds'], 0)
