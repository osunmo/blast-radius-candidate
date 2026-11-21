import unittest

from src.scheduler import Scheduler


class SchedulingTests(unittest.TestCase):
    def test_empty_and_zero_duration(self):
        scheduler = Scheduler({0: 0, 1: 0})
        self.assertEqual(scheduler.schedule(set()), [])
        self.assertEqual(scheduler.schedule({1, 0}), [[0, 1]])

    def test_worker_overhead_can_favor_serial_work(self):
        self.assertEqual(Scheduler({3: 200, 4: 300}).schedule({3, 4}), [[4, 3]])

    def test_long_work_parallelizes_and_preserves_ids(self):
        durations = {i: 5000 for i in range(8)}
        scheduler = Scheduler(durations)
        self.assertEqual(scheduler.schedule(set(durations)), [[0, 4], [1, 5], [2, 6], [3, 7]])

    def test_refinement_improves_uneven_partition(self):
        durations = dict(enumerate([3600, 3150, 2700, 2250, 1800]))
        def cost(workers):
            return max(sum(durations[i] for i in worker) for worker in workers) + 2000 * len(workers)
        plain = Scheduler(durations).schedule(durations)
        refined = Scheduler(durations, refine=True).schedule(durations)
        self.assertLess(cost(refined), cost(plain))
        self.assertCountEqual(sum(refined, []), durations)

    def test_unknown_id_rejected(self):
        with self.assertRaises(KeyError):
            Scheduler({0: 10}).schedule({0, 5})
