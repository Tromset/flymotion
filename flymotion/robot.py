"""Sends compiled commands to a robot.

Microduck's robotd speaks JSON-RPC 2.0, one object per line, on /run/robotd.sock:
  robot.move {vx, vy, vyaw}   notification, last writer wins; robotd stops the robot when they stop arriving
  robot.head {neck_pitch, head_pitch, head_yaw, head_roll}   notification, radians
  robot.stop                  request: zero velocity, keep standing
Skills have no JSON-RPC method yet, so they run `robotctl robot do <skill>` (the profile's skill_command).
"""
import json
import math
import shlex
import socket
import subprocess
import sys
import time

from .plan import gait


class Transport:
    """Base: records nothing, used by the executor through send() and skill()."""

    def send(self, method, params=None, request=False):
        raise NotImplementedError

    def skill(self, name):
        raise NotImplementedError

    def close(self):
        pass


class LineTransport(Transport):
    """NDJSON over a stream: a Unix socket, a TCP port (e.g. `socat TCP-LISTEN:7000,fork UNIX-CONNECT:/run/robotd.sock`)
    or standard output (pipe it: `... --transport print | ssh duck socat - UNIX-CONNECT:/run/robotd.sock`)."""

    def __init__(self, target, skill_command=None):
        self.skill_command, self.next_id, self.sock = skill_command, 1, None
        if target == 'print':
            self.out = sys.stdout
        else:
            kind, _, address = target.partition(':')
            if kind == 'unix':
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(address)
            elif kind == 'tcp':
                host, _, port = address.rpartition(':')
                self.sock = socket.create_connection((host, int(port)), timeout=5)
            else:
                raise ValueError(f'unknown transport {target!r} (use sim, print, unix:/path or tcp:host:port)')
            self.sock.settimeout(1.0)
            self.out = self.sock.makefile('w', encoding='utf-8', newline='\n')
            self.replies = self.sock.makefile('r', encoding='utf-8', newline='\n')

    def send(self, method, params=None, request=False):
        message = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            message['params'] = params
        if request:
            message['id'] = self.next_id
            self.next_id += 1
        self.out.write(json.dumps(message, separators=(',', ':')) + '\n')
        self.out.flush()
        if request and self.sock:
            return self._reply(message['id'])

    def _reply(self, wanted):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            try:
                line = self.replies.readline()
            except (socket.timeout, TimeoutError):
                break
            if not line:
                break
            try:
                reply = json.loads(line)
            except json.JSONDecodeError:
                continue
            if reply.get('id') == wanted:  # robotd also streams notifications (robot.state): skip them
                if 'error' in reply:
                    raise RuntimeError(f'robotd refused: {reply["error"]}')
                return reply.get('result')
        return None

    def skill(self, name):
        if not self.skill_command:
            print(f'flymotion: skill {name} skipped (no skill command)', file=sys.stderr)
            return
        command = [part.replace('{skill}', name) for part in self.skill_command]
        subprocess.run(command, check=True, timeout=30, stdout=subprocess.DEVNULL)

    def close(self):
        if self.sock:
            self.sock.close()


class SimTransport(Transport):
    """A flat-ground kinematic simulation: integrates the velocity the robot really reaches (profile `achieved`).
    It walks like the profile's gait: nothing moves during the start-up from standing, and an axis commanded below
    the gait threshold steps in place. That time is counted in `in_place`, so a plan that would only make the robot
    shake on the spot never passes for a walk. Used by --transport sim, the benchmark and the tests.
    Time is virtual unless realtime=True."""

    def __init__(self, profile, realtime=False):
        self.achieved, self.realtime, self.gait = profile['achieved'], realtime, gait(profile)
        self.x = self.y = self.heading = self.t = self.in_place = 0.0
        self.velocity = (0.0, 0.0, 0.0)
        self.started = None  # when the robot last started from standing
        self.head, self.skills, self.log = {}, [], []

    def advance(self, seconds):
        steps = max(1, int(seconds / 0.01))
        dt = seconds / steps
        minimum = self.gait['min_speed']
        commanded = dict(zip(('vx', 'vy', 'vyaw'), self.velocity))
        walked = {axis: value if abs(value) >= minimum[axis] else 0.0 for axis, value in commanded.items()}
        for _ in range(steps):
            if self.started is not None and self.t - self.started < self.gait['start_seconds'] - 1e-9:
                vx = vy = vyaw = 0.0
            else:
                vx, vy, vyaw = walked['vx'], walked['vy'], walked['vyaw']
                if any(commanded.values()) and not any(walked.values()):
                    self.in_place += dt
            self.heading += vyaw * self.achieved['vyaw'] * dt / 2
            c, s = math.cos(self.heading), math.sin(self.heading)
            self.x += (vx * self.achieved['vx'] * c - vy * self.achieved['vy'] * s) * dt
            self.y += (vx * self.achieved['vx'] * s + vy * self.achieved['vy'] * c) * dt
            self.heading += vyaw * self.achieved['vyaw'] * dt / 2
            self.t += dt

    def _set(self, velocity):
        if not any(velocity):
            self.started = None
        elif self.started is None:
            self.started = self.t
        self.velocity = velocity

    def send(self, method, params=None, request=False):
        self.log.append((round(self.t, 3), method, params))
        if method == 'robot.move':
            self._set((params['vx'], params['vy'], params['vyaw']))
        elif method == 'robot.stop':
            self._set((0.0, 0.0, 0.0))
        elif method == 'robot.head':
            self.head = dict(params)

    def skill(self, name):
        self.skills.append(name)

    def state(self):
        return {'x': round(self.x, 3), 'y': round(self.y, 3), 'heading': round(math.degrees(self.heading), 1),
                'head': self.head, 'skills': list(self.skills), 'seconds': round(self.t, 2), 'in_place': round(self.in_place, 2)}


def connect(target, profile, skill_command=None):
    if target == 'sim':
        return SimTransport(profile)
    if skill_command is None:
        skill_command = profile.get('skill_command')
    elif isinstance(skill_command, str):
        skill_command = shlex.split(skill_command)
    return LineTransport(target, skill_command)


def execute(commands, transport, profile, sleep=time.sleep, on_command=None):
    """Plays the commands, re-sending robot.move at rate_hz so robotd's deadman never fires mid-step.
    With a SimTransport the clock is virtual. Any exception or Ctrl-C ends with robot.stop."""
    sim = isinstance(transport, SimTransport)
    period = 1 / profile['rate_hz']
    sign = profile['head_sign']

    def hold(seconds, params=None):
        remaining = seconds
        while remaining > 1e-9:
            tick = min(period, remaining)
            if params:
                transport.send('robot.move', params)
            if sim:
                transport.advance(tick)
            else:
                sleep(tick)
            remaining -= tick

    try:
        for command in commands:
            if on_command:
                on_command(command)
            kind = command['kind']
            if kind == 'move':
                hold(command['seconds'], {k: command[k] for k in ('vx', 'vy', 'vyaw')})
            elif kind == 'head':
                transport.send('robot.head', {'neck_pitch': 0.0, 'head_pitch': command['pitch'] * sign['pitch'],
                                              'head_yaw': command['yaw'] * sign['yaw'], 'head_roll': command['roll'] * sign['roll']})
            elif kind == 'skill':
                transport.send('robot.stop', request=True)
                transport.skill(command['name'])
                hold(command['seconds'])
            elif kind == 'stop':
                transport.send('robot.stop', request=True)
    except BaseException:
        try:
            transport.send('robot.stop', request=True)
        finally:
            raise
