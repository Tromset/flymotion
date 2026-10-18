"""Motion plans: the JSON the model writes, its validation, and its compilation into timed robot commands.

The model only decides *what* to do, in words: "walk forward 0.5 m, turn right, look up, kick". Signs, speeds,
durations and limits come from the robot profile, so a small model never has to do arithmetic or remember that
right is negative, and can never exceed the limits.
"""
import json
import math
from pathlib import Path

ROBOTS = Path(__file__).resolve().parents[1] / 'robots'
ACTIONS = ('walk', 'turn', 'circle', 'head', 'skill', 'wait', 'stop', 'move')
DIRECTIONS = ('forward', 'backward', 'left', 'right')
LOOKS = ('ahead', 'left', 'right', 'up', 'down', 'up-left', 'up-right', 'down-left', 'down-right')
NUMBERS = ('meters', 'degrees', 'seconds', 'vx', 'vy', 'vyaw')
# What each action needs. turn without degrees is a quarter turn; head without degrees uses the profile's angles.
REQUIRED = {'walk': ('direction', 'meters'), 'turn': ('direction',), 'circle': ('direction', 'meters'), 'head': ('look',),
            'skill': ('skill',), 'wait': ('seconds',), 'move': ('seconds',)}
SIDEWAYS = {'walk': DIRECTIONS, 'turn': ('left', 'right'), 'circle': ('left', 'right')}
MAX_REPEAT = 10


class PlanError(ValueError):
    pass


def load_profile(name_or_path):
    path = Path(name_or_path)
    if not path.suffix:
        path = ROBOTS / f'{name_or_path}.json'
    if not path.is_file():
        known = ', '.join(sorted(p.stem for p in ROBOTS.glob('*.json')))
        raise PlanError(f'unknown robot "{name_or_path}" (known: {known})')
    return json.loads(path.read_text())


def schema(profile):
    """JSON schema passed to Ollama's `format`: the reply can only be a well-formed plan."""
    number = {'type': 'number'}
    step = {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': list(ACTIONS)},
        'direction': {'type': 'string', 'enum': list(DIRECTIONS)},
        'look': {'type': 'string', 'enum': list(LOOKS)},
        'skill': {'type': 'string', 'enum': list(profile['skills'])},
        **{name: number for name in NUMBERS},
    }, 'required': ['action']}
    # "repeat" comes before "steps" and is required: the model decides on shapes before writing the steps.
    return {'type': 'object', 'properties': {
        'say': {'type': 'string'},
        'repeat': {'type': 'integer'},
        'steps': {'type': 'array', 'items': step},
    }, 'required': ['say', 'repeat', 'steps']}


