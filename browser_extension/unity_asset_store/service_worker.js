"use strict";

importScripts("library_adapters.js", "library_sync.js");

const NATIVE_HOST = "jp.game_stack_planner";
const SCHEMA_VERSION = 1;
const GET_CONTEXT = "get_asset_store_context";
const SAVE_CANDIDATE = "save_current_asset_store_candidate";
const SAVE_OWNED_RAG = "save_current_asset_store_owned_rag";
const PRODUCT_PATH = /^\/packages\/(?:[^/?#]+\/)+[^/?#]+-([1-9][0-9]*)(?:\/reviews)?\/?$/i;

function fail(code, message) {
  return {
    ok: false,
    schema_version: SCHEMA_VERSION,
    error: {code, message: String(message).slice(0, 500)},
  };
}

function productPage(value) {
  if (typeof value !== "string" || value.length > 2048) return null;
  try {
    const url = new URL(value);
    const host = url.hostname.toLowerCase().replace(/\.$/, "");
    const match = PRODUCT_PATH.exec(url.pathname.replace(/\\/g, "/"));
    if (
      url.protocol !== "https:" ||
      !["assetstore.unity.com", "marketplace.unity.com"].includes(host) ||
      url.username ||
      url.password ||
      (url.port && url.port !== "443") ||
      !match ||
      /%2f|%5c/i.test(url.pathname)
    ) {
      return null;
    }
    return {productId: match[1], url: url.href};
  } catch {
    return null;
  }
}

async function activeProductTab() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab || !Number.isInteger(tab.id) || !productPage(tab.url)) {
    throw new Error("Unity Asset Storeの商品ページを開いてください。");
  }
  const title = String(tab.title || "").trim().slice(0, 500);
  if (!title) throw new Error("商品名を取得できませんでした。");
  return {
    url: tab.url,
    title: title.replace(/\s*[|–—-]\s*Unity Asset Store\s*$/i, "").trim(),
  };
}

async function native(message) {
  const response = await chrome.runtime.sendNativeMessage(NATIVE_HOST, message);
  if (!response || response.schema_version !== SCHEMA_VERSION) {
    throw new Error("Fafnir Native Hostから不正な応答がありました。");
  }
  return response;
}

async function context(message) {
  if (
    !message ||
    typeof message !== "object" ||
    Array.isArray(message) ||
    message.schema_version !== SCHEMA_VERSION ||
    message.action !== GET_CONTEXT ||
    Object.keys(message).some((key) => !["action", "schema_version"].includes(key))
  ) {
    return fail("invalid_extension_message", "コンテキスト要求が不正です。");
  }
  let product = null;
  try {
    product = await activeProductTab();
  } catch {
    product = null;
  }
  const status = await native({
    action: "get_stackforge_status",
    schema_version: SCHEMA_VERSION,
  });
  if (!status.ok) return status;
  return {
    ...status,
    product,
  };
}

async function save(message) {
  if (
    !message ||
    typeof message !== "object" ||
    Array.isArray(message) ||
    message.schema_version !== SCHEMA_VERSION ||
    message.action !== SAVE_CANDIDATE ||
    Object.keys(message).some(
      (key) => !["action", "schema_version", "categories", "notes"].includes(key)
    ) ||
    !Array.isArray(message.categories) ||
    message.categories.length > 20 ||
    !message.categories.every(
      (value) => typeof value === "string" && /^[a-z0-9_]{1,80}$/.test(value)
    ) ||
    typeof message.notes !== "string" ||
    message.notes.length > 1000
  ) {
    return fail("invalid_extension_message", "候補登録内容が不正です。");
  }
  const product = await activeProductTab();
  return native({
    action: "save_asset_store_candidate",
    schema_version: SCHEMA_VERSION,
    url: product.url,
    title: product.title,
    notes: message.notes.trim(),
    categories: [...new Set(message.categories)],
  });
}

