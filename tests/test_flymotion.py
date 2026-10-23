import json
import math
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'bench'))
sys.path.insert(0, str(ROOT / 'training'))

from flymotion.plan import PlanError, compile_plan, duration, load_profile, parse, schema, validate  # noqa: E402
from flymotion.planner import Planner  # noqa: E402
from flymotion.prompt import EXAMPLES, language, say, system_prompt  # noqa: E402
from flymotion.robot import LineTransport, SimTransport, execute  # noqa: E402
from flymotion.serve import Server  # noqa: E402
import build_dataset  # noqa: E402
import run as bench  # noqa: E402

PROFILE = load_profile('microduck')
SIM = load_profile('microduck-sim')


def simulate(steps, profile=PROFILE, repeat=1):
    commands, warnings = compile_plan({'repeat': repeat, 'steps': steps}, profile)
    sim = SimTransport(profile)
    execute(commands, sim, profile)
    return sim, commands, warnings


class PlanTest(unittest.TestCase):
    def test_schema_is_actions_values_and_order(self):
        plan = schema(PROFILE)
        skill, motion = plan['properties']['steps']['items']['anyOf']
        self.assertEqual(list(motion['properties']), ['action', 'direction', 'value'])
        self.assertEqual(motion['properties']['action']['enum'], ['walk', 'turn', 'circle', 'look', 'wait', 'stop'])
        self.assertEqual(skill['properties']['action']['enum'], list(PROFILE['skills']))
        self.assertFalse(skill['additionalProperties'])  # a skill is its name alone
        # One safety decision, then the steps in the request's order, then how many times to play them. Nothing else.
        self.assertEqual(list(plan['properties']), ['safe', 'steps', 'repeat'])
        self.assertEqual(plan['required'], ['safe', 'steps', 'repeat'])

    def test_an_unsafe_plan_drops_its_steps(self):
        plan = parse('{"safe": false, "steps": [{"action": "kick_left"}, {"action": "fly"}], "repeat": 1}', PROFILE)
        self.assertEqual(plan['steps'], [])
        self.assertEqual(compile_plan({'safe': False, 'steps': [{'action': 'kick_left'}]}, PROFILE)[0], [{'kind': 'stop'}])
        self.assertEqual(say({'safe': False, 'steps': [{'action': 'kick_left'}]}, 'en'), 'I cannot do that.')
        self.assertIn('true or false', validate({'safe': 'no', 'steps': []}, PROFILE)[0])

    def test_validate_names_each_problem(self):
        self.assertEqual(validate({'steps': [{'action': 'walk', 'direction': 'left', 'value': 1}]}, PROFILE), [])
        errors = validate({'steps': [{'action': 'jump'}, {'action': 'walk', 'direction': 'forward'}, {'action': 'dance'},
                                     {'action': 'turn', 'direction': 'left', 'value': '90'}, {'action': 'wait', 'value': 0},
                                     {'action': 'turn', 'direction': 'forward'}, {'action': 'look'},
                                     {'action': 'walk', 'direction': 'forward', 'value': 0.01}]}, PROFILE)
        self.assertEqual(len(errors), 8, errors)
        self.assertIn('unknown action', errors[0])
        self.assertIn('needs "value"', errors[1])
        self.assertIn('unknown action', errors[2])
        self.assertIn('must be a number', errors[3])
        self.assertIn('positive', errors[4])
        self.assertIn('left or right', errors[5])
        self.assertIn('needs "direction"', errors[6])
        self.assertIn('walk at least', errors[7])  # a shuffle is not a step
        self.assertTrue(validate([], PROFILE))
        self.assertTrue(validate({'steps': [], 'repeat': 50}, PROFILE))
        self.assertTrue(validate({'steps': [], 'repeat': 1.5}, PROFILE))
        self.assertTrue(validate({'steps': [{'action': 'wait', 'value': float('nan')}]}, PROFILE))
        # Raw velocities are not an action any more: the model cannot make the robot shake on the spot.
        self.assertIn('unknown action', validate({'steps': [{'action': 'move', 'vx': 0.01, 'seconds': 5}]}, PROFILE)[0])

    def test_parse_rejects_invalid_json(self):
        with self.assertRaises(PlanError):
            parse('{"steps": [', PROFILE)

    def test_walk_turn_and_strafe_reach_the_asked_pose(self):
        sim, _, warnings = simulate([{'action': 'walk', 'direction': 'forward', 'value': 1}, {'action': 'turn', 'direction': 'right'},
                                     {'action': 'walk', 'direction': 'forward', 'value': 0.5}, {'action': 'walk', 'direction': 'left', 'value': 0.2},
                                     {'action': 'walk', 'direction': 'backward', 'value': -0.1}])
        state = sim.state()
        self.assertEqual(warnings, [])
        self.assertAlmostEqual(state['x'], 1.2, delta=0.02)
        self.assertAlmostEqual(state['y'], -0.4, delta=0.02)  # the sign of the value is ignored: direction wins
        self.assertAlmostEqual(state['heading'], -90, delta=1)
        self.assertEqual(state['in_place'], 0)

    def test_skills_are_actions(self):
        sim, commands, _ = simulate([{'action': 'kick_left'}, {'action': 'sit_toggle'}])
        self.assertEqual([c['kind'] for c in commands], ['skill', 'skill', 'stop'])
        self.assertEqual(sim.skills, ['kick_left', 'sit_toggle'])

    def test_a_repeated_side_and_turn_draws_a_closed_square(self):
        sim, commands, _ = simulate([{'action': 'walk', 'direction': 'forward', 'value': 0.5}, {'action': 'turn', 'direction': 'right', 'value': 90}], repeat=4)
        self.assertEqual(len(commands), 9)
        self.assertAlmostEqual(sim.state()['x'], 0, delta=0.02)
        self.assertAlmostEqual(sim.state()['y'], 0, delta=0.02)

    def test_a_circle_comes_back_to_the_start_even_on_a_weak_turner(self):
        for achieved in ({'vx': 1.0, 'vy': 1.0, 'vyaw': 1.0}, {'vx': 0.8, 'vy': 1.0, 'vyaw': 0.6}):
            profile = dict(PROFILE, achieved=achieved)
            sim, commands, _ = simulate([{'action': 'circle', 'direction': 'right', 'value': 0.5}], profile)
            self.assertAlmostEqual(math.hypot(sim.state()['x'], sim.state()['y']), 0, delta=0.03)
            self.assertAlmostEqual(sim.state()['heading'] % 360, 0, delta=2)
            self.assertLess(commands[0]['vyaw'], 0)

    def test_looks_map_to_head_angles(self):
        commands, _ = compile_plan({'steps': [{'action': 'look', 'direction': 'down-right'}, {'action': 'look', 'direction': 'up', 'value': 90}]}, PROFILE)
        self.assertEqual(commands[0], {'kind': 'head', 'yaw': round(math.radians(-30), 4), 'pitch': round(math.radians(-20), 4), 'roll': 0.0})
        self.assertEqual((commands[1]['yaw'], commands[1]['pitch']), (0, round(math.radians(30), 4)))

    def test_limits_are_enforced_and_reported(self):
        _, commands, warnings = simulate([{'action': 'wait', 'value': 100}, {'action': 'look', 'direction': 'left', 'value': 90},
                                          {'action': 'walk', 'direction': 'forward', 'value': 50}])
        self.assertEqual(commands[0]['seconds'], PROFILE['limits']['step_seconds'])
        self.assertAlmostEqual(commands[1]['yaw'], math.radians(30), places=3)
        self.assertLessEqual(duration(commands), PROFILE['limits']['plan_seconds'])
        self.assertEqual(commands[-1], {'kind': 'stop'})
        self.assertEqual(len(warnings), 2, warnings)  # the wait and the walk

    def test_slow_robots_walk_longer(self):
        slow = dict(PROFILE, achieved={'vx': 0.5, 'vy': 1.0, 'vyaw': 0.29})
        sim, commands, _ = simulate([{'action': 'walk', 'direction': 'forward', 'value': 1}, {'action': 'turn', 'direction': 'left'}], slow)
        self.assertAlmostEqual(commands[0]['seconds'], 10, places=2)
        self.assertAlmostEqual(sim.state()['x'], 1, delta=0.02)
        self.assertAlmostEqual(sim.state()['heading'], 90, delta=1)

    def test_an_empty_plan_only_stops(self):
        self.assertEqual(compile_plan({'steps': []}, PROFILE)[0], [{'kind': 'stop'}])

    def test_prompt_examples_are_valid_plans(self):
        for request, plan in EXAMPLES:
            self.assertEqual(validate(plan, PROFILE), [], request)
        prompt = system_prompt(PROFILE)
        for skill in PROFILE['skills']:
            self.assertIn(skill, prompt)

    def test_unknown_robot(self):
        with self.assertRaises(PlanError):
            load_profile('r2d2')


