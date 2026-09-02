/* The only bridge between the page and Electron.
 *
 * Deliberately tiny: three window controls and two inbound notifications. The
 * page is the same document a browser loads, so nothing here may be required
 * for it to work -- `window.sentinels` is absent in a browser and every caller
 * guards for that.
 */
'use strict';

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('sentinels', {
  isDesktop: true,
  minimize: () => ipcRenderer.send('window:minimize'),
  maximize: () => ipcRenderer.send('window:maximize'),
  close: () => ipcRenderer.send('window:close'),
  onFocusComposer: (fn) => ipcRenderer.on('focus-composer', () => fn()),
  onBackendDown: (fn) => ipcRenderer.on('backend-down', () => fn()),
});
