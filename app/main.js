// FlyMotion test app: Pollen's Microduck simulator on the left, the FlyMotion panel on the right.
//
//   panel (renderer/)  --ipc-->  main  --stdin/stdout-->  python -m flymotion serve  (plans with Ollama)
//                                 |
//                                 +--executeJavaScript-->  simulator page + sim/driver.js  (MuJoCo + ONNX policies)
const fs = require('node:fs');
const path = require('node:path');
const { app, BrowserWindow, WebContentsView, ipcMain, shell } = require('electron');
const { Bridge } = require('./bridge');

const SIM_URL = process.env.FLYMOTION_SIM_URL || 'https://pollen-robotics-microduck-simulator.hf.space/?boot=1';
const PANEL_WIDTH = 440;
const ICON = path.join(__dirname, 'assets', 'icon.png');
const DRIVER = fs.readFileSync(path.join(__dirname, 'sim', 'driver.js'), 'utf8');
const SIM_METHODS = new Set(['play', 'stop', 'reset', 'status']);

let mainWindow = null;
let sim = null;
let bridge = null;

function send(channel, payload) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, payload);
}

function startBridge() {
  bridge?.stop();
  bridge = new Bridge({ robot: process.env.FLYMOTION_ROBOT || 'microduck-sim', model: process.env.FLYMOTION_MODEL }).start();
  bridge.on('log', (line) => send('bridge:log', line));
  bridge.on('exit', (reason) => send('bridge:exit', reason));
}

function layout() {
  const [width, height] = mainWindow.getContentSize();
  sim.setBounds({ x: 0, y: 0, width: Math.max(0, width - PANEL_WIDTH), height });
}

function sameOrigin(url) {
  try {
    return new URL(url).origin === new URL(SIM_URL).origin;
  } catch {
    return false;
  }
}

function createSimView() {
  sim = new WebContentsView({
    webPreferences: { preload: path.join(__dirname, 'sim-preload.js'), contextIsolation: true, sandbox: true },
  });
  const contents = sim.webContents;
  // The simulator's links (shop, back to Pollen's site) open in the browser, never inside the app.
  contents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });
  contents.on('will-navigate', (event, url) => {
    if (!sameOrigin(url)) {
      event.preventDefault();
      shell.openExternal(url);
    }
  });
  // Escape stops everything, even while the simulator has the keyboard focus.
  contents.on('before-input-event', (_event, input) => {
    if (input.type === 'keyDown' && input.key === 'Escape') stopAll();
  });
  contents.on('did-fail-load', (_event, code, description, url, isMainFrame) => {
    if (isMainFrame) send('sim:event', { type: 'error', error: `chargement impossible (${description || code}) : ${url}` });
  });
  contents.on('did-finish-load', () => {
    contents.executeJavaScript(`${DRIVER}\n;window.__flymotionInstall.install(window)`).catch((error) =>
      send('sim:event', { type: 'error', error: `injection du driver : ${error.message}` }));
  });
  mainWindow.contentView.addChildView(sim);
  layout();
  loadSim();
}

function loadSim() {
  send('sim:event', { type: 'loading', url: SIM_URL });
  sim.webContents.loadURL(SIM_URL);
}

// Stops the simulated duck and any run on the real robot (which then ends with robot.stop).
function stopAll() {
  const simulated = sim?.webContents.executeJavaScript('window.__flymotion?.stop(), true');
  const robot = bridge?.request({ type: 'stop' });
  return Promise.allSettled([simulated, robot]).then(() => true);
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 860,
    minWidth: 980,
    minHeight: 600,
    backgroundColor: '#08080c',
    title: 'FlyMotion · Microduck',
    icon: ICON,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, sandbox: true },
  });
  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));
  mainWindow.on('resize', layout);
  mainWindow.on('closed', () => {
    mainWindow = null;
  });
  createSimView();
}

ipcMain.handle('app:info', () => ({ simUrl: SIM_URL, panelWidth: PANEL_WIDTH, versions: { electron: process.versions.electron } }));

ipcMain.handle('bridge:request', async (event, message) => {
  const reply = await bridge.request(message, (progress) => event.sender.send('bridge:event', progress));
  return reply;
});

ipcMain.handle('bridge:restart', () => {
  startBridge();
  return true;
});

ipcMain.handle('sim:call', async (_event, method, ...args) => {
  if (!SIM_METHODS.has(method)) throw new Error(`méthode inconnue ${method}`);
  const invoke = `window.__flymotion.${method}(...${JSON.stringify(args)})`;
  // play() runs for the whole plan and reports through events: the IPC reply does not wait for it.
  const script = `window.__flymotion ? ${method === 'play' ? `(${invoke}, true)` : `(${invoke} ?? true)`} : false`;
  const result = await sim.webContents.executeJavaScript(script);
  if (result === false) throw new Error("le simulateur n'est pas encore prêt");
  return result;
});

ipcMain.handle('sim:reload', () => {
  loadSim();
  return true;
});

ipcMain.handle('app:stop', () => stopAll());

ipcMain.on('sim:emit', (event, payload) => {
  if (event.sender !== sim?.webContents) return;
  if (payload && typeof payload === 'object' && typeof payload.type === 'string') send('sim:event', payload);
});

app.setName('FlyMotion');

app.whenReady().then(() => {
  app.dock?.setIcon(ICON); // macOS: the Dock shows the FlyMotion duck, not Electron's logo
  startBridge();
  createWindow();
});

app.on('window-all-closed', () => {
  bridge?.stop();
  app.quit();
});
