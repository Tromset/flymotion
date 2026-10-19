// The panel's only door to the main process: bridge requests, simulator calls, and their events.
const { contextBridge, ipcRenderer } = require('electron');

const listen = (channel) => (callback) => {
  const handler = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, handler);
  return () => ipcRenderer.removeListener(channel, handler);
};

contextBridge.exposeInMainWorld('flymotion', {
  info: () => ipcRenderer.invoke('app:info'),
  request: (message) => ipcRenderer.invoke('bridge:request', message),
  restartBridge: () => ipcRenderer.invoke('bridge:restart'),
  sim: (method, ...args) => ipcRenderer.invoke('sim:call', method, ...args),
  reloadSim: () => ipcRenderer.invoke('sim:reload'),
  stopAll: () => ipcRenderer.invoke('app:stop'),
  onBridgeEvent: listen('bridge:event'),
  onBridgeLog: listen('bridge:log'),
  onBridgeExit: listen('bridge:exit'),
  onSimEvent: listen('sim:event'),
});
