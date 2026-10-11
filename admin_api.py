"""Persistent operational settings and authenticated back-office routes."""
import csv
import io
from datetime import date, timedelta
from fastapi import HTTPException, Request, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

DEFAULTS = dict(per_km_xof=200, minimum_xof=200, completion_radius_m=150,
                gps_max_age_seconds=120, rating_dispatch_enabled=True)

def migrate(cx):
    if 'enabled' not in {r[1] for r in cx.execute('PRAGMA table_info(users)')}:
        cx.execute('ALTER TABLE users ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1')
    cx.executescript('''CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS admin_audit(id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_id INTEGER NOT NULL,action TEXT NOT NULL,object_id TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',created_at TEXT NOT NULL);''')
    for key, value in DEFAULTS.items():
        cx.execute('INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)',(key,str(int(value))))

def get_settings(cx):
    values = dict(DEFAULTS)
    for row in cx.execute('SELECT key,value FROM settings'):
        if row['key'] in values:
            values[row['key']] = int(row['value'])
    values['rating_dispatch_enabled'] = bool(values['rating_dispatch_enabled'])
    return values

class SettingsBody(BaseModel):
    per_km_xof: int = Field(ge=1,le=100000)
    minimum_xof: int = Field(ge=1,le=100000)
    completion_radius_m: int = Field(ge=30,le=1000)
    gps_max_age_seconds: int = Field(ge=15,le=300)
    rating_dispatch_enabled: bool

class ReasonBody(BaseModel):
    reason: str = Field(min_length=2,max_length=400)

class ReassignBody(ReasonBody):
    rider_id: int = Field(ge=1)

class RiderEdit(BaseModel):
    name: str | None = Field(default=None,min_length=2,max_length=80)
    phone: str | None = Field(default=None,max_length=40)
    enabled: bool | None = None

class PasswordBody(BaseModel):
    password: str = Field(min_length=8,max_length=100)

def filters(search='', status='', start='', end='', rider_id=None):
    clauses=[];args=[]
    if search.strip():
        clauses.append("(o.code LIKE ? OR o.customer_phone LIKE ? OR o.sender_phone LIKE ?)")
        args.extend(['%'+search.strip()+'%']*3)
    if status:
        if status not in ('pending','assigned','to_pickup','picked_up','delivering','delivered','cancelled'):
            raise HTTPException(422,'Statut invalide')
        clauses.append('o.status=?');args.append(status)
    try:
        first=date.fromisoformat(start) if start else None
        last=date.fromisoformat(end) if end else None
    except ValueError: raise HTTPException(422,'Date invalide (AAAA-MM-JJ)')
    if first and last and first>last: raise HTTPException(422,'La date de début dépasse la fin')
    if first: clauses.append('o.created_at>=?');args.append(first.isoformat()+'T00:00:00')
    if last: clauses.append('o.created_at<?');args.append((last+timedelta(days=1)).isoformat()+'T00:00:00')
    if rider_id is not None: clauses.append('o.rider_id=?');args.append(rider_id)
    return (' WHERE '+' AND '.join(clauses) if clauses else ''),args

