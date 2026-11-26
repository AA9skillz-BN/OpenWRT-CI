// Run the actual view with simulated RPC/DOM; never operate a router.
'use strict';
const fs = require('fs'), path = require('path'), assert = require('assert');
const source = fs.readFileSync(path.join(__dirname,
 '../../Scripts/TR3000-512/luci-app-tr3000-ota/htdocs/luci-static/resources/view/tr3000-ota/overview.js'), 'utf8');
const tag = 'TR3000-512-VIKINGYFY-owrt-fixture';
function fixture(options = {}) {
 const nodes = [], calls = []; let accept = false, poll, modals = 0;
 let status = {enabled:true,ota_ready:true,current:'old',images:[],keep_config:true,
  cached:{latest:tag,size:4,sha256:'a'.repeat(64),update:true}, ...options.status};
 function E(name,attrs,children) {
  const node={name,attrs:attrs||{},children:Array.isArray(children)?children:[children],style:{},value:'',textContent:'',
   handlers:{},disabled:!!(attrs&&attrs.disabled),appendChild(c){this.children.push(c);},addEventListener(e,f){this.handlers[e]=f;}};
  if(attrs&&typeof attrs.click==='function')node.handlers.click=attrs.click;
  nodes.push(node);return node;
 }
 const ui = options.noUi ? undefined : {
  showModal(_title, body) {
   modals += 1;
   const target = accept ? '确认继续' : '取消';
   const btn = nodes.slice().reverse().find(n => n.name === 'button' && n.children[0] === target);
   assert(btn && body, 'modal must render action buttons');
   if (!options.keepModalOpen) btn.handlers.click();
  },
  hideModal() {}
 };
 const rpc={declare:spec=>(...args)=>{
  calls.push([spec.method,args]);
  if(options.lost&&spec.method!=='status')return Promise.reject(Error('response lost'));
  if(options.pollLost&&spec.method==='status')return Promise.reject(Error('status lost'));
  return Promise.resolve(spec.method==='status'?status:options.response||{started:true});
 }};
 const view=new Function('view','rpc','poll','ui','E','confirm',source)({extend:x=>x},rpc,{add(f){poll=f;}},ui,E,()=>accept);
 view.readonly=options.readonly;view.render(status);
 return {calls, nodes, modals:()=>modals, button:text=>nodes.find(n=>n.name==='button'&&n.children[0]===text),
  confirm:v=>accept=v, refresh:s=>{if(s)status=s;return poll();}};
}
(async()=>{
 const f=fixture(), upgrade=f.button('升级（保留配置）');
 assert.strictEqual(upgrade.disabled,false,'one click may download and verify as part of upgrade');
 upgrade.handlers.click();assert.deepStrictEqual(f.calls,[],'cancel never starts a task');
 f.confirm(true);upgrade.handlers.click();upgrade.handlers.click();
 await new Promise(r=>setImmediate(r));
 assert.deepStrictEqual(f.calls,[['upgrade',[true,tag]]]);
 assert(upgrade.disabled,'submitted operation is latched');
 await f.refresh();assert(upgrade.disabled,'stale idle status cannot release a submitted task');
 for(const options of [{status:{ota_ready:false}},{status:{enabled:false}},{readonly:true},
  {status:{error:'status unavailable'}},{status:{downloading:tag}},
  {status:{operation:{state:'running'}}},{status:{operation:{state:'flashing'}}}]) {
  const blocked=fixture(options);blocked.confirm(true);
  const b=blocked.button('升级（保留配置）');assert(b.disabled);
  b.handlers.click();assert.deepStrictEqual(blocked.calls,[]);
 }
 const wipe=fixture();wipe.confirm(true);wipe.button('升级（不保留配置）').handlers.click();
 await new Promise(r=>setImmediate(r));assert.deepStrictEqual(wipe.calls,[['upgrade',[false,tag]]]);
 const lost=fixture({lost:true});lost.confirm(true);lost.button('升级（保留配置）').handlers.click();
 await new Promise(r=>setImmediate(r));assert(lost.button('升级（保留配置）').disabled);
 const pollLost=fixture({pollLost:true});await pollLost.refresh();assert(pollLost.button('升级（保留配置）').disabled);
 const openModal=fixture({keepModalOpen:true});openModal.button('升级（保留配置）').handlers.click();openModal.button('升级（保留配置）').handlers.click();
 assert.strictEqual(openModal.modals(),1,'open modal latches duplicate clicks');
 assert(!source.includes('#333')&&!source.includes('#e6e6e6'),'TR OTA UI uses dark-mode compatible theme variables');
 assert(!source.includes('official'),'TR must not inherit foreign boot environment actions');
 console.log('TR OTA UI: modal confirmation, single submission, wipe choice, dark-mode styles, layout and status failures verified');
})().catch(e=>{console.error(e);process.exit(1);});
