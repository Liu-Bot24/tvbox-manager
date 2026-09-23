"""Build one FongMi configuration from imported JSON configurations."""

from copy import deepcopy
import json
from urllib.parse import urljoin


def normalize_resources(config, origin_url):
    """Resolve relative config assets before serving a merged config from NAS."""
    result = deepcopy(config)
    def absolute(value):
        if isinstance(value, str) and value.startswith(('./', '../', '/')):
            return urljoin(origin_url, value)
        return value
    for field in ('spider', 'wallpaper', 'logo'):
        if field in result: result[field] = absolute(result[field])
    for site in result.get('sites') or []:
        if isinstance(site, dict):
            for field in ('api', 'ext', 'jar'):
                if field in site: site[field] = absolute(site[field])
    for field in ('parses', 'lives'):
        for item in result.get(field) or []:
            if isinstance(item, dict) and 'url' in item:
                item['url'] = absolute(item['url'])
    return result


def merge_configs(inputs, preferences=None, online_only=False):
    """inputs: iterable of (source_id, config); first config supplies global options."""
    preferences = preferences or {}
    inputs = list(inputs)
    if not inputs:
        return {'sites': [], 'parses': [], 'lives': []}
    result = deepcopy(inputs[0][1])
    result['sites'] = []
    result['parses'] = []
    result['lives'] = []
    for field in ('rules', 'hosts', 'flags'):
        result[field] = []
    used_keys = set()
    seen_parses = set()
    seen_lives = set()
    seen_extras = {'rules': set(), 'hosts': set(), 'flags': set()}
    for source_id, config in inputs:
        spider = config.get('spider')
        for site in config.get('sites') or []:
            if not isinstance(site, dict):
                continue
            original_key = str(site.get('key') or '').strip()
            if not original_key:
                continue
            pref = preferences.get((source_id, original_key), {})
            if not pref.get('enabled', True):
                continue
            if online_only and pref.get('status') != 'online':
                continue
            copy = deepcopy(site)
            if copy.get('type') == 3 and str(copy.get('api', '')).startswith('csp_') and spider and not copy.get('jar'):
                copy['jar'] = spider
            key = original_key
            if key in used_keys:
                key = f'{source_id}_{original_key}'
                suffix = 2
                while key in used_keys:
                    key = f'{source_id}_{original_key}_{suffix}'
                    suffix += 1
                copy['key'] = key
            used_keys.add(key)
            result['sites'].append(copy)
        for field, seen in (('parses', seen_parses), ('lives', seen_lives)):
            for item in config.get(field) or []:
                if not isinstance(item, dict):
                    continue
                marker = (str(item.get('name', '')), str(item.get('url', '')))
                if marker not in seen:
                    result[field].append(deepcopy(item))
                    seen.add(marker)
        for field, seen in seen_extras.items():
            for item in config.get(field) or []:
                marker = json.dumps(item, sort_keys=True, ensure_ascii=False)
                if marker not in seen:
                    result[field].append(deepcopy(item))
                    seen.add(marker)
    return result
