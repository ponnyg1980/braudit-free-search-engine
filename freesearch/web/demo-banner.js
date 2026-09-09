/* DEMO ONLY chrome (Jonathan, 9 Sep). One file, included by every widget
   page; a NO-OP unless the page is in demo mode. Demo mode is entered by
   ?demo=1 or ?tenant=demo on the URL, or by the page calling
   window.markDemo() after discovering its journey session carries the demo
   tenant. The banner is deliberately loud — these pages must NEVER be
   mistaken for live. The real isolation is SERVER-side (the demo tenant is
   refused by every Zoho/Xero/Stripe path); this file is only the signage. */
(function(){
  var q=new URLSearchParams(location.search);
  function isDemo(){return q.get('demo')==='1'||q.get('tenant')==='demo';}
  var done=false;
  function mark(){
    if(done)return; done=true;
    try{
      document.title='[DEMO ONLY] '+document.title.replace(/^\[DEMO ONLY\] /,'');
      document.documentElement.setAttribute('data-demo','1');
      var b=document.createElement('div');
      b.id='demo-banner';
      b.setAttribute('style',
        'position:fixed;top:0;left:0;right:0;z-index:2147483000;'
        +'background:repeating-linear-gradient(45deg,#B3261E,#B3261E 28px,#8C1D17 28px,#8C1D17 56px);'
        +'color:#fff;font:800 13px/1.3 "Public Sans",system-ui,sans-serif;'
        +'letter-spacing:.08em;text-transform:uppercase;text-align:center;'
        +'padding:9px 14px;box-shadow:0 2px 10px rgba(0,0,0,.35)');
      b.textContent='🧪 DEMO ONLY — sandbox environment. No CRM records, no invoices, no payments, no emails. Not for client use.';
      document.body.appendChild(b);
      document.body.style.paddingTop=(b.offsetHeight||40)+'px';
      /* keep demo on same-host navigations so the signage never drops */
      document.addEventListener('click',function(e){
        var a=e.target && e.target.closest && e.target.closest('a[href]');
        if(!a)return;
        try{
          var u=new URL(a.getAttribute('href'),location.href);
          if(u.origin===location.origin && !u.searchParams.get('demo')){
            u.searchParams.set('demo','1'); a.href=u.toString();
          }
        }catch(err){}
      },true);
    }catch(e){}
  }
  window.markDemo=mark;
  window.IS_DEMO=isDemo;
  if(isDemo()){
    if(document.body)mark();
    else document.addEventListener('DOMContentLoaded',mark);
  }
})();
