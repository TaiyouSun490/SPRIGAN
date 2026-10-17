/* Read-only store adapters. Injected in an isolated world on an official library tab.
 * Authentication stays in that tab. Never return raw API bodies or private handles.
 */
(function (root) {
  "use strict";
  const SOURCES = Object.freeze({
    booth: {name: "BOOTH", origin: "https://accounts.booth.pm", library: "/library", permissions: ["https://accounts.booth.pm/*"]},
    fab: {name: "Fab", origin: "https://www.fab.com", library: "/library", permissions: ["https://www.fab.com/*"]},
    humble: {name: "Humble", origin: "https://www.humblebundle.com", library: "/home/library", permissions: ["https://www.humblebundle.com/*"]},
  });
  class SyncError extends Error {
    constructor(code) { super(code); this.code = code; }
  }
  const text = (s, max = 500) => typeof s === "string" ? s.replace(/\s+/g, " ").trim().slice(0, max) : "";
  const guid = s => typeof s === "string" && /^(?:[a-f0-9]{32}|[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})$/i.test(s);
  function isLibraryPage(source, value) {
    const spec = SOURCES[source];
    if (!spec) return false;
    try {
      const u = new URL(value);
      return u.origin === spec.origin && !u.username && !u.password && (source === "fab"
        ? /^\/(?:[a-z]{2}(?:-[a-z]{2})?\/)?library(?:\/|$)/i.test(u.pathname)
        : source === "booth" ? /^\/library(?:\/|$)/.test(u.pathname) : /^\/home\/library(?:\/|$)/.test(u.pathname));
    } catch { return false; }
  }
  async function sha(value) {
    const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
    return [...new Uint8Array(bytes)].map(v => v.toString(16).padStart(2, "0")).join("");
  }
  function boothProduct(href) {
    try {
      const u = new URL(href, SOURCES.booth.origin);
      const m = u.pathname.match(/^\/(?:(?:ja|en|ko|zh-cn|zh-tw)\/)?items\/(\d+)\/?$/);
      return u.protocol === "https:" && /^(?:[a-z0-9-]+\.)?booth\.pm$/.test(u.hostname) && m ? m[1] : null;
    } catch { return null; }
  }
  function parseBooth(doc, page, phase) {
    if (doc.querySelector('input[type="password"], form[action*="sign_in"], #challenge-running, .g-recaptcha')) throw new SyncError("login_required");
    const main = doc.querySelector('main, [role="main"], .l-main, #main');
    if (!main) throw new SyncError("site_changed");
    // Only recognizable purchased/downloadable cards, never recommendations or navigation.
    let cards = [];
    for (const selector of [".l-library-item", ".library-item", "[data-product-id]", "div[class*='desktop:rounded-8'][class*='bg-white'][class*='p-16']"]) {
      cards = [...main.querySelectorAll(selector)].filter(c => c.querySelector('.js-download-button, .l-library-item-thumbnail, img[src*="booth.pximg.net"]'));
      if (cards.length) break;
    }
    const items = new Map();
    for (const c of cards) {
      const a = [...c.querySelectorAll("a[href]")].find(v => boothProduct(v.getAttribute("href")));
      const id = a ? boothProduct(a.getAttribute("href")) : c.getAttribute("data-product-id");
      const titleNode = c.querySelector(".item-name, .library-item-title, .text-text-default.font-bold.text-16, h2, h3, [data-title]");
      const title = text(titleNode?.textContent || c.getAttribute("data-title") || a?.textContent);
      if (!/^\d{1,20}$/.test(id || "") || !title) throw new SyncError("site_changed");
      items.set(id, {source_product_id: id, title, url: "https://booth.pm/items/" + id, type: "other", author: text(c.querySelector(".shop-name, .library-item-shop, .text-14.text-text-gray600.break-all")?.textContent, 200)});
    }
    const empty = /(?:購入|受け取った|受取|ライブラリ).{0,30}(?:ありません|ない|登録されていません)|(?:no (?:items|purchases|gifts)|library is empty)/i.test(text(main.textContent, 20000));
    if (!items.size && !empty) throw new SyncError("site_changed");
    if (items.size > 200 || main.querySelector('[data-infinite-scroll], .infinite-scroll, button[data-load-more]')) throw new SyncError("site_changed");
    const current = doc.querySelector('[aria-current="page"], .pagination .current, .pager .current');
    if (current && Number(text(current.textContent)) !== page) throw new SyncError("site_changed");
    const path = "/library" + (phase === "gifts" ? "/gifts" : "");
    let nextPage = null;
    for (const a of doc.querySelectorAll('a[rel="next"], .pagination a[href], .pager a[href], a[href*="?page="]')) {
      if (a.getAttribute("aria-disabled") === "true" || a.classList.contains("disabled")) continue;
      let u;
      try { u = new URL(a.getAttribute("href"), SOURCES.booth.origin); } catch { continue; }
      if (u.origin !== SOURCES.booth.origin || u.pathname.replace(/\/$/, "") !== path) continue;
      const n = Number(u.searchParams.get("page"));
      if (Number.isSafeInteger(n) && n > page) nextPage = Math.min(nextPage ?? n, n);
    }
    if (nextPage && (nextPage !== page + 1 || !items.size)) throw new SyncError("site_changed");
    return {items: [...items.values()], nextPage};
  }
  function fabPath(value) {
    const u = new URL(value, SOURCES.fab.origin);
    if (u.origin !== SOURCES.fab.origin || u.pathname !== "/i/library/search" || u.username || u.password || u.hash) throw new SyncError("site_changed");
    if ([...u.searchParams.keys()].some(k => !["sort_by", "source", "cursor"].includes(k)) || u.searchParams.get("source") !== "acquired") throw new SyncError("site_changed");
    if (u.search.length > 4096) throw new SyncError("site_changed");
    return u.pathname + u.search;
  }
  function parseFab(data) {
    if (!data || !Array.isArray(data.results) || !("next" in data) || data.results.length > 200) throw new SyncError("site_changed");
    const items = data.results.map(item => {
      const l = item.listing || {}, id = l.uid || item.uid, title = text(l.title || item.title);
      if (!guid(id) || !title) throw new SyncError("site_changed");
      const kind = typeof l.listingType === "string" ? l.listingType : l.listingType?.code;
      const type = ({"3d-model": "model", "material": "material", "environment": "world", "audio": "audio", "animation": "animation", "texture": "texture", "tool-and-plugin": "script"})[kind] || "other";
      return {source_product_id: id.toLowerCase(), title, type, url: guid(l.uid) && item.isActiveInLive !== false ? SOURCES.fab.origin + "/listings/" + l.uid : "",
        author: text(l.publisher?.sellerName, 200), license: text(item.entitlement?.licenses?.map(v => v.name).filter(Boolean).join(" / "), 200)};
    });
    if (data.next !== null && data.next !== "" && typeof data.next !== "string") throw new SyncError("site_changed");
    if (!items.length && data.next) throw new SyncError("site_changed");
    return {items, next: data.next ? fabPath(data.next) : null};
  }
  function parseHumbleOrder(order, offset) {
    if (!order || !Array.isArray(order.subproducts)) throw new SyncError("site_changed");
    const entitlements = order.tpkd_dict?.all_tpks;
    if (entitlements != null && !Array.isArray(entitlements)) throw new SyncError("site_changed");
    // Some bundles expose redemption-only products as entitlement metadata.
    // Select names/IDs only; no keys, redemption state or private URLs leave this tab.
    const unique = new Map();
    const keyProducts = (entitlements || []).map(p => ({machine_name:p.machine_name, human_name:p.human_name || p.machine_name}));
    for (const p of [...order.subproducts, ...keyProducts]) {
      if (typeof p.machine_name !== "string" || !/^[a-z0-9][a-z0-9_-]{0,199}$/.test(p.machine_name)) throw new SyncError("site_changed");
      if (!unique.has(p.machine_name)) unique.set(p.machine_name, p);
    }
    const products = [...unique.values()];
    if (offset > products.length) throw new SyncError("library_changed");
    const items = products.slice(offset, offset + 200).map(p => {
      const id = p.machine_name, title = text(p.human_name);
      if (typeof id !== "string" || !/^[a-z0-9][a-z0-9_-]{0,199}$/.test(id) || !title) throw new SyncError("site_changed");
      // Never guess a storefront slug or forward keys, signed URLs or instructions_html.
      let url = "";
      try { const u = new URL(p.url); if (u.origin === SOURCES.humble.origin && /^\/(store|software|books)\/[a-z0-9/_-]+$/i.test(u.pathname)) url = u.origin + u.pathname; } catch {}
      return {source_product_id: id, title, url, type: ({audio: "audio", music: "music", soundtracks: "music"})[p.category] || "other", author: text(p.payee?.human_name, 200)};
    });
    return {items, more: offset + items.length < products.length};
  }
  async function collectPage(source, cp = {}) {
    try {
      const spec = SOURCES[source];
      if (!spec || !isLibraryPage(source, location.origin + location.pathname)) throw new SyncError("login_required");
      async function read(path, json) {
        const u = new URL(path, spec.origin);
        if (u.origin !== spec.origin) throw new SyncError("site_changed");
        let r;
        try { r = await fetch(u.href, {credentials: "same-origin", redirect: "error", cache: "no-store", headers: {Accept: json ? "application/json" : "text/html"}, signal: AbortSignal.timeout(12000)}); }
        catch { throw new SyncError("network_error"); }
        if (r.status === 401 || r.status === 403) throw new SyncError("login_required");
        if (r.status === 429) throw new SyncError("rate_limited");
        if (!r.ok) throw new SyncError(r.status >= 500 ? "network_error" : "site_changed");
        // Bound memory; the response is parsed in the tab and never sent to native messaging.
        const reader = r.body.getReader(), pieces = [];
        let size = 0;
        while (true) {const {value, done} = await reader.read(); if (done) break; size += value.byteLength; if (size > 16000000) {await reader.cancel(); throw new SyncError("site_changed");} pieces.push(value);}
        const content = await new Blob(pieces).text();
        if (json) {try {return JSON.parse(content);} catch {throw new SyncError(/sign.?in|log.?in|captcha/i.test(content.slice(0,20000)) ? "login_required" : "site_changed");}}
        return new DOMParser().parseFromString(content, "text/html");
      }
      let items, next, terminal, coverage = "", snapshot;
      if (source === "booth") {
        const first = parseBooth(await read("/library?page=1", false), 1, "library");
        snapshot = await sha(first.items.map(i => i.source_product_id).sort().join("|"));
        if (cp.snapshot && cp.snapshot !== snapshot) throw new SyncError("library_changed");
        const phase = cp.phase || "library", page = cp.page || 1;
        const parsed = phase === "library" && page === 1 ? first : parseBooth(await read("/library" + (phase === "gifts" ? "/gifts" : "") + "?page=" + page, false), page, phase);
        items = parsed.items;
        terminal = !parsed.nextPage && phase === "gifts";
        next = parsed.nextPage ? {phase, page: parsed.nextPage, snapshot} : {phase: "gifts", page: 1, snapshot};
        if (terminal) coverage = "booth_library_and_gifts_end";
      } else if (source === "fab") {
        const initial = "/i/library/search?sort_by=-createdAt&source=acquired";
        const first = parseFab(await read(initial, true));
        snapshot = await sha(first.items.map(i => i.source_product_id).sort().join("|"));
        if (cp.snapshot && cp.snapshot !== snapshot) throw new SyncError("library_changed");
        const parsed = cp.next ? parseFab(await read(fabPath(cp.next), true)) : first;
        items = parsed.items; terminal = !parsed.next; next = {next: parsed.next, snapshot};
        if (terminal) coverage = "fab_acquired_cursor_end";
      } else {
        const orders = await read("/api/v1/user/order", true);
        if (!Array.isArray(orders) || orders.length > 100000 || orders.some(v => typeof v.gamekey !== "string" || !/^[a-zA-Z0-9_-]{1,128}$/.test(v.gamekey))) throw new SyncError("site_changed");
        const keys = [...new Set(orders.map(v => v.gamekey))].sort(); // private, in-tab only
        snapshot = await sha(keys.join("|"));
        if (cp.snapshot && cp.snapshot !== snapshot) throw new SyncError("library_changed");
        const index = cp.index || 0, offset = cp.offset || 0;
        if (index > keys.length) throw new SyncError("library_changed");
        if (!keys.length) {items = []; terminal = true; next = {index: 0, offset: 0, snapshot};}
        else {
          const parsed = parseHumbleOrder(await read("/api/v1/order/" + keys[index] + "?all_tpkds=true", true), offset);
          items = parsed.items;
          next = {index: parsed.more ? index : index + 1, offset: parsed.more ? offset + items.length : 0, snapshot};
          terminal = next.index === keys.length;
        }
        if (terminal) coverage = "humble_all_orders_end";
      }
      return {ok: true, items, next, terminal, coverage,
        signature: await sha(items.map(v => v.source_product_id).join("|")), snapshot};
    } catch (e) {return {ok: false, code: e instanceof SyncError ? e.code : "site_changed"};}
  }
  const api = {SOURCES, SyncError, isLibraryPage, parseBooth, parseFab, parseHumbleOrder, fabPath, collectPage};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.FafnirLibraryAdapters = api;
})(globalThis);
