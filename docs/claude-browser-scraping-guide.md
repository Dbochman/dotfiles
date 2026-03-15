# Chrome DevTools MCP for Playwright scraping

A guide for Julia's [financial-dashboard](https://github.com/JJJennings/financial-dashboard) project. The Chrome DevTools MCP server gives Claude Code direct control over a browser — it can navigate pages, take screenshots, inspect the DOM, and run JavaScript. This is useful when Playwright scripts get stuck on login flows or dynamic content, because Claude can actually see what's happening instead of guessing.

## The problem this solves

When Claude writes Playwright scraping scripts without seeing the page, it tends to get stuck on login flows. It guesses at selectors, misses redirects, doesn't know when a CAPTCHA or MFA prompt appears, and can't tell why a page isn't loading. The fix is to let Claude look at the page while it works.

The Chrome DevTools MCP gives Claude a browser it can control directly. Open the page, see what's there, try selectors, handle whatever the login throws at you, then write the Playwright script based on what actually worked.

## Setup

The MCP server is already configured on the Mac Mini (`~/.claude/mcp.json`). Start a Claude Code session:

```bash
cd ~/path/to/financial-dashboard
claude
```

The chrome-devtools tools load automatically. If you're SSHing in from your MacBook Air, this works fine — the browser runs headless and Claude sees everything through screenshots.

## Debugging a login flow

This is the main use case. When a Playwright script fails at login, instead of guessing what went wrong, do this:

### 1. Have Claude open the login page

```
Navigate to https://the-bank-site.com/login and take a screenshot
```

Claude will show you exactly what the page looks like. You'll immediately see if there's a cookie banner blocking the form, a redirect you didn't expect, or a different login page than you assumed.

### 2. Walk through the login step by step

```
Fill the email field with julia@example.com and take a screenshot
```

After each step, Claude screenshots the result. You'll see if:
- The field didn't actually get filled (wrong selector)
- A validation error appeared
- The page redirected somewhere unexpected
- An MFA prompt or CAPTCHA showed up

```
Now fill the password field with [password] and click the Sign In button.
Take a screenshot after the page loads.
```

### 3. See what happens after login

```
What page are we on now? Take a screenshot.
List the network requests that fired during login.
```

Often the issue is a redirect chain after login that the Playwright script doesn't wait for. Claude can see exactly where the browser ended up and what requests fired.

### 4. Generate the fixed Playwright script

```
Now that we've walked through the login, write a Playwright script that:
1. Goes to the login page
2. Handles the login with the selectors that actually worked
3. Waits for the right redirect/page load
4. Scrapes [whatever you need]

Use environment variables for credentials.
```

Claude writes the script based on real selectors it verified, not documentation or guesses.

## Quick reference

| Task | What to tell Claude |
|---|---|
| Open a page | "Navigate to [url] and screenshot" |
| See the DOM | "Take a snapshot" (returns the DOM tree) |
| Find elements | "Find the element with text 'Sign In'" |
| Fill a form field | "Fill the input with name='email' with 'test@test.com'" |
| Click something | "Click the Submit button" |
| Run JS in the page | "Evaluate: document.querySelector('.balance').textContent" |
| Check network calls | "List network requests" |
| Wait for content | "Wait for .dashboard-content to appear" |
| Check for errors | "List console messages" |

## Skipping login entirely with Sweet Cookie

Sometimes the best login flow is no login flow. If you've already signed into a site in Chrome on the Mac Mini, [Sweet Cookie](https://github.com/steipete/sweet-cookie) can extract your existing session cookies and inject them into Playwright. No credentials, no MFA, no CAPTCHA — just reuse the session Chrome already has.

### Install

```bash
npm install @steipete/sweet-cookie
```

### How it works

Sweet Cookie reads Chrome's encrypted cookie database, decrypts the cookies using the macOS Keychain, and hands them back as plain values. You pass in a URL and the cookie names you need:

```javascript
import { getCookies } from '@steipete/sweet-cookie';

// Extract session cookies from Chrome
const cookies = await getCookies({
  url: 'https://your-bank.com',
  names: ['session_id', 'auth_token', '_csrf']
});
```

Then inject them into Playwright's browser context:

```javascript
const context = await browser.newContext();
await context.addCookies(cookies.map(c => ({
  name: c.name,
  value: c.value,
  domain: c.domain,
  path: c.path,
  httpOnly: c.httpOnly,
  secure: c.secure,
})));
// Now navigate directly to the dashboard — you're already logged in
```

### When to use this vs. the login flow approach

Use Sweet Cookie when:
- You're already logged in via Chrome and the session is long-lived
- The login flow has CAPTCHA or MFA that's painful to automate
- You're iterating on the scraping logic and don't want to re-login every run

Use the DevTools MCP login flow when:
- You need to figure out which cookies matter in the first place
- The session expires frequently and you need a repeatable login script
- You're setting this up for the first time on a new site

They work well together: use the DevTools MCP to explore the site and identify the right cookies, then use Sweet Cookie to grab them for subsequent runs.

There's also [SweetCookieKit](https://github.com/steipete/SweetCookieKit) if you ever need the same thing from Swift, but the npm package is what you want for Playwright projects.

## Tips

**Check the network tab first.** Many financial sites load data via JSON APIs. If Claude can find the API endpoint in the network requests, you can skip the DOM scraping entirely and hit the API directly in your Playwright script. Way more reliable.

```
Navigate to the dashboard page, then list all network requests.
Show me any JSON responses.
```

**Save browser state.** Once login works, have Claude write the script to save cookies/session so subsequent runs skip login:

```
Write the script to save the browser storage state after login
and load it on future runs.
```

**The browser is headless.** You won't see a Chrome window on the Mini's screen. That's fine — Claude sees through screenshots and DOM snapshots. If you need a visible browser (for manual CAPTCHA solving), screen share into the Mini and work from Terminal there.

**Login flows that redirect through third parties** (OAuth, SSO) are the ones that trip up Playwright the most. The DevTools MCP handles these well because Claude can follow the redirect chain visually instead of trying to predict it in code.
