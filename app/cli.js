#!/usr/bin/env node
// `flymotion`: starts the test app from anywhere once `npm run shortcut` has linked this package.
// `flymotion update`: updates the code, the dependencies and the installed models (app/update.js).
const { spawn } = require('node:child_process');
const path = require('node:path');

// `flymotion update` comes before Electron is loaded: it must work when the dependencies are missing or broken.
if (process.argv[2] === 'update') process.exit(require('./update.js').update());

const electron = require('electron'); // from Node, the path of the Electron binary
const child = spawn(electron, [path.resolve(__dirname, '..'), ...process.argv.slice(2)], { stdio: 'inherit' });
child.on('exit', (code, signal) => process.exit(signal ? 1 : code ?? 0));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
