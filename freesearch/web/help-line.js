/* TMH help line — ONE implementation, mounted by every customer-facing tool.
 *
 * Jonathan, 19 Sep 2026: "All customer facing tools should have the option
 * 'Need some help? Call us on 01618335400, Make an enquiry or Book a free 15
 * minutes consultation'."
 *
 * It is a shared script rather than a snippet pasted into each page for the
 * reason this codebase keeps relearning: four copies of a thing is how two of
 * them end up saying something different. Change the number, the booking URL
 * or the wording HERE and every tool moves together.
 *
 * Usage — put the mount point where you want it and load the script:
 *
 *   <div data-tmh-help></div>
 *   <script src="/help-line.js" defer></script>
 *
 * Attributes on the mount point, all optional:
 *   data-tmh-help="card"     card (default) | strip | inline
 *   data-help-title="..."    override "Need some help?"
 *   data-help-note="..."     one quiet line under the buttons
 *   data-temmy="phone"       phone | question | none  (default: card->phone,
 *                            strip->question, inline->none)
 *   data-help-align="left"   left (default) | centre
 *
 * There is no build step and no dependency. It renders on DOMContentLoaded and
 * again on demand via window.TMHHelp.mount(el), so a tool that builds its
 * screens dynamically can call it after rendering.
 */
