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
from flymotion.prompt import EXAMPLES, system_prompt  # noqa: E402
from flymotion.robot import LineTransport, SimTransport, execute  # noqa: E402
from flymotion.serve import Server  # noqa: E402
import build_dataset  # noqa: E402
import run as bench  # noqa: E402

PROFILE = load_profile('microduck')


def simulate(steps):
    commands, warnings = compile_plan({'say': '', 'steps': steps}, PROFILE)
    sim = SimTransport(PROFILE)
    execute(commands, sim, PROFILE)
    return sim, commands, warnings


class PlanTest(unittest.TestCase):
    def test_schema_lists_the_robot_skills(self):
        step = schema(PROFILE)['properties']['steps']['items']
        self.assertEqual(step['properties']['skill']['enum'], list(PROFILE['skills']))
        self.assertEqual(step['required'], ['action'])
        # The model decides "repeat" (shapes) before it writes the steps.
        self.assertEqual(list(schema(PROFILE)['properties']), ['say', 'repeat', 'steps'])
        self.assertEqual(schema(PROFILE)['required'], ['say', 'repeat', 'steps'])

    def test_validate_names_each_problem(self):
        self.assertEqual(validate({'say': 'ok', 'steps': [{'action': 'walk', 'direction': 'left', 'meters': 1}]}, PROFILE), [])
        errors = validate({'say': 'x', 'steps': [{'action': 'jump'}, {'action': 'walk', 'direction': 'forward'}, {'action': 'skill', 'skill': 'dance'},
                                                 {'action': 'turn', 'direction': 'left', 'degrees': '90'}, {'action': 'wait', 'seconds': 0},
                                                 {'action': 'turn', 'direction': 'forward'}, {'action': 'head'}]}, PROFILE)
        self.assertEqual(len(errors), 7, errors)
        self.assertIn('unknown action', errors[0])
        self.assertIn('needs "meters"', errors[1])
        self.assertIn('unknown skill', errors[2])
        self.assertIn('must be a number', errors[3])
        self.assertIn('positive', errors[4])
        self.assertIn('left or right', errors[5])
        self.assertIn('needs "look"', errors[6])
        self.assertTrue(validate([], PROFILE))
        self.assertTrue(validate({'say': 'x', 'steps': [], 'repeat': 50}, PROFILE))
        self.assertTrue(validate({'say': 'x', 'steps': [], 'repeat': 1.5}, PROFILE))
        self.assertTrue(validate({'say': 'x', 'steps': [{'action': 'move', 'seconds': float('nan')}]}, PROFILE))

    def test_parse_rejects_invalid_json(self):
        with self.assertRaises(PlanError):
            parse('{"say": "x", "steps": [', PROFILE)

    def test_walk_turn_and_strafe_reach_the_asked_pose(self):
        sim, _, warnings = simulate([{'action': 'walk', 'direction': 'forward', 'meters': 1}, {'action': 'turn', 'direction': 'right'},
                                     {'action': 'walk', 'direction': 'forward', 'meters': 0.5}, {'action': 'walk', 'direction': 'left', 'meters': 0.2},
                                     {'action': 'walk', 'direction': 'backward', 'meters': -0.1}])
        state = sim.state()
        self.assertEqual(warnings, [])
        self.assertAlmostEqual(state['x'], 1.2, delta=0.02)
        self.assertAlmostEqual(state['y'], -0.4, delta=0.02)  # the sign of meters is ignored: direction wins
        self.assertAlmostEqual(state['heading'], -90, delta=1)

    def test_a_repeated_side_and_turn_draws_a_closed_square(self):
        plan = {'say': '', 'repeat': 4, 'steps': [{'action': 'walk', 'direction': 'forward', 'meters': 0.5},
                                                  {'action': 'turn', 'direction': 'right', 'degrees': 90}]}
        commands, _ = compile_plan(plan, PROFILE)
        sim = SimTransport(PROFILE)
        execute(commands, sim, PROFILE)
        self.assertEqual(len(commands), 9)
        self.assertAlmostEqual(sim.state()['x'], 0, delta=0.02)
        self.assertAlmostEqual(sim.state()['y'], 0, delta=0.02)

    def test_a_circle_comes_back_to_the_start_even_on_a_weak_turner(self):
        for achieved in ({'vx': 1.0, 'vy': 1.0, 'vyaw': 1.0}, {'vx': 0.8, 'vy': 1.0, 'vyaw': 0.6}):
            profile = dict(PROFILE, achieved=achieved)
            commands, _ = compile_plan({'say': '', 'steps': [{'action': 'circle', 'direction': 'right', 'meters': 0.5}]}, profile)
            sim = SimTransport(profile)
            execute(commands, sim, profile)
            self.assertAlmostEqual(math.hypot(sim.state()['x'], sim.state()['y']), 0, delta=0.03)
            self.assertLess(commands[0]['vyaw'], 0)

    def test_head_looks_map_to_angles(self):
        commands, _ = compile_plan({'say': '', 'steps': [{'action': 'head', 'look': 'down-right'}, {'action': 'head', 'look': 'up', 'degrees': 90}]}, PROFILE)
        self.assertEqual(commands[0], {'kind': 'head', 'yaw': round(math.radians(-30), 4), 'pitch': round(math.radians(-20), 4), 'roll': 0.0})
        self.assertEqual((commands[1]['yaw'], commands[1]['pitch']), (0, round(math.radians(30), 4)))

    def test_limits_are_enforced_and_reported(self):
        _, commands, warnings = simulate([{'action': 'move', 'vx': 5, 'vy': -2, 'vyaw': 9, 'seconds': 100},
                                          {'action': 'head', 'look': 'left', 'degrees': 90}, {'action': 'walk', 'direction': 'forward', 'meters': 50}])
        move = commands[0]
        self.assertEqual((move['vx'], move['vy'], move['vyaw']), (0.4, -0.3, 1.0))
        self.assertEqual(move['seconds'], PROFILE['limits']['step_seconds'])
        self.assertAlmostEqual(commands[1]['yaw'], math.radians(30), places=3)
        self.assertLessEqual(duration(commands), PROFILE['limits']['plan_seconds'])
        self.assertEqual(commands[-1], {'kind': 'stop'})
        self.assertGreaterEqual(len(warnings), 5)

    def test_slow_robots_walk_longer(self):
        slow = dict(PROFILE, achieved={'vx': 0.5, 'vy': 1.0, 'vyaw': 0.29})
        commands, _ = compile_plan({'say': '', 'steps': [{'action': 'walk', 'direction': 'forward', 'meters': 1}, {'action': 'turn', 'direction': 'left'}]}, slow)
        self.assertAlmostEqual(commands[0]['seconds'], 10, places=2)
        sim = SimTransport(slow)
        execute(commands, sim, slow)
        self.assertAlmostEqual(sim.state()['x'], 1, delta=0.02)
        self.assertAlmostEqual(sim.state()['heading'], 90, delta=1)

    def test_an_empty_plan_only_stops(self):
        self.assertEqual(compile_plan({'say': 'no', 'steps': []}, PROFILE)[0], [{'kind': 'stop'}])

    def test_prompt_examples_are_valid_plans(self):
        for request, plan in EXAMPLES:
            self.assertEqual(validate(plan, PROFILE), [], request)
        prompt = system_prompt(PROFILE)
        for skill in PROFILE['skills']:
            self.assertIn(skill, prompt)

    def test_unknown_robot(self):
        with self.assertRaises(PlanError):
            load_profile('r2d2')


