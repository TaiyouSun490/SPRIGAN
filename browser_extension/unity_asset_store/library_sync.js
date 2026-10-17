/* MV3 coordinator: one bounded page at a time, durable checkpoints and native ACKs.
 * A pending sanitized page is saved BEFORE transmission, so a lost native reply is
 * replayed verbatim. No popup, long-lived worker or browser session export is needed.
 */
(function (root) {
  "use strict";
  const {SOURCES, isLibraryPage} = root.FafnirLibraryAdapters;
  const JOB = s => "fafnir.library." + s;
  const CONFIG = "fafnir.library.config";
  const ALARM = "fafnir-library-step", WATCH = "fafnir-library-watch";
  const running = j => j && ["queued", "running", "retrying"].includes(j.state);
  const newId = () => crypto.randomUUID().replaceAll("-", "");
  async function position(cp) {
    const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(cp)));
    return [...new Uint8Array(bytes)].map(n => n.toString(16).padStart(2, "0")).join("");
  }
  class LibrarySync {
    constructor(browser, native) {this.browser = browser; this.native = native; this.busy = false; this.writes = Promise.resolve();}
    async get(key) {return (await this.browser.storage.local.get(key))[key];}
    async put(key, value) {await this.browser.storage.local.set({[key]: value});}
    async mutate(source, id, fn) {
      const task = this.writes.then(async () => {
        const j = await this.get(JOB(source));
        if (!j || id && j.id !== id) return null;
        const next = fn(j);
        if (next) {next.updatedAt = Date.now(); await this.put(JOB(source), next);}
        return next;
      });
      this.writes = task.catch(() => {});
      return task;
    }
    async call(action, payload) {
      let r;
      try {r = await this.native({action, schema_version: 1, ...payload});} catch {throw new Error("native_unavailable");}
      if (!r?.ok) throw new Error(["asset_hub_error", "asset_hub_operation_failed"].includes(r?.error?.code) ? "site_changed" : "native_unavailable");
      return r.result;
    }
    async wake(delay = 1500) {await this.browser.alarms.create(ALARM, {when: Date.now() + delay});}
    async boot() {
      // A recurring watchdog also survives suspension between storage and alarm writes.
      await this.browser.alarms.create(WATCH, {periodInMinutes: 1});
      await this.wake();
    }
    async permission(source) {return this.browser.permissions.contains({origins: SOURCES[source].permissions});}
    async tab(source, existing, active = false) {
      const spec = SOURCES[source];
      let tab;
      existing ||= await this.get("fafnir.library.tab." + source);
      if (existing) try {tab = await this.browser.tabs.get(existing);} catch {}
      if (tab && isLibraryPage(source, tab.url)) {
        if (active) await this.browser.tabs.update(tab.id, {active: true});
        return tab;
      }
      // Only create dedicated library tabs; never navigate an unrelated user tab.
      tab = await this.browser.tabs.create({url: spec.origin + spec.library, active});
      await this.put("fafnir.library.tab." + source, tab.id);
      return tab;
    }
    async startSource(source, maxPages) {
      const old = await this.get(JOB(source));
      if (running(old)) return;
      let tabId = old?.tabId || await this.get("fafnir.library.tab." + source);
      if (tabId) try {await this.browser.tabs.get(tabId);} catch {tabId = undefined;}
      const job = {id: newId(), source, state: "queued", cp: {}, pages: 0, count: 0, attempts: 0,
        maxPages, tabId, visited: [], createdAt: Date.now(), updatedAt: Date.now()};
      await this.put(JOB(source), job);
    }
    async status() {
      const config = await this.get(CONFIG) || {sources: ["booth", "fab", "humble"], hours: 0, maxPages: 10000};
      const jobs = [];
      for (const source of Object.keys(SOURCES)) {
        const j = await this.get(JOB(source));
        jobs.push({source, name: SOURCES[source].name, connected: await this.permission(source),
          state: j?.state || "idle", id: j?.id, pages: j?.pages || 0, count: j?.count || 0,
          error: j?.error || "", updatedAt: j?.updatedAt, tabId: j?.tabId});
      }
      return {config, jobs};
    }
    async handle(m) {
      if (m.action === "library_sync_status") return this.status();
      if (m.action === "library_sync_schedule_off") {
        const config = await this.get(CONFIG);
        if (config) await this.put(CONFIG, {...config, hours: 0});
        return this.status();
      }
      if (m.action === "library_sync_start") {
        if (m.owned_confirmation !== true || !Array.isArray(m.sources) || !m.sources.length || m.sources.some(s => !SOURCES[s]) || ![0, 6, 24, 168].includes(m.hours)
            || !Number.isSafeInteger(m.maxPages) || m.maxPages < 10 || m.maxPages > 100000) throw Error("同期する購入元・所有の確認・設定を確認してください。");
        // Fail before opening tabs when the Native Host has not been configured.
        await this.call("hub_library_status", {});
        for (const s of new Set(m.sources)) {
          const old = await this.get(JOB(s));
          if (old && ["paused", "blocked", "needs_login"].includes(old.state)) throw Error(SOURCES[s].name + "は中断中です。「再開」または「最初から」を選んでください。");
        }
        await this.put(CONFIG, {sources: [...new Set(m.sources)], hours: m.hours, maxPages: m.maxPages, nextAt: Date.now() + m.hours * 3600000});
        for (const s of new Set(m.sources)) await this.startSource(s, m.maxPages);
        await this.boot(); return this.status();
      }
      const source = m.source;
      if (!SOURCES[source]) throw Error("対応していない購入元です。");
      if (m.action === "library_sync_connect") {
        if (!await this.permission(source)) throw Error("サイトの接続を許可してください。");
        const job = await this.get(JOB(source)), tab = await this.tab(source, job?.tabId, true);
        if (job) await this.mutate(source, job.id, j => ({...j, tabId: tab.id}));
        return this.status();
      }
      if (m.action === "library_sync_pause") {
        const j = await this.mutate(source, null, j => running(j) ? {...j, state: "paused"} : j);
        if (j?.begun && j.state === "paused") await this.call("hub_library_state", {run_id: j.id, state: "paused", error_code: ""}).catch(() => {});
      } else if (m.action === "library_sync_resume") {
        const j = await this.get(JOB(source));
        if (!j || !["paused", "blocked", "needs_login"].includes(j.state)) throw Error("再開できる同期はありません。");
        if (!await this.permission(source)) throw Error("先に購入元に接続してください。");
        if (j.begun) {
          const receipt = await this.call("hub_library_status", {run_id: j.id});
          if (receipt.state === "cancelled") throw Error("この同期は中断済みです。「最初から」を選んでください。");
          // A terminal page may already be committed when its ACK was lost.
          // Replay the pending page instead of trying to reopen a completed run.
          if (receipt.state !== "complete") await this.call("hub_library_state", {run_id: j.id, state: "running", error_code: ""});
        }
        const tab = await this.tab(source, j.tabId);
        await this.mutate(source, j.id, x => ({...x, state: "queued", error: "", attempts: 0, retryAt: 0, tabId: tab.id, loadingSince: Date.now()}));
        await this.boot();
      } else if (m.action === "library_sync_restart") {
        if (!Number.isSafeInteger(m.maxPages) || m.maxPages < 10 || m.maxPages > 100000) throw Error("走査上限は10〜100000にしてください。");
        const old = await this.get(JOB(source));
        if (running(old)) throw Error("一時停止してから最初から同期してください。");
        if (old?.begun && old.state !== "complete") {
          const receipt = await this.call("hub_library_status", {run_id: old.id});
          if (!["complete", "cancelled"].includes(receipt.state)) await this.call("hub_library_state", {run_id: old.id, state: "cancelled", error_code: ""});
        }
        const config = await this.get(CONFIG);
        if (config) await this.put(CONFIG, {...config, maxPages: m.maxPages});
        await this.startSource(source, m.maxPages); await this.boot();
      } else throw Error("未対応の同期操作です。");
      return this.status();
    }
    async stop(job, code) {
      const retry = ["network_error", "rate_limited"].includes(code) && job.attempts < 3;
      const state = retry ? "retrying" : code === "login_required" ? "needs_login" : "blocked";
      const result = await this.mutate(job.source, job.id, latest => running(latest) ? {...latest, state, error: code, attempts: latest.attempts + 1,
        retryAt: Date.now() + (code === "rate_limited" ? 60000 : 5000) * 2 ** latest.attempts} : latest);
      if (result?.begun && !retry && ["needs_login", "blocked"].includes(result.state)) await this.call("hub_library_state", {run_id: job.id, state, error_code: code}).catch(() => {});
    }
    async step(job) {
      const source = job.source;
      if (job.retryAt && job.retryAt > Date.now()) return;
      if (!await this.permission(source)) return this.stop(job, "permission_required");
      if (job.pages >= job.maxPages) return this.stop(job, "page_limit");
      if (!job.begun) {
        await this.call("hub_library_begin", {run_id: job.id, source, owned_confirmation: true});
        job = await this.mutate(source, job.id, j => ({...j, begun: true}));
      }
      if (!job || !running(job)) return;
      if (!job.pending) {
        let tab;
        if (!job.tabId) {
          tab = await this.tab(source);
          await this.mutate(source, job.id, j => ({...j, tabId: tab.id, loadingSince: Date.now()}));
          return; // navigation completes between ticks, rather than a fragile fixed wait
        }
        try {tab = await this.browser.tabs.get(job.tabId);} catch {return this.stop(job, "tab_closed");}
        if (tab.status === "loading") {
          if (Date.now() - (job.loadingSince || job.createdAt) > 60000) return this.stop(job, "network_error");
          return;
        }
        if (!isLibraryPage(source, tab.url)) return this.stop(job, "login_required");
        await this.browser.scripting.executeScript({target: {tabId: tab.id}, files: ["library_adapters.js"]});
        const r = await this.browser.scripting.executeScript({target: {tabId: tab.id},
          func: async (s, cp) => globalThis.FafnirLibraryAdapters.collectPage(s, cp), args: [source, job.cp]});
        const page = r[0]?.result;
        if (!page?.ok) return this.stop(job, page?.code || "site_changed");
        const here = await position(job.cp), next = await position(page.next);
        if (!page.terminal && (here === next || job.visited.includes(next))) return this.stop(job, "site_changed");
        // Detect ignored page/cursor parameters, without treating Humble duplicate purchases as errors.
        const phase = job.cp.phase || "library";
        if (source !== "humble" && page.items.length && page.signature === job.lastSignature && job.lastPhase === phase) return this.stop(job, "site_changed");
        job = await this.mutate(source, job.id, j => ({...j, pending: page}));
      }
      if (!job || !running(job)) return;
      const p = job.pending;
      const receipt = await this.call("hub_library_page", {run_id: job.id, page_index: job.pages, items: p.items, terminal: p.terminal, coverage: p.coverage});
      const pos = await position(job.cp);
      await this.mutate(source, job.id, j => ({...j, state: p.terminal ? "complete" : j.state === "paused" ? "paused" : "running",
        cp: p.next, pending: null, pages: receipt.pages, count: receipt.count, attempts: 0, retryAt: 0, error: "",
        lastSignature: p.signature, lastPhase: job.cp.phase || "library", loadingSince: 0, visited: [...j.visited, pos]}));
    }
    async tick() {
      if (this.busy) return;
      this.busy = true;
      try {
        const config = await this.get(CONFIG);
        if (config?.hours && config.nextAt <= Date.now()) {
          for (const s of config.sources) {
            const j = await this.get(JOB(s));
            if (!j || j.state === "complete") await this.startSource(s, config.maxPages);
          }
          await this.put(CONFIG, {...config, nextAt: Date.now() + config.hours * 3600000});
        }
        for (const source of Object.keys(SOURCES)) {
          const job = await this.get(JOB(source));
          if (!running(job)) continue;
          try {await this.step(job);} catch (e) {
            await this.stop(job, ["site_changed", "native_unavailable"].includes(e.message) ? e.message : "network_error");
          }
        }
        const jobs = await Promise.all(Object.keys(SOURCES).map(s => this.get(JOB(s))));
        if (jobs.some(running)) await this.wake();
      } finally {this.busy = false;}
    }
  }
  root.FafnirLibrarySync = {LibrarySync, ALARM, WATCH};
  if (typeof module !== "undefined" && module.exports) module.exports = root.FafnirLibrarySync;
})(globalThis);
