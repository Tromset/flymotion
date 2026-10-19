// FlyMotion driver for Pollen's Microduck simulator
// (https://huggingface.co/spaces/pollen-robotics/microduck-simulator).
//
// Injected into the simulator page by the Electron app. The simulator steps MuJoCo and runs Pollen's ONNX
// policies at 50 Hz; its input Controller merges pluggable sources (keyboard, gamepad, touch, click-to-walk)
// into the [vx, vy, wz] twist the walking policy tracks, plus discrete actions (kicks, sit, roll, pick).
// FlyMotion becomes one more source: the bridge's compiled commands are played in real time, exactly as
// robotd would receive them, and the physics decides what really happens.
//
// Uses the simulator's debug surface window.rl (controller, headTarget, data, resetSim, spawnBall...).
// Written against the Space at revision ad9d41a7 (2026-10-09).
(function (root) {
  // The simulator's legs-mode limits (game/constants.js VEL_FWD, VEL_BACK, VEL_ANG). The bridge clamps to the
  // robot profile; the simulated walker was only trained up to these.
  const SIM_LIMITS = { vxForward: 0.25, vxBackward: -0.2, vyaw: 1.0 };
  // Bridge skill -> simulator Controller action. polite-bow has no policy in the simulator.
  const SKILL_ACTIONS = { kick_left: 'kickL', kick_right: 'kickR', roulade: 'roll', ground_pick: 'groundPick', sit_toggle: 'sitToggle' };
  // headTarget slots are [neck_pitch, head_pitch, head_yaw, head_roll]; in the simulator + head_pitch looks down.
  const HEAD_PITCH = 1, HEAD_YAW = 2;
  const KICKS = ['kick_left', 'kick_right'];

  const clamp = (value, low, high) => Math.min(high, Math.max(low, value));
  const wrapDegrees = (degrees) => ((degrees + 540) % 360) - 180;

  function yawOf(qpos) {
    return Math.atan2(2 * (qpos[3] * qpos[6] + qpos[4] * qpos[5]), 1 - 2 * (qpos[5] * qpos[5] + qpos[6] * qpos[6]));
  }

  class FlyMotionSource {
    constructor() {
      this.id = 'flymotion';
      this.connected = true;
      this.command = new Float32Array(3); // updated in place: the Controller hands this array to the policy loop
      this.axes = { jaw: 0, orbitX: 0, orbitY: 0, ride: 0 };
      this.pressed = {};
      this.active = false;
      this.onAction = () => {};
    }

    isActive() {
      return this.active;
    }

    poll() {}

    set(vx, vy, vyaw) {
      this.command[0] = vx;
      this.command[1] = vy;
      this.command[2] = vyaw;
    }
  }

  function createDriver({ rl, emit = () => {}, timeScale = 1, setTimer = setTimeout, clearTimer = clearTimeout }) {
    const source = new FlyMotionSource();
    rl.controller.addSource(source);
    let run = null; // { token, timer, wake }

    function pose() {
      const qpos = rl.data.qpos;
      return { x: qpos[0], y: qpos[1], yaw: yawOf(qpos) };
    }

    // Where the duck is, in the frame it had when the plan started: the bridge's simulator reports the same.
    function relative(start, now) {
      const dx = now.x - start.x, dy = now.y - start.y;
      const c = Math.cos(start.yaw), s = Math.sin(start.yaw);
      return {
        x: +(c * dx + s * dy).toFixed(3),
        y: +(-s * dx + c * dy).toFixed(3),
        heading: +wrapDegrees(((now.yaw - start.yaw) * 180) / Math.PI).toFixed(1),
      };
    }

    function idle() {
      source.set(0, 0, 0);
      source.active = false;
    }

    function wait(seconds, token) {
      return new Promise((resolve) => {
        if (run?.token !== token) return resolve();
        run.wake = resolve;
        run.timer = setTimer(resolve, Math.max(0, seconds * 1000 * timeScale));
      });
    }

    // After a reset the simulator replays its respawn and ignores the input for a few seconds.
    async function unlocked(token) {
      for (let tries = 0; (rl.inputLocked || rl.respawnActive) && tries < 150; tries++) {
        await wait(0.1, token);
        if (run?.token !== token) return;
      }
    }

    function head(command) {
      rl.headTarget[HEAD_YAW] = command.yaw;
      rl.headTarget[HEAD_PITCH] = -command.pitch;
    }

    async function skill(command, token) {
      const action = SKILL_ACTIONS[command.name];
      if (!action) {
        emit({ type: 'skipped', skill: command.name, reason: 'pas de politique pour ce skill dans le simulateur' });
        return;
      }
      if (KICKS.includes(command.name) && !rl.ballActive && rl.spawnBall) {
        rl.spawnBall(); // "frappe le ballon": give it one, in front of the duck
        await wait(0.8, token);
        if (run?.token !== token) return;
      }
      source.onAction(action, {});
    }

    async function play(commands, { id } = {}) {
      stop();
      const token = {};
      run = { token, timer: null, wake: null };
      if (rl.loco && rl.loco !== 'legs' && rl.setLoco) rl.setLoco('legs');
      await unlocked(token);
      const start = pose();
      const sim = { ...SIM_LIMITS };
      const limited = [];
      emit({ type: 'start', id, count: commands.length });
      for (let index = 0; index < commands.length; index++) {
        if (run?.token !== token) break;
        const command = commands[index];
        emit({ type: 'command', id, index });
        if (command.kind === 'move') {
          const vx = clamp(command.vx, sim.vxBackward, sim.vxForward);
          const vyaw = clamp(command.vyaw, -sim.vyaw, sim.vyaw);
          if (vx !== command.vx || vyaw !== command.vyaw) limited.push(index);
          source.set(vx, command.vy, vyaw);
          source.active = true;
          await wait(command.seconds, token);
        } else if (command.kind === 'head') {
          head(command);
        } else if (command.kind === 'skill') {
          idle();
          await skill(command, token);
          await wait(command.seconds, token);
        } else {
          idle();
        }
      }
      const finished = run?.token === token;
      if (finished) {
        idle();
        run = null;
      }
      emit({ type: 'done', id, stopped: !finished, limited, pose: relative(start, pose()), mode: rl.mode });
    }

    function stop() {
      if (run) {
        clearTimer(run.timer);
        const wake = run.wake;
        run = null;
        wake?.();
      }
      idle();
    }

    function reset() {
      stop();
      rl.headTarget.fill(0);
      rl.resetSim();
    }

    function status() {
      return { ready: true, playing: !!run, mode: rl.mode, loco: rl.loco, inputLocked: !!rl.inputLocked, pose: pose() };
    }

    return { source, play, stop, reset, status, pose, relative };
  }

  // Waits for the simulator to boot (window.rl) and for its entrance to unlock the input, then installs itself.
  function install(window) {
    if (window.__flymotion) return 'already installed';
    const emit = (event) => window.flymotionHost?.emit(event);
    const started = Date.now();
    const timer = setInterval(() => {
      const rl = window.rl;
      if (rl && rl.controller && !rl.inputLocked) {
        clearInterval(timer);
        const driver = createDriver({ rl, emit });
        window.__flymotion = driver;
        emit({ type: 'ready', status: driver.status() });
      } else if (Date.now() - started > 120000) {
        clearInterval(timer);
        emit({ type: 'error', error: rl ? 'le simulateur ne rend pas la main (entrée verrouillée)' : 'window.rl introuvable : le simulateur a changé ?' });
      }
    }, 250);
    return 'waiting for the simulator';
  }

  const api = { createDriver, install, yawOf, SIM_LIMITS, SKILL_ACTIONS };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.__flymotionInstall = api;
})(typeof window !== 'undefined' ? window : globalThis);
