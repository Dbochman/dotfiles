#!/usr/bin/env node
'use strict';

// Incident-specific, fail-closed Chromium check. No app credentials, page text,
// shared tabs, external npm modules, or certificate-verification bypasses.
const canaries = ['emudhra', 'aaa-emudhra'].flatMap(chain =>
  ['dv', 'ov', 'ev'].flatMap(kind => ['g3b', 'g3c'].map(variant =>
    `${kind}-${variant}-${chain}.we-have-a-problem.com`)));
const controls = ['github.com', 'pypi.org'];
const trustErrors = new Set(['net::ERR_CERT_REVOKED', 'net::ERR_CERT_AUTHORITY_INVALID']);

function classify(host, navigation, response) {
  const control = controls.includes(host);
  if (navigation.errorText) {
    return !control && trustErrors.has(navigation.errorText) ? 'rejected' : 'inconclusive';
  }
  // A timeout, redirect, error page, or absent TLS metadata never passes.
  if (!response || response.url !== `https://${host}/` || !response.securityDetails ||
      response.securityState !== 'secure' || !Number.isInteger(response.status)) return 'inconclusive';
  if (control) return 'control_passed';
  const [kind, variant] = host.split('-');
  const expectedIssuer = `EM ${kind.toUpperCase()} TLS CA - ${variant.toUpperCase()}`;
  return response.securityDetails.issuer === expectedIssuer ? 'accepted' : 'canary_changed';
}

class CDP {
  constructor(socket) {
    this.socket = socket;
    this.next = 0;
    this.pending = new Map();
    this.listeners = new Set();
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      const pending = this.pending.get(message.id);
      if (pending) {
        clearTimeout(pending.timer);
        this.pending.delete(message.id);
        message.error ? pending.reject(new Error('CDP command failed')) : pending.resolve(message.result);
      } else {
        for (const listener of this.listeners) listener(message);
      }
    });
    socket.addEventListener('close', () => {
      for (const pending of this.pending.values()) {
        clearTimeout(pending.timer);
        pending.reject(new Error('CDP disconnected'));
      }
      this.pending.clear();
    });
  }
  send(method, params = {}, sessionId) {
    return new Promise((resolve, reject) => {
      const id = ++this.next;
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error('CDP timeout'));
      }, 10000);
      this.pending.set(id, {resolve, reject, timer});
      this.socket.send(JSON.stringify({id, method, params, ...(sessionId ? {sessionId} : {})}));
    });
  }
}

async function check(cdp) {
  const {browserContextId} = await cdp.send('Target.createBrowserContext', {disposeOnDetach: true});
  try {
    const results = [];
    // Four isolated tabs keep the check within acquisition deadlines without
    // creating fourteen concurrent renderers. Each worker navigates serially.
    await Promise.all([0, 1, 2, 3].map(async worker => {
      const {targetId} = await cdp.send('Target.createTarget', {url: 'about:blank', browserContextId});
      const {sessionId} = await cdp.send('Target.attachToTarget', {targetId, flatten: true});
      await cdp.send('Page.enable', {}, sessionId);
      await cdp.send('Network.enable', {}, sessionId);
      await cdp.send('Network.setCacheDisabled', {cacheDisabled: true}, sessionId);
      await cdp.send('Emulation.setScriptExecutionDisabled', {value: true}, sessionId);
      for (const host of [...controls, ...canaries].filter((_, index) => index % 4 === worker)) {
        let response;
        const listener = event => {
          if (event.sessionId === sessionId && event.method === 'Network.responseReceived' &&
              event.params.type === 'Document' && event.params.response.url === `https://${host}/`) {
            response = event.params.response;
          }
        };
        cdp.listeners.add(listener);
        try {
          const navigation = await cdp.send('Page.navigate', {url: `https://${host}/`}, sessionId);
          results.push({host, result: classify(host, navigation, response)});
          if (process.env.TRUST_AUDIT_PROGRESS === '1') console.error(`${host}: ${results.at(-1).result}`);
        } finally {
          cdp.listeners.delete(listener);
        }
      }
    }));
    return results;
  } finally {
    // Only this diagnostic's ephemeral context is removed.
    await cdp.send('Target.disposeBrowserContext', {browserContextId});
  }
}

async function main(port) {
  if (!/^\d{1,5}$/.test(port || '') || Number(port) < 1 || Number(port) > 65535) {
    throw new Error('Expected an explicit loopback CDP port');
  }
  const metadata = await fetch(`http://127.0.0.1:${port}/json/version`, {
    signal: AbortSignal.timeout(5000), redirect: 'error'
  }).then(response => {
    if (!response.ok) throw new Error('CDP discovery failed');
    return response.json();
  });
  const endpoint = new URL(metadata.webSocketDebuggerUrl);
  if (endpoint.protocol !== 'ws:' || !['127.0.0.1', 'localhost'].includes(endpoint.hostname) ||
      endpoint.port !== String(Number(port)) || endpoint.username || endpoint.password) {
    throw new Error('CDP discovery returned an unexpected endpoint');
  }
  const socket = new WebSocket(endpoint);
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {socket.close(); reject(new Error('CDP connect timeout'));}, 5000);
    socket.addEventListener('open', () => {clearTimeout(timeout); resolve();}, {once: true});
    socket.addEventListener('error', () => {clearTimeout(timeout); reject(new Error('CDP connect failed'));}, {once: true});
  });
  try {
    const cdp = new CDP(socket);
    for (let attempt = 1; attempt <= 3; attempt++) {
      const results = await check(cdp);
      const counts = results.reduce((all, row) => {all[row.result] = (all[row.result] || 0) + 1; return all;}, {});
      console.log(JSON.stringify({browser: metadata.Browser, attempt, counts, results}));
      if (counts.rejected === 12 && counts.control_passed === 2) return 0;
      console.error(`Browser trust not ready (attempt ${attempt}/3): ${JSON.stringify(counts)}`);
      if (attempt < 3) await new Promise(resolve => setTimeout(resolve, 2000));
    }
    return 1;
  } finally { socket.close(); }
}

module.exports = {canaries, controls, classify, check};
if (require.main === module) {
  // Absolute ceiling includes cleanup; disposeOnDetach handles unexpected exit.
  const deadline = setTimeout(() => {console.error('Browser trust check deadline exceeded'); process.exit(2);}, 20000);
  main(process.argv[2]).then(code => {clearTimeout(deadline); process.exitCode = code;})
    .catch(() => {clearTimeout(deadline); console.error('Browser trust check inconclusive; refusing clearance'); process.exitCode = 2;});
}
