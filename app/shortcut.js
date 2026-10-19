#!/usr/bin/env node
// `npm run shortcut`: puts a FlyMotion icon on the desktop and installs the `flymotion` command (npm link).
// `npm run unshortcut`: removes both.
//
//   macOS    ~/Desktop/FlyMotion.app, a small app bundle that starts Electron on this repository
//   Linux    ~/Desktop/flymotion.desktop, and the same entry in the applications menu
//   Windows  FlyMotion.lnk on the desktop
//
// On macOS and Linux the icon opens the app without a terminal, so its launcher records the PATH and the Python of
// this shell: Finder and desktop launchers start apps with a minimal PATH that often lacks Homebrew's or pyenv's python3.
const { execFileSync, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const ASSETS = path.join(__dirname, 'assets');
const NAME = 'FlyMotion';

function findOnPath(command) {
  const extensions = process.platform === 'win32' ? ['.exe', '.cmd', ''] : [''];
  for (const folder of (process.env.PATH || '').split(path.delimiter)) {
    for (const extension of extensions) {
      const candidate = path.join(folder, command + extension);
      if (folder && fs.existsSync(candidate) && fs.statSync(candidate).isFile()) return candidate;
    }
  }
  return null;
}

function desktopFolder() {
  if (process.platform === 'win32') {
    // The real desktop, also when OneDrive moves it.
    return execFileSync('powershell', ['-NoProfile', '-Command', "[Environment]::GetFolderPath('Desktop')"], { encoding: 'utf8' }).trim();
  }
  if (process.platform === 'linux') {
    try {
      return execFileSync('xdg-user-dir', ['DESKTOP'], { encoding: 'utf8' }).trim();
    } catch {
      // no xdg-user-dirs: the usual folder
    }
  }
  return path.join(os.homedir(), 'Desktop');
}

const quote = (value) => `'${String(value).replace(/'/g, "'\\''")}'`; // POSIX shell single quotes

function launcherScript(electron, python) {
  return [
    '#!/bin/sh',
    '# FlyMotion launcher, written by `npm run shortcut`. Run it again if you move the repository.',
    `export PATH=${quote(process.env.PATH || '/usr/bin:/bin')}`,
    ...(python ? [`export FLYMOTION_PYTHON=${quote(python)}`] : []),
    `cd ${quote(ROOT)}`,
    `exec ${quote(electron)} ${quote(ROOT)} "$@"`,
    '',
  ].join('\n');
}

function macApp(desktop, electron, python) {
  const bundle = path.join(desktop, `${NAME}.app`);
  fs.rmSync(bundle, { recursive: true, force: true });
  fs.mkdirSync(path.join(bundle, 'Contents', 'MacOS'), { recursive: true });
  fs.mkdirSync(path.join(bundle, 'Contents', 'Resources'), { recursive: true });
  fs.writeFileSync(path.join(bundle, 'Contents', 'Info.plist'), `<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>${NAME}</string>
  <key>CFBundleDisplayName</key><string>${NAME}</string>
  <key>CFBundleIdentifier</key><string>com.tromset.flymotion.launcher</string>
  <key>CFBundleExecutable</key><string>${NAME}</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleVersion</key><string>1</string>
</dict>
</plist>
`);
  const executable = path.join(bundle, 'Contents', 'MacOS', NAME);
  fs.writeFileSync(executable, launcherScript(electron, python), { mode: 0o755 });
  fs.copyFileSync(path.join(ASSETS, 'icon.icns'), path.join(bundle, 'Contents', 'Resources', 'icon.icns'));
  spawnSync('touch', [bundle]); // Finder picks up the new icon
  return bundle;
}

function linuxEntry(desktop, electron, python) {
  const launcher = path.join(os.homedir(), '.local', 'share', 'flymotion', 'flymotion.sh');
  fs.mkdirSync(path.dirname(launcher), { recursive: true });
  fs.writeFileSync(launcher, launcherScript(electron, python), { mode: 0o755 });
  const entry = [
    '[Desktop Entry]',
    'Type=Application',
    `Name=${NAME}`,
    'Comment=FlyMotion drives the simulated Microduck',
    `Exec="${launcher}"`,
    `Icon=${path.join(ASSETS, 'icon.png')}`,
    'Terminal=false',
    'Categories=Development;Science;',
    '',
  ].join('\n');
  const menu = path.join(os.homedir(), '.local', 'share', 'applications', 'flymotion.desktop');
  const icon = path.join(desktop, 'flymotion.desktop');
  for (const file of [menu, icon]) {
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, entry, { mode: 0o755 });
  }
  spawnSync('gio', ['set', icon, 'metadata::trusted', 'true']); // GNOME: launchable without a confirmation
  return icon;
}

