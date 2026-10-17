"use strict";

document.querySelector("#hub-full-sync").addEventListener("click", () => chrome.runtime.openOptionsPage());

const SCHEMA_VERSION = 1;
const GUI_URL = "http://127.0.0.1:8770/";
const $ = (selector) => document.querySelector(selector);
let productAvailable = false;
let busy = false;

function setStatus(message, state = "") {
  $("#status").textContent = String(message).slice(0, 500);
  $("#status").dataset.state = state;
}

function renderCategories(values) {
  const root = $("#categories");
  root.replaceChildren();
  values.forEach((category) => {
    const label = document.createElement("label");
    const input = document.createElement("input");
    const text = document.createElement("span");
    input.type = "checkbox";
    input.value = category.key;
    text.textContent = category.title;
    label.append(input, text);
    root.append(label);
  });
}

function selectedCategories() {
  return [...document.querySelectorAll("#categories input:checked")]
    .map((input) => input.value);
}

function hasRagContext() {
  return Boolean($("#user-alias").value.trim() ||
    $("#notes").value.trim() ||
    document.querySelector("#categories input:checked"));
}

function updateActions() {
  $("#save").disabled = busy || !productAvailable;
  $("#owned-confirmation").disabled = busy || !productAvailable;
  $("#save-owned-rag").disabled = busy ||
    !productAvailable ||
    !$("#owned-confirmation").checked ||
    !hasRagContext();
}

async function refresh() {
  const response = await chrome.runtime.sendMessage({
    action: "get_asset_store_context",
    schema_version: SCHEMA_VERSION,
  });
  if (!response?.ok) {
    throw new Error(response?.error?.message || "Fafnirへ接続できません。");
  }
  $("#connection").textContent = "接続済み";
  $("#connection").dataset.state = "ready";
  renderCategories(Array.isArray(response.requirement_categories)
    ? response.requirement_categories
    : []);
  const product = response.product;
  productAvailable = Boolean(product);
  updateActions();
  $("#product-title").textContent = product
    ? product.title
    : "Asset Storeの商品ページではありません";
  $("#product-note").textContent = product
    ? "候補保存、または自己申告で購入済みRAG登録ができます。"
    : "公式の商品ページを開いてから、もう一度拡張を開いてください。";
  setStatus(`手持ち・ローカル ${response.catalog?.owned_assets || 0}件 / 候補 ${response.catalog?.asset_store_market || 0}件`);
}

async function save() {
  busy = true;
  updateActions();
  setStatus("ローカルへ保存中…");
  try {
    const response = await chrome.runtime.sendMessage({
      action: "save_current_asset_store_candidate",
      schema_version: SCHEMA_VERSION,
      categories: selectedCategories(),
      notes: $("#notes").value,
    });
    if (!response?.ok) {
      throw new Error(response?.error?.message || "候補を保存できませんでした。");
    }
    setStatus("Fafnirへ候補を保存しました。", "success");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "保存に失敗しました。", "error");
  } finally {
    busy = false;
    updateActions();
  }
}

async function saveOwnedRag() {
  if (!productAvailable || !$("#owned-confirmation").checked) return;
  const userAlias = $("#user-alias").value;
  const notes = $("#notes").value;
  const categories = selectedCategories();
  if (!userAlias.trim() && !notes.trim() && categories.length === 0) {
    setStatus("呼び名・メモ・用途のいずれかを追加してください。", "error");
    return;
  }
  busy = true;
  updateActions();
  setStatus("購入済みアセットをローカルRAGへ登録中…");
  try {
    const response = await chrome.runtime.sendMessage({
      action: "save_current_asset_store_owned_rag",
      schema_version: SCHEMA_VERSION,
      user_alias: userAlias,
      notes,
      categories,
      purchase_confirmation: true,
    });
    if (!response?.ok) {
      throw new Error(response?.error?.message || "購入済みRAGへ登録できませんでした。");
    }
    $("#owned-confirmation").checked = false;
    setStatus("購入済み（自己申告）としてローカルRAGへ登録しました。", "success");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "RAG登録に失敗しました。", "error");
  } finally {
    busy = false;
    updateActions();
  }
}