class GaitTest(unittest.TestCase):
    """The robot must walk for real: a plan that only makes it shake on the spot never passes for a walk."""

    def test_commands_below_the_gait_threshold_step_in_place(self):
        sim = SimTransport(SIM)
        execute([{'kind': 'move', 'vx': 0.1, 'vy': 0.0, 'vyaw': 0.0, 'seconds': 5}, {'kind': 'stop'}], sim, SIM)
        self.assertEqual(sim.state()['x'], 0)
        self.assertGreater(sim.state()['in_place'], 3)
        self.assertTrue(any('stepped in place' in f for f in bench.check({'x': 0}, {'steps': [{}]}, sim.state(), [])))

    def test_the_simulator_cannot_walk_backward_so_the_duck_turns_around(self):
        sim, commands, warnings = simulate([{'action': 'walk', 'direction': 'backward', 'value': 0.5}], SIM)
        self.assertIn('cannot walk backward', warnings[0])
        self.assertTrue(all(c['vx'] >= SIM['gait']['min_speed']['vx'] for c in commands if c['kind'] == 'move' and c['vx']))
        state = sim.state()
        self.assertAlmostEqual(state['x'], -0.5, delta=0.02)
        self.assertAlmostEqual(bench.angle_gap(state['heading'], 0), 0, delta=1)
        self.assertEqual(state['in_place'], 0)

    def test_every_start_from_standing_includes_the_start_up(self):
        sim, commands, _ = simulate([{'action': 'walk', 'direction': 'forward', 'value': 0.5}, {'action': 'turn', 'direction': 'left'},
                                     {'action': 'wait', 'value': 1}, {'action': 'walk', 'direction': 'forward', 'value': 0.5}], SIM)
        start = SIM['gait']['start_seconds']
        self.assertAlmostEqual(commands[0]['seconds'], 0.5 / (0.25 * 0.44) + start, places=2)
        self.assertAlmostEqual(commands[1]['seconds'], math.radians(90) / 0.26, places=2)  # already walking
        self.assertAlmostEqual(commands[3]['seconds'], 0.5 / (0.25 * 0.44) + start, places=2)  # after the wait
        self.assertAlmostEqual(sim.state()['x'], 0.5, delta=0.02)
        self.assertAlmostEqual(sim.state()['y'], 0.5, delta=0.02)

    def test_a_tight_circle_widens_to_stay_above_the_threshold(self):
        sim, commands, warnings = simulate([{'action': 'circle', 'direction': 'left', 'value': 0.2}], SIM)
        self.assertGreaterEqual(commands[0]['vx'], SIM['gait']['min_speed']['vx'])
        self.assertIn('widened', warnings[0])
        self.assertAlmostEqual(math.hypot(sim.state()['x'], sim.state()['y']), 0, delta=0.03)
        self.assertEqual(sim.state()['in_place'], 0)

    def test_every_profile_walks_every_prompt_example_for_real(self):
        for path in sorted((ROOT / 'robots').glob('*.json')):
            profile = load_profile(path.stem)
            for request, plan in EXAMPLES:
                sim, _, _ = simulate(plan['steps'], profile, plan.get('repeat', 1))
                self.assertEqual(sim.state()['in_place'], 0, (path.stem, request))


