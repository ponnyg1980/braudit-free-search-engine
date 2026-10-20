/* consult-booking.js - the Clearance Audit consultation, picked and booked in our own page.
 *
 * Jonathan, 15 Oct 2026: the Zoho Bookings page "was the weakest link" - it opened in a new
 * tab, let people book without choosing a time and had no way back. This replaces it:
 *
 *   TMHConsult.picker(el, {type, from, journey, onPick})
 *       Shows the real free times (journey /consult/slots -> Zoho Bookings availability, so
 *       anything busy in the advisers' diaries is already blocked out), Monday-Friday,
 *       the whole call inside 9am-5pm, never sooner than 24 hours. Nothing is booked here.
 *   TMHConsult.book(el, {sid, journey})
 *       After the order exists: books the time the client chose (journey /consult/book) and
 *       shows the confirmation. If that time has gone meanwhile, shows the picker again.
 *
 * One file, used by the audit form and the thank-you page, so the two cannot drift.
 */
(function () {
  var DEFAULT_JOURNEY = 'https://jwanlhdmhgmbybcdhvkx.supabase.co/functions/v1/journey';
  var CSS = '.tc-days{display:flex;gap:8px;overflow-x:auto;padding:2px 0 10px}' +
    '.tc-day{flex:0 0 auto;border:1.5px solid #E6E9ED;border-radius:10px;padding:8px 12px;background:#fff;' +
    'cursor:pointer;text-align:center;font:600 13px/1.25 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;color:#2D455A}' +
    '.tc-day b{display:block;font-size:15px}.tc-day.on{border-color:#E51652;background:#FDE7EE}' +
    '.tc-times{display:grid;grid-template-columns:repeat(auto-fill,minmax(84px,1fr));gap:8px}' +
    '.tc-time{border:1.5px solid #E6E9ED;border-radius:8px;padding:9px 0;background:#fff;cursor:pointer;' +
    'font:700 14px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;color:#2D455A}' +
    '.tc-time:hover{border-color:#2D455A}.tc-time.on{border-color:#E51652;background:#E51652;color:#fff}' +
    '.tc-note{font-size:13px;color:#617383;margin:8px 0 0}.tc-chosen{margin-top:12px;font-size:14.5px;color:#1D1D1B}' +
    '.tc-ok{border-left:4px solid #1F8A5B;background:#E7F6EE;border-radius:0 10px 10px 0;padding:12px 16px;font-size:14.5px}' +
    '.tc-warn{border-left:4px solid #E51652;background:#FDE7EE;border-radius:0 10px 10px 0;padding:12px 16px;font-size:14.5px;margin-bottom:12px}' +
    '.tc-load{font-size:14px;color:#617383;padding:8px 0}';
  function css() {
    if (document.getElementById('tc-css')) return;
    var s = document.createElement('style'); s.id = 'tc-css'; s.textContent = CSS;
    document.head.appendChild(s);
  }
  function esc(x) { return String(x == null ? '' : x).replace(/[&<>"]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function day(iso) {
    var p = String(iso).split('-'); return new Date(+p[0], p[1] - 1, +p[2]);
  }
  function longDate(iso) {
    return day(iso).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long' });
  }
  function time12(t) {
    var h = +t.slice(0, 2), m = t.slice(3, 5);
    return (h % 12 || 12) + (m === '00' ? '' : ':' + m) + (h < 12 ? 'am' : 'pm');
  }
  function post(base, path, body) {
    return fetch((base || DEFAULT_JOURNEY).replace(/\/$/, '') + path, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body)
    }).then(function (r) { return r.json(); });
  }

  function picker(el, o) {
    css();
    o = o || {};
    el.innerHTML = '<div class="tc-load">Checking our advisers&rsquo; diaries&hellip;</div>';
    return post(o.journey, '/consult/slots', { type: o.type || 'Phone', from: o.from || '', days: 6 })
      .then(function (d) {
        if (!d || !d.ok) {
          el.innerHTML = '';
          if (o.onUnavailable) o.onUnavailable(d);
          return d;
        }
        var days = d.days || [];
        if (!days.length) {
          el.innerHTML = '<div class="tc-warn">We have no free times showing in the next few weeks. ' +
            'Please call us on <a href="tel:+441618335400">0161 833 5400</a> and we&rsquo;ll fit you in.</div>';
          return d;
        }
        var cur = 0, picked = o.selected || null;
        function draw() {
          var h = '<div class="tc-days">' + days.map(function (x, i) {
            var dt = day(x.date);
            return '<button type="button" class="tc-day' + (i === cur ? ' on' : '') + '" data-i="' + i + '">' +
              dt.toLocaleDateString('en-GB', { weekday: 'short' }) + '<b>' + dt.getDate() + '</b>' +
              dt.toLocaleDateString('en-GB', { month: 'short' }) + '</button>';
          }).join('') + '</div>';
          h += '<div class="tc-times">' + days[cur].slots.map(function (t) {
            var on = picked && picked.date === days[cur].date && picked.time === t;
            return '<button type="button" class="tc-time' + (on ? ' on' : '') + '" data-t="' + t + '">' + time12(t) + '</button>';
          }).join('') + '</div>';
          h += '<p class="tc-note">Times are UK time. Each consultation lasts up to ' + (d.minutes || 30) +
            ' minutes and can run on if you need it.</p>';
          if (picked) h += '<div class="tc-chosen">You&rsquo;ve chosen <b>' + esc(longDate(picked.date)) +
            ' at ' + esc(time12(picked.time)) + '</b>. We book it as soon as your order is placed.</div>';
          el.innerHTML = h;
          [].forEach.call(el.querySelectorAll('.tc-day'), function (b) {
            b.onclick = function () { cur = +b.getAttribute('data-i'); draw(); };
          });
          [].forEach.call(el.querySelectorAll('.tc-time'), function (b) {
            b.onclick = function () {
              picked = { date: days[cur].date, time: b.getAttribute('data-t'), type: o.type || 'Phone' };
              draw();
              if (o.onPick) o.onPick(picked);
            };
          });
        }
        if (picked) {
          var at = days.map(function (x) { return x.date; }).indexOf(picked.date);
          if (at >= 0) cur = at; else picked = null;
        }
        draw();
        return d;
      })
      .catch(function () { el.innerHTML = ''; if (o.onUnavailable) o.onUnavailable(null); });
  }

  function confirmHtml(r) {
    return '<div class="tc-ok"><b>Your free ' + esc(String(r.type || '').toLowerCase() === 'video' ? 'video (Teams)' : 'phone') +
      ' consultation is booked for ' + esc(longDate(r.date)) + ' at ' + esc(time12(r.time)) + '.</b><br>' +
      'A confirmation is on its way by email.' +
      (r.summary_url ? ' Need to change it? <a href="' + esc(r.summary_url) + '" target="_blank" rel="noopener">Reschedule or cancel here</a>.' : '') +
      '</div>';
  }

  function book(el, o) {
    css();
    o = o || {};
    if (!o.sid) return;
    el.innerHTML = '<div class="tc-load">Booking your consultation&hellip;</div>';
    function attempt(ov, tries) {
      return post(o.journey, '/consult/book', Object.assign({ session_id: o.sid }, ov || {})).then(function (r) {
        if (r && r.ok && !r.demo && r.date) { el.innerHTML = confirmHtml(r); if (o.onBooked) o.onBooked(r); return; }
        if (r && r.demo) { el.innerHTML = '<div class="tc-ok">Sandbox: no appointment was booked.</div>'; return; }
        if (r && r.skipped) { el.innerHTML = ''; return; }
        if (r && /no order yet|in progress/.test(String(r.error || '')) && tries < 4) {
          setTimeout(function () { attempt(ov, tries + 1); }, 4000); return;
        }
        if (r && r.need_pick) {
          var box = document.createElement('div');
          el.innerHTML = '<div class="tc-warn">' + (r.error === 'taken'
            ? 'Sorry, that time has just been taken. Please choose another.'
            : 'Please choose a time for your consultation.') + '</div>';
          el.appendChild(box);
          picker(box, { journey: o.journey, type: r.type || o.type || 'Phone', from: o.from || '',
            onPick: function (p) { el.innerHTML = '<div class="tc-load">Booking your consultation&hellip;</div>';
              attempt({ type: p.type, date: p.date, time: p.time }, 0); },
            onUnavailable: function () { el.innerHTML = fallback(); } });
          return;
        }
        el.innerHTML = fallback();
      }).catch(function () { el.innerHTML = fallback(); });
    }
    function fallback() {
      return '<div class="tc-warn">We couldn&rsquo;t confirm your consultation time automatically. ' +
        'Our team will be in touch to book it, or call us on <a href="tel:+441618335400">0161 833 5400</a>.</div>';
    }
    attempt(null, 0);
  }

  window.TMHConsult = { picker: picker, book: book, longDate: longDate, time12: time12 };
})();
