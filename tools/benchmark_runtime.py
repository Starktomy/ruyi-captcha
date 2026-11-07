"""Alternate native kernels with one provider/client and measure complete Linux process trees."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
from statistics import median
import subprocess
import sys
from time import monotonic, process_time, sleep

URL = "https://capskip.com/captcha-demo/cloudflare-turnstile/"
SITEKEY = "0x4AAAAAADogn3t3_JKwKkgS"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def worker(shell, concurrency):
    from ruyi_captcha import solve_turnstile_details
    from ruyi_captcha.runtime import check_runtime
    check_runtime(shell)
    print(json.dumps({"ready": True, "pid": os.getpid()}), flush=True)
    if sys.stdin.readline().strip() != "go":
        raise RuntimeError("Missing measurement start signal")
    started = monotonic()

    def run(index):
        begin = monotonic()
        try:
            result = solve_turnstile_details(URL, SITEKEY, timeout=30, attempts=1, proxy=None, shell=shell)
            return {"index": index, "success": True, "elapsed": result.elapsed,
                    "token_length": len(result.token), "attempts": result.attempts}
        except Exception as error:
            return {"index": index, "success": False, "elapsed": monotonic()-begin,
                    "code": getattr(error, "code", None), "error": f"{type(error).__name__}: {error}"}

    if concurrency == 1:
        results = [run(0)]
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            results = list(pool.map(run, range(concurrency)))
    print(json.dumps({"wall_seconds": monotonic()-started, "solutions": results}), flush=True)


def snapshot(cgroup):
    rows = []
    for text in (cgroup / "cgroup.procs").read_text().split():
        pid = int(text)
        try:
            path = Path("/proc") / text
            fields = (path / "stat").read_text().rsplit(")", 1)[1].split()
            memory = {}
            for line in (path / "smaps_rollup").read_text().splitlines():
                if ":" in line:
                    key, value = line.split(":", 1)
                    if key in ("Rss", "Pss", "Private_Clean", "Private_Dirty"):
                        memory[key] = int(value.split()[0])
            rows.append({"pid": pid, "start": int(fields[19]), "cpu_ticks": int(fields[11])+int(fields[12]),
                         "threads": int(fields[17]), "executable": (path / "exe").resolve().name,
                         "rss_kib": memory.get("Rss", 0), "pss_kib": memory.get("Pss", 0),
                         "private_kib": memory.get("Private_Clean", 0)+memory.get("Private_Dirty", 0)})
        except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError):
            continue
    return rows


def cpu_stat(cgroup):
    return {line.split()[0]: int(line.split()[1]) for line in (cgroup / "cpu.stat").read_text().splitlines()}


def measure(variant, concurrency, repetition, parent):
    env = dict(os.environ, RUYIJS_SHELL=variant["shell"], RUYIJS_NECKO_PYTHON=variant["helper"],
               RUYI_CAPTCHA_KERNEL="external")
    proc = subprocess.Popen([sys.executable, "-B", str(Path(__file__).resolve()), "--worker",
                             "--shell", variant["shell"], "--concurrency", str(concurrency)],
                            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    group = parent / f"ruyi-captcha-bench-{proc.pid}"
    try:
        ready = json.loads(proc.stdout.readline())
        if not ready.get("ready"):
            raise RuntimeError("Worker failed to initialise")
        group.mkdir()
        (group / "cgroup.procs").write_text(str(proc.pid))
        baseline = snapshot(group)
        before = cpu_stat(group)
        row = {"variant": variant["name"], "concurrency": concurrency, "repetition": repetition,
               "driver_idle_pss_mib": sum(p["pss_kib"] for p in baseline)/1024,
               "peak_tree_pss_mib": 0, "peak_tree_rss_mib": 0, "peak_tree_private_mib": 0,
               "peak_processes": 0, "samples": 0}
        started, sampler_cpu = monotonic(), process_time()
        proc.stdin.write("go\n")
        proc.stdin.flush()
        while proc.poll() is None:
            at = monotonic()
            rows = snapshot(group)
            row["samples"] += 1
            for metric, key in (("peak_tree_pss_mib", "pss_kib"), ("peak_tree_rss_mib", "rss_kib"),
                                ("peak_tree_private_mib", "private_kib")):
                row[metric] = max(row[metric], sum(p[key] for p in rows)/1024)
            row["peak_processes"] = max(row["peak_processes"], len(rows))
            if monotonic()-started > 45:
                (group / "cgroup.kill").write_text("1")
                raise RuntimeError("Measurement exceeded 45 seconds")
            sleep(max(0, 0.05-(monotonic()-at)))
        cleanup = monotonic()+2
        while (group / "cgroup.procs").read_text().strip() and monotonic()<cleanup:
            sleep(0.02)
        row["sampler_cpu_seconds"] = process_time()-sampler_cpu
        row["remaining_processes"] = len((group / "cgroup.procs").read_text().split())
        after = cpu_stat(group)
        row["cpu_seconds"] = (after["usage_usec"]-before["usage_usec"])/1e6
        row["cpu_user_seconds"] = (after["user_usec"]-before["user_usec"])/1e6
        row["cpu_system_seconds"] = (after["system_usec"]-before["system_usec"])/1e6
        stdout, stderr = proc.communicate(timeout=5)
        if proc.returncode:
            raise RuntimeError(f"Worker exited {proc.returncode}: {stderr[:1000]}")
        row.update(json.loads(stdout))
        row["successes"] = sum(item["success"] for item in row["solutions"])
        row["cpu_seconds_per_call"] = row["cpu_seconds"]/concurrency
        row["average_cpu_cores"] = row["cpu_seconds"]/row["wall_seconds"]
        return row
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
        if group.exists():
            if (group / "cgroup.procs").read_text().strip():
                (group / "cgroup.kill").write_text("1")
                sleep(0.1)
            group.rmdir()


def main(args):
    root, old_shell, old_helper = args.kernel.resolve(), args.old_shell.resolve(), args.old_helper.resolve()
    current_shell = root / "dist/js"
    helper = root / "vendor/necko-derived/python/bin/python3"
    variants = [
        {"name": "new_minimal", "shell": str(current_shell), "helper": str(helper)},
        {"name": "old_full", "shell": str(old_shell), "helper": str(old_helper)},
        {"name": "old_full_new_helper", "shell": str(old_shell), "helper": str(helper)},
    ]
    for variant in variants:
        variant["shell_sha256"] = digest(variant["shell"])
    parent_path = next(line.split(":", 2)[2] for line in Path('/proc/self/cgroup').read_text().splitlines()
                       if line.startswith('0::'))
    parent = Path('/sys/fs/cgroup') / parent_path.lstrip('/')
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "variants": variants,
              "machine": {"platform": platform.platform(), "affinity_cpus": len(os.sched_getaffinity(0)),
                          "logical_cpus": os.cpu_count(),
                          "cpu_model": next(line.split(':',1)[1].strip() for line in Path('/proc/cpuinfo').read_text().splitlines()
                                            if line.startswith('model name'))},
              "method": {"profile": "same linux Firefox 159", "driver": "same ruyi-captcha provider and Python bindings",
                         "measurement": "after imports and capability preflight; fresh Contexts; startup and cleanup included",
                         "cpu": "isolated cgroup cpu.stat; Python caller, runtime, workers and helpers; sampler excluded",
                         "memory": "simultaneous tree PSS/RSS sampled every 50ms, caller included",
                         "backend_verification": False, "attempts": 1, "timeout": 30,
                         "concurrency": [1,2,4], "repetitions": 3,
                         "order": "rotating and alternating variant order; sequential batches; one warmup per variant"},
              "warmups": [], "runs": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        args.output.write_text(json.dumps(report, indent=2)+'\n')
    for variant in variants:
        row = measure(variant, 1, 0, parent)
        report['warmups'].append(row)
        save()
        print(json.dumps({"warmup": variant['name'], "successes": row['successes']}), flush=True)
        if row['successes'] != 1:
            raise RuntimeError('Warmup failed; stop comparison')
    index = 0
    for repetition in (1,2,3):
        for concurrency in (1,2,4):
            order = variants[index%3:] + variants[:index%3]
            if index%2:
                order = list(reversed(order))
            index += 1
            for variant in order:
                row = measure(variant, concurrency, repetition, parent)
                report['runs'].append(row)
                save()
                print(json.dumps({k: round(v,3) if isinstance(v,float) else v for k,v in row.items()
                                  if k in ('variant','concurrency','repetition','successes','wall_seconds',
                                           'cpu_seconds_per_call','peak_tree_pss_mib','peak_tree_rss_mib','remaining_processes')}), flush=True)
    report['summary'] = []
    for concurrency in (1,2,4):
        for variant in variants:
            rows = [row for row in report['runs'] if row['concurrency']==concurrency and row['variant']==variant['name']]
            success = sum(row['successes'] for row in rows)
            latencies = [s['elapsed'] for row in rows for s in row['solutions'] if s['success']]
            report['summary'].append({'variant': variant['name'], 'concurrency': concurrency,
                'successes': success, 'calls': concurrency*len(rows),
                'median_token_seconds': median(latencies) if latencies else None,
                'median_batch_seconds': median(row['wall_seconds'] for row in rows),
                'cpu_seconds_per_success': sum(row['cpu_seconds'] for row in rows)/success if success else None,
                'median_peak_pss_mib': median(row['peak_tree_pss_mib'] for row in rows),
                'median_peak_rss_mib': median(row['peak_tree_rss_mib'] for row in rows),
                'remaining_processes': sum(row['remaining_processes'] for row in rows)})
    report['finished_at'] = datetime.now(timezone.utc).isoformat()
    save()
    print(json.dumps({'summary': report['summary']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker', action='store_true')
    parser.add_argument('--shell')
    parser.add_argument('--concurrency', type=int, default=1)
    parser.add_argument('--kernel', type=Path, default=Path('/root/ff-ruyijs'))
    parser.add_argument('--old-shell', type=Path)
    parser.add_argument('--old-helper', type=Path)
    parser.add_argument('--output', type=Path, default=Path('artifacts/runtime-comparison.json'))
    args = parser.parse_args()
    worker(args.shell, args.concurrency) if args.worker else main(args)
