"""Bounded probes for JSON TVBox configurations and standard CMS sites.

Spider/JAR sites need the TV client runtime and are deliberately reported as
unsupported rather than being mistaken for working sites.
"""

import json
import time
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from xml.etree import ElementTree


TIMEOUT = 8
MAX_CONFIG_BYTES = 2 * 1024 * 1024
MAX_MEDIA_BYTES = 256 * 1024


def fetch_limited(url, limit=MAX_CONFIG_BYTES, timeout=TIMEOUT, truncate=False):
    start = time.monotonic()
    with requests.get(url, stream=True, timeout=timeout, allow_redirects=True) as response:
        response.raise_for_status()
        chunks = []
        size = 0
        for chunk in response.iter_content(16384):
            size += len(chunk)
            if size > limit:
                if truncate:
                    chunks.append(chunk[:len(chunk) - (size - limit)])
                    break
                raise ValueError('响应超过大小限制')
            chunks.append(chunk)
        return b''.join(chunks), round((time.monotonic() - start) * 1000)


def load_config(url):
    body, latency_ms = fetch_limited(url)
    lines = body.decode('utf-8-sig').splitlines()
    # Some TVBox configurations begin with whole-line comments.
    content = '\n'.join(line for line in lines if not line.lstrip().startswith('//'))
    data = json.loads(content)
    if not isinstance(data, dict) or not isinstance(data.get('sites'), list):
        raise ValueError('不是包含 sites 列表的 TVBox JSON 配置')
    return data, latency_ms


def site_key(site):
    return str(site.get('key', '')).strip()


def cms_url(api, **params):
    parts = urlsplit(api)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query.update(params)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def first_video(data):
    if not isinstance(data, dict):
        return None
    items = data.get('list')
    if not isinstance(items, list) or not items:
        return None
    return items[0] if isinstance(items[0], dict) else None


def parse_video(body, kind):
    if kind != 0:
        return first_video(json.loads(body.decode('utf-8-sig')))
    root = ElementTree.fromstring(body)
    video = root.find('.//video')
    if video is None:
        return None
    play = video.find('.//dd')
    return {
        'vod_id': video.findtext('id'),
        'vod_play_url': play.text if play is not None else '',
    }


def first_play_url(video):
    raw = str(video.get('vod_play_url') or '')
    for group in raw.split('$$$'):
        for entry in group.split('#'):
            candidate = entry.rsplit('$', 1)[-1].strip()
            if candidate.startswith(('http://', 'https://')):
                return candidate
    return None


def media_probe(url):
    """Measure a playlist and one segment when directly accessible."""
    body, first_ms = fetch_limited(url, MAX_MEDIA_BYTES)
    if not body.lstrip().startswith(b'#EXTM3U'):
        return {'playback': 'unverified', 'play_latency_ms': first_ms}
    playlist = body.decode('utf-8-sig', errors='replace')
    lines = [line.strip() for line in playlist.splitlines() if line.strip() and not line.startswith('#')]
    if not lines:
        return {'playback': 'unverified', 'play_latency_ms': first_ms}
    next_url = urljoin(url, lines[0])
    # Master playlists point at a media playlist.
    if lines[0].lower().split('?', 1)[0].endswith('.m3u8'):
        body, _ = fetch_limited(next_url, MAX_MEDIA_BYTES)
        nested = body.decode('utf-8-sig', errors='replace')
        lines = [line.strip() for line in nested.splitlines() if line.strip() and not line.startswith('#')]
        if not lines:
            return {'playback': 'unverified', 'play_latency_ms': first_ms}
        next_url = urljoin(next_url, lines[0])
    start = time.monotonic()
    segment, _ = fetch_limited(next_url, MAX_MEDIA_BYTES, truncate=True)
    elapsed = max(time.monotonic() - start, 0.001)
    return {
        'playback': 'ok' if segment else 'failed',
        'play_latency_ms': first_ms,
        'speed_kbps': round(len(segment) * 8 / elapsed / 1000),
    }


def probe_site(site, keyword='测试'):
    """Search, detail, then optionally sample HLS for a standard CMS API."""
    api = str(site.get('api') or '').strip()
    kind = site.get('type')
    if kind not in (0, 1, 4) or not api.startswith(('http://', 'https://')):
        return {'status': 'unsupported', 'stage': 'CMS 外部探测不支持此类站点'}
    stage = 'search'
    metrics = {}
    try:
        body, search_ms = fetch_limited(cms_url(api, ac='detail', wd=keyword))
        metrics['search_ms'] = search_ms
        video = parse_video(body, kind)
        if not video:
            return {'status': 'failed', 'stage': 'search', **metrics}
        vod_id = video.get('vod_id') or video.get('id')
        if not vod_id:
            return {'status': 'failed', 'stage': 'detail', **metrics}
        stage = 'detail'
        body, detail_ms = fetch_limited(cms_url(api, ac='detail', ids=str(vod_id)))
        metrics['detail_ms'] = detail_ms
        detail = parse_video(body, kind)
        if not detail:
            return {'status': 'failed', 'stage': 'detail', **metrics}
        result = {'status': 'online', 'stage': 'detail', **metrics}
        play_url = first_play_url(detail)
        if play_url and '.m3u8' in urlsplit(play_url).path.lower():
            try:
                result.update(media_probe(play_url))
                result['stage'] = 'playback' if result['playback'] == 'ok' else 'detail'
            except (requests.RequestException, ValueError, UnicodeError):
                result['playback'] = 'failed'
        else:
            result['playback'] = 'unverified'
        return result
    except (requests.RequestException, ValueError, UnicodeError, ElementTree.ParseError) as exc:
        return {'status': 'failed', 'stage': stage, 'error': str(exc)[:180], **metrics}
