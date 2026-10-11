"use strict";

const {
  BACKEND_PORT,
  assertPortsAvailable,
  checkBackend,
  reapPort,
} = require("./preflight");

if (!checkBackend()) {
  process.exit(1);
}

// Clear a stale listener of our backend port before uvicorn tries to bind it.
reapPort(BACKEND_PORT);
if (!assertPortsAvailable([BACKEND_PORT])) {
  process.exit(1);
}
