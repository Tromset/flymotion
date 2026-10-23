#!/usr/bin/env python3
"""FlyMotion benchmark: each request is planned by the model, compiled, played in the simulator, and the final
position, heading, head and skills are checked, and so is the gait: a plan that makes the robot shake on the spot
instead of walking fails, whatever its final position. It scores what the robot would do, not the exact JSON.

  python3 bench/run.py --model flymotion0.2lite [--robot microduck-sim] [--out docs/bench/flymotion0.2lite.json] [--only 3,8]
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flymotion.plan import PlanError, compile_plan, duration, load_profile  # noqa: E402
from flymotion.planner import Planner  # noqa: E402
from flymotion.robot import SimTransport, execute  # noqa: E402


def angle_gap(a, b):
    return abs((a - b + 180) % 360 - 180)


def check(expect, plan, state, commands):
    """Returns the list of failed expectations; empty when the case passes."""
    failures = []
    if expect.get('refuse'):
        return [] if not plan['steps'] else [f'should refuse, planned {len(plan["steps"])} steps']
    if not plan['steps']:
        return ['refused a feasible request']
    tolerance = expect.get('tolerance_m', 0.1)
    for axis in ('x', 'y'):
        if axis in expect and abs(state[axis] - expect[axis]) > max(tolerance, 0.1 * abs(expect[axis])):
            failures.append(f'{axis}={state[axis]:+.2f} m, expected {expect[axis]:+.2f}')
    if 'heading' in expect and angle_gap(state['heading'], expect['heading']) > 10:
        failures.append(f'heading={state["heading"]:+.0f}, expected {expect["heading"]:+.0f}')
    if 'skills' in expect and state['skills'] != expect['skills']:
        failures.append(f'skills={state["skills"]}, expected {expect["skills"]}')
    for joint, sign in expect.get('head', {}).items():
        value = state['head'].get({'yaw': 'head_yaw', 'pitch': 'head_pitch', 'roll': 'head_roll'}[joint], 0)
        if (sign == '+' and value <= 0.05) or (sign == '-' and value >= -0.05):
            failures.append(f'head {joint}={math.degrees(value):+.0f} degrees, expected {sign}')
    if state.get('in_place', 0) > 0.2:
        failures.append(f'stepped in place for {state["in_place"]:.1f} s instead of walking')
    if duration(commands) < expect.get('min_seconds', 0):
        failures.append(f'{duration(commands):.1f} s of motion, expected at least {expect["min_seconds"]}')
    return failures


def run_case(planner, profile, case):
    planner.history = []  # every case starts a fresh conversation
    started = time.monotonic()
    try:
        plan, info = planner.plan(case['request'])
    except PlanError as error:
        return {'request': case['request'], 'passed': False, 'failures': [f'no valid plan: {error}'],
                'seconds': round(time.monotonic() - started, 2)}
    commands, warnings = compile_plan(plan, profile)
    sim = SimTransport(profile)
    execute(commands, sim, profile)
    failures = check(case['expect'], plan, sim.state(), commands)
    return {'request': case['request'], 'passed': not failures, 'failures': failures, 'plan': plan,
            'warnings': warnings, 'sim': sim.state(), 'seconds': info['seconds'], 'attempts': info['attempts']}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', default='flymotion0.2')
    parser.add_argument('--robot', default='microduck')
    parser.add_argument('--out')
    parser.add_argument('--only', help='comma-separated case numbers, from 1')
    args = parser.parse_args()
    profile = load_profile(args.robot)
    cases = json.loads((ROOT / 'bench/cases.json').read_text())
    numbers = [int(n) for n in args.only.split(',')] if args.only else range(1, len(cases) + 1)
    planner = Planner(profile, args.model, timeout=600)
    results = []
    for n in numbers:
        result = run_case(planner, profile, cases[n - 1])
        results.append({'case': n, **result})
        mark = 'pass' if result['passed'] else 'FAIL'
        print(f'{n:2} {mark} {result["seconds"]:6.1f} s  {result["request"]}' + ''.join(f'\n          {f}' for f in result['failures']), flush=True)
    passed = sum(r['passed'] for r in results)
    print(f'\n{args.model}: {passed}/{len(results)} passed, median planning time '
          f'{sorted(r["seconds"] for r in results)[len(results) // 2]:.1f} s')
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps({'model': args.model, 'robot': args.robot, 'passed': passed,
                                              'total': len(results), 'results': results}, ensure_ascii=False, indent=1) + '\n')


if __name__ == '__main__':
    main()