def validate(plan, profile):
    """Returns the list of problems in a parsed plan; empty when the plan can be compiled."""
    if not isinstance(plan, dict):
        return ['the reply must be a JSON object with "say" and "steps"']
    errors = []
    if not isinstance(plan.get('say'), str):
        errors.append('"say" must be a string')
    repeat = plan.get('repeat', 1)
    if isinstance(repeat, bool) or not isinstance(repeat, int) or not 1 <= repeat <= MAX_REPEAT:
        errors.append(f'"repeat" must be a whole number from 1 to {MAX_REPEAT}')
    steps = plan.get('steps')
    if not isinstance(steps, list):
        return errors + ['"steps" must be a list']
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            errors.append(f'step {i} must be an object')
            continue
        action = step.get('action')
        if action not in ACTIONS:
            errors.append(f'step {i}: unknown action {action!r} (use one of {", ".join(ACTIONS)})')
            continue
        for name in REQUIRED.get(action, ()):
            if name not in step:
                errors.append(f'step {i}: "{action}" needs "{name}"')
        for name in NUMBERS:
            value = step.get(name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
                errors.append(f'step {i}: "{name}" must be a number')
        if action in SIDEWAYS and 'direction' in step and step['direction'] not in SIDEWAYS[action]:
            errors.append(f'step {i}: "{action}" goes {" or ".join(SIDEWAYS[action])}, not {step["direction"]!r}')
        if action == 'head' and 'look' in step and step['look'] not in LOOKS:
            errors.append(f'step {i}: "look" is one of {", ".join(LOOKS)}')
        if action == 'skill' and 'skill' in step and step['skill'] not in profile['skills']:
            errors.append(f'step {i}: unknown skill {step["skill"]!r} (this robot knows {", ".join(profile["skills"])})')
        if action in ('move', 'wait') and isinstance(step.get('seconds'), (int, float)) and step['seconds'] <= 0:
            errors.append(f'step {i}: "seconds" must be positive')
    return errors


def parse(text, profile):
    try:
        plan = json.loads(text)
    except json.JSONDecodeError as error:
        raise PlanError(f'the reply is not valid JSON ({error.msg})') from None
    errors = validate(plan, profile)
    if errors:
        raise PlanError('; '.join(errors))
    return plan


def _clamp(value, limit):
    return max(-limit, min(limit, value))


def compile_plan(plan, profile):
    """Turns a valid plan into timed commands and the warnings for everything that had to be limited.

    Commands use the robot's frame (x forward, y left, yaw counterclockwise = left):
    {"kind": "move", "vx", "vy", "vyaw", "seconds"}, {"kind": "head", "yaw", "pitch", "roll"} (radians, yaw > 0 left,
    pitch > 0 up), {"kind": "skill", "name", "seconds"}, {"kind": "stop"}. A plan always ends with a stop.
    """
    limits, cruise, achieved, look = profile['limits'], profile['cruise'], profile['achieved'], profile['head_look']
    commands, warnings = [], []
    total = 0.0

    def timed(kind, seconds, **fields):
        nonlocal total
        seconds = float(seconds)
        if seconds > limits['step_seconds']:
            warnings.append(f'{kind} shortened from {seconds:.1f} s to {limits["step_seconds"]} s')
            seconds = float(limits['step_seconds'])
        if total + seconds > limits['plan_seconds']:
            warnings.append(f'plan cut at {limits["plan_seconds"]} s')
            seconds = limits['plan_seconds'] - total
        if seconds > 0:
            commands.append({'kind': kind, **fields, 'seconds': round(seconds, 3)})
            total += seconds

    def amount(step, name, limit_key, default=0.0):
        value = abs(float(step.get(name) or default))
        if value > limits[limit_key]:
            warnings.append(f'{step["action"]} limited from {value:g} to {limits[limit_key]:g} {name}')
            value = float(limits[limit_key])
        return value

    for step in plan['steps'] * plan.get('repeat', 1):
        if total >= limits['plan_seconds']:
            break
        action = step['action']
        get = lambda name: float(step.get(name) or 0)  # noqa: E731
        if action == 'walk':
            meters = amount(step, 'meters', 'meters')
            axis, sign = {'forward': ('vx', 1), 'backward': ('vx', -1), 'left': ('vy', 1), 'right': ('vy', -1)}[step['direction']]
            if meters:
                speed = min(cruise[axis], limits[axis])
                velocity = {'vx': 0.0, 'vy': 0.0, 'vyaw': 0.0, axis: sign * speed}
                timed('move', meters / (speed * achieved[axis]), **velocity)
        elif action == 'turn':
            degrees = amount(step, 'degrees', 'degrees', default=90)
            if degrees:
                speed = min(cruise['vyaw'], limits['vyaw'])
                sign = 1 if step['direction'] == 'left' else -1
                timed('move', math.radians(degrees) / (speed * achieved['vyaw']), vx=0.0, vy=0.0, vyaw=sign * speed)
        elif action == 'circle':
            # One full circle of the given radius: forward at cruise speed while turning at speed / radius.
            radius = max(amount(step, 'meters', 'meters'), 0.15)
            vx = min(cruise['vx'], limits['vx'], limits['vyaw'] * radius)
            sign = 1 if step['direction'] == 'left' else -1
            vyaw = min(vx / radius * achieved['vx'] / achieved['vyaw'], limits['vyaw'])
            timed('move', 2 * math.pi * radius / (vx * achieved['vx']), vx=vx, vy=0.0, vyaw=round(sign * vyaw, 4))
        elif action == 'move':
            velocity = {}
            for axis in ('vx', 'vy', 'vyaw'):
                velocity[axis] = _clamp(get(axis), limits[axis])
                if velocity[axis] != get(axis):
                    warnings.append(f'{axis} limited from {get(axis):g} to {velocity[axis]:g}')
            timed('move', get('seconds'), **velocity)
        elif action == 'head':
            where = step['look']
            yaw = look['yaw_deg'] if 'left' in where else -look['yaw_deg'] if 'right' in where else 0
            pitch = look['pitch_deg'] if 'up' in where else -look['pitch_deg'] if 'down' in where else 0
            if step.get('degrees'):
                magnitude = min(abs(get('degrees')), limits['head_deg'])
                yaw, pitch = math.copysign(magnitude, yaw) if yaw else 0, math.copysign(magnitude, pitch) if pitch else 0
            commands.append({'kind': 'head', 'yaw': round(math.radians(yaw), 4), 'pitch': round(math.radians(pitch), 4), 'roll': 0.0})
        elif action == 'skill':
            timed('skill', profile['skill_seconds'], name=step['skill'])
        elif action == 'wait':
            timed('move', get('seconds'), vx=0.0, vy=0.0, vyaw=0.0)
        elif action == 'stop':
            commands.append({'kind': 'stop'})
    if not commands or commands[-1]['kind'] != 'stop':
        commands.append({'kind': 'stop'})
    return commands, warnings


def duration(commands):
    return sum(c.get('seconds', 0) for c in commands)
