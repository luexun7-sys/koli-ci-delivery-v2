"""Run: python -m unittest discover -s tests -v"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient


class DeliveryFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        os.environ['KOLI_DB'] = str(Path(cls.tmp.name) / 'test.db')
        # Change database path before importing app, then initialize only test DB.
        import importlib
        if 'server' in sys.modules:
            del sys.modules['server']
        cls.server = importlib.import_module('server')
        cls.client = TestClient(cls.server.app)
        cls.admin = cls.login('admin', 'admin12345')
        cls.rider = cls.login('rider', 'rider12345')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def login(cls, username, password):
        response = cls.client.post('/api/login', json={'username': username, 'password': password})
        assert response.status_code == 200, response.text
        return response.json()['token']

    def auth(self, token):
        return {'Authorization': 'Bearer ' + token}

    def test_full_order_lifecycle(self):
        # Customer creates order; no rider GPS/online yet.
        request = {'customer_name': 'Jean K', 'customer_phone': '0700000000', 'notes': 'Porte bleue',
                   'pickup': {'label':'Angré Abidjan','lat':5.398,'lng':-3.984},
                   'dropoff': {'label':'Plateau Abidjan','lat':5.320,'lng':-4.019}}
        res = self.client.post('/api/orders', json=request)
        self.assertEqual(res.status_code, 200, res.text)
        raw = res.json(); order=raw['order'];key=raw['tracking_token'];id=order['id']
        self.assertEqual(order['status'],'pending')
        self.assertEqual(len(key)>20,True)
        self.assertNotIn('tracking_hash',order)
        self.assertEqual(self.client.get(f'/api/orders/{id}/track?key=bad').status_code,404)
        self.assertEqual(self.client.get(f'/api/rider/orders').status_code,401)
        self.assertEqual(self.client.get('/api/admin/dashboard',headers=self.auth(self.rider)).status_code,403)

        # Simulate live GPS and online toggle, dispatch should happen.
        r=self.client.post('/api/rider/location',json={'lat':5.3978,'lng':-3.9842},headers=self.auth(self.rider))
        self.assertEqual(r.status_code,200,r.text)
        r=self.client.post('/api/rider/online',json={'online':True},headers=self.auth(self.rider))
        self.assertEqual(r.status_code,200,r.text)
        tracked=self.client.get(f'/api/orders/{id}/track?key={key}').json()
        self.assertEqual(tracked['status'],'assigned')
        self.assertEqual(tracked['rider']['name'],'Kouamé (démo)')

        # Invalid jump and invalid rider mutation rejected.
        r=self.client.patch(f'/api/rider/orders/{id}/status',json={'status':'delivered'},headers=self.auth(self.rider))
        self.assertEqual(r.status_code,409)
        for status in ('to_pickup','picked_up','delivering','delivered'):
            if status=='picked_up':
                r=self.client.post(f'/api/rider/orders/{id}/pickup',json={'code':raw['pickup_code']},headers=self.auth(self.rider))
                self.assertEqual(r.status_code,200,r.text)
                continue
            if status=='delivered':
                self.assertEqual(self.client.patch(f'/api/rider/orders/{id}/status',json={'status':'delivered'},headers=self.auth(self.rider)).status_code,409)
                self.client.post('/api/rider/location',json={'lat':5.320,'lng':-4.019},headers=self.auth(self.rider))
            r=self.client.patch(f'/api/rider/orders/{id}/status',json={'status':status},headers=self.auth(self.rider))
            self.assertEqual(r.status_code,200,r.text)
        tracked=self.client.get(f'/api/orders/{id}/track?key={key}').json()
        self.assertEqual(tracked['status'],'delivered')
        self.assertEqual(len(tracked['events']),6)
        self.assertIsNone(tracked['rider']['lat'], 'Do not reveal rider movements after completing a job')
        self.assertIsNone(tracked['rider']['lng'])

        self.assertEqual(self.client.post(f'/api/orders/{id}/rate?key={key}',json={'rating':4,'comment':'Bon service'}).status_code,200)
        self.assertEqual(self.client.post(f'/api/orders/{id}/rate?key={key}',json={'rating':5}).status_code,409)

        # Admin can see orders and create another rider.
        dashboard=self.client.get('/api/admin/dashboard',headers=self.auth(self.admin))
        self.assertEqual(dashboard.status_code,200)
        self.assertTrue(any(o['id']==id for o in dashboard.json()['orders']))
        add=self.client.post('/api/admin/riders',headers=self.auth(self.admin),json={
            'name':'Sali','username':'sali','password':'strong_pass_456','phone':'05000000'})
        self.assertEqual(add.status_code,200,add.text)
        self.assertTrue(self.login('sali','strong_pass_456'))

    def test_z_expired_gps_does_not_dispatch(self):
        from datetime import datetime, timedelta, timezone
        with self.server.db() as cx:
            cx.execute("UPDATE users SET online=1,last_location_at=? WHERE username='rider'", ((datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat(timespec='seconds'),))
        request = {'customer_name': 'Koffi K', 'customer_phone': '0700000000',
                   'pickup': {'label':'Angré Abidjan','lat':5.398,'lng':-3.984},
                   'dropoff': {'label':'Cocody Abidjan','lat':5.35,'lng':-3.99}}
        created = self.client.post('/api/orders',json=request)
        self.assertEqual(created.status_code,200,created.text)
        order=created.json()['order']
        self.assertEqual(order['status'],'pending', 'Old GPS must not be used to dispatch')
        update=self.client.post('/api/rider/location',json={'lat':5.397,'lng':-3.983},headers=self.auth(self.rider))
        self.assertEqual(update.status_code,200,update.text)
        tracked=self.client.get(f"/api/orders/{order['id']}/track",params={'key':created.json()['tracking_token']}).json()
        self.assertEqual(tracked['status'],'assigned')
        self.assertTrue(tracked['rider']['gps_live'])

    def test_three_entry_points(self):
        for path in ('/', '/client', '/rider', '/admin'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn('KOLI CI', response.text)

    def test_installable_pwa_assets(self):
        for path in ('/static/manifest-client.webmanifest','/static/manifest-rider.webmanifest','/static/sw.js','/static/icon-192.png'):
            self.assertEqual(self.client.get(path).status_code,200, path)

    def test_bad_inputs(self):
        self.assertEqual(self.client.post('/api/orders',json={'pickup':{'label':'X','lat':100,'lng':0},'dropoff':{'label':'Destination','lat':5,'lng':-4},'customer_name':'Jean','customer_phone':'07000000'}).status_code,422)
        self.assertEqual(self.client.post('/api/login',json={'username':'admin','password':'wrong'}).status_code,401)
        self.assertEqual(self.client.post('/api/admin/riders',headers=self.auth(self.rider),json={'name':'Sali','username':'sali2','password':'longpassword'}).status_code,403)


if __name__=='__main__':
    unittest.main()
