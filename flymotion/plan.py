"""Motion plans: the JSON the model writes, its validation, and its compilation into timed robot commands.

The model reads three things from the request and nothing more: the actions, their value (how far, how much, how
long) and their order, after one yes or no: can the robot do it safely.
{"safe": true, "steps": [{"action": "walk", "direction": "forward", "value": 0.5}, ...], "repeat": 1}
Signs, speeds, durations, limits and the way each action is walked come from the robot profile, so a small model
never has to do arithmetic, and can never ask for a command the robot's gait cannot really follow.
"""
import json
import math
from pathlib import Path

ROBOTS = Path(__file__).resolve().parents[1] / 'robots'
MOTIONS = ('walk', 'turn', 'circle', 'look', 'wait', 'stop')  # plus the skills of the profile, each one an action
DIRECTIONS = ('forward', 'backward', 'left', 'right')
LOOKS = ('ahead', 'left', 'right', 'up', 'down', 'up-left', 'up-right', 'down-left', 'down-right')
# What each action needs, and the directions it takes. The value is meters for walk and circle (radius), degrees for
# turn and look, seconds for wait. turn without a value is a quarter turn; look without a value uses the profile's angles.
REQUIRED = {'walk': ('direction', 'value'), 'turn': ('direction',), 'circle': ('direction', 'value'), 'look': ('direction',),
            'wait': ('value',)}
WAYS = {'walk': DIRECTIONS, 'turn': ('left', 'right'), 'circle': ('left', 'right'), 'look': LOOKS}
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


def actions(profile):
    return [*MOTIONS, *profile['skills']]


def gait(profile):
    """How the robot's walking policy really behaves: below min_speed an axis only steps in place, every start from
    standing takes start_seconds before the robot moves, and a walk shorter than min_meters is a shuffle, not a step."""
    measured = profile.get('gait', {})
    return {'start_seconds': measured.get('start_seconds', 0.0), 'min_meters': measured.get('min_meters', 0.05),
            'min_speed': {'vx': 0.0, 'vy': 0.0, 'vyaw': 0.0, **measured.get('min_speed', {})}}


def schema(profile):
    """JSON schema passed to Ollama's `format`: the reply can only be a well-formed plan."""
    motion = {'type': 'object', 'properties': {
        'action': {'type': 'string', 'enum': list(MOTIONS)},
        'direction': {'type': 'string', 'enum': list(dict.fromkeys(DIRECTIONS + LOOKS))},
        'value': {'type': 'number'},
    }, 'required': ['action']}
    # A skill is its name alone: no direction or value tokens to write.
    skill = {'type': 'object', 'properties': {'action': {'type': 'string', 'enum': list(profile['skills'])}},
             'required': ['action'], 'additionalProperties': False}
    # "safe" first: with no sentence to write any more, the model needs this one decision before it picks actions, or
    # "kick the cat" becomes kick_left. Then the steps, in the order of the request, then how many times to play
    # them: a small model that decides "repeat" first tends to also write the repeated steps out.
    return {'type': 'object', 'properties': {
        'safe': {'type': 'boolean'},
        'steps': {'type': 'array', 'items': {'anyOf': [skill, motion]}},
        'repeat': {'type': 'integer'},
    }, 'required': ['safe', 'steps', 'repeat']}


def validate(plan, profile):
    """Returns the list of problems in a parsed plan; empty when the plan can be compiled."""
    if not isinstance(plan, dict):
        return ['the reply must be a JSON object with "steps" and "repeat"']
    errors = []
    if 'say' in plan and not isinstance(plan['say'], str):
        errors.append('"say" must be a string')
    if 'safe' in plan and not isinstance(plan['safe'], bool):
        errors.append('"safe" must be true or false')
    repeat = plan.get('repeat', 1)
    if isinstance(repeat, bool) or not isinstance(repeat, int) or not 1 <= repeat <= MAX_REPEAT:
        errors.append(f'"repeat" must be a whole number from 1 to {MAX_REPEAT}')
    steps = plan.get('steps')
    if not isinstance(steps, list):
        return errors + ['"steps" must be a list']
    if plan.get('safe') is False:
        return errors  # refused: the steps are dropped
    known, min_meters = actions(profile), gait(profile)['min_meters']
    for i, step in enumerate(steps, 1):
        if not isinstance(step, dict):
            errors.append(f'step {i} must be an object')
            continue
        action = step.get('action')
        if action not in known:
            errors.append(f'step {i}: unknown action {action!r} (use one of {", ".join(known)})')
            continue
        for name in REQUIRED.get(action, ()):
            if name not in step:
                errors.append(f'step {i}: "{action}" needs "{name}"')
        value = step.get('value')
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)):
            errors.append(f'step {i}: "value" must be a number')
            value = None
        if action in WAYS and 'direction' in step and step['direction'] not in WAYS[action]:
            errors.append(f'step {i}: "{action}" goes {" or ".join(WAYS[action])}, not {step["direction"]!r}')
        if action == 'wait' and value is not None and value <= 0:
            errors.append(f'step {i}: "wait" needs a positive number of seconds')
        if action == 'walk' and value is not None and abs(value) < min_meters:
            # A tiny walk is not a step: the robot would only shuffle in place.
            errors.append(f'step {i}: walk at least {min_meters:g} m, or leave the step out')
    return errors


