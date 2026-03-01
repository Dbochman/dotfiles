#!/usr/bin/env bash
# opentable-book.sh — Book a restaurant on OpenTable via pinchtab
#
# Usage: opentable-book.sh <search_term> <date YYYY-MM-DD> <time HH:MM> <party_size>
# Example: opentable-book.sh "italian brookline" 2026-04-11 19:00 2
#
# Outputs JSON: {"success": true, "restaurant": "...", "date": "...", "time": "...", "url": "..."}
# Or on failure: {"success": false, "error": "..."}

set -euo pipefail

SEARCH="${1:-}"
DATE="${2:-}"
TIME="${3:-19:00}"
PARTY="${4:-2}"

if [[ -z "$SEARCH" || -z "$DATE" ]]; then
  echo '{"success":false,"error":"Usage: opentable-book.sh <search> <date> [time] [party_size]"}'
  exit 1
fi

DATETIME="${DATE}T${TIME}:00"
ENCODED_SEARCH=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$SEARCH'))")

# Start pinchtab
pkill -f "pinchtab" 2>/dev/null || true
sleep 1
pinchtab &
PINCH_PID=$!
sleep 5

cleanup() {
  kill $PINCH_PID 2>/dev/null || true
  pkill -f pinchtab 2>/dev/null || true
}
trap cleanup EXIT

pt() { pinchtab eval "$1" 2>/dev/null; }

# Navigate to search results
pinchtab nav "https://www.opentable.com/s?covers=${PARTY}&dateTime=$(python3 -c "import urllib.parse; print(urllib.parse.quote('${DATETIME}'))")&metroId=7&term=${ENCODED_SEARCH}" 2>/dev/null
sleep 8

# Dismiss cookie consent
pt "(function(){
  document.cookie='OptanonAlertBoxClosed='+new Date().toISOString()+'; path=/; domain=.opentable.com; max-age=31536000';
  document.cookie='OptanonConsent=isGpcEnabled=0&interactionCount=1&groups=C0001:1,C0002:1,C0003:1,C0004:1; path=/; domain=.opentable.com; max-age=31536000';
  const s=document.querySelector('#onetrust-consent-sdk'); if(s) s.remove();
})()" > /dev/null

sleep 2

# Find available timeslots and click the closest to requested time.
# Uses dispatchEvent with full mousedown/mouseup/click sequence and coordinates
# to properly trigger React's synthetic event system (simple .click() doesn't work).
CLICK_RESULT=$(pt "(function(){
  const els = Array.from(document.querySelectorAll('a[role=button]'));
  const timeEls = els.filter(e => /\d:\d\d [AP]M/.test(e.textContent.trim()) && e.getBoundingClientRect().width > 0);
  if(!timeEls.length) return '';
  const target = '${TIME}'.split(':');
  const targetMins = parseInt(target[0])*60 + parseInt(target[1]);
  let best = null, bestDiff = 9999;
  timeEls.forEach(el => {
    const m = el.textContent.trim().match(/(\d+):(\d\d) ([AP]M)/);
    if(!m) return;
    let h = parseInt(m[1]); if(m[3]==='PM'&&h!==12) h+=12; if(m[3]==='AM'&&h===12) h=0;
    const mins = h*60+parseInt(m[2]);
    const diff = Math.abs(mins-targetMins);
    if(diff < bestDiff) { bestDiff=diff; best=el; }
  });
  if(!best) return '';
  const rect = best.getBoundingClientRect();
  const x = rect.x + rect.width/2;
  const y = rect.y + rect.height/2;
  const opts = {bubbles:true, cancelable:true, clientX:x, clientY:y, button:0};
  best.dispatchEvent(new MouseEvent('mousedown', opts));
  best.dispatchEvent(new MouseEvent('mouseup', opts));
  best.dispatchEvent(new MouseEvent('click', opts));
  return 'clicked:'+best.textContent.trim();
})()")

if [[ -z "$CLICK_RESULT" || "$CLICK_RESULT" == '{"result":""}' ]]; then
  echo '{"success":false,"error":"No available timeslots found for search: '"$SEARCH"'"}'
  exit 1
fi

sleep 10

# Verify we landed on the booking details page
PAGE=$(pt "JSON.stringify({url:window.location.href,title:document.title,body:document.body.innerText.slice(0,300)})")
if ! echo "$PAGE" | python3 -c "import json,sys; d=json.loads(sys.stdin.read()); r=d.get('result',''); exit(0 if 'almost done' in r.lower() or 'booking/details' in r else 1)" 2>/dev/null; then
  echo '{"success":false,"error":"Did not reach booking page. Page: '"$(echo $PAGE | head -c 100)"'"}'
  exit 1
fi

# Get restaurant name and confirmed time from the page
INFO=$(pt "(function(){
  const body = document.body.innerText;
  const restaurant = document.querySelector('h1, [class*=restaurant-name], [class*=restaurantName]')?.textContent?.trim() || '';
  const timeMatch = body.match(/(\d+:\d\d [AP]M)/);
  const dateMatch = body.match(/(Mon|Tue|Wed|Thu|Fri|Sat|Sun),?\s+\w+\s+\d+/);
  const iframes = document.querySelectorAll('iframe[src*=spreedly]').length;
  const hasCompleteBtn = !!Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('Complete reservation'));
  return JSON.stringify({restaurant,time:timeMatch?.[1],date:dateMatch?.[0],spreedlyIframes:iframes,hasCompleteBtn});
})()")

# Complete the reservation
RESULT=$(pt "(function(){
  const btn = Array.from(document.querySelectorAll('button')).find(b=>b.textContent.includes('Complete reservation'));
  if(!btn) return 'no button';
  if(btn.disabled) return 'button disabled';
  btn.click();
  return 'clicked';
})()")

sleep 10

# Check confirmation
CONFIRM=$(pt "(function(){
  const url = window.location.href;
  const body = document.body.innerText;
  const confirmed = url.includes('/booking/confirmation') || body.includes('Reservation confirmed');
  const restaurant = document.querySelector('h1')?.textContent?.trim() || '';
  const timeMatch = body.match(/(\d+:\d\d [AP]M)/);
  const dateMatch = body.match(/(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[^\n]*/);
  return JSON.stringify({confirmed,url,restaurant,time:timeMatch?.[1],date:dateMatch?.[0]});
})()")

python3 -c "
import json, sys
raw = '''$CONFIRM'''
try:
    d = json.loads(raw.strip())
    r = json.loads(d.get('result','{}'))
    if r.get('confirmed'):
        print(json.dumps({'success':True,'restaurant':r.get('restaurant',''),'date':r.get('date',''),'time':r.get('time',''),'url':r.get('url','')}))
    else:
        print(json.dumps({'success':False,'error':'Booking did not confirm','url':r.get('url','')}))
except Exception as e:
    print(json.dumps({'success':False,'error':str(e),'raw':raw[:200]}))
"
