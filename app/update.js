#!/usr/bin/env node
// `flymotion update` (or `npm run update`): brings this copy of FlyMotion up to date.
//
//   1. the code: a fast-forward `git pull` in this clone;
//   2. the app's dependencies: `npm ci`, only when package-lock.json changed or node_modules is missing;
//   3. the models: `ollama pull` for every published FlyMotion model already installed.
//
// It needs nothing from node_modules, so it still runs when the dependencies are missing or broken.
const { spawnSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');

// Runs a command; `capture` returns its output instead of showing it. `status` is null when the command is missing.
function spawn(command, args, { cwd, capture = false } = {}) {
  const result = spawnSync(command, args, {
    cwd,
    encoding: 'utf8',
    stdio: capture ? ['ignore', 'pipe', 'pipe'] : 'inherit',
    shell: process.platform === 'win32' && command === 'npm', // npm is npm.cmd on Windows
  });
  return { status: result.error ? null : result.status, output: (result.stdout || '').trim() };
}

// The published FlyMotion models in `ollama list` output: only names with a namespace can be pulled again
// (a model built locally with `ollama create flymotion0.2 -f Modelfile` has none).
function publishedModels(listOutput) {
  return listOutput.split('\n').slice(1)
    .map((line) => line.trim().split(/\s+/)[0])
    .filter((name) => /^[^/\s]+\/flymotion/i.test(name || ''));
}

function update({ root = ROOT, run = spawn, log = console.log, exists = fs.existsSync } = {}) {
  if (!exists(path.join(root, '.git'))) {
    log(`flymotion: ${root} is not a git clone, so it cannot update itself.\n`
      + 'Install it from a clone: git clone https://github.com/Tromset/flymotion && cd flymotion && npm install');
    return 1;
  }
  const head = () => run('git', ['rev-parse', 'HEAD'], { cwd: root, capture: true }).output;

  // npm rewrites package-lock.json on some machines, and git would then refuse the pull. It is a generated file
  // whose reference copy is the repository's: put it back first.
  if (run('git', ['status', '--porcelain', '--', 'package-lock.json'], { cwd: root, capture: true }).output) {
    log('Restoring package-lock.json (modified locally by npm).');
    run('git', ['checkout', '--', 'package-lock.json'], { cwd: root });
  }

  const before = head();
  log(`Updating FlyMotion in ${root}`);
  // --ff-only never merges or rewrites: conflicting local changes stop here and nothing is modified.
  if (run('git', ['pull', '--ff-only'], { cwd: root }).status !== 0) {
    log(`\nUpdate stopped: git could not fast-forward. Nothing was changed.\nCheck the clone with: git -C "${root}" status`);
    return 1;
  }
  const after = head();

  const lockChanged = before !== after
    && run('git', ['diff', '--quiet', before, after, '--', 'package-lock.json'], { cwd: root }).status !== 0;
  if (lockChanged || !exists(path.join(root, 'node_modules'))) {
    log('Installing the app\'s dependencies...');
    if (run('npm', ['ci', '--no-audit', '--no-fund'], { cwd: root }).status !== 0) {
      log(`\nThe code is updated but npm ci failed. Run it again in ${root}.`);
      return 1;
    }
  }

  const list = run('ollama', ['list'], { capture: true });
  if (list.status !== 0) {
    log('Ollama is not installed or not running: the models were not updated.');
  } else {
    const models = publishedModels(list.output);
    if (!models.length) log('No published FlyMotion model is installed. Get one with: ollama pull Tromset/flymotion0.2lite');
    for (const model of models) {
      log(`Updating the model ${model}`);
      if (run('ollama', ['pull', model]).status !== 0) {
        log(`\nThe code is updated but ${model} could not be pulled. Run: ollama pull ${model}`);
        return 1;
      }
    }
  }

  log(before === after ? 'FlyMotion code already up to date.' : `FlyMotion updated: ${before.slice(0, 7)} -> ${after.slice(0, 7)}.`);
  return 0;
}

module.exports = { update, publishedModels };

if (require.main === module) process.exitCode = update();