class ExecuteTest(unittest.TestCase):
    def test_move_is_resent_for_the_deadman_and_ends_with_stop(self):
        sim, _, _ = simulate([{'action': 'walk', 'direction': 'forward', 'meters': 0.2}])
        moves = [entry for entry in sim.log if entry[1] == 'robot.move']
        self.assertEqual(len(moves), 10)  # 1 s at 10 Hz
        self.assertEqual(sim.log[-1][1], 'robot.stop')

    def test_skills_stop_first_then_run(self):
        sim, _, _ = simulate([{'action': 'walk', 'direction': 'forward', 'meters': 0.2}, {'action': 'skill', 'skill': 'kick_left'}])
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
        commands, _ = compile_plan({'say': '', 'steps': [{'action': 'head', 'look': 'left', 'degrees': 10},
                                                         {'action': 'walk', 'direction': 'forward', 'meters': 0.02}]}, PROFILE)
        execute(commands, transport, PROFILE, sleep=lambda s: None)
        transport.close()
        thread.join(2)
        server.close()
        self.assertEqual([m['method'] for m in received], ['robot.head', 'robot.move', 'robot.stop'])
        self.assertEqual(received[0]['params']['head_yaw'], round(math.radians(10), 4))
        self.assertEqual(received[1]['params'], {'vx': 0.2, 'vy': 0.0, 'vyaw': 0.0})
        self.assertEqual(received[2], {'jsonrpc': '2.0', 'method': 'robot.stop', 'id': 1})


