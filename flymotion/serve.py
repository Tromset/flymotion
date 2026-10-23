"""JSON lines protocol for front ends such as the Electron test app (app/): one request per line on stdin,
replies and events on stdout. The bridge stays the only place that plans, validates, compiles and drives the robot.

  python -m flymotion serve [--robot microduck] [--model Tromset/flymotion0.2]

Requests carry an "id" that their reply repeats:
  {"id": 1, "type": "hello"}                                  -> profile, model, Ollama address, robot profiles
  {"id": 2, "type": "profile", "robot": "microduck-sim"}       -> plans for another robot profile from now on
  {"id": 3, "type": "models"}                                 -> the models Ollama has, FlyMotion ones first
  {"id": 4, "type": "plan", "request": "fais un carré", "model": "..."}   -> plan, commands, warnings, simulated pose
  {"id": 5, "type": "compile", "plan": {"repeat": 1, "steps": [...]}}     -> the same without the model
  {"id": 6, "type": "run", "plan": {...}, "robot": "microduck", "target": "tcp:duck.local:7000"}
                                                              -> streams {"id": 6, "event": "command", "index": i}
  {"id": 7, "type": "stop"}                                   -> interrupts a run (it ends with robot.stop)
  {"id": 8, "type": "reset"}                                  -> forgets the conversation
"""
import json
import sys
import threading
import time
import urllib.request
from queue import Queue

from . import __version__
from .plan import ROBOTS, PlanError, compile_plan, duration, load_profile, parse
from .planner import DEFAULT_MODEL, Planner, ollama_url
from .prompt import say
from .robot import SimTransport, connect, execute


class Stopped(Exception):
    pass


def known_profile(name):
    """A profile from robots/ by name: front ends never pick arbitrary files."""
    if name not in {path.stem for path in ROBOTS.glob('*.json')}:
        raise PlanError(f'unknown robot {name!r}')
    return load_profile(name)


def simulate(commands, profile):
    sim = SimTransport(profile)
    execute(commands, sim, profile)
    return sim.state()


