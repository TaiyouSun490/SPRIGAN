"use strict";

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
let lastResult = null;
let toastTimer = null;
let ragStatusTimer = null;
let requirementCategories = [];
let activeInstallPlan = null;
let activeInstallNonce = "";
let catalogRequestId = 0;
let artStyleCandidate = null;
const STYLE_LABELS = {realistic: "リアル", stylized: "スタイライズ", low_poly: "ローポリ", pixel_art: "ピクセルアート", anime: "アニメ調", hand_painted: "手描き風", cartoon: "カートゥーン"};

function artStyleInfo(candidate) {
  const user = candidate.art_styles?.user || [];
  const hints = candidate.art_styles?.text_hints || [];
  return [user.length ? `画風（自分のタグ）：${user.map(x => STYLE_LABELS[x] || x).join("、")}` : "", hints.length ? `画風の推定：${hints.map(x => STYLE_LABELS[x] || x).join("、")}` : ""].filter(Boolean).join(" / ");
}

function editArtStyle(candidate) {
  artStyleCandidate = candidate;
  $("#art-style-title").textContent = candidate.title;
  $("#art-style-input").value = (candidate.art_styles?.user || []).join(", ");
  $("#art-style-hints").textContent = artStyleInfo(candidate) || "名前などからの画風推定はありません。";
  $("#art-style-message").textContent = "";
  $("#art-style-dialog").showModal();
  $("#art-style-input").focus();
}
const INSTALL_STATUS_LABELS = {
  ready: "導入準備完了",
  manual: "手動導入が必要",
  needs_inspection: "導入可否の検査が必要",
  already_present: "導入済み",
  blocked: "導入できません",
  applied_waiting_for_unity: "適用済み・Unityの解決待ち",
};
const INSTALL_STATUS_COPY = {
  ready: "変更差分とリスクを確認後、導入を実行できます。",
  manual: "自動導入対象外。表示内容に従って手動で導入してください。",
  needs_inspection: "パッケージ定義、導入パス、固定コミットを確認できません。",
  already_present: "対象プロジェクトに導入済みです。",
  blocked: "安全条件を満たしていないため実行できません。",
  applied_waiting_for_unity: "manifest.jsonへ適用済み。Unityの依存解決とコンパイル結果を確認してください。",
};
const SEARCH_LANES = [
  {key: "owned_assets", label: "所有・ローカル"},
  {key: "asset_store_market", label: "未購入候補"},
  {key: "community", label: "GitHub / OpenUPM"},
];

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function safeUrl(value) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.href : "";
  } catch {
    return "";
  }
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: options.body ? {"Content-Type": "application/json"} : {},
    ...options,
  });
  let value;
  try { value = await response.json(); }
  catch { throw new Error("サーバー応答を読み取れませんでした。もう一度お試しください。"); }
  if (!response.ok) {
    const error = new Error(value.error?.message || `HTTP ${response.status}`);
    error.code = value.error?.code;
    throw error;
  }
  return value;
}

function showToast(message) {
  const node = $("#toast");
  node.textContent = message;
  node.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.classList.remove("show"), 2400);
}

function sourceLabel(source) {
  return {local: "LOCAL", github: "GITHUB", openupm: "OPENUPM", asset_store: "ASSET STORE", asset_store_cache: "ASSET CACHE"}[source] || source.toUpperCase();
}

function inventoryLabel(candidate) {
  if (candidate.ownership_evidence?.kind === "unity_editor_my_assets") {
    return "Unity My Assets";
  }
  return {
    confirmed_owned: "自己申告の所有",
    locally_cached: "ローカルキャッシュ",
    project_present: "プロジェクト導入済み",
    unknown: candidate.scope === "asset_store_market" ? "購入未確認" : "",
  }[candidate.inventory_state] || "";
}

function chips(values, target, className = "") {
  values.filter(Boolean).forEach((value) => {
    target.append(element("span", `chip ${className}`.trim(), value));
  });
}

function installActionSpec(candidate) {
  const specs = {
    openupm: {label: "導入内容を確認", disabled: false},
    github: {label: "導入可否を調べる", disabled: false},
    asset_store: {label: "手動導入", disabled: false},
    asset_store_cache: {label: "手動導入", disabled: false},
    local: {label: "導入済み", disabled: true},
  };
  return specs[candidate.source] || {label: "導入可否を調べる", disabled: false};
}

function installActionButton(candidate) {
  const spec = installActionSpec(candidate);
  const button = element("button", "install-action-button", spec.label);
  button.type = "button";
  button.disabled = spec.disabled;
  if (spec.disabled) {
    button.setAttribute("aria-label", `${candidate.title}：導入済み`);
  } else {
    button.addEventListener("click", () => prepareInstall(candidate, button));
  }
  return button;
}