def parse(text, profile):
    try:
        plan = json.loads(text)
    except json.JSONDecodeError as error:
        raise PlanError(f'the reply is not valid JSON ({error.msg})') from None
    errors = validate(plan, profile)
    if errors:
        raise PlanError('; '.join(errors))
    if plan.get('safe') is False:
        plan['steps'] = []
    return plan


def compile_plan(plan, profile):
    """Turns a valid plan into timed commands and the warnings for everything that had to be limited or changed.

    Commands use the robot's frame (x forward, y left, yaw counterclockwise = left):
    {"kind": "move", "vx", "vy", "vyaw", "seconds"}, {"kind": "head", "yaw", "pitch", "roll"} (radians, yaw > 0 left,
    pitch > 0 up), {"kind": "skill", "name", "seconds"}, {"kind": "stop"}. A plan always ends with a stop.

    Every move is one the gait really walks: its speed is above the profile's gait threshold, and a move from
    standing lasts the start-up time on top of the distance. A direction the robot cannot walk fast enough is
    walked forward, turning to face it first and back after.
    """
    limits, cruise, achieved, look = profile['limits'], profile['cruise'], profile['achieved'], profile['head_look']
    walking = gait(profile)
    minimum = walking['min_speed']
    commands, warnings = [], []
    total = 0.0
    moving = False  # the walker is already stepping: no start-up time

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

    def move(seconds, vx=0.0, vy=0.0, vyaw=0.0):
        nonlocal moving
        if not moving:
            seconds += walking['start_seconds']
            moving = True
        timed('move', seconds, vx=vx, vy=vy, vyaw=vyaw)

    def amount(step, limit_key, default=0.0):
        value = abs(float(step.get('value') or default))
        if value > limits[limit_key]:
            warnings.append(f'{step["action"]} limited from {value:g} to {limits[limit_key]:g}')
            value = float(limits[limit_key])
        return value

    def top_speed(axis, sign):
        limit = limits.get('vx_backward', limits['vx']) if (axis, sign) == ('vx', -1) else limits[axis]
        return min(cruise[axis], limit)

    def turn(degrees, sign):
        speed = top_speed('vyaw', 1)
        move(math.radians(degrees) / (speed * achieved['vyaw']), vyaw=sign * speed)

    def walk(direction, meters):
        axis, sign = {'forward': ('vx', 1), 'backward': ('vx', -1), 'left': ('vy', 1), 'right': ('vy', -1)}[direction]
        speed = top_speed(axis, sign)
        if speed >= minimum[axis] and speed > 0:
            move(meters / (speed * achieved[axis]), **{axis: sign * speed})
        elif top_speed('vx', 1) >= minimum['vx']:
            # Below the gait threshold the robot only steps in place: face that way and walk forward instead.
            warnings.append(f'this robot cannot walk {direction} for real: it turns, walks forward and turns back')
            degrees, side = {'backward': (180, 1), 'left': (90, 1), 'right': (90, -1)}[direction]
            turn(degrees, side)
            walk('forward', meters)
            turn(degrees, -side)
        else:
            warnings.append(f'walk {direction} skipped: this robot cannot walk')

    for step in (plan['steps'] if plan.get('safe', True) else []) * plan.get('repeat', 1):
        if total >= limits['plan_seconds']:
            break
        action = step['action']
        if action == 'walk':
            meters = amount(step, 'meters')
            if meters:
                walk(step['direction'], meters)
        elif action == 'turn':
            degrees = amount(step, 'degrees', default=90)
            if degrees:
                turn(degrees, 1 if step['direction'] == 'left' else -1)
        elif action == 'circle':
            # One full circle: forward while turning so that speed / turn rate is the radius, both above the gait threshold.
            radius = max(amount(step, 'meters'), 0.15)
            sign = 1 if step['direction'] == 'left' else -1
            ratio = achieved['vx'] / achieved['vyaw']
            vx = min(top_speed('vx', 1), limits['vyaw'] * radius / ratio)
            if vx < minimum['vx']:
                vx = minimum['vx']
                radius = vx * ratio / limits['vyaw']
                warnings.append(f'circle widened to a radius of {radius:.2f} m: a tighter one is too slow to walk')
            move(2 * math.pi * radius / (vx * achieved['vx']), vx=vx, vyaw=round(sign * min(vx * ratio / radius, limits['vyaw']), 4))
        elif action == 'look':
            where = step['direction']
            yaw = look['yaw_deg'] if 'left' in where else -look['yaw_deg'] if 'right' in where else 0
            pitch = look['pitch_deg'] if 'up' in where else -look['pitch_deg'] if 'down' in where else 0
            if step.get('value'):
                magnitude = min(abs(float(step['value'])), limits['head_deg'])
                yaw, pitch = math.copysign(magnitude, yaw) if yaw else 0, math.copysign(magnitude, pitch) if pitch else 0
            commands.append({'kind': 'head', 'yaw': round(math.radians(yaw), 4), 'pitch': round(math.radians(pitch), 4), 'roll': 0.0})
        elif action == 'wait':
            timed('move', float(step['value']), vx=0.0, vy=0.0, vyaw=0.0)
            moving = False
        elif action == 'stop':
            commands.append({'kind': 'stop'})
            moving = False
        else:
            timed('skill', profile['skill_seconds'], name=action)
            moving = False
    if not commands or commands[-1]['kind'] != 'stop':
        commands.append({'kind': 'stop'})
    return commands, warnings


def duration(commands):
    return sum(c.get('seconds', 0) for c in commands)
