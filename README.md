# Blast Radius

A batch regression-test selector for the JoinBuilders challenge. The image reads `/data/tests.json` and `/data/commits/*.patch`, then emits one JSON work assignment per commit. It analyzes each patch against SQLGlot `eb11aec4be8cb2122afe713a4816662c3f0fb2fa`. All test execution used to construct the dependency map happens offline.

## Run the candidate

Use Docker with BuildKit. Set `KIT` to an absolute path containing the extracted official starter.

```sh
docker build --platform linux/amd64 -t blast-radius-local .
python tools/benchmark.py --kit "$KIT" --image blast-radius-local --mode hybrid --save ../sample-results
python -m unittest discover -s tests
```

The benchmark starts three containers with the official memory, CPU, PID, filesystem, identity and network restrictions. It mounts just the patches and catalog in each. After each exit it feeds stdout to the kit's unmodified scorer and writes per-run metrics and predictions to the requested output directory. Its Linux cgroup v2 CPU reading includes a small profiling shell and precedes final container shutdown. The competition measures through exit.

For a native debugging run, use a clean checkout of the pinned upstream revision. Explanations go to stderr.

```sh
python -B -m src.main --source "$SQLGLOT" --data "$KIT/sample" --explain > ../predictions.jsonl
python "$KIT/score.py" "$KIT/sample" "$KIT/sample/answers.json" ../predictions.jsonl
```

## How selection works

1. The profiler matches pytest items to whole catalog node IDs. It tracks lines across setup, call and teardown, broadens shared fixture contexts, records contextless execution separately, and audits reads of baseline fixture and source files. Each source line stores its test set as an integer bitset. The gzip-compressed map loads once per batch.
2. At inference, the selector checks the pinned map and ordered catalog, validates hunk counts, coordinates and context, and reconstructs changed source in memory. It parses baseline and patched syntax and compiles changed Python without importing it. A supported local edit can select tests associated with an old statement or enclosing function. Added statements use the enclosing scope. Fixture edits use observed readers when available.
3. Discovery, ambiguous definitions, changes to initialization, invalid source, unsupported patch metadata and unknown dependencies can select the entire catalog. Every result includes tests flagged as opaque by the profiler. Dynamic selection depends on the behavior exercised on the baseline, including its Python version, data and execution order. A change to cached state or an unobserved external observer remains a source of miss risk.
4. Scheduling uses the catalog's integer milliseconds. Longest jobs go first onto the lightest worker. The scheduler considers counts from one through eight, skipping a trial only if its lower bound cannot beat the best known cost. Schedules are cached by selected test set. `--refine` enables an experimental bounded pass of moves and swaps.

## Rebuild the dependency map

The production image uses only the Python standard library. The separate profiling image uses the starter's exact Linux base image and package lock. From the submission root, set `SCRATCH` to an empty, absolute directory outside the submission. The two profiling passes run in separate fresh containers. Allow several minutes for each and additional time for the initial image download.

```sh
git init "$SCRATCH/sqlglot"
git -C "$SCRATCH/sqlglot" fetch --no-tags --no-recurse-submodules --depth=1 https://github.com/tobymao/sqlglot.git eb11aec4be8cb2122afe713a4816662c3f0fb2fa
git -C "$SCRATCH/sqlglot" checkout --detach FETCH_HEAD
git -C "$SCRATCH/sqlglot" archive --format=tar --output="$SCRATCH/pinned.tar" HEAD
docker build --platform linux/amd64 -f tools/profile.Dockerfile -t blast-radius-map-env "$KIT"
for tool in build_maps refine_children; do
  docker run --rm --platform linux/amd64 --network none --cpus 4 --memory 8g --pids-limit 256 \
    -e PYTHON_CPU_COUNT=2 -e OPENBLAS_NUM_THREADS=1 \
    -v "$SCRATCH/pinned.tar:/input/pinned.tar:ro" -v "$KIT/sample/tests.json:/data/tests.json:ro" \
    -v "$PWD/tools:/tools:ro" -v "$PWD/artifacts:/artifacts" blast-radius-map-env \
    python -u "/tools/$tool.py" --source /tmp/sqlglot --archive /input/pinned.tar \
    --catalog /data/tests.json --out /artifacts --compare-encodings || exit 1
done
python tools/build_observers.py "$SCRATCH/sqlglot" artifacts
python3.13 tools/build_scopes.py "$SCRATCH/sqlglot" artifacts
```

The generator checks the revision embedded in the Git archive. It records map provenance, test collection, unresolved dependencies and package versions in `artifacts/manifest.json`. The second pass records subprocess dependencies and baseline imports. The static indexes record which test modules inspect program metadata, which routines participate in shared state, and the source locations of functions and statements. The optional `indexed` mode checks the interpreter tag and source hashes before using the stored locations.

## Handoff

The safe all-tests version is Git commit `a31d859baa18f1d058f36a47c6434472b468b5d9`. Select `--mode all` to use its scheduling strategy with any later entry.

To enter the competition, the repository owner can push the chosen commit and its history to the team's public GitHub repository, then enter the repository URL and full commit SHA on the track page. The entry builds from the root Dockerfile. The pinned source retains SQLGlot's MIT license.

The measurements and validation limits are in [EXPERIMENTS.md](EXPERIMENTS.md). The design references and their license information are in [RESEARCH.md](RESEARCH.md).