class SayTest(unittest.TestCase):
    def test_the_bridge_writes_the_sentence_in_the_user_language(self):
        plan = {'repeat': 4, 'steps': [{'action': 'walk', 'direction': 'forward', 'value': 0.5}, {'action': 'turn', 'direction': 'right'}]}
        self.assertEqual(say(plan, 'fr'), "J'avance de 50 cm, puis je tourne à droite, 4 fois.")
        self.assertEqual(say(plan, 'en'), 'Walking forward 50 cm, then turning right, 4 times.')
        self.assertEqual(say({'steps': [{'action': 'look', 'direction': 'up-left'}, {'action': 'kick_left'}]}, 'en'),
                         'Looking up and left, then running kick_left.')
        self.assertEqual(say({'steps': []}, 'fr'), 'Je ne peux pas faire ça.')

    def test_language(self):
        self.assertEqual(language('Avance de 50 cm.'), 'fr')
        self.assertEqual(language('Fais demi-tour'), 'fr')
        self.assertEqual(language('Walk forward two meters.'), 'en')
        self.assertEqual(language('Kick the cat.'), 'en')


class ExecuteTest(unittest.TestCase):
    def test_move_is_resent_for_the_deadman_and_ends_with_stop(self):
        sim, _, _ = simulate([{'action': 'walk', 'direction': 'forward', 'value': 0.2}])
        moves = [entry for entry in sim.log if entry[1] == 'robot.move']
        self.assertEqual(len(moves), 10)  # 1 s at 10 Hz
        self.assertEqual(sim.log[-1][1], 'robot.stop')

    def test_skills_stop_first_then_run(self):
        sim, _, _ = simulate([{'action': 'walk', 'direction': 'forward', 'value': 0.2}, {'action': 'kick_left'}])
        self.assertEqual(sim.skills, ['kick_left'])
        self.assertAlmostEqual(sim.state()['seconds'], 1 + PROFILE['skill_seconds'])

    def test_an_interruption_still_stops_the_robot(self):
        sent = []

        class Broken(SimTransport):
            def send(self, method, params=None, request=False):
                sent.append(method)
                if method == 'robot.head':
                    raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            execute([{'kind': 'head', 'yaw': 0, 'pitch': 0, 'roll': 0}, {'kind': 'stop'}], Broken(PROFILE), PROFILE)
        self.assertEqual(sent, ['robot.head', 'robot.stop'])

    def test_unix_socket_speaks_robotd_ndjson(self):
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, 'robotd.sock')
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(1)
        received = []

        def serve():
            connection, _ = server.accept()
            with connection, connection.makefile('rw', newline='\n') as stream:
                for line in stream:
                    message = json.loads(line)
                    received.append(message)
                    if 'id' in message:  # a state notification first, as robotd streams them, then the reply
                        stream.write(json.dumps({'jsonrpc': '2.0', 'method': 'robot.state', 'params': {}}) + '\n')
                        stream.write(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': {}}) + '\n')
                        stream.flush()

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        transport = LineTransport(f'unix:{path}')
        commands, _ = compile_plan({'steps': [{'action': 'look', 'direction': 'left', 'value': 10},
                                              {'action': 'walk', 'direction': 'forward', 'value': 0.05}]}, PROFILE)
        execute(commands, transport, PROFILE, sleep=lambda s: None)
        transport.close()
        thread.join(2)
        server.close()
        self.assertEqual([m['method'] for m in received], ['robot.head', 'robot.move', 'robot.move', 'robot.move', 'robot.stop'])  # 0.25 s at 10 Hz
        self.assertEqual(received[0]['params']['head_yaw'], round(math.radians(10), 4))
        self.assertEqual(received[1]['params'], {'vx': 0.2, 'vy': 0.0, 'vyaw': 0.0})
        self.assertEqual(received[-1], {'jsonrpc': '2.0', 'method': 'robot.stop', 'id': 1})


class PlannerTest(unittest.TestCase):
    def test_one_correction_round_then_success(self):
        replies = iter(['{"safe": true, "steps": [{"action": "walk", "direction": "forward"}], "repeat": 1}',
                        '{"safe": true, "steps": [{"action": "walk", "direction": "forward", "value": 1}], "repeat": 1}'])
        seen = []

        def chat(messages, format_schema):
            seen.append(messages)
            return next(replies)

        plan, info = Planner(PROFILE, chat=chat).plan('avance')
        self.assertEqual(plan, {'say': "J'avance de 1 m.", 'safe': True, 'steps': [{'action': 'walk', 'direction': 'forward', 'value': 1}], 'repeat': 1})
        self.assertEqual(info['attempts'], 2)
        self.assertIn('needs "value"', seen[1][-1]['content'])
        self.assertEqual(seen[0][0]['content'], system_prompt(PROFILE))

    def test_gives_up_after_the_retry(self):
        with self.assertRaises(PlanError):
            Planner(PROFILE, chat=lambda m, s: 'not json').plan('avance')

    def test_a_silent_server_gives_a_plan_error_not_a_traceback(self):
        server = socket.socket()
        server.bind(('127.0.0.1', 0))
        server.listen(1)  # accepts the connection, never answers
        host = os.environ.get('OLLAMA_HOST')
        os.environ['OLLAMA_HOST'] = f'127.0.0.1:{server.getsockname()[1]}'
        try:
            with self.assertRaises(PlanError) as caught:
                Planner(PROFILE, timeout=0.3).plan('avance')
            self.assertIn('did not answer within 0.3 s', str(caught.exception))
        finally:
            server.close()
            if host is None:
                del os.environ['OLLAMA_HOST']
            else:
                os.environ['OLLAMA_HOST'] = host

    def test_keeps_a_short_history(self):
        planner = Planner(PROFILE, chat=lambda m, s: '{"safe": false, "steps": [], "repeat": 1}')
        for _ in range(5):
            planner.plan('hello')
        self.assertEqual(len(planner.history), 10)
        self.assertNotIn('say', planner.history[-1]['content'])  # the model only ever sees plans in its own format


def robotd(path, received):
    """A minimal robotd on a Unix socket: records every message, answers requests."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)

    def serve():
        connection, _ = server.accept()
        with connection, connection.makefile('rw', newline='\n') as stream:
            for line in stream:
                message = json.loads(line)
                received.append(message)
                if 'id' in message:
                    stream.write(json.dumps({'jsonrpc': '2.0', 'id': message['id'], 'result': {}}) + '\n')
                    stream.flush()

    threading.Thread(target=serve, daemon=True).start()
    return server


class ServeTest(unittest.TestCase):
    PLAN = {'repeat': 1, 'steps': [{'action': 'walk', 'direction': 'forward', 'value': 1}, {'action': 'turn', 'direction': 'left'}]}

    def server(self):
        written = []
        server = Server(PROFILE, chat=lambda messages, format_schema: json.dumps(self.PLAN), write=written.append)
        return server, written

    def test_plans_compile_and_report_errors(self):
        server, written = self.server()
        server.answer({'id': 1, 'type': 'plan', 'request': 'avance d un mètre et tourne à gauche'})
        server.answer({'id': 2, 'type': 'compile', 'plan': {'steps': [{'action': 'fly'}]}})
        server.answer({'id': 3, 'type': 'dance'})
        plan, invalid, unknown = written
        self.assertTrue(plan['ok'])
        self.assertEqual(plan['commands'][-1], {'kind': 'stop'})
        self.assertAlmostEqual(plan['sim']['x'], 1, delta=0.02)
        self.assertAlmostEqual(plan['sim']['heading'], 90, delta=1)
        self.assertEqual(plan['plan']['say'], "J'avance de 1 m, puis je tourne à gauche.")
        self.assertEqual((invalid['id'], invalid['ok']), (2, False))
        self.assertIn('unknown action', invalid['error'])
        self.assertFalse(unknown['ok'])

    def test_the_simulator_profile_plans_but_never_drives_a_robot(self):
        server, written = self.server()
        server.answer({'id': 1, 'type': 'profile', 'robot': 'microduck-sim'})
        server.answer({'id': 2, 'type': 'plan', 'request': 'avance'})
        server.answer({'id': 3, 'type': 'run', 'plan': self.PLAN, 'robot': 'microduck-sim', 'target': 'tcp:127.0.0.1:9'})
        server.answer({'id': 4, 'type': 'profile', 'robot': '../robots/microduck'})
        server.answer({'id': 5, 'type': 'run', 'plan': self.PLAN, 'robot': 'microduck', 'target': 'print'})
        switched, plan, refused, unknown, printed = written
        self.assertEqual(switched['profile']['name'], 'microduck-sim')
        self.assertEqual(plan['commands'][0]['vx'], 0.25)  # the simulated walker only starts above about 0.22 m/s
        self.assertAlmostEqual(plan['sim']['x'], 1, delta=0.02)
        self.assertIn('simulator profile', refused['error'])
        self.assertIn('unknown robot', unknown['error'])
        self.assertIn('cannot run from serve', printed['error'])  # robotd lines would mix with the protocol

    def test_a_run_drives_robotd_with_the_robot_profile_and_stops_on_request(self):
        path = os.path.join(tempfile.mkdtemp(), 'robotd.sock')
        received = []
        socket_server = robotd(path, received)
        server, written = self.server()
        server.profile = load_profile('microduck-sim')  # planning for the simulator does not change how the robot moves
        run = threading.Thread(target=server.answer, args=({'id': 1, 'type': 'run', 'plan': self.PLAN, 'robot': 'microduck', 'target': f'unix:{path}'},))
        run.start()
        for _ in range(500):  # wait for the robot to start moving
            if any(m.get('method') == 'robot.move' for m in list(received)):
                break
            run.join(0.01)
        server.answer({'id': 2, 'type': 'run', 'plan': self.PLAN, 'robot': 'microduck', 'target': f'unix:{path}'})
        server.answer({'id': 3, 'type': 'stop'})
        run.join(2)
        socket_server.close()
        busy, stop, reply = [m for m in written if 'event' not in m]
        self.assertIn('already running', busy['error'])
        self.assertEqual(stop, {'id': 3, 'ok': True, 'running': True, 'elapsed': stop['elapsed']})
        self.assertEqual((reply['id'], reply['stopped']), (1, True))
        self.assertEqual(received[0]['params'], {'vx': 0.2, 'vy': 0.0, 'vyaw': 0.0})  # microduck cruise, not the simulator's
        self.assertEqual(received[-1]['method'], 'robot.stop')
        self.assertEqual([m['index'] for m in written if m.get('event') == 'command'], [0])

    def test_serve_answers_in_order_until_the_end_of_input(self):
        server, written = self.server()
        lines = ['{"id": 1, "type": "profile", "robot": "microduck-sim"}', 'not json', '',
                 json.dumps({'id': 2, 'type': 'compile', 'plan': self.PLAN}), '{"id": 3, "type": "hello"}']
        server.serve(iter(lines))
        self.assertEqual([m['id'] for m in written], [None, 1, 2, 3])
        self.assertEqual(written[2]['commands'][0]['vx'], 0.25)
        self.assertIn({'name': 'microduck-sim', 'title': load_profile('microduck-sim')['title'], 'simulated': True}, written[3]['robots'])

    def test_every_robot_profile_compiles_the_prompt_examples(self):
        for path in sorted((ROOT / 'robots').glob('*.json')):
            profile = load_profile(path.stem)
            self.assertEqual(profile['name'], path.stem)
            for request, plan in EXAMPLES:
                plan = {'repeat': plan.get('repeat', 1), 'steps': plan['steps']}
                self.assertEqual(validate(plan, profile), [], (path.stem, request))
                commands, _ = compile_plan(plan, profile)
                self.assertLessEqual(duration(commands), profile['limits']['plan_seconds'])


class BenchTest(unittest.TestCase):
    def test_check_scores_outcomes(self):
        plan = {'steps': [{'action': 'walk', 'direction': 'forward', 'value': 1}, {'action': 'turn', 'direction': 'right'}]}
        sim, commands, _ = simulate(plan['steps'])
        self.assertEqual(bench.check({'x': 1, 'y': 0, 'heading': -90}, plan, sim.state(), commands), [])
        self.assertEqual(len(bench.check({'x': 1, 'heading': 90}, plan, sim.state(), commands)), 1)
        self.assertEqual(len(bench.check({'refuse': True}, plan, sim.state(), commands)), 1)
        self.assertEqual(bench.check({'refuse': True}, {'steps': []}, sim.state(), commands), [])

    def test_a_full_turn_counts_as_heading_zero(self):
        self.assertEqual(bench.angle_gap(358, 0), 2)
        self.assertEqual(bench.angle_gap(-180, 180), 0)

    def test_cases_are_well_formed(self):
        cases = json.loads((ROOT / 'bench/cases.json').read_text())
        self.assertGreaterEqual(len(cases), 30)
        for case in cases:
            self.assertTrue(case['request'] and case['expect'], case)


class DatasetTest(unittest.TestCase):
    def test_every_example_is_a_valid_plan_and_none_is_a_bench_case(self):
        rows = build_dataset.build(600, 1, PROFILE)
        bench_requests = {c['request'].lower().strip(' .!?') for c in json.loads((ROOT / 'bench/cases.json').read_text())}
        for row in rows:
            system, user, assistant = row['messages']
            self.assertEqual(system['content'], system_prompt(PROFILE))
            self.assertNotIn(user['content'].lower().strip(' .!?'), bench_requests)
            self.assertEqual(validate(json.loads(assistant['content']), PROFILE), [])
        self.assertEqual({row['lang'] for row in rows}, {'fr', 'en'})

    def test_direction_words_match_signs(self):
        for row in build_dataset.build(800, 2, PROFILE):
            request = row['messages'][1]['content'].lower()
            steps = json.loads(row['messages'][2]['content'])['steps']
            if len(steps) == 1 and steps[0]['action'] in ('turn', 'walk') and not (steps[0]['action'] == 'turn' and steps[0]['value'] == 180):
                right = 'droite' in request or 'right' in request
                left = 'gauche' in request or 'left' in request
                self.assertEqual(steps[0]['direction'] == 'right', right, request)
                self.assertEqual(steps[0]['direction'] == 'left', left, request)


class ModelfileTest(unittest.TestCase):
    def test_modelfiles_match_the_profile_and_prompt(self):
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/gen_modelfiles.py'), '--check'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
