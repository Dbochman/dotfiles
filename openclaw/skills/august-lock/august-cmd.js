#!/usr/bin/env node
/**
 * august-cmd.js — CLI wrapper for August smart lock API
 *
 * Usage:
 *   august-cmd.js status          # Lock state, door, battery
 *   august-cmd.js lock            # Lock the door
 *   august-cmd.js unlock          # Unlock the door
 *   august-cmd.js authorize       # Start 2FA (sends code to email/phone)
 *   august-cmd.js validate <code> # Complete 2FA with 6-digit code
 *   august-cmd.js locks           # List all locks on account
 *
 * Environment variables:
 *   AUGUST_INSTALL_ID   — persistent install ID (saved after first auth)
 *   AUGUST_ID           — email or phone (e.g., dylanbochman@gmail.com)
 *   AUGUST_PASSWORD     — account password
 *
 * Config file: ~/.openclaw/august/config.json
 *   { "installId": "...", "augustId": "...", "password": "..." }
 */

const fs = require('fs')
const path = require('path')

const CONFIG_FILE = path.join(
  process.env.HOME || '/tmp',
  '.openclaw/august/config.json'
)

function loadConfig() {
  const config = {}

  // Load from config file first
  try {
    if (fs.existsSync(CONFIG_FILE)) {
      const data = JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'))
      Object.assign(config, data)
    }
  } catch (e) {
    // ignore
  }

  // Env vars override
  if (process.env.AUGUST_INSTALL_ID) config.installId = process.env.AUGUST_INSTALL_ID
  if (process.env.AUGUST_ID) config.augustId = process.env.AUGUST_ID
  if (process.env.AUGUST_PASSWORD) config.password = process.env.AUGUST_PASSWORD

  // Generate installId if missing
  if (!config.installId) {
    const crypto = require('crypto')
    config.installId = crypto.randomUUID()
    saveConfig(config)
  }

  return config
}

function saveConfig(config) {
  try {
    const dir = path.dirname(CONFIG_FILE)
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true })
    fs.writeFileSync(CONFIG_FILE, JSON.stringify(config, null, 2), { mode: 0o600 })
  } catch (e) {
    console.error(JSON.stringify({ error: 'Failed to save config', message: e.message }))
  }
}

async function main() {
  const August = require('august-api')
  const cmd = process.argv[2]

  if (!cmd) {
    console.error('Usage: august-cmd.js <status|lock|unlock|authorize|validate|locks>')
    process.exit(1)
  }

  const config = loadConfig()

  if (!config.augustId || !config.password) {
    console.error(JSON.stringify({
      error: 'Missing credentials',
      message: 'Set AUGUST_ID and AUGUST_PASSWORD env vars or add to ~/.openclaw/august/config.json'
    }))
    process.exit(1)
  }

  const august = new August(config)

  try {
    switch (cmd) {
      case 'authorize': {
        await august.authorize()
        console.log(JSON.stringify({ ok: true, message: 'Verification code sent to ' + config.augustId }))
        break
      }

      case 'validate': {
        const code = process.argv[3]
        if (!code) {
          console.error(JSON.stringify({ error: 'Missing code', message: 'Usage: august-cmd.js validate <6-digit-code>' }))
          process.exit(1)
        }
        await august.validate(code)
        // Save installId so we don't need 2FA again
        saveConfig(config)
        console.log(JSON.stringify({ ok: true, message: 'Validated successfully. installId saved.' }))
        break
      }

      case 'locks': {
        const locks = await august.locks()
        console.log(JSON.stringify(locks, null, 2))
        break
      }

      case 'status': {
        const lockId = process.argv[3] || undefined
        const status = await august.status(lockId)
        console.log(JSON.stringify(status))
        break
      }

      case 'lock': {
        const lockId = process.argv[3] || undefined
        const result = await august.lock(lockId)
        console.log(JSON.stringify(result))
        break
      }

      case 'unlock': {
        const lockId = process.argv[3] || undefined
        const result = await august.unlock(lockId)
        console.log(JSON.stringify(result))
        break
      }

      case 'details': {
        const lockId = process.argv[3] || undefined
        const details = await august.details(lockId)
        console.log(JSON.stringify(details, null, 2))
        break
      }

      default:
        console.error(JSON.stringify({ error: 'Unknown command: ' + cmd }))
        process.exit(1)
    }
  } catch (e) {
    console.error(JSON.stringify({
      error: e.message || String(e),
      code: e.statusCode || e.code,
    }))
    process.exit(1)
  }
}

main()
