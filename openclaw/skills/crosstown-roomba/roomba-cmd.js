#!/usr/bin/env node
// roomba-cmd.js — authenticated localhost rest980 client for CLI use
// Usage: node roomba-cmd.js <env-file> <command>
// Commands: status, state, start, stop, pause, resume, dock, find, wifi, mission

"use strict";

const fs = require("fs");
const http = require("http");
const net = require("net");
const { TextDecoder } = require("util");

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
const MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const INFO_PATHS = new Map([
  ["status", "/api/local/info/mission"],
  ["mission", "/api/local/info/mission"],
  ["state", "/api/local/info/state"],
  ["wifi", "/api/local/info/state"],
]);

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

  for (const key of [
    "BLID",
    "PASSWORD",
    "ROBOT_IP",
    "PORT",
    "BASIC_AUTH_USER",
    "BASIC_AUTH_PASS",
    "FIRMWARE_VERSION",
  ]) {
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
  if (!/^[1-9][0-9]{0,4}$/.test(env.PORT)) {
    throw new RoombaCommandError(
      "env_invalid_port",
      "PORT must be a valid TCP port",
      2,
    );
  }
  const port = Number(env.PORT);
  if (!Number.isSafeInteger(port) || port > 65535) {
    throw new RoombaCommandError(
      "env_invalid_port",
      "PORT must be a valid TCP port",
      2,
    );
  }
  for (const key of ["BASIC_AUTH_USER", "BASIC_AUTH_PASS"]) {
    if (env[key].length > 1024 || /[\r\n]/.test(env[key])) {
      throw new RoombaCommandError(
        "env_invalid_auth",
        "REST authentication settings are invalid",
        2,
      );
    }
  }
  if (env.FIRMWARE_VERSION !== "2") {
    throw new RoombaCommandError(
      "env_invalid_firmware",
      "FIRMWARE_VERSION must be 2 for the rest980 MQTT transport",
      2,
    );
  }

  return env;
}

function requestJson(env, requestPath) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      callback(value);
    };
    const authorization = Buffer.from(
      `${env.BASIC_AUTH_USER}:${env.BASIC_AUTH_PASS}`,
      "utf8",
    ).toString("base64");
    const request = http.request({
      hostname: "127.0.0.1",
      port: Number(env.PORT),
      path: requestPath,
      method: "GET",
      agent: false,
      headers: {
        Accept: "application/json",
        Authorization: `Basic ${authorization}`,
      },
    }, (response) => {
      const chunks = [];
      let length = 0;
      response.on("data", (chunk) => {
        length += chunk.length;
        if (length > MAX_RESPONSE_BYTES) {
          request.destroy(new RoombaCommandError(
            "rest_response_too_large",
            "rest980 returned an oversized response",
          ));
          return;
        }
        chunks.push(chunk);
      });
      response.on("end", () => {
        if (settled) return;
        if (response.statusCode === 401 || response.statusCode === 403) {
          finish(reject, new RoombaCommandError(
            "rest_auth_failed",
            "rest980 rejected the protected local credentials",
          ));
          return;
        }
        if (
          typeof response.statusCode !== "number"
          || response.statusCode < 200
          || response.statusCode >= 300
        ) {
          finish(reject, new RoombaCommandError(
            "rest_request_failed",
            "rest980 rejected the bounded request",
          ));
          return;
        }
        let parsed;
        try {
          const text = new TextDecoder("utf-8", { fatal: true }).decode(
            Buffer.concat(chunks),
          );
          parsed = JSON.parse(text);
        } catch (_error) {
          finish(reject, new RoombaCommandError(
            "invalid_response",
            "rest980 returned invalid JSON",
          ));
          return;
        }
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
          finish(reject, new RoombaCommandError(
            "invalid_response",
            "rest980 returned invalid object JSON",
          ));
          return;
        }
        finish(resolve, parsed);
      });
    });
    request.setTimeout(COMMAND_TIMEOUT_MS, () => {
      request.destroy(new RoombaCommandError(
        "rest_timeout",
        "rest980 did not respond within 20s",
      ));
    });
    request.on("error", (error) => {
      finish(
        reject,
        error instanceof RoombaCommandError
          ? error
          : new RoombaCommandError(
            "rest_unavailable",
            "The protected rest980 service is unavailable",
          ),
      );
    });
    request.end();
  });
}

async function validateRestBinding(env) {
  const info = await requestJson(env, "/api/info/protocol");
  if (
    info.firmwareVersion !== 2
    || info.protocol !== "v2"
    || info.transport !== "mqtt-tls"
    || info.keepAlive !== true
    || info.robotIP !== env.ROBOT_IP
  ) {
    throw new RoombaCommandError(
      "rest_binding_mismatch",
      "rest980 is not bound to the expected robot",
    );
  }
}

async function executeCommand(env, command) {
  const infoPath = INFO_PATHS.get(command);
  if (infoPath) {
    const result = await requestJson(env, infoPath);
    if (command !== "wifi") return result;
    return {
      netinfo: result.netinfo || {},
      signal: result.signal || {},
      wifistat: result.wifistat || {},
      wlcfg: result.wlcfg || {},
    };
  }
  return requestJson(env, `/api/local/action/${command}`);
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
  await validateRestBinding(env);
  const result = await executeCommand(env, command);
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
