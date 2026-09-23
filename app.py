import os
import sqlite3
import json
import secrets
import concurrent.futures
import requests
import logging
import time
from datetime import datetime, timezone, timedelta
from functools import wraps
from contextlib import contextmanager
from flask import Flask, request, session, redirect, url_for, render_template, jsonify, Response
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix
import urllib3
from site_probe import load_config, probe_site, site_key
from merge_config import merge_configs, normalize_resources
from grouping import PRESET_GROUPS, PROVIDERS, preset_group_for, fernet_for_secret, classify_with_jev

# 禁用不安全请求警告（针对 verify=False）
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Initialization & Logging ---
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1, x_prefix=1)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Configuration & Constants ---
APP_VERSION = "v1.0.41"
app.secret_key = os.environ.get('SECRET_KEY', 'super-secret-starlink-clone-key')
app.permanent_session_lifetime = timedelta(days=30)
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')
DATABASE = os.environ.get('DB_PATH', '/app/data/database.db')
REG_CODE = os.environ.get('REG_CODE', '888888')
BASE_URL = os.environ.get('BASE_URL', '').rstrip('/')
HTTP_TIMEOUT = 8

# 全局 HTTP 会话
http_session = requests.Session()
http_session.headers.update({
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
})

@app.errorhandler(Exception)
def handle_exception(e):
    """全局意外错误处理"""
    logger.error(f"Unhandled Exception: {str(e)}", exc_info=True)
    if request.path.startswith('/api/'):
        return jsonify({'status': 'error', 'message': '服务器内部错误'}), 500
    return "系统异常，请稍后再试", 500

