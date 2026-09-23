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


if __name__ == '__main__':
    unittest.main()
