"use strict";

const { spawn } = require("child_process");
const path = require("path");
const {
  BACKEND_PORT,
  FRONTEND_PORT,
  assertPortsAvailable,
  checkBackend,
  checkFrontend,
  reapStaleDevProcesses,
  rootDir,
} = require("./preflight");

if (!checkBackend() || !checkFrontend()) {
  process.exit(1);
}

// Remove orphans from a previous run before binding. This is what stops a
// crashed/hung session from poisoning every later `npm run dev`.
reapStaleDevProcesses();
if (!assertPortsAvailable([FRONTEND_PORT, BACKEND_PORT])) {
  process.exit(1);
}

const backendArgs = [
  "server:app",
  "--app-dir",
  "backend",
  "--host",
  "0.0.0.0",
  "--port",
  String(BACKEND_PORT),
  "--reload",
  // Watch only the backend. Watching the repo root made frontend builds and
  // editor temp files restart uvicorn constantly, and a reload landing on top
  // of an open SSE stream leaves the reloader stuck holding the port.
  "--reload-dir",
  "backend",
];

const children = [
  spawn(process.execPath, [require.resolve("./frontend-dev")], {
    cwd: rootDir,
    stdio: "inherit",
    detached: true,
    env: { ...process.env, PORT: String(FRONTEND_PORT) },
  }),
  spawn(path.join(rootDir, "backend", ".venv", "bin", "python"), ["-m", "uvicorn", ...backendArgs], {
    cwd: rootDir,
    stdio: "inherit",
    detached: true,
  }),
];

console.log(`[ai-lab] frontend → http://localhost:${FRONTEND_PORT}`);
console.log(`[ai-lab] backend  → http://localhost:${BACKEND_PORT}/api/`);

let shuttingDown = false;
let forceKillTimer = null;

// Each child is spawned detached, so its PID is its process-group id and a
// negative kill takes down the whole tree (reloader children, craco's
// fork-ts-checker workers, ...) instead of leaving orphans behind.
function signalTree(child, signal) {
  if (!child || child.pid == null) return;
  try {
    process.kill(-child.pid, signal);
  } catch {
    try {
      child.kill(signal);
    } catch {
      // already gone
    }
  }
}

function stopChildren(code) {
  if (shuttingDown) return;
  shuttingDown = true;
  process.exitCode = code;
  for (const child of children) signalTree(child, "SIGTERM");
  forceKillTimer = setTimeout(() => {
    for (const child of children) signalTree(child, "SIGKILL");
  }, 5000);
  if (forceKillTimer.unref) forceKillTimer.unref();
}

for (const child of children) {
  child.on("error", (error) => {
    console.error(`[ai-lab] Unable to start a development service: ${error.message}`);
    stopChildren(1);
  });
  child.on("exit", (code, signal) => {
    if (shuttingDown) return;
    console.error(
      `[ai-lab] A development service exited (code=${code ?? "null"}, signal=${signal ?? "null"}); stopping the rest.`,
    );
    stopChildren(code === 0 ? 1 : code || 1);
  });
}

function shutdown() {
  stopChildren(0);
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
process.on("SIGHUP", shutdown);
process.on("exit", () => {
  for (const child of children) signalTree(child, "SIGKILL");
});
