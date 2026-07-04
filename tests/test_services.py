import unittest
import sys
import os

# Add parent directory to sys.path so we can import microservices
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from microservices.api.service import app as api_app
from microservices.auth.service import app as auth_app
from microservices.cache.service import app as cache_app
from microservices.database.service import app as database_app
from microservices.frontend.service import app as frontend_app

class TestMicroservices(unittest.TestCase):
    def setUp(self):
        self.api = api_app.test_client()
        self.auth = auth_app.test_client()
        self.cache = cache_app.test_client()
        self.database = database_app.test_client()
        self.frontend = frontend_app.test_client()
        for client in [self.api, self.auth, self.cache, self.database, self.frontend]:
            response = client.post('/heal')
            self.assertEqual(response.status_code, 200)

    def test_api_endpoints(self):
        # Test status endpoint
        r = self.api.get('/status')
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data['service'], 'api')
        self.assertIn('cpu', data)
        self.assertIn('latency', data)
        
        # Test load endpoint
        r = self.api.get('/load')
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data['service'], 'api')
        self.assertIn('load_cpu_percent', data)
        
        # Test incident endpoint
        r = self.api.get('/incident')
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data['service'], 'api')
        self.assertIn('active_incident', data)

        r = self.api.get('/metrics')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'service_cpu_percent', r.data)

    def test_stress_and_heal_cycle_all_services(self):
        clients = {
            'api': self.api,
            'auth': self.auth,
            'cache': self.cache,
            'database': self.database,
            'frontend': self.frontend,
        }

        for service, client in clients.items():
            stressed = client.post('/stress')
            self.assertEqual(stressed.status_code, 200)
            self.assertEqual(stressed.get_json()['service'], service)

            incident = client.get('/incident')
            self.assertEqual(incident.status_code, 200)
            self.assertTrue(incident.get_json()['active_incident'])

            healed = client.post('/heal')
            self.assertEqual(healed.status_code, 200)
            self.assertEqual(healed.get_json()['service'], service)

            incident = client.get('/incident')
            self.assertEqual(incident.status_code, 200)
            self.assertFalse(incident.get_json()['active_incident'])

    def test_auth_endpoints(self):
        r = self.auth.get('/status')
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data['service'], 'auth')

        r = self.auth.get('/load')
        self.assertEqual(r.status_code, 200)

        r = self.auth.get('/incident')
        self.assertEqual(r.status_code, 200)

        r = self.auth.get('/metrics')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'service_latency_ms', r.data)

    def test_cache_endpoints(self):
        r = self.cache.get('/status')
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data['service'], 'cache')

        r = self.cache.get('/load')
        self.assertEqual(r.status_code, 200)

        r = self.cache.get('/incident')
        self.assertEqual(r.status_code, 200)

        r = self.cache.get('/metrics')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'service_latency_ms', r.data)

    def test_database_endpoints(self):
        r = self.database.get('/status')
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data['service'], 'database')

        r = self.database.get('/load')
        self.assertEqual(r.status_code, 200)

        r = self.database.get('/incident')
        self.assertEqual(r.status_code, 200)

        r = self.database.get('/metrics')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'service_latency_ms', r.data)

    def test_frontend_endpoints(self):
        # Frontend / serves HTML
        r = self.frontend.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'CloudMind', r.data)

        # /status
        r = self.frontend.get('/status')
        self.assertEqual(r.status_code, 200)
        data = r.get_json()
        self.assertEqual(data['service'], 'frontend')

        # /load and /incident
        r = self.frontend.get('/load')
        self.assertEqual(r.status_code, 200)
        r = self.frontend.get('/incident')
        self.assertEqual(r.status_code, 200)
        r = self.frontend.get('/metrics')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'service_latency_ms', r.data)

if __name__ == '__main__':
    unittest.main()
