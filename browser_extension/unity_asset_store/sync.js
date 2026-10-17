"use strict";
const $ = s => document.querySelector(s);
const chosen = new Set(["booth", "fab", "humble"]);
const states = {idle:"未同期",queued:"開始待ち",running:"同期中",retrying:"接続を再試行中",paused:"一時停止中",needs_login:"ログインが必要",blocked:"未完了・確認が必要",complete:"全件走査完了"};
const errors = {permission_required:"「接続・ログイン」でサイトへの接続を許可してください。",login_required:"公式サイトでログイン・認証を完了し、「再開」を押してください。",site_changed:"ライブラリの形式・ページ送りを確認できません。取得済みの素材は残っています。",library_changed:"走査中にライブラリが変化しました。「最初から」で取り直してください。",rate_limited:"サイトのアクセス制限です。時間を置いて再開してください。",network_error:"通信が途切れました。接続を確認して再開してください。",native_unavailable:"FAFNIR Native Hostの設定・接続を確認し、再開してください。",tab_closed:"購入ライブラリのタブが閉じられました。「再開」で開き直します。",page_limit:"詳細設定の走査上限に達しました。上限を増やして「最初から」を選んでください。"};
let initialized = false, updating = false;
function el(tag, text, cls) {const n=document.createElement(tag); if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function notice(s, error=false){$("#status").textContent=s;$("#status").dataset.error=String(error);}
async function send(action, data={}) {const r=await chrome.runtime.sendMessage({action,schema_version:1,...data});if(!r?.ok)throw Error(r?.error?.message||"拡張機能に接続できません。拡張機能を再読み込みしてください。");return r;}
async function run(fn){try{await fn();}catch(e){notice(e.message,true);}}
function button(label, fn){const b=el("button",label,"secondary");b.type="button";b.addEventListener("click",()=>{b.disabled=true;run(fn).finally(()=>b.disabled=false);});return b;}
function render(data) {
  if(!initialized){chosen.clear();data.config.sources.forEach(s=>chosen.add(s));$("#hours").value=String(data.config.nextAt===undefined?24:data.config.hours);$("#max-pages").value=String(data.config.maxPages);initialized=true;}
  for(const j of data.jobs){
    let card=document.getElementById("source-"+j.source);
    if(!card){
      card=el("article",undefined,"source");card.id="source-"+j.source;
      const name=el("label",undefined,"name"),check=el("input");check.type="checkbox";check.checked=chosen.has(j.source);check.setAttribute("aria-label",j.name+"を同期");check.addEventListener("change",()=>check.checked?chosen.add(j.source):chosen.delete(j.source));name.append(check,el("h3",j.name));
      card.append(name,el("p","","connection"),el("p","","state"),el("p","","progress"),el("p","","error"),el("div",undefined,"controls"));$("#sources").append(card);
      const connect=button("接続・ログイン",async()=>{
        // Optional permission must be requested directly within this user gesture.
        const allowed=await chrome.permissions.request({origins:FafnirLibraryAdapters.SOURCES[j.source].permissions});
        if(!allowed)throw Error("接続が許可されませんでした。このサイトの同期は開始しません。");
        render(await send("library_sync_connect",{source:j.source}));notice(j.name+"の公式サイトでログインしてください。パスワードをFAFNIRへ入力する必要はありません。");
      });card.querySelector(".controls").append(connect);
      for(const [action,label] of [["pause","一時停止"],["resume","再開"],["restart","最初から"]]){
        const b=button(label,async()=>{render(await send("library_sync_"+action,{source:j.source,...(action==="restart"?{maxPages:Number($("#max-pages").value)}:{})}));notice(j.name+"："+label+"を受け付けました。");});b.dataset.action=action;card.querySelector(".controls").append(b);
      }
    }
    card.querySelector(".connection").textContent=j.connected?"サイト接続を許可済み（ログイン状態は同期時に確認）":"未接続";
    card.querySelector(".state").textContent=states[j.state]||j.state;
    card.querySelector(".progress").textContent=`${j.count.toLocaleString()}素材 · ${j.pages.toLocaleString()}ページ取得`;
    card.querySelector(".error").textContent=errors[j.error]||"";
    card.querySelector('[data-action="pause"]').disabled=!["queued","running","retrying"].includes(j.state);
    card.querySelector('[data-action="resume"]').disabled=!["paused","blocked","needs_login"].includes(j.state);
    card.querySelector('[data-action="restart"]').disabled=["queued","running","retrying","idle"].includes(j.state);
  }
  $("#schedule-off").disabled=!data.config.hours;
}
async function refresh(){if(updating)return;updating=true;try{render(await send("library_sync_status"));}finally{updating=false;}}
$("#start-form").addEventListener("submit",e=>{e.preventDefault();$("#start").disabled=true;run(async()=>{render(await send("library_sync_start",{sources:[...chosen],hours:Number($("#hours").value),maxPages:Number($("#max-pages").value),owned_confirmation:$("#owned").checked}));notice("同期を開始しました。この画面は閉じても構いません。ログイン待ちのサイトは、接続後に再開してください。");}).finally(()=>$("#start").disabled=false);});
$("#schedule-off").addEventListener("click",()=>run(async()=>{render(await send("library_sync_schedule_off"));$("#hours").value="0";notice("定期更新をオフにしました。進行中の同期は続行します。");}));
$("#hub").addEventListener("click",()=>run(async()=>{const u=new URL($("#hub-url").value);if(u.protocol!=="http:"||!["127.0.0.1","localhost","[::1]"].includes(u.hostname)||u.username||u.password||u.pathname!=="/hub.html"||u.search)throw Error("このPCのAsset Hub URLを指定してください（例：http://127.0.0.1:8770/hub.html）。");u.hash="owned";await chrome.storage.local.set({"fafnir.hub.url":u.href});await chrome.tabs.create({url:u.href});}));
chrome.storage.local.get("fafnir.hub.url").then(d=>{if(d["fafnir.hub.url"])$("#hub-url").value=d["fafnir.hub.url"];});
run(async()=>{await refresh();notice("接続するサイトを選び、公式サイトでログインしてから全件同期を開始してください。");});
setInterval(()=>run(refresh),2500);