async function validateCachedCandidate(candidate, button, compileTest = false) {
  const projectPath = $("#project-path").value.trim();
  if (!projectPath) {
    showToast("Unity Projectが未指定です");
    return;
  }
  if (compileTest && !window.confirm(
    "一時Unity Projectへパッケージをimportし、コンパイルを検証します。実行しますか？",
  )) return;
  button.disabled = true;
  const original = button.textContent;
  button.textContent = compileTest ? "コンパイル中…" : "検査中…";
  try {
    const result = await api("/api/asset-store/validate", {
      method: "POST",
      body: JSON.stringify({
        candidate_id: candidate.id,
        project_path: projectPath,
        platform: $("#platform").value,
        cache_path: $("#cache-path")?.value.trim() || "",
        compile: compileTest,
      }),
    });
    showToast(`検査結果：${result.validation.overall_status}`);
    await loadCatalog();
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

function validationActionButtons(candidate) {
  const fragment = document.createDocumentFragment();
  if (candidate.source !== "asset_store" || !candidate.metadata?.cache_candidate_id) {
    return fragment;
  }
  const staticButton = element("button", "button secondary compact", "静的検査");
  staticButton.type = "button";
  staticButton.addEventListener("click", () => validateCachedCandidate(
    candidate, staticButton, false,
  ));
  const compileButton = element("button", "button secondary compact", "コンパイル検証");
  compileButton.type = "button";
  compileButton.addEventListener("click", () => validateCachedCandidate(
    candidate, compileButton, true,
  ));
  fragment.append(staticButton, compileButton);
  return fragment;
}

function detailText(value, fallback = "—") {
  if (Array.isArray(value)) return value.filter(Boolean).join(" / ") || fallback;
  if (value && typeof value === "object") return JSON.stringify(value, null, 2);
  if (value === undefined || value === null || value === "") return fallback;
  return String(value);
}

function safeInstallStatus(value) {
  return Object.hasOwn(INSTALL_STATUS_LABELS, value) ? value : "blocked";
}

function renderInstallStatus(status) {
  const safeStatus = safeInstallStatus(status);
  const node = $("#install-status");
  node.className = `install-status status-${safeStatus}`;
  node.textContent = INSTALL_STATUS_LABELS[safeStatus];
  $("#install-state-copy").textContent = INSTALL_STATUS_COPY[safeStatus];
  return safeStatus;
}

function renderInstallPlan(payload) {
  const plan = payload?.plan;
  if (!plan || !plan.id) throw new Error("導入計画の応答が不完全です。");
  activeInstallPlan = plan;
  activeInstallNonce = payload.approval_nonce || "";
  const status = renderInstallStatus(plan.status);
  $("#install-title").textContent = plan.candidate?.title || "導入計画";
  $("#install-kind").textContent = detailText(plan.kind);
  $("#install-target").textContent = detailText(
    [plan.project?.path, plan.project?.unity_version].filter(Boolean),
  );
  $("#install-package").textContent = detailText(plan.package?.name, "対象外");
  $("#install-version").textContent = detailText(plan.package?.version, "対象外");
  $("#install-license").textContent = detailText(plan.candidate?.license, "未確認");
  $("#install-manifest-diff").textContent = detailText(
    plan.manifest?.diff,
    "manifest.json の自動変更はありません。",
  );
  const risks = $("#install-risks");
  risks.replaceChildren();
  const riskItems = Array.isArray(plan.risks) ? plan.risks.filter(Boolean) : [];
  if (riskItems.length) {
    riskItems.forEach((risk) => risks.append(element("li", "", detailText(risk))));
  } else {
    risks.append(element("li", "install-no-risk", "追加のリスクは報告されていません。"));
  }
  $("#install-verification").textContent = detailText(
    plan.verification,
    "Unityを開き、Package Managerの解決結果とコンパイル結果を確認してください。",
  );
  $("#install-rollback").textContent = detailText(
    plan.rollback_limit,
    "適用後にmanifest.jsonが別途変更された場合、自動ロールバックはできません。",
  );
  $("#install-message").textContent = status === "ready" && !activeInstallNonce
    ? "承認情報がないため実行できません。計画を作り直してください。"
    : "";
  $("#install-result").textContent = "";
  $("#install-result-section").classList.add("hidden");
  const confirm = $("#confirm-install-button");
  confirm.hidden = status !== "ready";
  confirm.disabled = status !== "ready" || !activeInstallNonce;
  confirm.textContent = "承認して導入";
  $("#install-dialog").showModal();
}

async function prepareInstall(candidate, button) {
  const projectPath = $("#project-path").value.trim();
  if (!projectPath) {
    showToast("先にUnityプロジェクトのパスを入力してください");
    $("#project-path").focus();
    return;
  }
  const originalLabel = button.textContent;
  button.disabled = true;
  button.textContent = "確認中…";
  try {
    const payload = await api("/api/install/prepare", {
      method: "POST",
      body: JSON.stringify({candidate_id: candidate.id, project_path: projectPath}),
    });
    renderInstallPlan(payload);
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = originalLabel;
  }
}

function closeInstallDialog() {
  $("#install-dialog").close();
}

async function executeInstall(event) {
  event.preventDefault();
  if (!activeInstallPlan || activeInstallPlan.status !== "ready" || !activeInstallNonce) return;
  const confirm = $("#confirm-install-button");
  confirm.disabled = true;
  confirm.textContent = "適用中…";
  $("#install-message").textContent = "承認済みの計画を検証して適用しています。";
  try {
    const payload = await api("/api/install/execute", {
      method: "POST",
      body: JSON.stringify({plan_id: activeInstallPlan.id, approval_nonce: activeInstallNonce}),
    });
    const job = payload?.job || {};
    const status = renderInstallStatus(job.status || "blocked");
    $("#install-message").textContent = INSTALL_STATUS_COPY[status];
    $("#install-result").textContent = detailText(job.result, "結果の詳細はありません。");
    $("#install-result-section").classList.remove("hidden");
    confirm.hidden = true;
    activeInstallNonce = "";
    showToast(status === "applied_waiting_for_unity"
      ? "manifest.jsonへ適用しました。Unityで確認してください"
      : INSTALL_STATUS_LABELS[status]);
  } catch (error) {
    activeInstallNonce = "";
    $("#install-message").textContent = `${error.message} 計画を作り直してください。`;
    confirm.disabled = true;
    confirm.textContent = "計画を作り直してください";
  }
}

function updateStats(summary = {}) {
  $("#stat-total").textContent = summary.total ?? 0;
  $("#stat-owned").textContent = summary.owned_assets ?? 0;
  $("#stat-market").textContent = summary.asset_store_market ?? 0;
  $("#stat-community").textContent = summary.community ?? 0;
  $("#rag-count").textContent = summary.asset_rag_owned ?? 0;
}

function renderRagIndex(index = {}) {
  const labels = {
    empty: "対象アセットなし",
    pending: "索引開始待ち",
    preparing_model: "モデルを準備中",
    building: "索引を作成中",
    ready: "意味検索を利用可能",
    dependency_missing: "必要コンポーネント不足",
    error: "索引作成エラー",
    cancelled: "索引作成を中断",
    disabled: "意味検索は無効",
  };
  const count = `${index.indexed ?? 0} / ${index.documents ?? 0}`;
  const download = index.model_download;
  const modelProgress = download && download.total_bytes
    ? ` · モデル ${Math.round((download.progress || 0) * 100)}%`
    : "";
  const error = index.error ? ` · ${index.error}` : "";
  $("#rag-index-state").textContent = `${labels[index.state] || index.state || "不明"} · ${count}${modelProgress}${error}`;
}

function renderAssetStoreDetails(details = {}) {
  const progress = `${details.enriched ?? 0} / ${details.owned ?? 0}`;
  const activity = details.worker_active
    ? "更新中"
    : details.due
      ? `${details.due}件待機`
      : "最新";
  const unavailable = details.public_page_unavailable
    ? ` · 公開ページなし ${details.public_page_unavailable}件`
    : "";
  const error = details.retryable_errors && details.last_error?.message
    ? ` · 再試行エラー: ${details.last_error.message}`
    : "";
  $("#asset-details-state").textContent = `商品詳細 ${progress} · ${activity}${unavailable}${error}`;
}

async function refreshAssetStoreDetails() {
  const button = $("#refresh-asset-details-button");
  button.disabled = true;
  try {
    const result = await api("/api/asset-store/details/refresh", {
      method: "POST",
      body: JSON.stringify({force: false}),
    });
    renderAssetStoreDetails({
      ...result.asset_store_details,
      worker_active: result.accepted || result.asset_store_details?.fetching > 0,
    });
    showToast(result.accepted ? "商品詳細の差分更新を開始しました" : "商品詳細は更新待ちなしです");
    window.setTimeout(loadStatus, 1000);
  } catch (error) {
    showToast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function loadStatus() {
  try {
    const status = await api("/api/status");
    $("#health").classList.add("ready");
    $("#health span").textContent = `LOCAL · v${status.version}`;
    requirementCategories = Array.isArray(status.requirement_categories)
      ? status.requirement_categories
      : [];
    renderPinCategories();
    updateStats(status.catalog);
    renderRagIndex(status.rag_index);
    renderAssetStoreDetails(status.asset_store_details);
    if (ragStatusTimer) window.clearTimeout(ragStatusTimer);
    if (
      (
        status.rag_index?.automatic
        && ["pending", "preparing_model", "building"].includes(status.rag_index.state)
      )
      || status.asset_store_details?.worker_active
    ) {
      ragStatusTimer = window.setTimeout(loadStatus, 2000);
    }
  } catch {
    $("#health span").textContent = "接続エラー";
  }
}

function renderPinCategories(selected = []) {
  const root = $("#pin-categories");
  if (!root) return;
  const selectedKeys = new Set(selected);
  root.replaceChildren();
  requirementCategories.forEach((category) => {
    const label = element("label", "category-option");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.name = "pin-category";
    input.value = category.key;
    input.checked = selectedKeys.has(category.key);
    label.append(input, element("span", "", category.title));
    root.append(label);
  });
}

function updatePinOwnershipFields() {
  const owned = $("#pin-ownership").value === "owned";
  const fields = $("#pin-rag-fields");
  const alias = $("#pin-user-alias");
  const confirmation = $("#pin-purchase-confirmation");
  fields.classList.toggle("hidden", !owned);
  fields.disabled = !owned;
  confirmation.required = owned;
  $("#pin-submit-button").textContent = owned
    ? "所有索引へ登録"
    : "保存";
  if (!owned) {
    alias.value = "";
    confirmation.checked = false;
  }
}

function openPinDialog(categoryKey = "") {
  $("#pin-form").reset();
  renderPinCategories(categoryKey ? [categoryKey] : []);
  updatePinOwnershipFields();
  const category = requirementCategories.find((item) => item.key === categoryKey);
  $("#pin-category-hint").textContent = category
    ? `「${category.title}」の候補として登録。ほかの要件も選択できます。`
    : "構成候補として使う要件を選択。";
  $("#pin-message").textContent = "";
  $("#pin-dialog").showModal();
  $("#pin-url").focus();
}

function renderProject(project) {
  const box = $("#project-summary");
  box.replaceChildren();
  box.classList.remove("hidden");
  box.append(element("strong", "", project.product_name || project.name));
  box.append(element("div", "", `${project.unity_version || "Unity version不明"} · ${project.render_pipeline.toUpperCase()} · Input: ${project.input_backend}`));
  const chipBox = element("div", "chips");
  chips([`${project.packages.length} packages`, ...project.warnings.map(() => "WARNING")], chipBox);
  box.append(chipBox);
  if (project.warnings.length) box.append(element("div", "risk", project.warnings.join(" / ")));
}

function renderRequirements(result) {
  const root = $("#requirements");
  root.replaceChildren();
  result.requirements.forEach((req) => {
    const card = element("article", "requirement");
    const head = element("header");
    head.append(element("h4", "", req.title));
    head.append(element("span", "priority", req.priority));
    card.append(head, element("p", "", req.rationale));
    root.append(card);
  });
  const exclusions = Object.entries(result.excluded_requirements || {});
  if (exclusions.length) root.append(element("p", "muted", `明示的な除外：${exclusions.map(([key, reason]) => `${key}（${reason}）`).join(" / ")}`));
  if (result.inferred_context?.length) root.append(element("p", "muted", `読み取った舞台の手がかり：${result.inferred_context.map(item => item.label).join(" / ")}（語句による推定）`));
  if (result.inferred_art_styles?.length) root.append(element("p", "muted", `読み取った画風：${result.inferred_art_styles.map(item => item.label).join(" / ")}（語句による推定）`));
}

function renderPlans(result) {
  const root = $("#plans");
  root.replaceChildren();
  const graphLabels = new Map((result.planning_graph?.nodes || []).map(node => [node.id, node.label]));
  result.plans.forEach((plan, index) => {
    const card = element("article", `plan ${index === 0 ? "recommended" : ""}`);
    card.append(element("div", "coverage", `${plan.coverage}%`));
    card.append(element("h4", "", plan.title));
    card.append(element("p", "", plan.description));
    const list = element("div", "stack-items");
    plan.selected.forEach((item) => {
      const row = element("article", "stack-item");
      const heading = element("h5", "", item.candidate.title);
      const meta = element("div", "meta");
      chips([
        sourceLabel(item.candidate.source),
        inventoryLabel(item.candidate),
        ...item.requirement_titles,
      ], meta);
      row.append(assetThumbnail(item.candidate), heading, meta);
      row.append(assetActionButtons(item.candidate));
      if (item.coverage_evidence) {
        (item.covers || []).forEach((key, index) => {
          const evidence = item.coverage_evidence[key];
          if (evidence?.reasons?.length) row.append(element("p", "muted", `${item.requirement_titles[index]}の根拠：${evidence.reasons.join(" / ")}`));
        });
      } else if (item.reasons?.length) row.append(element("p", "muted", `候補の根拠：${item.reasons.join(" / ")}`));
      if (item.risks?.length) row.append(element("p", "risk", `要確認：${item.risks.join(" / ")}`));
      if (Array.isArray(item.usage)) {
        item.usage.forEach((usage) => {
          row.append(element("p", "stack-use", `${usage.role}：${usage.use_case}`));
          if (usage.integration) row.append(element("p", "stack-integration", `組み込み：${usage.integration}`));
        });
      } else {
        row.append(element("p", "stack-use", `用途：${item.requirement_titles.join(" / ")}`));
      }
      list.append(row);
    });
    if (!plan.selected.length) list.append(element("p", "muted", "候補なし"));
    card.append(list);
    if (plan.dependency_review) {
      const details = element("details", "dependency-review");
      details.append(element("summary", "", `依存の根拠（${plan.dependency_review.dependencies.length}件）`));
      if (!plan.dependency_review.dependencies.length) details.append(element("p", "muted", "明示された依存情報はありません。依存がないことの証明ではありません。"));
      plan.dependency_review.dependencies.forEach(edge => {
        const path = (edge.path || [edge.from, edge.to]).map(id => graphLabels.get(id) || id).join(" → ");
        const source = {asset_store_product_metadata: "商品情報", inspected_package_manifest: "検査済みpackage.json", unity_packages_lock: "Unityのpackages-lock.json"}[edge.basis] || edge.basis;
        const version = edge.required_version ? ` / 要求 ${edge.required_version}${edge.resolved_version ? `・解決済み ${edge.resolved_version}` : ""}` : "";
        const status = edge.version_status === "below_minimum" ? "要求版を下回る" : edge.version_status === "satisfied" ? "最低版を満たす" : edge.availability === "installed_in_project" ? "解決・導入情報あり、版は未判定" : "導入・解決未確認";
        const compatibility = edge.dependency_compatibility ? ` / 対象環境：${{compatible: "商品表で対応", incompatible: "非対応", unknown: "未判定", not_evaluated: "未判定"}[edge.dependency_compatibility.status] || "未判定"}` : "";
        details.append(element("p", edge.version_status === "below_minimum" || edge.dependency_compatibility?.status === "incompatible" ? "risk" : "muted", `${path}：${status}（${source}${version}${compatibility}）${edge.cycle ? " / 循環" : ""}`));
      });
      card.append(details);
    }
    if (plan.unresolved_dependencies?.length) {
      const pending = plan.unresolved_dependencies.map(edge => `${(edge.path || [edge.from, edge.to]).map(id => graphLabels.get(id) || id).join(" → ")}（${edge.dependency_compatibility?.status === "incompatible" ? `対象環境で非対応：${(edge.dependency_compatibility.reasons || []).join(" / ")}` : edge.version_status === "below_minimum" ? `必要 ${edge.required_version} / 解決済み ${edge.resolved_version}` : edge.availability === "installed_in_project" && edge.required_version ? "解決済み・版の適合は未判定" : edge.selected_in_plan ? "構成に含む・導入未確認" : edge.availability === "owned_not_confirmed_installed" ? "所有情報あり・導入未確認" : "導入・解決未確認"}）`);
      card.append(element("p", "risk", `依存関係の確認：${pending.join(" / ")}`));
    }
    if (plan.dependency_review?.shared_dependencies?.length) card.append(element("p", "muted", `共通依存：${plan.dependency_review.shared_dependencies.map(item => `${(item.candidates || []).map(id => graphLabels.get(id) || id).join("・")} → ${graphLabels.get(item.dependency) || item.dependency}${item.required_versions.length ? `（要求: ${item.required_versions.join(" / ")}）` : ""}`).join(" / ")}。相互動作は未検証。`));
    if (plan.planning_dependency_issues?.length) card.append(element("p", "risk", "依存の版不足・対象環境の非対応は、構成の比較時に減点しています。更新などで解消できる可能性があります。"));
    if (plan.dependency_review?.truncated) card.append(element("p", "risk", "依存関係の探索上限に到達しました。全件ではありません。"));
    if (plan.missing?.length) card.append(element("p", "risk", `不足：${plan.missing.join(" / ")}`));
    root.append(card);
  });
}

function candidateCard(item, index) {
  const candidate = item.candidate;
  const card = element("article", "candidate");
  card.append(element("div", "rank", String(index + 1).padStart(2, "0")));
  const body = element("div");
  const title = element("h5");
  const url = safeUrl(candidate.url);
  if (url) {
    const link = element("a", "", candidate.title);
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    title.append(link);
  } else {
    title.textContent = candidate.title;
  }
  body.append(assetThumbnail(candidate), title);
  const meta = element("div", "meta");
  chips([
    sourceLabel(candidate.source),
    inventoryLabel(candidate),
    candidate.license || "",
    candidate.version ? `v${candidate.version}` : "",
    candidate.compatibility?.status === "compatible" ? "商品表で対象環境に対応" : "",
  ], meta, candidate.ownership !== "candidate" ? "owned" : "source");
  body.append(meta);
  if (artStyleInfo(candidate)) body.append(element("p", "muted", artStyleInfo(candidate)));
  if (item.reasons?.length) body.append(element("div", "reason", `根拠：${item.reasons.join(" / ")}`));
  if (item.risks?.length) body.append(element("div", "risk", `確認：${item.risks.join(" / ")}`));
  if (item.planning_dependency_issues?.length) {
    const labels = new Map((lastResult?.planning_graph?.nodes || []).map(node => [node.id, node.label]));
    body.append(element("p", "risk", `構成比較で減点：${item.planning_dependency_issues.map(issue => `${(issue.path || []).map(id => labels.get(id) || id).join(" → ")}（${issue.kind === "below_minimum" ? `必要 ${issue.required_version} / 解決済み ${issue.resolved_version}` : "依存先が対象環境に非対応"}）`).join(" / ")}`));
    body.append(element("p", "muted", `適合度 ${Math.round(item.score)} / 依存を考慮 ${Math.round(item.planning_score)}`));
  }
  const actions = element("div", "candidate-actions");
  actions.append(assetActionButtons(candidate));
  actions.append(validationActionButtons(candidate));
  body.append(actions);
  card.append(body, element("div", "score", String(Math.round(item.score))));
  return card;
}

function renderRecommendations(result) {
  const root = $("#recommendations");
  root.replaceChildren();
  result.requirements.forEach((req) => {
    const group = element("section", "recommendation-group");
    group.append(element("h4", "", req.title));
    const options = result.recommendations?.[req.key] || [];
    const list = element("div", "recommendation-list");
    if (!options.length) list.append(element("p", "muted", "候補なし"));
    options.slice(0, 12).forEach((item, index) => list.append(candidateCard(item, index)));
    group.append(list);
    root.append(group);
  });
}

function renderAssetSearches(result) {
  const root = $("#asset-searches");
  root.replaceChildren();
  if (!result.asset_store_searches.length) {
    root.append(element("p", "muted", "未充足の要件はありません。候補の互換性と依存関係は別途確認してください。"));
  }
  result.asset_store_searches.forEach((search) => {
    const link = element("a", "", `${search.title} ↗`);
    link.href = safeUrl(search.url);
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    const save = element("button", "", "候補を追加");
    save.type = "button";
    save.addEventListener("click", () => openPinDialog(search.requirement));
    const row = element("div", "asset-search");
    row.append(link, save);
    root.append(row);
  });
}

function renderResult(result) {
  lastResult = result;
  $("#empty-state").classList.add("hidden");
  $("#results").classList.remove("hidden");
  renderRequirements(result);
  renderPlans(result);
  renderRecommendations(result);
  renderAssetSearches(result);
  if (result.project) renderProject(result.project);
  const states = Object.entries(result.source_status)
    .map(([name, state]) => `${name}: ${state.status}`)
    .join(" · ");
  $("#form-message").textContent = `${result.elapsed_ms} ms · ${states}`;
}

async function scanProject() {
  const path = $("#project-path").value.trim();
  if (!path) {
    $("#form-message").textContent = "Unity Projectが未指定です。";
    return;
  }
  const button = $("#scan-button");
  button.disabled = true;
  try {
    const value = await api("/api/project/scan", {
      method: "POST",
      body: JSON.stringify({path}),
    });
    renderProject(value.project);
    updateStats(value.catalog);
    localStorage.setItem("stackforge.projectPath", path);
    showToast("Unity Projectを解析しました");
  } catch (error) {
    $("#form-message").textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function analyze() {
  const prompt = $("#prompt").value.trim();
  if (!prompt) {
    $("#form-message").textContent = "ゲーム要件が未入力です。";
    $("#prompt").focus();
    return;
  }
  const button = $("#analyze-button");
  button.disabled = true;
  button.querySelector("span").textContent = "生成中…";
  $("#form-message").textContent = "要件、所有、互換性、導入状態を照合中。";
  const preferences = {
    projectPath: $("#project-path").value.trim(),
    platform: $("#platform").value,
    budget: $("#budget").value,
  };
  localStorage.setItem("stackforge.preferences", JSON.stringify(preferences));
  try {
    const result = await api("/api/recommend", {
      method: "POST",
      body: JSON.stringify({
        prompt,
        project_path: preferences.projectPath,
        platform: preferences.platform,
        budget: preferences.budget,
        remote: $("#remote").checked,
      }),
    });
    renderResult(result);
    window.scrollTo({top: $(".workspace").offsetTop - 80, behavior: "smooth"});
  } catch (error) {
    $("#form-message").textContent = error.message;
  } finally {
    button.disabled = false;
    button.querySelector("span").textContent = "構成を生成";
  }
}

function catalogCard(candidate) {
  const card = element("article", "catalog-card");
  const labels = element("div");
  chips([sourceLabel(candidate.source)], labels, "source");
  chips([inventoryLabel(candidate)], labels, "owned");
  if (candidate.metadata?.rag_indexed === true) {
    chips(["購入済みRAG"], labels, "rag");
  }
  chips(candidate.categories || [], labels);
  card.append(assetThumbnail(candidate), labels, element("h3", "", candidate.title));
  if (artStyleInfo(candidate)) card.append(element("p", "muted", artStyleInfo(candidate)));
  card.append(element("p", "", candidate.description || "説明なし"));
  const details = candidate.metadata?.asset_store_details;
  if (details) {
    const price = details.price?.current
      ? `${details.price.currency || ""} ${details.price.current}`.trim()
      : "";
    const rating = details.rating?.average != null
      ? `★${details.rating.average} (${details.rating.review_count || details.rating.count || 0})`
      : "";
    const detailMeta = element("div", "meta");
    chips([
      details.publisher?.name || "",
      details.category?.name || "",
      details.latest_version ? `v${details.latest_version}` : "",
      details.original_unity_version ? `Unity ${details.original_unity_version}+` : "",
      price,
      rating,
    ], detailMeta, "source");
    card.append(detailMeta);
  }
  if (candidate.metadata?.local_validation?.overall_status) {
    chips([
      `検査:${candidate.metadata.local_validation.overall_status}`,
    ], labels, "rag");
  }
  const footer = element("footer");
  const info = element("span", "meta", candidate.license || candidate.version || "");
  footer.append(info);
  const actions = element("div", "catalog-actions");
  actions.append(assetActionButtons(candidate));
  actions.append(validationActionButtons(candidate));
  const artButton = element("button", "", "画風タグを編集");
  artButton.type = "button";
  artButton.addEventListener("click", () => editArtStyle(candidate));
  actions.append(artButton);
  const url = safeUrl(candidate.url);
  if (url) {
    const link = element("a", "", "開く ↗");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    actions.append(link);
  }
  footer.append(actions);
  card.append(footer);
  return card;
}

async function loadCatalog() {
  const requestId = ++catalogRequestId;
  const params = new URLSearchParams({
    q: $("#catalog-query").value.trim(),
    source: $("#catalog-source").value,
    ownership: $("#catalog-ownership").value,
    scope: $("#catalog-scope").value,
    limit: "200",
  });
  try {
    const value = await api(`/api/catalog?${params}`);
    if (requestId !== catalogRequestId) return;
    if (!Array.isArray(value.items)) throw new Error("一覧を取得できませんでした。もう一度お試しください。");
    $("#catalog-count").textContent = value.count;
    const root = $("#catalog-items");
    root.replaceChildren();
    if (!value.items.length) root.append(element("p", "muted", "該当なし"));
    value.items.forEach((item) => root.append(catalogCard(item)));
    updateStats(value.summary);
  } catch (error) {
    if (requestId === catalogRequestId) showToast(error.message);
  }
}

async function scanAssetStoreCache() {
  const button = $("#scan-cache-button");
  const message = $("#cache-message");
  button.disabled = true;
  message.textContent = "ローカルキャッシュを確認中…";
  try {
    const result = await api("/api/asset-store/cache/scan", {
      method: "POST",
      body: JSON.stringify({
        path: $("#cache-path").value.trim(),
        inspect: $("#inspect-cache-packages").checked,
      }),
    });
    const suffix = result.scan.ownership_confirmed ? "" : "（所有未確認）";
    message.textContent = `${result.scan.found}件を取り込み、${result.scan.inspected || 0}件を内容検査しました${suffix}`;
    updateStats(result.catalog);
    await loadCatalog();
    showToast("Unityローカル資産を更新しました");
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

function showView(viewName, {scope = "", load = true} = {}) {
  const safeView = viewName === "catalog" ? "catalog" : "planner";
  $$(".tab").forEach((item) => item.classList.toggle(
    "active",
    item.dataset.view === safeView,
  ));
  $$(".view").forEach((view) => view.classList.remove("active"));
  $(`#${safeView}-view`).classList.add("active");
  if (safeView === "catalog") {
    $("#catalog-scope").value = SEARCH_LANES.some((lane) => lane.key === scope)
      ? scope
      : "";
    if (load) loadCatalog();
  }
}

async function syncUnityMyAssets() {
  const button = $("#sync-my-assets-button");
  const message = $("#my-assets-message");
  button.disabled = true;
  message.textContent = "Unity Editorの同期ファイルを確認中…";
  try {
    const result = await api("/api/asset-store/my-assets/sync", {
      method: "POST",
      body: JSON.stringify({path: $("#my-assets-path").value.trim()}),
    });
    message.textContent = result.sync.changed
      ? `${result.sync.imported}件を所有アセットとして同期しました`
      : `変更はありません（所有アセット ${result.sync.total}件）`;
    updateStats(result.catalog);
    await loadCatalog();
    const url = new URL(window.location.href);
    url.searchParams.set("view", "catalog");
    url.searchParams.set("scope", "owned_assets");
    url.searchParams.delete("sync");
    window.history.replaceState({}, "", url);
    showToast(result.sync.changed ? "Unity My Assetsを同期しました" : "所有一覧は最新です");
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function pinAsset(event) {
  event.preventDefault();
  const message = $("#pin-message");
  const ownership = $("#pin-ownership").value;
  const owned = ownership === "owned";
  const purchaseConfirmation = $("#pin-purchase-confirmation");
  const userAlias = $("#pin-user-alias").value;
  const notes = $("#pin-notes").value;
  const categories = $$("#pin-categories input:checked").map((input) => input.value);
  if (owned && !userAlias.trim() && !notes.trim() && !categories.length) {
    message.textContent = "AI用の別名、メモ、機能カテゴリのいずれかを入力してください。";
    $("#pin-user-alias").focus();
    return;
  }
  if (owned && !purchaseConfirmation.checked) {
    message.textContent = "購入済みであることを自己申告で確認してください。";
    purchaseConfirmation.focus();
    return;
  }
  message.textContent = "保存中…";
  try {
    const payload = {
      url: $("#pin-url").value,
      title: $("#pin-title").value,
      notes,
      categories,
    };
    if (owned) {
      payload.user_alias = userAlias;
      payload.purchase_confirmation = true;
    } else {
      payload.ownership = ownership;
    }
    const result = await api(owned ? "/api/asset-store/rag" : "/api/catalog/manual", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    updateStats(result.catalog);
    $("#pin-dialog").close();
    $("#pin-form").reset();
    message.textContent = "";
    await loadCatalog();
    showToast(owned
      ? "購入済みアセットをRAGへ登録しました"
      : "Asset Store商品を保存しました");
  } catch (error) {
    message.textContent = error.message;
  }
}

function downloadResult() {
  if (!lastResult) return;
  const blob = new Blob([JSON.stringify(lastResult, null, 2)], {type: "application/json"});
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `game-stack-plan-${lastResult.run_id || "draft"}.json`;
  link.click();
  URL.revokeObjectURL(url);
}

function restorePreferences() {
  $("#project-path").value = localStorage.getItem("stackforge.projectPath") || "";
  try {
    const value = JSON.parse(localStorage.getItem("stackforge.preferences") || "{}");
    if (value.projectPath) $("#project-path").value = value.projectPath;
    if (value.platform) $("#platform").value = value.platform;
    if (value.budget) $("#budget").value = value.budget;
  } catch {
    localStorage.removeItem("stackforge.preferences");
  }
}

function bind() {
  const closeArtStyle = () => $("#art-style-dialog").close();
  $("#close-art-style").addEventListener("click", closeArtStyle);
  $("#cancel-art-style").addEventListener("click", closeArtStyle);
  $("#art-style-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = event.submitter;
    button.disabled = true;
    try {
      const styles = $("#art-style-input").value.split(/[,、]/).map(s => s.trim()).filter(Boolean);
      await api("/api/catalog/art-styles", {method: "POST", body: JSON.stringify({candidate_id: artStyleCandidate.id, styles})});
      closeArtStyle();
      await loadCatalog();
      showToast("画風タグを保存しました。構成を再生成すると反映されます。");
    } catch (error) { $("#art-style-message").textContent = error.message; }
    finally { button.disabled = false; }
  });
  $("#show-asset-images").checked = localStorage.getItem("fafnir.showAssetImages") !== "false";
  $("#show-asset-images").addEventListener("change", () => {
    localStorage.setItem("fafnir.showAssetImages", String($("#show-asset-images").checked));
    if (lastResult) renderResult(lastResult);
    if ($("#catalog-view").classList.contains("active")) loadCatalog();
  });
  $$(".tab").forEach((tab) => tab.addEventListener("click", () => {
    showView(tab.dataset.view);
  }));
  $$(".examples button").forEach((button) => button.addEventListener("click", () => {
    $("#prompt").value = button.dataset.example;
    $("#prompt").focus();
  }));
  $("#scan-button").addEventListener("click", scanProject);
  $("#analyze-button").addEventListener("click", analyze);
  $("#catalog-search-button").addEventListener("click", loadCatalog);
  $("#scan-cache-button").addEventListener("click", scanAssetStoreCache);
  $("#sync-my-assets-button").addEventListener("click", syncUnityMyAssets);
  $("#refresh-asset-details-button").addEventListener("click", refreshAssetStoreDetails);
  $("#catalog-query").addEventListener("keydown", (event) => {
    if (event.key === "Enter") loadCatalog();
  });
  $("#open-pin-button").addEventListener("click", () => openPinDialog());
  $("#close-pin-button").addEventListener("click", () => $("#pin-dialog").close());
  $("#pin-ownership").addEventListener("change", updatePinOwnershipFields);
  $("#pin-form").addEventListener("submit", pinAsset);
  $("#close-install-button").addEventListener("click", closeInstallDialog);
  $("#cancel-install-button").addEventListener("click", closeInstallDialog);
  $("#install-form").addEventListener("submit", executeInstall);
  $("#install-dialog").addEventListener("close", () => {
    activeInstallPlan = null;
    activeInstallNonce = "";
  });
  $("#download-button").addEventListener("click", downloadResult);
  $("#copy-button").addEventListener("click", async () => {
    if (!lastResult) return;
    await navigator.clipboard.writeText(JSON.stringify(lastResult, null, 2));
    showToast("JSONをコピーしました");
  });
}

restorePreferences();
bind();
const initialRoute = new URLSearchParams(window.location.search);
const initialView = initialRoute.get("view") || "planner";
const initialScope = initialRoute.get("scope") || "";
showView(initialView, {scope: initialScope, load: false});
loadStatus().then(async () => {
  if (initialView !== "catalog") return;
  if (initialRoute.get("sync") === "my-assets") {
    await syncUnityMyAssets();
  } else {
    await loadCatalog();
  }
});
