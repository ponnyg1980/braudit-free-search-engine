/* The ONE AI class assistant, as a chat. Shared by every tool that offers AI
   class help (Jonathan, 2 Oct 2026: "simplify with one AI option").

     TMHClassChat.mount(el, {
       api: '',                 // engine base; '' = same origin
       staff: false,            // staff see the terms in the summary; clients never do
       context: {},             // what the page already knows (17 Oct 2026): word, tagline, has_logo,
                                // logoFile (a File) or logo (data URL), business, website, description
       onDone: function(result){}   // {classes:[{n,label}], terms:{n:[term]}, description, brief, flags, state}
     });

   The server (POST /class-chat, freesearch/class_chat.py) runs the
   conversation and is stateless: the state it returns is sent straight back
   with the next message. Terms come back inside that state so the order can
   carry them as Terms_Status = Draft; a CLIENT is shown classes only.
*/
(function(){
  'use strict';
  var CSS = ''
    + '.tcc{border:1px solid #E6E9ED;border-radius:14px;background:#fff;overflow:hidden;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:#1D1D1B}'
    + '.tcc-log{max-height:420px;overflow-y:auto;padding:16px 16px 6px;background:#F7F8FA}'
    + '.tcc-m{max-width:86%;margin:0 0 10px;padding:10px 13px;border-radius:12px;white-space:pre-wrap}'
    + '.tcc-a{background:#fff;border:1px solid #E6E9ED;border-top-left-radius:4px}'
    + '.tcc-u{background:#2D455A;color:#fff;margin-left:auto;border-top-right-radius:4px}'
    + '.tcc-q{display:flex;flex-wrap:wrap;gap:8px;padding:0 16px 10px;background:#F7F8FA}'
    + '.tcc-q button{border:1.5px solid #E51652;background:#fff;color:#E51652;border-radius:99px;padding:6px 14px;font-weight:700;cursor:pointer;font-size:14px}'
    + '.tcc-q button:hover{background:#FDE7EE}'
    + '.tcc-in{display:flex;gap:8px;padding:12px;border-top:1px solid #E6E9ED}'
    + '.tcc-in input{flex:1;min-width:0;border:1.5px solid #E6E9ED;border-radius:10px;padding:10px 12px;font-size:15px}'
    + '.tcc-in button{background:#E51652;color:#fff;border:0;border-radius:10px;padding:0 18px;font-weight:700;cursor:pointer;transition:background .15s ease}'
    + '.tcc-in button:hover{background:#c9134a}.tcc-in button:disabled{opacity:.5;cursor:default}'
    + '.tcc-typing{color:#617383;font-style:italic;margin:0 0 10px}'
    + '.tcc-res{padding:12px 16px;border-top:1px solid #E6E9ED}'
    + '.tcc-res .c{margin:4px 0}.tcc-res .c b{color:#2D455A}'
    + '.tcc-res ul{margin:4px 0 8px 18px;padding:0;color:#3f4c58;font-size:14px}'
    + '.tcc-q.multi{flex-direction:column;align-items:stretch}.tcc-q.multi label{display:flex;gap:8px;align-items:flex-start;background:#fff;border:1px solid #E6E9ED;border-radius:9px;padding:7px 10px;cursor:pointer;font-size:14px}'
    + '.tcc-q.multi .go{align-self:flex-start;background:#E51652;color:#fff}'
    + '.tcc-cert{font-size:11px;font-weight:700;text-transform:uppercase;border-radius:6px;padding:1px 6px;margin-left:6px;background:#EEF2F6;color:#2D455A}'
    + '.tcc-brief{white-space:pre-wrap;font-size:13px;color:#3f4c58;background:#F7F8FA;border-radius:8px;padding:8px 10px;margin:8px 0}'
    + '.tcc-flag{font-size:13px;color:#8f1d1d;margin:4px 0}';

  function el(tag, cls, text){ var e=document.createElement(tag); if(cls)e.className=cls; if(text!=null)e.textContent=text; return e; }

  function mount(root, opts){
    opts = opts || {};
    if(!document.getElementById('tcc-css')){
      var st=document.createElement('style'); st.id='tcc-css'; st.textContent=CSS; document.head.appendChild(st);
    }
    var api = String(opts.api || '').replace(/\/$/, '');
    var state = {}, busy = false, finished = false;
    root.innerHTML = '';
    var box = el('div','tcc'), log = el('div','tcc-log'), quick = el('div','tcc-q'),
        form = el('form','tcc-in'), input = el('input'), send = el('button',null,'Send');
    input.placeholder = 'Type your answer…'; input.setAttribute('aria-label','Your answer');
    send.type = 'submit';
    form.appendChild(input); form.appendChild(send);
    box.appendChild(log); box.appendChild(quick); box.appendChild(form); root.appendChild(box);

    function add(who, text){
      var m = el('div', 'tcc-m ' + (who === 'u' ? 'tcc-u' : 'tcc-a'), text);
      log.appendChild(m); log.scrollTop = log.scrollHeight;
    }
    function setQuick(list, multi){
      quick.innerHTML = ''; quick.className = 'tcc-q' + (multi ? ' multi' : '');
      if(multi && (list || []).length){
        var boxes = [];
        list.forEach(function(q){
          var lab = el('label'), cb = el('input'); cb.type = 'checkbox'; cb.value = q.value;
          lab.appendChild(cb); lab.appendChild(document.createTextNode(q.label)); quick.appendChild(lab);
          boxes.push([cb, q]);
        });
        var go = el('button', 'go', 'Continue'); go.type = 'button';
        go.onclick = function(){
          var on = boxes.filter(function(b){ return b[0].checked; });
          var typed = input.value.trim(); input.value = '';
          var vals = on.map(function(b){ return b[1].value; }); if(typed) vals.push(typed);
          if(!vals.length) return;
          var shown = on.map(function(b){ return b[1].label; }); if(typed) shown.push(typed);
          talk(vals.join('|'), shown.join('; '));
        };
        quick.appendChild(go);
        return;
      }
      (list || []).forEach(function(q){
        var b = el('button', null, q.label); b.type = 'button';
        b.onclick = function(){ talk(q.value || q.label, q.label); };
        quick.appendChild(b);
      });
    }
    function showResult(d){
      finished = true; form.style.display = 'none';
      var terms = {};
      (state.classes || []).forEach(function(c){ terms[c.n] = (c.terms || []).map(function(t){ return t.term; }); });
      var res = el('div','tcc-res');
      var full = {}; (state.classes || []).forEach(function(c){ full[c.n] = c; });
      (d.classes || []).forEach(function(c){
        var row = el('div','c'); var b = el('b',null,'Class ' + c.n); row.appendChild(b);
        row.appendChild(document.createTextNode(' — ' + c.label));
        if(opts.staff && full[c.n] && full[c.n].certainty) row.appendChild(el('span','tcc-cert', full[c.n].certainty));
        res.appendChild(row);
        if(opts.staff && full[c.n] && (full[c.n].terms || []).length){
          ['core','likely','possible'].forEach(function(g){
            var ts = full[c.n].terms.filter(function(t){ return (t.certainty || 'possible') === g; });
            if(!ts.length) return;
            var ul = el('ul'); ul.appendChild(el('li', null, g.charAt(0).toUpperCase() + g.slice(1) + ':'));
            ts.forEach(function(t){ ul.appendChild(el('li', null, t.term)); });
            res.appendChild(ul);
          });
        }
      });
      if(opts.staff){
        (state.flags || []).forEach(function(f){ res.appendChild(el('div','tcc-flag','Staff note: ' + f)); });
        if(state.brief) res.appendChild(el('div','tcc-brief', state.brief));
      }
      box.appendChild(res);
      var desc = state.brief || [state.site_summary, state.about].filter(Boolean).join('\n');
      var mk = state.mark || {};
      if(typeof opts.onDone === 'function'){
        var mub = state.brief ? state.brief + ((state.flags || []).length ? '\n\nStaff notes:\n- ' + state.flags.join('\n- ') : '') : '';
        opts.onDone({classes: d.classes || [], terms: terms, description: desc, brief: state.brief || '', mark_use_brief: mub,
                     flags: state.flags || [], certainty: (state.classes || []).map(function(c){ return {n: c.n, certainty: c.certainty}; }),
                     website: mk.website || state.website || '', provides: state.provides || '', state: state});
      }
    }
    var ctx = null;                       // sent with the FIRST turn only
    function talk(message, shown){
      if(busy || finished) return;
      if(message) add('u', shown || message);
      busy = true; send.disabled = true; setQuick([]);
      var typing = el('div','tcc-typing', message ? 'Thinking…' : '');
      if(message) log.appendChild(typing);
      var slow = setTimeout(function(){ typing.textContent = 'Working out your classes… this can take up to half a minute.'; }, 4000);
      fetch(api + '/class-chat', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(ctx ? {state: state, message: message || '', context: ctx} : {state: state, message: message || ''})})
        .then(function(r){ return r.json(); })
        .then(function(d){
          clearTimeout(slow); typing.remove(); busy = false; send.disabled = false;
          if(!d || !d.ok){ add('a', (d && d.reply) || "Sorry, I can't help with classes right now. Please pick them from the list instead."); return; }
          state = d.state || state; ctx = null;
          add('a', d.reply);
          if(d.done) showResult(d); else { setQuick(d.quick, d.multi); input.focus(); }
        })
        .catch(function(){
          clearTimeout(slow); typing.remove(); busy = false; send.disabled = false;
          add('a', "Sorry, something went wrong. Please try again, or pick your classes from the list.");
        });
    }
    form.addEventListener('submit', function(e){
      e.preventDefault(); var v = input.value.trim(); if(!v) return; input.value = ''; talk(v);
    });
    // The page's context, with the logo shrunk in the browser (max 768px JPEG) so the request stays small.
    function shrink(file, cb){
      try{
        var fr = new FileReader();
        fr.onload = function(){
          var img = new Image();
          img.onload = function(){
            var k = Math.min(1, 768 / Math.max(img.width, img.height)), c = document.createElement('canvas');
            c.width = Math.round(img.width * k); c.height = Math.round(img.height * k);
            var g = c.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, c.width, c.height);
            g.drawImage(img, 0, 0, c.width, c.height); cb(c.toDataURL('image/jpeg', 0.85));
          };
          img.onerror = function(){ cb(null); }; img.src = fr.result;
        };
        fr.onerror = function(){ cb(null); }; fr.readAsDataURL(file);
      }catch(e){ cb(null); }
    }
    var c0 = opts.context || {};
    ctx = {word: c0.word || '', tagline: c0.tagline || '', has_logo: !!(c0.has_logo || c0.logoFile || c0.logo),
           business: c0.business || '', website: c0.website || '', description: c0.description || ''};
    if(c0.logoFile){ shrink(c0.logoFile, function(u){ if(u) ctx.logo = u; talk(''); }); }
    else { if(c0.logo) ctx.logo = c0.logo; talk(''); }   // opening question
    return {reset: function(){ mount(root, opts); }};
  }
  window.TMHClassChat = {mount: mount};
})();
