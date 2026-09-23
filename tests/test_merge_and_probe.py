import unittest
from unittest.mock import patch

from merge_config import merge_configs, normalize_resources
from site_probe import probe_site


class MergeTests(unittest.TestCase):
    def test_relative_spider_and_script_are_resolved(self):
        config = {'spider': './jar/spider.jar;md5;abc', 'sites': [
            {'key': 'js', 'api': './js/site.js', 'ext': './js/options.js'}]}
        normalized = normalize_resources(config, 'https://example.org/config/main.json')
        self.assertEqual(normalized['spider'], 'https://example.org/config/jar/spider.jar;md5;abc')
        self.assertEqual(normalized['sites'][0]['api'], 'https://example.org/config/js/site.js')
        self.assertEqual(config['spider'], './jar/spider.jar;md5;abc')

    def test_merges_selected_sites_preserving_each_spider(self):
        first = {'spider': 'https://example.org/a.jar', 'sites': [
            {'key': 'one', 'name': 'One', 'type': 3, 'api': 'csp_One'},
            {'key': 'skip', 'name': 'Skip', 'type': 1, 'api': 'https://example.org/api'},
        ], 'parses': [], 'lives': []}
        second = {'spider': 'https://example.org/b.jar', 'sites': [
            {'key': 'one', 'name': 'Another One', 'type': 3, 'api': 'csp_Two'},
        ], 'parses': [], 'lives': []}
        merged = merge_configs([(10, first), (11, second)], {(10, 'skip'): {'enabled': False}})
        self.assertEqual([s['key'] for s in merged['sites']], ['one', '11_one'])
        self.assertEqual([s['jar'] for s in merged['sites']],
                         ['https://example.org/a.jar', 'https://example.org/b.jar'])
        self.assertEqual(len(first['sites']), 2)

    def test_online_only_excludes_unverified_and_failed(self):
        config = {'sites': [{'key': key} for key in ('good', 'bad', 'new')]}
        merged = merge_configs([(1, config)], {
            (1, 'good'): {'enabled': True, 'status': 'online'},
            (1, 'bad'): {'enabled': True, 'status': 'failed'},
        }, online_only=True)
        self.assertEqual([s['key'] for s in merged['sites']], ['good'])


class ProbeTests(unittest.TestCase):
    @patch('site_probe.fetch_limited')
    def test_xml_cms_search_and_detail(self, fetch):
        fetch.side_effect = [
            (b'<rss><list><video><id>7</id></video></list></rss>', 18),
            (b'<rss><list><video><id>7</id><dl><dd>Ep$https://example.org/video.mp4</dd></dl></video></list></rss>', 24),
        ]
        result = probe_site({'type': 0, 'api': 'https://example.org/xml'})
        self.assertEqual(result['status'], 'online')
        self.assertEqual(result['detail_ms'], 24)
        self.assertEqual(result['playback'], 'unverified')

    @patch('site_probe.fetch_limited')
    def test_cms_search_detail_playback(self, fetch):
        fetch.side_effect = [
            (b'{"list":[{"vod_id":"42"}]}', 27),
            (b'{"list":[{"vod_play_url":"Episode$https://example.org/a.m3u8"}]}', 31),
        ]
        with patch('site_probe.media_probe', return_value={'playback': 'ok', 'play_latency_ms': 40, 'speed_kbps': 2000}):
            result = probe_site({'type': 1, 'api': 'https://example.org/api'})
        self.assertEqual(result['status'], 'online')
        self.assertEqual(result['stage'], 'playback')
        self.assertEqual(result['search_ms'], 27)

    def test_spider_is_not_reported_online_without_client_runtime(self):
        result = probe_site({'type': 3, 'api': 'csp_Example'})
        self.assertEqual(result['status'], 'unsupported')

    @patch('site_probe.fetch_limited', return_value=(b'{"list":[]}', 35))
    def test_empty_search_is_failure(self, fetch):
        result = probe_site({'type': 1, 'api': 'https://example.org/api'})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['stage'], 'search')


if __name__ == '__main__':
    unittest.main()
