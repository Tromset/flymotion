#!/usr/bin/env node
// `flymotion`: starts the test app from anywhere once `npm run shortcut` has linked this package.
const { spawn } = require('node:child_process');
const path = require('node:path');

const electron = require('electron'); // from Node, the path of the Electron binary
const child = spawn(electron, [path.resolve(__dirname, '..'), ...process.argv.slice(2)], { stdio: 'inherit' });
child.on('exit', (code, signal) => process.exit(signal ? 1 : code ?? 0));
for (const signal of ['SIGINT', 'SIGTERM']) process.on(signal, () => child.kill(signal));
