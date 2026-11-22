"""Translate immutable baseline dependency masks to an input test catalog."""
from functools import lru_cache


class CatalogRemapper:
    def __init__(self, baseline, tests):
        ids = [row['id'] for row in tests]
        numbers = [row['number'] for row in tests]
        if (any(type(node) is not str for node in (*baseline, *ids))
                or any(type(number) is not int for number in numbers)):
            raise ValueError('invalid catalog ID or number type')
        if len(baseline) != len(set(baseline)) or len(ids) != len(set(ids)):
            raise ValueError('duplicate catalog IDs')
        if sorted(numbers) != list(range(len(tests))):
            raise ValueError('noncontiguous input test numbers')
        current = {row['id']:row['number'] for row in tests}
        self.baseline = tuple(baseline)
        self.baseline_mask = (1 << len(baseline))-1
        self.destinations = tuple(current.get(node) for node in baseline)
        known = set(baseline)
        self.unknown = sum(1 << row['number'] for row in tests if row['id'] not in known)
        self.identity = self.unknown == 0 and self.destinations == tuple(range(len(tests)))

    def validate_catalog(self, catalog):
        if tuple(catalog) != self.baseline:
            raise ValueError('supplemental baseline catalog differs')

    @lru_cache(maxsize=32768)
    def bits(self, value):
        """Decode baseline hexadecimal bits; unknown input tests are separate."""
        source = int(value, 16)
        if source < 0 or source & ~self.baseline_mask:
            raise ValueError('out-of-baseline-catalog bits')
        if self.identity:
            return source
        result = 0
        while source:
            bit = source & -source
            target = self.destinations[bit.bit_length()-1]
            if target is not None:
                result |= 1 << target
            source ^= bit
        return result
