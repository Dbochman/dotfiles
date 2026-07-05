#!/usr/bin/env node
// roomba-cmd.js — direct dorita980 wrapper for CLI use
// Usage: node roomba-cmd.js <env-file> <command>
// Commands: status, state, start, stop, pause, resume, dock, find, wifi, mission

"use strict";

const fs = require("fs");
const net = require("net");
const path = require("path");

const VALID_COMMANDS = new Set([
  "status",
  "state",
  "start",
  "stop",
  "pause",
  "resume",
  "dock",
  "find",
  "wifi",
  "mission",
]);
const COMMAND_TIMEOUT_MS = 20000;

class RoombaCommandError extends Error {
  constructor(code, message, exitCode = 1) {
    super(message);
    this.code = code;
    this.exitCode = exitCode;
  }
}

function parseEnvFile(envFile) {
  let metadata;
  try {
    metadata = fs.lstatSync(envFile);
  } catch (_error) {
    throw new RoombaCommandError(
      "env_file_unreadable",
      "Unable to read the robot environment file",
      2,
    );
  }
  if (
    !metadata.isFile()
    || metadata.isSymbolicLink()
    || typeof process.getuid !== "function"
    || metadata.uid !== process.getuid()
    || (metadata.mode & 0o777) !== 0o600
  ) {
    throw new RoombaCommandError(
      "env_file_unsafe",
      "Robot environment must be an owner-only mode-0600 regular file",
      2,
    );
  }

  let content;
  try {
    content = fs.readFileSync(envFile, "utf8");
  } catch (_error) {
    throw new RoombaCommandError(
      "env_file_unreadable",
      "Unable to read the robot environment file",
      2,
    );
  }

  const env = {};
  for (const [index, rawLine] of content.split(/\r?\n/).entries()) {
    let line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    if (line.startsWith("export ")) line = line.slice(7).trim();

    const separator = line.indexOf("=");
    if (separator <= 0) {
      throw new RoombaCommandError(
        "env_file_invalid",
        `Invalid environment entry on line ${index + 1}`,
        2,
      );
    }

    const key = line.slice(0, separator).trim();
    let value = line.slice(separator + 1).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
      throw new RoombaCommandError(
        "env_file_invalid",
        `Invalid environment key on line ${index + 1}`,
        2,
      );
    }
    if (
      value.length >= 2
      && ((value.startsWith('"') && value.endsWith('"'))
        || (value.startsWith("'") && value.endsWith("'")))
    ) {
      value = value.slice(1, -1);
    }
    env[key] = value;
  }

  for (const key of ["BLID", "PASSWORD", "ROBOT_IP"]) {
    if (typeof env[key] !== "string" || env[key].trim() === "") {
      throw new RoombaCommandError(
        "env_missing_value",
        `Robot environment is missing ${key}`,
        2,
      );
    }
  }
  if (net.isIP(env.ROBOT_IP) === 0) {
    throw new RoombaCommandError(
      "env_invalid_ip",
      "ROBOT_IP must be a valid IP address",
      2,
    );
  }

  return env;
}

function loadDorita() {
  const doritaPath = path.join(
    process.env.HOME || "",
    ".openclaw/rest980/node_modules/dorita980",
  );
  try {
    const dorita980 = require(doritaPath);
    if (!dorita980 || typeof dorita980.Local !== "function") {
      throw new Error("dorita980.Local is unavailable");
    }
    return dorita980;
  } catch (_error) {
    throw new RoombaCommandError(
      "dependency_unavailable",
      "Unable to load the dorita980 runtime",
    );
  }
}

function waitForState(robot) {
  return new Promise((resolve) => {
    const onState = (state) => {
      if (state && typeof state === "object" && Object.keys(state).length > 10) {
        robot.removeListener("state", onState);
        resolve(state);
      }
    };
    robot.on("state", onState);
  });
}

