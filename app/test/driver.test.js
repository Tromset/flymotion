// The simulator driver against a fake window.rl: what reaches the Controller, the head and the actions.
const test = require('node:test');
const assert = require('node:assert/strict');
const { createDriver, yawOf } = require('../sim/driver.js');

function fakeSimulator() {
  const sources = [];
  const actions = [];
  const rl = {
    mode: 'walk',
    loco: 'legs',
    inputLocked: false,
    respawnActive: false,
    ballActive: false,
    data: { qpos: new Float64Array([0.5, -0.2, 0.1, 1, 0, 0, 0]) },
    headTarget: new Float32Array(4),
    controller: { addSource: (source) => sources.push(source) },
    spawnBall() {
      this.ballActive = true;
    },
    resetSim() {
      this.reset = true;
    },
  };
  return { rl, sources, actions };
}

function driverFor(rl, events, actions) {
  const driver = createDriver({ rl, emit: (event) => events.push(event), timeScale: 0.01 });
  driver.source.onAction = (name) => actions.push(name);
  return driver;
}

const until = async (check) => {
  for (let i = 0; i < 500 && !check(); i++) await new Promise((resolve) => setTimeout(resolve, 2));
};

test('moves become the twist of the FlyMotion source, then the duck stops', async () => {
  const { rl, sources, actions } = fakeSimulator();
  const events = [];
  const driver = driverFor(rl, events, actions);
  assert.equal(sources[0], driver.source);
  const seen = [];
  const commands = [
    { kind: 'move', vx: 0.4, vy: 0.1, vyaw: -1.5, seconds: 2 },
    { kind: 'head', yaw: 0.52, pitch: 0.35, roll: 0 },
    { kind: 'stop' },
  ];
  const playing = driver.play(commands, { id: 7 });
  await until(() => driver.source.isActive());
  seen.push([...driver.source.command], driver.source.isActive());
  await playing;
  // Clamped to the simulator walker (0.25 forward, 1 rad/s); vy is passed through.
  assert.deepEqual(seen[0].map((v) => +v.toFixed(3)), [0.25, 0.1, -1]);
  assert.equal(seen[1], true);
  assert.deepEqual([...driver.source.command], [0, 0, 0]);
  assert.equal(driver.source.isActive(), false);
  // In the simulator + head_pitch looks down: looking up writes a negative target.
  assert.deepEqual([...rl.headTarget].map((v) => +v.toFixed(2)), [0, -0.35, 0.52, 0]);
  const done = events.find((event) => event.type === 'done');
  assert.deepEqual(events.filter((e) => e.type === 'command').map((e) => e.index), [0, 1, 2]);
  assert.equal(done.id, 7);
  assert.equal(done.stopped, false);
  assert.deepEqual(done.limited, [0]);
});

test('skills fire Controller actions; a kick gets a ball; unknown skills are reported', async () => {
  const { rl, actions } = fakeSimulator();
  const events = [];
  const driver = driverFor(rl, events, actions);
  await driver.play([
    { kind: 'skill', name: 'kick_left', seconds: 4 },
    { kind: 'skill', name: 'sit_toggle', seconds: 4 },
    { kind: 'skill', name: 'roulade', seconds: 4 },
    { kind: 'skill', name: 'ground_pick', seconds: 4 },
    { kind: 'skill', name: 'polite-bow', seconds: 4 },
    { kind: 'stop' },
  ]);
  assert.equal(rl.ballActive, true);
  assert.deepEqual(actions, ['kickL', 'sitToggle', 'roll', 'groundPick']);
  assert.deepEqual(events.filter((e) => e.type === 'skipped').map((e) => e.skill), ['polite-bow']);
});

test('stop interrupts a plan and zeroes the twist', async () => {
  const { rl, actions } = fakeSimulator();
  const events = [];
  const driver = createDriver({ rl, emit: (event) => events.push(event), timeScale: 1 });
  driver.source.onAction = (name) => actions.push(name);
  const playing = driver.play([{ kind: 'move', vx: 0.25, vy: 0, vyaw: 0, seconds: 30 }, { kind: 'skill', name: 'kick_left', seconds: 4 }]);
  await until(() => driver.source.isActive());
  driver.stop();
  await playing;
  assert.deepEqual([...driver.source.command], [0, 0, 0]);
  assert.equal(events.at(-1).type, 'done');
  assert.equal(events.at(-1).stopped, true);
  assert.deepEqual(actions, []);
});

test('a plan waits for the respawn to end before it starts', async () => {
  const { rl } = fakeSimulator();
  const driver = driverFor(rl, [], []);
  rl.inputLocked = true;
  let started = false;
  const playing = driver.play([{ kind: 'move', vx: 0.25, vy: 0, vyaw: 0, seconds: 1 }]).then(() => (started = true));
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(driver.source.isActive(), false);
  rl.inputLocked = false;
  await playing;
  assert.equal(started, true);
});

test('moves last in simulated seconds, however slowly the physics runs', async () => {
  const { rl } = fakeSimulator();
  rl.data.time = 0;
  const driver = driverFor(rl, [], []);
  // The physics at a tenth of real time: 1 s of walking takes 10 s of wall clock (0.1 s with timeScale 0.01).
  const clock = setInterval(() => (rl.data.time += 0.001), 1);
  const started = Date.now();
  await driver.play([{ kind: 'move', vx: 0.25, vy: 0, vyaw: 0, seconds: 1 }, { kind: 'stop' }]);
  clearInterval(clock);
  assert.ok(rl.data.time >= 1, `stopped after ${rl.data.time} simulated seconds`);
  assert.ok(Date.now() - started > 200, 'paced by the simulator, not the wall clock');
});

test('a sitting duck stands up before it walks', async () => {
  const { rl, actions } = fakeSimulator();
  rl.mode = 'sitstand';
  const events = [];
  const driver = driverFor(rl, events, actions);
  driver.source.onAction = (name) => {
    actions.push(name);
    if (name === 'walk') setTimeout(() => (rl.mode = 'walk'), 20); // the sitstand policy stands it up
  };
  let walkingWhileSitting = false;
  const watch = setInterval(() => (walkingWhileSitting ||= driver.source.isActive() && rl.mode !== 'walk'), 1);
  await driver.play([{ kind: 'move', vx: 0.25, vy: 0, vyaw: 0, seconds: 1 }, { kind: 'stop' }]);
  clearInterval(watch);
  assert.deepEqual(actions, ['walk']);
  assert.equal(walkingWhileSitting, false);
  assert.equal(events.at(-1).mode, 'walk');
});

test('poses are reported in the frame the duck had when the plan started', () => {
  const { rl } = fakeSimulator();
  const driver = driverFor(rl, [], []);
  const quarter = { x: 1, y: 1, yaw: Math.PI / 2 };
  // Facing +y, then moving 0.5 m along +y and 0.2 m along -x: 0.5 m forward, 0.2 m to the left.
  assert.deepEqual(driver.relative(quarter, { x: 0.8, y: 1.5, yaw: Math.PI }), { x: 0.5, y: 0.2, heading: 90 });
  const half = Math.SQRT1_2;
  assert.ok(Math.abs(yawOf([0, 0, 0, half, 0, 0, half]) - Math.PI / 2) < 1e-9);
});
