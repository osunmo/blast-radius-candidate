# Experiments

The official kit supplied for this run contains 30 independent patches, 1,620 numbered tests and one skipped test without a number. Across the 30 patches the labels contain 73 failing test instances. All parsing, dependency collection and scheduling use the baseline source and catalog. The evaluation harness reads labels after the inference subprocess exits.

## Baselines

The published reference in the brief is 1,614.1 seconds. The unchanged supplied kit's `make sample` reports **1,210.470 simulated seconds and zero misses**. The kit's bundled scoring guide and the separately supplied copy have the same SHA-256, `26a9941ca2980d8da00bea00910a29703efac95ec79e30a15ab3482e3a9c0903`. The experiment compares against the result reproduced from this kit.

Running all tests costs 103.047 recorded seconds before parallelism. The longest individual test takes 28.817 seconds. On four workers LPT reaches loads of 28.817, 24.744, 24.743 and 24.743 seconds, so the cost per patch is 36.817 seconds. Every other worker count has a larger lower bound from the longest job or the average load plus worker overhead. Thus **1,104.510 seconds is the minimum simulated score for running this entire catalog on all 30 patches**.

The scheduler also passed 2,000 seeded synthetic job sets, including zero times and equal durations. Refinement improved 79 partitions by 4.963 simulated seconds in aggregate. On the all-tests sample it saved zero simulated seconds. The choice for the shipped selector uses measured net cost.

## Reading the measurements

Selected instances count repetitions across commits. Test-level recall is covered failing instances divided by 73. Precision is covered failing instances divided by all selected instances. A commit incurs the 300-second penalty if any failing instance is absent. The simulated column includes penalties. Local totals add measured CPU to that exact scorer output. Docker wall times include container create, startup and removal. The benchmark reads Linux cgroup v2 counters immediately before exit. A server result can differ with machine speed and its precise accounting endpoint.
| Run | Selected test instances | Failures covered | Missed commits | Simulated s | CPU s at median | All local totals, s | Median total, s | All wall times, s |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | --- |
| docker-starter | 48,600 | 73/73 | 0 | 1210.470 | 1.758 | 1213.644, 1212.228, 1211.669 | 1212.228 | 40.38, 13.04, 6.80 |
| docker-all | 48,600 | 73/73 | 0 | 1104.510 | 4.202 | 1111.644, 1108.712, 1108.153 | 1108.712 | 23.11, 33.65, 37.41 |
| win-first-file | 33,569 | 73/73 | 0 | 990.880 | 1.891 | 992.771 | 992.771 | 4.14 |
| win-first-block | 31,353 | 73/73 | 0 | 979.517 | 1.969 | 981.486 | 981.486 | 4.36 |
| win-first-line | 31,353 | 73/73 | 0 | 979.517 | 1.969 | 981.486 | 981.486 | 5.06 |
| win-first-hybrid | 18,606 | 73/73 | 0 | 888.613 | 2.078 | 890.691 | 890.691 | 5.40 |
| final | 18,516 | 73/73 | 0 | 665.107 | 5.196 | 673.070, 669.510, 670.303 | 670.303 | 12.20, 7.67, 8.37 |

Final Linux baseline passed 1620 tests and 25440 subtests in 2206.71 seconds. Child refinement verified 24 of 25 guarded tests. All 27 unit tests pass. Default hybrid scored 665.107 simulated seconds with zero misses and 73/73 failing test instances covered. Three constrained Docker totals were 673.070086, 669.509680 and 670.302681 seconds. Median is 670.302681 seconds (670.3 rounded). Selector wall times were 7.45, 4.32 and 5.33 seconds. Peak memory was 72069120 bytes, peak PID count 6, stdout 81278 bytes.

Build used a clean local checkout, four-core and 8 GB build settings. Runs used the official two-core, 4 GB, no-network, nobody, read-only, 64 MB tmpfs and 256-PID limits. CPU counters include a small profiling shell. The optional indexed mode selected all tests in the final native matrix, so hybrid remains the default. Verified mode matched selections with higher native CPU.

The independent 45-patch stress check finished in 15.47 selector seconds, 14.7011 CPU seconds, 168648704 peak bytes and 82177 output bytes. Format checks passed. The final Linux-map selector covered all four observed failures in three independent holdouts against a fixed 50-test Windows scope, selecting 31, 23 and 87 tests respectively.

