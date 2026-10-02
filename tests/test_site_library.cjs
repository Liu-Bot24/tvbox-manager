const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const libraryPath = path.join(__dirname, '..', 'static', 'site-library.js');
const {filterSites, refId, selectMatching, suggestionKey, suggestedSelection} = require(libraryPath);

const sites = [
  {source_id: 1, key: 'anime-fast', name: '动漫甲', source_name: '第一来源', group_id: 4, type: 1, enabled: true, result: {status: 'online', search_ms: 300, resource_ms: 10}},
  {source_id: 1, key: 'anime-failed', name: '动漫乙', source_name: '第一来源', group_id: 4, type: 3, enabled: false, result: {status: 'failed', search_ms: null, resource_ms: 20}},
  {source_id: 2, key: 'outside-failed', name: '动漫课程', source_name: '课堂来源', group_id: 7, type: 4, enabled: true, result: {status: 'failed', search_ms: 100, resource_ms: 50}},
  {source_id: 2, key: 'empty-search', name: '动漫丙', source_name: '第二来源', group_id: 4, type: 0, enabled: true, result: {status: 'no_match', search_ms: 200, resource_ms: null}},
  {source_id: 3, key: 'script', name: '脚本丁', source_name: '第三来源', group_id: null, type: 3, enabled: false, result: {status: 'unsupported'}},
  {source_id: 3, key: 'resource', name: '脚本戊', source_name: '第三来源', group_id: null, type: 3, enabled: true, result: {status: 'resource_only', resource_ms: 5}},
  {source_id: 4, key: 'new', name: '未测己', source_name: '新来源', group_id: null, type: 1, enabled: false},
  {source_id: 4, key: 'unknown', name: '未知庚', source_name: '新来源', group_id: 4, type: 2, enabled: true, result: {status: 'future_status'}},
];
const original = JSON.stringify(sites);
for (const site of sites) {
  if (site.result) Object.freeze(site.result);
  Object.freeze(site);
}
Object.freeze(sites);

const groupResults = filterSites(sites, {group: '4', query: '动漫'});
assert.deepEqual(groupResults.map(refId), ['1:anime-fast', '1:anime-failed', '2:empty-search']);
assert.deepEqual(selectMatching(groupResults, 'failed'), ['1:anime-failed'], 'Failed selection must stay inside the already filtered group.');
assert(!selectMatching(groupResults, 'all').includes('2:outside-failed'));
assert.strictEqual(groupResults[0], sites[0], 'Filtering must preserve site identities.');
assert.notStrictEqual(filterSites(sites), sites, 'The returned list must be independently sortable.');

assert.deepEqual(filterSites(sites, {group: '4', query: '第一', enabled: 'disabled', type: 'spider', source: '1'}).map(refId), ['1:anime-failed']);
assert.deepEqual(filterSites(sites, {query: 'ANIME-FAST'}).map(refId), ['1:anime-fast']);
assert.deepEqual(filterSites(sites, {group: 'unclassified', status: 'unprobed'}).map(refId), ['4:new']);
assert.deepEqual(filterSites(sites, {status: 'resource_only'}).map(refId), ['3:resource']);
assert.deepEqual(filterSites(sites, {status: 'no_match'}).map(refId), ['2:empty-search']);
assert.deepEqual(filterSites(sites, {status: 'failed'}).map(refId), ['1:anime-failed', '2:outside-failed']);
assert(!selectMatching(sites, 'failed').includes('2:empty-search'), 'No search match is not a transport failure.');
assert(!selectMatching(sites, 'failed').includes('4:unknown'), 'Unknown statuses must not be treated as failures.');
assert.deepEqual(selectMatching(groupResults, 'cms'), ['1:anime-fast', '2:empty-search']);
assert.deepEqual(selectMatching(groupResults, 'spider'), ['1:anime-failed']);
assert.deepEqual(selectMatching(sites, 'unsupported'), ['3:script']);
assert.deepEqual(selectMatching(sites, 'invalid'), []);

assert.deepEqual(filterSites(sites, {sort: 'search'}).slice(0, 3).map(refId), ['2:outside-failed', '2:empty-search', '1:anime-fast']);
assert.deepEqual(filterSites(sites, {sort: 'resource'}).slice(0, 4).map(refId), ['3:resource', '1:anime-fast', '1:anime-failed', '2:outside-failed']);
assert.equal(filterSites(sites, {sort: 'search'}).at(-1).key, 'unknown', 'Missing measurements belong at the end.');
assert.deepEqual(filterSites(sites, {sort: 'source'}), sites);
assert.equal(JSON.stringify(sites), original, 'Filtering, sorting and selecting must not mutate source data.');

const suggestion = Object.freeze({source_id: 1, site_key: 'anime-fast', context_group_id: null, created_at: '2026-10-02T04:00:00Z', suggested_group_id: 4, confidence: 0.7, membership: 0.3});
assert.equal(suggestionKey(suggestion), '1:anime-fast:unclassified:2026-10-02T04:00:00Z');
assert.notEqual(suggestionKey({...suggestion, context_group_id: 4}), suggestionKey(suggestion));
assert.notEqual(suggestionKey({...suggestion, created_at: '2026-10-02T05:00:00Z'}), suggestionKey(suggestion));
assert.equal(suggestedSelection(suggestion, 'unclassified', 70), true);
assert.equal(suggestedSelection(suggestion, 'unclassified', 71), false);
assert.equal(suggestedSelection({...suggestion, suggested_group_id: null}, 'unclassified', 0), false);
assert.equal(suggestedSelection({...suggestion, confidence: null}, 'unclassified', 0), false);
assert.equal(suggestedSelection(suggestion, '4', 70), true);
assert.equal(suggestedSelection({...suggestion, membership: 0.7}, '4', 70), false);
assert.equal(suggestedSelection({...suggestion, membership: null}, '4', 70), false);
assert.equal(suggestedSelection({...suggestion, membership: 0}, '4', 70), true);
assert.equal(suggestedSelection(suggestion, 'all', 70), false);

const manualSelections = new Map([[suggestionKey(suggestion), false]]);
const beforeSelection = [...manualSelections];
suggestedSelection(suggestion, 'unclassified', 50);
suggestedSelection(suggestion, 'unclassified', 90);
assert.deepEqual([...manualSelections], beforeSelection, 'Threshold decisions must not overwrite a caller-owned manual review draft.');

const browserContext = {window: {}};
vm.runInNewContext(fs.readFileSync(libraryPath, 'utf8'), browserContext);
assert.equal(browserContext.window.SiteLibrary.refId(sites[0]), '1:anime-fast', 'The same module must load as a browser global.');
console.log('Site library checks passed.');
