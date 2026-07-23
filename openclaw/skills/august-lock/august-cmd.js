#!/usr/bin/env node
/**
 * Safe CLI wrapper for the August lock API.
 *
 * Normal invocation comes from the `august` SSH wrapper as a base64-encoded
 * JSON argv array. Direct local invocation remains supported for diagnostics.
 */

'use strict'

const crypto = require('crypto')
const fs = require('fs')
const path = require('path')

const CONFIG_FILE = path.join(
  process.env.HOME || '/tmp',
  '.openclaw/august/config.json'
)
const CONFIG_KEYS = new Set([
  'installId',
  'augustId',
  'password',
  'observeLockId',
  'observeAlias',
])
const COMMANDS = new Set([
  'authorize',
  'validate',
  'locks',
  'status',
  'observe',
  'lock',
  'unlock',
  'details',
])
const LOCK_ID_PATTERN = /^[a-fA-F0-9]{32}$/
const SAFE_ALIAS_PATTERN = /^[a-z][a-z0-9_]{0,63}$/
const VERIFY_ATTEMPTS = 5
const DEFAULT_VERIFY_DELAY_MS = 1000

class CliError extends Error {
  constructor(code, message) {
    super(message)
    this.code = code
  }
}

function fail(code, message) {
  throw new CliError(code, message)
}

function decodeInvocation(argv) {
  if (argv[0] !== '--argv-base64') return argv
  if (argv.length !== 2) fail('invalid_arguments', 'Encoded invocation requires exactly one payload')

  const payload = argv[1]
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(payload) || payload.length % 4 !== 0) {
    fail('invalid_arguments', 'Encoded invocation payload is invalid')
  }

  let decoded
  try {
    decoded = JSON.parse(Buffer.from(payload, 'base64').toString('utf8'))
  } catch {
    fail('invalid_arguments', 'Encoded invocation payload is invalid')
  }
  if (!Array.isArray(decoded) || decoded.length === 0 || decoded.length > 3 ||
      decoded.some(value => typeof value !== 'string')) {
    fail('invalid_arguments', 'Encoded invocation payload must be a short string array')
  }
  return decoded
}

function validateLockId(lockId) {
  if (lockId !== undefined && !LOCK_ID_PATTERN.test(lockId)) {
    fail('invalid_lock_id', 'Lock ID must be exactly 32 hexadecimal characters')
  }
}

function validateInvocation(argv) {
  const [command, ...args] = argv
  if (!COMMANDS.has(command)) fail('unsupported_command', 'Unsupported August command')

  let lockId
  switch (command) {
    case 'authorize':
    case 'locks':
    case 'observe':
      if (args.length !== 0) fail('invalid_arguments', `${command} does not accept arguments`)
      break
    case 'validate':
      if (args.length !== 1 || !/^\d{6}$/.test(args[0])) {
        fail('invalid_auth_code', 'Validation code must be exactly 6 digits')
      }
      break
    case 'status':
    case 'lock':
    case 'details':
      if (args.length > 1) fail('invalid_arguments', `${command} accepts at most one lock ID`)
      lockId = args[0]
      validateLockId(lockId)
      break
    case 'unlock':
      if (args[0] !== '--confirm' || args.length > 2) {
        fail('confirmation_required', 'Unlock requires the explicit --confirm flag')
      }
      lockId = args[1]
      validateLockId(lockId)
      break
  }
  return { command, args, lockId }
}

function validateConfigObject(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    fail('invalid_config', 'August config must be a JSON object')
  }
  for (const key of Object.keys(value)) {
    if (!CONFIG_KEYS.has(key)) fail('invalid_config', 'August config contains unsupported fields')
    const field = value[key]
    if (typeof field !== 'string' || field.length === 0 || field.length > 1024 || field.includes('\0')) {
      fail('invalid_config', `August config field ${key} is invalid`)
    }
  }
  return { ...value }
}

function requireConfigDirectory(create = false) {
  const directory = path.dirname(CONFIG_FILE)
  let metadata
  try {
    metadata = fs.lstatSync(directory)
  } catch (error) {
    if (!error || error.code !== 'ENOENT' || !create) {
      if (error && error.code === 'ENOENT') return false
      fail('insecure_config', 'August config directory is unavailable or unsafe')
    }
    try {
      fs.mkdirSync(directory, { recursive: true, mode: 0o700 })
      metadata = fs.lstatSync(directory)
    } catch {
      fail('config_save_failed', 'Could not securely create the August config directory')
    }
  }
  if (!metadata.isDirectory() || metadata.isSymbolicLink() ||
      (typeof process.getuid === 'function' && metadata.uid !== process.getuid()) ||
      (metadata.mode & 0o777) !== 0o700) {
    fail('insecure_config', 'August config directory must be owner-only mode 0700')
  }
  return true
}

