"""Directory coverage must be explicit; missing pages cannot look like success."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
try:
    import sync_moegirl_music as sync
except ModuleNotFoundError as exc:
    if exc.name != 'bs4':
        raise
    sync = None


class Source:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.urls = []

    def read(self, url):
        self.urls.append(url)
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value


@unittest.skipIf(sync is None, 'Optional catalogue maintenance dependency is not installed')
class MoegirlDirectoryTests(unittest.TestCase):
    def category(self, titles, total, next_link=''):
        return '<div id="mw-pages"><p>以下页面属于本分类，共' + str(total) + '个页面。</p><div class="mw-category">' + ''.join(
            '<a title="' + title + '">' + title + '</a>' for title in titles) + '</div>' + next_link + '</div>'

    def page(self, title, ident=123, cats=('网络歌手', '1月2日'), image=True):
        return {'title': title, 'pageid': ident, 'ns': 0, 'aliases': [title], 'lastrevid': 9,
                'categories': [{'title': 'Category:' + c} for c in cats],
                'original': {'source': 'https://storage.moegirl.org.cn/p.jpg', 'width': 400, 'height': 400} if image else {}}

    def payloads(self):
        return {kind: {'records': [], 'manual_records': [], 'overrides': {'keep': {'name': 'Custom'}}}
                for kind in sync.FILES}

    def test_follows_nested_pagination_without_top_n_and_reports_access_failure(self):
        first = self.category(['A'], 2, '<div><a href="/index.php?title=Category:歌手&amp;pagefrom=B">下一页</a></div>')
        with patch.object(sync, 'ROOT_CATEGORIES', ('歌手',)):
            entries, report = sync.discover(Source([first, self.category(['B'], 2)]))
            self.assertEqual(set(entries), {'A', 'B'})
            self.assertTrue(report['歌手']['complete'])
            entries, report = sync.discover(Source([first, ValueError('Login required')]))
            self.assertEqual(set(entries), {'A'})
            self.assertFalse(report['歌手']['complete'])
            self.assertEqual(report['歌手']['reported_total'], 2)

    def test_api_continuation_merges_aliases_and_categories(self):
        page = self.page('Singer')
        first = {'query': {'pages': {'123': page}}, 'continue': {'continue': '||', 'rdcontinue': '123|B'}}
        second = {'query': {'pages': {'123': {'title': 'Singer', 'pageid': 123, 'ns': 0,
                  'redirects': [{'title': '别名', 'ns': 0}], 'categories': [{'title': 'Category:作曲家'}]}}}}
        source = Source([json.dumps(first), json.dumps(second)])
        pages = sync.query_pages(source, ['Singer'])
        self.assertIn('别名', pages['Singer']['aliases'])
        self.assertEqual(len(pages['Singer']['categories']), 3)
        self.assertIn('rdcontinue=', source.urls[1])

    def test_browser_export_requires_all_pages_and_tracks_boundary_names(self):
        observations = {'歌手': [
            {'title': 'Category:歌手', 'summary': '共3个页面', 'names': ['A'],
             'next': '/index.php?title=Category:歌手&pagefrom=B'},
            {'title': 'Category:歌手', 'summary': '共3个页面', 'names': ['C']}]}
        memberships, audit = {}, {}
        sync.merge_browser_categories(memberships, audit, observations)
        self.assertEqual(set(memberships), {'A', 'B', 'C'})
        self.assertTrue(audit['歌手']['complete'])
        self.assertEqual(audit['歌手']['boundary_titles'], ['B'])
        with self.assertRaises(ValueError):
            sync.merge_browser_categories({}, {}, {'歌手': observations['歌手'][:1]})

    def test_imports_new_people_preserves_ids_overrides_and_is_idempotent(self):
        payloads = self.payloads()
        old = {'id': 'ut:utaitedb:9', 'name': 'Singer', 'aliases': ['Old Alias'], 'sources': ['https://example.org'],
               'moegirl': {'title': 'Singer', 'url': 'https://zh.moegirl.org.cn/Singer'}}
        payloads['utaite']['records'].append(old)
        pages = {'Singer': self.page('Singer'), 'New P': self.page('New P', 456, ('作曲家',))}
        result = sync.merge_pages(payloads, pages, {})
        self.assertEqual(result['added'], [{'id': 'mc:moegirl:456', 'title': 'New P'}])
        self.assertEqual(old['id'], 'ut:utaitedb:9')
        self.assertIn('Old Alias', old['aliases'])
        self.assertEqual(old['birthday'], '01-02')
        self.assertEqual(payloads['utaite']['overrides']['keep']['name'], 'Custom')
        self.assertEqual(sync.merge_pages(payloads, pages, {})['added'], [])

    def test_filters_vtubers_groups_drafts_no_portraits_and_existing_voice_actors(self):
        pages = {title: self.page(title, i, cats, image) for i, (title, cats, image) in enumerate([
            ('VT', ('歌手', '虚拟UP主'), True), ('Group', ('歌手', '歌唱组合'), True),
            ('No photo', ('网络歌手',), False), ('Actor', ('网络歌手',), True)], 1)}
        draft = self.page('User:Draft'); draft['ns'] = 2; pages['User:Draft'] = draft
        result = sync.merge_pages(self.payloads(), pages, {}, actor_aliases={'actor'})
        self.assertFalse(result['added'])
        self.assertEqual({r['reason'] for r in result['excluded']},
                         {'vtuber', 'group_or_fictional', 'no_portrait', 'already_in_voice_actor_catalogue', 'not_public_person_page'})

    def test_online_singer_verification_does_not_use_lyrics_or_collaborator_lists(self):
        self.assertTrue(sync.online_singer_profile('<div class="mw-parser-output"><p>某某是同人歌手。</p><h2>作品</h2></div>'))
        self.assertFalse(sync.online_singer_profile('<div class="mw-parser-output"><p>某某是演员。</p><h2>作品</h2><p>与唱见合作。</p></div>'))
        self.assertFalse(sync.online_singer_profile('<div class="mw-parser-output"><p>某某是演员。</p><div class="mw-heading"><h2>作品</h2></div><p>与唱见合作。</p></div>'))
        self.assertEqual(sync.music_profile_roles('<div class="mw-parser-output"><p>某某是日本的歌手、作词家、作曲家。</p></div>'), {'music_creator'})
        with self.assertRaises(ValueError):
            sync.online_singer_profile('<html>请登录</html>')

    def test_multiple_content_classes_are_read_but_login_redirects_are_not_cached(self):
        with tempfile.TemporaryDirectory() as folder:
            source = sync.PublicSource(folder, interval=0)
            source.client.close()
            html = '<div class="mw-content-ltr mw-parser-output"><p>同人歌手</p></div>'
            with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html))) as client:
                source.client = client
                self.assertEqual(source.read(sync.BASE + '/Singer'), html)
                self.assertEqual(source.read(sync.BASE + '/Singer'), html)
            def login_response(request):
                if 'Special:MoeAuth' in str(request.url):
                    return httpx.Response(200, text='<html>登录</html>')
                return httpx.Response(302, headers={'Location': sync.BASE + '/Special:MoeAuth/Userlogin'})
            with httpx.Client(transport=httpx.MockTransport(login_response), follow_redirects=True) as client:
                source.client = client
                with self.assertRaises(ValueError):
                    source.read(sync.BASE + '/Category:Test?pagefrom=X')
            self.assertEqual(len(list(Path(folder).glob('*.txt'))), 1)

    def test_ambiguous_moegirl_identity_is_reported_not_merged(self):
        payloads = self.payloads()
        for i in range(2):
            payloads['utaite']['records'].append({'id': 'ut:old:' + str(i), 'name': 'Same', 'moegirl': {'title': 'Same'}})
        result = sync.merge_pages(payloads, {'Same': self.page('Same')}, {})
        self.assertFalse(result['updated'])
        self.assertEqual(len(result['conflicts']), 1)
