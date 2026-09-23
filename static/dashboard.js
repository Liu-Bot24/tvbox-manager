const $ = id => document.getElementById(id);
const subUrl = document.body.dataset.subUrl;
let sites = [];
let sources = [];
let busyProbing = false;

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function toast(message) {
  $('toast').textContent = message;
  $('toast').classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => $('toast').classList.remove('show'), 3200);
}
async function api(url, body) {
  const response = await fetch(url, body === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
  if (response.redirected && response.url.includes('/login')) { location.href = '/login'; throw new Error('请重新登录'); }
  const data = await response.json();
  if (!response.ok || data.status !== 'success') throw new Error(data.message || `请求失败 (${response.status})`);
  return data;
}
function visibleSites() {
  const query = $('siteSearch').value.trim().toLocaleLowerCase();
  const filter = $('statusFilter').value;
  const shown = sites.filter(site => {
    const match = `${site.name} ${site.source_name} ${site.key}`.toLocaleLowerCase().includes(query);
    const status = site.result?.status;
    return match && (filter === 'all' || (filter === 'enabled' && site.enabled) || (filter === 'disabled' && !site.enabled) || (filter === 'online' && status === 'online') || (filter === 'failed' && status === 'failed') || (filter === 'unprobed' && !site.result));
  });
  if ($('sortSites').value === 'name') shown.sort((a,b) => String(a.name).localeCompare(String(b.name), 'zh-CN'));
  if ($('sortSites').value === 'latency') shown.sort((a,b) => (a.result?.search_ms ?? a.result?.resource_ms ?? Infinity) - (b.result?.search_ms ?? b.result?.resource_ms ?? Infinity));
  return shown;
}
function probeLabel(result) {
  if (!result) return ['未探测', ''];
  if (result.status === 'online') return [result.playback === 'ok' ? '播放首段可达' : '搜索与详情可用', 'online'];
  if (result.status === 'resource_only') return [`资源可达 · ${result.resource_ms} ms`, 'resource'];
  if (result.status === 'unsupported') return ['需电视端验证', 'unsupported'];
  return [`${({search:'搜索',detail:'详情',playback:'播放',resource:'资源访问'})[result.stage] || '请求'}失败`, 'failed'];
}
function metric(value, suffix = '') { return value === undefined || value === null ? '—' : `${escapeHtml(value)}${suffix}`; }
function rowId(site) { return `${site.source_id}:${site.key}`; }
function renderSites() {
  const shown = visibleSites();
  $('totalCount').textContent = sites.length;
  $('enabledCount').textContent = sites.filter(s => s.enabled).length;
  $('onlineCount').textContent = sites.filter(s => s.result?.status === 'online').length;
  $('shownCount').textContent = `显示 ${shown.length} / ${sites.length} 个站点`;
  $('siteRows').innerHTML = shown.length ? shown.map(site => {
    const [status, state] = probeLabel(site.result);
    const result = site.result || {};
    const type = [0,1,4].includes(site.type) ? 'CMS' : site.type === 3 ? 'Spider' : `类型 ${site.type ?? '?'}`;
    const title = result.error ? `${status} · ${result.error}` : status;
    return `<tr data-id="${escapeHtml(rowId(site))}"><td><input class="site-toggle" type="checkbox" aria-label="启用 ${escapeHtml(site.name)}" ${site.enabled ? 'checked' : ''}></td><td><span class="site-title" title="${escapeHtml(site.name)}">${escapeHtml(site.name)}</span><span class="site-subtitle" title="${escapeHtml(site.source_name)} · ${escapeHtml(site.key)}">${escapeHtml(site.source_name)} · ${escapeHtml(site.key)}</span></td><td><span class="type-badge">${escapeHtml(type)}</span></td><td><span class="status ${state}" title="${escapeHtml(title)}">${escapeHtml(status)}</span></td><td class="numeric">${metric(result.search_ms)}</td><td class="numeric">${metric(result.detail_ms)}</td><td class="numeric">${metric(result.play_latency_ms)}</td><td class="numeric">${metric(result.speed_kbps,' kbps')}</td><td><button class="row-action" type="button">探测</button></td></tr>`;
  }).join('') : '<tr><td colspan="9" class="empty">没有符合条件的站点。请调整筛选，或在下方导入配置。</td></tr>';
}
async function loadSites() {
  $('siteRows').innerHTML = '<tr><td colspan="9" class="empty">正在读取站点…</td></tr>';
  try {
    const data = await api('/api/site/list');
    sites = data.data;
    const errors = data.errors || [];
    $('siteErrors').hidden = !errors.length;
    $('siteErrors').textContent = errors.map(e => `${e.source}：${e.message}`).join('；');
    renderSites();
  } catch (error) { $('siteRows').innerHTML = `<tr><td colspan="9" class="empty">${escapeHtml(error.message)}</td></tr>`; }
}
async function probeOne(site, button) {
  if (button) { button.disabled = true; button.textContent = '探测中…'; }
  try {
    const data = await api('/api/site/probe', {source_id:site.source_id, key:site.key, keyword:$('probeKeyword').value.trim() || '庆余年'});
    site.result = data.data;
    renderSites();
  } catch (error) { toast(`${site.name}：${error.message}`); if (button) { button.disabled = false; button.textContent = '探测'; } }
}
async function probeVisible() {
  if (busyProbing) return;
  const jobs = visibleSites();
  if (!jobs.length) { toast('当前列表没有站点'); return; }
  busyProbing = true;
  $('probeVisible').disabled = true;
  let next = 0, done = 0;
  const worker = async () => {
    while (next < jobs.length) {
      const site = jobs[next++];
      $('probeProgress').textContent = `探测中 ${done} / ${jobs.length}`;
      await probeOne(site);
      done++;
      $('probeProgress').textContent = `探测中 ${done} / ${jobs.length}`;
    }
  };
  await Promise.all(Array.from({length:Math.min(6,jobs.length)}, worker));
  $('probeProgress').textContent = `已完成 ${done} 个站点`;
  $('probeVisible').disabled = false;
  busyProbing = false;
}
function renderSources() {
  $('sourceCount').textContent = sources.filter(s => s.type === 'site').length;
  $('sourceRows').innerHTML = sources.length ? sources.map((source,index) => `<div class="source-row" data-id="${source.id}"><span class="source-index">${String(index+1).padStart(2,'0')}</span><div class="source-meta"><strong>${escapeHtml(source.name)} <span class="type-badge">${source.type === 'live' ? '直播' : 'TVBox 配置'}</span></strong><small title="${escapeHtml(source.url)}">${escapeHtml(source.url)}</small></div><div class="source-actions"><button data-action="refresh" type="button">刷新</button><button data-action="edit" type="button">编辑</button><button data-action="delete" class="danger" type="button">移除</button></div></div>`).join('') : '<p class="empty">尚未导入配置，请在上方填写地址。</p>';
}
async function loadSources() {
  try { sources = (await api('/api/source/list')).data; renderSources(); }
  catch (error) { $('sourceRows').innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`; }
}
function resetForm() { $('sourceForm').reset(); $('sourceId').value = ''; $('saveSource').textContent = '添加配置'; $('cancelEdit').hidden = true; }
async function refreshSource(source) {
  if (source.type !== 'site') return;
  await api(`/api/source/${source.id}/sites?refresh=true`);
}
async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const area = document.createElement('textarea'); area.value = text; area.style.position = 'fixed'; area.style.left = '-9999px'; document.body.append(area); area.select();
  const copied = document.execCommand('copy'); area.remove(); if (!copied) throw new Error('复制失败');
}

$('siteSearch').addEventListener('input', renderSites);
$('statusFilter').addEventListener('change', renderSites);
$('sortSites').addEventListener('change', renderSites);
$('reloadSites').addEventListener('click', loadSites);
$('probeVisible').addEventListener('click', probeVisible);
$('siteRows').addEventListener('change', async event => {
  if (!event.target.matches('.site-toggle')) return;
  const site = sites.find(s => rowId(s) === event.target.closest('tr').dataset.id);
  if (!site) return;
  const box = event.target; box.disabled = true;
  try { await api('/api/site/enable', {source_id:site.source_id, key:site.key, enabled:box.checked}); site.enabled = box.checked; renderSites(); }
  catch (error) { box.checked = site.enabled; box.disabled = false; toast(error.message); }
});
$('siteRows').addEventListener('click', event => {
  if (!event.target.matches('.row-action')) return;
  const site = sites.find(s => rowId(s) === event.target.closest('tr').dataset.id);
  if (site) probeOne(site, event.target);
});
$('sourceForm').addEventListener('submit', async event => {
  event.preventDefault(); const id = $('sourceId').value;
  const payload = {name:$('sourceName').value.trim(),url:$('sourceUrl').value.trim(),type:$('sourceType').value};
  if (id) payload.id = Number(id);
  $('saveSource').disabled = true;
  try { const data = await api(id ? '/api/source/update' : '/api/source/add', payload); resetForm(); await loadSources(); await loadSites(); toast(data.message); }
  catch (error) { toast(error.message); }
  finally { $('saveSource').disabled = false; }
});
$('cancelEdit').addEventListener('click', resetForm);
$('sourceRows').addEventListener('click', async event => {
  const button = event.target.closest('button[data-action]'); if (!button) return;
  const source = sources.find(s => s.id === Number(button.closest('.source-row').dataset.id)); if (!source) return;
  const action = button.dataset.action;
  if (action === 'edit') { $('sourceId').value = source.id; $('sourceName').value = source.name; $('sourceUrl').value = source.url; $('sourceType').value = source.type; $('saveSource').textContent = '保存修改'; $('cancelEdit').hidden = false; $('sourceName').focus(); return; }
  if (action === 'delete' && !confirm(`移除「${source.name}」及其站点选择？`)) return;
  button.disabled = true;
  try {
    if (action === 'delete') await api('/api/source/delete', {id:source.id});
    else await refreshSource(source);
    await loadSources(); await loadSites(); toast(action === 'delete' ? '已移除配置' : '原始配置已刷新');
  } catch (error) { toast(error.message); button.disabled = false; }
});
$('refreshAllSources').addEventListener('click', async event => {
  const button = event.target; button.disabled = true;
  let failed = 0;
  for (const source of sources.filter(s => s.type === 'site')) {
    button.textContent = `刷新中：${source.name}`;
    try { await refreshSource(source); } catch (error) { failed++; toast(`${source.name}：${error.message}`); }
  }
  await loadSites(); button.disabled = false; button.textContent = '刷新原始配置';
  toast(failed ? `${failed} 份配置刷新失败` : '所有原始配置已刷新');
});
$('copyLink').addEventListener('click', async () => { try { await copyText(subUrl); toast('订阅地址已复制'); } catch (error) { toast(error.message); } });
$('previewButton').addEventListener('click', async () => { $('previewDialog').showModal(); $('jsonPreview').textContent = '正在生成…'; try { const response = await fetch(subUrl); $('jsonPreview').textContent = await response.text(); } catch (error) { $('jsonPreview').textContent = error.message; } });
$('closePreview').addEventListener('click', () => $('previewDialog').close());
loadSources(); loadSites();