def register(app, s):
    db=s['db'];get_user=s['get_user'];public=s['public_order'];now=s['utc_now'];dispatch=s['try_dispatch']
    def audit(cx,actor,action,obj,reason=''):
        cx.execute('INSERT INTO admin_audit(actor_id,action,object_id,reason,created_at) VALUES(?,?,?,?,?)',
                   (actor['id'],action,str(obj),reason,now()))
    def rider_row(cx,rid):
        r=cx.execute("SELECT * FROM users WHERE id=? AND role='rider'",(rid,)).fetchone()
        if not r: raise HTTPException(404,'Livreur introuvable')
        return r

    @app.get('/api/admin/settings')
    def settings(request: Request):
        get_user(request,'admin')
        with db() as cx:return get_settings(cx)

    @app.put('/api/admin/settings')
    def update_settings(body: SettingsBody, request: Request):
        actor=get_user(request,'admin')
        with db() as cx:
            for k,v in body.model_dump().items():
                cx.execute('UPDATE settings SET value=? WHERE key=?',(str(int(v)),k))
            audit(cx,actor,'settings','global',str(body.model_dump()))
            dispatch(cx)
            return get_settings(cx)

    @app.get('/api/admin/orders/export')
    def export(request: Request,search: str='',status: str='',start: str='',end: str='',rider_id: int|None=None):
        get_user(request,'admin');where,args=filters(search,status,start,end,rider_id)
        fields=['code','created_at','status','sender_name','sender_phone','customer_name','customer_phone',
                'pickup_label','dropoff_label','distance_km','price_xof','rider_id','rating']
        out=io.StringIO();out.write('\ufeff');writer=csv.writer(out);writer.writerow(fields)
        with db() as cx:
            for row in cx.execute('SELECT o.* FROM orders o'+where+' ORDER BY o.id DESC',args):
                values=[]
                for field in fields:
                    value=row[field] if row[field] is not None else ''
                    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@','\t','\r','\n')):value="'"+value
                    values.append(value)
                writer.writerow(values)
        return Response(out.getvalue(),media_type='text/csv; charset=utf-8',headers={'Content-Disposition':'attachment; filename="koli-orders.csv"'})

    @app.get('/api/admin/orders')
    def orders(request: Request, search: str='',status: str='',start: str='',end: str='',rider_id: int|None=None,
               page: int=Query(1,ge=1),page_size: int=Query(20,ge=1,le=100)):
        get_user(request,'admin');where,args=filters(search,status,start,end,rider_id)
        with db() as cx:
            total=cx.execute('SELECT COUNT(*) FROM orders o'+where,args).fetchone()[0]
            rows=cx.execute('SELECT o.* FROM orders o'+where+' ORDER BY o.id DESC LIMIT ? OFFSET ?',args+[page_size,(page-1)*page_size]).fetchall()
            items=[public(cx,r) for r in rows]
            for item in items:
                if item['status']=='pending':item['waiting_reason']='En attente d’un livreur actif, en ligne, libre et avec GPS récent'
            return dict(items=items,total=total,page=page,page_size=page_size)

    @app.get('/api/admin/orders/{oid}')
    def detail(oid: int,request: Request):
        get_user(request,'admin')
        with db() as cx:
            row=cx.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
            if not row:raise HTTPException(404,'Commande introuvable')
            data=public(cx,row)
            data['audit']=[dict(r) for r in cx.execute("SELECT a.*,u.name AS actor_name FROM admin_audit a LEFT JOIN users u ON u.id=a.actor_id WHERE object_id=? AND action IN ('cancel','reassign','assign') ORDER BY a.id",(str(oid),))]
            return data

    @app.post('/api/admin/orders/{oid}/cancel')
    def cancel(oid: int,body: ReasonBody,request: Request):
        actor=get_user(request,'admin');reason=body.reason.strip()
        if len(reason)<2:raise HTTPException(422,'Motif requis')
        with db() as cx:
            row=cx.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone()
            if not row:raise HTTPException(404,'Commande introuvable')
            if row['status'] in ('delivered','cancelled'):raise HTTPException(409,'Commande déjà terminée')
            cx.execute("UPDATE orders SET status='cancelled',updated_at=? WHERE id=?",(now(),oid))
            s['event'](cx,oid,'cancelled','Annulation : '+reason)
            audit(cx,actor,'cancel',oid,reason);dispatch(cx)
            return public(cx,cx.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone())

    @app.post('/api/admin/orders/{oid}/assign')
    @app.post('/api/admin/orders/{oid}/reassign')
    def reassign(oid: int,body: ReassignBody,request: Request):
        actor=get_user(request,'admin');reason=body.reason.strip()
        if len(reason)<2:raise HTTPException(422,'Motif requis')
        with db() as cx:
            row=cx.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone();r=rider_row(cx,body.rider_id)
            if not row:raise HTTPException(404,'Commande introuvable')
            if row['status'] not in ('pending','assigned','to_pickup'):raise HTTPException(409,'Impossible après le retrait ou la fin')
            if not r['enabled']:raise HTTPException(409,'Compte livreur désactivé')
            if cx.execute("SELECT 1 FROM orders WHERE rider_id=? AND status NOT IN ('delivered','cancelled') AND id<>?",(r['id'],oid)).fetchone():
                raise HTTPException(409,'Ce livreur a déjà une course')
            cx.execute("UPDATE orders SET rider_id=?,status='assigned',updated_at=? WHERE id=?",(r['id'],now(),oid))
            s['event'](cx,oid,'assigned','Attribution à '+r['name']+' : '+reason)
            audit(cx,actor,'reassign',oid,reason);dispatch(cx)
            return public(cx,cx.execute('SELECT * FROM orders WHERE id=?',(oid,)).fetchone())

    @app.get('/api/admin/riders')
    def riders(request: Request):
        get_user(request,'admin')
        with db() as cx:
            result=[]
            for r in cx.execute("SELECT id,name,username,phone,online,enabled,lat,lng,last_location_at FROM users WHERE role='rider' ORDER BY id").fetchall():
                item=dict(r)
                active=cx.execute("SELECT id,code,status FROM orders WHERE rider_id=? AND status NOT IN ('delivered','cancelled')",(r['id'],)).fetchone()
                metrics=cx.execute("SELECT SUM(CASE WHEN status='delivered' THEN 1 ELSE 0 END) AS completed,AVG(rating) AS average_rating,COUNT(rating) AS rating_count FROM orders WHERE rider_id=?",(r['id'],)).fetchone()
                item.update(dict(metrics));item['completed']=item['completed'] or 0
                item['current_order']=dict(active) if active else None;item['gps_live']=s['fresh_gps'](r['last_location_at'])
                if not item['gps_live']:item['lat']=None;item['lng']=None
                result.append(item)
            return result

    @app.patch('/api/admin/riders/{rid}')
    def edit_rider(rid: int,body: RiderEdit,request: Request):
        actor=get_user(request,'admin');data=body.model_dump(exclude_none=True)
        if 'name' in data:
            data['name']=data['name'].strip()
            if len(data['name'])<2:raise HTTPException(422,'Nom requis')
        with db() as cx:
            rider_row(cx,rid)
            if data.get('enabled') is False:
                if cx.execute("SELECT 1 FROM orders WHERE rider_id=? AND status NOT IN ('delivered','cancelled')",(rid,)).fetchone():raise HTTPException(409,'Terminez ou réattribuez la course avant de désactiver')
                cx.execute('DELETE FROM sessions WHERE user_id=?',(rid,))
                cx.execute('UPDATE users SET online=0 WHERE id=?',(rid,))
            for key,value in data.items():cx.execute(f'UPDATE users SET {key}=? WHERE id=?',(int(value) if key=='enabled' else value,rid))
            audit(cx,actor,'rider_edit',rid,str(data));dispatch(cx)
            return {'ok':True}

    @app.post('/api/admin/riders/{rid}/reset-password')
    def reset_password(rid: int,body: PasswordBody,request: Request):
        actor=get_user(request,'admin')
        with db() as cx:
            rider_row(cx,rid);cx.execute('UPDATE users SET password_hash=? WHERE id=?',(s['hash_password'](body.password),rid))
            cx.execute('DELETE FROM sessions WHERE user_id=?',(rid,));audit(cx,actor,'password_reset',rid)
            return {'ok':True}

    @app.get('/api/admin/riders/{rid}/ratings')
    def ratings(rid: int,request: Request,page: int=Query(1,ge=1)):
        get_user(request,'admin')
        with db() as cx:
            rider_row(cx,rid)
            total=cx.execute('SELECT COUNT(*) FROM orders WHERE rider_id=? AND rating IS NOT NULL',(rid,)).fetchone()[0]
            items=[dict(r) for r in cx.execute('SELECT code,rating,rating_comment,updated_at FROM orders WHERE rider_id=? AND rating IS NOT NULL ORDER BY id DESC LIMIT 20 OFFSET ?',(rid,(page-1)*20))]
            return {'items':items,'total':total,'page':page}

    @app.get('/api/admin/audit')
    def audit_list(request: Request):
        get_user(request,'admin')
        with db() as cx:return [dict(r) for r in cx.execute('SELECT a.*,u.name AS actor_name FROM admin_audit a LEFT JOIN users u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 100')]

    @app.get('/api/admin/stats')
    def stats(request: Request,start: str='',end: str='',rider_id: int|None=None):
        get_user(request,'admin');where,args=filters(start=start,end=end,rider_id=rider_id)
        with db() as cx:
            rows=cx.execute('SELECT o.* FROM orders o'+where,args).fetchall();total=len(rows)
            counts={};trend={};rank={};fees=0
            for r in rows:
                counts[r['status']]=counts.get(r['status'],0)+1
                day=r['created_at'][:10];trend[day]=trend.get(day,0)+1
                if r['status']=='delivered':fees+=r['price_xof']
                if r['rider_id']:
                    entry=rank.setdefault(r['rider_id'],{'rider_id':r['rider_id'],'completed':0,'ratings':[]})
                    if r['status']=='delivered':entry['completed']+=1
                    if r['rating'] is not None:entry['ratings'].append(r['rating'])
            ranking=[]
            for rid,entry in rank.items():
                rider=cx.execute('SELECT name FROM users WHERE id=?',(rid,)).fetchone()
                entry['name']=rider['name'] if rider else '—';values=entry.pop('ratings')
                entry['average_rating']=round(sum(values)/len(values),2) if values else None;entry['rating_count']=len(values);ranking.append(entry)
            return dict(total=total,status_counts=counts,completion_rate=round(counts.get('delivered',0)*100/total,1) if total else 0,
                        completed_fees_xof=fees,daily=[dict(date=d,count=n) for d,n in sorted(trend.items())],
                        ranking=sorted(ranking,key=lambda r:r['completed'],reverse=True))
