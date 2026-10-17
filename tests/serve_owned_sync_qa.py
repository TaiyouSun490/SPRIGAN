"""Ephemeral browser QA: actual sync UI/coordinator + native handler + temporary DB.

The browser API and store responses are fixtures; no store accounts are contacted.
Run with the repository venv; automatically stops after five minutes.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import tempfile
import threading

from game_stack_planner.api import GameStackApplication
from game_stack_planner.native_messaging import handle_message

EXT = Path(__file__).resolve().parents[1] / "browser_extension" / "unity_asset_store"
BRIDGE = r'''
"use strict";
const data = {}, tabs = new Map(), permissions = new Set();
let tabId = 0;
const chromeFixture = {
 storage:{local:{get:async k=>({[k]:structuredClone(data[k])}),set:async v=>Object.assign(data,structuredClone(v))}},
 permissions:{contains:async({origins})=>origins.every(v=>permissions.has(v)),request:async({origins})=>{origins.forEach(v=>permissions.add(v));return true;}},
 alarms:{create:async(name,options)=>{if(options.when)setTimeout(()=>engine.tick(),Math.max(300,options.when-Date.now()));}},
 tabs:{get:async id=>tabs.get(id),update:async(id,v)=>Object.assign(tabs.get(id),v),create:async({url})=>{const tab={id:++tabId,url,status:"complete"};tabs.set(tab.id,tab);return tab;}},
 scripting:{executeScript:async spec=>{
   if(spec.files)return [];
   const [s,cp]=spec.args,n=cp.index||0;
   const ids={booth:["101","102","103"],fab:["11111111-2222-3333-4444-555555555555","22222222-2222-3333-4444-555555555555","33333333-2222-3333-4444-555555555555"],humble:["forest_sfx","river_pack","bird_songs"]}[s];
   const items=(n===0?ids.slice(0,2):ids.slice(1)).map((id,i)=>({source_product_id:id,title:["Forest ambience","River materials","Bird songs"][n+i],type:s==="humble"?"audio":"pack"}));
   return [{result:{ok:true,items,next:{index:n+1},signature:s+n,terminal:n===1,coverage:n===1?{booth:"booth_library_and_gifts_end",fab:"fab_acquired_cursor_end",humble:"humble_all_orders_end"}[s]:""}}];
 }}
};
const engine=new FafnirLibrarySync.LibrarySync(chromeFixture,async message=>{
 const r=await fetch("/qa/native",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(message)});return r.json();
});
chromeFixture.runtime={sendMessage:async message=>{try{return {ok:true,...await engine.handle(message)};}catch(e){return {ok:false,error:{message:e.message}};}}};
// Chrome is substituted only on this isolated fixture origin.
window.chrome=chromeFixture;
const banner=document.createElement("p");banner.textContent="QA FIXTURE — 購入アカウントには接続していません。";banner.style="text-align:center;background:#553b19;color:#fff;margin:0;padding:8px";document.body.prepend(banner);

const outcomes=[];
const check=(name,fn)=>{try{fn();outcomes.push(name+": PASS");}catch(e){outcomes.push(name+": FAIL "+e.message);}};
const parse=(html,page=1,phase="library")=>FafnirLibraryAdapters.parseBooth(new DOMParser().parseFromString(html,"text/html"),page,phase);
const card=(id,title)=>`<article class="l-library-item"><a href="https://shop.booth.pm/ja/items/${id}?ref=test"><span class="item-name">${title}</span></a><button class="js-download-button" data-href="PRIVATE-DOWNLOAD"></button><span class="shop-name">Fixture creator</span></article>`;
const expect=(value)=>{if(!value)throw Error("assertion failed");};
check("BOOTH page 1 / public product links",()=>{const r=parse(`<main>${card(1,"Forest")}${card(2,"River")}</main><nav class="pagination"><span class="current">1</span><a rel="next" href="/library?page=2">次へ</a></nav>`);expect(r.items.length===2&&r.nextPage===2&&!JSON.stringify(r).includes("PRIVATE"));});
check("BOOTH last page",()=>expect(parse(`<main>${card(3,"Birds")}</main><nav class="pagination"><a href="/library?page=1">1</a><span class="current">2</span></nav>`,2).nextPage===null));
check("BOOTH gifts",()=>expect(parse(`<main>${card(1,"Gift")}</main><nav class="pagination"><a rel="next" href="/library/gifts?page=2">次へ</a></nav>`,1,"gifts").nextPage===2));
check("BOOTH explicit empty",()=>expect(parse('<main><p>購入した商品はありません</p></main>').items.length===0));
check("BOOTH deduplication / recommendation exclusion",()=>expect(parse(`<main>${card(1,"Owned")}${card(1,"Owned")}</main><aside><a href="https://booth.pm/items/999">Recommended</a></aside>`).items.length===1));
check("BOOTH wrong pagination fails",()=>{try{parse(`<main>${card(1,"Forest")}</main><span aria-current="page">1</span>`,2);throw Error("accepted wrong page");}catch(e){expect(e.code==="site_changed");}});
check("BOOTH login fails",()=>{try{parse('<main><input type="password"></main>');throw Error("accepted login");}catch(e){expect(e.code==="login_required");}});
check("BOOTH unknown markup fails",()=>{try{parse('<main><p>Changed layout</p></main>');throw Error("accepted unknown page");}catch(e){expect(e.code==="site_changed");}});
const evidence=document.createElement("details"),summary=document.createElement("summary"),results=document.createElement("pre");summary.textContent="BOOTH DOM解析テスト："+outcomes.filter(v=>v.includes(": PASS")).length+" / "+outcomes.length+" PASS";results.textContent=outcomes.join("\n");evidence.append(summary,results);document.querySelector("main").append(evidence);
'''


def main():
    with tempfile.TemporaryDirectory(prefix="fafnir-owned-sync-qa-") as temporary:
        os.environ["LOCALAPPDATA"] = str(Path(temporary) / "appdata")
        app = GameStackApplication(Path(temporary) / "catalog.db")

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path == "/qa_bridge.js":
                    body, mime = BRIDGE.encode(), "application/javascript"
                elif path == "/sync.html":
                    html = (EXT / "sync.html").read_text(encoding="utf-8")
                    html = html.replace('<script src="sync.js" defer>', '<script src="library_sync.js" defer></script><script src="qa_bridge.js" defer></script><script src="sync.js" defer>')
                    body, mime = html.encode(), "text/html; charset=utf-8"
                elif path.lstrip("/") in {"sync.css", "sync.js", "library_adapters.js", "library_sync.js", "icons/icon48.png"}:
                    target = EXT / path.lstrip("/")
                    body, mime = target.read_bytes(), mimetypes.guess_type(target)[0] or "application/octet-stream"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path != "/qa/native":
                    self.send_error(404)
                    return
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size < 1000000:
                    self.send_error(400)
                    return
                try:
                    message = json.loads(self.rfile.read(size))
                    result = handle_message(message, app)
                except Exception:
                    result = {"ok": False, "schema_version": 1, "error": {"message": "QA fixture native operation failed"}}
                body = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        print(f"http://127.0.0.1:{server.server_port}/sync.html", flush=True)
        timer = threading.Timer(300, server.shutdown)
        timer.daemon = True
        timer.start()
        try: server.serve_forever(poll_interval=.2)
        except KeyboardInterrupt: pass
        finally:
            timer.cancel()
            server.server_close()
            app.close()


if __name__ == "__main__": main()