async function saveOwnedRag(message) {
  if (
    !message ||
    typeof message !== "object" ||
    Array.isArray(message) ||
    message.schema_version !== SCHEMA_VERSION ||
    message.action !== SAVE_OWNED_RAG ||
    Object.keys(message).some(
      (key) => ![
        "action",
        "schema_version",
        "user_alias",
        "notes",
        "categories",
        "purchase_confirmation",
      ].includes(key)
    ) ||
    typeof message.user_alias !== "string" ||
    message.user_alias.length > 200 ||
    typeof message.notes !== "string" ||
    message.notes.length > 1000 ||
    !Array.isArray(message.categories) ||
    message.categories.length > 20 ||
    !message.categories.every(
      (value) => typeof value === "string" && /^[a-z0-9_]{1,80}$/.test(value)
    ) ||
    message.purchase_confirmation !== true
  ) {
    return fail("invalid_extension_message", "購入済みRAG登録内容が不正です。");
  }
  const product = await activeProductTab();
  return native({
    action: "save_owned_asset_rag",
    schema_version: SCHEMA_VERSION,
    url: product.url,
    title: product.title,
    user_alias: message.user_alias.trim(),
    notes: message.notes.trim(),
    categories: [...new Set(message.categories)],
    purchase_confirmation: true,
  });
}

const ownedLibrarySync = new FafnirLibrarySync.LibrarySync(chrome, native);
chrome.alarms.onAlarm.addListener(alarm => {
  if ([FafnirLibrarySync.ALARM, FafnirLibrarySync.WATCH].includes(alarm.name)) ownedLibrarySync.tick().catch(() => {});
});
chrome.runtime.onStartup.addListener(() => ownedLibrarySync.boot().catch(() => {}));
chrome.runtime.onInstalled.addListener(() => ownedLibrarySync.boot().catch(() => {}));

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender?.id !== chrome.runtime.id || message?.schema_version !== SCHEMA_VERSION) {
    return false;
  }
  // Full-sync controls are available only to extension pages, never website scripts.
  const action = typeof message.action === "string" && message.action.startsWith("library_sync_") && sender.url?.startsWith(chrome.runtime.getURL(""))
    ? () => ownedLibrarySync.handle(message).then(result => ({ok:true,schema_version:SCHEMA_VERSION,...result}))
    : message.action === "hub_capture_context"
    ? async () => {const [tab] = await chrome.tabs.query({active:true,currentWindow:true});return native({action:"get_asset_hub_capture_context",schema_version:SCHEMA_VERSION,url:tab?.url||"",title:tab?.title||""});}
    : message.action === "hub_capture_library"
    ? async () => {
      const [tab]=await chrome.tabs.query({active:true,currentWindow:true});
      if(!tab?.id)throw Error("購入済みライブラリを開いてください。");
      const u=new URL(tab.url);
      const allowed=[[/^(?:[^.]+\.)?booth\.pm$/,/^\/(?:library|orders)(?:\/|$)/], [/^(?:www\.)?fab\.com$/,/^\/(?:[a-z]{2}\/)?library(?:\/|$)/], [/^(?:www\.)?humblebundle\.com$/,/^\/home\/(?:library|purchases)(?:\/|$)/], [/^(?:[^.]+\.)?itch\.io$/,/^\/(?:my-purchases|my-collections)(?:\/|$)/], [/^(?:[^.]+\.)?gumroad\.com$/,/^\/library(?:\/|$)/]];
      if(u.protocol!=="https:"||!allowed.some(([host,path])=>host.test(u.hostname)&&path.test(u.pathname)))throw Error("対応する購入ライブラリ画面でのみ一括読込できます。その他は商品単位またはCSVを利用してください。");
      const results=await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>[...document.querySelectorAll("a[href]")].filter(a=>a.getClientRects().length&&a.textContent.trim()).slice(0,3000).map(a=>({url:a.href,title:a.textContent.trim().slice(0,500)}))});
      // Visible link metadata only. Never fetch private APIs, cookies, hidden fields or files.
      return {ok:true,schema_version:SCHEMA_VERSION,url:u.origin+u.pathname,links:results[0]?.result||[]};
    }
    : message.action === "hub_save_owned"
    ? () => native({action:"sync_asset_hub_owned",schema_version:SCHEMA_VERSION,source:message.source,items:message.items,owned_confirmation:message.owned_confirmation})
    : message.action === GET_CONTEXT
    ? () => context(message)
    : message.action === SAVE_CANDIDATE
      ? () => save(message)
      : message.action === SAVE_OWNED_RAG
        ? () => saveOwnedRag(message)
      : null;
  if (!action) return false;
  action()
    .then(sendResponse)
    .catch((error) => sendResponse(fail(
      "extension_action_failed",
      error instanceof Error ? error.message : "拡張機能の処理に失敗しました。"
    )));
  return true;
});
