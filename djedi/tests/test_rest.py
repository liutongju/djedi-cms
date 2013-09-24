import cio
import os
import simplejson as json
import urllib
from django.core.files import File
from django.core.urlresolvers import reverse
from django.test import Client
from cio.plugins import plugins
from cio.backends import storage
from cio.backends.exceptions import PersistenceError, NodeDoesNotExist
from cio.utils.uri import URI
from djedi.tests.base import DjediTest, AssertionMixin, UserMixin


def json_node(response, simple=True):
    node = json.loads(response.content)
    if simple and 'meta' in node:
        del node['meta']
    return node


class PermissionTest(DjediTest, UserMixin):

    def setUp(self):
        super(PermissionTest, self).setUp()
        self.master = self.create_djedi_master()
        self.apprentice = self.create_djedi_apprentice()

    def test_permissions(self):
        client = Client()
        url = reverse('admin:djedi_api', args=['i18n://sv-se@page/title'])

        response = client.get(url)
        assert response.status_code == 403

        logged_in = client.login(username=self.master.username, password='test')
        assert logged_in
        response = client.get(url)
        assert response.status_code == 404

        client.logout()
        logged_in = client.login(username=self.apprentice.username, password='test')
        assert logged_in
        response = client.get(url)
        assert response.status_code == 404


