"use strict";

const { spawn } = require("child_process");
const {
  FRONTEND_PORT,
  assertPortsAvailable,
  checkFrontend,
  frontendCraco,
  reapPort,
  rootDir,
} = require("./preflight");

if (!checkFrontend()) {
  process.exit(1);
}

// Drop only a stale listener of *our* frontend port; never touch the backend.
reapPort(FRONTEND_PORT);
if (!assertPortsAvailable([FRONTEND_PORT])) {
  process.exit(1);
}

const port = String(FRONTEND_PORT);

const frontend = spawn(frontendCraco, ["start"], {
  cwd: `${rootDir}/frontend`,
  env: { ...process.env, PORT: port },
  stdio: "inherit",
  detached: true,
});

console.log(`[ai-lab] frontend → http://localhost:${port}`);

let shuttingDown = false;
let forceKillTimer = null;

function signalTree(signal) {
  if (frontend.pid == null) return;
  try {
    process.kill(-frontend.pid, signal);
  } catch {
    try {
      frontend.kill(signal);
    } catch {
      // already gone
    }
  }
}

function shutdown() {
  if (shuttingDown) return;
  shuttingDown = true;
  signalTree("SIGTERM");
  forceKillTimer = setTimeout(() => signalTree("SIGKILL"), 5000);
  if (forceKillTimer.unref) forceKillTimer.unref();
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
process.on("SIGHUP", shutdown);
process.on("exit", () => signalTree("SIGKILL"));

frontend.on("error", (error) => {
  console.error(`[ai-lab] Unable to start local CRACO: ${error.message}`);
  process.exitCode = 1;
});
frontend.on("exit", (code, signal) => {
  if (signal && !shuttingDown) {
    process.exitCode = 1;
  } else if (code !== null) {
    process.exitCode = code;
  }
});