(function () {
  'use strict';

  /* The three actions. THE ONLY PLACE THESE LIVE. -------------------------
     The booking URL is the live one (bookings.thetrademarkhelpline.com). An
     older note named a link.cerebrumai.io widget; it appears in no live tool
     and must not come back. Jonathan's personal booking link is never
     published. */
  var PHONE_DISPLAY = '0161 833 5400';
  var PHONE_TEL     = '+441618335400';
  var ENQUIRY_URL   = 'https://www.thetrademarkhelpline.com/make-an-enquiry/';
  var BOOKING_URL   = 'https://bookings.thetrademarkhelpline.com/#/webinitialcall';

  /* Assets are absolute to the host serving this script, so the strip works
     the same inside an embed.js iframe on a partner site as it does here. */
  var HOST = (function () {
    var s = document.currentScript;
    if (!s) {
      var all = document.getElementsByTagName('script');
      for (var i = all.length - 1; i >= 0; i--) {
        if ((all[i].src || '').indexOf('help-line.js') !== -1) { s = all[i]; break; }
      }
    }
    try { return new URL(s.src, location.href).origin; }
    catch (e) { return location.origin; }
  })();

  var TEMMY = {
    phone:    HOST + '/brand/temmy/clean/phone.png',
    question: HOST + '/brand/temmy/clean/question.png'
  };

  /* Inside an iframe every one of these must break OUT of the frame, or the
     booking page tries to load in a 600px-tall widget. This is the whole
     reason the links are built here rather than written by hand per page. */
  var FRAMED = (function () { try { return window.self !== window.top; }
                              catch (e) { return true; } })();
  var TARGET = FRAMED ? ' target="_blank" rel="noopener"' : '';

  /* STAFF SURFACES GET NO HELP LINE. The class tools are opened from inside
     the two staff forms (`/class-assistant?return=staff`, and free-search's
     builder with the same `return`), and telling a sales person on a call to
     "call us on 0161 833 5400" is absurd. The signal is the opener's own
     `return=staff`, which already exists for posting the scope back — so this
     costs nothing and covers any future staff embed that uses it.
     A page can also opt out explicitly with data-tmh-help-off on <body>. */
  var STAFF = (function () {
    try {
      if (new URLSearchParams(location.search).get('return') === 'staff') return true;
      return document.body ? document.body.hasAttribute('data-tmh-help-off') : false;
    } catch (e) { return false; }
  })();

  var PINK = '#E51652', NAVY = '#2D455A', INK = '#1D1D1B', SLATE = '#3f4c58';
  var esc = function (s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  };

  function btn(href, label, primary, extra) {
    var base = 'display:inline-block;border-radius:10px;padding:12px 18px;' +
      "font-size:15px;font-weight:700;text-decoration:none;text-align:center;" +
      'font-family:inherit;line-height:1.2;transition:background .15s ease,color .15s ease;';
    var look = primary
      ? 'background:' + PINK + ';color:#fff;border:1.5px solid ' + PINK + ';'
      : 'background:#fff;color:' + NAVY + ';border:1.5px solid ' + NAVY + ';';
    return '<a href="' + esc(href) + '"' + TARGET + ' data-tmh-help-action="' +
      esc(label) + '" style="' + base + look + (extra || '') + '">' +
      esc(label) + '</a>';
  }

  /* The phone number is a tel: link on every variant. On a phone that is the
     difference between the help line working and being a picture of a number. */
  function telLink(size) {
    return '<a href="tel:' + PHONE_TEL + '" data-tmh-help-action="Call" ' +
      'style="color:' + PINK + ';font-weight:800;text-decoration:none;' +
      'font-size:' + (size || 'inherit') + '">' + PHONE_DISPLAY + '</a>';
  }

  function render(el) {
    var variant = el.getAttribute('data-tmh-help') || 'card';
    if (variant === '' || variant === 'true') variant = 'card';
    var title = el.getAttribute('data-help-title') || 'Need some help?';
    var note  = el.getAttribute('data-help-note') || '';
    var centre = (el.getAttribute('data-help-align') || 'left') === 'centre';

    var owl = el.getAttribute('data-temmy');
    if (!owl) owl = variant === 'card' ? 'phone'
                  : variant === 'strip' ? 'question' : 'none';
    var owlSrc = TEMMY[owl] || '';

    var actions =
      btn(BOOKING_URL, 'Book a free 15 minute consultation', true) +
      btn(ENQUIRY_URL, 'Make an enquiry', false);

    var html;

    if (variant === 'inline') {
      /* One quiet sentence. For the foot of a results screen or a drawer,
         where a card would shout over the thing the user came for. */
      html =
        '<p style="margin:0;font-size:14.5px;line-height:1.65;color:' + SLATE + ';' +
        (centre ? 'text-align:center;' : '') + '">' +
        esc(title) + ' Call us on ' + telLink() +
        ', <a href="' + ENQUIRY_URL + '"' + TARGET + ' data-tmh-help-action="Make an enquiry" ' +
        'style="color:' + PINK + ';font-weight:700">make an enquiry</a> or ' +
        '<a href="' + BOOKING_URL + '"' + TARGET + ' data-tmh-help-action="Book" ' +
        'style="color:' + PINK + ';font-weight:700">book a free 15 minute consultation</a>.' +
        (note ? ' <span style="color:#617383">' + esc(note) + '</span>' : '') +
        '</p>';

    } else if (variant === 'strip') {
      /* A full-width band. Sits between sections without needing a container. */
      html =
        '<div style="display:flex;align-items:center;gap:18px;flex-wrap:wrap;' +
        'background:#FDE7EE;border-radius:14px;padding:16px 20px">' +
        /* Temmy sits on a white plate. He is drawn white-and-pink, so on the
           pink-tint band he loses his outline entirely — checked in the
           browser, not assumed. */
        (owlSrc ? '<div style="flex:0 0 auto;width:68px;height:68px;border-radius:50%;' +
          'background:#fff;display:flex;align-items:center;justify-content:center;' +
          'box-shadow:0 4px 12px rgba(45,69,90,.10)">' +
          '<img src="' + owlSrc + '" alt="" style="width:50px;height:auto;display:block">' +
          '</div>' : '') +
        '<div style="flex:1;min-width:210px">' +
          '<div style="font-size:16px;font-weight:800;letter-spacing:-.2px;color:' + INK + '">' +
            esc(title) + '</div>' +
          '<div style="font-size:14.5px;line-height:1.55;color:' + SLATE + ';margin-top:2px">' +
            'Call us on ' + telLink() + (note ? ' &middot; ' + esc(note) : '') + '</div>' +
        '</div>' +
        '<div style="display:flex;gap:9px;flex-wrap:wrap;flex:0 0 auto">' + actions + '</div>' +
        '</div>';

    } else {
      /* Card: the classes page's own pattern, so the tools match the page. */
      html =
        '<div style="border:1px solid #E6E9ED;border-radius:16px;background:#fff;' +
        'padding:22px 24px;box-shadow:0 6px 22px rgba(45,69,90,.07);' +
        'display:flex;gap:20px;align-items:flex-start;flex-wrap:wrap">' +
        /* The card is white already, so here the plate is the pink tint —
           same trick, inverted, so Temmy has an edge either way. */
        (owlSrc ? '<div style="flex:0 0 auto;width:92px;height:92px;border-radius:50%;' +
          'background:#FDE7EE;display:flex;align-items:center;justify-content:center">' +
          '<img src="' + owlSrc + '" alt="" style="width:68px;height:auto;display:block">' +
          '</div>' : '') +
        '<div style="flex:1;min-width:230px' + (centre ? ';text-align:center' : '') + '">' +
          '<div style="font-size:17px;font-weight:800;letter-spacing:-.3px;color:' + INK + ';' +
            'margin-bottom:6px">' + esc(title) + '</div>' +
          '<p style="margin:0 0 14px;font-size:15px;line-height:1.6;color:' + SLATE + '">' +
            'Call us on ' + telLink('15.5px') + ', or pick whichever of these suits you.' +
            (note ? ' ' + esc(note) : '') + '</p>' +
          '<div style="display:flex;gap:10px;flex-wrap:wrap' +
            (centre ? ';justify-content:center' : '') + '">' + actions + '</div>' +
        '</div>' +
        '</div>';
    }

    el.innerHTML = html;
    el.setAttribute('data-tmh-help-ready', '1');

    /* Hover states, to match the homepage behaviour the brand file asks for. */
    var links = el.querySelectorAll('a[data-tmh-help-action]');
    for (var i = 0; i < links.length; i++) (function (a) {
      var s = a.getAttribute('style') || '';
      if (s.indexOf(PINK + ';color:#fff') !== -1) {
        a.onmouseover = function () { a.style.background = '#c9134a'; };
        a.onmouseout  = function () { a.style.background = PINK; };
      } else if (s.indexOf('border:1.5px solid ' + NAVY) !== -1) {
        a.onmouseover = function () { a.style.background = NAVY; a.style.color = '#fff'; };
        a.onmouseout  = function () { a.style.background = '#fff'; a.style.color = NAVY; };
      }
    })(links[i]);
  }

  function mountAll(root) {
    var els = (root || document).querySelectorAll('[data-tmh-help]');
    for (var i = 0; i < els.length; i++) {
      if (els[i].getAttribute('data-tmh-help-ready') === '1') continue;
      if (STAFF) {                    // staff surface: remove it, don't hide it
        els[i].innerHTML = '';
        els[i].setAttribute('data-tmh-help-ready', 'staff');
        continue;
      }
      render(els[i]);
    }
  }

  window.TMHHelp = {
    mount: function (el) { el ? render(el) : mountAll(); },
    remount: mountAll,
    PHONE: PHONE_DISPLAY, ENQUIRY_URL: ENQUIRY_URL, BOOKING_URL: BOOKING_URL
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', function () { mountAll(); });
  } else {
    mountAll();
  }

  /* Pages that build screens after load (free-search moves between screens,
     the assistant swaps route panels) get their mount points picked up without
     having to remember to call us. Cheap: it only touches unrendered nodes. */
  if (window.MutationObserver) {
    new MutationObserver(function () { mountAll(); })
      .observe(document.documentElement, { childList: true, subtree: true });
  }
})();
