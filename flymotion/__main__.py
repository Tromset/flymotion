"""FlyMotion command line.

  python -m flymotion "avance de 50 cm puis tourne à droite"            plan, then play it in the simulator
  python -m flymotion --transport robot "fais un carré"                  on the robot (robotd socket of the profile)
  python -m flymotion --transport robot                                  conversation: one request per line
  python -m flymotion --plan-only "kick the ball"                        print the JSON plan only
  python -m flymotion calibrate vx --transport robot                     measure the real speed of an axis
  python -m flymotion serve                                              JSON lines bridge for the app (app/)
"""
import argparse
import json
import sys

from .plan import PlanError, compile_plan, duration, load_profile
from .planner import DEFAULT_MODEL, Planner
from .robot import SimTransport, connect, execute


def describe(command):
    kind = command['kind']
    if kind == 'move':
        return f'move vx={command["vx"]:+.2f} vy={command["vy"]:+.2f} vyaw={command["vyaw"]:+.2f} for {command["seconds"]:.1f} s'
    if kind == 'head':
        return f'head yaw={command["yaw"]:+.2f} pitch={command["pitch"]:+.2f} roll={command["roll"]:+.2f} rad'
    if kind == 'skill':
        return f'skill {command["name"]}'
    return 'stop'


def run_request(request, planner, args, profile):
    plan, info = planner.plan(request)
    if args.plan_only:
        print(json.dumps(plan, ensure_ascii=False))
        return
    commands, warnings = compile_plan(plan, profile)
    print(f'FlyMotion: {plan["say"]}')
    print(f'  ({len(plan["steps"])} steps, {duration(commands):.1f} s of motion, planned in {info["seconds"]} s)', file=sys.stderr)
    for warning in warnings:
        print(f'  limited: {warning}', file=sys.stderr)
    if not plan['steps']:
        return
    target = profile['transport'] if args.transport == 'robot' else args.transport
    if target != 'sim' and not args.yes:
        for command in commands:
            print(f'  - {describe(command)}', file=sys.stderr)
        if input('Run it on the robot? [y/N] ').strip().lower() not in ('y', 'yes', 'o', 'oui'):
            return
    transport = connect(target, profile, args.skill_command)
    try:
        execute(commands, transport, profile, on_command=lambda c: print(f'  > {describe(c)}', file=sys.stderr) if args.verbose else None)
    finally:
        transport.close()
    if isinstance(transport, SimTransport):
        state = transport.state()
        print(f'  sim: x={state["x"]:+.2f} m, y={state["y"]:+.2f} m, heading={state["heading"]:+.0f} degrees'
              + (f', skills: {", ".join(state["skills"])}' if state['skills'] else ''), file=sys.stderr)


def calibrate(args, profile):
    """Commands one axis at cruise speed for a few seconds; the user measures what really happened."""
    axis, seconds = args.axis, args.seconds
    speed = profile['cruise'][axis]
    unit = 'degrees' if axis == 'vyaw' else 'cm'
    expected = speed * seconds * (57.2958 if axis == 'vyaw' else 100)
    print(f'The robot will move on {axis} at {speed} for {seconds} s: {expected:.0f} {unit} if it tracked the command perfectly.')
    if input('Ready? [y/N] ').strip().lower() not in ('y', 'yes', 'o', 'oui'):
        return
    target = profile['transport'] if args.transport == 'robot' else args.transport
    transport = connect(target, profile, args.skill_command)
    try:
        execute([{'kind': 'move', 'vx': 0.0, 'vy': 0.0, 'vyaw': 0.0, axis: speed, 'seconds': seconds}, {'kind': 'stop'}], transport, profile)
    finally:
        transport.close()
    measured = float(input(f'Measured {unit}: ').replace(',', '.'))
    print(f'Put this in robots/{profile["name"]}.json: "achieved": {{ ... "{axis}": {abs(measured) / expected:.2f} ... }}')


def main(argv=None):
    parser = argparse.ArgumentParser(prog='flymotion', description='Natural-language motion for small robots, planned by a local Ollama model.')
    parser.add_argument('request', nargs='*', help='what the robot should do; none for a conversation')
    parser.add_argument('--robot', default='microduck', help='robot profile: a name in robots/ or a JSON path (default: microduck)')
    parser.add_argument('--model', default=DEFAULT_MODEL, help=f'Ollama model (default: {DEFAULT_MODEL})')
    parser.add_argument('--transport', default='sim', help='sim (default), robot (the profile socket), print, unix:/path, tcp:host:port')
    parser.add_argument('--skill-command', help='command that runs a skill, {skill} replaced (e.g. "ssh duck robotctl robot do {skill}")')
    parser.add_argument('--plan-only', action='store_true', help='print the JSON plan without running it')
    parser.add_argument('--yes', '-y', action='store_true', help='run on the robot without asking for confirmation')
    parser.add_argument('--verbose', '-v', action='store_true', help='print each command as it is sent')
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ['serve']:
        from .serve import main as serve
        return serve(argv[1:])
    if argv[:1] == ['calibrate']:
        sub = argparse.ArgumentParser(prog='flymotion calibrate')
        sub.add_argument('axis', choices=['vx', 'vy', 'vyaw'])
        sub.add_argument('--seconds', type=float, default=5)
        sub.add_argument('--robot', default='microduck')
        sub.add_argument('--transport', default='robot')
        sub.add_argument('--skill-command')
        args = sub.parse_args(argv[1:])
        return calibrate(args, load_profile(args.robot))
    args = parser.parse_args(argv)
    try:
        profile = load_profile(args.robot)
        planner = Planner(profile, args.model)
        if args.request:
            run_request(' '.join(args.request), planner, args, profile)
            return 0
        print(f'FlyMotion with {args.model} on {profile["name"]} ({args.transport}). One request per line, Ctrl-D to quit.', file=sys.stderr)
        for line in sys.stdin:
            if line.strip():
                try:
                    run_request(line.strip(), planner, args, profile)
                except PlanError as error:
                    print(f'flymotion: {error}', file=sys.stderr)
        return 0
    except PlanError as error:
        print(f'flymotion: {error}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nflymotion: stopped.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
