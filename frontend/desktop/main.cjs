const { app, BrowserWindow, dialog, shell, Menu } = require('electron');
const { spawn } = require('node:child_process');
const { randomBytes } = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const appRoot = path.resolve(__dirname, '../..');
const localDataRoot = path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'MeetingAssistant');
const devFrontendPort = 5179;
app.setPath('userData', localDataRoot);

let backendProcess = null;
let mainWindow = null;
let quitting = false;
let quitTask = null;
let backendPort = null;
let sessionToken = null;
let logStream = null;
let viteServer = null;

function getRuntimePaths() {
  if (app.isPackaged) {
    const resources = process.resourcesPath;
    return {
      command: path.join(resources, 'backend', 'MeetingAssistantBackend.exe'),
      args: ['--electron'],
      cwd: path.join(resources, 'backend'),
      frontend: path.join(resources, 'backend', '_internal', 'frontend', 'dist'),
      model: path.join(app.getPath('userData'), 'models'),
    };
  }
  return {
    command: path.join(appRoot, '.venv', 'Scripts', 'python.exe'),
    args: ['-m', 'meeting_assistant.launch', '--electron'],
    cwd: appRoot,
    frontend: path.join(appRoot, 'frontend', 'dist'),
    model: path.join(app.getPath('userData'), 'models'),
  };
}

function verifyFiles(runtime) {
  const required = app.isPackaged
    ? [path.join(runtime.frontend, 'index.html')]
    : [
        path.join(appRoot, 'frontend', 'index.html'),
        path.join(appRoot, 'frontend', 'vite.config.ts'),
        path.join(appRoot, 'frontend', 'node_modules', 'vite', 'package.json'),
      ];
  const missing = required.filter(file => !fs.existsSync(file));
  if (!fs.existsSync(runtime.command)) missing.push(runtime.command);
  if (missing.length) {
    const nextStep = app.isPackaged ? '请重新运行桌面安装包构建脚本。' : '请先安装前端依赖并准备好项目 Python 环境。';
    throw new Error(`缺少桌面运行文件：\n${missing.join('\n')}\n\n${nextStep}`);
  }
}

function appendLog(text) {
  if (logStream) logStream.write(text);
}

function startBackend(runtime) {
  return new Promise((resolve, reject) => {
    sessionToken = randomBytes(32).toString('hex');
    const env = {
      ...process.env,
      MEETING_ASSISTANT_DESKTOP: '1',
      MEETING_ASSISTANT_DEV_FRONTEND_PORT: app.isPackaged ? '' : String(devFrontendPort),
      MEETING_ASSISTANT_DESKTOP_TOKEN: sessionToken,
      MEETING_ASSISTANT_ROOT: app.isPackaged ? runtime.cwd : appRoot,
      MEETING_ASSISTANT_DATA: path.join(app.getPath('userData'), 'data'),
      MEETING_ASSISTANT_MODEL_DIR: runtime.model,
      MEETING_ASSISTANT_FRONTEND: runtime.frontend,
    };
    const child = spawn(runtime.command, runtime.args, { cwd: runtime.cwd, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
    backendProcess = child;
    let stdoutBuffer = '';
    let settled = false;
    const timeout = setTimeout(() => finish(new Error('本地服务启动超时。请查看日志：' + path.join(app.getPath('userData'), 'logs', 'backend.log'))), 90000);

    function finish(error, message) {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      if (error) reject(error);
      else { backendPort = message.port; resolve(message); }
    }

    child.once('error', error => finish(new Error(`无法启动本地语音服务：${error.message}`)));
    child.once('exit', (code, signal) => {
      appendLog(`\n[backend exit] code=${code} signal=${signal}\n`);
      if (!settled) finish(new Error(`本地语音服务提前退出（${code ?? signal ?? 'unknown'}）。请检查 backend.log。`));
      else if (!quitting && mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent('<meta charset="utf-8"><h2>MeetingAssistant 本地服务已停止</h2><p>请关闭并重新打开应用。</p>'));
      }
    });
    child.stderr.on('data', data => appendLog(data.toString()));
    child.stdout.on('data', data => {
      stdoutBuffer += data.toString();
      const lines = stdoutBuffer.split(/\r?\n/);
      stdoutBuffer = lines.pop() || '';
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const message = JSON.parse(line);
          if (message.type === 'meeting-assistant-ready' && Number.isInteger(message.port)) finish(null, message);
          else appendLog(`[stdout] ${line}\n`);
        } catch { appendLog(`[stdout] ${line}\n`); }
      }
    });
  });
}

