/* tool-events.js — GA4 step tracking for the free tools (handoff
   SEARCH_JOURNEY_GA4_TRACKING_HANDOFF.md, 24 Sep 2026).

   The tools NEVER load Google Analytics. This script posts a step message to
   the parent page; embed.js, running on thetrademarkhelpline.com, forwards it
   to that page's own gtag / dataLayer. So GA credits the event to the page the
   visitor is actually on, inherits the site's cookie-consent state, and no
   cross-domain cookie is involved. Standalone (not framed) it does nothing.

   ONE shared file, like help-line.js: the step names, their numbers and the
   no-personal-data rule live here and nowhere else.

     tmhTool.step('results_shown', {result_band:'similar', classes:[9,35]})
     tmhTool.context()   -> {ga_client_id, landing_page, tool_page, tool, placement}

   Rules enforced here, not left to each page:
   - Standard steps only (below). A tool that skips a step never sends it.
   - Each step fires ONCE per page load; `lead_submitted` in particular can
     never fire twice (double click, retry).
   - Only whitelisted parameters leave the iframe: tool, step, step_no,
     placement, result_band (clear|similar|conflict|n/a), classes (numbers
     only), partner_ref. Nothing a visitor typed is ever sent.
*/
(function(){
  if(window.tmhTool) return;
  var STEPS={tool_shown:1,tool_started:2,search_submitted:3,results_shown:4,
             selection_made:5,details_started:6,lead_submitted:7};
  var BANDS={clear:1,similar:1,conflict:1,'n/a':1};
  var qs0; try{ qs0=new URLSearchParams(location.search); }catch(e){ qs0={get:function(){return null;}}; }
  /* The context embed.js passes in (placement, ref, gcid, lp, tp) arrives on the
     FIRST iframe URL only. A tool that moves on inside the frame (the finder's
     hand-off to the builder) keeps it here for the rest of the tab. */
  var CTX_KEYS=['placement','ref','gcid','lp','tp'], saved={};
  try{ saved=JSON.parse(sessionStorage.getItem('tmh_tool_ctx')||'{}')||{}; }catch(e){ saved={}; }
  CTX_KEYS.forEach(function(k){ var v=qs0.get(k); if(v) saved[k]=v; });
  try{ sessionStorage.setItem('tmh_tool_ctx',JSON.stringify(saved)); }catch(e){}
  var qs={get:function(k){ return qs0.get(k)||(CTX_KEYS.indexOf(k)>=0?saved[k]:null)||null; }};
  var framed=false; try{ framed=window.parent&&window.parent!==window; }catch(e){ framed=true; }

  /* Which tool this page is. One file serves several routes (free-search.html
     is Free Search, Quick Search and the Class Builder), so the PATH decides. */
  function toolName(){
    var p=location.pathname;
    if(/^\/class-finder/.test(p)) return 'class_finder';
    if(/^\/class-builder/.test(p)) return 'class_builder';
    if(/^\/uk-trademark-quick-search/.test(p)) return 'quick_search';
    if(/^\/(search-box|search-bar)/.test(p)) return 'search_box';
    if(/^\/(audit|brand-audit)(\/|$)/.test(p)) return 'brand_audit';
    if(/^\/class-assistant/.test(p)) return 'class_assistant';
    if(/^\/(search-report|report)/.test(p)) return 'search_report';
    return 'free_search';
  }
  var TOOL=toolName();
  var PLACEMENT=(qs.get('placement')||'inline').replace(/[^a-z_]/gi,'').slice(0,20)||'inline';
  var REF=(function(v){ return v&&/^[A-Za-z0-9_-]{2,40}$/.test(v)?v:''; })(qs.get('ref'));
  var sent={};

  function cleanClasses(c){
    if(c==null) return undefined;
    var a=Array.isArray(c)?c:String(c).split(/[^0-9]+/);
    var out=[];
    a.forEach(function(x){ var n=parseInt(x,10); if(n>=1&&n<=45&&out.indexOf(n)<0) out.push(n); });
    return out.length?out.sort(function(x,y){return x-y;}).join(','):undefined;
  }

  function step(name, extra){
    try{
      if(!STEPS[name]||sent[name]) return;
      sent[name]=true;
      if(!framed) return;
      extra=extra||{};
      var ev={name:name==='lead_submitted'?'tool_lead':'tool_step',
              tool:TOOL, step:name, step_no:STEPS[name], placement:PLACEMENT};
      if(extra.result_band&&BANDS[extra.result_band]) ev.result_band=extra.result_band;
      var cl=cleanClasses(extra.classes); if(cl) ev.classes=cl;
      if(REF) ev.partner_ref=REF;
      window.parent.postMessage({brauditEvent:ev},'*');
    }catch(e){ /* tracking must never break a tool */ }
  }

  /* The visit context embed.js passed in, for the Zoho lead (handoff §6).
     Never sent to GA. */
  function context(){
    return {ga_client_id:(qs.get('gcid')||'').slice(0,60)||null,
            landing_page:(qs.get('lp')||'').slice(0,300)||null,
            tool_page:(qs.get('tp')||'').slice(0,300)||null,
            tool:TOOL, placement:PLACEMENT};
  }

  window.tmhTool={step:step, context:context, tool:TOOL};

  /* 1 tool_shown: the tool has rendered. Pages that must fetch before they can
     draw (the class finder) call tmhTool.step('tool_shown') themselves when
     ready; everything else is drawn from markup, so load is the moment. */
  function shown(){ if(!window.TMH_TOOL_MANUAL_SHOWN) step('tool_shown'); }
  if(document.readyState==='complete') setTimeout(shown,0);
  else window.addEventListener('load',shown);

  /* 2 tool_started: the first real interaction. */
  function started(ev){
    if(ev&&ev.isTrusted===false) return;
    step('tool_started');
    document.removeEventListener('input',started,true);
    document.removeEventListener('click',started,true);
  }
  document.addEventListener('input',started,true);
  document.addEventListener('click',started,true);

  /* 6 details_started: first focus on a contact field. Pages mark other
     contact inputs with data-tmh-details if name/email/tel is not enough. */
  document.addEventListener('focusin',function(ev){
    var t=ev.target; if(!t||!t.tagName) return;
    if(t.matches&&t.matches('input[type=email],input[type=tel],input[autocomplete^="given"],input[autocomplete^="family"],[data-tmh-details]'))
      step('details_started');
  },true);
})();