# --- Database Layer ---
@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    """初始化数据库并建立索引"""
    with get_db() as db:
        # 用户表
        db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_admin INTEGER DEFAULT 0
            )
        ''')
        
        # 接口数据表
        db.execute('''
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                url TEXT NOT NULL,
                type TEXT DEFAULT 'site',
                status TEXT DEFAULT 'unknown',
                order_index INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (id)
            )
        ''')
        
        # 系统设置表
        db.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')

        # 建立索引以提升大规模数据下的性能
        db.execute('CREATE INDEX IF NOT EXISTS idx_sources_user ON sources(user_id)')
        db.execute('CREATE INDEX IF NOT EXISTS idx_sources_url ON sources(url)')
        db.execute('CREATE INDEX IF NOT EXISTS idx_sources_order ON sources(order_index)')
        columns = {row['name'] for row in db.execute('PRAGMA table_info(sources)')}
        for name, definition in (
            ('enabled', 'INTEGER NOT NULL DEFAULT 1'),
            ('latency_ms', 'INTEGER'),
            ('checked_at', 'TEXT'),
        ):
            if name not in columns:
                db.execute(f'ALTER TABLE sources ADD COLUMN {name} {definition}')
        db.execute('''CREATE TABLE IF NOT EXISTS source_configs (
            source_id INTEGER PRIMARY KEY, body TEXT NOT NULL, fetched_at TEXT NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS site_preferences (
            source_id INTEGER NOT NULL, site_key TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1, result TEXT, checked_at TEXT,
            PRIMARY KEY (source_id, site_key)
        )''')
        preference_columns = {row['name'] for row in db.execute('PRAGMA table_info(site_preferences)')}
        if 'group_id' not in preference_columns:
            db.execute('ALTER TABLE site_preferences ADD COLUMN group_id INTEGER')
        db.execute('''CREATE TABLE IF NOT EXISTS site_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
            name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            order_index INTEGER NOT NULL DEFAULT 0,
            UNIQUE(user_id, name)
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS group_bootstrap (
            user_id INTEGER PRIMARY KEY, created_at TEXT NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS model_settings (
            user_id INTEGER PRIMARY KEY, provider TEXT NOT NULL,
            model TEXT NOT NULL, encrypted_key TEXT NOT NULL
        )''')
        db.execute('''CREATE TABLE IF NOT EXISTS group_suggestions (
            user_id INTEGER NOT NULL, source_id INTEGER NOT NULL, site_key TEXT NOT NULL,
            context_group_id INTEGER, suggested_group_id INTEGER,
            confidence REAL NOT NULL, membership REAL,
            probabilities TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY (user_id, source_id, site_key)
        )''')

        # 初始化默认配置
        db.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('invite_code', REG_CODE))
        db.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', ('webhook_token', secrets.token_hex(16)))
        
        db.commit()

# Ensure DB is initialized
init_db()

# --- Helpers & Decorators ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('is_admin'):
            if request.path.startswith('/api/'):
                return jsonify({'status': 'error', 'message': '需要管理员权限'}), 403
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def jsonify_success(message='操作成功', **kwargs):
    return jsonify({'status': 'success', 'message': message, **kwargs})

def jsonify_error(message='操作失败', code=200):
    return jsonify({'status': 'error', 'message': message}), code


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def source_for_user(db, source_id, user_id):
    return db.execute('SELECT * FROM sources WHERE id = ? AND user_id = ?', (source_id, user_id)).fetchone()


def refresh_config(db, source):
    config, latency_ms = load_config(source['url'])
    config = normalize_resources(config, source['url'])
    body = json.dumps(config, ensure_ascii=False)
    db.execute('INSERT OR REPLACE INTO source_configs (source_id, body, fetched_at) VALUES (?, ?, ?)',
               (source['id'], body, utc_now()))
    db.execute('UPDATE sources SET status = ?, latency_ms = ?, checked_at = ? WHERE id = ?',
               ('online', latency_ms, utc_now(), source['id']))
    db.execute('UPDATE site_preferences SET result = NULL, checked_at = NULL WHERE source_id = ?', (source['id'],))
    db.execute('DELETE FROM group_suggestions WHERE source_id = ?', (source['id'],))
    return config


def cached_config(db, source):
    row = db.execute('SELECT body FROM source_configs WHERE source_id = ?', (source['id'],)).fetchone()
    return json.loads(row['body']) if row else refresh_config(db, source)


def ensure_groups_bootstrapped(db, user_id):
    if db.execute('SELECT 1 FROM group_bootstrap WHERE user_id = ?', (user_id,)).fetchone():
        return
    for index, (name, description, _) in enumerate(PRESET_GROUPS):
        db.execute('''INSERT OR IGNORE INTO site_groups (user_id, name, description, order_index)
            VALUES (?, ?, ?, ?)''', (user_id, name, description, index))
    groups_by_name = {row['name']: row['id'] for row in db.execute(
        'SELECT id, name FROM site_groups WHERE user_id = ?', (user_id,))}
    sources = db.execute("SELECT * FROM sources WHERE user_id = ? AND type = 'site'", (user_id,)).fetchall()
    for source in sources:
        try:
            config = cached_config(db, source)
        except (requests.RequestException, ValueError, UnicodeError):
            continue
        for site in config.get('sites') or []:
            if not isinstance(site, dict) or not site_key(site):
                continue
            name = preset_group_for(site)
            if name:
                db.execute('''INSERT INTO site_preferences (source_id, site_key, group_id)
                    VALUES (?, ?, ?) ON CONFLICT(source_id, site_key)
                    DO UPDATE SET group_id = COALESCE(site_preferences.group_id, excluded.group_id)''',
                    (source['id'], site_key(site), groups_by_name[name]))
    db.execute('INSERT INTO group_bootstrap (user_id, created_at) VALUES (?, ?)', (user_id, utc_now()))


def owned_site_refs(db, user_id, refs):
    if not isinstance(refs, list) or not 1 <= len(refs) <= 2000:
        raise ValueError('请选择 1 至 2000 个站点')
    sources = {row['id']: row for row in db.execute(
        "SELECT * FROM sources WHERE user_id = ? AND type = 'site'", (user_id,))}
    valid_keys = {}
    answer = []
    seen = set()
    for ref in refs:
        if not isinstance(ref, dict) or not isinstance(ref.get('source_id'), int) or not isinstance(ref.get('key'), str):
            raise ValueError('站点参数错误')
        source_id, key = ref['source_id'], ref['key']
        if source_id not in sources or not key:
            raise ValueError('站点不属于当前用户')
        if source_id not in valid_keys:
            config = cached_config(db, sources[source_id])
            valid_keys[source_id] = {site_key(site) for site in config.get('sites') or [] if isinstance(site, dict)}
        if key not in valid_keys[source_id]:
            raise ValueError('站点不存在')
        if (source_id, key) not in seen:
            answer.append((source_id, key))
            seen.add((source_id, key))
    return answer


def group_for_user(db, user_id, group_id):
    if group_id is None:
        return None
    if not isinstance(group_id, int):
        raise ValueError('分组参数错误')
    group = db.execute('SELECT * FROM site_groups WHERE id = ? AND user_id = ?',
                       (group_id, user_id)).fetchone()
    if not group:
        raise ValueError('分组不存在')
    return group

def parse_aggregate_source(url):
    """尝试解析并解构多仓 JSON"""
    try:
        # 只针对可能是 JSON 的 URL 进行尝试
        if not ('urls' in url or url.endswith('.json')):
            return None
            
        r = http_session.get(url, timeout=HTTP_TIMEOUT, verify=False)
        if r.status_code != 200:
            return None
            
        content = r.text.strip()
        # 兼容性处理：剔除特殊注释
        if content.startswith('//'):
            content = '\n'.join([l for l in content.split('\n') if not l.strip().startswith('//')])

        json_data = json.loads(content)
        if isinstance(json_data, dict) and "urls" in json_data and isinstance(json_data["urls"], list):
            return json_data["urls"]
    except:
        pass
    return None

@app.context_processor
def inject_version():
    return dict(version=APP_VERSION)

# --- Frontend Routes ---
@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session else url_for('login'))

@app.route('/login')
def login():
    if 'user_id' in session: return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/register')
def register():
    if 'user_id' in session: return redirect(url_for('dashboard'))
    return render_template('register.html')

@app.route('/dashboard')
@login_required
def dashboard():
    username = session.get('username')
    is_admin = session.get('is_admin', False)
    host_url = BASE_URL if BASE_URL else request.host_url.rstrip('/')
    sub_url = host_url + url_for('get_tvbox_json', username=username)
    return render_template('dashboard.html', username=username, sub_url=sub_url, is_admin=is_admin)

@app.route('/admin')
@admin_required
def admin_dashboard():
    with get_db() as db:
        invite_code = db.execute("SELECT value FROM settings WHERE key = 'invite_code'").fetchone()['value']
        webhook_token = db.execute("SELECT value FROM settings WHERE key = 'webhook_token'").fetchone()['value']
    return render_template('admin.html', is_admin=True, invite_code=invite_code, webhook_token=webhook_token)

# --- Auth APIs ---
@app.route('/api/auth/register', methods=['POST'])
def api_register():
    data = request.json
    username, password, invite_code = data.get('username', '').strip(), data.get('password', ''), data.get('invite_code', '').strip()

    if not username or not password: return jsonify_error('用户名和密码不能为空')

    with get_db() as db:
        expected_code = db.execute("SELECT value FROM settings WHERE key = 'invite_code'").fetchone()['value']
        if invite_code != expected_code: return jsonify_error('邀请码错误')

        try:
            user_count = db.execute("SELECT COUNT(id) as c FROM users").fetchone()['c']
            is_admin = 1 if user_count == 0 else 0
            db.execute('INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)', 
                       (username, generate_password_hash(password, method='pbkdf2:sha256'), is_admin))
            db.commit()
            return jsonify_success('注册成功')
        except sqlite3.IntegrityError:
            return jsonify_error('用户名已存在')

@app.route('/api/auth/login', methods=['POST'])
def api_login():
    data = request.json
    username, password = data.get('username', '').strip(), data.get('password', '')

    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE username = ?', (username,)).fetchone()
        
    if user and check_password_hash(user['password_hash'], password):
        session.update({'user_id': user['id'], 'username': user['username'], 'is_admin': user['is_admin'] == 1})
        session.permanent = bool(data.get('remember', False))
        return jsonify_success('登录成功')
    return jsonify_error('用户名或密码错误')

@app.route('/api/auth/logout')
def api_logout():
    session.clear()
    return redirect(url_for('login'))

# --- Source Management APIs ---
@app.route('/api/source/list')
@login_required
def api_source_list():
    with get_db() as db:
        sources = db.execute('SELECT * FROM sources WHERE user_id = ? ORDER BY order_index ASC, id ASC', (session['user_id'],)).fetchall()
        return jsonify_success(data=[dict(row) for row in sources])


@app.route('/api/site/list')
@login_required
def api_all_sites():
    with get_db() as db:
        sources = db.execute('''SELECT * FROM sources WHERE user_id = ? AND type = 'site'
            ORDER BY order_index ASC, id ASC''', (session['user_id'],)).fetchall()
        preferences = {}
        for row in db.execute('''SELECT p.* FROM site_preferences p
            JOIN sources s ON s.id = p.source_id WHERE s.user_id = ?''', (session['user_id'],)):
            preferences[(row['source_id'], row['site_key'])] = dict(row)
        rows = []
        errors = []
        for source in sources:
            try:
                config = cached_config(db, source)
            except (requests.RequestException, ValueError, UnicodeError) as exc:
                errors.append({'source': source['name'], 'message': str(exc)[:160]})
                continue
            for site in config['sites']:
                if not isinstance(site, dict) or not site_key(site): continue
                key = site_key(site)
                pref = preferences.get((source['id'], key), {})
                rows.append({
                    'source_id': source['id'], 'source_name': source['name'],
                    'key': key, 'name': site.get('name') or key,
                    'type': site.get('type'), 'api': site.get('api', ''),
                    'enabled': bool(pref.get('enabled', 1)),
                    'group_id': pref.get('group_id'),
                    'result': json.loads(pref['result']) if pref.get('result') else None,
                    'checked_at': pref.get('checked_at'),
                })
        return jsonify_success(data=rows, errors=errors)

@app.route('/api/source/add', methods=['POST'])
@login_required
def api_source_add():
    user_id = session['user_id']
    data = request.json
    name, url, stype = data.get('name', '').strip(), data.get('url', '').strip(), data.get('type', 'site')

    if not name or not url: return jsonify_error('名称和 URL 不能为空')

    # 1. 尝试作为多仓 JSON 解析
    aggregate_urls = parse_aggregate_source(url)
    if aggregate_urls:
        added = 0
        with get_db() as db:
            existens = {row['url'] for row in db.execute('SELECT url FROM sources WHERE user_id = ?', (user_id,)).fetchall()}
            for entry in aggregate_urls:
                e_url = entry.get('url', '').strip()
                if e_url and e_url not in existens:
                    db.execute('INSERT INTO sources (user_id, name, url, type) VALUES (?, ?, ?, ?)', 
                               (user_id, entry.get('name', '未命名'), e_url, stype))
                    existens.add(e_url)
                    added += 1
            db.commit()
        return jsonify_success(f'成功导入 {added} 个聚合接口')

    # 2. 单个添加模式
    with get_db() as db:
        if db.execute('SELECT id FROM sources WHERE user_id = ? AND url = ?', (user_id, url)).fetchone():
            return jsonify_error('该接口已在您的列表中')
        db.execute('INSERT INTO sources (user_id, name, url, type) VALUES (?, ?, ?, ?)', (user_id, name, url, stype))
        db.commit()
    return jsonify_success('添加成功')

@app.route('/api/source/batch_add', methods=['POST'])
@login_required
def api_source_batch_add():
    user_id, items = session['user_id'], request.json.get('items', [])
    if not items: return jsonify_error('未选择任何接口')
    
    with get_db() as db:
        max_order = (db.execute('SELECT MAX(order_index) FROM sources WHERE user_id = ?', (user_id,)).fetchone()[0] or 0)
        for i, item in enumerate(items):
            link = item.get('link') or item.get('url')
            if link:
                db.execute('INSERT INTO sources (user_id, name, url, type, order_index) VALUES (?, ?, ?, ?, ?)', 
                           (user_id, item.get('name', '未命名'), link, 'site', max_order + i + 1))
        db.commit()
    return jsonify_success(f'成功批量添加 {len(items)} 个接口')

@app.route('/api/source/update', methods=['POST'])
@login_required
def api_source_update():
    data = request.json
    sid, name, url, stype = data.get('id'), data.get('name', '').strip(), data.get('url', '').strip(), data.get('type', 'site')
    if not sid or not name or not url: return jsonify_error('参数不全')

    with get_db() as db:
        old = source_for_user(db, sid, session['user_id'])
        if not old: return jsonify_error('接口不存在', 404)
        db.execute('UPDATE sources SET name = ?, url = ?, type = ? WHERE id = ? AND user_id = ?', 
                   (name, url, stype, sid, session['user_id']))
        if old['url'] != url:
            db.execute('DELETE FROM source_configs WHERE source_id = ?', (sid,))
            db.execute('DELETE FROM site_preferences WHERE source_id = ?', (sid,))
            db.execute('DELETE FROM group_suggestions WHERE source_id = ?', (sid,))
            db.execute('UPDATE sources SET status = ?, latency_ms = NULL, checked_at = NULL WHERE id = ?', ('unknown', sid))
        db.commit()
    return jsonify_success('保存成功')

@app.route('/api/source/delete', methods=['POST'])
@login_required
def api_source_delete():
    source_id = request.json.get('id')
    with get_db() as db:
        db.execute('DELETE FROM source_configs WHERE source_id = ? AND source_id IN (SELECT id FROM sources WHERE user_id = ?)', (source_id, session['user_id']))
        db.execute('DELETE FROM site_preferences WHERE source_id = ? AND source_id IN (SELECT id FROM sources WHERE user_id = ?)', (source_id, session['user_id']))
        db.execute('DELETE FROM group_suggestions WHERE source_id = ? AND user_id = ?', (source_id, session['user_id']))
        db.execute('DELETE FROM sources WHERE id = ? AND user_id = ?', (source_id, session['user_id']))
        db.commit()
    return jsonify_success('删除成功')

@app.route('/api/source/reorder', methods=['POST'])
@login_required
def api_source_reorder():
    order_data = request.json.get('order', [])
    with get_db() as db:
        for idx, sid in enumerate(order_data):
            db.execute('UPDATE sources SET order_index = ? WHERE id = ? AND user_id = ?', (idx, sid, session['user_id']))
        db.commit()
    return jsonify_success('排序已保存')


@app.route('/api/source/enable', methods=['POST'])
@login_required
def api_source_enable():
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get('enabled'), bool): return jsonify_error('参数错误', 400)
    with get_db() as db:
        source = source_for_user(db, data.get('id'), session['user_id'])
        if not source: return jsonify_error('接口不存在', 404)
        db.execute('UPDATE sources SET enabled = ? WHERE id = ?', (int(data['enabled']), source['id']))
    return jsonify_success()

@app.route('/api/source/check', methods=['POST'])
@login_required
def api_source_check():
    url, sid = request.json.get('url'), request.json.get('id')
    if sid:
        with get_db() as db:
            source = source_for_user(db, sid, session['user_id'])
            if not source: return jsonify_error('接口不存在', 404)
            url = source['url']
    if not url: return jsonify_error('URL missing')
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
    }
    
    try:
        start = time.monotonic()
        # 重归原生 requests.get 以提升对海量异构站点的兼容性，增加超时至 10s
        r = requests.get(url, timeout=10, stream=True, verify=False, headers=headers)
        r.close()
        latency_ms = round((time.monotonic() - start) * 1000)
        status = 'online' if r.status_code < 400 else 'offline'
        if sid:
            with get_db() as db:
                db.execute('UPDATE sources SET status = ?, latency_ms = ?, checked_at = ? WHERE id = ? AND user_id = ?', (status, latency_ms, utc_now(), sid, session['user_id']))
                db.commit()
        return jsonify_success(status_val=status, code=r.status_code, latency_ms=latency_ms)
    except Exception as e:
        logger.error(f"Check failed for {url}: {str(e)}")
        if sid:
            with get_db() as db:
                db.execute('UPDATE sources SET status = ?, latency_ms = NULL, checked_at = ? WHERE id = ? AND user_id = ?', ('offline', utc_now(), sid, session['user_id']))
                db.commit()
        return jsonify_error(str(e))


@app.route('/api/source/<int:source_id>/sites')
@login_required
def api_source_sites(source_id):
    refresh = request.args.get('refresh') == 'true'
    with get_db() as db:
        source = source_for_user(db, source_id, session['user_id'])
        if not source: return jsonify_error('接口不存在', 404)
        try:
            config = refresh_config(db, source) if refresh else cached_config(db, source)
        except (requests.RequestException, ValueError, UnicodeError) as exc:
            db.execute('UPDATE sources SET status = ?, checked_at = ? WHERE id = ?', ('offline', utc_now(), source_id))
            return jsonify_error(f'配置读取失败：{exc}', 502)
        prefs = {row['site_key']: dict(row) for row in db.execute(
            'SELECT * FROM site_preferences WHERE source_id = ?', (source_id,))}
        sites = []
        for site in config['sites']:
            if not isinstance(site, dict) or not site_key(site): continue
            pref = prefs.get(site_key(site), {})
            sites.append({
                'key': site_key(site), 'name': site.get('name') or site_key(site),
                'type': site.get('type'), 'api': site.get('api', ''),
                'enabled': bool(pref.get('enabled', 1)),
                'result': json.loads(pref['result']) if pref.get('result') else None,
                'checked_at': pref.get('checked_at'),
            })
        return jsonify_success(data=sites)


@app.route('/api/site/enable', methods=['POST'])
@login_required
def api_site_enable():
    data = request.get_json(silent=True) or {}
    source_id, key, enabled = data.get('source_id'), data.get('key'), data.get('enabled')
    if not isinstance(key, str) or not key or not isinstance(enabled, bool):
        return jsonify_error('参数错误', 400)
    with get_db() as db:
        source = source_for_user(db, source_id, session['user_id'])
        if not source: return jsonify_error('接口不存在', 404)
        try:
            config = cached_config(db, source)
        except (requests.RequestException, ValueError, UnicodeError) as exc:
            return jsonify_error(f'配置读取失败：{exc}', 502)
        if key not in {site_key(site) for site in config['sites'] if isinstance(site, dict)}:
            return jsonify_error('站点不存在', 404)
        db.execute('''INSERT INTO site_preferences (source_id, site_key, enabled) VALUES (?, ?, ?)
            ON CONFLICT(source_id, site_key) DO UPDATE SET enabled = excluded.enabled''',
            (source_id, key, int(enabled)))
    return jsonify_success()


@app.route('/api/site/batch_enable', methods=['POST'])
@login_required
def api_site_batch_enable():
    data = request.get_json(silent=True) or {}
    if not isinstance(data.get('enabled'), bool):
        return jsonify_error('启用状态无效', 400)
    with get_db() as db:
        try:
            refs = owned_site_refs(db, session['user_id'], data.get('sites'))
        except (ValueError, requests.RequestException, UnicodeError) as exc:
            return jsonify_error(str(exc), 400)
        for source_id, key in refs:
            db.execute('''INSERT INTO site_preferences (source_id, site_key, enabled) VALUES (?, ?, ?)
                ON CONFLICT(source_id, site_key) DO UPDATE SET enabled = excluded.enabled''',
                (source_id, key, int(data['enabled'])))
    return jsonify_success(f'已更新 {len(refs)} 个站点', count=len(refs))


@app.route('/api/group/list')
@login_required
def api_group_list():
    with get_db() as db:
        ensure_groups_bootstrapped(db, session['user_id'])
        groups = [dict(row) for row in db.execute('''SELECT id, name, description, order_index
            FROM site_groups WHERE user_id = ? ORDER BY order_index, id''', (session['user_id'],))]
    return jsonify_success(data=groups)


@app.route('/api/group/add', methods=['POST'])
@login_required
def api_group_add():
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()[:40]
    description = str(data.get('description') or '').strip()[:240]
    if not name:
        return jsonify_error('请输入分组名称', 400)
    with get_db() as db:
        ensure_groups_bootstrapped(db, session['user_id'])
        order = db.execute('SELECT COALESCE(MAX(order_index), -1) + 1 FROM site_groups WHERE user_id = ?',
                           (session['user_id'],)).fetchone()[0]
        try:
            cursor = db.execute('''INSERT INTO site_groups (user_id, name, description, order_index)
                VALUES (?, ?, ?, ?)''', (session['user_id'], name, description, order))
        except sqlite3.IntegrityError:
            return jsonify_error('分组名称已存在', 409)
        db.execute('DELETE FROM group_suggestions WHERE user_id = ?', (session['user_id'],))
    return jsonify_success(group_id=cursor.lastrowid)


@app.route('/api/group/update', methods=['POST'])
@login_required
def api_group_update():
    data = request.get_json(silent=True) or {}
    name = str(data.get('name') or '').strip()[:40]
    description = str(data.get('description') or '').strip()[:240]
    if not name:
        return jsonify_error('请输入分组名称', 400)
    with get_db() as db:
        try:
            group = group_for_user(db, session['user_id'], data.get('id'))
        except ValueError as exc:
            return jsonify_error(str(exc), 404)
        try:
            db.execute('UPDATE site_groups SET name = ?, description = ? WHERE id = ?',
                       (name, description, group['id']))
        except sqlite3.IntegrityError:
            return jsonify_error('分组名称已存在', 409)
        db.execute('DELETE FROM group_suggestions WHERE user_id = ?', (session['user_id'],))
    return jsonify_success()


@app.route('/api/group/delete', methods=['POST'])
@login_required
def api_group_delete():
    data = request.get_json(silent=True) or {}
    with get_db() as db:
        try:
            group = group_for_user(db, session['user_id'], data.get('id'))
        except ValueError as exc:
            return jsonify_error(str(exc), 404)
        db.execute('''UPDATE site_preferences SET group_id = NULL
            WHERE group_id = ? AND source_id IN (SELECT id FROM sources WHERE user_id = ?)''',
            (group['id'], session['user_id']))
        db.execute('DELETE FROM site_groups WHERE id = ?', (group['id'],))
        db.execute('DELETE FROM group_suggestions WHERE user_id = ?', (session['user_id'],))
    return jsonify_success('分组已删除，站点已移至未分类')


@app.route('/api/group/assign', methods=['POST'])
@login_required
def api_group_assign():
    data = request.get_json(silent=True) or {}
    with get_db() as db:
        try:
            group = group_for_user(db, session['user_id'], data.get('group_id'))
            refs = owned_site_refs(db, session['user_id'], data.get('sites'))
        except (ValueError, requests.RequestException, UnicodeError) as exc:
            return jsonify_error(str(exc), 400)
        group_id = group['id'] if group else None
        for source_id, key in refs:
            db.execute('''INSERT INTO site_preferences (source_id, site_key, group_id)
                VALUES (?, ?, ?) ON CONFLICT(source_id, site_key)
                DO UPDATE SET group_id = excluded.group_id''', (source_id, key, group_id))
            db.execute('''DELETE FROM group_suggestions WHERE user_id = ? AND source_id = ? AND site_key = ?''',
                       (session['user_id'], source_id, key))
    return jsonify_success(f'已移动 {len(refs)} 个站点', count=len(refs))


@app.route('/api/model/settings', methods=['GET', 'POST'])
@login_required
def api_model_settings():
    user_id = session['user_id']
    if request.method == 'GET':
        with get_db() as db:
            row = db.execute('SELECT provider, model FROM model_settings WHERE user_id = ?',
                             (user_id,)).fetchone()
        return jsonify_success(data={'configured': bool(row),
                                     'provider': row['provider'] if row else 'openrouter',
                                     'model': row['model'] if row else PROVIDERS['openrouter'][1]})
    data = request.get_json(silent=True) or {}
    provider = data.get('provider')
    if provider not in PROVIDERS:
        return jsonify_error('不支持的模型服务', 400)
    model = str(data.get('model') or '').strip()[:80] or PROVIDERS[provider][1]
    key = str(data.get('api_key') or '').strip()
    with get_db() as db:
        existing = db.execute('SELECT provider, encrypted_key FROM model_settings WHERE user_id = ?',
                              (user_id,)).fetchone()
        if not key and not existing:
            return jsonify_error('请输入 API Key', 400)
        if not key and existing['provider'] != provider:
            return jsonify_error('切换模型服务时需要输入对应服务的 API Key', 400)
        encrypted = fernet_for_secret(app.secret_key).encrypt(key.encode()).decode() if key else existing['encrypted_key']
        db.execute('''INSERT INTO model_settings (user_id, provider, model, encrypted_key)
            VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET
            provider = excluded.provider, model = excluded.model,
            encrypted_key = excluded.encrypted_key''', (user_id, provider, model, encrypted))
    return jsonify_success('模型接入设置已保存')


@app.route('/api/group/suggestions')
@login_required
def api_group_suggestions():
    raw = request.args.get('group_id', 'unclassified')
    try:
        group_id = None if raw == 'unclassified' else int(raw)
    except ValueError:
        return jsonify_error('分组参数错误', 400)
    with get_db() as db:
        try:
            group_for_user(db, session['user_id'], group_id)
        except ValueError as exc:
            return jsonify_error(str(exc), 404)
        rows = db.execute('''SELECT g.* FROM group_suggestions g
            JOIN sources s ON s.id = g.source_id
            LEFT JOIN site_preferences p ON p.source_id = g.source_id AND p.site_key = g.site_key
            WHERE g.user_id = ? AND s.user_id = ?
              AND g.context_group_id IS ? AND p.group_id IS ?
            ORDER BY g.created_at DESC, g.source_id, g.site_key''',
            (session['user_id'], session['user_id'], group_id, group_id)).fetchall()
    return jsonify_success(data=[{**dict(row), 'probabilities': json.loads(row['probabilities'])}
                                 for row in rows])


@app.route('/api/group/analyze', methods=['POST'])
@login_required
def api_group_analyze():
    data = request.get_json(silent=True) or {}
    group_id = data.get('group_id')
    with get_db() as db:
        ensure_groups_bootstrapped(db, session['user_id'])
        try:
            group_for_user(db, session['user_id'], group_id)
            refs = owned_site_refs(db, session['user_id'], data.get('sites'))
        except (ValueError, requests.RequestException, UnicodeError) as exc:
            return jsonify_error(str(exc), 400)
        if len(refs) > 25:
            return jsonify_error('每批最多分析 25 个站点', 400)
        settings = db.execute('SELECT * FROM model_settings WHERE user_id = ?',
                              (session['user_id'],)).fetchone()
        if not settings:
            return jsonify_error('请先配置 Jev API Key', 400)
        try:
            api_key = fernet_for_secret(app.secret_key).decrypt(settings['encrypted_key'].encode()).decode()
        except Exception:
            return jsonify_error('模型密钥无法解密，请重新保存 API Key', 500)
        groups = [dict(row) for row in db.execute('''SELECT id, name, description
            FROM site_groups WHERE user_id = ? ORDER BY order_index, id''', (session['user_id'],))]
        source_map = {row['id']: row for row in db.execute(
            "SELECT * FROM sources WHERE user_id = ? AND type = 'site'", (session['user_id'],))}
        config_maps = {}
        config_bodies = {}
        selected = []
        for source_id, key in refs:
            current = db.execute('SELECT group_id FROM site_preferences WHERE source_id = ? AND site_key = ?',
                                 (source_id, key)).fetchone()
            if (current['group_id'] if current else None) != group_id:
                return jsonify_error('站点已不在当前分组，请刷新列表', 409)
            if source_id not in config_maps:
                config_maps[source_id] = {site_key(site): site for site in cached_config(db, source_map[source_id])['sites']
                                          if isinstance(site, dict)}
                config_bodies[source_id] = db.execute(
                    'SELECT body FROM source_configs WHERE source_id = ?', (source_id,)).fetchone()['body']
            site = config_maps[source_id][key]
            selected.append((source_id, key, {
                'name': site.get('name') or key, 'key': key, 'type': site.get('type'),
                'source_name': source_map[source_id]['name']}))
        provider, model = settings['provider'], settings['model']
    def classify(item):
        source_id, key, site = item
        try:
            result = classify_with_jev(site, groups, group_id, provider, model, api_key)
            return (source_id, key, result, None)
        except (requests.RequestException, ValueError, KeyError) as exc:
            return (source_id, key, None, str(exc)[:160])
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        outcomes = list(executor.map(classify, selected))
    results, errors = [], []
    with get_db() as db:
        for source_id, key, result, error in outcomes:
            if error:
                errors.append({'source_id': source_id, 'key': key, 'message': error})
                continue
            current = db.execute('''SELECT p.group_id FROM site_preferences p
                JOIN sources s ON s.id = p.source_id
                WHERE p.source_id = ? AND p.site_key = ? AND s.user_id = ?''',
                (source_id, key, session['user_id'])).fetchone()
            if (current['group_id'] if current else None) != group_id:
                errors.append({'source_id': source_id, 'key': key, 'message': '站点已移组，建议未保存'})
                continue
            if not source_for_user(db, source_id, session['user_id']):
                errors.append({'source_id': source_id, 'key': key, 'message': '配置来源已移除'})
                continue
            latest_config = db.execute('SELECT body FROM source_configs WHERE source_id = ?',
                                       (source_id,)).fetchone()
            if not latest_config or latest_config['body'] != config_bodies[source_id]:
                errors.append({'source_id': source_id, 'key': key, 'message': '原配置已更新，建议未保存'})
                continue
            db.execute('''INSERT INTO group_suggestions
                (user_id, source_id, site_key, context_group_id, suggested_group_id,
                 confidence, membership, probabilities, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, source_id, site_key) DO UPDATE SET
                context_group_id = excluded.context_group_id,
                suggested_group_id = excluded.suggested_group_id,
                confidence = excluded.confidence, membership = excluded.membership,
                probabilities = excluded.probabilities, created_at = excluded.created_at''',
                (session['user_id'], source_id, key, group_id, result['suggested_group_id'],
                 result['confidence'], result['membership'],
                 json.dumps(result['probabilities']), utc_now()))
            results.append({'source_id': source_id, 'key': key, **result})
    return jsonify_success(data=results, errors=errors)


@app.route('/api/site/probe', methods=['POST'])
@login_required
def api_site_probe():
    data = request.get_json(silent=True) or {}
    source_id, key = data.get('source_id'), data.get('key')
    keyword = str(data.get('keyword') or '庆余年').strip()[:40]
    if not keyword: return jsonify_error('请输入搜索词', 400)
    with get_db() as db:
        source = source_for_user(db, source_id, session['user_id'])
        if not source: return jsonify_error('接口不存在', 404)
        try:
            config = cached_config(db, source)
        except (requests.RequestException, ValueError, UnicodeError) as exc:
            return jsonify_error(f'配置读取失败：{exc}', 502)
        site = next((item for item in config['sites'] if isinstance(item, dict) and site_key(item) == key), None)
        if not site: return jsonify_error('站点不存在', 404)
        result = probe_site(site, keyword)
        db.execute('''INSERT INTO site_preferences (source_id, site_key, result, checked_at)
            VALUES (?, ?, ?, ?) ON CONFLICT(source_id, site_key)
            DO UPDATE SET result = excluded.result, checked_at = excluded.checked_at''',
            (source_id, key, json.dumps(result, ensure_ascii=False), utc_now()))
    return jsonify_success(data=result)

# --- Recommendation & External APIs ---
@app.route('/api/external/aipan')
@login_required
def api_external_aipan():
    combined_list = []
    # 1. 本地推荐 (Webhook 注入)
    for path in ['/app/data/recommended.json', 'data/recommended.json']:
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    combined_list.extend(json.load(f).get('list', []))
            except: pass
            break
    
    # 2. 爱盼接口推荐
    try:
        r = http_session.get('https://www.aipan.me/api/tvbox', timeout=5, verify=False)
        if r.status_code == 200: combined_list.extend(r.json().get('list', []))
    except: pass

    # 3. 兜底列表
    if not combined_list:
        combined_list = [
            {"name": "🌟 饭太硬", "link": "http://饭太硬.top/tv"},
            {"name": "🐱 肥猫", "link": "http://肥猫.com"},
            {"name": "🐉 道长", "link": "https://pastebin.com/raw/5NHaxyO7"},
            {"name": "💨 南风", "link": "https://m3u8.xn--nwy97m.cn/nanfeng/lite.json"},
            {"name": "📦 荷城茶秀", "link": "http://rihou.cc:88/荷城茶秀"},
            {"name": "📱 小米", "link": "http://xhww.fun:63/小米/DEMO.json"},
            {"name": "🚀 欧歌", "link": "http://tv.nxog.top/m"},
            {"name": "🐼 熊猫", "link": "https://jihulab.com/yw88075/tvbox/-/raw/main/tv/tv.json"},
        ]

    # 去重处理
    with get_db() as db:
        existens = {row['url'] for row in db.execute('SELECT url FROM sources WHERE user_id = ?', (session['user_id'],)).fetchall()}
    
    final_list, seen = [], set()
    for item in combined_list:
        link = item.get('link') or item.get('url')
        if link and link not in seen and link not in existens:
            final_list.append(item)
            seen.add(link)
    
    return jsonify_success(data=final_list[:40])

# --- Admin & Webhook APIs ---
@app.route('/api/admin/settings/code', methods=['POST'])
@admin_required
def api_admin_update_code():
    new_code = request.json.get('code', '').strip()
    if not new_code: return jsonify_error('不能为空')
    with get_db() as db:
        db.execute("UPDATE settings SET value = ? WHERE key = 'invite_code'", (new_code,))
        db.commit()
    return jsonify_success('已更新')

@app.route('/api/admin/settings/token', methods=['POST'])
@admin_required
def api_admin_update_token():
    new_token = secrets.token_hex(16)
    with get_db() as db:
        db.execute("UPDATE settings SET value = ? WHERE key = 'webhook_token'", (new_token,))
        db.commit()
    return jsonify_success('已重置', token=new_token)

@app.route('/api/admin/users')
@admin_required
def api_admin_users():
    with get_db() as db:
        users = db.execute('''
            SELECT u.id, u.username, u.created_at, COUNT(s.id) as source_count
            FROM users u LEFT JOIN sources s ON u.id = s.user_id
            GROUP BY u.id ORDER BY u.id DESC
        ''').fetchall()
    return jsonify_success(data=[dict(row) for row in users])

@app.route('/api/admin/users/delete', methods=['POST'])
@admin_required
def api_admin_users_delete():
    uid = request.json.get('id')
    with get_db() as db:
        db.execute('DELETE FROM group_suggestions WHERE user_id = ?', (uid,))
        db.execute('DELETE FROM model_settings WHERE user_id = ?', (uid,))
        db.execute('DELETE FROM group_bootstrap WHERE user_id = ?', (uid,))
        db.execute('DELETE FROM site_groups WHERE user_id = ?', (uid,))
        db.execute('''DELETE FROM source_configs WHERE source_id IN
            (SELECT id FROM sources WHERE user_id = ?)''', (uid,))
        db.execute('''DELETE FROM site_preferences WHERE source_id IN
            (SELECT id FROM sources WHERE user_id = ?)''', (uid,))
        db.execute('DELETE FROM sources WHERE user_id = ?', (uid,))
        db.execute('DELETE FROM users WHERE id = ?', (uid,))
        db.commit()
    return jsonify_success('已删除')

@app.route('/api/admin/recommendations/push', methods=['POST'])
def api_admin_push_recommendations():
    data = request.json
    pwd = data.get('password') or data.get('token') or request.headers.get('Authorization', '').replace('Bearer ', '')
    
    with get_db() as db:
        expected = db.execute("SELECT value FROM settings WHERE key = 'webhook_token'").fetchone()['value']
    if not expected or pwd != expected: return jsonify_error('未授权', 401)
        
    items = data.get('list', [])
    normalized = []
    for it in items:
        link = it if isinstance(it, str) else (it.get('link') or it.get('url'))
        name = "推荐源" if isinstance(it, str) else it.get('name', '推荐源')
        if link: normalized.append({"name": name, "link": link})
    
    try:
        save_path = '/app/data/recommended.json' if os.path.exists('/app/data') else 'data/recommended.json'
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, 'w', encoding='utf-8') as f:
            json.dump({'list': normalized}, f, ensure_ascii=False)
        return jsonify_success(f'成功更新 {len(normalized)} 条推荐源')
    except Exception as e: return jsonify_error(str(e))

# --- Public Subscription API ---
@app.route('/api/subscribe/<username>.json')
def get_tvbox_json(username):
    only_online = request.args.get('only_online') == 'true'
    
    with get_db() as db:
        user = db.execute('SELECT id FROM users WHERE username = ?', (username,)).fetchone()
        if not user: return jsonify_error('用户不存在', 404)
            
        sql = 'SELECT * FROM sources WHERE user_id = ? ORDER BY order_index ASC, id ASC'
        sources = db.execute(sql, (user['id'],)).fetchall()
        inputs = []
        direct_lives = []
        for source in sources:
            if source['type'] == 'live':
                direct_lives.append({'name': source['name'], 'type': 0, 'url': source['url']})
                continue
            try:
                inputs.append((source['id'], cached_config(db, source)))
            except (requests.RequestException, ValueError, UnicodeError) as exc:
                logger.warning('Unable to load config %s: %s', source['id'], exc)
                return jsonify_error(f'无法读取「{source["name"]}」，请刷新配置后重试', 502)
        preferences = {}
        for row in db.execute('''SELECT p.source_id, p.site_key, p.enabled, p.result
            FROM site_preferences p JOIN sources s ON s.id = p.source_id WHERE s.user_id = ?''', (user['id'],)):
            result = json.loads(row['result']) if row['result'] else {}
            preferences[(row['source_id'], row['site_key'])] = {
                'enabled': bool(row['enabled']), 'status': result.get('status')}
        config = merge_configs(inputs, preferences, only_online)
        config['lives'].extend(direct_lives)

    json_str = json.dumps(config, indent=4, ensure_ascii=False)
    if request.args.get('comment') == 'true':
        json_str = '//TVBox 整合配置\n' + json_str
    
    res = Response(json_str, mimetype='application/json; charset=utf-8')
    res.headers.add('Access-Control-Allow-Origin', '*')
    return res

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=8089)