function createWindow(baseUrl) {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1024,
    minHeight: 680,
    show: false,
    autoHideMenuBar: true,
    titleBarStyle: 'hidden',
    titleBarOverlay: {
      color: '#f7f8fa',
      symbolColor: '#53615d',
      height: 36,
    },
    backgroundColor: '#f7f8fa',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webviewTag: false,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https:\/\//i.test(url)) void shell.openExternal(url);
    return { action: 'deny' };
  });
  mainWindow.webContents.on('will-navigate', (event, url) => {
    if (url !== baseUrl && !url.startsWith(`${baseUrl}/`)) event.preventDefault();
  });
  mainWindow.once('ready-to-show', () => mainWindow && mainWindow.show());
  mainWindow.on('close', event => {
    if (!quitting) {
      event.preventDefault();
      void gracefulQuit();
    }
  });
  mainWindow.on('closed', () => { mainWindow = null; });
  void mainWindow.loadURL(baseUrl);
}

async function startFrontend(backendUrl) {
  if (app.isPackaged) return backendUrl;

  process.env.MEETING_ASSISTANT_BACKEND_URL = backendUrl;
  const { createServer } = await import('vite');
  viteServer = await createServer({
    configFile: path.join(appRoot, 'frontend', 'vite.config.ts'),
    root: path.join(appRoot, 'frontend'),
    server: { host: '127.0.0.1', port: devFrontendPort, strictPort: true },
  });
  await viteServer.listen();
  const address = viteServer.httpServer.address();
  if (!address || typeof address === 'string') {
    await viteServer.close();
    viteServer = null;
    throw new Error('无法读取 Vite 开发服务器地址。');
  }
  viteServer.printUrls();
  return `http://127.0.0.1:${address.port}`;
}

async function waitForBackendExit(timeoutMs) {
  if (!backendProcess || backendProcess.exitCode !== null || backendProcess.signalCode !== null) return;
  await Promise.race([
    new Promise(resolve => backendProcess.once('exit', resolve)),
    new Promise(resolve => setTimeout(resolve, timeoutMs)),
  ]);
}

function gracefulQuit() {
  if (quitTask) return quitTask;
  quitTask = (async () => {
    quitting = true;
    if (backendProcess && backendProcess.exitCode === null && backendProcess.signalCode === null) {
      if (backendPort) {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 70000);
        try {
          const response = await fetch(`http://127.0.0.1:${backendPort}/api/desktop/shutdown`, {
            method: 'POST',
            headers: { Cookie: `ma_session=${sessionToken}` },
            signal: controller.signal,
          });
          if (!response.ok) appendLog(`[shutdown request failed] HTTP ${response.status}\n`);
        } catch (error) { appendLog(`[shutdown request failed] ${error.message}\n`); }
        finally { clearTimeout(timeout); }
      }
      await waitForBackendExit(15000);
      if (backendProcess.exitCode === null && backendProcess.signalCode === null) {
        backendProcess.kill();
        await waitForBackendExit(5000);
      }
    }
    if (viteServer) { await viteServer.close(); viteServer = null; }
    if (logStream) { await new Promise(resolve => logStream.end(resolve)); logStream = null; }
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.destroy();
    app.quit();
  })();
  return quitTask;
}

const hasInstance = app.requestSingleInstanceLock();
if (!hasInstance) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  app.on('before-quit', event => {
    if (!quitting) {
      event.preventDefault();
      void gracefulQuit();
    }
  });
  app.whenReady().then(async () => {
    try {
      Menu.setApplicationMenu(null);
      const logDirectory = path.join(app.getPath('userData'), 'logs');
      fs.mkdirSync(logDirectory, { recursive: true });
      logStream = fs.createWriteStream(path.join(logDirectory, 'backend.log'), { flags: 'a' });
      const runtime = getRuntimePaths();
      verifyFiles(runtime);
      const ready = await startBackend(runtime);
      const origin = `http://127.0.0.1:${ready.port}`;
      await require('electron').session.defaultSession.cookies.set({
        url: origin,
        name: 'ma_session',
        value: sessionToken,
        httpOnly: true,
        sameSite: 'strict',
        path: '/',
      });
      createWindow(await startFrontend(origin));
    } catch (error) {
      dialog.showErrorBox('MeetingAssistant 启动失败', error.message);
      await gracefulQuit();
    }
  });
  app.on('window-all-closed', () => { if (!quitting) void gracefulQuit(); });
}