async function executeCommand(robot, command) {
  switch (command) {
    case "status":
    case "mission":
      return robot.getMission();
    case "state":
      // getRobotState() without args returns {} in connect-disconnect mode.
      // Wait for the robot to publish its full state over MQTT instead.
      return waitForState(robot);
    case "start":
      return robot.start();
    case "stop":
      return robot.stop();
    case "pause":
      return robot.pause();
    case "resume":
      return robot.resume();
    case "dock":
      return robot.dock();
    case "find":
      return robot.find();
    case "wifi":
      return robot.getRobotState(["netinfo", "signal", "wifistat", "wlcfg"]);
    default:
      // The command is validated before the MQTT client is constructed.
      throw new RoombaCommandError(
        "unknown_command",
        `Unknown command: ${command}`,
        2,
      );
  }
}

function runConnectedCommand(robot, command) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      callback(value);
    };

    const timeout = setTimeout(() => {
      finish(
        reject,
        new RoombaCommandError(
          "timeout",
          "Robot did not respond within 20s",
        ),
      );
    }, COMMAND_TIMEOUT_MS);

    robot.once("error", (error) => {
      finish(
        reject,
        new RoombaCommandError(
          "connection",
          error && error.message ? error.message : "Robot connection failed",
        ),
      );
    });

    robot.once("connect", () => {
      Promise.resolve().then(() => executeCommand(robot, command)).then(
        (result) => finish(resolve, result),
        (error) => finish(
          reject,
          error instanceof RoombaCommandError
            ? error
            : new RoombaCommandError(
              "command_failed",
              error && error.message ? error.message : "Robot command failed",
            ),
        ),
      );
    });
  });
}

function resultFailure(result) {
  if (!result || typeof result !== "object" || Array.isArray(result)) return null;
  const hasError = Object.prototype.hasOwnProperty.call(result, "error")
    && ![undefined, null, false, 0, ""].includes(result.error);
  if (!hasError && result.ok !== false) return null;

  const code = hasError ? String(result.error) : "action_failed";
  const message = typeof result.message === "string" && result.message
    ? result.message
    : "Robot reported that the command failed";
  return new RoombaCommandError(code, message);
}

function printableResult(result, command) {
  if (result === undefined) return { ok: true, command };
  if (result === null || typeof result !== "object") {
    return { ok: true, command, result };
  }
  return result;
}

async function main() {
  const args = process.argv.slice(2);
  if (args.length !== 2) {
    throw new RoombaCommandError(
      "usage",
      "Usage: roomba-cmd.js <env-file> <command>",
      2,
    );
  }

  const [envFile, command] = args;
  if (!VALID_COMMANDS.has(command)) {
    throw new RoombaCommandError(
      "unknown_command",
      `Unknown command: ${command}`,
      2,
    );
  }

  const env = parseEnvFile(envFile);
  const dorita980 = loadDorita();
  let robot;
  try {
    robot = new dorita980.Local(env.BLID, env.PASSWORD, env.ROBOT_IP, 2);
  } catch (_error) {
    throw new RoombaCommandError(
      "client_initialization_failed",
      "Unable to initialize the robot client",
    );
  }

  try {
    const result = await runConnectedCommand(robot, command);
    const failure = resultFailure(result);
    if (failure) throw failure;

    let serialized;
    try {
      serialized = JSON.stringify(printableResult(result, command));
    } catch (_error) {
      throw new RoombaCommandError(
        "invalid_response",
        "Robot returned a response that could not be encoded as JSON",
      );
    }
    process.stdout.write(`${serialized}\n`);
  } finally {
    if (robot && typeof robot.end === "function") {
      try {
        robot.end();
      } catch (_error) {
        // The command result is authoritative; cleanup cannot make it successful.
      }
    }
  }
}

main().catch((error) => {
  const knownError = error instanceof RoombaCommandError
    ? error
    : new RoombaCommandError("command_failed", "Robot command failed");
  process.stderr.write(`${JSON.stringify({
    error: knownError.code,
    message: knownError.message,
  })}\n`);
  process.exitCode = knownError.exitCode;
});
