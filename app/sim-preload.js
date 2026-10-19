// Runs in the simulator page (remote content): it can only report the driver's events to the app.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('flymotionHost', {
  emit: (event) => ipcRenderer.send('sim:emit', event),
});
