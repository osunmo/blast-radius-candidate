"""Build a static attribute-read index from the pinned baseline only."""
import argparse
import gzip
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.fine import attribute_reads
from src.selector import BASE


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    source = args.source.resolve()
    revision = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
    if revision != BASE:
        raise ValueError('The setting index requires the pinned SQLGlot checkout')
    args.output.mkdir(exist_ok=True, parents=True)
    content = json.dumps({'base': BASE, 'reads': attribute_reads(source)}, separators=(',', ':')).encode()
    (args.output / 'settings.json.gz').write_bytes(gzip.compress(content, mtime=0))


if __name__ == '__main__':
    main()
