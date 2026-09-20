#!/usr/bin/env python3
"""
crashprobe.py -- find the minimal server action that kills a BF2 server.

Method: for each hypothesis, start the server fresh, run exactly that one step,
then check whether the process is still alive. Each trial is isolated so a crash
cannot contaminate the next result.

Run:  python crashprobe.py
"""
import os
import subprocess
import sys
import time

BF = r'D:\BF2ServerForDeepseek\Battlefield 2'
BAT = os.path.join(BF, 'RUN-SERVER.bat')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bf2rcon import connect, send  # noqa: E402


def server_alive():
    out = subprocess.run(
        ['tasklist', '/FI', 'IMAGENAME eq bf2_w32ded.exe', '/NH'],
        capture_output=True, text=True, errors='replace')
    return 'bf2_w32ded' in (out.stdout or '')


def kill_server():
    subprocess.run(['taskkill', '/F', '/IM', 'bf2_w32ded.exe'],
                   capture_output=True, text=True, errors='replace')
    subprocess.run(['taskkill', '/F', '/IM', 'cmd.exe'],
                   capture_output=True, text=True, errors='replace')
    time.sleep(2)


def start_server(wait=14):
    kill_server()
    for f in ('pmadmin.log',):
        p = os.path.join(BF, f)
        if os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
    subprocess.Popen(['cmd.exe', '/c', BAT], cwd=BF)
    time.sleep(wait)
    return server_alive()


def try_step(name, cmds, wait=2.0):
    """Start the server, run cmds, report whether it survived."""
    print('=' * 70)
    print('TRIAL: %s' % name)
    if not start_server():
        print('  server failed to start; skipping')
        return None
    try:
        sock = connect('127.0.0.1', 4711, 'deepseek', 8.0)
    except Exception as e:
        print('  rcon connect failed: %s' % e)
        kill_server()
        return None

    died_on = None
    for c in cmds:
        try:
            out = send(sock, c, 8.0)
        except Exception as e:
            print('  %-50s -> CONNECTION LOST (%s)' % (c[:50], type(e).__name__))
            died_on = c
            break
        print('  %-50s -> %s' % (c[:50], out.strip()[:110]))
        time.sleep(0.4)

    time.sleep(wait)
    alive = server_alive()
    print('  RESULT: server %s' % ('ALIVE' if alive else 'CRASHED'))
    try:
        sock.close()
    except Exception:
        pass
    kill_server()
    return alive


def main():
    trials = [
        ('A: eval only (baseline)', [
            'eval ctx.write("hello")',
        ]),
        ('B: create vehicle, nothing else', [
            'exec Object.create jep_nanjing',
        ]),
        ('C: create vehicle then set position', [
            'exec Object.create jep_nanjing',
            'exec Object.absolutePosition 0.0/-1000.0/0.0',
        ]),
        ('D: create STATIC object then set position', [
            'exec Object.create woodencrate_4m',
            'exec Object.absolutePosition -770.0/186.0/-134.0',
        ]),
        ('E: Object.list (read-only)', [
            'exec Object.list',
        ]),
    ]

    results = {}
    for name, cmds in trials:
        results[name] = try_step(name, cmds)

    print()
    print('=' * 70)
    print('SUMMARY')
    for name, alive in results.items():
        if alive is None:
            verdict = 'skipped'
        elif alive:
            verdict = 'ALIVE  (safe)'
        else:
            verdict = 'CRASHED'
        print('  %-42s %s' % (name, verdict))


if __name__ == '__main__':
    main()
