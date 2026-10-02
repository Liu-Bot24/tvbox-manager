(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.SiteLibrary = factory();
}(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  function refId(site) {
    return `${site.source_id}:${site.key}`;
  }

  function isCms(site) {
    return site.type != null && [0, 1, 4].includes(Number(site.type));
  }

  function isSpider(site) {
    return site.type != null && Number(site.type) === 3;
  }

  function latency(site, field) {
    const value = site.result?.[field];
    return value != null && Number.isFinite(Number(value)) ? Number(value) : Infinity;
  }

  function filterSites(sites, filters = {}) {
    const group = String(filters.group ?? 'all');
    const query = String(filters.query ?? '').trim().toLocaleLowerCase();
    const status = filters.status ?? 'all';
    const enabled = filters.enabled ?? 'all';
    const type = filters.type ?? 'all';
    const source = String(filters.source ?? 'all');
    const shown = sites.filter(site => {
      const siteGroup = site.group_id == null ? 'unclassified' : String(site.group_id);
      if (group !== 'all' && siteGroup !== group) return false;
      if (source !== 'all' && String(site.source_id) !== source) return false;
      if (query && !`${site.name ?? ''} ${site.source_name ?? ''} ${site.key ?? ''}`.toLocaleLowerCase().includes(query)) return false;
      if (status !== 'all') {
        if (status === 'unprobed' ? site.result != null : site.result?.status !== status) return false;
      }
      if (enabled === 'enabled' && !site.enabled) return false;
      if (enabled === 'disabled' && site.enabled) return false;
      if (type === 'cms' && !isCms(site)) return false;
      if (type === 'spider' && !isSpider(site)) return false;
      return true;
    });
    if (filters.sort === 'name') {
      shown.sort((a, b) => String(a.name ?? '').localeCompare(String(b.name ?? ''), 'zh-CN'));
    } else if (filters.sort === 'search' || filters.sort === 'resource') {
      const field = filters.sort === 'search' ? 'search_ms' : 'resource_ms';
      shown.sort((a, b) => {
        const left = latency(a, field), right = latency(b, field);
        return left === right ? 0 : left < right ? -1 : 1;
      });
    }
    return shown;
  }

  function selectMatching(sites, rule) {
    const predicates = {
      all: () => true,
      failed: site => site.result?.status === 'failed',
      unsupported: site => site.result?.status === 'unsupported',
      cms: isCms,
      spider: isSpider,
    };
    const predicate = predicates[rule];
    return predicate ? sites.filter(predicate).map(refId) : [];
  }

  function suggestionKey(suggestion) {
    return `${suggestion.source_id}:${suggestion.site_key}:${suggestion.context_group_id ?? 'unclassified'}:${suggestion.created_at}`;
  }

  function suggestedSelection(suggestion, currentGroup, thresholdPercent) {
    const threshold = Number(thresholdPercent) / 100;
    if (!Number.isFinite(threshold) || currentGroup === 'all') return false;
    if (currentGroup === 'unclassified') {
      return suggestion.suggested_group_id != null
        && suggestion.confidence != null
        && Number.isFinite(Number(suggestion.confidence))
        && Number(suggestion.confidence) >= threshold;
    }
    return suggestion.membership != null
      && Number.isFinite(Number(suggestion.membership))
      && Number(suggestion.membership) < threshold;
  }

  return {filterSites, refId, selectMatching, suggestionKey, suggestedSelection};
}));