function windowsShortcut(desktop, electron) {
  const link = path.join(desktop, `${NAME}.lnk`);
  const ps = (value) => `'${String(value).replace(/'/g, "''")}'`;
  const script = [
    '$shell = New-Object -ComObject WScript.Shell',
    `$link = $shell.CreateShortcut(${ps(link)})`,
    `$link.TargetPath = ${ps(electron)}`,
    `$link.Arguments = '"' + ${ps(ROOT)} + '"'`,
    `$link.WorkingDirectory = ${ps(ROOT)}`,
    `$link.IconLocation = ${ps(path.join(ASSETS, 'icon.ico'))}`,
    "$link.Description = 'FlyMotion'",
    '$link.Save()',
  ].join('; ');
  execFileSync('powershell', ['-NoProfile', '-Command', script], { stdio: 'inherit' });
  return link;
}

function npm(args) {
  // Under `npm run`, npm_execpath is the npm that runs us: use the same one.
  const [command, prefix] = process.env.npm_execpath ? [process.execPath, [process.env.npm_execpath]] : ['npm', []];
  return spawnSync(command, [...prefix, ...args], { cwd: ROOT, stdio: 'inherit', shell: !process.env.npm_execpath && process.platform === 'win32' });
}

function install() {
  const electron = require('electron'); // the binary's path; downloads it on first use
  const python = findOnPath(process.platform === 'win32' ? 'python' : 'python3');
  if (!python) console.warn('flymotion: no python3 on the PATH; the app needs Python 3.9 or later for its bridge.');
  const desktop = desktopFolder();
  fs.mkdirSync(desktop, { recursive: true });
  const make = { darwin: macApp, linux: linuxEntry, win32: windowsShortcut }[process.platform];
  if (!make) throw new Error(`no desktop icon for ${process.platform}`);
  console.log(`Desktop icon: ${make(desktop, electron, python)}`);
  if (npm(['link']).status === 0) {
    console.log('Command: flymotion');
    return 0;
  }
  console.error('flymotion: `npm link` failed, so there is no `flymotion` command yet (the desktop icon works).\n'
    + 'If npm lacks the rights on its global folder, run `sudo npm run shortcut`, or use a Node installed with nvm.');
  return 1;
}

function remove() {
  const desktop = desktopFolder();
  const files = {
    darwin: [path.join(desktop, `${NAME}.app`)],
    linux: [path.join(desktop, 'flymotion.desktop'), path.join(os.homedir(), '.local', 'share', 'applications', 'flymotion.desktop'),
      path.join(os.homedir(), '.local', 'share', 'flymotion')],
    win32: [path.join(desktop, `${NAME}.lnk`)],
  }[process.platform] || [];
  for (const file of files) fs.rmSync(file, { recursive: true, force: true });
  npm(['unlink', '--global', 'flymotion']);
  console.log('FlyMotion icon and command removed.');
  return 0;
}

module.exports = { launcherScript, findOnPath, macApp, linuxEntry };

if (require.main === module) {
  try {
    process.exitCode = process.argv.includes('--remove') ? remove() : install();
  } catch (error) {
    console.error(`flymotion: ${error.message}`);
    process.exitCode = 1;
  }
}
