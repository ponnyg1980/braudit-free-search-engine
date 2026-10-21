/* booking-capture.js (20 Oct 2026) - reads a Zoho Bookings appointment back into our forms.
 *
 * The audit booking types (Audit-Phone, audit-review) redirect, after booking, to /booked on this
 * site. That page runs INSIDE the booking iframe, so it is same-origin with the form that holds the
 * iframe and can hand the booking details to it with postMessage. One file for the staff forms and
 * the client checkout, so they read the booking the same way.
 *
 *   TMHBooking.listen(function (b) { ... })   b = {date:'yyyy-mm-dd', time:'HH:MM', adviser, type:
 *                                               'Phone'|'Teams', booking_id, raw:{...}}
 */
(function () {
  var MON = { jan: 1, feb: 2, mar: 3, apr: 4, may: 5, jun: 6, jul: 7, aug: 8, sep: 9, oct: 10, nov: 11, dec: 12 };
  function pad(n) { return String(n).padStart(2, '0'); }
  function pick(raw, keys) {
    var low = {};
    Object.keys(raw || {}).forEach(function (k) { low[k.toLowerCase().replace(/[^a-z]/g, '')] = raw[k]; });
    for (var i = 0; i < keys.length; i++) { var v = low[keys[i]]; if (v) return String(v); }
    return '';
  }
  function parseDate(s) {
    s = String(s || '');
    var m;
    if ((m = s.match(/(\d{4})-(\d{1,2})-(\d{1,2})/))) return m[1] + '-' + pad(m[2]) + '-' + pad(m[3]);
    if ((m = s.match(/(\d{1,2})[-\s\/]([A-Za-z]{3})[A-Za-z]*[-\s\/,]+(\d{4})/)) && MON[m[2].toLowerCase()])
      return m[3] + '-' + pad(MON[m[2].toLowerCase()]) + '-' + pad(m[1]);
    if ((m = s.match(/([A-Za-z]{3})[A-Za-z]*\s+(\d{1,2}),?\s+(\d{4})/)) && MON[m[1].toLowerCase()])
      return m[3] + '-' + pad(MON[m[1].toLowerCase()]) + '-' + pad(m[2]);
    if ((m = s.match(/(\d{1,2})\/(\d{1,2})\/(\d{4})/))) return m[3] + '-' + pad(m[2]) + '-' + pad(m[1]);
    return '';
  }
  function parseTime(s) {
    var m = String(s || '').match(/(\d{1,2}):(\d{2})\s*([AaPp][Mm])?/);
    if (!m) return '';
    var h = +m[1];
    if (m[3]) { var pm = /p/i.test(m[3]); if (pm && h < 12) h += 12; if (!pm && h === 12) h = 0; }
    return pad(h) + ':' + m[2];
  }
  function londonParts(iso) {
    /* Zoho passes booking_start_time as ISO 8601; with Z or an offset, show it in UK time. */
    var d = new Date(iso);
    if (isNaN(d)) return null;
    var f = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', year: 'numeric', month: '2-digit',
      day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }).formatToParts(d);
    var g = {}; f.forEach(function (p) { g[p.type] = p.value; });
    return { date: g.year + '-' + g.month + '-' + g.day, time: (g.hour === '24' ? '00' : g.hour) + ':' + g.minute };
  }
  function normalise(raw) {
    var st = pick(raw, ['bookingstarttime']);
    if (st && /T\d{2}:\d{2}/.test(st) && /(Z|[+-]\d{2}:?\d{2})$/.test(st)) {
      var lp = londonParts(st);
      if (lp) {
        var svc0 = pick(raw, ['servicename', 'service']);
        return { date: lp.date, time: lp.time, adviser: pick(raw, ['assignedto', 'staffname', 'staff']),
          type: /teams|video|review/i.test(svc0) ? 'Teams' : (svc0 ? 'Phone' : ''), service: svc0,
          booking_id: pick(raw, ['bookingid']), raw: raw || {} };
      }
    }
    var when = [pick(raw, ['bookingstarttime', 'bookingdate', 'appointmentdate', 'date', 'startdate', 'bookingdatetime', 'starttime', 'appointmenttime', 'bookingtime', 'fromtime', 'time']),
                pick(raw, ['starttime', 'appointmenttime', 'bookingtime', 'fromtime', 'time'])].join(' ');
    var svc = pick(raw, ['servicename', 'service', 'eventtype', 'appointmenttype']);
    return {
      date: parseDate(when), time: parseTime(when),
      adviser: pick(raw, ['staffname', 'staff', 'consultantname', 'consultant', 'resourcename', 'assignedto']),
      type: /teams|video|review/i.test(svc) ? 'Teams' : (svc ? 'Phone' : ''),
      service: svc, booking_id: pick(raw, ['bookingid', 'appointmentid', 'id']),
      raw: raw || {}
    };
  }
  var fns = [];
  window.addEventListener('message', function (e) {
    if (e.origin !== location.origin || !e.data || !e.data.tmhBooking) return;
    var b = normalise(e.data.tmhBooking);
    fns.forEach(function (f) { try { f(b); } catch (x) {} });
  });
  window.TMHBooking = { listen: function (f) { fns.push(f); }, normalise: normalise };
})();