function readStoredConfig() {
  requireConfigDirectory(false)
  let stat
  try {
    stat = fs.lstatSync(CONFIG_FILE)
  } catch (error) {
    if (error && error.code === 'ENOENT') return {}
    fail('invalid_config', 'August config is unreadable; repair it manually')
  }
  try {
    if (!stat.isFile() || stat.isSymbolicLink() ||
        (typeof process.getuid === 'function' && stat.uid !== process.getuid()) ||
        (stat.mode & 0o777) !== 0o600) {
      fail('insecure_config', 'August config must be a regular file with mode 0600')
    }
    return validateConfigObject(JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8')))
  } catch (error) {
    if (error instanceof CliError) throw error
    fail('invalid_config', 'August config is unreadable or malformed; repair it manually')
  }
}

function saveConfig(config) {
  const validated = validateConfigObject(config)
  const directory = path.dirname(CONFIG_FILE)
  const temporary = path.join(
    directory,
    `.config.json.${process.pid}.${crypto.randomBytes(8).toString('hex')}.tmp`
  )
  let descriptor
  try {
    requireConfigDirectory(true)
    descriptor = fs.openSync(temporary, 'wx', 0o600)
    fs.fchmodSync(descriptor, 0o600)
    fs.writeFileSync(descriptor, `${JSON.stringify(validated, null, 2)}\n`, 'utf8')
    fs.fsyncSync(descriptor)
    fs.closeSync(descriptor)
    descriptor = undefined
    fs.renameSync(temporary, CONFIG_FILE)
  } catch {
    if (descriptor !== undefined) {
      try { fs.closeSync(descriptor) } catch {}
    }
    try { fs.unlinkSync(temporary) } catch {}
    fail('config_save_failed', 'Could not securely save August config')
  }
}

function effectiveConfig(stored) {
  const config = { ...stored }
  if (process.env.AUGUST_INSTALL_ID) config.installId = process.env.AUGUST_INSTALL_ID
  if (process.env.AUGUST_ID) config.augustId = process.env.AUGUST_ID
  if (process.env.AUGUST_PASSWORD) config.password = process.env.AUGUST_PASSWORD
  validateConfigObject(config)
  return config
}

function loadConfig() {
  const stored = readStoredConfig()
  const config = effectiveConfig(stored)
  if (!config.installId) {
    config.installId = crypto.randomUUID()
    saveConfig({ ...stored, installId: config.installId })
  }
  return { config, stored }
}

function observeBinding(config) {
  if (!LOCK_ID_PATTERN.test(config.observeLockId || '')) {
    fail('observe_binding_missing', 'Protected August observe lock binding is unavailable')
  }
  if (!SAFE_ALIAS_PATTERN.test(config.observeAlias || '') || config.observeAlias !== 'front_door') {
    fail('observe_binding_missing', 'Protected August observe alias is unavailable')
  }
  return { lockId: config.observeLockId, alias: config.observeAlias }
}

function verificationDelay() {
  const raw = process.env.AUGUST_VERIFY_DELAY_MS
  if (raw === undefined) return DEFAULT_VERIFY_DELAY_MS
  if (!/^(0|[1-9]\d{0,3})$/.test(raw) || Number(raw) > 5000) {
    fail('invalid_verify_delay', 'AUGUST_VERIFY_DELAY_MS must be 0-5000')
  }
  return Number(raw)
}

function sleep(milliseconds) {
  return new Promise(resolve => setTimeout(resolve, milliseconds))
}

function resolveExclusiveState(canonical, canonicalValues, nested, first, second) {
  let candidates = new Set([first, second])

  if (canonical !== undefined && canonical !== null && canonical !== '') {
    const resolved = canonicalValues[canonical]
    if (!resolved) return null
    candidates = new Set([resolved])
  }

  for (const key of [first, second]) {
    if (!Object.prototype.hasOwnProperty.call(nested, key)) continue
    if (typeof nested[key] !== 'boolean') return null
    if (nested[key]) {
      candidates = new Set([...candidates].filter(value => value === key))
    } else {
      candidates.delete(key)
    }
  }

  return candidates.size === 1 ? [...candidates][0] : null
}

function observedPhysicalState(status) {
  if (!status || typeof status !== 'object' || Array.isArray(status)) return null
  if (status.state !== undefined &&
      (!status.state || typeof status.state !== 'object' || Array.isArray(status.state))) {
    return null
  }
  const state = status.state || {}
  const lockState = resolveExclusiveState(
    status.status,
    {
      kAugLockState_Locked: 'locked',
      kAugLockState_Unlocked: 'unlocked',
    },
    state,
    'locked',
    'unlocked'
  )
  const doorState = resolveExclusiveState(
    status.doorState,
    {
      kAugDoorState_Closed: 'closed',
      kAugDoorState_Open: 'open',
    },
    state,
    'closed',
    'open'
  )
  return { lockState, doorState }
}

function statusSummary(status) {
  const physicalState = observedPhysicalState(status)
  if (!physicalState) return null
  const { lockState, doorState } = physicalState
  if (!lockState || !doorState) return null
  return {
    lockID: typeof status.lockID === 'string' ? status.lockID : undefined,
    status: typeof status.status === 'string' ? status.status : undefined,
    doorState: typeof status.doorState === 'string' ? status.doorState : undefined,
    state: {
      locked: lockState === 'locked',
      unlocked: lockState === 'unlocked',
      closed: doorState === 'closed',
      open: doorState === 'open',
    },
  }
}

function batteryPercentage(status) {
  for (const candidate of [
    status && status.batteryPercentage,
    status && status.batteryLevel,
    status && status.battery,
    status && status.state && status.state.batteryPercentage,
  ]) {
    if (typeof candidate === 'number' && Number.isFinite(candidate) &&
        candidate >= 0 && candidate <= 100) {
      return Math.round(candidate)
    }
  }
  return undefined
}

function sanitizedObservation(status, alias) {
  const physicalState = observedPhysicalState(status)
  if (!physicalState) fail('status_invalid', 'Current August state is malformed')
  const output = {
    ok: true,
    alias,
    observed_at: new Date().toISOString(),
    lock_state: physicalState.lockState || 'unknown',
    door_state: physicalState.doorState || 'unknown',
  }
  const battery = batteryPercentage(status)
  if (battery !== undefined) output.battery_percent = battery
  return output
}

function matchesPostcondition(summary, action) {
  if (!summary) return false
  if (action === 'lock') {
    return summary.state.locked && !summary.state.unlocked && summary.state.closed
  }
  return summary.state.unlocked && !summary.state.locked &&
    (summary.state.closed || summary.state.open)
}

async function verifyAction(august, action, lockId, delay) {
  let lastSummary = null
  for (let attempt = 1; attempt <= VERIFY_ATTEMPTS; attempt += 1) {
    try {
      lastSummary = statusSummary(await august.status(lockId))
      if (matchesPostcondition(lastSummary, action)) {
        return { ...lastSummary, attempts: attempt }
      }
    } catch {
      lastSummary = null
    }
    if (attempt < VERIFY_ATTEMPTS && delay > 0) await sleep(delay)
  }
  const error = new CliError(
    'verification_failed',
    action === 'lock'
      ? 'Lock action was not verified as locked with the door closed'
      : 'Unlock action was not verified as unlocked with a known door state'
  )
  error.summary = lastSummary
  throw error
}

async function checkActionPrecondition(august, action, lockId) {
  let summary
  try {
    summary = statusSummary(await august.status(lockId))
  } catch {
    fail('precondition_unavailable', 'Current lock and door state could not be verified')
  }
  if (!summary) {
    fail('precondition_unavailable', 'Current lock and door state could not be verified')
  }

  if (action === 'lock' && !summary.state.closed) {
    const error = new CliError(
      'door_not_closed',
      'Refusing to extend the lock while the door is not verified closed'
    )
    error.summary = summary
    throw error
  }

  const alreadySatisfied = action === 'lock'
    ? summary.state.locked && !summary.state.unlocked
    : summary.state.unlocked && !summary.state.locked
  return { summary, alreadySatisfied }
}

async function main() {
  const invocation = validateInvocation(decodeInvocation(process.argv.slice(2)))
  const { config, stored } = loadConfig()
  if (!config.augustId || !config.password) {
    fail('missing_credentials', 'Set AUGUST_ID and AUGUST_PASSWORD or add them to the protected config')
  }

  const August = require('august-api')
  const august = new August({
    installId: config.installId,
    augustId: config.augustId,
    password: config.password,
  })
  const { command, args, lockId } = invocation

  switch (command) {
    case 'authorize':
      await august.authorize()
      console.log(JSON.stringify({ ok: true, message: 'Verification code sent' }))
      return
    case 'validate':
      await august.validate(args[0])
      saveConfig({ ...stored, installId: config.installId })
      console.log(JSON.stringify({ ok: true, message: 'Validation succeeded' }))
      return
    case 'locks':
      console.log(JSON.stringify(await august.locks(), null, 2))
      return
    case 'status':
      console.log(JSON.stringify(await august.status(lockId)))
      return
    case 'observe': {
      const binding = observeBinding(config)
      const status = await august.status(binding.lockId)
      console.log(JSON.stringify(sanitizedObservation(status, binding.alias)))
      return
    }
    case 'details':
      console.log(JSON.stringify(await august.details(lockId), null, 2))
      return
    case 'lock':
    case 'unlock': {
      const delay = verificationDelay()
      const precondition = await checkActionPrecondition(august, command, lockId)
      if (precondition.alreadySatisfied) {
        console.log(JSON.stringify({
          ok: true,
          action: command,
          verified: true,
          alreadySatisfied: true,
          attempts: 0,
          ...precondition.summary,
        }))
        return
      }
      await august[command](lockId)
      const verified = await verifyAction(august, command, lockId, delay)
      console.log(JSON.stringify({ ok: true, action: command, verified: true, ...verified }))
      return
    }
  }
}

main().catch(error => {
  const output = {
    ok: false,
    error_code: error instanceof CliError ? error.code : 'august_api_error',
    message: error instanceof CliError ? error.message : 'August API request failed',
  }
  if (error instanceof CliError && error.summary) output.observed = error.summary
  console.error(JSON.stringify(output))
  process.exitCode = 1
})
