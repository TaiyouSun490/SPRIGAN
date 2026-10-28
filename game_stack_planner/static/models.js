"use strict";
const $m = s => document.querySelector(s);
const modelViews = new Map();
let nextPages = {}, downloadPlan = null, lastSearch = null, settingsRevision = 0;
const labels = {adopt:"採用",hold:"保留",reject:"却下",pass:"確認済み",fail:"条件外",unknown:"未確認"};
const referenceAxes={reference:"形状・見た目",era:"年代",dimensions:"寸法・スケール",material:"材質・摩耗",construction:"構造・用途"};
function el(tag, text, className) { const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(className)n.className=className; return n; }
async function call(action, payload={}) {
  const response=await fetch(`/api/models/${action}`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const result=await response.json(); if(!response.ok)throw new Error(result.error?.message || "処理に失敗しました"); return result;
}
function link(text,url) {
  const n=el("a",text); try {const u=new URL(url); if(u.protocol==="https:"){n.href=u.href;n.target="_blank";n.rel="noopener noreferrer";}} catch {} return n;
}
function button(text,action) {const b=el("button",text);b.type="button";b.addEventListener("click",()=>run(b,action));return b;}
async function run(b,action){b.disabled=true;try{await action();}catch(e){$m("#message").textContent=e.message;}finally{b.disabled=false;}}
function count(){ $m("#selection-count").textContent=`${[...modelViews.values()].filter(v=>v.selected.checked).length}件選択`; }
function invalidate(v){v.revision++;if(v.evaluation){v.evaluation=null;v.result.textContent="条件または根拠が変わりました。選択した候補を再評価してください。";}}
for(const [axis,title] of Object.entries(referenceAxes)){
  const label=el("label",title+"の条件"),input=el("input");input.id="expected-"+axis;input.maxLength=2000;input.placeholder="指定する項目だけ入力";label.append(input);$m("#reference-expectations").append(label);
}
for(const input of document.querySelectorAll(".review-settings input, .review-settings textarea, .review-settings select"))input.addEventListener("input",()=>{settingsRevision++;modelViews.forEach(invalidate);});
function showMetadata(v){v.meta.textContent=`${v.model.source} · ${v.model.author} · ${v.model.license}\n三角形 ${v.model.triangles??"未確認"} / 材質 ${v.model.materials??"未確認"}${v.model.external_download?"\nOpenUSD・帰属表示と利用条件を確認。取得は配布元から。":""}`;if(v.model.license_url){v.meta.append(document.createTextNode(" "),link("ライセンス原文",v.model.license_url));}}
function showPreview(v){v.preview.replaceChildren();if(!v.model.thumbnail)return;const image=el("img",undefined,"preview");image.src=v.model.thumbnail;image.alt=v.model.title;image.loading="lazy";image.addEventListener("error",()=>{image.replaceWith(el("p","プレビューを取得できません。配布元で確認してください。"));});v.preview.append(image);}
function renderModel(model) {
  if(modelViews.has(model.id))return;
  const card=el("article",undefined,"model-card"), selected=el("input"); selected.type="checkbox"; selected.addEventListener("change",count);
  const pick=el("label",undefined,"select-model");pick.append(selected,el("span","比較に選択"));card.append(pick);
  const preview=el("div");card.append(preview);
  card.append(el("h2",model.title),link("配布元で3D・ライセンスを確認",model.url));
  const meta=el("p"), visual=el("select");
  for(const [value,title] of [["unknown","見た目：未確認"],["pass","見た目：用途に合う"],["fail","見た目：用途に合わない"]]){const o=el("option",title);o.value=value;visual.append(o);}
  visual.setAttribute("aria-label",`${model.title}の見た目の確認`);
  const evidence=el("textarea");evidence.placeholder="見た画像・3D表示と確認した点（例：配布元の回転表示、脚の接合部を確認）";evidence.maxLength=3000;evidence.setAttribute("aria-label",`${model.title}の確認根拠`);
  const v={model,card,meta,preview,selected,visual,evidence,result:el("div",undefined,"result"),evaluation:null,revision:0,reference:{}};modelViews.set(model.id,v);showMetadata(v);showPreview(v);
  const reference=el("details");reference.append(el("summary","リファレンス比較の根拠"));
  for(const [axis,title] of Object.entries(referenceAxes)){
    const label=el("label",title),observed=el("textarea"),basis=el("select"),result=el("select");observed.maxLength=3000;observed.placeholder="このモデルを実際に見て確認した点・寸法（未確認なら空欄）";
    for(const [value,name] of [["unknown","未確認"],["observed","画像・3Dで観察"],["measured","実測した"],["inferred","推測"]]){const o=el("option",name);o.value=value;basis.append(o);}
    for(const [value,name] of [["unknown","照合は未判定"],["pass","条件に合う"],["fail","条件に合わない"]]){const o=el("option",name);o.value=value;result.append(o);}
    basis.setAttribute("aria-label",title+"の根拠の種類");result.setAttribute("aria-label",title+"の手動判断");label.append(observed);reference.append(label,basis,result);v.reference[axis]={observed,basis,result};
    for(const control of [observed,basis,result])control.addEventListener("input",()=>invalidate(v));
  }
  visual.addEventListener("change",()=>invalidate(v));evidence.addEventListener("input",()=>invalidate(v));
  const format=el("select"), resolution=el("select");format.setAttribute("aria-label",`${model.title}の形式`);resolution.setAttribute("aria-label",`${model.title}の解像度`);
  for(const f of (model.source==="sketchfab"?["gltf","glb","usdz"]:["gltf","fbx","blend","usd"])) {const o=el("option",f);o.value=f;format.append(o);}
  for(const f of ["1k","2k","4k","8k"]){const o=el("option",f);o.value=f;resolution.append(o);}resolution.value="2k";resolution.disabled=model.source==="sketchfab";
  const actions=el("div",undefined,"actions");
  actions.append(button("詳細を更新",async()=>{v.model=await call("detail",{model_id:model.id});invalidate(v);showMetadata(v);showPreview(v);$m("#message").textContent="詳細を更新しました。判断前に再評価してください。";}));
  if(!model.external_download)actions.append(button("取得内容を確認",async()=>{
    downloadPlan=await call("prepare_download",{model_id:model.id,format:format.value,resolution:resolution.value,max_bytes:Number($m("#download-limit").value)*1048576});
    const summary=$m("#download-summary");summary.replaceChildren();
    for(const line of [downloadPlan.model.title,`作者：${downloadPlan.model.author}`,`ライセンス：${downloadPlan.model.license}`,`形式：${downloadPlan.format} / ${downloadPlan.resolution}`,`サイズ上限：${Math.round(downloadPlan.max_bytes/1048576)} MiB`,downloadPlan.destination])summary.append(el("p",line));
    if(downloadPlan.model.license_url)summary.append(link("ライセンス原文",downloadPlan.model.license_url));
    $m("#license-accepted").checked=false;$m("#download-start").disabled=true;$m("#download-message").textContent="";$m("#download-dialog").showModal();
  }));
  const graph=el("details"),summary=el("summary","関係・判断履歴"), graphContent=el("div");graph.append(summary,graphContent);
  graph.addEventListener("toggle",async()=>{if(!graph.open)return;graphContent.textContent="読み込み中…";try{const data=await call("graph",{model_id:model.id,depth:1});const list=el("ul");for(const node of data.nodes){if(node.id===model.id)continue;const p=node.properties;list.append(el("li",`${node.kind}: ${p.label||p.reason||p.proposed_decision||p.state||node.id}${p.created_at?" · "+p.created_at:""}`));}graphContent.replaceChildren(list);}catch(e){graphContent.textContent=e.message;}});
  card.append(meta,visual,evidence,reference);if(!model.external_download)card.append(format,resolution);card.append(actions,v.result,graph);$m("#results").append(card);
  if(model.source==="nvidia_simready"&&!model.detail_fetched){preview.textContent="プレビューと利用条件を確認中…";call("detail",{model_id:model.id}).then(data=>{if(modelViews.get(model.id)!==v)return;v.model=data;showMetadata(v);preview.textContent="";if(data.thumbnail)showPreview(v);else preview.textContent="プレビューなし。配布元で確認してください。";}).catch(()=>{preview.textContent="プレビューを取得できません。詳細を更新するか配布元をご確認ください。";});}
}
async function search(source,cursor="",append=false){
  const query=append?lastSearch.query:$m("#query").value;
  const data=await call("search",{query,source,cursor,limit:12});
  if(!append){modelViews.clear();$m("#results").replaceChildren();lastSearch={query,source};}
  data.models.forEach(renderModel);count();
  const errors=Object.entries(data.sources).filter(([,v])=>v.status==="error").map(([k,v])=>`${k}: ${v.message}`);
  $m("#message").textContent=`${modelViews.size}件の候補。${errors.join(" / ")}`;
  nextPages=append?{...nextPages,[source]:data.next[source]}:data.next;$m("#pagination").replaceChildren();
  for(const [s,c] of Object.entries(nextPages)){if(c)$m("#pagination").append(button(`${s}の次の候補`,()=>search(s,c,true)));}
}
$m("#search-form").addEventListener("submit",e=>{e.preventDefault();run(e.submitter,()=>search($m("#source").value));});
$m("#nvidia-refresh").addEventListener("click",()=>run($m("#nvidia-refresh"),async()=>{const r=await call("refresh_nvidia_index");$m("#message").textContent=`NVIDIA索引を更新しました：${r.count}件。検索して確認できます。`;}));
$m("#evaluate").addEventListener("click",()=>run($m("#evaluate"),async()=>{
  const chosen=[...modelViews.values()].filter(v=>v.selected.checked);
  if(!chosen.length)throw new Error("比較する候補を選択してください。");
  const project=$m("#project").value.trim(), brief=$m("#brief").value.trim();
  if(!project||!brief)throw new Error("プロジェクトと用途・見た目の目標を入力してください。");
  const policy={max_triangles:Number($m("#triangles").value),max_materials:Number($m("#materials").value),allowed_licenses:$m("#licenses").value.split(",").map(s=>s.trim()).filter(Boolean),min_confidence:Number($m("#confidence").value)};
  const reviewRevision=settingsRevision;
  for(const v of chosen){
    const evidenceRevision=v.revision;
    try{
      if(v.visual.value!=="unknown"&&!v.evidence.value.trim())throw new Error("見た目の判断根拠を入力してください。");
      v.result.textContent="詳細を取得して評価中…";
      v.model=await call("detail",{model_id:v.model.id});showMetadata(v);
      const observations=v.evidence.value.trim()?[{kind:"visual",result:v.visual.value,evidence:v.evidence.value}]:[];
      const criteria=Object.keys(referenceAxes).filter(a=>$m("#expected-"+a).value.trim()).map(a=>({axis:a,expected:$m("#expected-"+a).value,observed:v.reference[a].observed.value,basis:v.reference[a].basis.value,result:v.reference[a].result.value,evidence_ids:($m("#reference-source").value.trim()&&$m("#reference-note").value.trim())?["reference-1"]:[]}));
      const sources=criteria.some(c=>c.evidence_ids.length)?[{id:"reference-1",source:$m("#reference-source").value,note:$m("#reference-note").value}]:[];
      const reference_profile=criteria.length?{sources,criteria}:null;
      v.evaluation=await call("evaluate",{model_id:v.model.id,project,brief,policy,observations,reference_profile,evaluator:$m("#evaluator").value});
      if(settingsRevision!==reviewRevision||v.revision!==evidenceRevision){v.evaluation=null;throw new Error("評価中に条件または根拠が変わりました。再評価してください。");}
      const r=v.evaluation;v.result.replaceChildren(el("span",`推奨：${labels[r.proposed_decision]}`,"pill"));
      v.result.append(el("p",`${r.project} — ${r.brief}`));
      const list=el("ul");for(const c of r.checks)list.append(el("li",`${c.name}：${labels[c.result]} — ${c.evidence}`));v.result.append(list);
      if(r.recommendation)v.result.append(el("p",`${r.recommendation.provider} / ${r.recommendation.model}：${labels[r.recommendation.choice]}（確信度 ${r.recommendation.confidence.toFixed(2)}）`));
      for(const [axis,j] of Object.entries(r.recommendation?.reference_judgments||{}))v.result.append(el("p",`${referenceAxes[axis]}：${labels[j.choice]}（確信度 ${j.confidence.toFixed(2)}）`));
      for(const source of r.reference_profile?.sources||[])v.result.append(el("p",`資料 ${source.id}：${source.source} — ${source.note}`));
      if(r.recommendation?.confidence_kind==="uncalibrated_probability")v.result.append(el("p","AnyJevの数値は未校正の選択肢確率です。Jevの確信度とは比較せず、最終判断は保留にしています。"));
      const reason=el("textarea",undefined,"review-reason");reason.placeholder="最終判断の理由（推奨と違う判断も記録できます）";reason.maxLength=3000;reason.setAttribute("aria-label",`${v.model.title}の最終判断理由`);
      const buttons=el("div",undefined,"actions"), status=el("p");status.setAttribute("role","status");
      for(const decision of ["adopt","hold","reject"])buttons.append(button(labels[decision],async()=>{const saved=await call("decide",{evaluation_id:r.id,decision,reason:reason.value});status.textContent=`保存済み：${labels[saved.decision]} — ${saved.reason}`;}));
      v.result.append(reason,buttons,status);
    }catch(e){v.result.textContent=e.message;}
  }
  $m("#message").textContent="評価処理が終わりました。各候補の結果と未確認項目を確認してください。";
}));
$m("#license-accepted").addEventListener("change",()=>{$m("#download-start").disabled=!$m("#license-accepted").checked;});
$m("#download-start").addEventListener("click",async()=>{
  if(!downloadPlan||!$m("#license-accepted").checked)return;
  const b=$m("#download-start");b.disabled=true;$m("#license-accepted").disabled=true;
  $m("#download-message").textContent="ダウンロード中…";
  try{const r=await call("download",{plan_id:downloadPlan.id,approval_nonce:downloadPlan.approval_nonce,accepted_license:downloadPlan.model.license});$m("#download-message").textContent=`保存しました：${r.destination}（${r.files.length}ファイル・帰属情報付き）`;}
  catch(e){$m("#download-message").textContent=e.message+" 取得内容を再確認して再試行してください。";}
  finally{$m("#license-accepted").disabled=false;}
});
async function connectionStatus(){const s=await call("status");$m("#connections").textContent=`Sketchfab：${s.sketchfab_authenticated?"接続済み":s.sketchfab_oauth_configured?"ログイン待ち":"初回設定が必要"}${s.sketchfab_connection?.remembered?"（このPCに保存済み）":""} / Jev：${s.evaluators.jev?"設定済み":"未設定"} / AnyJev：${s.evaluators.anyjev?"設定済み":"未設定"} / 保存先：${s.cache_root}`;
  $m("#oauth-remember").disabled=!s.sketchfab_connection?.remember_available;$m("#oauth-message").textContent=s.sketchfab_connection?.error||"";if(!s.sketchfab_oauth_configured)$m("#oauth-setup").open=true;return s;}
connectionStatus().catch(e=>{$m("#connections").textContent=e.message;});
$m("#oauth-redirect").value=`http://127.0.0.1:${location.port||80}/api/models/oauth_callback`;
async function login(){const r=await call("oauth_start");const u=new URL(r.authorize_url);if(u.origin!=="https://sketchfab.com")throw new Error("ログイン先が不正です");window.location.assign(u.href);}
$m("#sketchfab-login").addEventListener("click",()=>run($m("#sketchfab-login"),async()=>{const s=await connectionStatus();if(!s.sketchfab_oauth_configured){$m("#oauth-setup").open=true;$m("#oauth-client").focus();return;}await login();}));
$m("#oauth-form").addEventListener("submit",e=>{e.preventDefault();run(e.submitter,async()=>{try{await call("oauth_setup",{client_id:$m("#oauth-client").value.trim(),client_secret:$m("#oauth-secret").value.trim(),redirect_uri:$m("#oauth-redirect").value,remember:$m("#oauth-remember").checked});$m("#oauth-message").textContent="設定しました。Sketchfabでログインしてください。";await login();}finally{$m("#oauth-secret").value="";}});});
$m("#sketchfab-disconnect").addEventListener("click",()=>run($m("#sketchfab-disconnect"),async()=>{await call("oauth_disconnect");await connectionStatus();$m("#oauth-message").textContent="このPCの接続設定を解除しました。Sketchfab側の許可は公式アカウント設定で解除できます。";}));
