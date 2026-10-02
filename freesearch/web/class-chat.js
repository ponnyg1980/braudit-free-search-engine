/* The ONE AI class assistant, as a chat. Shared by every tool that offers AI
   class help (Jonathan, 2 Oct 2026: "simplify with one AI option").

     TMHClassChat.mount(el, {
       api: '',                 // engine base; '' = same origin
       staff: false,            // staff see the terms in the summary; clients never do
       onDone: function(result){}   // {classes:[{n,label}], terms:{n:[term]}, description, state}
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
    + '.tcc-res ul{margin:4px 0 8px 18px;padding:0;color:#3f4c58;font-size:14px}';

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
    function setQuick(list){
      quick.innerHTML = '';
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
      (d.classes || []).forEach(function(c){
        var row = el('div','c'); var b = el('b',null,'Class ' + c.n); row.appendChild(b);
        row.appendChild(document.createTextNode(' — ' + c.label)); res.appendChild(row);
        if(opts.staff && (terms[c.n] || []).length){
          var ul = el('ul');
          terms[c.n].forEach(function(t){ ul.appendChild(el('li', null, t)); });
          res.appendChild(ul);
        }
      });
      box.appendChild(res);
      var desc = [state.site_summary, state.about].filter(Boolean).join('\n');
      if(typeof opts.onDone === 'function'){
        opts.onDone({classes: d.classes || [], terms: terms, description: desc,
                     website: state.website || '', provides: state.provides || '', state: state});
      }
    }
    function talk(message, shown){
      if(busy || finished) return;
      if(message) add('u', shown || message);
      busy = true; send.disabled = true; setQuick([]);
      var typing = el('div','tcc-typing', message ? 'Thinking…' : '');
      if(message) log.appendChild(typing);
      fetch(api + '/class-chat', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({state: state, message: message || ''})})
        .then(function(r){ return r.json(); })
        .then(function(d){
          typing.remove(); busy = false; send.disabled = false;
          if(!d || !d.ok){ add('a', (d && d.reply) || "Sorry, I can't help with classes right now. Please pick them from the list instead."); return; }
          state = d.state || state;
          add('a', d.reply);
          if(d.done) showResult(d); else { setQuick(d.quick); input.focus(); }
        })
        .catch(function(){
          typing.remove(); busy = false; send.disabled = false;
          add('a', "Sorry, something went wrong. Please try again, or pick your classes from the list.");
        });
    }
    form.addEventListener('submit', function(e){
      e.preventDefault(); var v = input.value.trim(); if(!v) return; input.value = ''; talk(v);
    });
    talk('');   // opening question
    return {reset: function(){ mount(root, opts); }};
  }
  window.TMHClassChat = {mount: mount};
})();
