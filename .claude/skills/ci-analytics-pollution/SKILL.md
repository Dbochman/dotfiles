---
name: ci-analytics-pollution
description: |
  Debug unexplained analytics traffic from CI/CD pipelines hitting production.
  Use when: (1) Analytics show traffic to URLs that match test patterns like
  /this-page-does-not-exist, /test-route, or 404 test paths, (2) Traffic has
  near 1:1 session-to-user ratio suggesting automated hits, (3) Traffic
  correlates with deploy frequency, (4) HeadlessChrome or Playwright appears
  in user agents. Covers Playwright, Cypress, and other e2e test frameworks
  running against production URLs.
author: Claude Code
version: 1.0.0
date: 2026-01-19
---

# CI/CD Analytics Pollution Debugging

## Problem

E2E tests configured to run against production URLs (for post-deploy verification)
generate real analytics hits, polluting your data with synthetic traffic. This is
especially problematic when tests hit non-existent routes for 404 verification.

## Context / Trigger Conditions

Suspect CI pollution when you see:

- Traffic to URLs matching test patterns (`/this-page-does-not-exist`, `/test-*`, etc.)
- Near 1:1 session-to-user ratio (each CI run = one unique "user")
- Traffic volume correlating with deploy/PR frequency
- User agents containing: `HeadlessChrome`, `Playwright`, `Puppeteer`, `Cypress`
- Traffic source showing as "Direct" (CI doesn't send referrers)

## Investigation Steps

1. **Identify suspicious URLs in analytics**
   ```
   Look for: /this-page-does-not-exist, /404-test, /non-existent, etc.
   ```

2. **Search codebase for those URLs**
   ```bash
   grep -r "this-page-does-not-exist" --include="*.ts" --include="*.js"
   ```

3. **Check if tests are in e2e/integration test files**
   ```bash
   # Common locations
   tests/e2e/
   cypress/integration/
   playwright/
   ```

4. **Find workflows that set production BASE_URL**
   ```bash
   grep -r "BASE_URL.*production-domain.com" .github/workflows/
   grep -r "baseURL.*production-domain.com" playwright.config.*
   ```

5. **Correlate traffic timing with CI runs**
   - Check GitHub Actions history
   - Compare deploy timestamps with analytics spikes

## Solutions

### Option A: Exclude problematic tests from production runs (Recommended)

For Playwright:
```yaml
# .github/workflows/post-deploy-check.yml
- name: Run smoke tests on production
  run: npx playwright test --grep-invert="404"  # Skip 404 tests
```

For Cypress:
```yaml
- name: Run smoke tests
  run: npx cypress run --spec "cypress/e2e/smoke/**" --env SKIP_404=true
```

### Option B: Tag CI traffic in analytics

Detect and tag CI sessions via custom dimension:

```typescript
// analytics.ts
const isCI = /HeadlessChrome|Playwright|Puppeteer|Cypress/.test(navigator.userAgent);

gtag('set', { 'traffic_type': isCI ? 'ci' : 'human' });
```

Then filter in your analytics dashboard by `traffic_type != 'ci'`.

### Option C: Block analytics in CI entirely

```typescript
// Only init analytics for real users
if (!navigator.userAgent.includes('HeadlessChrome')) {
  initializeAnalytics();
}
```

## Verification

After implementing:
1. Trigger a deploy and check that suspicious traffic stops
2. Verify CI tests still pass (didn't break anything)
3. Check analytics for reduction in synthetic traffic

## Example: Real Investigation

**Symptom**: 374 sessions to `/this-page-does-not-exist` in GA4

**Investigation**:
```bash
# Found in test file
grep -r "this-page-does-not-exist" tests/
# tests/e2e/console-errors.spec.ts:172: await page.goto(`${BASE_URL}/this-page-does-not-exist`);

# Found workflow running against production
grep -r "BASE_URL" .github/workflows/
# .github/workflows/console-check.yml:55: BASE_URL: https://production-site.com
```

**Root cause**: Post-deploy workflow ran full Playwright suite including 404 test

**Fix**: Added `--grep-invert="404"` to skip 404 tests in production checks

## Notes

- This is especially common with post-deploy verification workflows
- The 404 test is useful locally/in preview, just not against production
- Consider separate test suites: `smoke` (production-safe) vs `full` (all tests)
- Some teams use a `@production-safe` tag to mark tests that can run against live sites

## Related Patterns

- Bot traffic from web crawlers (different user agents, different solution)
- Preview deployment traffic (separate concern, use preview URL detection)
- Load testing traffic (should use dedicated analytics property)
