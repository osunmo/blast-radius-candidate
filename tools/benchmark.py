"""Score saved predictions with the unmodified starter scorer, outside inference."""

import argparse
import ctypes
import importlib.util
import json
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def measure_host(cmd):
    if sys.platform != "win32":
        import resource
        before = resource.getrusage(resource.RUSAGE_CHILDREN)
    start = time.perf_counter()
    process = subprocess.Popen(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, diagnostics = process.communicate()
    wall = time.perf_counter() - start
    if process.returncode:
        raise RuntimeError(diagnostics.decode(errors="replace"))
    if sys.platform == "win32":
        times = [ctypes.c_uint64() for _ in range(4)]
        ok = ctypes.windll.kernel32.GetProcessTimes(
            ctypes.c_void_p(int(process._handle)), *(ctypes.byref(t) for t in times)
        )
        if not ok:
            raise ctypes.WinError()
        cpu = (times[2].value + times[3].value) / 10_000_000
    else:
        after = resource.getrusage(resource.RUSAGE_CHILDREN)
        cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
    return output, diagnostics, cpu, wall


def docker_command(image, data, starter, mode, refine):
    # A shell reads kernel counters after the selector exits. Include the
    # shell in the measured cgroup. The official entry point needs no shell.
    args = ["python", "/app/selector.py"] if starter else ["python", "-m", "src.main", "--mode", mode]
    if refine and not starter:
        args.append("--refine")
    return [
        "docker", "run", "--rm", "--platform", "linux/amd64", "--network", "none",
        "--cpus", "2", "--memory", "4096m", "--memory-swap", "4096m", "--pids-limit", "256",
        "--read-only", "--tmpfs", "/tmp:rw,size=64m,mode=1777", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--user", "65534:65534", "-e", "HOME=/tmp",
        "-v", f"{data / 'tests.json'}:/data/tests.json:ro",
        "-v", f"{data / 'commits'}:/data/commits:ro", image, "sh", "-ec",
        'read begin idle < /proc/uptime; "$@"; read end idle < /proc/uptime; '
        'printf "selector_window %s %s\\n" "$begin" "$end" >&2; '
        'cat /sys/fs/cgroup/cpu.stat /sys/fs/cgroup/memory.peak /sys/fs/cgroup/pids.peak >&2',
        "profile", *args,
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kit", required=True, type=Path, help="extracted official starter")
    parser.add_argument("--source", type=Path, default=Path("/opt/sqlglot"))
    parser.add_argument("--artifacts", type=Path, help="override dependency maps for native comparisons")
    parser.add_argument("--mode", choices=("starter", "all", "file", "block", "line", "hybrid", "verified", "indexed", "fine"), default="hybrid")
    parser.add_argument("--image", help="profile a built image on Linux (requires cgroup v2)")
    parser.add_argument("--refine", action="store_true")
    parser.add_argument("--save", required=True, type=Path, help="directory outside the submission for results")
    parser.add_argument("--runs", type=int, default=3)
    opts = parser.parse_args()
    kit = opts.kit.resolve()
    data = kit / "sample"
    opts.save.mkdir(exist_ok=True, parents=True)
    if opts.runs < 1:
        parser.error("--runs must be positive")
    if opts.image:
        if opts.artifacts:
            parser.error("--artifacts applies only to native runs")
        cmd = docker_command(opts.image, data, opts.mode == "starter", opts.mode, opts.refine)
    elif opts.mode == "starter":
        # Execute the original Python file, remapping only its /data mount.
        cmd = [sys.executable, "-c",
               "import runpy,sys,pathlib; m=runpy.run_path(sys.argv[1]); f=m['main']; "
               "f.__globals__['DATA']=pathlib.Path(sys.argv[2]); f()", str(kit / "selector.py"), str(data)]
    else:
        cmd = [sys.executable, "-B", "-m", "src.main", "--data", str(data),
               "--source", str(opts.source.resolve()), "--mode", opts.mode]
        if opts.artifacts:
            cmd.extend(("--artifacts", str(opts.artifacts.resolve())))
        if opts.refine:
            cmd.append("--refine")
    spec = importlib.util.spec_from_file_location("official_score", kit / "score.py")
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)
    reports = []
    for run in range(1, opts.runs + 1):
        output, diagnostics, cpu, wall = measure_host(cmd)
        peak_bytes = peak_pids = runtime_wall = None
        if opts.image:
            stats = re.search(rb"(?m)^usage_usec (\d+)\r?$", diagnostics)
            if not stats:
                raise RuntimeError("missing cgroup CPU counter")
            cpu = int(stats[1]) / 1_000_000
            peak_bytes, peak_pids = map(int, diagnostics.splitlines()[-2:])
            interval = re.search(rb"(?m)^selector_window ([\d.]+) ([\d.]+)\r?$", diagnostics)
            runtime_wall = round(float(interval[2]) - float(interval[1]), 2)
        # No label-derived input is provided to the child process.
        tests = json.loads((data / "tests.json").read_bytes())["tests"]
        answers = json.loads((data / "answers.json").read_bytes())["commits"]
        if set(answers) != {p.stem for p in (data / "commits").glob("*.patch")}:
            raise RuntimeError("answer IDs differ from patch names")
        if len(output) > 2_000_000:
            raise RuntimeError("stdout exceeds limit")
        scored = scorer.evaluate_run(output, tests, answers)
        if not scored["ok"]:
            raise RuntimeError(scored)
        rows = [json.loads(s) for s in output.splitlines()]
        selected = hits = failures = 0
        for row in rows:
            chosen = {t for worker in row["workers"] for t in worker}
            failing = set(answers[row["commit"]]["failing"])
            selected += len(chosen)
            failures += len(failing)
            hits += len(chosen & failing)
        simulated = scored["simulated_milliseconds"] / 1000
        report = dict(run=run, simulated=simulated, cpu=cpu, total=simulated + cpu, wall=wall,
                      missed=scored["missed_commits"], selected=selected, hits=hits, failures=failures,
                      commits=len(rows), bytes=len(output), peak_bytes=peak_bytes, peak_pids=peak_pids,
                      selector_wall=runtime_wall)
        reports.append(report)
        print(json.dumps(report), flush=True)
        (opts.save / f"run-{run}.jsonl").write_bytes(output)
    (opts.save / "metrics.json").write_text(json.dumps(reports, indent=2) + "\n", encoding="utf-8")
    median = statistics.median(r["total"] for r in reports)
    print(f"median total {median:.6f} s")


if __name__ == "__main__":
    main()
