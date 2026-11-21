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

Remaining limitations include dynamic callbacks, aliased shared state, warm caches, native file reads and environment-dependent behavior outside baseline observations. Hidden-set soundness and the server score remain unverified. Offline Linux profiling took over 36 minutes locally, so the committed maps are needed for a quick competition build. Publication and competition submission remain owner actions.
