// The Electron side of the bridge protocol, against the real `python -m flymotion serve`.
const test = require('node:test');
const assert = require('node:assert/strict');
const { Bridge } = require('../bridge.js');

const PLAN = { repeat: 4, steps: [{ action: 'walk', direction: 'forward', value: 0.5 }, { action: 'turn', direction: 'left', value: 90 }] };

test('the bridge compiles plans, reports errors and switches profiles', async (t) => {
  const bridge = new Bridge({ robot: 'microduck-sim', env: { ...process.env, OLLAMA_HOST: '127.0.0.1:9' } }).start();
  t.after(() => bridge.stop());
  const hello = await bridge.request({ type: 'hello' });
  assert.equal(hello.profile.name, 'microduck-sim');
  assert.ok(hello.robots.some((robot) => robot.name === 'microduck' && !robot.simulated));

  const square = await bridge.request({ type: 'compile', plan: PLAN });
  assert.equal(square.commands.at(-1).kind, 'stop');
  assert.ok(Math.hypot(square.sim.x, square.sim.y) < 0.02, 'a square ends where it started');
  assert.equal(square.commands[0].vx, 0.25);
  assert.equal(square.plan.say, "J'avance de 50 cm, puis je tourne à gauche, 4 fois.");
  assert.equal(square.sim.in_place, 0);

  await assert.rejects(bridge.request({ type: 'compile', plan: { steps: [{ action: 'jump' }] } }), /unknown action/);
  await assert.rejects(bridge.request({ type: 'plan', request: 'avance' }), /cannot reach Ollama/);
  await assert.rejects(bridge.request({ type: 'run', plan: PLAN, robot: 'microduck-sim', target: 'robot' }), /simulator profile/);

  await bridge.request({ type: 'profile', robot: 'microduck' });
  assert.equal((await bridge.request({ type: 'compile', plan: PLAN })).commands[0].vx, 0.2);
});

test('pending requests fail when the bridge stops', async () => {
  const bridge = new Bridge().start();
  const exited = new Promise((resolve) => bridge.once('exit', resolve));
  const pending = bridge.request({ type: 'models' });
  bridge.stop();
  await assert.rejects(pending, /arrêt demandé/);
  assert.match(await exited, /arrêt demandé/);
  await assert.rejects(bridge.request({ type: 'hello' }), /pas lancé/);
});
