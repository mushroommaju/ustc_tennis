'use strict';
// Local CLI adapter. Sensitive results go only to the parent process over stdout.
const WebSocket = require('./work/runtime-validation/WMPFDebugger/node_modules/ws');
const APP_ID = 'wxdff189a3c910e75f';
async function main(input) {
  if (!['status','session','noise'].includes(input.operation)) throw Error('Unsupported operation');
  const ws = new WebSocket('ws://127.0.0.1:62000');
  let id=0; const pending=new Map(); const contexts=new Set();
  const request=(method,params={})=>new Promise((resolve,reject)=>{
    const key=++id; const timer=setTimeout(()=>{pending.delete(key);reject(Error('CDP timeout'));},15000);
    pending.set(key,{resolve,reject,timer}); ws.send(JSON.stringify({id:key,method,params}));
  });
  ws.on('message',raw=>{let m;try{m=JSON.parse(raw);}catch{return;}
    if(m.method==='Runtime.executionContextCreated')contexts.add(m.params.context.id);
    if(m.method==='Runtime.executionContextDestroyed')contexts.delete(m.params.executionContextId);
    const p=pending.get(m.id);if(!p)return;clearTimeout(p.timer);pending.delete(m.id);
    m.error?p.reject(Error('CDP rejected request')):p.resolve(m.result);
  });
  ws.on('close',()=>{for(const p of pending.values()){clearTimeout(p.timer);p.reject(Error('CDP closed'));}pending.clear();});
  try {
    await new Promise((resolve,reject)=>{ws.once('open',resolve);ws.once('error',()=>reject(Error('CDP unavailable')));});
    await request('Runtime.disable'); await request('Runtime.enable');
    const evaluate=async(contextId,expression)=>{
      const r=await request('Runtime.evaluate',{contextId,expression,returnByValue:true,awaitPromise:true});
      if(r.exceptionDetails||!r.result||!Object.hasOwn(r.result,'value'))throw Error('Runtime expression failed');
      return r.result.value;
    };
    const matches=[];
    for(const c of contexts){
      const found=await evaluate(c,`typeof wx!=='undefined'&&wx.getAccountInfoSync&&typeof getApp==='function'?wx.getAccountInfoSync().miniProgram.appId:null`);
      if(found===APP_ID)matches.push(c);
    }
    if(matches.length!==1)throw Error('Target AppService missing or ambiguous');
    const ctx=matches[0];
    const guard=`if(wx.getAccountInfoSync().miniProgram.appId!==${JSON.stringify(APP_ID)})throw Error('Wrong app');const g=getApp().globalData;`;
    if(input.operation==='status')return await evaluate(ctx,`(()=>{${guard}return {appId:${JSON.stringify(APP_ID)},cloudAvailable:!!(wx.cloud&&typeof wx.cloud.callFunction==='function'),loggedIn:!!g.is_login,hasToken:!!g.token,hasOpenId:!!g.open_id};})()`);
    if(input.operation==='session')return await evaluate(ctx,`(()=>{${guard}if(!g.is_login||!g.token||!g.open_id)throw Error('Login required');return {open_id:g.open_id,token:g.token,version:g.accountInfo.miniProgram.version||'1.0.0',deviceFingerprint:g.deviceFingerprint||'',environment:g.environment||''};})()`);
    if(!input.expectedOpenId||!input.payload||typeof input.payload!=='object'||Array.isArray(input.payload))throw Error('Invalid noise input');
    const keys=Object.keys(input.payload).sort().join(',');
    if(keys!=='day,kind_id,selected_block,selected_partner,share_open'||input.payload.kind_id!==2)throw Error('Invalid booking fields');
    if(!Array.isArray(input.payload.selected_block)||input.payload.selected_block.length<2||input.payload.selected_block.length>6)throw Error('Invalid blocks');
    if(!Array.isArray(input.payload.selected_partner)||input.payload.selected_partner.length<1||input.payload.selected_partner.length>3)throw Error('Invalid partners');
    return await evaluate(ctx,`(async()=>{${guard}if(!g.is_login||g.open_id!==${JSON.stringify(input.expectedOpenId)})throw Error('Account mismatch');wx.cloud.init();const r=await wx.cloud.callFunction({name:'noise',data:${JSON.stringify(input.payload)}});if(getApp().globalData.open_id!==${JSON.stringify(input.expectedOpenId)})throw Error('Account changed');return {noise:r.result};})()`);
  } finally {ws.close();}
}
let data='';process.stdin.setEncoding('utf8');process.stdin.on('data',x=>data+=x);process.stdin.on('end',()=>{
  Promise.resolve().then(()=>main(JSON.parse(data))).then(result=>{process.stdout.write(JSON.stringify({ok:true,result}));process.exit(0);}).catch(()=>{process.stdout.write(JSON.stringify({ok:false,error:'Runtime bridge failed; check target app, login and local debugger.'}));process.exit(1);});
});
setTimeout(()=>process.exit(2),45000).unref();
