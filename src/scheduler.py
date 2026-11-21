"""Balance recorded integer milliseconds and pay only for occupied workers."""

from bisect import bisect_left


class Scheduler:
    def __init__(self, durations, refine=False):
        self.durations = durations
        self.refine = refine
        self.cache = {}

    def schedule(self, selected):
        key = frozenset(selected)
        if not key:
            return []
        if key in self.cache:
            return self.cache[key]
        jobs = sorted(key, key=lambda job: (-self.durations[job], job))
        total = sum(self.durations[job] for job in jobs)
        longest = self.durations[jobs[0]]
        best = [jobs]
        best_cost = total + 2000
        for count in range(2, min(8, len(jobs)) + 1):
            lower_bound = max(longest, (total + count - 1) // count)
            if lower_bound + 2000 * count >= best_cost:
                continue
            workers = [[] for _ in range(count)]
            loads = [0] * count
            for job in jobs:
                index = loads.index(min(loads))
                workers[index].append(job)
                loads[index] += self.durations[job]
            if self.refine and max(loads) > lower_bound:
                self._improve(workers, loads, lower_bound)
            workers = [worker for worker in workers if worker]
            cost = max(loads) + 2000 * len(workers)
            if cost < best_cost:
                best_cost, best = cost, workers
        self.cache[key] = best
        return best

    def _improve(self, workers, loads, lower_bound):
        # Transfer a job, or exchange two, between the busiest and idlest.
        # Local improvement is optional so its CPU cost can be benchmarked.
        for _ in range(3):
            high = loads.index(max(loads))
            low = loads.index(min(loads))
            difference = loads[high] - loads[low]
            if loads[high] <= lower_bound or difference <= 1:
                break
            light = sorted((self.durations[j], j) for j in workers[low])
            light_sizes = [size for size, _ in light]
            best_gap = difference
            exchange = None
            for job in workers[high]:
                size = self.durations[job]
                gap = abs(difference - 2 * size)
                if gap < best_gap:
                    best_gap, exchange = gap, (job, None, size)
                index = bisect_left(light_sizes, size - difference / 2)
                for other_size, other in light[max(0, index - 1):index + 1]:
                    delta = size - other_size
                    gap = abs(difference - 2 * delta)
                    if gap < best_gap:
                        best_gap, exchange = gap, (job, other, delta)
            if exchange is None:
                break
            job, other, delta = exchange
            workers[high].remove(job)
            workers[low].append(job)
            if other is not None:
                workers[low].remove(other)
                workers[high].append(other)
            loads[high] -= delta
            loads[low] += delta
