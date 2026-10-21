const {test} = require('node:test');
const assert = require('node:assert/strict');
const {canaries, controls, classify, check} = require('../bin/pinchtab-browser-trust.cjs');

function secure(host) {
  const [kind, variant] = host.split('-');
  const issuer = canaries.includes(host) ? `EM ${kind.toUpperCase()} TLS CA - ${variant.toUpperCase()}` : 'public CA';
  return {url: `https://${host}/`, status: 200, securityState: 'secure', securityDetails: {issuer}};
}
test('only explicit revocation/authority failures count as canary rejection', () => {
  for (const errorText of ['net::ERR_CERT_REVOKED', 'net::ERR_CERT_AUTHORITY_INVALID']) {
    assert.equal(classify(canaries[0], {errorText}), 'rejected');
    assert.equal(classify(controls[0], {errorText}), 'inconclusive');
  }
  for (const errorText of ['net::ERR_NAME_NOT_RESOLVED', 'net::ERR_TIMED_OUT', 'net::ERR_CERT_DATE_INVALID', 'net::ERR_CERT_COMMON_NAME_INVALID', 'Privacy error']) {
    assert.equal(classify(canaries[0], {errorText}), 'inconclusive');
  }
});
test('accepted canaries, absent TLS evidence, redirects, and error pages cannot pass', () => {
  assert.equal(classify(canaries[0], {}, secure(canaries[0])), 'accepted');
  assert.equal(classify(canaries[0], {}, {...secure(canaries[0]), securityDetails: {issuer: 'intercepting CA'}}), 'canary_changed');
  assert.equal(classify(controls[0], {}, secure(controls[0])), 'control_passed');
  for (const response of [undefined, {}, {...secure(controls[0]), securityDetails: undefined}, {...secure(controls[0]), securityState: 'insecure'}, {...secure(controls[0]), url: 'chrome-error://chromewebdata/'}]) {
    assert.equal(classify(controls[0], {}, response), 'inconclusive');
  }
});

function fakeCDP(fail = false) {
  return {
    listeners: new Set(), calls: [], next: 0,
    async send(method, params, sessionId) {
      this.calls.push({method, params, sessionId});
      if (method === 'Target.createBrowserContext') return {browserContextId: 'audit-only'};
      if (method === 'Target.createTarget') return {targetId: `target-${++this.next}`};
      if (method === 'Target.attachToTarget') return {sessionId: params.targetId};
      if (method === 'Network.enable' && fail) throw new Error('test transport failure');
      if (method === 'Page.navigate') {
        const host = new URL(params.url).hostname;
        if (canaries.includes(host)) return {errorText: 'net::ERR_CERT_REVOKED'};
        for (const listener of this.listeners) listener({sessionId, method: 'Network.responseReceived', params: {type: 'Document', response: secure(host)}});
      }
      return {};
    }
  };
}
test('all fourteen probes use disposable contexts without shared tabs or trust bypasses', async () => {
  const cdp = fakeCDP();
  const results = await check(cdp);
  assert.equal(results.filter(x => x.result === 'rejected').length, 12);
  assert.equal(results.filter(x => x.result === 'control_passed').length, 2);
  assert.equal(cdp.listeners.size, 0);
  assert.equal(cdp.calls.filter(x => x.method === 'Target.createTarget').length, 4);
  assert.deepEqual(cdp.calls.filter(x => x.method === 'Target.disposeBrowserContext').map(x => x.params), [{browserContextId: 'audit-only'}]);
  assert.ok(cdp.calls.every(x => !/ignore|cookie|Browser.close|Target.getTargets/i.test(x.method)));
  assert.ok(cdp.calls.filter(x => x.method === 'Target.createTarget').every(x => x.params.browserContextId === 'audit-only'));
});
test('transport failure still disposes only the audit context', async () => {
  const cdp = fakeCDP(true);
  await assert.rejects(check(cdp));
  assert.ok(cdp.calls.some(x => x.method === 'Target.disposeBrowserContext' && x.params.browserContextId === 'audit-only'));
});
