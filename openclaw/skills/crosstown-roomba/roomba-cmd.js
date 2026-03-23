#!/usr/bin/env node
// roomba-cmd.js — direct dorita980 wrapper for CLI use
// Usage: node roomba-cmd.js <env-file> <command>
// Commands: status, state, start, stop, pause, resume, dock, find, wifi, mission

const fs = require("fs");
const path = require("path");

const DORITA_PATH = path.join(process.env.HOME, ".openclaw/rest980/node_modules/dorita980");
const dorita980 = require(DORITA_PATH);

const envFile = process.argv[2];
const command = process.argv[3];

if (!envFile || !command) {
  console.error("Usage: roomba-cmd.js <env-file> <command>");
  process.exit(1);
}

// Load env file
const envContent = fs.readFileSync(envFile, "utf8");
const env = {};
envContent.split("\n").forEach(line => {
  const match = line.match(/^([^=]+)=(.*)$/);
  if (match) env[match[1]] = match[2].replace(/^["']|["']$/g, "");
});

const robot = new dorita980.Local(env.BLID, env.PASSWORD, env.ROBOT_IP, 2);
const timeout = setTimeout(() => {
  console.error(JSON.stringify({ error: "timeout", message: "Robot did not respond within 20s" }));
  process.exit(1);
}, 20000);

robot.on("error", (e) => {
  console.error(JSON.stringify({ error: "connection", message: e.message }));
  clearTimeout(timeout);
  process.exit(1);
});

robot.on("connect", async () => {
  try {
    let result;
    switch (command) {
      case "status":
        result = await robot.getMission();
        break;
      case "state":
        result = await robot.getRobotState();
        break;
      case "start":
        result = await robot.start();
        break;
      case "stop":
        result = await robot.stop();
        break;
      case "pause":
        result = await robot.pause();
        break;
      case "resume":
        result = await robot.resume();
        break;
      case "dock":
        result = await robot.dock();
        break;
      case "find":
        result = await robot.find();
        break;
      case "wifi":
        result = await robot.getRobotState(["netinfo", "signal", "wifistat", "wlcfg"]);
        break;
      case "mission":
        result = await robot.getMission();
        break;
      default:
        result = { error: "unknown_command", message: "Unknown command: " + command };
    }
    console.log(JSON.stringify(result));
  } catch (e) {
    console.error(JSON.stringify({ error: "command_failed", message: e.message }));
  }
  clearTimeout(timeout);
  robot.end();
});
