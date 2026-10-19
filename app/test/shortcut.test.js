// The desktop icons `npm run shortcut` writes, in a temporary folder.
const test = require('node:test');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { launcherScript, macApp, linuxEntry } = require('../shortcut.js');

const ROOT = path.resolve(__dirname, '..', '..');
const posix = process.platform !== 'win32';

function fakeElectron(folder) {
  // Stands for Electron: prints the folder it runs in and its arguments, one per line.
  const file = path.join(folder, "fake electron's binary");
  fs.writeFileSync(file, '#!/bin/sh\npwd\nfor arg in "$@"; do echo "$arg"; done\necho "python=$FLYMOTION_PYTHON"\n', { mode: 0o755 });
  return file;
}

test('the launcher starts Electron on the repository, with the recorded Python, whatever the quotes and spaces', { skip: !posix }, () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), 'fly motion '));
  const launcher = path.join(folder, 'launch.sh');
  fs.writeFileSync(launcher, launcherScript(fakeElectron(folder), "/opt/py'thon 3/bin/python3"), { mode: 0o755 });
  const output = execFileSync(launcher, ['--extra'], { encoding: 'utf8', env: { HOME: os.homedir() } }).trim().split('\n');
  assert.deepEqual(output, [fs.realpathSync(ROOT), ROOT, '--extra', "python=/opt/py'thon 3/bin/python3"]);
});

test('macOS: FlyMotion.app has its plist, icon and launcher', { skip: !posix }, () => {
  const desktop = fs.mkdtempSync(path.join(os.tmpdir(), 'desktop-'));
  const bundle = macApp(desktop, fakeElectron(desktop), null);
  assert.equal(bundle, path.join(desktop, 'FlyMotion.app'));
  const plist = fs.readFileSync(path.join(bundle, 'Contents', 'Info.plist'), 'utf8');
  assert.match(plist, /<key>CFBundleExecutable<\/key><string>FlyMotion<\/string>/);
  assert.match(plist, /<key>CFBundleIconFile<\/key><string>icon<\/string>/);
  assert.equal(fs.readFileSync(path.join(bundle, 'Contents', 'Resources', 'icon.icns')).subarray(0, 4).toString(), 'icns');
  const executable = path.join(bundle, 'Contents', 'MacOS', 'FlyMotion');
  assert.ok(fs.statSync(executable).mode & 0o111);
  assert.equal(execFileSync(executable, { encoding: 'utf8' }).trim().split('\n')[1], ROOT);
  macApp(desktop, fakeElectron(desktop), null); // running the shortcut again replaces the bundle
});

test('Linux: a desktop entry on the desktop and in the applications menu', { skip: !posix }, (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'home-'));
  const saved = process.env.HOME;
  process.env.HOME = home;
  t.after(() => (process.env.HOME = saved));
  const desktop = path.join(home, 'Desktop');
  fs.mkdirSync(desktop);
  const icon = linuxEntry(desktop, fakeElectron(home), '/usr/bin/python3');
  const entry = fs.readFileSync(icon, 'utf8');
  assert.match(entry, /^Exec=".*\/\.local\/share\/flymotion\/flymotion\.sh"$/m);
  assert.match(entry, new RegExp(`^Icon=${path.join(ROOT, 'app', 'assets', 'icon.png')}$`, 'm'));
  assert.equal(fs.readFileSync(path.join(home, '.local', 'share', 'applications', 'flymotion.desktop'), 'utf8'), entry);
});
