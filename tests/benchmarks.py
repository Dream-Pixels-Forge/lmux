#!/usr/bin/env python3
"""
benchmarks.py — Performance benchmarks for lmux daemon.

Measures:
  - Request latency (ping round-trip)
  - Throughput (requests/sec)
  - Session save/load speed

Usage:
  python3 tests/benchmarks.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time

SOCK = "/tmp/lmux-bench.sock"
ITERATIONS = 500
TIMEOUT = 5.0


def start_daemon():
    """Start lmux daemon, return Popen handle."""
    # Clean stale socket
    if os.path.exists(SOCK):
        os.unlink(SOCK)
    proc = subprocess.Popen(
        ["./build/lmux", "--socket", SOCK, "daemon"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # Wait for socket to appear
    for _ in range(30):
        if os.path.exists(SOCK):
            time.sleep(0.2)
            return proc
        time.sleep(0.2)
    raise RuntimeError("Daemon failed to start — socket not found")


def stop_daemon(proc):
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
    proc.wait()


def send_cmd(cmd, args=None):
    """Send a command, return parsed response and elapsed seconds."""
    payload = json.dumps({"cmd": cmd, "args": args or {}}) + "\n"
    t0 = time.perf_counter()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(TIMEOUT)
    sock.connect(SOCK)
    sock.sendall(payload.encode())
    data = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if b"\n" in data:
            break
    elapsed = time.perf_counter() - t0
    sock.close()
    return json.loads(data.decode().strip()), elapsed


def bench_ping():
    """Benchmark: ping latency."""
    latencies = []
    for _ in range(ITERATIONS):
        _, elapsed = send_cmd("ping")
        latencies.append(elapsed)
    latencies.sort()
    avg = sum(latencies) / len(latencies)
    p50 = latencies[len(latencies) // 2]
    p99 = latencies[int(len(latencies) * 0.99)]
    return {"name": "ping_latency", "iterations": ITERATIONS,
            "avg_ms": round(avg * 1000, 2),
            "p50_ms": round(p50 * 1000, 2),
            "p99_ms": round(p99 * 1000, 2)}


def bench_throughput():
    """Benchmark: requests per second."""
    t0 = time.perf_counter()
    for _ in range(ITERATIONS):
        send_cmd("ping")
    elapsed = time.perf_counter() - t0
    rps = ITERATIONS / elapsed
    return {"name": "throughput", "iterations": ITERATIONS,
            "rps": round(rps, 1), "total_s": round(elapsed, 3)}


def bench_workspace_list():
    """Benchmark: workspace.list (heavier payload)."""
    # Create a few workspaces first
    for i in range(5):
        send_cmd("workspace.create", {"title": f"bench-ws-{i}"})
    latencies = []
    for _ in range(ITERATIONS):
        _, elapsed = send_cmd("workspace.list")
        latencies.append(elapsed)
    latencies.sort()
    avg = sum(latencies) / len(latencies)
    p50 = latencies[len(latencies) // 2]
    p99 = latencies[int(len(latencies) * 0.99)]
    return {"name": "workspace_list_latency", "iterations": ITERATIONS,
            "avg_ms": round(avg * 1000, 2),
            "p50_ms": round(p50 * 1000, 2),
            "p99_ms": round(p99 * 1000, 2)}


def bench_snapshot():
    """Benchmark: snapshot save/load."""
    save_times = []
    load_times = []
    snap_path = tempfile.mktemp(suffix=".json")
    for _ in range(50):
        _, elapsed = send_cmd("snapshot.save", {"path": snap_path})
        save_times.append(elapsed)
        _, elapsed = send_cmd("snapshot.load", {"path": snap_path})
        load_times.append(elapsed)
    os.unlink(snap_path) if os.path.exists(snap_path) else None
    save_times.sort()
    load_times.sort()
    return {
        "name": "snapshot_io",
        "iterations": 50,
        "save_avg_ms": round(sum(save_times) / len(save_times) * 1000, 2),
        "save_p50_ms": round(save_times[len(save_times) // 2] * 1000, 2),
        "load_avg_ms": round(sum(load_times) / len(load_times) * 1000, 2),
        "load_p50_ms": round(load_times[len(load_times) // 2] * 1000, 2),
    }


def main():
    print("lmux Performance Benchmarks")
    print("=" * 50)

    proc = start_daemon()
    try:
        results = []
        for bench_fn in [bench_ping, bench_throughput, bench_workspace_list, bench_snapshot]:
            name = bench_fn.__name__
            print(f"  Running {name}...", end=" ", flush=True)
            result = bench_fn()
            results.append(result)
            print("OK")

        print("\nResults:")
        print(json.dumps(results, indent=2))

        # Verify latency SLA: p99 < 50ms
        for r in results:
            if "p99_ms" in r and r["p99_ms"] > 50:
                print(f"\nFAIL: {r['name']} p99={r['p99_ms']}ms > 50ms SLA")
                sys.exit(1)

        print("\nAll benchmarks passed.")
    finally:
        stop_daemon(proc)


if __name__ == "__main__":
    main()