class RestTest(DjediTest, UserMixin, AssertionMixin):

    def setUp(self):
        super(RestTest, self).setUp()
        master = self.create_djedi_master()
        client = Client(enforce_csrf_checks=True)
        client.login(username=master.username, password='test')
        self.client = client

    def get(self, url_name, uri):
        url = reverse('admin:' + url_name, args=[urllib.quote(uri)])
        return self.client.get(url)

    def post(self, url_name, uri, data):
        url = reverse('admin:' + url_name, args=[urllib.quote(uri)])
        return self.client.post(url, data)

    def test_get(self):
        response = self.get('djedi_api', 'i18n://sv-se@page/title')
        self.assertEqual(response.status_code, 404)

        cio.set('i18n://sv-se@page/title.md', u'# Djedi', publish=False)

        response = self.get('djedi_api', 'i18n://sv-se@page/title')
        self.assertEqual(response.status_code, 404)

        response = self.get('djedi_api', 'i18n://sv-se@page/title#draft')
        self.assertEqual(response.status_code, 200)
        node = json_node(response)
        self.assertKeys(node, 'uri', 'content')
        self.assertEqual(node['uri'], 'i18n://sv-se@page/title.md#draft')
        self.assertEqual(node['content'], u'<h1>Djedi</h1>')

    def test_load(self):
        response = self.get('djedi_api.load', 'i18n://sv-se@page/title')
        self.assertEqual(response.status_code, 200)
        json_content = json.loads(response.content)
        self.assertEqual(json_content['uri'], 'i18n://sv-se@page/title.txt')
        self.assertIsNone(json_content['data'])
        self.assertEqual(len(json_content['meta'].keys()), 0)

        # TODO: Should get 404
        # response = self.get('djedi_api.load', 'i18n://sv-se@page/title#1')
        # self.assertEqual(response.status_code, 404)

        cio.set('i18n://sv-se@page/title.md', u'# Djedi')

        response = self.get('djedi_api.load', 'sv-se@page/title')
        self.assertEqual(response.status_code, 200)
        node = json_node(response, simple=False)
        meta = node.pop('meta', {})
        self.assertDictEqual(node, {'uri': 'i18n://sv-se@page/title.md#1', 'data': u'# Djedi', 'content': u'<h1>Djedi</h1>'})
        self.assertKeys(meta, 'modified_at', 'published_at', 'is_published')

        response = self.get('djedi_api.load', 'i18n://sv-se@page/title#1')
        json_content = json.loads(response.content)
        self.assertEqual(json_content['uri'], 'i18n://sv-se@page/title.md#1')

        self.assertEqual(len(cio.revisions('i18n://sv-se@page/title')), 1)

    def test_set(self):
        response = self.post('djedi_api', 'i18n://page/title', {'data': u'# Djedi'})
        self.assertEqual(response.status_code, 400)

        response = self.post('djedi_api', 'i18n://sv-se@page/title.txt', {'data': u'# Djedi', 'data[extra]': u'foobar'})
        self.assertEqual(response.status_code, 400)

        uri = 'i18n://sv-se@page/title.md'
        response = self.post('djedi_api', uri, {'data': u'# Djedi', 'meta[message]': u'lundberg'})
        self.assertEqual(response.status_code, 200)
        node = json_node(response, simple=False)
        meta = node.pop('meta')
        self.assertDictEqual(node, {'uri': 'i18n://sv-se@page/title.md#draft', 'content': u'<h1>Djedi</h1>'})
        self.assertEqual(meta['author'], u'master')
        self.assertEqual(meta['message'], u'lundberg')

        node = cio.get(uri, lazy=False)
        self.assertIsNone(node.content)
        cio.publish(uri)
        node = cio.get(uri, lazy=False)
        self.assertEqual(node.uri, 'i18n://sv-se@page/title.md#1')
        self.assertEqual(node.content, u'<h1>Djedi</h1>')

        response = self.post('djedi_api', node.uri, {'data': u'# Djedi', 'meta[message]': u'Lundberg'})
        node = json_node(response, simple=False)
        self.assertEqual(node['meta']['message'], u'Lundberg')

        with self.assertRaises(PersistenceError):
            storage.backend._create(URI(node['uri']), None)

    def test_delete(self):
        url = reverse('admin:djedi_api', args=['i18n://sv-se@page/title'])

        response = self.client.delete(url)
        self.assertEqual(response.status_code, 404)

        node = cio.set('i18n://sv-se@page/title.md', u'# Djedi')

        url = reverse('admin:djedi_api', args=[urllib.quote(node.uri, '')])
        response = self.client.delete(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, u'')

        with self.assertRaises(NodeDoesNotExist):
            storage.get('i18n://sv-se@page/title')

        node = cio.get('i18n://page/title', lazy=False)
        self.assertIsNone(node.content)

    def test_publish(self):
        url = reverse('admin:djedi_api', args=['i18n://sv-se@page/title'])

        node = cio.set('sv-se@page/title', u'Djedi', publish=False)

        response = self.client.get(url)
        assert response.status_code == 404

        url = reverse('admin:djedi_api.publish', args=[urllib.quote(node.uri, '')])
        response = self.client.put(url)
        assert response.status_code == 200

        url = reverse('admin:djedi_api', args=['i18n://sv-se@page/title'])
        response = self.client.get(url)
        assert response.status_code == 200
        assert json_node(response) == {'uri': 'i18n://sv-se@page/title.txt#1', 'content': u'Djedi'}

        url = reverse('admin:djedi_api.publish', args=[urllib.quote('i18n://sv-se@foo/bar.txt#draft', '')])
        response = self.client.put(url)
        assert response.status_code == 404

    def test_revisions(self):
        cio.set('sv-se@page/title', u'Djedi 1')
        cio.set('sv-se@page/title', u'Djedi 2')

        url = reverse('admin:djedi_api.revisions', args=['sv-se@page/title'])
        response = self.client.get(url)
        assert response.status_code == 200

        content = json.loads(response.content)
        assert content == [['i18n://sv-se@page/title.txt#1', False], ['i18n://sv-se@page/title.txt#2', True]]

    def test_render(self):
        url = reverse('admin:djedi_api.render', args=['foo'])
        response = self.client.post(url, {'data': u'# Djedi'})
        assert response.status_code == 404

        url = reverse('admin:djedi_api.render', args=['md'])
        response = self.client.post(url, {'data': u'# Djedi'})
        assert response.status_code == 200
        assert response.content == u'<h1>Djedi</h1>'

    def test_editor(self):
        url = reverse('admin:djedi_cms.editor', args=['sv-se@page/title.foo'])
        response = self.client.get(url)
        assert response.status_code == 404

        url = reverse('admin:djedi_cms.editor', args=['sv-se@page/title'])
        response = self.client.get(url)
        assert response.status_code == 404

        for ext in plugins:
            url = reverse('admin:djedi_cms.editor', args=['sv-se@page/title.' + ext])
            response = self.client.get(url)
            assert response.status_code == 200
            assert set(response.context_data.keys()) == set(('THEME', 'VERSION', 'uri',))

        url = reverse('admin:djedi_cms.editor', args=['sv-se@page/title'])
        response = self.client.post(url, {'data': u'Djedi'})
        assert response.status_code == 200

    def test_upload(self):
        url = reverse('admin:djedi_api', args=['i18n://sv-se@header/logo.img'])

        tests_dir = os.path.dirname(os.path.abspath(__file__))
        image_path = os.path.join(tests_dir, 'assets', 'image.png')

        with open(image_path) as image:
            file = File(image, name=image_path)
            response = self.client.post(url, {'data[file]': file, 'data[alt]': u'Zwitter', 'meta[comment]': u'VW'})
            assert response.status_code == 200
            node = json_node(response, simple=False)
            meta = node.pop('meta')
            uri, content = node['uri'], node['content']
            assert uri == 'i18n://sv-se@header/logo.img#draft'
            assert content.startswith(u'<img src="/media/content-io/img/30/3045c6b466a1a816b180f679c024b7959e1d373c.')
            assert meta['comment'] == u'VW'
