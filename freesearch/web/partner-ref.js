/* partner-ref.js — the ONE partner-referral reader for every customer-facing
 * tool (search-journey, 3 Oct 2026).
 *
 * Jonathan, 3 Oct: "partners can put the widgets on their own websites too, so
 * every customer facing widget needs to be able to hold an ?ref= at the end."
 *
 * Rules (wordpress/FREE_SEARCH_PARTNER_REF_INSTRUCTIONS.md, INTRODUCER spec §7):
 *   - read ?ref= on load, else the embedding page's URL (document.referrer);
 *   - normalise: trim, lowercase, a-z 0-9 '-' only, 20 chars;
 *   - FIRST TOUCH WINS for the visit, kept in sessionStorage only (carrying a
 *     code across visits is the tmh_ref cookie's job, set after consent);
 *   - never validated in the browser, never a partner list here. Zoho decides.
 *
 * What it does:
 *   window.tmhRef.code            the code for this visit ('' if none)
 *   window.tmhRef.withRef(url)    url with the code added, for our own hosts:
 *                                   thetrademarkhelpline.com, the search engine
 *                                   -> ?ref=<code>
 *                                   bookings.thetrademarkhelpline.com
 *                                   -> #/route?Partner%20Code=<code> (Bookings
 *                                      only reads prefill AFTER the hash, keyed
 *                                      by the field LABEL — tested 3 Oct)
 *   window.tmhRef.log(base, {session_id, request_id})
 *                                 records partner_ref_captured on the journey so
 *                                 the Lead push carries Partner_Ref_Code_Used.
 *                                 Await it BEFORE logging the lead event.
 *   Every click on a link to one of our hosts gets the code added, so a
 *   hand-off between tools (finder -> builder, report -> booking) keeps it.
 *
 * free-search.html and audit-checkout.html read the same sessionStorage key
 * (tmh_partner_ref), so the code is one value across every tool in a visit.
 */
(function () {
  var KEY = 'tmh_partner_ref';
  var OWN = /(^|\.)thetrademarkhelpline\.com$|^braudit-free-search\.onrender\.com$/i;
  var BOOK = /^bookings\.thetrademarkhelpline\.com$/i;

  function norm(v) {
    return String(v || '').trim().toLowerCase().replace(/[^a-z0-9-]/g, '').slice(0, 20);
  }
  var raw = '';
  try { raw = new URLSearchParams(location.search).get('ref') || ''; } catch (e) {}
  if (!raw) {
    try { raw = new URLSearchParams((document.referrer.split('?')[1] || '').split('#')[0]).get('ref') || ''; } catch (e) {}
  }
  var code = '';
  try { code = sessionStorage.getItem(KEY) || ''; } catch (e) {}
  if (!code) {
    code = norm(raw);
    if (code) { try { sessionStorage.setItem(KEY, code); } catch (e) {} }
  }

  function withRef(href) {
    if (!code || !href) return href;
    try {
      var u = new URL(href, location.href);
      if (!/^https?:$/.test(u.protocol)) return href;
      if (BOOK.test(u.hostname)) {
        if (/Partner(%20| )Code=/i.test(u.hash)) return href;
        var h = u.hash || '#/';
        u.hash = h + (h.indexOf('?') > -1 ? '&' : '?') + 'Partner%20Code=' + encodeURIComponent(code);
        return u.toString();
      }
      if (!OWN.test(u.hostname) && u.origin !== location.origin) return href;
      if (u.searchParams.get('ref')) return href;
      u.searchParams.set('ref', code);
      return u.toString();
    } catch (e) { return href; }
  }

  document.addEventListener('click', function (ev) {
    if (!code) return;
    var a = ev.target && ev.target.closest ? ev.target.closest('a[href]') : null;
    if (!a) return;
    var href = a.getAttribute('href') || '';
    if (!href || href.charAt(0) === '#' || /^(mailto|tel|javascript):/i.test(href)) return;
    var nh = withRef(a.href);
    if (nh !== a.href) a.setAttribute('href', nh);
  }, true);

  var logged = {};
  function log(base, ids) {
    ids = ids || {};
    var sid = ids.session_id || '', rid = ids.request_id || '';
    if (!code || !base || (!sid && !rid)) return Promise.resolve(false);
    var k = sid + '|' + rid;
    if (logged[k]) return Promise.resolve(true);
    logged[k] = 1;
    var path = sid ? '/session/event' : '/audit/event';
    var body = { event_type: 'partner_ref_captured', screen: null, payload: { code: code } };
    if (sid) body.session_id = sid;
    if (rid) body.request_id = rid;
    try {
      return fetch(String(base).replace(/\/$/, '') + path, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body), keepalive: true
      }).then(function () { return true; }, function () { return false; });
    } catch (e) { return Promise.resolve(false); }
  }

  window.tmhRef = { code: code, withRef: withRef, log: log };
})();
