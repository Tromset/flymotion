// Runs the FlyMotion bridge (python -m flymotion serve) and speaks its JSON lines protocol (flymotion/serve.py).
// The bridge plans with Ollama, validates, clamps and compiles; the app only displays and plays its output.
const { spawn } = require('node:child_process');
const { EventEmitter } = require('node:events');
const path = require('node:path');
const readline = require('node:readline');

const ROOT = path.resolve(__dirname, '..');

function pythonCommand() {
  return process.env.FLYMOTION_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
}

class Bridge extends EventEmitter {
  constructor({ robot = 'microduck', model, python = pythonCommand(), cwd = ROOT, env = process.env } = {}) {
    super();
    Object.assign(this, { robot, model, python, cwd, env });
    this.nextId = 1;
    this.pending = new Map(); // id -> { resolve, reject, onEvent }
    this.stderr = [];
    this.child = null;
  }

  start() {
    const args = ['-m', 'flymotion', 'serve', '--robot', this.robot, ...(this.model ? ['--model', this.model] : [])];
    const child = spawn(this.python, args, { cwd: this.cwd, env: { ...this.env, PYTHONUNBUFFERED: '1' } });
    this.child = child;
    readline.createInterface({ input: child.stdout }).on('line', (line) => this.receive(line));
    readline.createInterface({ input: child.stderr }).on('line', (line) => {
      this.stderr = [...this.stderr.slice(-19), line];
      this.emit('log', line);
    });
    child.stdin.on('error', () => {}); // a write after the bridge died: its exit already rejected the requests
    child.on('error', (error) => this.fail(`impossible de lancer ${this.python} : ${error.message}`));
    child.on('exit', (code, signal) => this.fail(`le bridge s'est arrêté (${signal || `code ${code}`})\n${this.stderr.join('\n')}`));
    return this;
  }

  receive(line) {
    let message;
    try {
      message = JSON.parse(line);
    } catch {
      this.emit('log', line);
      return;
    }
    const waiting = this.pending.get(message.id);
    if (!waiting) return;
    if (message.event) {
      waiting.onEvent?.(message);
      return;
    }
    this.pending.delete(message.id);
    if (message.ok) waiting.resolve(message);
    else waiting.reject(new Error(message.error || 'erreur inconnue du bridge'));
  }

  fail(reason) {
    if (!this.child) return;
    this.child = null;
    for (const waiting of this.pending.values()) waiting.reject(new Error(reason));
    this.pending.clear();
    this.emit('exit', reason);
  }

  // request({type: "plan", request: "..."}) -> the bridge's reply; onEvent gets the events of a run.
  request(message, onEvent) {
    if (!this.child) return Promise.reject(new Error("le bridge n'est pas lancé"));
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, onEvent });
      this.child.stdin.write(JSON.stringify({ ...message, id }) + '\n');
    });
  }

  stop() {
    const child = this.child;
    if (!child) return;
    this.fail('arrêt demandé');
    child.stdin.end(); // end of input: the bridge stops any run on the robot, then exits
    setTimeout(() => child.exitCode === null && child.kill(), 3000).unref();
  }
}

module.exports = { Bridge, ROOT, pythonCommand };