$("#save").addEventListener("click", save);
$("#save-owned-rag").addEventListener("click", saveOwnedRag);
$("#owned-confirmation").addEventListener("change", updateActions);
$("#user-alias").addEventListener("input", updateActions);
$("#notes").addEventListener("input", updateActions);
$("#categories").addEventListener("change", updateActions);
$("#open-stackforge").addEventListener("click", () => {
  chrome.tabs.create({url: GUI_URL});
});

refresh().catch((error) => {
  $("#connection").textContent = "未接続";
  $("#connection").dataset.state = "error";
  productAvailable = false;
  updateActions();
  setStatus(error instanceof Error ? error.message : "初期化に失敗しました。", "error");
});

let hubContext=null,hubRows=[];
function hubPreview(rows){hubRows=rows;const root=$("#hub-preview");root.replaceChildren();rows.forEach((row,index)=>{const label=document.createElement("label"),check=document.createElement("input"),text=document.createElement("span");check.type="checkbox";check.dataset.index=String(index);text.textContent=row.title;label.append(check,text);root.append(label);});setStatus(`${rows.length}件の候補。所有している商品だけ選択してください。`);}
async function hubMessage(data){const r=await chrome.runtime.sendMessage({schema_version:SCHEMA_VERSION,...data});if(!r?.ok)throw Error(r?.error?.message||"Asset Hubへ接続できません。");return r;}
$("#hub-open").addEventListener("click",()=>chrome.tabs.create({url:GUI_URL+"hub.html"}));
$("#hub-current").addEventListener("click",()=>{if(hubContext?.product)hubPreview([hubContext.product]);else setStatus("対応する公開商品ページを開いてください。","error");});
$("#hub-library").addEventListener("click",async()=>{try{const r=await hubMessage({action:"hub_capture_library"}),source=$("#hub-source").value,config=hubContext.sources.find(v=>v.id===source),found=new Map();for(const a of r.links){try{const u=new URL(a.url);if(u.protocol!=="https:"||!config.domains.some(d=>u.hostname===d||u.hostname.endsWith("."+d)))continue;const path=u.pathname;if(source==="booth"&&!/^\/(?:(?:ja|en)\/)?items\/\d+/.test(path))continue;if(source==="fab"&&!/\/listings\/[a-z0-9-]+/i.test(path))continue;if(source==="humble"&&!/^\/(store|software|books)\//.test(path))continue;if(source==="itchio"&&(!/^[^.]+\.itch\.io$/.test(u.hostname)||path==="/"))continue;if(source==="gumroad"&&!/^\/l\//.test(path))continue;u.search="";u.hash="";found.set(u.href,{url:u.href,title:a.title});}catch{}}hubPreview([...found.values()]);}catch(e){setStatus(e.message,"error");}});
$("#hub-save").addEventListener("click",async()=>{try{if(!$("#hub-owned").checked)throw Error("所有の申告欄を確認してください。");const rows=[...$("#hub-preview").querySelectorAll("input:checked")].map(c=>({...hubRows[Number(c.dataset.index)],type:$("#hub-kind").value}));rows.forEach(r=>delete r.source);const d=await hubMessage({action:"hub_save_owned",source:$("#hub-source").value,items:rows,owned_confirmation:true});setStatus(`${d.count}件を所有素材として同期しました。`,"success");$("#hub-owned").checked=false;}catch(e){setStatus(e.message,"error");}});
hubMessage({action:"hub_capture_context"}).then(r=>{hubContext=r;$("#hub-source").replaceChildren(...r.sources.map(s=>{const e=document.createElement("option");e.value=s.id;e.textContent=s.name;return e;}));if(r.page_source)$("#hub-source").value=r.page_source;if(r.product){$("#hub-source").value=r.product.source;$("#hub-product").textContent=r.product.title;}else $("#hub-product").textContent="購入ライブラリなら提供元を選び、一括読込してください。";}).catch(e=>setStatus(e.message,"error"));
