"""Preset site groups and TypeSafe Jev decision requests."""

import base64
import hashlib
import re

import requests
from cryptography.fernet import Fernet


PRESET_GROUPS = (
    ('综合影视', '电影、电视剧、影视资源站和普通点播内容', r'影视|影院|电影|电视剧|美剧|韩剧|短剧|追剧|剧集|点播|采集|资源|不卡|秒播|4[kK]'),
    ('动漫', '动画、番剧、二次元内容；少儿教育优先放在少儿', r'动漫|动画|番剧|二次元|[Aa]nime'),
    ('少儿', '儿童节目、启蒙动画和亲子内容', r'少儿|儿童|童趣|启蒙|兔小贝|贝乐虎'),
    ('教育课堂', '课程、学校学科、教学与公开课', r'教育|课堂|公开课|教学|小学|初中|高中|大学|知识'),
    ('听书音频', '听书、有声书、广播、电台、播客与相声戏曲', r'听书|有声|小说|广播|电台|播客|相声|小品|戏曲|\bFM\b'),
    ('音乐', '歌曲、音乐、MV、DJ 和演唱会', r'音乐|演唱会|\bMV\b|\bDJ\b'),
    ('体育直播', '体育、赛事和直播平台', r'体育|看球|足球|篮球|直播|虎牙|斗鱼'),
    ('网盘搜索', '网盘、云盘、磁力和资源搜索', r'网盘|云盘|磁力|盘搜|盘她|盘他|夸克|阿里云盘|百度网盘'),
    ('无实际意义', '广告、公告、免责声明和仅用于提示的站点', r'请勿相信|广告|免责声明|更新日期|配置接口完全免费|流量提示|公告'),
)


def preset_group_for(site):
    """High precision first-match bootstrap; unknown sites stay unclassified."""
    name = str(site.get('name') or '')
    key = str(site.get('key') or '')
    text = f'{name} {key}'
    for group_name, _, pattern in reversed(PRESET_GROUPS):
        if re.search(pattern, text, re.IGNORECASE):
            return group_name
    return None


def fernet_for_secret(secret):
    digest = hashlib.sha256(secret.encode('utf-8')).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


PROVIDERS = {
    'typesafe': ('https://api.typesafe.ai/v1/systemone', 'jev-latest'),
    'openrouter': ('https://openrouter.ai/api/alpha/decisions', 'typesafe/jev-1.13'),
}


def classify_with_jev(site, groups, current_group_id, provider, model, api_key):
    """Return group probabilities and a separate membership probability."""
    endpoint, _ = PROVIDERS[provider]
    criteria = {f'g_{group["id"]}': f'{group["name"]}：{group["description"] or group["name"]}'
                for group in groups}
    criteria['unclassified'] = '无法根据站点名称和信息可靠归入任何已有分组'
    current = next((g for g in groups if g['id'] == current_group_id), None)
    questions = {'category': {
        'type': 'choice',
        'instructions': '根据站点名称和用途，选择最贴切的一个分组；信息不足时选择 unclassified。广告、免责声明和仅作提示的条目应归入无实际意义。',
        'criteria': criteria,
    }}
    if current:
        questions['belongs'] = {
            'type': 'noul',
            'instructions': f'这个具体站点是否适合归入「{current["name"]}」？',
            'criteria': {'true': current['description'] or current['name'],
                         'false': '站点主要用途属于其他分组，或无法判断'},
        }
    state = {
        'site_name': str(site.get('name') or '')[:160],
        'site_key': str(site.get('key') or '')[:160],
        'source_name': str(site.get('source_name') or '')[:100],
        'api_type': site.get('type'),
    }
    response = requests.post(endpoint, json={'model': model, 'state': state, 'questions': questions},
                             headers={'Authorization': f'Bearer {api_key}'}, timeout=25)
    response.raise_for_status()
    body = response.json()
    answers = body.get('answers') or {}
    category = answers.get('category') or {}
    if category.get('type') != 'choice':
        raise ValueError('Jev 未返回分组选择结果')
    raw = category.get('probabilities') or {}
    if set(raw) != set(criteria):
        raise ValueError('Jev 返回的分组选项与当前配置不一致')
    probabilities = {}
    for key, value in raw.items():
        if not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError('Jev 返回了无效概率')
        probabilities[key] = float(value)
    choice = category.get('choice')
    if choice not in criteria:
        raise ValueError('Jev 返回了未知分组')
    belongs = answers.get('belongs') if current else None
    membership = None
    if current:
        if not isinstance(belongs, dict) or belongs.get('type') != 'noul':
            raise ValueError('Jev 未返回当前组归属概率')
        membership = belongs.get('noul')
        if not isinstance(membership, (int, float)) or not 0 <= membership <= 1:
            raise ValueError('Jev 返回了无效归属概率')
    return {
        'suggested_group_id': None if choice == 'unclassified' else int(choice[2:]),
        'confidence': probabilities[choice],
        'membership': float(membership) if membership is not None else None,
        'probabilities': probabilities,
    }
