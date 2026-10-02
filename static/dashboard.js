'use strict';
const $ = id => document.getElementById(id);
const {filterSites, refId, selectMatching, suggestionKey, suggestedSelection} = window.SiteLibrary;
const subUrl = document.body.dataset.subUrl;
let sites = [], sources = [], groups = [], suggestions = [];
let currentGroup = 'all', currentView = 'library', page = 1, selected = new Set();
let busyWrite = false, modelConfigured = false, probeJob = null, analysisJob = null, suggestionRequest = 0;
let draggedIds = [], reviewDraft = {};
const draftStorageKey = `tvbox-review:${subUrl}`;
try { reviewDraft = JSON.parse(sessionStorage.getItem(draftStorageKey) || '{}'); } catch { reviewDraft = {}; }
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const ref = site => ({source_id:site.source_id, key:site.key});
const groupValue = site => site.group_id == null ? 'unclassified' : String(site.group_id);
const groupName = id => id == null || id === 'unclassified' ? '未分类' : (groups.find(g => String(g.id) === String(id))?.name || '已删除分组');
const scopeName = () => currentGroup === 'all' ? '全部站点' : groupName(currentGroup);
const inGroup = site => currentGroup === 'all' || groupValue(site) === currentGroup;
const pct = value => value == null ? '—' : `${Math.round(Number(value) * 100)}%`;
function when(value) { if (!value) return '未探测'; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', {hour12:false}); }
function toast(message) { $('toast').textContent = message; $('toast').classList.add('show'); clearTimeout(toast.timer); toast.timer = setTimeout(() => $('toast').classList.remove('show'), 4500); }
function showError(message) { $('pageError').textContent = message; $('pageError').hidden = !message; }
async function api(url, body) {
  const response = await fetch(url, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (response.redirected && response.url.includes('/login')) { location.href = '/login'; throw new Error('登录已过期，请重新登录'); }
  let data;
  try { data = await response.json(); } catch { throw new Error(`服务返回了无法读取的内容 (${response.status})`); }
  if (!response.ok || data.status !== 'success') throw new Error(data.message || `请求失败 (${response.status})`);
  return data;
}
function ask(title, message, targets = []) {
  const dialog = $('confirmDialog');
  $('confirmTitle').textContent = title; $('confirmMessage').textContent = message;
  $('confirmNames').innerHTML = targets.slice(0, 6).map(s => `<li>${esc(s.name)}${s.source_name ? ` <small>· ${esc(s.source_name)}</small>` : ''}</li>`).join('') + (targets.length > 6 ? `<li>另有 ${targets.length - 6} 个站点</li>` : '');
  $('confirmApply').textContent = title; dialog.returnValue = 'cancel';
  return new Promise(resolve => { dialog.addEventListener('close', () => resolve(dialog.returnValue === 'confirm'), {once:true}); dialog.showModal(); });
}
function filters() { return {group:currentGroup, query:$('siteSearch').value, source:$('sourceFilter').value, type:$('typeFilter').value, enabled:$('enabledFilter').value, status:$('statusFilter').value, sort:$('sortSites').value}; }
function visibleSites() {
  const shown = filterSites(sites, filters());
  if (probeJob) shown.sort((a,b) => (probeJob.order.get(refId(a)) ?? Infinity) - (probeJob.order.get(refId(b)) ?? Infinity));
  return shown;
}
function pageSites(shown = visibleSites()) { const size = Number($('pageSize').value); return shown.slice((page-1)*size, page*size); }
function selectedSites() { return visibleSites().filter(s => selected.has(refId(s))); }
function options(value, blank = false) { return (blank ? '<option value="">移到分组…</option>' : '') + `<option value="unclassified" ${value === 'unclassified' ? 'selected' : ''}>未分类</option>` + groups.map(g => `<option value="${g.id}" ${String(g.id) === String(value) ? 'selected' : ''}>${esc(g.name)}</option>`).join(''); }
function probeLabel(result) {
  if (!result) return ['未探测',''];
  if (result.status === 'online') return [result.playback === 'ok' ? '播放首段可达' : '搜索验证通过','online'];
  if (result.status === 'no_match') return ['可达 · 无搜索匹配','no_match'];
  if (result.status === 'resource_only') return ['资源可达','resource'];
  if (result.status === 'unsupported') return ['需电视验证','unsupported'];
  if (result.status === 'needs_recheck') return ['旧结果待重测','needs_recheck'];
  if (result.status === 'failed') return [`${({search:'搜索',detail:'详情',playback:'播放',resource:'资源访问'})[result.stage] || '请求'}失败`,'failed'];
  return ['结果待确认',''];
}
function latency(site) {
  const result = site.result || {};
  if (result.search_ms != null) return `<span class="metric">${esc(result.search_ms)} <small>ms</small></span><small class="site-subtitle">搜索响应</small>`;
  if (result.resource_ms != null) return `<span class="metric">${esc(result.resource_ms)} <small>ms</small></span><small class="site-subtitle">资源响应</small>`;
  return '<span class="muted">—</span>';
}
function renderGroups() {
  const items = [{id:'all',name:'全部站点'}, {id:'unclassified',name:'未分类'}, ...groups.map(g => ({id:String(g.id),name:g.name}))];
  $('groupNav').innerHTML = items.map(g => {
    const members = sites.filter(s => g.id === 'all' || groupValue(s) === g.id);
    return `<div class="group-item ${currentGroup === g.id ? 'active' : ''}" data-group="${g.id}"><button class="group-target" type="button" data-group="${g.id}" ${currentGroup === g.id ? 'aria-current="true"' : ''}><span>${esc(g.name)}</span><b>${members.filter(s=>s.enabled).length} / ${members.length}</b></button>${!['all','unclassified'].includes(g.id) ? `<button class="group-edit" type="button" data-edit="${g.id}" aria-label="编辑 ${esc(g.name)}">···</button>` : ''}</div>`;
  }).join('');
  $('librarySummary').textContent = `${sites.filter(s=>s.enabled).length} 个已启用 / ${sites.length} 个站点`;
}
function renderLocks() {
  const count = selectedSites().length, hasSites = visibleSites().length > 0;
  for (const id of ['selectAllEnable','selectAllDisable']) $(id).disabled = busyWrite || !hasSites;
  for (const id of ['enableSelected','disableSelected']) $(id).disabled = busyWrite || !count;
  $('moveSelected').disabled = busyWrite || !count || !$('moveTarget').value;
  $('probeSelected').disabled = !!probeJob || !count || busyWrite;
  $('probeFiltered').disabled = !!probeJob || !hasSites || busyWrite;
  $('analyzeGroup').disabled = !!analysisJob || busyWrite || !modelConfigured || currentGroup === 'all' || !sites.some(inGroup);
  for (const id of ['refreshAllSources','saveSource','addGroup']) $(id).disabled = busyWrite || !!probeJob || !!analysisJob;
  document.querySelectorAll('.site-toggle').forEach(el => { el.disabled = busyWrite; });
  document.querySelectorAll('.row-group-select,.group-edit,.apply-suggestions,#editGroupForm button[type=submit],#deleteGroup,#modelForm button[type=submit]').forEach(el => { el.disabled = busyWrite || !!analysisJob; });
  document.querySelectorAll('.source-actions button').forEach(el => { el.disabled = busyWrite || !!probeJob || !!analysisJob; });
  $('moveSelected').disabled = $('moveSelected').disabled || !!analysisJob;
  document.querySelectorAll('[data-probe]').forEach(el => { el.disabled = busyWrite || !!probeJob; });
}
function renderSelection() {
  const shown = visibleSites(), allowed = new Set(shown.map(refId));
  selected = new Set([...selected].filter(id => allowed.has(id)));
  $('selectionSummary').textContent = `已选 ${selected.size} 个`;
  $('selectFiltered').textContent = `选择全部筛选结果 (${shown.length})`;
  const slice = pageSites(shown), count = slice.filter(s=>selected.has(refId(s))).length;
  $('selectPage').checked = slice.length > 0 && count === slice.length;
  $('selectPage').indeterminate = count > 0 && count < slice.length;
  $('selectPage').disabled = !slice.length;
  renderLocks();
}
function renderSites() {
  const shown = visibleSites(), size = Number($('pageSize').value), pages = Math.max(1, Math.ceil(shown.length / size));
  page = Math.max(1, Math.min(page, pages));
  const members = sites.filter(inGroup);
  $('currentGroupTitle').textContent = scopeName();
  $('scopeSummary').textContent = `分组内 ${members.length} 个 · 筛选后 ${shown.length} 个 · 其中已启用 ${shown.filter(s=>s.enabled).length} 个`;
  $('scopeNote').textContent = `全选启用、全选停用只影响「${scopeName()}」当前筛选结果的 ${shown.length} 个站点，包含其他分页。`;
  $('siteRows').innerHTML = pageSites(shown).map(site => {
    const id = refId(site), [label,state] = probeLabel(site.result), pending = probeJob?.active.has(id);
    const type = [0,1,4].includes(Number(site.type)) ? 'CMS' : Number(site.type) === 3 ? 'Spider' : `类型 ${site.type ?? '?'}`;
    return `<tr class="site-row ${selected.has(id) ? 'selected' : ''}" data-id="${esc(id)}" draggable="true"><td class="check-cell"><input class="site-select" type="checkbox" aria-label="选择 ${esc(site.name)}" ${selected.has(id) ? 'checked' : ''}></td><td data-label="站点"><span class="site-title">${esc(site.name)} <span class="type-badge">${esc(type)}</span></span><small class="site-subtitle" title="${esc(site.key)}">${esc(site.source_name)} · ${esc(site.key)}</small></td><td data-label="分组"><select class="row-group-select" aria-label="${esc(site.name)}所属分组">${options(groupValue(site))}</select></td><td data-label="探测"><span class="status ${state}">${pending ? '探测中…' : esc(label)}</span><small class="site-subtitle">${esc(when(site.checked_at))}</small></td><td data-label="响应">${latency(site)}</td><td data-label="启用"><button type="button" class="site-toggle" role="switch" aria-label="启用 ${esc(site.name)}" aria-checked="${site.enabled}">${site.enabled ? '已启用' : '已停用'}</button></td><td data-label="操作"><div class="row-actions"><button type="button" class="text-button" data-probe>探测</button><button type="button" class="text-button" data-details>详情</button></div></td></tr>`;
  }).join('') || '<tr><td colspan="7" class="empty">没有符合条件的站点。可重置筛选，或到配置来源导入。</td></tr>';
  $('shownCount').textContent = shown.length ? `${(page-1)*size+1}–${Math.min(page*size,shown.length)} / ${shown.length} 个` : '0 个站点';
  $('pageNumber').textContent = `${page} / ${pages}`; $('previousPage').disabled = page === 1; $('nextPage').disabled = page === pages;
  renderGroups(); renderSelection();
}
function setView(view) {
  currentView = view;
  $('managementLayout').hidden = !['library','review'].includes(view);
  for (const name of ['library','review','sources','settings']) $(`${name}View`).hidden = name !== view;
  document.querySelectorAll('[data-view]').forEach(button => { button.classList.toggle('active', button.dataset.view === view); if (button.dataset.view === view) button.setAttribute('aria-current','page'); else button.removeAttribute('aria-current'); });
  if (view === 'review') loadSuggestions();
}
async function loadLibrary() {
  groups = (await api('/api/group/list')).data;
  const data = await api('/api/site/list'); sites = data.data.map(site => {
    const result = site.result;
    if (result?.status === 'failed' && result.stage === 'search' && result.search_ms != null && !result.error && !result.keyword) {
      return {...site, result:{...result,status:'needs_recheck',message:'旧版未区分空搜索和无效响应，请重新探测'}};
    }
    return site;
  });
  $('siteErrors').textContent = (data.errors || []).map(e=>`${e.source}：${e.message}`).join('；'); $('siteErrors').hidden = !(data.errors || []).length;
  if (currentGroup !== 'all' && currentGroup !== 'unclassified' && !groups.some(g=>String(g.id) === currentGroup)) currentGroup = 'unclassified';
  const move = $('moveTarget').value; $('moveTarget').innerHTML = options(move,true);
  renderSites();
}
async function reload() { await loadLibrary(); await loadSources(); await loadSuggestions(); }
async function mutation(task, confirmation) {
  if (busyWrite) return;
  busyWrite = true; renderLocks();
  try {
    if (confirmation && !await ask(...confirmation)) return;
    showError(''); await task();
  } catch (error) { renderSites(); showError(error.message); toast(error.message); }
  finally { busyWrite = false; renderLocks(); }
}
async function setEnabled(targets, enabled, confirm = true) {
  const changes = targets.filter(s => s.enabled !== enabled);
  if (!changes.length) { toast(`当前范围已全部${enabled ? '启用' : '停用'}`); return; }
  const title = enabled ? '启用站点' : '停用站点';
  await mutation(async () => {
    await api('/api/site/batch_enable', {enabled, sites:changes.map(ref)});
    for (const site of changes) site.enabled = enabled;
    renderSites();
    try { await loadLibrary(); await loadSources(); } catch(error) { throw new Error(`启用状态已保存，但刷新数据失败：${error.message}`); }
    toast(`已${enabled ? '启用' : '停用'} ${changes.length} 个站点`);
  }, confirm ? [title, `将改变 ${changes.length} 个站点。电视下次刷新订阅时生效。`, changes] : null);
}
async function moveSites(targets, target, confirm = true) {
  if (analysisJob) { toast('请等待当前模型分析完成后再调整分组'); return; }
  const changes = targets.filter(s=>groupValue(s) !== target); if (!changes.length) { toast('所选站点已在目标分组'); return; }
  await mutation(async () => {
    await api('/api/group/assign', {group_id:target === 'unclassified' ? null : Number(target), sites:changes.map(ref)});
    for (const site of changes) site.group_id = target === 'unclassified' ? null : Number(target);
    renderSites();
    try { await loadLibrary(); await loadSuggestions(); } catch(error) { throw new Error(`分组已保存，但刷新数据失败：${error.message}`); }
    toast(`已将 ${changes.length} 个站点移到「${groupName(target)}」`);
  }, confirm ? ['移动站点', `将 ${changes.length} 个站点移到「${groupName(target)}」，启用状态保持原样。`, changes] : null);
}
function updateProbeJob() {
  if (!probeJob) return;
  const job = probeJob;
  $('jobStatus').hidden = false;
  $('jobMessage').textContent = `${job.scope} · ${job.cancelled ? '正在停止' : '正在探测'} ${job.done}/${job.total} · 已验证 ${job.online} · 无匹配 ${job.no_match} · 资源可达 ${job.resource_only} · 失败 ${job.failed} · 需电视验证 ${job.unsupported}`;
  $('stopJob').disabled = job.cancelled;
}
async function probeSites(targets) {
  if (probeJob || !targets.length) return;
  const jobs = [...targets], keyword = $('probeKeyword').value.trim() || '庆余年';
  const job = {scope:`${scopeName()} / ${jobs.length} 个站点`,total:jobs.length,done:0,cancelled:false,online:0,no_match:0,resource_only:0,failed:0,unsupported:0, errors:[], active:new Set(), order:new Map(visibleSites().map((s,i)=>[refId(s),i]))};
  probeJob = job; updateProbeJob(); renderLocks(); let next = 0;
  const worker = async () => {
    while (!job.cancelled && next < jobs.length) {
      const original = jobs[next++], id = refId(original); job.active.add(id);
      try {
        const result = (await api('/api/site/probe', {...ref(original),keyword})).data;
        const site = sites.find(s=>refId(s) === id); if (site) { site.result = result; site.checked_at = new Date().toISOString(); }
        if (Object.hasOwn(job,result.status)) job[result.status]++;
      } catch(error) { job.failed++; job.errors.push(`${original.name}：${error.message}`); }
      finally { job.active.delete(id); job.done++; updateProbeJob(); renderSites(); }
    }
  };
  try { await Promise.all(Array.from({length:Math.min(4,jobs.length)},worker)); }
  finally {
    probeJob = null; renderSites(); $('stopJob').disabled = true;
    $('jobMessage').textContent = `${job.cancelled ? '已停止' : '探测完成'} · 已处理 ${job.done}/${job.total} 个 · 失败 ${job.failed} · 无匹配 ${job.no_match} · 需电视验证 ${job.unsupported}`;
    if (job.errors.length) showError(job.errors.slice(0,5).join('；'));
    renderLocks();
  }
}
function saveDraft() { try { sessionStorage.setItem(draftStorageKey,JSON.stringify(reviewDraft)); } catch { toast('浏览器无法保存审核勾选，离开页面前请完成确认'); } }
function currentSuggestions() { return suggestions.filter(s => sites.some(site=>site.source_id === s.source_id && site.key === s.site_key && groupValue(site) === currentGroup)); }
function renderSuggestions() {
  const unclassified = currentGroup === 'unclassified';
  $('aiTitle').textContent = currentGroup === 'all' ? '分类审核' : unclassified ? '未分类 · 分拣建议' : `${scopeName()} · 归属审核`;
  $('reviewScope').textContent = currentGroup === 'all' ? '从左侧选择一个分组或未分类。' : `${sites.filter(inGroup).length} 个站点 · 模型仅提供建议`;
  $('reviewRule').textContent = unclassified ? '预选达到基线的归类建议' : '预选低于基线的站点，移回未分类';
  $('modelNotice').hidden = modelConfigured;
  const current = currentSuggestions();
  $('preselectSuggestions').disabled = !current.length; $('clearSuggestions').disabled = !current.length;
  if (currentGroup === 'all') { $('suggestionRows').innerHTML = '<p class="empty">选择一个分组审核归属，或进入“未分类”分拣新站点。</p>'; renderLocks(); return; }
  if (!current.length) { $('suggestionRows').innerHTML = '<p class="empty">暂无分类建议。接入模型后，点击“分析当前分组”。</p>'; renderLocks(); return; }
  const buckets = new Map();
  for (const s of current) { const target = unclassified ? (s.suggested_group_id == null ? 'unclassified' : String(s.suggested_group_id)) : 'unclassified'; if (!buckets.has(target)) buckets.set(target,[]); buckets.get(target).push(s); }
  $('suggestionRows').innerHTML = [...buckets].map(([target,entries]) => {
    const movable = !(unclassified && target === 'unclassified'), picked = entries.filter(s=>reviewDraft[suggestionKey(s)] === true).length;
    return `<section class="suggestion-bucket" data-target="${target}"><div class="bucket-head"><div><strong>${unclassified ? `建议归入：${esc(groupName(target))}` : '移回未分类'}</strong><small>${entries.length} 个建议 · 已选 ${picked} 个</small></div><div class="bucket-actions">${movable ? '<button type="button" class="text-button" data-review-select="all">全选</button><button type="button" class="text-button" data-review-select="none">取消全选</button><button type="button" class="button primary apply-suggestions">确认移动所选</button>' : '<span class="muted">暂留未分类</span>'}</div></div>${entries.map(s => {
      const site = sites.find(v=>v.source_id === s.source_id && v.key === s.site_key), key = suggestionKey(s);
      return `<div class="suggestion-row" data-key="${esc(key)}"><label><input class="review-check" type="checkbox" ${reviewDraft[key] === true ? 'checked' : ''} ${movable ? '' : 'disabled'}><span><strong>${esc(site.name)}</strong><small>${esc(site.source_name)} · ${esc(when(s.created_at))}</small></span></label><div class="suggestion-score">${unclassified ? `归类置信度 ${pct(s.confidence)}` : `属于本组 ${pct(s.membership)} · 建议 ${esc(groupName(s.suggested_group_id))}`}<details><summary>各分组概率</summary><div class="probabilities">${Object.entries(s.probabilities || {}).sort((a,b)=>b[1]-a[1]).map(([id,value])=>`<span>${esc(id === 'unclassified' ? '未分类' : groupName(Number(id.slice(2))))} <b>${pct(value)}</b></span>`).join('')}</div></details></div></div>`;
    }).join('')}</section>`;
  }).join('');
  renderLocks();
}
async function loadSuggestions() {
  const ticket = ++suggestionRequest, group = currentGroup;
  if (group === 'all') { suggestions = []; renderSuggestions(); return; }
  try { const data = await api(`/api/group/suggestions?group_id=${encodeURIComponent(group)}`); if (ticket !== suggestionRequest || group !== currentGroup) return; suggestions = data.data; renderSuggestions(); }
  catch(error) { if (ticket === suggestionRequest) { suggestions = []; renderSuggestions(); showError(error.message); } }
}
async function analyzeGroup() {
  if (analysisJob || !modelConfigured || currentGroup === 'all') return;
  const group = currentGroup, name = scopeName(), jobs = sites.filter(inGroup);
  if (!jobs.length) return;
  analysisJob = {group,name}; renderLocks();
  let started = false, done = 0, errors = [];
  try {
    if (!await ask('开始模型分析', `将「${name}」的 ${jobs.length} 个站点名称、Key、来源、类型和分组说明发送到已配置的模型服务，可能产生 API 费用。建议需你确认后才会应用。`)) return;
    started = true;
    for (let i=0;i<jobs.length;i+=25) {
      $('analysisProgress').textContent = `正在分析「${name}」 ${done}/${jobs.length}`;
      const batch = jobs.slice(i,i+25), result = await api('/api/group/analyze',{group_id:group === 'unclassified' ? null : Number(group),sites:batch.map(ref)});
      done += batch.length; errors.push(...(result.errors || []));
      $('analysisProgress').textContent = `「${name}」已处理 ${done}/${jobs.length} · ${errors.length} 个失败`;
    }
    if (errors.length) showError(errors.slice(0,5).map(e=>`${e.key}：${e.message}`).join('；'));
    await loadSuggestions(); toast(errors.length ? `部分分析失败：${errors.length} 个，已保留成功建议` : '建议已生成。点击“按基线预选”后逐项审核。');
  } catch(error) { showError(error.message); $('analysisProgress').textContent = `「${name}」分析中断 · 已处理 ${done}/${jobs.length}，已生成的建议仍可审核`; }
  finally { analysisJob = null; if (started) await loadSuggestions(); renderLocks(); }
}
async function loadModelSettings() {
  const data = (await api('/api/model/settings')).data; modelConfigured = data.configured;
  $('modelProvider').value = data.provider; $('modelName').value = data.model;
  $('modelStatus').textContent = data.configured ? `${data.provider === 'openrouter' ? 'OpenRouter' : 'TypeSafe'} · ${data.model} · 已保存接入信息` : '尚未接入模型'; renderSuggestions();
}
function renderSources() {
  const old = $('sourceFilter').value;
  $('sourceFilter').innerHTML = '<option value="all">全部来源</option>' + sources.filter(s=>s.type === 'site').map(s=>`<option value="${s.id}">${esc(s.name)}</option>`).join('');
  $('sourceFilter').value = sources.some(s=>s.type === 'site' && String(s.id) === old) ? old : 'all';
  $('sourceRows').innerHTML = sources.map(source=>`<div class="source-row" data-id="${source.id}"><div class="source-meta"><strong>${esc(source.name)} <span class="type-badge">${source.type === 'site' ? 'TVBox 配置' : '直播地址'}</span></strong><small>${esc(source.url)}</small><small>${source.type === 'site' ? `${source.site_count ?? 0} 个站点 · 已启用 ${source.enabled_count ?? 0} 个 · 更新于 ${esc(when(source.fetched_at))}` : '直播内容随整合订阅输出'}</small></div><div class="source-actions">${source.type === 'site' ? '<button class="button" type="button" data-action="refresh">更新</button>' : ''}<button class="button" type="button" data-action="edit">编辑</button><button class="button danger" type="button" data-action="delete">移除</button></div></div>`).join('') || '<p class="empty">还没有配置来源。在上方输入地址导入。</p>';
  renderSites();
}
async function loadSources() { sources = (await api('/api/source/list')).data; renderSources(); }
function resetSourceForm() { $('sourceForm').reset(); $('sourceId').value = ''; $('cancelEdit').hidden = true; $('saveSource').textContent = '导入配置'; }
function changeSummary(data) { const c = data.changes; return c ? `新增 ${c.added} · 移除 ${c.removed} · 变更 ${c.changed} · 保留 ${c.unchanged}` : data.message; }
function editGroup(id) {
  const group = groups.find(g=>String(g.id) === String(id));
  $('groupDialogTitle').textContent = group ? '编辑分组' : '新建分组'; $('editGroupId').value = group?.id ?? '';
  $('editGroupName').value = group?.name ?? ''; $('editGroupDescription').value = group?.description ?? ''; $('deleteGroup').hidden = !group; $('groupDialog').showModal();
}
async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const area = document.createElement('textarea'); area.value = text; area.style.position = 'fixed'; area.style.left = '-9999px'; document.body.append(area); area.select(); const ok = document.execCommand('copy'); area.remove(); if (!ok) throw new Error('复制失败，请手动复制订阅地址');
}
function filterChanged() { selected.clear(); page = 1; renderSites(); }
for (const id of ['siteSearch','sourceFilter','typeFilter','enabledFilter','statusFilter']) $(id).addEventListener(id === 'siteSearch' ? 'input' : 'change',filterChanged);
$('sortSites').addEventListener('change',()=>{page=1;renderSites();});
$('clearFilters').addEventListener('click',()=>{ $('siteSearch').value=''; for(const id of ['sourceFilter','typeFilter','enabledFilter','statusFilter']) $(id).value='all'; filterChanged(); });
$('selectFiltered').addEventListener('click',()=>{ selected=new Set(visibleSites().map(refId)); renderSites(); });
$('clearSelection').addEventListener('click',()=>{selected.clear();renderSites();});
$('quickSelect').addEventListener('change',event=>{selected=new Set(selectMatching(visibleSites(),event.target.value));event.target.value='';renderSites();});
$('selectPage').addEventListener('change',event=>{for(const site of pageSites()) event.target.checked ? selected.add(refId(site)) : selected.delete(refId(site));renderSites();});
$('selectAllEnable').addEventListener('click',()=>setEnabled(visibleSites(),true));
$('selectAllDisable').addEventListener('click',()=>setEnabled(visibleSites(),false));
$('enableSelected').addEventListener('click',()=>setEnabled(selectedSites(),true));
$('disableSelected').addEventListener('click',()=>setEnabled(selectedSites(),false));
$('moveTarget').addEventListener('change',renderLocks);
$('moveSelected').addEventListener('click',()=>moveSites(selectedSites(),$('moveTarget').value));
$('probeSelected').addEventListener('click',()=>probeSites(selectedSites()));
$('probeFiltered').addEventListener('click',()=>probeSites(visibleSites()));
$('stopJob').addEventListener('click',()=>{if(probeJob){probeJob.cancelled=true;updateProbeJob();}});
$('pageSize').addEventListener('change',()=>{page=1;renderSites();});
$('previousPage').addEventListener('click',()=>{page--;renderSites();});
$('nextPage').addEventListener('click',()=>{page++;renderSites();});
$('reloadSites').addEventListener('click',()=>mutation(reload));
$('addGroup').addEventListener('click',()=>editGroup(null));
for(const button of document.querySelectorAll('[data-view],[data-open-view]')) button.addEventListener('click',()=>setView(button.dataset.view || button.dataset.openView));
$('groupNav').addEventListener('click',event=>{
  const edit = event.target.closest('[data-edit]'); if(edit){editGroup(edit.dataset.edit);return;}
  const button=event.target.closest('.group-target'); if(!button)return;
  currentGroup=button.dataset.group; selected.clear(); page=1; renderSites(); loadSuggestions();
});
$('siteRows').addEventListener('change',event=>{
  const row=event.target.closest('tr'),site=sites.find(s=>refId(s)===row?.dataset.id);if(!site)return;
  if(event.target.matches('.site-select')){event.target.checked?selected.add(refId(site)):selected.delete(refId(site));row.classList.toggle('selected',event.target.checked);renderSelection();}
  if(event.target.matches('.row-group-select'))moveSites([site],event.target.value,false);
});
$('siteRows').addEventListener('click',event=>{
  const row=event.target.closest('tr'),site=sites.find(s=>refId(s)===row?.dataset.id);if(!site)return;
  if(event.target.closest('.site-toggle'))setEnabled([site],!site.enabled,false);
  if(event.target.closest('[data-probe]'))probeSites([site]);
  if(event.target.closest('[data-details]')){
    $('previewTitle').textContent=`${site.name} · 探测详情`;
    $('jsonPreview').textContent=`来源：${site.source_name}\n分组：${groupName(site.group_id)}\n探测时间：${when(site.checked_at)}\n\n${site.result ? JSON.stringify(site.result,null,2) : '尚未探测。'}\n\n资源可达只表示脚本或接口可访问；完整播放能力需在电视端验证。`;$('previewDialog').showModal();
  }
});
$('siteRows').addEventListener('dragstart',event=>{
  if(event.target.closest('input,select,button')){event.preventDefault();return;}
  const id=event.target.closest('tr')?.dataset.id;if(!id)return;
  draggedIds=selected.has(id)?[...selected]:[id];event.dataTransfer.setData('text/plain',draggedIds.join('\n'));
});
$('siteRows').addEventListener('dragend',()=>{draggedIds=[];document.querySelectorAll('.drop-ready').forEach(e=>e.classList.remove('drop-ready'));});
$('groupNav').addEventListener('dragover',event=>{const target=event.target.closest('.group-item');if(target&&target.dataset.group!=='all'&&draggedIds.length&&!busyWrite){event.preventDefault();target.classList.add('drop-ready');}});
$('groupNav').addEventListener('dragleave',event=>event.target.closest('.group-item')?.classList.remove('drop-ready'));
$('groupNav').addEventListener('drop',event=>{const target=event.target.closest('.group-item');if(!target||target.dataset.group==='all'||!draggedIds.length)return;event.preventDefault();target.classList.remove('drop-ready');const targets=sites.filter(s=>draggedIds.includes(refId(s)));draggedIds=[];moveSites(targets,target.dataset.group);});
$('editGroupForm').addEventListener('submit',event=>{event.preventDefault();const id=$('editGroupId').value,payload={name:$('editGroupName').value.trim(),description:$('editGroupDescription').value.trim()};if(id)payload.id=Number(id);mutation(async()=>{await api(id?'/api/group/update':'/api/group/add',payload);$('groupDialog').close();await reload();toast('分组已保存');});});
$('cancelGroupEdit').addEventListener('click',()=>$('groupDialog').close());
$('deleteGroup').addEventListener('click',()=>{const id=Number($('editGroupId').value),name=$('editGroupName').value;$('groupDialog').close();mutation(async()=>{await api('/api/group/delete',{id});await reload();toast('分组已删除，站点已移到未分类');},['删除分组',`删除「${name}」并将其中站点移回未分类，启用状态保持原样。`]);});
$('confidenceThreshold').addEventListener('input',()=>{$('thresholdLabel').textContent=`${$('confidenceThreshold').value}%`;});
$('preselectSuggestions').addEventListener('click',()=>{for(const s of currentSuggestions())reviewDraft[suggestionKey(s)]=suggestedSelection(s,currentGroup,Number($('confidenceThreshold').value));saveDraft();renderSuggestions();});
$('clearSuggestions').addEventListener('click',()=>{for(const s of currentSuggestions())reviewDraft[suggestionKey(s)]=false;saveDraft();renderSuggestions();});
$('analyzeGroup').addEventListener('click',analyzeGroup);
$('suggestionRows').addEventListener('change',event=>{if(event.target.matches('.review-check')){reviewDraft[event.target.closest('.suggestion-row').dataset.key]=event.target.checked;saveDraft();renderSuggestions();}});
$('suggestionRows').addEventListener('click',event=>{
  const bucket=event.target.closest('.suggestion-bucket');if(!bucket)return;
  const keys=[...bucket.querySelectorAll('.suggestion-row')].map(row=>row.dataset.key),selection=event.target.closest('[data-review-select]');
  if(selection){for(const key of keys)reviewDraft[key]=selection.dataset.reviewSelect==='all';saveDraft();renderSuggestions();}
  if(event.target.closest('.apply-suggestions')){
    const accepted=currentSuggestions().filter(s=>keys.includes(suggestionKey(s))&&reviewDraft[suggestionKey(s)]===true);
    const targets=accepted.map(s=>sites.find(site=>site.source_id===s.source_id&&site.key===s.site_key)).filter(Boolean);
    if(!targets.length){toast('请先选择要移动的站点');return;}moveSites(targets,bucket.dataset.target);
  }
});
$('modelProvider').addEventListener('change',()=>{$('modelName').value=$('modelProvider').value==='openrouter'?'typesafe/jev-1.13':'jev-latest';});
$('modelForm').addEventListener('submit',event=>{event.preventDefault();mutation(async()=>{await api('/api/model/settings',{provider:$('modelProvider').value,model:$('modelName').value.trim(),api_key:$('modelKey').value.trim()});$('modelKey').value='';await loadModelSettings();toast('模型设置已保存');});});
$('sourceForm').addEventListener('submit',event=>{event.preventDefault();const id=$('sourceId').value,payload={name:$('sourceName').value.trim(),url:$('sourceUrl').value.trim(),type:$('sourceType').value};if(id)payload.id=Number(id);mutation(async()=>{const data=await api(id?'/api/source/update':'/api/source/add',payload);resetSourceForm();await reload();toast(changeSummary(data));});});
$('cancelEdit').addEventListener('click',resetSourceForm);
$('sourceRows').addEventListener('click',event=>{
  const button=event.target.closest('[data-action]');if(!button)return;const source=sources.find(s=>s.id===Number(button.closest('.source-row').dataset.id));if(!source)return;
  const action=button.dataset.action;
  if(action==='edit'){$('sourceId').value=source.id;$('sourceName').value=source.name;$('sourceUrl').value=source.url;$('sourceType').value=source.type;$('saveSource').textContent='保存修改';$('cancelEdit').hidden=false;$('sourceName').focus();return;}
  mutation(async()=>{const data=action==='delete'?await api('/api/source/delete',{id:source.id}):await api(`/api/source/${source.id}/sites?refresh=true`);await reload();toast(action==='delete'?'来源已移除':changeSummary(data));},action==='delete'?['移除来源',`移除「${source.name}」及其站点，相关站点将从整合订阅中消失。`]:null);
});
$('refreshAllSources').addEventListener('click',()=>mutation(async()=>{
  const errors=[],changes=[];
  for(const source of sources.filter(s=>s.type==='site')){try{const data=await api(`/api/source/${source.id}/sites?refresh=true`);changes.push(`${source.name}：${changeSummary(data)}`);}catch(error){errors.push(`${source.name}：${error.message}`);}}
  await reload();if(errors.length)showError(`部分来源更新失败，保留原配置：${errors.join('；')}`);toast(changes.length?changes.join('；'):'没有来源更新成功');
}));
$('copyLink').addEventListener('click',async()=>{try{await copyText(subUrl);toast('订阅地址已复制');}catch(error){showError(error.message);}});
$('previewButton').addEventListener('click',async()=>{$('previewTitle').textContent='整合配置预览';$('jsonPreview').textContent='正在读取…';$('previewDialog').showModal();try{const response=await fetch(subUrl);if(!response.ok)throw new Error(`读取失败 (${response.status})`);$('jsonPreview').textContent=JSON.stringify(await response.json(),null,2);}catch(error){$('jsonPreview').textContent=error.message;}});
$('closePreview').addEventListener('click',()=>$('previewDialog').close());
async function start(){await loadLibrary();await loadSources();await loadModelSettings();await loadSuggestions();}
start().catch(error=>showError(error.message));
