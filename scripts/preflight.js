"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const rootDir = path.resolve(__dirname, "..");
const backendUvicorn = path.join(rootDir, "backend", ".venv", "bin", "uvicorn");
const frontendCraco = path.join(rootDir, "frontend", "node_modules", ".bin", "craco");

// Single source of truth for the dev ports. The orchestrator passes these down
// to every child so an ambient `PORT` or a stale `frontend/.env` can never
// silently move the stack onto a port another service already owns.
// Override explicitly with AILAB_BACKEND_PORT / AILAB_FRONTEND_PORT.
const BACKEND_PORT = Number(process.env.AILAB_BACKEND_PORT || 8001);
const FRONTEND_PORT = Number(process.env.AILAB_FRONTEND_PORT || 3002);

function isExecutable(filePath) {
  try {
    fs.accessSync(filePath, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

function checkBackend() {
  if (isExecutable(backendUvicorn)) {
    return true;
  }

  console.error(`Backend uvicorn is missing: ${path.relative(rootDir, backendUvicorn)}`);
  console.error(
    "Create the venv and install dependencies with: " +
      "python3 -m venv backend/.venv && " +
      "backend/.venv/bin/python -m pip install -r backend/requirements.txt",
  );
  return false;
}

function checkFrontend() {
  if (isExecutable(frontendCraco)) {
    return true;
  }

  console.error(
    `Frontend dependencies are missing: ${path.relative(rootDir, frontendCraco)}`,
  );
  console.error("Install them with: corepack yarn --cwd frontend install");
  return false;
}

// --- process/port hygiene -------------------------------------------------
// Orphaned dev processes are the recurring cause of "ERR_CONNECTION_REFUSED on
// :8001": a previous uvicorn --reload can survive a terminal close and keep the
// port bound while no longer serving. We match only processes that clearly
// belong to *this* checkout, so unrelated services (e.g. a container on :3000)
// are never touched.

const repoMarkers = [
  path.join(rootDir, "scripts", "dev.js"),
  path.join(rootDir, "scripts", "frontend-dev.js"),
  path.join(rootDir, "frontend", "node_modules", "@craco"),
  path.join(rootDir, "frontend", "node_modules", ".bin", "craco"),
  path.join(rootDir, "frontend", "node_modules", "react-scripts"),
  path.join(rootDir, "frontend", "node_modules", "fork-ts-checker-webpack-plugin"),
  path.join(rootDir, "backend", ".venv", "bin", "uvicorn"),
];
const backendDir = path.join(rootDir, "backend");
const backendUvicornRelative = path.join("backend", ".venv", "bin", "uvicorn");

function readCmdline(pid) {
  try {
    return fs.readFileSync(`/proc/${pid}/cmdline`, "utf8").replace(/\0/g, " ").trim();
  } catch {
    return "";
  }
}

function isAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function processCwd(pid) {
  try {
    return fs.realpathSync(`/proc/${pid}/cwd`);
  } catch {
    return "";
  }
}

function processGroupId(pid) {
  try {
    const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
    const close = stat.lastIndexOf(")");
    const fields = stat.slice(close + 2).trim().split(/\s+/);
    const groupId = Number(fields[2]);
    return Number.isFinite(groupId) ? groupId : null;
  } catch {
    return null;
  }
}

function sleepMs(ms) {
  if (ms <= 0) return;
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

// Never reap ourselves or any parent (the shell/npm that invoked us).
function ancestorPids() {
  const seen = new Set();
  let pid = process.pid;
  while (pid && pid > 1 && !seen.has(pid)) {
    seen.add(pid);
    try {
      const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
      const close = stat.lastIndexOf(")");
      const ppid = Number(stat.slice(close + 2).trim().split(/\s+/)[1]);
      pid = Number.isFinite(ppid) ? ppid : 0;
    } catch {
      break;
    }
  }
  return seen;
}

function isRepoProcess(pid) {
  const cmd = readCmdline(pid);
  if (!cmd) return false;
  if (repoMarkers.some((marker) => cmd.includes(marker))) return true;
  if (cmd.includes(backendUvicornRelative) && processCwd(pid) === rootDir) return true;
  // Also recognize ``python -m uvicorn`` when it was launched from this
  // checkout; that form does not contain the absolute uvicorn marker.
  return processCwd(pid) === backendDir && /(?:^|\s)-m\s+uvicorn(?:\s|$)/.test(cmd);
}

// The multiprocessing worker spawned by ``uvicorn --reload`` does not retain
// the uvicorn executable in its command line (it appears as ``spawn_main``),
// so command-line markers alone miss the process that actually owns the port.
// Limit this fallback to a Python reload worker in this checkout's root or
// backend directory; it cannot match unrelated services on the same machine.
function isRepoReloadWorker(pid) {
  const cmd = readCmdline(pid);
  const cwd = processCwd(pid);
  return Boolean(
    cmd &&
      cmd.includes("spawn_main") &&
      cmd.includes("python") &&
      (cwd === rootDir || cwd === backendDir),
  );
}

function childPids(parentPid) {
  if (process.platform !== "linux") return [];
  let entries;
  try {
    entries = fs.readdirSync("/proc");
  } catch {
    return [];
  }
  const children = [];
  for (const entry of entries) {
    if (!/^[0-9]+$/.test(entry)) continue;
    const pid = Number(entry);
    try {
      const stat = fs.readFileSync(`/proc/${pid}/stat`, "utf8");
      const close = stat.lastIndexOf(")");
      const fields = stat.slice(close + 2).trim().split(/\s+/);
      if (Number(fields[1]) === parentPid) children.push(pid);
    } catch {
      // The process may exit while the proc directory is being scanned.
    }
  }
  return children;
}

function expandProcessTree(roots) {
  const all = new Set(roots);
  const queue = [...all];
  while (queue.length) {
    const parent = queue.shift();
    for (const child of childPids(parent)) {
      if (all.has(child)) continue;
      all.add(child);
      queue.push(child);
    }
  }
  return [...all];
}

function findStaleDevProcesses() {
  if (process.platform !== "linux") return [];
  const protectedPids = ancestorPids();
  let entries;
  try {
    entries = fs.readdirSync("/proc");
  } catch {
    return [];
  }
  const found = [];
  for (const entry of entries) {
    if (!/^[0-9]+$/.test(entry)) continue;
    const pid = Number(entry);
    if (protectedPids.has(pid)) continue;
    if (isRepoProcess(pid)) found.push(pid);
  }
  return found.sort((a, b) => a - b);
}

// Returns listening PIDs, [] when the port is free, or null when detection is
// unavailable (e.g. no `lsof`).
function listeningPids(port) {
  try {
    const out = execFileSync(
      "lsof",
      ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN", "-t"],
      { stdio: ["ignore", "pipe", "ignore"] },
    ).toString();
    return out.split(/\s+/).filter(Boolean).map(Number);
  } catch (error) {
    if (error && error.status === 1) return [];
    return null;
  }
}

function reapPids(pids) {
  // Reap the complete process tree. Killing only the reload parent leaves its
  // multiprocessing worker listening with a MongoClient that was already
  // closed during shutdown, which surfaces as auth 500s without CORS headers.
  const alive = expandProcessTree(pids).filter(isAlive);
  if (!alive.length) return [];

  console.log(`[ai-lab] Cleaning up ${alive.length} stale dev process(es): ${alive.join(", ")}`);
  for (const pid of alive) {
    try {
      const groupId = processGroupId(pid);
      // Detached dev services use their PID as the process-group leader. Kill
      // the group so reload workers are included; otherwise signal the member
      // directly (e.g. when launched from an interactive shell).
      process.kill(groupId === pid ? -pid : pid, "SIGTERM");
    } catch {
      // already gone
    }
  }

  const deadline = Date.now() + 4000;
  while (Date.now() < deadline && alive.some(isAlive)) {
    sleepMs(200);
  }

  for (const pid of alive) {
    if (isAlive(pid)) {
      try {
        const groupId = processGroupId(pid);
        process.kill(groupId === pid ? -pid : pid, "SIGKILL");
      } catch {
        // already gone
      }
    }
  }
  return alive;
}

// Reap every stale process from this checkout (used by the full orchestrator).
function reapStaleDevProcesses() {
  return reapPids(findStaleDevProcesses());
}

// Reap only the stale listener(s) of one port that belong to this checkout.
// Safer for the single-service scripts, which must not touch the sibling.
function reapPort(port) {
  const pids = listeningPids(port) || [];
  return reapPids(pids.filter((pid) => isRepoProcess(pid) || isRepoReloadWorker(pid)));
}

function assertPortsAvailable(ports) {
  let ok = true;
  for (const port of ports) {
    const pids = listeningPids(port);
    if (!pids || pids.length === 0) continue;
    ok = false;
    for (const pid of pids) {
      const cmd = readCmdline(pid);
      console.error(`[ai-lab] Port ${port} is already in use by PID ${pid}${cmd ? `: ${cmd}` : ""}`);
    }
    console.error(`[ai-lab] Free it with: kill ${pids.join(" ")}`);
  }
  return ok;
}

module.exports = {
  BACKEND_PORT,
  FRONTEND_PORT,
  ancestorPids,
  assertPortsAvailable,
  backendUvicorn,
  checkBackend,
  checkFrontend,
  findStaleDevProcesses,
  frontendCraco,
  listeningPids,
  reapPort,
  reapStaleDevProcesses,
  rootDir,
};
