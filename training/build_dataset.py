#!/usr/bin/env python3
"""Builds the FlyMotion instruction dataset: French and English requests paired with the plan the robot should run.

Every target plan is validated, compiled with the robot profile and played in the simulator with the gait of Pollen's
simulator, where it must walk for real (no time stepping in place). No request of the benchmark is used, so the
benchmark stays a fair test of a model trained on this data.
  python3 training/build_dataset.py [--count 3000] [--out data/flymotion-commands]
Writes train.jsonl and valid.jsonl in the chat format ({"messages": [system, user, assistant]}) used by TRL and mlx-lm.
"""
import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from flymotion.plan import compile_plan, load_profile, validate  # noqa: E402
from flymotion.prompt import system_prompt  # noqa: E402
from flymotion.robot import SimTransport, execute  # noqa: E402

FR_NUMBERS = {1: 'un', 2: 'deux', 3: 'trois', 4: 'quatre', 5: 'cinq', 10: 'dix', 20: 'vingt', 30: 'trente', 50: 'cinquante'}
EN_NUMBERS = {1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five', 10: 'ten', 20: 'twenty', 30: 'thirty', 50: 'fifty'}


def distance(rng, lang):
    """A distance in meters and one way of saying it."""
    meters = rng.choice([0.1, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6, 0.75, 0.8, 1, 1, 1.2, 1.5, 2, 2.5, 3])
    cm = round(meters * 100)
    numbers = FR_NUMBERS if lang == 'fr' else EN_NUMBERS
    options = [f'{cm} cm']
    if lang == 'fr':
        options += [f'{cm} centimètres', f'{meters:g} m'.replace('.', ',')]
        if meters == 1:
            options += ['un mètre', '1 mètre']
        elif meters == 0.5:
            options += ['un demi-mètre', '50 centimètres']
        elif meters == int(meters):
            options += [f'{numbers.get(int(meters), int(meters))} mètres', f'{int(meters)} mètres']
    else:
        options += [f'{cm} centimeters', f'{meters:g} m']
        if meters == 1:
            options += ['one meter', '1 meter', 'a meter']
        elif meters == 0.5:
            options += ['half a meter']
        elif meters == int(meters):
            options += [f'{numbers.get(int(meters), int(meters))} meters', f'{int(meters)} meters']
    return meters, rng.choice(options)


def angle(rng, lang):
    degrees = rng.choice([15, 20, 30, 45, 45, 60, 90, 90, 90, 120, 135, 180, 270, 360])
    named = {90: ('d\'un quart de tour', 'a quarter turn'), 180: ('d\'un demi-tour', 'a half turn'), 360: ('d\'un tour complet', 'a full turn')}
    if degrees in named and rng.random() < 0.5:
        return degrees, named[degrees][0 if lang == 'fr' else 1]
    return degrees, (f'de {degrees} degrés' if lang == 'fr' else f'{degrees} degrees')


def walk(rng, lang):
    meters, said = distance(rng, lang)
    forward = rng.random() < 0.7
    if lang == 'fr':
        text = rng.choice(['avance de {d}', 'marche {d} en avant', 'va tout droit sur {d}', 'avance {d}']) if forward else \
            rng.choice(['recule de {d}', 'recule {d}', 'fais {d} en arrière', 'marche à reculons sur {d}'])
    else:
        text = rng.choice(['walk forward {d}', 'go forward {d}', 'move ahead {d}', 'walk {d} straight ahead']) if forward else \
            rng.choice(['walk back {d}', 'back up {d}', 'go backwards {d}', 'reverse {d}'])
    return text.format(d=said), [{'action': 'walk', 'direction': 'forward' if forward else 'backward', 'value': meters}]


def strafe(rng, lang):
    meters, said = distance(rng, lang)
    meters = min(meters, 1.5)
    if 'cm' not in said and 'centi' not in said:
        said = f'{round(meters * 100)} cm'
    left = rng.random() < 0.5
    side = ('gauche' if left else 'droite') if lang == 'fr' else ('left' if left else 'right')
    if lang == 'fr':
        text = rng.choice(['décale-toi de {d} vers la {s}', 'fais un pas chassé de {d} à {s}', 'glisse à {s} sur {d}', 'marche de côté vers la {s} sur {d}'])
    else:
        text = rng.choice(['step {s} by {d}', 'strafe {s} {d}', 'sidestep {d} to the {s}', 'shuffle {d} to the {s}', 'walk sideways {d} to the {s}'])
    return text.format(d=said, s=side), [{'action': 'walk', 'direction': 'left' if left else 'right', 'value': meters}]


def turn(rng, lang):
    left = rng.random() < 0.5
    direction = 'left' if left else 'right'
    if rng.random() < 0.3:  # no number: 90 degrees, or a named half turn
        if rng.random() < 0.3:
            return (rng.choice(['fais demi-tour', 'retourne-toi']) if lang == 'fr' else rng.choice(['turn around', 'do a U-turn'])), \
                [{'action': 'turn', 'direction': 'left', 'value': 180}]
        text = (f'tourne à {"gauche" if left else "droite"}' if lang == 'fr' else f'turn {direction}')
        return text, [{'action': 'turn', 'direction': direction, 'value': 90}]
    degrees, said = angle(rng, lang)
    if lang == 'fr':
        text = rng.choice(['tourne à {s} {a}', 'pivote {a} vers la {s}', 'tourne-toi {a} sur la {s}']).format(s='gauche' if left else 'droite', a=said)
    else:
        text = rng.choice(['turn {s} {a}', 'rotate {a} to the {s}', 'spin {a} {s}']).format(s=direction, a=said)
    return text, [{'action': 'turn', 'direction': direction, 'value': degrees}]


HEAD = {
    'fr': [('regarde à gauche', 'left'), ('regarde à droite', 'right'), ('lève la tête', 'up'), ('regarde en l\'air', 'up'),
           ('baisse la tête', 'down'), ('regarde par terre', 'down'), ('regarde en haut à gauche', 'up-left'),
           ('regarde en haut à droite', 'up-right'), ('regarde en bas à gauche', 'down-left'), ('regarde droit devant', 'ahead'),
           ('tourne la tête vers la droite', 'right'), ('tourne la tête à gauche', 'left'), ('regarde le plafond', 'up')],
    'en': [('look left', 'left'), ('look right', 'right'), ('look up', 'up'), ('look at the ceiling', 'up'), ('look down', 'down'),
           ('look at the floor', 'down'), ('look up and to the right', 'up-right'), ('look up to the left', 'up-left'),
           ('look down to the left', 'down-left'), ('look straight ahead', 'ahead'), ('turn your head to the left', 'left'),
           ('turn your head right', 'right')],
}


def head(rng, lang):
    text, look = rng.choice(HEAD[lang])
    return text, [{'action': 'look', 'direction': look}]


SKILLS = {
    'fr': [('frappe le ballon du pied gauche', 'kick_left'), ('tire avec le pied gauche', 'kick_left'), ('shoote du gauche', 'kick_left'),
           ('frappe le ballon du pied droit', 'kick_right'), ('tire du pied droit', 'kick_right'), ('donne un coup de pied droit dans la balle', 'kick_right'),
           ('fais une galipette', 'roulade'), ('fais une roulade avant', 'roulade'), ('ramasse l\'objet par terre', 'ground_pick'),
           ('attrape ce qui est au sol', 'ground_pick'), ('ramasse le jouet', 'ground_pick'), ('assieds-toi', 'sit_toggle'),
           ('relève-toi', 'sit_toggle'), ('lève-toi', 'sit_toggle'), ('fais la révérence', 'polite-bow'), ('salue le public', 'polite-bow'),
           ('dis bonjour en t\'inclinant', 'polite-bow')],
    'en': [('kick the ball with your left foot', 'kick_left'), ('shoot with your left', 'kick_left'), ('kick with the right foot', 'kick_right'),
           ('shoot the ball with your right foot', 'kick_right'), ('do a forward roll', 'roulade'), ('do a somersault', 'roulade'),
           ('pick up the thing on the floor', 'ground_pick'), ('grab what is on the ground', 'ground_pick'), ('pick that up', 'ground_pick'),
           ('sit down', 'sit_toggle'), ('stand up', 'sit_toggle'), ('take a bow', 'polite-bow'), ('greet everyone with a bow', 'polite-bow')],
}


def skill(rng, lang):
    text, name = rng.choice(SKILLS[lang])
    return text, [{'action': name}]


def wait(rng, lang):
    seconds = rng.choice([1, 2, 3, 5])
    return (f'attends {seconds} seconde{"s" if seconds > 1 else ""}' if lang == 'fr' else f'wait {seconds} second{"s" if seconds > 1 else ""}'), \
        [{'action': 'wait', 'value': seconds}]


SHAPES = {3: ('triangle', 'triangle', 120), 4: ('carré', 'square', 90), 6: ('hexagone', 'hexagon', 60)}


def polygon(rng, lang):
    """A polygon is one side and one turn, repeated."""
    meters, said = distance(rng, lang)
    meters = min(meters, 1)
    sides = rng.choice([3, 4, 4, 4, 6])
    fr, en, degrees = SHAPES[sides]
    left = rng.random() < 0.6
    if lang == 'fr':
        text = f'fais un {fr} de {said} de côté' + ('' if left else ' en tournant à droite')
    else:
        text = f'walk a {en} with {said} sides' + ('' if left else ', turning right')
    steps = [{'action': 'walk', 'direction': 'forward', 'value': meters}, {'action': 'turn', 'direction': 'left' if left else 'right', 'value': degrees}]
    return text, steps, sides


def circle(rng, lang):
    radius = rng.choice([0.3, 0.5, 1])
    left = rng.random() < 0.5
    said = f'{round(radius * 100)} cm'
    text = (f'fais un cercle de {said} de rayon vers la {"gauche" if left else "droite"}' if lang == 'fr'
            else f'walk a circle of radius {said} to the {"left" if left else "right"}')
    return text, [{'action': 'circle', 'direction': 'left' if left else 'right', 'value': radius}]


REFUSALS = {
    'fr': ['saute sur le canapé', 'vole jusqu\'au plafond', 'monte les escaliers en courant', 'frappe le chien', 'donne un coup de pied à mon frère',
           'jette-toi dans la piscine', 'porte ce sac de courses', 'grimpe sur l\'armoire', 'casse le vase', 'cours à 10 km/h',
           'fais un salto arrière', 'pousse le verre de la table'],
    'en': ['jump off the table', 'fly to the kitchen', 'climb the stairs', 'kick the dog', 'hit my sister', 'jump into the pool',
           'carry my backpack', 'run as fast as you can', 'knock the glass off the table', 'break the window', 'do a backflip'],
}
CHAT = {'fr': ['quelle heure est-il ?', 'comment tu t\'appelles ?', 'tu es fatigué ?', 'raconte-moi une blague'],
        'en': ['what time is it?', 'what is your name?', 'are you tired?', 'tell me a joke']}
STOPS = {'fr': ['stop', 'arrête-toi', 'stop !', 'arrête tout', 'ne bouge plus'], 'en': ['stop', 'freeze', 'stop now', 'halt', 'stop moving']}

SINGLE = [walk, walk, walk, strafe, strafe, turn, turn, turn, head, skill, skill]
JOIN = {'fr': [', puis ', ' puis ', ' et ensuite ', ', ensuite ', ' et '], 'en': [', then ', ' then ', ' and then ', ', and ', ' and ']}


def sample(rng):
    """A request and the plan the model should write for it: the actions, their values, their order."""
    lang = 'fr' if rng.random() < 0.5 else 'en'
    roll = rng.random()
    if roll < 0.06:
        return lang, rng.choice(REFUSALS[lang]), {'steps': []}
    if roll < 0.09:
        return lang, rng.choice(CHAT[lang]), {'steps': []}
    if roll < 0.12:
        return lang, rng.choice(STOPS[lang]), {'steps': [{'action': 'stop'}]}
    if roll < 0.16:
        request, steps, sides = polygon(rng, lang)
        return lang, request, {'steps': steps, 'repeat': sides}
    if roll < 0.18:
        request, steps = circle(rng, lang)
        return lang, request, {'steps': steps}
    parts, steps = [], []
    for i in range(rng.choice([1, 1, 2, 2, 3])):
        text, more = (wait if i and rng.random() < 0.1 else rng.choice(SINGLE))(rng, lang)
        parts.append(text)
        steps += more
    request = parts[0]
    for part in parts[1:]:
        request += rng.choice(JOIN[lang]) + part
    request = request[0].upper() + request[1:] + rng.choice(['.', '', ' !' if lang == 'fr' else '!', '.'])
    return lang, request, {'steps': steps}


def build(count, seed, profile):
    rng = random.Random(seed)
    bench = {c['request'].lower().strip(' .!?') for c in json.loads((ROOT / 'bench/cases.json').read_text())}
    prompt = system_prompt(profile)
    gaited = load_profile('microduck-sim')  # the strictest gait we have
    rows, seen = [], set()
    while len(rows) < count:
        lang, request, plan = sample(rng)
        key = request.lower().strip(' .!?')
        if key in bench or key in seen:
            continue
        plan = {'safe': bool(plan['steps']), 'steps': plan['steps'], 'repeat': plan.get('repeat', 1)}  # the schema's order
        assert not validate(plan, profile), (request, plan, validate(plan, profile))
        compile_plan(plan, profile)
        commands, _ = compile_plan(plan, gaited)
        sim = SimTransport(gaited)
        execute(commands, sim, gaited)
        assert not sim.in_place, (request, plan, sim.in_place)
        seen.add(key)
        rows.append({'messages': [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': request},
                                  {'role': 'assistant', 'content': json.dumps(plan, ensure_ascii=False)}], 'lang': lang})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--count', type=int, default=3000)
    parser.add_argument('--valid', type=float, default=0.1)
    parser.add_argument('--seed', type=int, default=20261018)
    parser.add_argument('--robot', default='microduck')
    parser.add_argument('--out', default=str(ROOT / 'data/flymotion-commands'))
    args = parser.parse_args()
    rows = build(args.count, args.seed, load_profile(args.robot))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cut = int(len(rows) * (1 - args.valid))
    for name, part in (('train', rows[:cut]), ('valid', rows[cut:])):
        with (out / f'{name}.jsonl').open('w') as file:
            for row in part:
                file.write(json.dumps(row, ensure_ascii=False) + '\n')
    print(f'{len(rows)} examples ({cut} train, {len(rows) - cut} valid) in {out}')


if __name__ == '__main__':
    main()
