/* No real accounts or network. Exercise MV3 suspension, adapters and pagination. */
const {test} = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const base = path.join(__dirname, "../browser_extension/unity_asset_store");
const adapters = require(path.join(base, "library_adapters.js"));
const {LibrarySync} = require(path.join(base, "library_sync.js"));
const uid = n => `00000000-0000-0000-0000-${String(n).padStart(12,"0")}`;
const row = id => ({source_product_id: String(id), title: "Pack " + id});
const page = (ids, next, terminal=false) => ({ok:true,items:ids.map(row),next,terminal,coverage:terminal?"booth_library_and_gifts_end":"",signature:ids.join("|"),snapshot:"fingerprint"});
function fixture() {
  const storage={}, tabs=new Map(), nativeRuns=new Map(), queues={booth:[],fab:[],humble:[]}, calls=[];
  let nextTab=1, loseReply=false;
  const browser={
    storage:{local:{get:async k=>({[k]:structuredClone(storage[k])}),set:async v=>Object.assign(storage,structuredClone(v))}},
    alarms:{create:async()=>{}}, permissions:{contains:async()=>true},
    tabs:{get:async id=>{if(!tabs.has(id))throw Error("closed");return tabs.get(id);},update:async(id,v)=>Object.assign(tabs.get(id),v),
      create:async({url})=>{const tab={id:nextTab++,url,status:"complete"};tabs.set(tab.id,tab);return tab;}},
    scripting:{executeScript:async spec=>{
      if(spec.files)return [];
      const [s,cp]=spec.args;const q=queues[s].shift();
      if(!q)throw Error("Missing fixture page for " + s);
      return [{result:typeof q==="function"?await q(cp):q}];
    }},
  };
  const native=async m=>{
    calls.push(structuredClone(m));
    let run=nativeRuns.get(m.run_id);
    if(m.action==="hub_library_status")return {ok:true,result:m.run_id?run:{runs:[...nativeRuns.values()]}};
    if(m.action==="hub_library_begin") {
      if(!run){run={id:m.run_id,source:m.source,state:"running",pages:0,count:0,seen:new Set(),batches:[]};nativeRuns.set(m.run_id,run);}
    } else if(m.action==="hub_library_state"){
      if(["complete","cancelled"].includes(run.state))return {ok:false,error:{code:"asset_hub_operation_failed"}};
      run.state=m.state;
    }
    else if(m.action==="hub_library_page"){
      if(m.page_index===run.pages){
        assert.equal(run.state,"running");
        m.items.forEach(v=>run.seen.add(v.source_product_id));run.count=run.seen.size;run.pages++;run.batches.push(JSON.stringify(m));
        if(m.terminal)run.state="complete";
      } else assert.equal(run.batches[m.page_index],JSON.stringify(m));
      if(loseReply){loseReply=false;throw Error("reply lost after commit");}
    }
    return {ok:true,result:{id:run.id,source:run.source,state:run.state,pages:run.pages,count:run.count}};
  };
  const engine=new LibrarySync(browser,native);
  const start=async(sources=["booth"])=>engine.handle({action:"library_sync_start",sources,hours:0,maxPages:10000,owned_confirmation:true});
  return {engine,browser,storage,tabs,queues,calls,nativeRuns,native,start,loseReply:()=>loseReply=true};
}
const job=(f,s="booth")=>f.storage["fafnir.library."+s];