class PlannerTest(unittest.TestCase):
    def test_one_correction_round_then_success(self):
        replies = iter(['{"say": "ok", "steps": [{"action": "walk", "direction": "forward"}]}',
                        '{"say": "ok", "steps": [{"action": "walk", "direction": "forward", "meters": 1}]}'])
        seen = []

        def chat(messages, format_schema):
            seen.append(messages)
            return next(replies)

        plan, info = Planner(PROFILE, chat=chat).plan('avance')
        self.assertEqual(plan['steps'], [{'action': 'walk', 'direction': 'forward', 'meters': 1}])
        self.assertEqual(info['attempts'], 2)
        self.assertIn('needs "meters"', seen[1][-1]['content'])
        self.assertEqual(seen[0][0]['content'], system_prompt(PROFILE))

    def test_gives_up_after_the_retry(self):
        with self.assertRaises(PlanError):
            Planner(PROFILE, chat=lambda m, s: 'not json').plan('avance')

    def test_keeps_a_short_history(self):
        planner = Planner(PROFILE, chat=lambda m, s: '{"say": "ok", "steps": []}')
        for _ in range(5):
            planner.plan('hello')
        self.assertEqual(len(planner.history), 10)


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
    PLAN = {'say': 'ok', 'steps': [{'action': 'walk', 'direction': 'forward', 'meters': 1}, {'action': 'turn', 'direction': 'left'}]}

    def server(self):
        written = []
        server = Server(PROFILE, chat=lambda messages, format_schema: json.dumps(self.PLAN), write=written.append)
        return server, written

    def test_plans_compile_and_report_errors(self):
        server, written = self.server()
        server.answer({'id': 1, 'type': 'plan', 'request': 'avance d un mètre et tourne à gauche'})
        server.answer({'id': 2, 'type': 'compile', 'plan': {'say': '', 'steps': [{'action': 'fly'}]}})
        server.answer({'id': 3, 'type': 'dance'})
        plan, invalid, unknown = written
        self.assertTrue(plan['ok'])
        self.assertEqual(plan['commands'][-1], {'kind': 'stop'})
        self.assertAlmostEqual(plan['sim']['x'], 1, delta=0.02)
        self.assertAlmostEqual(plan['sim']['heading'], 90, delta=1)
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
                self.assertEqual(validate(plan, profile), [], (path.stem, request))
                commands, _ = compile_plan(plan, profile)
                self.assertLessEqual(duration(commands), profile['limits']['plan_seconds'])


class BenchTest(unittest.TestCase):
    def test_check_scores_outcomes(self):
        plan = {'say': '', 'steps': [{'action': 'walk', 'direction': 'forward', 'meters': 1}, {'action': 'turn', 'direction': 'right'}]}
        sim, commands, _ = simulate(plan['steps'])
        self.assertEqual(bench.check({'x': 1, 'y': 0, 'heading': -90}, plan, sim.state(), commands), [])
        self.assertEqual(len(bench.check({'x': 1, 'heading': 90}, plan, sim.state(), commands)), 1)
        self.assertEqual(len(bench.check({'refuse': True}, plan, sim.state(), commands)), 1)
        self.assertEqual(bench.check({'refuse': True}, {'say': '', 'steps': []}, sim.state(), commands), [])

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
            if len(steps) == 1 and steps[0]['action'] in ('turn', 'walk') and steps[0].get('degrees') != 180:
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
