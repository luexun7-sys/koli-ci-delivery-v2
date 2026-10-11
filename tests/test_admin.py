import os, tempfile, importlib, unittest, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient

class AdminFeatures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        os.environ['KOLI_DB'] = str(Path(self.tmp.name)/'test.db')
        os.environ['KOLI_MODE'] = 'test'
        sys.modules.pop('server',None)
        self.s = importlib.import_module('server')
        self.c = TestClient(self.s.APP)
        self.a = self.login('admin','admin12345')
        self.r = self.login('rider','rider12345')
    def tearDown(self): self.tmp.cleanup()
    def login(self,u,p):
        q=self.c.post('/api/login',json={'username':u,'password':p})
        self.assertEqual(q.status_code,200,q.text)
        return {'Authorization':'Bearer '+q.json()['token']}
    def order(self):
        q=self.c.post('/api/orders',json={'customer_name':'QA customer','customer_phone':'0700000000','pickup':{'label':'Angre','lat':5.398,'lng':-3.984},'dropoff':{'label':'Plateau','lat':5.320,'lng':-4.019}})
        self.assertEqual(q.status_code,200,q.text);return q.json()
    def test_settings_history_and_permissions(self):
        old=self.order()['order'];q=self.c.get('/api/admin/settings',headers=self.a)
        self.assertEqual(q.status_code,200);cfg=q.json();self.assertEqual(cfg['per_km_xof'],200)
        cfg['per_km_xof']=400
        self.assertEqual(self.c.put('/api/admin/settings',headers=self.a,json=cfg).status_code,200)
        new=self.order()['order'];self.assertEqual(new['price_xof'],old['price_xof']*2)
        self.assertEqual(self.c.get('/api/admin/orders/'+str(old['id']),headers=self.a).json()['price_xof'],old['price_xof'])
        cfg['completion_radius_m']=2000
        self.assertEqual(self.c.put('/api/admin/settings',headers=self.a,json=cfg).status_code,422)
        self.assertEqual(self.c.get('/api/admin/settings',headers=self.r).status_code,403)
        self.assertEqual(self.c.get('/api/admin/settings').status_code,401)
        self.s.initialize_db()
        self.assertEqual(self.c.get('/api/admin/settings',headers=self.a).json()['per_km_xof'],400)
    def test_cancel_reassign_and_auto_dispatch(self):
        x=self.order()['order'];y=self.order()['order']
        with self.s.db() as cx: rid=cx.execute("SELECT id FROM users WHERE username='rider'").fetchone()[0]
        assign=lambda i:self.c.post(f'/api/admin/orders/{i}/reassign',headers=self.a,json={'rider_id':rid,'reason':'QA assignment'})
        self.assertEqual(assign(x['id']).status_code,200)
        self.assertEqual(self.c.post(f'/api/admin/orders/{x["id"]}/assign',headers=self.a,json={'rider_id':rid}).status_code,422)
        self.assertTrue(self.c.get(f'/api/admin/orders/{x["id"]}',headers=self.a).json()['audit'])
        self.assertEqual(assign(y['id']).status_code,409)
        self.assertEqual(self.c.patch(f'/api/admin/riders/{rid}',headers=self.a,json={'enabled':False}).status_code,409)
        self.c.post('/api/rider/location',headers=self.r,json={'lat':5.398,'lng':-3.984})
        self.c.post('/api/rider/online',headers=self.r,json={'online':True})
        self.assertEqual(self.c.post(f'/api/admin/orders/{x["id"]}/cancel',headers=self.a,json={'reason':'QA cancellation'}).status_code,200)
        self.assertEqual(self.c.get(f'/api/admin/orders/{y["id"]}',headers=self.a).json()['status'],'assigned')
        z=self.order();self.assertEqual(z['order']['status'],'pending')
    def test_rider_disable_and_reset(self):
        riders=self.c.get('/api/admin/riders',headers=self.a)
        self.assertEqual(riders.status_code,200)
        rid=riders.json()[0]['id']
        self.assertEqual(self.c.patch(f'/api/admin/riders/{rid}',headers=self.a,json={'enabled':False}).status_code,200)
        self.assertEqual(self.c.get('/api/me',headers=self.r).status_code,401)
        self.assertEqual(self.c.post('/api/login',json={'username':'rider','password':'rider12345'}).status_code,401)
        self.c.patch(f'/api/admin/riders/{rid}',headers=self.a,json={'enabled':True,'name':'Changed name'})
        token=self.login('rider','rider12345')
        self.assertEqual(self.c.post(f'/api/admin/riders/{rid}/reset-password',headers=self.a,json={'password':'new-password-123'}).status_code,200)
        self.assertEqual(self.c.get('/api/me',headers=token).status_code,401)
        self.login('rider','new-password-123')
    def test_concurrent_dispatch_and_pagination(self):
        from concurrent.futures import ThreadPoolExecutor
        from datetime import datetime,timezone
        with self.s.db() as cx:
            cx.execute("UPDATE users SET online=1,lat=5.398,lng=-3.984,last_location_at=? WHERE username='rider'",(datetime.now(timezone.utc).isoformat(timespec='seconds'),))
        with ThreadPoolExecutor(max_workers=4) as pool:
            jobs=list(pool.map(lambda _:self.order(),range(8)))
        self.assertEqual(sum(x['order']['status']=='assigned' for x in jobs),1)
        for _ in range(96):self.order()
        q=self.c.get('/api/admin/orders?page=6',headers=self.a).json()
        self.assertEqual(q['total'],104);self.assertEqual(len(q['items']),4)
        self.assertEqual(self.c.get('/api/admin/stats',headers=self.a).json()['total'],104)
    def test_pickup_cannot_be_reassigned_and_stale_gps(self):
        x=self.order();i=x['order']['id']
        with self.s.db() as cx:rid=cx.execute("SELECT id FROM users WHERE username='rider'").fetchone()[0]
        self.c.post(f'/api/admin/orders/{i}/reassign',headers=self.a,json={'rider_id':rid,'reason':'test'})
        self.c.patch(f'/api/rider/orders/{i}/status',headers=self.r,json={'status':'to_pickup'})
        self.assertEqual(self.c.post(f'/api/rider/orders/{i}/pickup',headers=self.r,json={'code':x['pickup_code']}).status_code,200)
        self.assertEqual(self.c.post(f'/api/admin/orders/{i}/reassign',headers=self.a,json={'rider_id':rid,'reason':'test'}).status_code,409)
        self.c.patch(f'/api/rider/orders/{i}/status',headers=self.r,json={'status':'delivering'})
        with self.s.db() as cx:cx.execute("UPDATE users SET lat=5.320,lng=-4.019,last_location_at='2020-01-01T00:00:00+00:00' WHERE id=?",(rid,))
        self.assertEqual(self.c.patch(f'/api/rider/orders/{i}/status',headers=self.r,json={'status':'delivered'}).status_code,409)
    def test_list_export_stats(self):
        x=self.order()['order']
        with self.s.db() as cx:
            cx.execute("UPDATE orders SET customer_name='=BAD',status='delivered',created_at='2026-10-01T23:59:59+00:00' WHERE id=?",(x['id'],))
        q=self.c.get('/api/admin/orders?start=2026-10-01&end=2026-10-01',headers=self.a)
        self.assertEqual(q.status_code,200);self.assertEqual(q.json()['total'],1)
        self.assertEqual(self.c.get('/api/admin/orders?start=2026-10-02',headers=self.a).json()['total'],0)
        export=self.c.get('/api/admin/orders/export',headers=self.a)
        self.assertEqual(export.status_code,200);self.assertIn("'=BAD",export.text);self.assertNotIn('tracking_hash',export.text)
        stats=self.c.get('/api/admin/stats?start=2026-10-01&end=2026-10-01',headers=self.a)
        self.assertEqual(stats.status_code,200);self.assertEqual(stats.json()['completed_fees_xof'],x['price_xof'])
        self.assertEqual(self.c.post(f'/api/admin/orders/{x["id"]}/cancel',headers=self.a,json={'reason':'bad'}).status_code,409)

if __name__=='__main__':unittest.main()
