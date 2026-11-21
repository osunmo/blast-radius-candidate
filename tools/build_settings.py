"""Build a static attribute-read index from the pinned baseline only."""
import sys,json,gzip
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.fine import attribute_reads
from src.selector import BASE

if __name__ == '__main__':
    source=Path(sys.argv[1]).resolve()
    output=Path(sys.argv[2])
    output.mkdir(exist_ok=True,parents=True)
    content=json.dumps({'base':BASE,'reads':attribute_reads(source)},separators=(',',':')).encode()
    (output/'settings.json.gz').write_bytes(gzip.compress(content,mtime=0))
