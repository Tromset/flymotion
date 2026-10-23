"""The system prompt, built from a robot profile, and the sentence the bridge says for a plan.
`scripts/gen_modelfiles.py` writes the prompt into the Modelfiles.

The model only writes the actions, their values and their order: the short prompt and the short reply are what
make it fast. The sentence shown to the user ("J'avance de 50 cm, puis je tourne à droite.") is written here.
"""
import json
import re

EXAMPLES = [
    ('Avance de 50 cm puis tourne à droite.',
     {'steps': [{'action': 'walk', 'direction': 'forward', 'value': 0.5}, {'action': 'turn', 'direction': 'right', 'value': 90}]}),
    ('Back up a little, step left 30 cm and look up.',
     {'steps': [{'action': 'walk', 'direction': 'backward', 'value': 0.2}, {'action': 'walk', 'direction': 'left', 'value': 0.3},
                {'action': 'look', 'direction': 'up'}]}),
    ("Fais un triangle d'un mètre de côté.",
     {'repeat': 3, 'steps': [{'action': 'walk', 'direction': 'forward', 'value': 1}, {'action': 'turn', 'direction': 'left', 'value': 120}]}),
    ('Turn around and look down to the left.',
     {'steps': [{'action': 'turn', 'direction': 'left', 'value': 180}, {'action': 'look', 'direction': 'down-left'}]}),
    ('Shoot with your right foot, then pick up the toy.', {'steps': [{'action': 'kick_right'}, {'action': 'ground_pick'}]}),
    ('Recule de 10 cm, trois fois.', {'repeat': 3, 'steps': [{'action': 'walk', 'direction': 'backward', 'value': 0.1}]}),
    ('Saute par la fenêtre.', {'steps': []}),
    ('Kick my sister.', {'steps': []}),
]


def _ordered(plan):
    return {'safe': bool(plan['steps']), 'steps': plan['steps'], 'repeat': plan.get('repeat', 1)}


def system_prompt(profile):
    limits = profile['limits']
    skills = '\n'.join(f'- {name}: {what}' for name, what in profile['skills'].items())
    examples = '\n'.join(f'User: {request}\nYou: {json.dumps(_ordered(plan), ensure_ascii=False)}' for request, plan in EXAMPLES)
    return f"""You are FlyMotion, the motion planner of {profile['title']}. Read the request and write the robot's actions, their values and their order.

Reply with JSON only: {{"safe": true, "steps": [{{"action": ..., "direction": ..., "value": ...}}], "repeat": 1}}
- "safe": false when the robot cannot or must not do it (see the last rule), then "steps": [].
- "steps": the actions of the request, in its order.
- "repeat": how many times to play all the steps. 1, except for shapes and "N times".

Actions:
- walk: direction forward, backward, left or right (sideways); value in meters
- turn: direction left or right, on the spot; value in degrees (90 when not given)
- circle: direction left or right; value = radius in meters
- look: direction ahead, left, right, up, down, up-left, up-right, down-left or down-right
- wait: value in seconds
- stop: stop now (arrête, freeze)
{skills}

Rules:
- gauche = left, droite = right, avant = forward, arrière / recule = backward, haut = up, bas = down.
- Values are positive: the direction gives the way. 1 m = 100 cm. Half turn ("demi-tour", "turn around") = turn 180. Full turn ("tour complet", "sur toi-même") = turn 360. "A little" = 0.2 m or 30 degrees.
- Shapes: one side and one turn, "repeat" = the number of sides. Square 4 and 90, triangle 3 and 120, hexagon 6 and 60. "N times": the steps once, "repeat" = N.
- At most {limits['meters']} m per walk.
- "safe": false and "steps": [] for anything the robot cannot do (jump, fly, climb, run), anything that could hurt a person or an animal or break something (kicks are for balls, never for a person or an animal), and anything that is not a motion (a question, a chat).

Examples:
{examples}"""


FRENCH = re.compile(r"[àâçéèêëîïôûùœ]|\b(avance|recule|tourne|fais|regarde|marche|décale|saute|vole|frappe|ramasse|assieds|"
                    r"le|la|les|de|du|des|et|puis|un|une|à|droite|gauche)\b", re.IGNORECASE)


def language(request):
    return 'fr' if FRENCH.search(request or '') else 'en'


def _number(value, lang):
    text = f'{value:g}'
    return text.replace('.', ',') if lang == 'fr' else text


SIDES = {'fr': {'left': 'à gauche', 'right': 'à droite'}, 'en': {'left': 'left', 'right': 'right'}}
LOOK_WORDS = {'fr': {'ahead': 'droit devant', 'left': 'à gauche', 'right': 'à droite', 'up': 'en haut', 'down': 'en bas',
                     'up-left': 'en haut à gauche', 'up-right': 'en haut à droite', 'down-left': 'en bas à gauche',
                     'down-right': 'en bas à droite'},
              'en': {name: name.replace('-', ' and ') if '-' in name else name for name in
                     ('ahead', 'left', 'right', 'up', 'down', 'up-left', 'up-right', 'down-left', 'down-right')}}


def _distance(meters, lang):
    return f'{round(meters * 100)} cm' if meters < 1 else f'{_number(meters, lang)} m'


def _phrase(step, lang):
    action, way, value = step['action'], step.get('direction'), abs(float(step.get('value') or 0))
    fr = lang == 'fr'
    if action == 'walk':
        if way in ('forward', 'backward'):
            verb = ("j'avance de" if way == 'forward' else 'je recule de') if fr else ('walking forward' if way == 'forward' else 'backing up')
            return f'{verb} {_distance(value, lang)}'
        return f'je me décale de {_distance(value, lang)} {SIDES["fr"][way]}' if fr else f'stepping {way} {_distance(value, lang)}'
    if action == 'turn':
        degrees = value or 90
        if fr:
            return 'je fais demi-tour' if degrees == 180 else f'je tourne {SIDES["fr"][way]}' + ('' if degrees == 90 else f' de {degrees:g} degrés')
        return 'turning around' if degrees == 180 else f'turning {way}' + ('' if degrees == 90 else f' {degrees:g} degrees')
    if action == 'circle':
        return (f'je fais un cercle de {_distance(value, lang)} de rayon {SIDES["fr"][way]}' if fr
                else f'walking a circle of {_distance(value, lang)} radius to the {way}')
    if action == 'look':
        return f'je regarde {LOOK_WORDS["fr"][way]}' if fr else f'looking {LOOK_WORDS["en"][way]}'
    if action == 'wait':
        return f"j'attends {_number(value, lang)} s" if fr else f'waiting {_number(value, lang)} s'
    if action == 'stop':
        return "je m'arrête" if fr else 'stopping'
    return f'je lance {action}' if fr else f'running {action}'


def say(plan, lang='fr'):
    """One sentence for the user, in their language, from the plan alone."""
    if not plan['steps'] or plan.get('safe') is False:
        return 'Je ne peux pas faire ça.' if lang == 'fr' else 'I cannot do that.'
    text = (', puis ' if lang == 'fr' else ', then ').join(_phrase(step, lang) for step in plan['steps'])
    repeat = plan.get('repeat', 1)
    if repeat > 1:
        text += f', {repeat} fois' if lang == 'fr' else f', {repeat} times'
    return text[0].upper() + text[1:] + '.'