Reproduce with python -m unittest discover -s tests, then docker build --platform linux/amd64 --resource cpu-quota=400000 --resource memory=8g -t blast-radius-final-local . and python tools/benchmark.py --kit KIT_PATH --image blast-radius-final-local --mode hybrid --save ../results/final.

Remaining limitations include dynamic callbacks, aliased shared state, warm caches, native file reads and environment-dependent behavior outside baseline observations. Hidden-set soundness and the server score remain unverified. Offline Linux profiling took over 36 minutes locally, so the committed maps are needed for a quick competition build.

## Finer dependency candidate

The results above describe the original hybrid submission. The new default is `fine`. It reduces sample simulated seconds by 50.6% relative to the original candidate on the same supplied sample. Its server score is unmeasured.

| Experiment | Simulated seconds | Missed commits | Notes |
| --- | ---: | ---: | --- |
| Original hybrid | 665.107 | 0 | Published baseline |
| Lambda expressions and text observers | 499.208 | 0 | Separate lambda bodies from dispatch initialization |
| Literal-setting exploratory version | 470.767 | 0 | Rejected after adding dynamic-lookup and initialization guards |
| Corrected audit-wrapper attribution | 367.421 | 0 | Loader reads excluded through the replay subclass |
| Exact parent fixture contexts | 328.456 | 0 | Keep parent reads separate from shared worker lifetime unions |

The source-audit bug was reproduced with a collector subclass. The base audit callback saw the replay wrapper instead of the loader and recorded imported source as a text dependency. `tools/test_audit.py` verifies exclusion of loader reads through the subclass and retention of actual source reads. It passed on the exact Linux profiling interpreter and on the local Python 3.13 interpreter. The profiler also retains module/class initialization separately from optimized function execution. Parent-side fixture reads retain their item contexts, while verified shared worker dependencies retain their conservative pool union.

The final replay passed all 25 targeted tests and 2586 subtests. Its independent workload census passed 22 tests and 2529 subtests. Complete child coverage was verified for 24 guarded tests. The map covers all 1620 catalog IDs with remaining uncertainty accounted for. Its SHA-256 is `59a6f80f1aa3ab2bf33f11bb072afbb0ba5b95d9390dbf0206e153b01416762d`. It has 243 source files, 143 recorded reads and 624473 compressed bytes. The setting-read index reproduced byte for byte with SHA-256 `d710ba1376e68bfce22cef2edab6195a9db8c05e6ad0d86bd5f3fb95b91d0916`.

Runtime source commit `697f00a` was built from a clean local clone with the official pinned image and upstream checkout. The preceding clean build took 22.587 seconds. The final build reused cached upstream layers. All three final runs used two cores, 4 GB, no network, user nobody, HOME=/tmp, read-only root, 64 MB temporary storage and a 256-process limit.

| Run | Simulated seconds | CPU seconds | Local total seconds | Selector wall seconds |
| --- | ---: | ---: | ---: | ---: |
| 1 | 328.456 | 8.014455 | 336.470455 | 7.95 |
| 2 | 328.456 | 5.627497 | 334.083497 | 5.67 |
| 3 | 328.456 | 7.665373 | 336.121373 | 7.97 |

The median local total is **336.121373 seconds**, rounding to **336.1 seconds**. Each run covered all 73 failing test instances with zero missed commits. Selected instances fell from 18516 to 6972. Each output was 31258 bytes. Maximum memory was 76574720 bytes and maximum PID count was 7. The CPU measurement includes the small profiling shell and is taken immediately before container shutdown.

All 31 implementation tests pass. The three independent holdout mutations still cover all four observed failing instances within their fixed 50-test Windows validation scope. Selected counts fell to 7, 2 and 85. The 45-patch stress batch passed output-format checks in 23.03 selector seconds, 21.808749 CPU seconds and 177799168 peak bytes. Its output was 80187 bytes. Ground truth was not collected for the stress batch.

Reproduce the final measurement with `python tools/benchmark.py --kit KIT_PATH --image blast-radius-fine-local --mode fine --save ../results/fine`. Build that image from the repository root with the same Docker build command used above. Rebuild the setting-read index with `python tools/build_settings.py PINNED_SQLGLOT_PATH artifacts` and run `python3.13 tools/test_audit.py` inside the profiling environment. No sample labels or prediction files are stored in the submission.

At that stage four sample patches still used the full-suite fallback. The next candidate below replaces those fallbacks with baseline class dependencies. Arbitrary initialization, dynamic reflection, native readers, shared-state effects and source-position observers outside baseline observations remain limitations.