test("all three sources walk subsequent pages and count unique assets",async()=>{
  const f=fixture();
  for(const s of ["booth","fab","humble"]){f.queues[s].push(page([1,2],{page:2}),page([2,3],{page:3},true));}
  await f.start(["booth","fab","humble"]);
  for(let i=0;i<3;i++)await f.engine.tick();
  for(const s of ["booth","fab","humble"]){assert.equal(job(f,s).state,"complete");assert.equal(job(f,s).count,3);assert.equal(job(f,s).pages,2);}
});
test("lost native reply is recovered verbatim after worker recreation",async()=>{
  const f=fixture();f.queues.booth.push(page([1],{page:2},true));await f.start();await f.engine.tick();
  f.loseReply();await f.engine.tick();assert.equal(job(f).state,"blocked");assert.ok(job(f).pending);
  const second=new LibrarySync(f.browser,f.native);
  await second.handle({action:"library_sync_resume",source:"booth"});await second.tick();
  assert.equal(job(f).state,"complete");assert.equal(job(f).count,1);assert.equal(f.queues.booth.length,0);
  assert.equal(f.calls.filter(v=>v.action==="hub_library_page").length,2);
});
test("pause during fetch retains pending data without importing; resume imports it",async()=>{
  const f=fixture();f.queues.booth.push(async()=>{await f.engine.handle({action:"library_sync_pause",source:"booth"});return page([1],{page:2},true);});
  await f.start();await f.engine.tick();await f.engine.tick();
  assert.equal(job(f).state,"paused");assert.ok(job(f).pending);assert.equal(f.calls.filter(v=>v.action==="hub_library_page").length,0);
  await f.engine.handle({action:"library_sync_resume",source:"booth"});await f.engine.tick();assert.equal(job(f).state,"complete");
});
test("login failure in one source does not stop the other sources",async()=>{
  const f=fixture();f.queues.booth.push({ok:false,code:"login_required"});f.queues.fab.push(page([1],{page:2},true));
  await f.start(["booth","fab"]);await f.engine.tick();await f.engine.tick();
  assert.equal(job(f).state,"needs_login");assert.equal(job(f,"fab").state,"complete");
});
test("missing optional site permission never injects into or reads a site",async()=>{
  const f=fixture();f.browser.permissions.contains=async()=>false;await f.start();await f.engine.tick();
  assert.equal(job(f).error,"permission_required");assert.equal(f.tabs.size,0);assert.equal(f.calls.filter(v=>v.action==="hub_library_page").length,0);
});
test("closing a library tab stops and resume creates a replacement",async()=>{
  const f=fixture();await f.start();await f.engine.tick();f.tabs.clear();await f.engine.tick();assert.equal(job(f).error,"tab_closed");
  await f.engine.handle({action:"library_sync_resume",source:"booth"});f.queues.booth.push(page([],{page:1},true));await f.engine.tick();assert.equal(job(f).state,"complete");
});
test("public progress never contains cursors or pending metadata",async()=>{
  const f=fixture();await f.start();job(f).cp={next:"private cursor"};job(f).pending={items:[{title:"private pending"}]};
  const publicData=JSON.stringify(await f.engine.status());assert.ok(!publicData.includes("private"));assert.ok(!publicData.includes('"cp"'));
});
test("page limit stops with incomplete status, never a silent success",async()=>{
  const f=fixture();await f.start();job(f).pages=10000;await f.engine.tick();assert.equal(job(f).error,"page_limit");assert.equal(job(f).state,"blocked");
});
test("restart applies the visible updated page cap",async()=>{
  const f=fixture();await f.start();job(f).state="blocked";
  await f.engine.handle({action:"library_sync_restart",source:"booth",maxPages:20000});
  assert.equal(job(f).maxPages,20000);assert.equal(job(f).pages,0);
});
test("cursor cycle is stopped before it can loop forever",async()=>{
  const f=fixture();f.queues.fab.push(page([1],{next:"a"}),page([2],{next:"b"}),page([3],{next:"a"}));
  await f.start(["fab"]);for(let i=0;i<4;i++)await f.engine.tick();assert.equal(job(f,"fab").state,"blocked");assert.equal(job(f,"fab").pages,2);
});
test("network retry preserves checkpoint and is bounded",async()=>{
  const f=fixture();f.queues.booth.push(...Array.from({length:4},()=>({ok:false,code:"network_error"})));await f.start();await f.engine.tick();
  for(let i=0;i<4;i++){job(f).retryAt=0;await f.engine.tick();}
  assert.equal(job(f).state,"blocked");assert.equal(job(f).pages,0);assert.deepEqual(job(f).cp,{});
});
test("daily renewal restarts completed sources and leaves a paused source paused",async()=>{
  const f=fixture();await f.start(["booth","fab"]);job(f).state="paused";job(f,"fab").state="complete";const previous=job(f,"fab").id;
  f.storage["fafnir.library.config"]={sources:["booth","fab"],hours:24,maxPages:10000,nextAt:1};
  await f.engine.tick();assert.equal(job(f).state,"paused");assert.notEqual(job(f,"fab").id,previous);
  await f.engine.handle({action:"library_sync_schedule_off"});assert.equal(f.storage["fafnir.library.config"].hours,0);
});
test("BOOTH gift duplication across phase boundary is valid",async()=>{
  const f=fixture();f.queues.booth.push(page([1],{phase:"gifts",page:1}),page([1],{phase:"gifts",page:1},true));
  await f.start();for(let i=0;i<3;i++)await f.engine.tick();assert.equal(job(f).state,"complete");assert.equal(job(f).count,1);
});
test("Fab parses acquired listings and delisted inventory without private links",()=>{
  const r=adapters.parseFab({results:[{uid:uid(99),isActiveInLive:true,listing:{uid:uid(1),title:"River",listingType:"environment",publisher:{sellerName:"Studio"}},entitlement:{licenses:[{name:"Standard"}]}},{uid:uid(2),title:"Removed",isActiveInLive:false}],next:null});
  assert.equal(r.items[0].type,"world");assert.equal(r.items[1].url,"");assert.equal(r.next,null);
  for(const data of [{results:[]},{results:[{}],next:null},{results:[],next:"https://evil.example/i/library/search?source=acquired"}])assert.throws(()=>adapters.parseFab(data));
});
test("Fab never follows checkout, unrelated origins or marketplace search",()=>{
  for(const v of ["https://evil.example/i/library/search?source=acquired","/checkout","/i/library/search?source=all","/i/library/search?source=acquired&filter=wishlist"]){assert.throws(()=>adapters.fabPath(v));}
});
test("localized Fab library redirects remain valid; lookalike paths do not",async()=>{
  for(const language of ["en","ja","pt-BR","zh-cn"])assert.ok(adapters.isLibraryPage("fab",`https://www.fab.com/${language}/library`));
  for(const u of ["https://www.fab.com/library-fake","https://www.fab.com/ja/listings/abc","https://evil.example/library","https://user:password@www.fab.com/library"])assert.ok(!adapters.isLibraryPage("fab",u));
  const f=fixture();await f.start(["fab"]);await f.engine.tick();f.tabs.get(job(f,"fab").tabId).url="https://www.fab.com/ja/library";
  f.queues.fab.push(page([1],{page:2},true));await f.engine.tick();assert.equal(job(f,"fab").state,"complete");
  await withFetch("fab",[{data:{results:[{uid:uid(1),title:"Forest"}],next:null}}],async()=>{
    global.location.pathname="/ja/library";assert.equal((await adapters.collectPage("fab",{})).terminal,true);
  });
});
test("Humble emits all bundle constituents in bounded chunks, no keys or download links",()=>{
  const order={gamekey:"PRIVATE-KEY",tpkd_dict:{steam_key:"SECRET"},subproducts:Array.from({length:205},(_,i)=>({machine_name:"sfx_"+i,human_name:"Rain "+i,category:"audio",downloads:[{url:"https://private.invalid/signed?key=SECRET"}],instructions_html:"PRIVATE",payee:{human_name:"Studio"}}))};
  const first=adapters.parseHumbleOrder(order,0),last=adapters.parseHumbleOrder(order,200);
  assert.equal(first.items.length,200);assert.equal(first.more,true);assert.equal(last.items.length,5);assert.equal(last.more,false);
  assert.equal(first.items[0].type,"audio");assert.equal(first.items[0].url,"");assert.ok(!JSON.stringify(first).includes("PRIVATE"));assert.ok(!JSON.stringify(first).includes("SECRET"));
  assert.throws(()=>adapters.parseHumbleOrder({subproducts:[{human_name:"missing product id"}]},0));
  assert.equal(adapters.parseHumbleOrder({subproducts:[{machine_name:"asset_pack",human_name:"Asset pack",category:"software"}]},0).items[0].type,"other");
});
test("Humble redemption-only entitlements contribute metadata without exporting keys",()=>{
  const r=adapters.parseHumbleOrder({subproducts:[{machine_name:"forest",human_name:"Forest download",category:"audio"}],tpkd_dict:{all_tpks:[
    {machine_name:"forest",human_name:"Forest Steam",redeemed_key_val:"PRIVATE-STEAM-KEY"},
    {machine_name:"asset_license",human_name:"Asset licence",redeemed_key_val:"PRIVATE-KEY",instructions_html:"PRIVATE-HTML"}]}},0);
  assert.equal(r.items.length,2);assert.equal(r.items[0].title,"Forest download");assert.equal(r.items[0].type,"audio");
  assert.equal(r.items[1].source_product_id,"asset_license");assert.ok(!JSON.stringify(r).includes("PRIVATE"));
});
test("Humble null entitlements are empty and sparse key metadata uses the product ID",()=>{
  assert.deepEqual(adapters.parseHumbleOrder({subproducts:[],tpkd_dict:{all_tpks:null}},0).items,[]);
  const r=adapters.parseHumbleOrder({subproducts:[],tpkd_dict:{all_tpks:[{machine_name:"asset_license",redeemed_key_val:"PRIVATE"}]}},0);
  assert.equal(r.items[0].title,"asset_license");assert.ok(!JSON.stringify(r).includes("PRIVATE"));
});
async function withFetch(source,responses,fn){
  const oldFetch=global.fetch,oldLocation=global.location,requests=[];
  global.location={origin:adapters.SOURCES[source].origin,pathname:adapters.SOURCES[source].library};
  global.fetch=async(u,options)=>{requests.push({url:u,options});const value=responses.shift();if(!value)throw Error("No fixture response");return new Response(JSON.stringify(value.data),{status:value.status||200,headers:{"content-type":"application/json"}});};
  try{return await fn(requests);}finally{global.fetch=oldFetch;global.location=oldLocation;}
}
test("Humble traverses all orders, deduplicates order handles and keeps them in tab",async()=>{
  const orders=[{gamekey:"PRIVATE2"},{gamekey:"PRIVATE1"},{gamekey:"PRIVATE1"}];
  await withFetch("humble",[{data:orders},{data:{subproducts:[{machine_name:"forest",human_name:"Forest"}]}},{data:orders},{data:{subproducts:[{machine_name:"river",human_name:"River"}]}}],async requests=>{
    const a=await adapters.collectPage("humble",{}),b=await adapters.collectPage("humble",a.next);
    assert.equal(a.terminal,false);assert.equal(b.terminal,true);assert.equal(b.coverage,"humble_all_orders_end");assert.ok(!JSON.stringify([a,b]).includes("PRIVATE"));
    assert.ok(requests.every(r=>r.options.credentials==="same-origin"));assert.ok(requests.every(r=>!r.options.method||r.options.method==="GET"));
  });
});
test("empty Humble library has explicit terminal evidence",async()=>{
  await withFetch("humble",[{data:[]}],async()=>{const r=await adapters.collectPage("humble",{});assert.equal(r.terminal,true);assert.deepEqual(r.items,[]);});
});
test("account/library changes and authentication failures are incomplete",async()=>{
  await withFetch("humble",[{data:[{gamekey:"OTHER-ACCOUNT"}]}],async()=>{const r=await adapters.collectPage("humble",{snapshot:"old"});assert.equal(r.code,"library_changed");});
  await withFetch("fab",[{data:{},status:403}],async()=>assert.equal((await adapters.collectPage("fab",{})).code,"login_required"));
  await withFetch("fab",[{data:{},status:429}],async()=>assert.equal((await adapters.collectPage("fab",{})).code,"rate_limited"));
});
test("full-sync permissions are optional and limited to the three library origins",()=>{
  const manifest=require(path.join(base,"manifest.json"));assert.ok(!manifest.permissions.includes("cookies"));assert.ok(!manifest.host_permissions);
  assert.deepEqual(new Set(manifest.optional_host_permissions),new Set(Object.values(adapters.SOURCES).flatMap(s=>s.permissions)));
  const fs=require("node:fs");
  for(const file of ["library_adapters.js","library_sync.js","service_worker.js"]){const s=fs.readFileSync(path.join(base,file),"utf8");for(const bad of ["document.cookie","chrome.cookies","localStorage","sessionStorage","Authorization:"])assert.ok(!s.includes(bad));}
});
