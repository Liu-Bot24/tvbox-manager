import importlib
import json
import os
import tempfile
import unittest
from unittest.mock import patch


class AppFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        os.environ['DB_PATH'] = os.path.join(cls.directory.name, 'test.db')
        cls.module = importlib.import_module('app')

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def setUp(self):
        self.client = self.module.app.test_client()
        with self.module.get_db() as db:
            db.execute("INSERT OR IGNORE INTO users (id, username, password_hash) VALUES (1, 'tester', 'x')")
            db.execute('DELETE FROM group_suggestions')
            db.execute('DELETE FROM model_settings')
            db.execute('DELETE FROM group_bootstrap')
            db.execute('DELETE FROM site_groups')
            db.execute('DELETE FROM site_preferences')
            db.execute('DELETE FROM source_configs')
            db.execute('DELETE FROM sources')
            db.execute("INSERT INTO sources (id, user_id, name, url, order_index) VALUES (1, 1, '甲', 'https://example.org/a.json', 0)")
            db.execute("INSERT INTO sources (id, user_id, name, url, order_index) VALUES (2, 1, '乙', 'https://example.org/b.json', 1)")
        with self.client.session_transaction() as session:
            session['user_id'] = 1
            session['username'] = 'tester'

    def test_two_configs_merge_and_selection_changes_public_output(self):
        configs = {
            'https://example.org/a.json': {'spider': 'https://example.org/a.jar', 'sites': [
                {'key': 'a', 'name': 'A', 'type': 1, 'api': 'https://example.org/api'},
                {'key': 'same', 'name': 'A Spider', 'type': 3, 'api': 'csp_A'}]},
            'https://example.org/b.json': {'spider': 'https://example.org/b.jar', 'sites': [
                {'key': 'same', 'name': 'B Spider', 'type': 3, 'api': 'csp_B'}]},
        }
        def load(url):
            return configs[url], 20
        with patch.object(self.module, 'load_config', side_effect=load):
            self.assertEqual(self.client.get('/api/source/1/sites').status_code, 200)
            self.assertEqual(self.client.get('/api/source/2/sites').status_code, 200)
        aggregate = self.client.get('/api/site/list').json['data']
        self.assertEqual([(site['source_name'], site['key']) for site in aggregate],
                         [('甲', 'a'), ('甲', 'same'), ('乙', 'same')])
        self.assertEqual(self.client.post('/api/site/enable', json={
            'source_id': 1, 'key': 'a', 'enabled': False}).json['status'], 'success')
        merged = self.client.get('/api/subscribe/tester.json').json
        self.assertEqual([site['key'] for site in merged['sites']], ['same', '2_same'])
        self.assertEqual([site['jar'] for site in merged['sites']],
                         ['https://example.org/a.jar', 'https://example.org/b.jar'])
        self.assertFalse(next(site for site in self.client.get('/api/site/list').json['data']
                              if site['key'] == 'a')['enabled'])

    def test_source_switch_does_not_override_individual_sites(self):
        with patch.object(self.module, 'load_config', return_value=(
                {'sites': [{'key': 'one', 'name': 'One', 'type': 1,
                            'api': 'https://example.org/api'}]}, 10)):
            self.client.get('/api/source/1/sites')
        self.client.post('/api/source/enable', json={'id': 1, 'enabled': False})
        self.client.post('/api/source/delete', json={'id': 2})
        self.assertEqual([site['key'] for site in self.client.get('/api/subscribe/tester.json').json['sites']],
                         ['one'])
        self.client.post('/api/site/enable', json={'source_id': 1, 'key': 'one', 'enabled': False})
        self.assertEqual(self.client.get('/api/subscribe/tester.json').json['sites'], [])

    def test_other_user_cannot_toggle_site(self):
        response = self.client.post('/api/site/enable', json={
            'source_id': 999, 'key': 'a', 'enabled': False})
        self.assertEqual(response.status_code, 404)

    def test_remember_login_uses_persistent_cookie_only_when_checked(self):
        from werkzeug.security import generate_password_hash
        with self.module.get_db() as db:
            db.execute('UPDATE users SET password_hash = ? WHERE id = 1',
                       (generate_password_hash('example-password'),))
        fresh = self.module.app.test_client()
        remembered = fresh.post('/api/auth/login', json={
            'username': 'tester', 'password': 'example-password', 'remember': True})
        self.assertEqual(remembered.json['status'], 'success')
        self.assertIn('Expires=', remembered.headers['Set-Cookie'])
        fresh.get('/api/auth/logout')
        temporary = fresh.post('/api/auth/login', json={
            'username': 'tester', 'password': 'example-password', 'remember': False})
        self.assertNotIn('Expires=', temporary.headers['Set-Cookie'])

    def test_probe_result_is_listed_and_filters_merged_config(self):
        config = {'sites': [{'key': 'a', 'name': 'A', 'type': 1,
                             'api': 'https://example.org/api'}]}
        with patch.object(self.module, 'load_config', return_value=(config, 20)):
            self.client.get('/api/source/1/sites')
        with patch.object(self.module, 'probe_site', return_value={
            'status': 'online', 'stage': 'detail', 'search_ms': 12, 'detail_ms': 15}):
            response = self.client.post('/api/site/probe', json={
                'source_id': 1, 'key': 'a', 'keyword': '庆余年'})
        self.assertEqual(response.json['data']['search_ms'], 12)
        listed = self.client.get('/api/source/1/sites').json['data']
        self.assertEqual(listed[0]['result']['status'], 'online')
        # The second source remains uncached; remove it for this isolated probe test.
        self.client.post('/api/source/delete', json={'id': 2})
        output = self.client.get('/api/subscribe/tester.json?only_online=true').json
        self.assertEqual([site['key'] for site in output['sites']], ['a'])
        self.assertEqual(self.client.get('/dashboard').status_code, 200)

    def test_unreachable_config_is_not_silently_dropped(self):
        with patch.object(self.module, 'load_config', side_effect=ValueError('bad json')):
            response = self.client.get('/api/subscribe/tester.json')
        self.assertEqual(response.status_code, 502)

    def test_preset_groups_and_new_sites_stay_unclassified(self):
        config = {'sites': [
            {'key': 'anime', 'name': '番剧动漫', 'type': 3, 'api': 'csp_Anime'},
            {'key': 'notice', 'name': '请勿相信视频中任何广告', 'type': 3, 'api': 'csp_Notice'},
            {'key': 'unknown', 'name': '海星', 'type': 3, 'api': 'csp_Sea'},
        ]}
        with patch.object(self.module, 'load_config', return_value=(config, 10)):
            groups = self.client.get('/api/group/list').json['data']
        names = {group['id']: group['name'] for group in groups}
        listing = {site['key']: site for site in self.client.get('/api/site/list').json['data']}
        self.assertEqual(names[listing['anime']['group_id']], '动漫')
        self.assertEqual(names[listing['notice']['group_id']], '无实际意义')
        self.assertIsNone(listing['unknown']['group_id'])
        with self.module.get_db() as db:
            db.execute('INSERT INTO sources (id, user_id, name, url) VALUES (3, 1, ?, ?)',
                       ('新增', 'https://example.org/c.json'))
            db.execute('INSERT INTO source_configs (source_id, body, fetched_at) VALUES (3, ?, ?)',
                       (json.dumps({'sites': [{'key': 'late', 'name': '新动漫', 'type': 3}]}), 'now'))
        late = next(site for site in self.client.get('/api/site/list').json['data'] if site['key'] == 'late')
        self.assertIsNone(late['group_id'])

    def test_bulk_enable_is_atomic_and_group_move_preserves_subscription(self):
        with self.module.get_db() as db:
            db.execute('INSERT INTO source_configs (source_id, body, fetched_at) VALUES (1, ?, ?)',
                       (json.dumps({'sites': [{'key': 'a', 'name': 'A'}, {'key': 'b', 'name': 'B'}]}), 'now'))
        self.client.post('/api/source/delete', json={'id': 2})
        refs = [{'source_id': 1, 'key': 'a'}, {'source_id': 1, 'key': 'b'}]
        invalid = self.client.post('/api/site/batch_enable', json={
            'enabled': False, 'sites': refs + [{'source_id': 1, 'key': 'missing'}]})
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual([s['key'] for s in self.client.get('/api/subscribe/tester.json').json['sites']], ['a', 'b'])
        group_id = self.client.post('/api/group/add', json={'name': '自定义', 'description': '测试'}).json['group_id']
        moved = self.client.post('/api/group/assign', json={'group_id': group_id, 'sites': refs})
        self.assertEqual(moved.json['count'], 2)
        self.assertEqual([s['key'] for s in self.client.get('/api/subscribe/tester.json').json['sites']], ['a', 'b'])
        disabled = self.client.post('/api/site/batch_enable', json={'enabled': False, 'sites': refs})
        self.assertEqual(disabled.json['count'], 2)
        self.assertEqual(self.client.get('/api/subscribe/tester.json').json['sites'], [])
        self.assertTrue(all(s['group_id'] == group_id for s in self.client.get('/api/site/list').json['data']))
        self.client.post('/api/group/delete', json={'id': group_id})
        self.assertTrue(all(s['group_id'] is None for s in self.client.get('/api/site/list').json['data']))
        self.assertEqual(self.client.get('/api/subscribe/tester.json').json['sites'], [])

    def test_jev_suggestions_require_manual_assignment_and_keep_key_private(self):
        with self.module.get_db() as db:
            db.execute('INSERT INTO source_configs (source_id, body, fetched_at) VALUES (1, ?, ?)',
                       (json.dumps({'sites': [{'key': 'ocean', 'name': '海星', 'type': 3}]}), 'now'))
        self.client.post('/api/source/delete', json={'id': 2})
        groups = self.client.get('/api/group/list').json['data']
        anime = next(g for g in groups if g['name'] == '动漫')
        saved = self.client.post('/api/model/settings', json={
            'provider': 'openrouter', 'model': 'typesafe/jev-1.13', 'api_key': 'test-key-private'})
        self.assertEqual(saved.json['status'], 'success')
        self.assertNotIn('api_key', self.client.get('/api/model/settings').json['data'])
        with self.module.get_db() as db:
            encrypted = db.execute('SELECT encrypted_key FROM model_settings WHERE user_id = 1').fetchone()[0]
        self.assertNotIn('test-key-private', encrypted)
        probs = {f'g_{g["id"]}': 0.0 for g in groups}
        probs[f'g_{anime["id"]}'] = 0.92
        probs['unclassified'] = 0.08
        class Response:
            def raise_for_status(self): pass
            def json(self): return {'answers': {'category': {'type': 'choice',
                'choice': f'g_{anime["id"]}', 'probabilities': probs, 'confidence': 0.92}}}
        with patch('grouping.requests.post', return_value=Response()) as call:
            result = self.client.post('/api/group/analyze', json={
                'group_id': None, 'sites': [{'source_id': 1, 'key': 'ocean'}]})
        self.assertEqual(result.json['data'][0]['confidence'], 0.92)
        self.assertEqual(call.call_args.args[0], 'https://openrouter.ai/api/alpha/decisions')
        self.assertIsNone(self.client.get('/api/site/list').json['data'][0]['group_id'])
        suggested = self.client.get('/api/group/suggestions?group_id=unclassified').json['data']
        self.assertEqual(suggested[0]['suggested_group_id'], anime['id'])
        self.client.post('/api/group/assign', json={
            'group_id': anime['id'], 'sites': [{'source_id': 1, 'key': 'ocean'}]})
        self.assertEqual(self.client.get('/api/group/suggestions?group_id=unclassified').json['data'], [])
        class ReviewResponse:
            def raise_for_status(self): pass
            def json(self): return {'answers': {
                'category': {'type': 'choice', 'choice': f'g_{anime["id"]}',
                             'probabilities': probs, 'confidence': 0.92},
                'belongs': {'type': 'noul', 'noul': 0.36}}}
        with patch('grouping.requests.post', return_value=ReviewResponse()):
            review = self.client.post('/api/group/analyze', json={
                'group_id': anime['id'], 'sites': [{'source_id': 1, 'key': 'ocean'}]})
        self.assertEqual(review.json['data'][0]['membership'], 0.36)
        self.assertEqual(self.client.get(f'/api/group/suggestions?group_id={anime["id"]}').json['data'][0]['membership'], 0.36)
        switched = self.client.post('/api/model/settings', json={
            'provider': 'typesafe', 'model': 'jev-latest', 'api_key': ''})
        self.assertEqual(switched.status_code, 400)

    def test_analysis_drops_stale_result_if_source_changes_during_request(self):
        with self.module.get_db() as db:
            db.execute('INSERT INTO source_configs (source_id, body, fetched_at) VALUES (1, ?, ?)',
                       (json.dumps({'sites': [{'key': 'old', 'name': '旧站点', 'type': 3}]}), 'now'))
        self.client.post('/api/source/delete', json={'id': 2})
        groups = self.client.get('/api/group/list').json['data']
        self.client.post('/api/model/settings', json={
            'provider': 'openrouter', 'api_key': 'test-key-private'})
        probabilities = {f'g_{group["id"]}': 0.0 for group in groups}
        probabilities['unclassified'] = 1.0

        class Response:
            def raise_for_status(self): pass
            def json(inner):
                with self.module.get_db() as db:
                    db.execute('UPDATE source_configs SET body = ? WHERE source_id = 1',
                               (json.dumps({'sites': [{'key': 'new', 'name': '新站点', 'type': 3}]}),))
                return {'answers': {'category': {'type': 'choice', 'choice': 'unclassified',
                        'confidence': 1.0, 'probabilities': probabilities}}}

        with patch('grouping.requests.post', return_value=Response()):
            result = self.client.post('/api/group/analyze', json={
                'group_id': None, 'sites': [{'source_id': 1, 'key': 'old'}]})
        self.assertEqual(result.json['data'], [])
        self.assertIn('原配置已更新', result.json['errors'][0]['message'])
        self.assertEqual(self.client.get('/api/group/suggestions?group_id=unclassified').json['data'], [])


if __name__ == '__main__':
    unittest.main()
