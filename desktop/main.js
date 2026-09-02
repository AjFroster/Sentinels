/* Sentinels desktop shell.
 *
 * The window is a thin wrapper around the local FastAPI server. Electron's job
 * here is the part a browser tab cannot do: own the backend process, be
 * reachable from a keystroke, and refuse to navigate anywhere off this machine.
 */
'use strict';

const { app, BrowserWindow, Tray, Menu, globalShortcut, ipcMain, shell } = require('electron');
const { spawn } = require('node:child_process');
const path = require('node:path');
const http = require('node:http');

const PORT = Number(process.env.SENTINELS_PORT || 8765);
const ORIGIN = `http://127.0.0.1:${PORT}`;
const REPO = path.join(__dirname, '..');

let win = null;
let tray = null;
let backend = null;
let quitting = false;

/* ---------- backend lifecycle ---------- */

function pythonBin() {
  // Prefer the project venv; fall back to whatever python is on PATH so the
  // app still starts on a machine that installed the package globally.
  const venv = path.join(REPO, '.venv', 'bin', 'uvicorn');
  return require('node:fs').existsSync(venv) ? venv : 'uvicorn';
}

function startBackend() {
  backend = spawn(pythonBin(), ['main:app', '--port', String(PORT), '--log-level', 'warning'], {
    cwd: REPO,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUNBUFFERED: '1' },
  });
  backend.stdout.on('data', (d) => process.stdout.write(`[api] ${d}`));
  backend.stderr.on('data', (d) => process.stderr.write(`[api] ${d}`));
  backend.on('exit', (code) => {
    backend = null;
    if (!quitting) {
      console.error(`[api] backend exited with ${code}`);
      if (win) win.webContents.send('backend-down');
    }
  });
}

function waitForBackend(timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve, reject) => {
    const poll = () => {
      http.get(`${ORIGIN}/health`, (res) => {
        res.resume();
        if (res.statusCode === 200) resolve();
        else retry();
      }).on('error', retry);
    };
    const retry = () => {
      if (Date.now() > deadline) reject(new Error('backend did not start in time'));
      else setTimeout(poll, 300);
    };
    poll();
  });
}

function stopBackend() {
  if (!backend) return;
  quitting = true;
  backend.kill('SIGTERM');
  // SIGTERM is enough for uvicorn; escalate only if it is still up.
  setTimeout(() => { if (backend) backend.kill('SIGKILL'); }, 3000);
}

/* ---------- window ---------- */

function createWindow() {
  win = new BrowserWindow({
    width: 1180,
    height: 820,
    minWidth: 900,
    minHeight: 560,
    show: false,
    // The page draws its own titlebar -- the airgap badge lives there, so it
    // has to be part of the document rather than OS chrome.
    frame: false,
    backgroundColor: '#0D0E15',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  win.loadURL(ORIGIN);
  win.once('ready-to-show', () => win.show());

  // A council that keeps your questions on the machine must not be able to
  // wander off it. Anything not served by the local backend is refused, and
  // genuine external links are handed to the real browser instead.
  const isLocal = (url) => url.startsWith(ORIGIN);
  win.webContents.on('will-navigate', (event, url) => {
    if (!isLocal(url)) {
      event.preventDefault();
      console.warn(`[nav] blocked in-window navigation to ${url}`);
    }
  });
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url) && !isLocal(url)) shell.openExternal(url);
    return { action: 'deny' };
  });

  win.on('close', (event) => {
    // Closing hides; the tray keeps it warm so the hotkey stays instant.
    if (!quitting) { event.preventDefault(); win.hide(); }
  });
  win.on('closed', () => { win = null; });
}

function toggleWindow() {
  if (!win) return createWindow();
  if (win.isVisible() && win.isFocused()) win.hide();
  else { win.show(); win.focus(); }
}

/* ---------- tray ---------- */

function createTray() {
  const { nativeImage } = require('electron');
  // A generated dot rather than a bundled asset: one less binary to keep in
  // sync, and the tray only needs to be findable, not detailed.
  const icon = nativeImage.createFromDataURL(
    'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAAXklEQVR42mNgGAWjYBSMglEwCkbBK'
    + 'BgFo2AUjIJRMApGwSgYBaNgFIyCUTAKRsEoGAWjYBSMglEwCkbBKBgFo2AUjIJRMApGwSgYBaNgFIyCUTAKRsEoGAUDDwBt'
    + 'AAGxWvoZAAAAAElFTkSuQmCC'
  );
  tray = new Tray(icon);
  tray.setToolTip('Sentinels');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Show Sentinels', click: toggleWindow },
    { label: 'Pose a question', click: () => { toggleWindow(); win?.webContents.send('focus-composer'); } },
    { type: 'separator' },
    { label: 'Quit', click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on('click', toggleWindow);
}

/* ---------- window controls from the custom titlebar ---------- */

ipcMain.on('window:minimize', () => win?.minimize());
ipcMain.on('window:maximize', () => (win?.isMaximized() ? win.unmaximize() : win?.maximize()));
ipcMain.on('window:close', () => win?.hide());

/* ---------- boot ---------- */

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', toggleWindow);

  app.whenReady().then(async () => {
    startBackend();
    try {
      await waitForBackend();
    } catch (err) {
      console.error(`[boot] ${err.message}`);
    }
    createWindow();
    createTray();
    // Same chord the web page listens for, so the habit works either way.
    globalShortcut.register('CommandOrControl+Shift+K', () => {
      toggleWindow();
      win?.webContents.send('focus-composer');
    });
  });

  app.on('window-all-closed', () => { /* tray keeps the app alive */ });
  app.on('before-quit', () => { quitting = true; });
  app.on('will-quit', () => { globalShortcut.unregisterAll(); stopBackend(); });
}