## Class and operand dependencies: final candidate

Two additional independent class-setting mutations were checked against a fixed 181-test Windows scope. Its baseline passed 181 tests and 2837 subtests. The Snowflake TRY_CAST constraint mutation produced one failing test, which was selected; the TSQL typed-division mutation produced no failures in that scope. Both selected fewer than the full catalog (373 and 193 tests respectively). These checks do not establish recall outside that scope.

The class profiler passed all 1620 tests and 25440 subtests on the pinned Linux environment in 562.34 seconds. The independent operand profiler passed the same suite in 988.07 seconds. Worker replay passed 25 tests and 2586 subtests in 325.65 seconds and verified 24 guarded contexts; the unresolved lazy-load context remains included. Neither profiler receives sample patches or answers. The maps include source references and copied initialization dependencies, with conservative unions for shared workers.

| Refinement | Simulated seconds | Missed commits |
| --- | ---: | ---: |
| Previous published candidate | 328.456 | 0 |
| Expression entry anchors | 325.558 | 0 |
| NoneType identity slots | 302.771 | 0 |
| Class maps with broad reference propagation | 220.402 | 0 |
| Distinguish class identity references from field reads | 184.875 | 0 |
| Canonical finite enum domains | 161.800 | 0 |

All four former full-suite cases now use narrower selections: the tokenizer setting selects 287 tests, the dialect offset 193, the parser factory argument 34, and the expression argument constraint 5. The final output selects 728 test instances across 30 commits and covers all 73 labeled failing instances.

The final image was built with the pinned root Dockerfile and four-core, 8 GB build settings. All three measurements used two cores, 4 GB, no network, nobody, read-only root, HOME=/tmp, 64 MB tmpfs and a 256-PID limit. The original scorer was unchanged.

| Run | Simulated seconds | CPU seconds | Local total seconds | Selector wall seconds |
| --- | ---: | ---: | ---: | ---: |
| 1 | 161.800 | 17.042418 | 178.842418 | 17.31 |
| 2 | 161.800 | 21.071526 | 182.871526 | 21.20 |
| 3 | 161.800 | 16.940031 | 178.740031 | 16.95 |

The median local total is **178.842418 seconds**, rounding to **178.8 seconds**, with zero missed commits in every run. This is 46.8% below the preceding published local median of 336.121373. The displayed leader in the supplied screenshot scored 138.1 seconds; this candidate's local median remains 40.742418 seconds above that result, and the machines and accounting endpoints may differ. The competition server score is unmeasured.

Peak memory was 122851328 bytes, peak PID count 6, and stdout 4167 bytes. The independent 45-patch stress batch passed schema checks in 21.11 selector seconds, 19.254281 CPU seconds and 179773440 peak bytes, with 80187 output bytes. No failure truth was collected for that stress batch. All 46 implementation tests and three exact-Linux profiling guard tests passed. The previous three independent holdouts still selected all four observed failing instances within the fixed 50-test Windows scope.

Reproduce the final three runs with `python tools/benchmark.py --kit KIT_PATH --image blast-radius-next-local --mode fine --save ../results/class-final`. Rebuild the supplemental baseline artifacts using the pinned profiling image: run `build_classes.py` and `build_operands.py` in separate fresh containers, then `refine_classes.py` in another, adding static references with `refine_class_refs.py`. Merge the parent and verified-worker maps with `merge_consumers.py`, propagate initialization dependencies with `refine_class_links.py`, and generate `build_type_domains.py` in a fresh container. Each tool provides `--help`; mount the whole repository at `/submission` and the catalog separately. The final class/operand hashes and worker verification list are recorded in `artifacts/consumers-manifest.json`. Regenerate its hashes after class-link propagation. The enum-domain map reproduced byte for byte on Linux, with SHA-256 `e211ce047ae588b5857f3ceb4feac232d1fd286c4b4eef8cd3869f9e5bea76de`.

These dependencies describe baseline behavior, not a general proof of hidden-set soundness. New reflective or native constructor bypasses, class metadata observers, dynamic callbacks and cache effects outside the measured baseline can invalidate an inferred dependency. Finite enum equivalence relies on canonical construction and stable builtin equality; known mutation contexts are retained, direct constructor bypasses disable the proof, and multi-file patches disable operand proofs. The isolated import check detects initialization failures but does not establish equivalence of every initialization side effect. Hidden-set recall and the competition server score remain unverified.