class Server:
    def __init__(self, profile, model=DEFAULT_MODEL, write=None, chat=None):
        self.profile, self.chat = profile, chat
        self.planner = Planner(profile, model, chat=chat)
        self.write = write or self._stdout
        self.writing = threading.Lock()  # replies come from the worker, the runs and stop: one line at a time
        self.stop_requested = threading.Event()
        self.moving = threading.Lock()  # held while a plan plays on the robot

    def _stdout(self, message):
        line = json.dumps(message, ensure_ascii=False) + '\n'
        with self.writing:
            sys.stdout.write(line)
            sys.stdout.flush()

    def result(self, plan, info=None):
        commands, warnings = compile_plan(plan, self.profile)
        plan = {'say': say(plan), **plan}  # a plan written by hand gets its sentence too (the app speaks French)
        return {'plan': plan, 'commands': commands, 'warnings': warnings, 'duration': round(duration(commands), 2),
                'sim': simulate(commands, self.profile), **(info or {})}

    def models(self):
        with urllib.request.urlopen(ollama_url() + '/api/tags', timeout=5) as response:
            names = [model['name'] for model in json.load(response).get('models', [])]
        return sorted(names, key=lambda name: ('flymotion' not in name.lower(), name))

    def run(self, message_id, plan, robot, target, skill_command=None):
        """Plays a plan on a real robot, compiled again here with that robot's own profile: the limits and speeds
        hold whatever the front end sends, and a plan made for the simulator never drives the robot with its speeds."""
        profile = known_profile(robot or self.profile['name'])
        if profile.get('simulated'):
            raise PlanError(f'{profile["name"]} is a simulator profile; run real robots with their own profile')
        commands, _ = compile_plan(parse(json.dumps(plan), profile), profile)
        target = profile['transport'] if target == 'robot' else target
        if target in ('sim', 'print'):
            raise PlanError(f'target {target!r} cannot run from serve: use robot, unix:/path or tcp:host:port')
        if not self.moving.acquire(blocking=False):
            raise PlanError('the robot is already running a plan')
        played = iter(range(len(commands)))

        def sleep(seconds):
            if self.stop_requested.wait(seconds):
                raise Stopped

        def on_command(command):
            self.write({'id': message_id, 'event': 'command', 'index': next(played)})

        try:
            self.stop_requested.clear()
            transport = connect(target, profile, skill_command)
            try:
                execute(commands, transport, profile, sleep=sleep, on_command=on_command)
                return {'stopped': False}
            except Stopped:
                return {'stopped': True}
            finally:
                transport.close()
        finally:
            self.moving.release()

    def handle(self, message):
        kind = message.get('type')
        if kind == 'hello':
            return {'version': __version__, 'profile': self.profile, 'model': self.planner.model, 'ollama': ollama_url(),
                    'robots': [{'name': path.stem, 'title': robot['title'], 'simulated': bool(robot.get('simulated'))}
                               for path in sorted(ROBOTS.glob('*.json')) for robot in [load_profile(path.stem)]]}
        if kind == 'profile':
            self.profile = known_profile(message.get('robot'))
            self.planner = Planner(self.profile, self.planner.model, chat=self.chat)
            return {'profile': self.profile}
        if kind == 'models':
            return {'models': self.models()}
        if kind == 'plan':
            request = str(message.get('request', '')).strip()
            if not request:
                raise PlanError('empty request')
            self.planner.model = message.get('model') or self.planner.model
            plan, info = self.planner.plan(request)
            return self.result(plan, info)
        if kind == 'compile':
            plan = message.get('plan')
            return self.result(parse(plan if isinstance(plan, str) else json.dumps(plan), self.profile))
        if kind == 'run':
            return self.run(message.get('id'), message.get('plan'), message.get('robot'), message.get('target', 'robot'),
                            message.get('skill_command'))
        if kind == 'stop':
            self.stop_requested.set()
            return {'running': self.moving.locked()}
        if kind == 'reset':
            self.planner.history = []
            return {}
        raise PlanError(f'unknown message type {kind!r}')

    def answer(self, message):
        """Handles one message and writes its reply; errors become {"error": "..."} so the front end can show them."""
        reply = {'id': message.get('id')}
        started = time.monotonic()
        try:
            reply.update(ok=True, **self.handle(message))
        except (PlanError, OSError, RuntimeError, ValueError) as error:
            reply.update(ok=False, error=str(error))
        except Exception as error:  # a malformed message must not take the bridge down
            reply.update(ok=False, error=f'{type(error).__name__}: {error}')
        reply['elapsed'] = round(time.monotonic() - started, 2)
        self.write(reply)

    def serve(self, lines):
        """Messages are answered in order, one at a time, except "stop" (at once) and "run" (in its own thread),
        so a stop always gets through and the app can plan while the robot moves."""
        queue, runs = Queue(), []

        def worker():
            for message in iter(queue.get, None):
                self.answer(message)

        ordered = threading.Thread(target=worker, daemon=True)
        ordered.start()
        for line in lines:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as error:
                self.write({'id': None, 'ok': False, 'error': f'not JSON ({error.msg})'})
                continue
            if not isinstance(message, dict):
                self.write({'id': None, 'ok': False, 'error': 'a message is a JSON object'})
            elif message.get('type') == 'stop':
                self.answer(message)
            elif message.get('type') == 'run':
                runs.append(threading.Thread(target=self.answer, args=(message,), daemon=True))
                runs[-1].start()
            else:
                queue.put(message)
        # End of input (the app quit): stop the robot, let the requests already sent answer.
        self.stop_requested.set()
        queue.put(None)
        for thread in [ordered, *runs]:
            thread.join()


def main(argv):
    import argparse
    parser = argparse.ArgumentParser(prog='flymotion serve', description='JSON lines bridge for the FlyMotion app.')
    parser.add_argument('--robot', default='microduck')
    parser.add_argument('--model', default=DEFAULT_MODEL)
    args = parser.parse_args(argv)
    try:
        server = Server(load_profile(args.robot), args.model)
    except PlanError as error:
        print(f'flymotion: {error}', file=sys.stderr)
        return 1
    print(f'flymotion serve: {args.robot}, {args.model}, Ollama at {ollama_url()}', file=sys.stderr)
    try:
        server.serve(sys.stdin)
    except KeyboardInterrupt:
        server.stop_requested.set()
    return 0
