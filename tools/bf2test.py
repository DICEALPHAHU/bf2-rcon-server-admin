#!/usr/bin/env python3
"""
bf2test.py -- run a batch of RCON commands in ONE connection.

Reconnecting per command is slow and, when the server dies mid-run, produces a
confusing pile of connection errors. This opens one connection and reports each
result as it arrives, plus whether the server survived.

Usage:
    python bf2test.py                # run the built-in vehicle-spawn experiment
    python bf2test.py --cmd "..."    # run specific commands
"""
import argparse
import sys

from bf2rcon import connect, send, RconError


def run(sock, cmds, timeout, label=None):
    ok = True
    for c in cmds:
        try:
            out = send(sock, c, timeout)
        except Exception as e:
            print('  !! connection lost on: %s  (%s)' % (c, e))
            ok = False
            break
        print('  %-58s -> %s' % (c[:58], out.strip()[:150]))
    return ok


def experiment(host, port, password, timeout):
    """Vehicle spawn, sandbox style: create, bury underground, set team, then raise."""
    sock = connect(host, port, password, timeout)
    print('connected + authenticated')
    print()

    print('--- baseline ---')
    run(sock, ['eval ctx.write("map=" + str(bf2.gameLogic.getMapName()))'], timeout)
    print()

    print('--- step 1: create the vehicle (sandbox buries it first) ---')
    run(sock, [
        'exec Object.create jep_nanjing',
    ], timeout)
    print()

    print('--- step 2: bury it underground immediately (sandbox order) ---')
    run(sock, [
        'exec Object.absolutePosition 0.0/-1000.0/0.0',
        'exec Object.rotation 0.0/0.0/0.0',
        'exec Object.team 2',
    ], timeout)
    print()

    print('--- step 3: is it alive and does the manager see it? ---')
    run(sock, [
        'eval ctx.write("count=" + str(len(list(bf2.objectManager.getObjectsOfTemplate("jep_nanjing")))))',
    ], timeout)
    print()

    print('--- step 4: move it to a real position ---')
    run(sock, [
        'eval ctx.write("ground=" + str(bf2.gameLogic.getWorldSize()))',
        'exec Object.absolutePosition 0.0/60.0/0.0',
    ], timeout)
    print()

    print('--- step 5: verify ---')
    run(sock, [
        'eval _o=list(bf2.objectManager.getObjectsOfTemplate("jep_nanjing")); ctx.write("n=" + str(len(_o))); ctx.write(" pos=" + str(_o[0].getPosition()) if _o else " none")',
        'eval ctx.write("server alive")',
    ], timeout)
    print()
    print('server survived: yes (if you saw the last line)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=4711)
    ap.add_argument('--pass', dest='password', default='deepseek')
    ap.add_argument('--timeout', type=float, default=8.0)
    ap.add_argument('--cmd', action='append', default=[])
    args = ap.parse_args()

    try:
        if args.cmd:
            sock = connect(args.host, args.port, args.password, args.timeout)
            print('connected + authenticated')
            run(sock, args.cmd, args.timeout)
        else:
            experiment(args.host, args.port, args.password, args.timeout)
    except RconError as e:
        print('RCON ERROR: %s' % e)
        return 2
    except Exception as e:
        print('ERROR: %s' % e)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
