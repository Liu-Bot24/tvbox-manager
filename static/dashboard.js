const $ = id => document.getElementById(id);
const subUrl = document.body.dataset.subUrl;
let sites = [], sources = [], groups = [], suggestions = [];
let currentGroup = 'all', modelConfigured = false, busyProbing = false, draggedSite = null;

function esc(value) { return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function ref(site) { return {source_id:site.source_id, key:site.key}; }
function rowId(site) { return `${site.source_id}:${site.key}`; }
function groupValue(site) { return site.group_id == null ? 'unclassified' : String(site.group_id); }
function groupName(id) { return id == null ? '未分类' : (groups.find(g => g.id === id)?.name || '已删除分组'); }
function inCurrentGroup(site) { return currentGroup === 'all' || groupValue(site) === currentGroup; }
function toast(message) { $('toast').textContent = message; $('toast').classList.add('show'); clearTimeout(toast.timer); toast.timer = setTimeout(() => $('toast').classList.remove('show'), 3200); }
async function api(url, body) {
  const response = await fetch(url, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (response.redirected && response.url.includes('/login')) { location.href = '/login'; throw new Error('请重新登录'); }
  const data = await response.json();
  if (!response.ok || data.status !== 'success') throw new Error(data.message || `请求失败 (${response.status})`);
  return data;
}
function visibleSites() {
  const query = $('siteSearch').value.trim().toLocaleLowerCase(), filter = $('statusFilter').value;
  const shown = sites.filter(site => {
    const match = `${site.name} ${site.source_name} ${site.key}`.toLocaleLowerCase().includes(query);
    const state = site.result?.status;
    const statusMatch = filter === 'all' || (filter === 'enabled' && site.enabled) || (filter === 'disabled' && !site.enabled) || (filter === 'online' && state === 'online') || (filter === 'resource' && state === 'resource_only') || (filter === 'failed' && state === 'failed') || (filter === 'unsupported' && state === 'unsupported') || (filter === 'unprobed' && !site.result);
    return inCurrentGroup(site) && match && statusMatch;
  });
  if ($('sortSites').value === 'name') shown.sort((a,b) => String(a.name).localeCompare(String(b.name), 'zh-CN'));
  if ($('sortSites').value === 'latency') shown.sort((a,b) => (a.result?.search_ms ?? a.result?.resource_ms ?? Infinity) - (b.result?.search_ms ?? b.result?.resource_ms ?? Infinity));
  return shown;
}
function probeLabel(result) {
  if (!result) return ['未探测',''];
  if (result.status === 'online') return [result.playback === 'ok' ? '播放首段可达' : '搜索与详情可用','online'];
  if (result.status === 'resource_only') return [`资源可达 · ${result.resource_ms} ms`,'resource'];
  if (result.status === 'unsupported') return ['需电视端验证','unsupported'];
  return [`${({search:'搜索',detail:'详情',playback:'播放',resource:'资源访问'})[result.stage] || '请求'}失败`,'failed'];
}
function metric(value, suffix='') { return value == null ? '—' : `${esc(value)}${suffix}`; }
function groupOptions(selected) { return `<option value="unclassified" ${selected === 'unclassified' ? 'selected' : ''}>未分类</option>` + groups.map(g => `<option value="${g.id}" ${selected === String(g.id) ? 'selected' : ''}>${esc(g.name)}</option>`).join(''); }
function renderGroups() {
  const items = [{id:'all',name:'全部站点',count:sites.length},{id:'unclassified',name:'未分类',count:sites.filter(s => s.group_id == null).length},...groups.map(g => ({id:String(g.id),name:g.name,count:sites.filter(s => s.group_id === g.id).length}))];
  $('groupNav').innerHTML = items.map(item => `<div class="group-item ${currentGroup === item.id ? 'active' : ''}" data-group="${item.id}"><button class="group-target" type="button" data-group="${item.id}"><span>${esc(item.name)}</span><b>${item.count}</b></button>${item.id !== 'all' && item.id !== 'unclassified' ? `<button class="group-edit" type="button" data-edit="${item.id}" aria-label="编辑 ${esc(item.name)}">···</button>` : ''}</div>`).join('');
  $('currentGroupTitle').textContent = currentGroup === 'all' ? '全部站点' : currentGroup === 'unclassified' ? '未分类' : groupName(Number(currentGroup));
  $('aiTitle').textContent = currentGroup === 'unclassified' ? '未分类站点分拣' : currentGroup === 'all' ? '选择一个分组' : `审核「${groupName(Number(currentGroup))}」`;
  $('analyzeGroup').disabled = !modelConfigured || currentGroup === 'all';
}
function renderSites() {
  const shown = visibleSites();
  $('totalCount').textContent = sites.length;
  $('enabledCount').textContent = sites.filter(s => s.enabled).length;
  $('onlineCount').textContent = sites.filter(s => s.result?.status === 'online').length;
  $('shownCount').textContent = `显示 ${shown.length} / ${sites.filter(inCurrentGroup).length} 个站点`;
  $('siteRows').innerHTML = shown.length ? shown.map(site => {
    const [status,state] = probeLabel(site.result), result = site.result || {};
    const type = [0,1,4].includes(site.type) ? 'CMS' : site.type === 3 ? 'Spider' : `类型 ${site.type ?? '?'}`;
    const title = result.error ? `${status} · ${result.error}` : status;
    return `<tr draggable="true" data-id="${esc(rowId(site))}"><td><input class="site-toggle" type="checkbox" aria-label="启用 ${esc(site.name)}" ${site.enabled ? 'checked' : ''}></td><td><span class="site-title" title="${esc(site.name)}">${esc(site.name)}</span><span class="site-subtitle" title="${esc(site.source_name)} · ${esc(site.key)}">${esc(site.source_name)} · ${esc(site.key)}</span></td><td><select class="row-group-select" aria-label="${esc(site.name)}所属分组">${groupOptions(groupValue(site))}</select></td><td><span class="type-badge">${esc(type)}</span></td><td><span class="status ${state}" title="${esc(title)}">${esc(status)}</span></td><td class="numeric">${metric(result.search_ms)}</td><td class="numeric">${metric(result.detail_ms)}</td><td class="numeric">${metric(result.play_latency_ms)}</td><td class="numeric">${metric(result.speed_kbps,' kbps')}</td><td><button class="row-action" type="button">探测</button></td></tr>`;
  }).join('') : '<tr><td colspan="10" class="empty">没有符合条件的站点。请调整筛选或导入配置。</td></tr>';
  renderGroups(); renderBatchPreview();
}
async function loadGroups() { groups = (await api('/api/group/list')).data; renderGroups(); }
async function loadSites() {
  $('siteRows').innerHTML = '<tr><td colspan="10" class="empty">正在读取站点…</td></tr>';
  try {
    const data = await api('/api/site/list'); sites = data.data;
    const errors = data.errors || []; $('siteErrors').hidden = !errors.length;
    $('siteErrors').textContent = errors.map(e => `${e.source}：${e.message}`).join('；');
    renderSites();
  } catch (error) { $('siteRows').innerHTML = `<tr><td colspan="10" class="empty">${esc(error.message)}</td></tr>`; }
}
async function moveSites(selected, target) {
  if (!selected.length) return;
  const groupId = target === 'unclassified' ? null : Number(target);
  const data = await api('/api/group/assign', {group_id:groupId, sites:selected.map(ref)});
  const moved = new Set(selected.map(rowId));
  for (const site of sites) if (moved.has(rowId(site))) site.group_id = groupId;
  suggestions = suggestions.filter(s => !moved.has(`${s.source_id}:${s.site_key}`));
  renderSites(); renderSuggestions(); toast(data.message);
}
function batchTargets(action) {
  if (!action) return [];
  if (action === 'disable_failed') return sites.filter(s => s.enabled && s.result?.status === 'failed');
  if (action === 'disable_unsupported') return sites.filter(s => s.enabled && s.result?.status === 'unsupported');
  if (action === 'enable_cms') return sites.filter(s => !s.enabled && [0,1,4].includes(s.type));
  if (action === 'disable_cms') return sites.filter(s => s.enabled && [0,1,4].includes(s.type));
  if (action === 'enable_spider') return sites.filter(s => !s.enabled && s.type === 3);
  if (action === 'disable_spider') return sites.filter(s => s.enabled && s.type === 3);
  if (action === 'enable_group') return sites.filter(s => inCurrentGroup(s) && !s.enabled);
  if (action === 'disable_group') return sites.filter(s => inCurrentGroup(s) && s.enabled);
  if (action === 'enable_visible') return visibleSites().filter(s => !s.enabled);
  if (action === 'disable_visible') return visibleSites().filter(s => s.enabled);
  return [];
}
function renderBatchPreview() {
  const action = $('batchAction').value, count = batchTargets(action).length;
  $('batchPreview').textContent = action ? `将影响 ${count} 个站点` : '选择操作后显示影响数量';
  $('applyBatch').disabled = !count;
}
async function applyBatch() {
  const action = $('batchAction').value, targets = batchTargets(action), label = $('batchAction').selectedOptions[0].textContent;
  if (!targets.length || !confirm(`${label}：将更新 ${targets.length} 个站点，确定执行？`)) return;
  const enabled = action.startsWith('enable_'), button = $('applyBatch'); button.disabled = true;
  try { const data = await api('/api/site/batch_enable', {enabled,sites:targets.map(ref)}); for (const site of targets) site.enabled = enabled; renderSites(); toast(data.message); }
  catch (error) { toast(error.message); renderBatchPreview(); }
}
async function probeOne(site, button) {
  if (button) { button.disabled = true; button.textContent = '探测中…'; }
  try { site.result = (await api('/api/site/probe', {source_id:site.source_id,key:site.key,keyword:$('probeKeyword').value.trim() || '庆余年'})).data; renderSites(); }
  catch (error) { toast(`${site.name}：${error.message}`); if (button) { button.disabled = false; button.textContent = '探测'; } }
}
async function probeVisible() {
  if (busyProbing) return;
  const jobs = visibleSites(); if (!jobs.length) { toast('当前列表没有站点'); return; }
  busyProbing = true; $('probeVisible').disabled = true;
  let next = 0, done = 0;
  const worker = async () => { while (next < jobs.length) { const site = jobs[next++]; await probeOne(site); $('probeProgress').textContent = `探测中 ${++done} / ${jobs.length}`; } };
  await Promise.all(Array.from({length:Math.min(6,jobs.length)},worker));
  $('probeProgress').textContent = `已完成 ${done} 个站点`; $('probeVisible').disabled = false; busyProbing = false;
}

function pct(value) { return value == null ? '—' : `${Math.round(Number(value)*100)}%`; }
function suggestionDetails(s) {
  const entries = Object.entries(s.probabilities || {}).sort((a,b) => b[1]-a[1]);
  return `<details><summary>查看各组概率</summary><div class="probabilities">${entries.map(([key,value]) => `<span>${esc(key === 'unclassified' ? '未分类' : groupName(Number(key.slice(2))))} <b>${pct(value)}</b></span>`).join('')}</div></details>`;
}
function suggestionRow(s, site, checked) {
  return `<div class="suggestion-row" data-id="${esc(rowId(site))}"><label><input class="suggest-select" type="checkbox" ${checked ? 'checked' : ''}><span><strong>${esc(site.name)}</strong><small>${esc(site.source_name)}</small></span></label><div class="suggestion-score">${currentGroup === 'unclassified' ? `建议归入 ${esc(groupName(s.suggested_group_id))} · ${pct(s.confidence)}` : `当前组归属 ${pct(s.membership)} · 建议 ${esc(groupName(s.suggested_group_id))}`}${suggestionDetails(s)}</div></div>`;
}
function renderSuggestions() {
  const container = $('suggestionRows');
  if (currentGroup === 'all') { container.innerHTML = '<p class="empty">选择左侧一个分组或“未分类”后分析。</p>'; return; }
  const current = suggestions.filter(s => sites.some(site => site.source_id === s.source_id && site.key === s.site_key && groupValue(site) === currentGroup));
  if (!current.length) { container.innerHTML = '<p class="empty">暂无建议。点击“分析当前分组”获取每个站点的置信度。</p>'; return; }
  const threshold = Number($('confidenceThreshold').value)/100;
  if (currentGroup !== 'unclassified') {
    container.innerHTML = current.map(s => { const site = sites.find(v => v.source_id === s.source_id && v.key === s.site_key); return suggestionRow(s,site,s.membership != null && s.membership < threshold); }).join('') + '<div class="suggestion-actions"><button class="button button-outline" id="removeSuggested" type="button">将选中站点移到未分类</button></div>';
    return;
  }
  const buckets = new Map();
  for (const s of current) { const key = s.suggested_group_id == null ? 'unclassified' : String(s.suggested_group_id); if (!buckets.has(key)) buckets.set(key,[]); buckets.get(key).push(s); }
  container.innerHTML = [...buckets].map(([target, entries]) => `<div class="suggestion-bucket" data-target="${target}"><div class="bucket-head"><strong>${esc(groupName(target === 'unclassified' ? null : Number(target)))} <small>${entries.length} 个建议</small></strong>${target === 'unclassified' ? '<span class="muted">保留在未分类</span>' : `<button class="button button-outline apply-suggestions" type="button">确认移动选中项</button>`}</div>${entries.map(s => { const site = sites.find(v => v.source_id === s.source_id && v.key === s.site_key); return suggestionRow(s,site,s.confidence >= threshold && target !== 'unclassified'); }).join('')}</div>`).join('');
}
async function loadSuggestions() {
  suggestions = [];
  if (currentGroup === 'all') { renderSuggestions(); return; }
  try { suggestions = (await api(`/api/group/suggestions?group_id=${encodeURIComponent(currentGroup)}`)).data; }
  catch (error) { toast(error.message); }
  renderSuggestions();
}
async function analyzeGroup() {
  if (!modelConfigured) { $('modelSettings').open = true; toast('请先配置 Jev API Key'); return; }
  if (currentGroup === 'all') { toast('请先选择一个分组'); return; }
  const current = currentGroup, jobs = sites.filter(inCurrentGroup);
  if (!jobs.length) { toast('当前分组没有站点'); return; }
  if (!confirm(`将 ${jobs.length} 个站点的名称、Key、来源、类型及分组说明发送给模型分析。结果仅作建议，确定继续？`)) return;
  const button = $('analyzeGroup'); button.disabled = true;
  let errors = 0, done = 0;
  try {
    for (let i=0; i<jobs.length; i+=25) {
      const batch = jobs.slice(i,i+25);
      $('analysisProgress').textContent = `分析中 ${done}/${jobs.length}`;
      const data = await api('/api/group/analyze', {group_id:current === 'unclassified' ? null : Number(current),sites:batch.map(ref)});
      errors += (data.errors || []).length; done += batch.length;
      $('analysisProgress').textContent = `已分析 ${done}/${jobs.length}`;
      if (currentGroup !== current) break;
    }
    await loadSuggestions(); toast(errors ? `${errors} 个站点分析失败，请检查模型设置` : '建议已生成，请审核后再移动');
  } catch (error) { toast(error.message); }
  finally { button.disabled = currentGroup === 'all' || !modelConfigured; }
}
async function applySuggestions(target, container) {
  const selected = [...container.querySelectorAll('.suggestion-row')].filter(row => row.querySelector('.suggest-select').checked).map(row => sites.find(site => rowId(site) === row.dataset.id)).filter(Boolean);
  if (!selected.length) { toast('请先勾选要移动的站点'); return; }
  if (!confirm(`将 ${selected.length} 个站点移到「${groupName(target === 'unclassified' ? null : Number(target))}」？`)) return;
  try { await moveSites(selected,target); await loadSuggestions(); }
  catch (error) { toast(error.message); }
}
async function loadModelSettings() {
  try { const data = (await api('/api/model/settings')).data; modelConfigured = data.configured; $('modelProvider').value = data.provider; $('modelName').value = data.model; $('modelStatus').textContent = data.configured ? `${data.provider === 'openrouter' ? 'OpenRouter' : 'TypeSafe'} 已接入` : '未接入模型'; renderGroups(); }
  catch (error) { toast(error.message); }
}

function renderSources() {
  $('sourceCount').textContent = sources.filter(s => s.type === 'site').length;
  $('sourceRows').innerHTML = sources.length ? sources.map((source,index) => `<div class="source-row" data-id="${source.id}"><span class="source-index">${String(index+1).padStart(2,'0')}</span><div class="source-meta"><strong>${esc(source.name)} <span class="type-badge">${source.type === 'live' ? '直播' : 'TVBox 配置'}</span></strong><small title="${esc(source.url)}">${esc(source.url)}</small></div><div class="source-actions"><button data-action="refresh" type="button">刷新</button><button data-action="edit" type="button">编辑</button><button data-action="delete" class="danger" type="button">移除</button></div></div>`).join('') : '<p class="empty">尚未导入配置，请在上方填写地址。</p>';
}
async function loadSources() { try { sources = (await api('/api/source/list')).data; renderSources(); } catch (error) { $('sourceRows').innerHTML = `<p class="empty">${esc(error.message)}</p>`; } }
function resetForm() { $('sourceForm').reset(); $('sourceId').value = ''; $('saveSource').textContent = '添加配置'; $('cancelEdit').hidden = true; }
async function refreshSource(source) { if (source.type === 'site') await api(`/api/source/${source.id}/sites?refresh=true`); }
async function copyText(value) { if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(value); const area = document.createElement('textarea'); area.value = value; area.style.position = 'fixed'; area.style.left = '-9999px'; document.body.append(area); area.select(); const copied = document.execCommand('copy'); area.remove(); if (!copied) throw new Error('复制失败'); }

$('siteSearch').addEventListener('input', renderSites);
$('statusFilter').addEventListener('change', renderSites);
$('sortSites').addEventListener('change', renderSites);
$('batchAction').addEventListener('change', renderBatchPreview);
$('applyBatch').addEventListener('click', applyBatch);
$('reloadSites').addEventListener('click', async () => { await loadGroups(); await loadSites(); await loadSuggestions(); });
$('probeVisible').addEventListener('click', probeVisible);
$('groupNav').addEventListener('click', async event => {
  const edit = event.target.closest('[data-edit]');
  if (edit) { const group = groups.find(g => String(g.id) === edit.dataset.edit); if (!group) return; $('editGroupId').value = group.id; $('editGroupName').value = group.name; $('editGroupDescription').value = group.description; $('groupDialog').showModal(); return; }
  const button = event.target.closest('.group-target'); if (!button) return;
  currentGroup = button.dataset.group; renderSites(); await loadSuggestions();
});
$('groupNav').addEventListener('dragover', event => { const target = event.target.closest('.group-item'); if (target && target.dataset.group !== 'all' && draggedSite) { event.preventDefault(); target.classList.add('drop-ready'); } });
$('groupNav').addEventListener('dragleave', event => { const target = event.target.closest('.group-item'); if (target) target.classList.remove('drop-ready'); });
$('groupNav').addEventListener('drop', async event => { const target = event.target.closest('.group-item'); if (!target || target.dataset.group === 'all' || !draggedSite) return; event.preventDefault(); target.classList.remove('drop-ready'); try { await moveSites([draggedSite], target.dataset.group); await loadSuggestions(); } catch (error) { toast(error.message); } draggedSite = null; });
$('siteRows').addEventListener('dragstart', event => { if (event.target.closest('input,select,button')) { event.preventDefault(); return; } draggedSite = sites.find(s => rowId(s) === event.target.closest('tr')?.dataset.id) || null; if (draggedSite) event.dataTransfer.setData('text/plain',rowId(draggedSite)); });
$('siteRows').addEventListener('dragend', () => { draggedSite = null; document.querySelectorAll('.drop-ready').forEach(el => el.classList.remove('drop-ready')); });
$('siteRows').addEventListener('change', async event => {
  const row = event.target.closest('tr'), site = sites.find(s => rowId(s) === row?.dataset.id); if (!site) return;
  if (event.target.matches('.site-toggle')) { const box = event.target; box.disabled = true; try { await api('/api/site/enable',{...ref(site),enabled:box.checked}); site.enabled = box.checked; renderSites(); } catch (error) { box.checked = site.enabled; box.disabled = false; toast(error.message); } }
  if (event.target.matches('.row-group-select')) { const target = event.target.value; try { await moveSites([site],target); await loadSuggestions(); } catch (error) { renderSites(); toast(error.message); } }
});
$('siteRows').addEventListener('click', event => { if (!event.target.matches('.row-action')) return; const site = sites.find(s => rowId(s) === event.target.closest('tr').dataset.id); if (site) probeOne(site,event.target); });
$('newGroupForm').addEventListener('submit', async event => { event.preventDefault(); try { const data = await api('/api/group/add',{name:$('newGroupName').value.trim(),description:$('newGroupDescription').value.trim()}); $('newGroupForm').reset(); await loadGroups(); currentGroup = String(data.group_id); renderSites(); await loadSuggestions(); toast('分组已创建'); } catch (error) { toast(error.message); } });
$('editGroupForm').addEventListener('submit', async event => { event.preventDefault(); try { await api('/api/group/update',{id:Number($('editGroupId').value),name:$('editGroupName').value.trim(),description:$('editGroupDescription').value.trim()}); $('groupDialog').close(); await loadGroups(); renderSites(); await loadSuggestions(); toast('分组已更新'); } catch (error) { toast(error.message); } });
$('cancelGroupEdit').addEventListener('click', () => $('groupDialog').close());
$('deleteGroup').addEventListener('click', async () => { const id = Number($('editGroupId').value), group = groups.find(g => g.id === id); if (!group || !confirm(`删除「${group.name}」？其中站点会移到未分类，启用状态不变。`)) return; try { await api('/api/group/delete',{id}); for (const site of sites) if (site.group_id === id) site.group_id = null; $('groupDialog').close(); currentGroup = 'unclassified'; await loadGroups(); renderSites(); await loadSuggestions(); toast('分组已删除'); } catch (error) { toast(error.message); } });
$('confidenceThreshold').addEventListener('input', () => { $('thresholdLabel').textContent = `${$('confidenceThreshold').value}%`; renderSuggestions(); });
$('analyzeGroup').addEventListener('click', analyzeGroup);
$('suggestionRows').addEventListener('click', event => { if (event.target.id === 'removeSuggested') applySuggestions('unclassified',$('suggestionRows')); const button = event.target.closest('.apply-suggestions'); if (button) { const bucket = button.closest('.suggestion-bucket'); applySuggestions(bucket.dataset.target,bucket); } });
$('modelProvider').addEventListener('change', () => { $('modelName').value = $('modelProvider').value === 'openrouter' ? 'typesafe/jev-1.13' : 'jev-latest'; });
$('modelForm').addEventListener('submit', async event => { event.preventDefault(); try { await api('/api/model/settings',{provider:$('modelProvider').value,model:$('modelName').value.trim(),api_key:$('modelKey').value.trim()}); $('modelKey').value = ''; await loadModelSettings(); toast('模型接入已保存'); } catch (error) { toast(error.message); } });
$('sourceForm').addEventListener('submit', async event => { event.preventDefault(); const id = $('sourceId').value, payload = {name:$('sourceName').value.trim(),url:$('sourceUrl').value.trim(),type:$('sourceType').value}; if (id) payload.id = Number(id); $('saveSource').disabled = true; try { const data = await api(id ? '/api/source/update' : '/api/source/add',payload); resetForm(); await loadSources(); await loadSites(); await loadSuggestions(); toast(data.message); } catch (error) { toast(error.message); } finally { $('saveSource').disabled = false; } });
$('cancelEdit').addEventListener('click', resetForm);
$('sourceRows').addEventListener('click', async event => { const button = event.target.closest('button[data-action]'); if (!button) return; const source = sources.find(s => s.id === Number(button.closest('.source-row').dataset.id)); if (!source) return; const action = button.dataset.action; if (action === 'edit') { $('sourceId').value = source.id; $('sourceName').value = source.name; $('sourceUrl').value = source.url; $('sourceType').value = source.type; $('saveSource').textContent = '保存修改'; $('cancelEdit').hidden = false; $('sourceName').focus(); return; } if (action === 'delete' && !confirm(`移除「${source.name}」及其站点选择？`)) return; button.disabled = true; try { if (action === 'delete') await api('/api/source/delete',{id:source.id}); else await refreshSource(source); await loadSources(); await loadSites(); await loadSuggestions(); toast(action === 'delete' ? '已移除配置' : '原始配置已刷新'); } catch (error) { toast(error.message); button.disabled = false; } });
$('refreshAllSources').addEventListener('click', async event => { const button = event.target; button.disabled = true; let failed = 0; for (const source of sources.filter(s => s.type === 'site')) { button.textContent = `刷新中：${source.name}`; try { await refreshSource(source); } catch (error) { failed++; toast(`${source.name}：${error.message}`); } } await loadSites(); await loadSuggestions(); button.disabled = false; button.textContent = '刷新原始配置'; toast(failed ? `${failed} 份配置刷新失败` : '所有原始配置已刷新'); });
$('copyLink').addEventListener('click', async () => { try { await copyText(subUrl); toast('订阅地址已复制'); } catch (error) { toast(error.message); } });
$('previewButton').addEventListener('click', async () => { $('previewDialog').showModal(); $('jsonPreview').textContent = '正在生成…'; try { const response = await fetch(subUrl); $('jsonPreview').textContent = await response.text(); } catch (error) { $('jsonPreview').textContent = error.message; } });
$('closePreview').addEventListener('click', () => $('previewDialog').close());
async function start() { await loadGroups(); await Promise.all([loadSites(),loadSources(),loadModelSettings()]); await loadSuggestions(); }
start().catch(error => toast(error.message));
