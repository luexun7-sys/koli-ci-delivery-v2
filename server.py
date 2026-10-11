"""KOLI CI — single-service courier delivery MVP for Abidjan.

Runs with Python 3.10+ / FastAPI and SQLite. All admin and rider actions
require a server-issued, expiring bearer token. Customer order access uses
an unguessable, order-specific tracking token.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import secrets
import sqlite3
import threading
from admin_api import migrate as migrate_admin, get_settings, register as register_admin
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
DB_PATH = Path(os.environ.get('KOLI_DB', ROOT / 'koli.db'))
DEFAULT_ADMIN_PASSWORD = os.environ.get('KOLI_ADMIN_PASSWORD', 'admin12345')
DEFAULT_RIDER_PASSWORD = os.environ.get('KOLI_RIDER_PASSWORD', 'rider12345')
LOCK = threading.RLock()
STATUSES = ['pending', 'assigned', 'to_pickup', 'picked_up', 'delivering', 'delivered']
GPS_MAX_AGE_SECONDS = 120  # Reject old last-known positions for dispatch and live tracking
if os.environ.get('KOLI_MODE') == 'production':
    if min(len(DEFAULT_ADMIN_PASSWORD), len(DEFAULT_RIDER_PASSWORD)) < 14 or DEFAULT_ADMIN_PASSWORD == DEFAULT_RIDER_PASSWORD or DEFAULT_ADMIN_PASSWORD == 'admin12345' or DEFAULT_RIDER_PASSWORD == 'rider12345':
        raise RuntimeError('Set two different strong passwords (14+ characters) in KOLI_ADMIN_PASSWORD and KOLI_RIDER_PASSWORD before production start')

APP = FastAPI(title='KOLI CI API', version='0.1.0')
APP.mount('/static', StaticFiles(directory=ROOT / 'static'), name='static')


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def as_dict(row):
    return dict(row) if row else None


def hash_password(password: str, salt: Optional[str] = None):
    salt = salt or secrets.token_hex(16)
    return salt + '$' + hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), 260_000).hex()


def verify_password(password: str, stored: str):
    try:
        salt, hashed = stored.split('$')
        return hmac.compare_digest(hash_password(password, salt), stored)
    except (ValueError, TypeError):
        return False


@contextmanager
def db():
    with LOCK:
        cx = sqlite3.connect(DB_PATH, timeout=30)
        cx.row_factory = sqlite3.Row
        cx.execute('PRAGMA foreign_keys=ON')
        try:
            yield cx
            cx.commit()
        except Exception:
            cx.rollback()
            raise
        finally:
            cx.close()


def initialize_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as cx:
        cx.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin','rider')),
            phone TEXT NOT NULL DEFAULT '',
            online INTEGER NOT NULL DEFAULT 0,
            lat REAL, lng REAL, last_location_at TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            expires_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL UNIQUE,
            tracking_hash TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            customer_phone TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '',
            pickup_label TEXT NOT NULL,
            pickup_lat REAL NOT NULL,
            pickup_lng REAL NOT NULL,
            dropoff_label TEXT NOT NULL,
            dropoff_lat REAL NOT NULL,
            dropoff_lng REAL NOT NULL,
            distance_km REAL NOT NULL,
            price_xof INTEGER NOT NULL,
            status TEXT NOT NULL,
            rider_id INTEGER REFERENCES users(id),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER NOT NULL REFERENCES orders(id),
            status TEXT NOT NULL,
            note TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_order_rider ON orders(rider_id, status);
        CREATE INDEX IF NOT EXISTS idx_events_order ON events(order_id);
        ''')
        # Backward-compatible migration for sender contact details.
        cols = {r[1] for r in cx.execute('PRAGMA table_info(orders)').fetchall()}
        if 'sender_name' not in cols:
            cx.execute("ALTER TABLE orders ADD COLUMN sender_name TEXT NOT NULL DEFAULT ''")
        if 'sender_phone' not in cols:
            cx.execute("ALTER TABLE orders ADD COLUMN sender_phone TEXT NOT NULL DEFAULT ''")
        if 'pickup_code_hash' not in cols:
            cx.execute("ALTER TABLE orders ADD COLUMN pickup_code_hash TEXT NOT NULL DEFAULT ''")
        if 'pickup_verified_at' not in cols:
            cx.execute("ALTER TABLE orders ADD COLUMN pickup_verified_at TEXT")
        if 'rating' not in cols:
            cx.execute("ALTER TABLE orders ADD COLUMN rating INTEGER")
        if 'rating_comment' not in cols:
            cx.execute("ALTER TABLE orders ADD COLUMN rating_comment TEXT NOT NULL DEFAULT ''")
        for column, definition in [('pickup_attempts','INTEGER NOT NULL DEFAULT 0'), ('pickup_locked_until','TEXT')]:
            if column not in cols:
                cx.execute(f'ALTER TABLE orders ADD COLUMN {column} {definition}')
        migrate_admin(cx)
        for username, password, role, name in (
            ('admin', DEFAULT_ADMIN_PASSWORD, 'admin', 'Administrateur'),
            ('rider', DEFAULT_RIDER_PASSWORD, 'rider', 'Kouamé (démo)'),
        ):
            if not cx.execute('SELECT 1 FROM users WHERE username=?', (username,)).fetchone():
                cx.execute('INSERT INTO users(name,username,password_hash,role,created_at) VALUES(?,?,?,?,?)',
                           (name, username, hash_password(password), role, utc_now()))


initialize_db()


def validate_point(lat: float, lng: float):
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(422, 'Coordonnées GPS invalides')


def fresh_gps(at, max_age=None):
    if max_age is None:
        with db() as cx: max_age = get_settings(cx)['gps_max_age_seconds']
    if not at:
        return False
    try:
        ts = datetime.fromisoformat(at)
        return ts.tzinfo is not None and timedelta(0) <= datetime.now(timezone.utc) - ts <= timedelta(seconds=max_age)
    except (ValueError, TypeError):
        return False


def haversine(a, b, c, d):
    p1, p2 = math.radians(a), math.radians(c)
    dlat, dlng = p2 - p1, math.radians(d - b)
    x = math.sin(dlat / 2)**2 + math.cos(p1) * math.cos(p2) * math.sin(dlng / 2)**2
    return 6371 * 2 * math.asin(min(1, math.sqrt(x)))


def estimate(lat1, lng1, lat2, lng2):
    dist = round(haversine(lat1, lng1, lat2, lng2), 2)
    # Customer-facing tariff: 200 FCFA per started kilometre, with a 200 FCFA minimum.
    with db() as cx: cfg = get_settings(cx)
    return dist, max(cfg['minimum_xof'], math.ceil(dist) * cfg['per_km_xof'])


def event(cx, order_id, status, note):
    cx.execute('INSERT INTO events(order_id,status,note,created_at) VALUES(?,?,?,?)',
               (order_id, status, note, utc_now()))


def try_dispatch(cx):
    """Assign oldest waiting orders to the nearest online, idle rider with a GPS fix.
    Riders with active orders are not available until delivery completes.
    """
    cfg = get_settings(cx)
    waiting = cx.execute('SELECT * FROM orders WHERE status=? AND rider_id IS NULL ORDER BY id', ('pending',)).fetchall()
    for order in waiting:
        candidates = cx.execute('''SELECT u.* FROM users u WHERE u.role='rider' AND u.enabled=1 AND u.online=1
            AND u.lat IS NOT NULL AND u.lng IS NOT NULL AND u.last_location_at >= ?
            AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.rider_id=u.id AND o.status NOT IN ('delivered','cancelled'))''',
            ((datetime.now(timezone.utc) - timedelta(seconds=cfg['gps_max_age_seconds'])).isoformat(timespec='seconds'),)).fetchall()
        candidates = [r for r in candidates if fresh_gps(r['last_location_at'], cfg['gps_max_age_seconds'])]
        if not candidates:
            break
        weights = []
        for candidate in candidates:
            average = cx.execute('SELECT AVG(rating) FROM orders WHERE rider_id=? AND rating IS NOT NULL', (candidate['id'],)).fetchone()[0]
            score = average if average is not None and cfg['rating_dispatch_enabled'] else 5
            distance = haversine(order['pickup_lat'], order['pickup_lng'], candidate['lat'], candidate['lng'])
            weights.append(max(1, int((score / 5) ** 2 * 10000 / (1 + distance))))
        draw = secrets.randbelow(sum(weights))
        for candidate, weight in zip(candidates, weights):
            draw -= weight
            if draw < 0:
                rider = candidate
                break
        cx.execute('UPDATE orders SET rider_id=?,status=?,updated_at=? WHERE id=? AND status=?',
                   (rider['id'], 'assigned', utc_now(), order['id'], 'pending'))
        event(cx, order['id'], 'assigned', f"Livreur attribué : {rider['name']}")


def public_order(cx, row):
    data = as_dict(row)
    if not data:
        return None
    data.pop('tracking_hash', None)
    data.pop('pickup_code_hash', None)
    data.pop('pickup_attempts', None)
    data.pop('pickup_locked_until', None)
    data['rider'] = None
    if row['rider_id']:
        rider = cx.execute('SELECT id,name,phone,lat,lng,last_location_at FROM users WHERE id=?', (row['rider_id'],)).fetchone()
        data['rider'] = as_dict(rider)
        # A completed delivery must never leak a rider's later movements.
        # An outdated last-known fix is not presented as live GPS.
        is_live = row['status'] not in ('delivered', 'cancelled') and fresh_gps(rider['last_location_at'])
        data['rider']['gps_live'] = is_live
        if not is_live:
            data['rider']['lat'] = None
            data['rider']['lng'] = None
    data['events'] = [dict(e) for e in cx.execute('SELECT status,note,created_at FROM events WHERE order_id=? ORDER BY id', (row['id'],)).fetchall()]
    return data


def get_user(request: Request, role: Optional[str] = None):
    header = request.headers.get('authorization', '')
    if not header.startswith('Bearer '):
        raise HTTPException(401, 'Connexion requise')
    token = header.removeprefix('Bearer ').strip()
    if not token:
        raise HTTPException(401, 'Session invalide')
    with db() as cx:
        row = cx.execute('''SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
            WHERE s.token_hash=? AND s.expires_at>?''',
            (hashlib.sha256(token.encode()).hexdigest(), utc_now())).fetchone()
    if not row or not row['enabled']:
        raise HTTPException(401, 'Session expirée, veuillez vous reconnecter')
    if role and row['role'] != role:
        raise HTTPException(403, 'Accès interdit')
    return as_dict(row)


class LoginBody(BaseModel):
    username: str
    password: str


class Point(BaseModel):
    lat: float
    lng: float
    label: str = Field(min_length=2, max_length=200)


class OrderBody(BaseModel):
    sender_name: str = Field(default='', max_length=90)
    sender_phone: str = Field(default='', max_length=40)
    customer_name: str = Field(min_length=2, max_length=90)
    customer_phone: str = Field(min_length=5, max_length=40)
    notes: str = Field(default='', max_length=400)
    pickup: Point
    dropoff: Point


class LocationBody(BaseModel):
    lat: float
    lng: float


class RiderCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=8, max_length=100)
    phone: str = Field(default='', max_length=40)


class AssignBody(BaseModel):
    rider_id: int


class StatusBody(BaseModel):
    status: str

class PickupCodeBody(BaseModel):
    code: str = Field(min_length=4, max_length=4)

class RatingBody(BaseModel):
    rating: int = Field(ge=1, le=5)
    comment: str = Field(default='', max_length=300)


@APP.get('/')
def index():
    return FileResponse(ROOT / 'static' / 'index.html')


@APP.get('/client')
@APP.get('/rider')
@APP.get('/admin')
def standalone_entry():
    return FileResponse(ROOT / 'static' / 'index.html')


@APP.get('/api/health')
def health():
    return {'ok': True, 'service': 'KOLI CI', 'time': utc_now()}


@APP.get('/api/config')
def public_config():
    # Browser-restricted Maps keys are intended to be public; never expose server secrets here.
    with db() as cx: cfg = get_settings(cx)
    return {'google_maps_api_key': os.environ.get('GOOGLE_MAPS_API_KEY', ''), 'pricing': {'per_km_xof': cfg['per_km_xof'], 'minimum_xof': cfg['minimum_xof']}}


@APP.post('/api/login')
def login(body: LoginBody):
    with db() as cx:
        user = cx.execute('SELECT * FROM users WHERE username=?', (body.username.strip(),)).fetchone()
        if not user or not user['enabled'] or not verify_password(body.password, user['password_hash']):
            raise HTTPException(401, 'Identifiants incorrects')
        token = secrets.token_urlsafe(40)
        cx.execute('INSERT INTO sessions(token_hash,user_id,expires_at) VALUES(?,?,?)',
                   (hashlib.sha256(token.encode()).hexdigest(), user['id'], (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat(timespec='seconds')))
        return {'token': token, 'user': {'id': user['id'], 'name': user['name'], 'role': user['role'], 'username': user['username']}}


@APP.get('/api/me')
def me(request: Request):
    user = get_user(request)
    return {'id': user['id'], 'name': user['name'], 'role': user['role'], 'online': bool(user['online']),
            'lat': user['lat'], 'lng': user['lng'], 'last_location_at': user['last_location_at']}


@APP.post('/api/logout')
def logout(request: Request):
    get_user(request)
    token = request.headers.get('authorization', '').removeprefix('Bearer ').strip()
    with db() as cx:
        cx.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))
    return {'ok': True}


@APP.post('/api/orders')
def create_order(body: OrderBody):
    for p in (body.pickup, body.dropoff):
        validate_point(p.lat, p.lng)
    distance, price = estimate(body.pickup.lat, body.pickup.lng, body.dropoff.lat, body.dropoff.lng)
    token = secrets.token_urlsafe(32)
    pickup_code = f'{secrets.randbelow(10000):04d}'
    with db() as cx:
        code = 'KC-' + secrets.token_hex(4).upper()
        now = utc_now()
        cur = cx.execute('''INSERT INTO orders(code,tracking_hash,pickup_code_hash,sender_name,sender_phone,customer_name,customer_phone,notes,
            pickup_label,pickup_lat,pickup_lng,dropoff_label,dropoff_lat,dropoff_lng,
            distance_km,price_xof,status,created_at,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (code, hashlib.sha256(token.encode()).hexdigest(), hashlib.sha256((hashlib.sha256(token.encode()).hexdigest() + ':' + pickup_code).encode()).hexdigest(), body.sender_name.strip(), body.sender_phone.strip(), body.customer_name.strip(), body.customer_phone.strip(), body.notes.strip(),
             body.pickup.label, body.pickup.lat, body.pickup.lng, body.dropoff.label, body.dropoff.lat, body.dropoff.lng,
             distance, price, 'pending', now, now))
        event(cx, cur.lastrowid, 'pending', 'Commande enregistrée')
        try_dispatch(cx)
        order = public_order(cx, cx.execute('SELECT * FROM orders WHERE id=?', (cur.lastrowid,)).fetchone())
    return {'tracking_token': token, 'pickup_code': pickup_code, 'order': order}


@APP.get('/api/orders/{order_id}/track')
def track(order_id: int, key: str):
    with db() as cx:
        row = cx.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
        if not row or not hmac.compare_digest(row['tracking_hash'], hashlib.sha256(key.encode()).hexdigest()):
            raise HTTPException(404, 'Commande introuvable')
        return public_order(cx, row)


@APP.post('/api/rider/location')
def rider_location(body: LocationBody, request: Request):
    user = get_user(request, 'rider')
    validate_point(body.lat, body.lng)
    with db() as cx:
        cx.execute('UPDATE users SET lat=?,lng=?,last_location_at=? WHERE id=?', (body.lat, body.lng, utc_now(), user['id']))
        try_dispatch(cx)
    return {'ok': True}


@APP.post('/api/rider/online')
def rider_online(request: Request, body: dict):
    user = get_user(request, 'rider')
    online = body.get('online')
    if not isinstance(online, bool):
        raise HTTPException(422, 'Valeur online requise')
    with db() as cx:
        cx.execute('UPDATE users SET online=? WHERE id=?', (int(online), user['id']))
        if online:
            try_dispatch(cx)
    return {'online': online}


@APP.get('/api/rider/orders')
def rider_orders(request: Request):
    user = get_user(request, 'rider')
    with db() as cx:
        orders = cx.execute('''SELECT * FROM orders WHERE rider_id=? ORDER BY
            CASE WHEN status IN ('delivered','cancelled') THEN 1 ELSE 0 END, id DESC LIMIT 60''', (user['id'],)).fetchall()
        return [public_order(cx, o) for o in orders]


@APP.patch('/api/rider/orders/{order_id}/status')
def rider_change_status(order_id: int, body: StatusBody, request: Request):
    user = get_user(request, 'rider')
    with db() as cx:
        row = cx.execute('SELECT * FROM orders WHERE id=? AND rider_id=?', (order_id, user['id'])).fetchone()
        if not row:
            raise HTTPException(404, 'Commande non attribuée')
        next_status = {'assigned': 'to_pickup', 'picked_up': 'delivering', 'delivering': 'delivered'}.get(row['status'])
        if body.status != next_status:
            raise HTTPException(409, 'Transition de statut incorrecte')
        if next_status == 'delivered':
            if not fresh_gps(user.get('last_location_at')):
                raise HTTPException(409, 'GPS du livreur trop ancien')
            if haversine(user['lat'], user['lng'], row['dropoff_lat'], row['dropoff_lng']) > get_settings(cx)['completion_radius_m'] / 1000:
                raise HTTPException(409, f"Le livreur doit être à moins de {get_settings(cx)['completion_radius_m']} m de la destination")
        cx.execute('UPDATE orders SET status=?,updated_at=? WHERE id=?', (next_status, utc_now(), order_id))
        event(cx, order_id, next_status, {'to_pickup': 'Livreur en route vers le point de retrait',
              'picked_up': 'Colis récupéré', 'delivering': 'Colis en livraison', 'delivered': 'Colis livré'}[next_status])
        if next_status == 'delivered':
            try_dispatch(cx)
        return public_order(cx, cx.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone())

@APP.post('/api/rider/orders/{order_id}/pickup')
def verify_pickup(order_id: int, body: PickupCodeBody, request: Request):
    user = get_user(request, 'rider')
    with db() as cx:
        row = cx.execute('SELECT * FROM orders WHERE id=? AND rider_id=?', (order_id, user['id'])).fetchone()
        if not row or row['status'] != 'to_pickup':
            raise HTTPException(409, 'Commande non prête pour le code de retrait')
        if row['pickup_locked_until'] and row['pickup_locked_until'] > utc_now():
            raise HTTPException(429, 'Trop de tentatives, réessayez dans 10 minutes')
        if not hmac.compare_digest(row['pickup_code_hash'], hashlib.sha256((row['tracking_hash'] + ':' + body.code).encode()).hexdigest()):
            attempts = row['pickup_attempts'] + 1
            locked = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(timespec='seconds') if attempts >= 5 else None
            cx.execute('UPDATE orders SET pickup_attempts=?,pickup_locked_until=? WHERE id=?', (0 if locked else attempts, locked, order_id))
            cx.commit()
            raise HTTPException(409, 'Code de retrait incorrect')
        now = utc_now(); cx.execute("UPDATE orders SET status='picked_up',pickup_verified_at=?,updated_at=? WHERE id=?", (now, now, order_id))
        event(cx, order_id, 'picked_up', 'Code de retrait vérifié, colis récupéré')
        return public_order(cx, cx.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone())

@APP.post('/api/orders/{order_id}/rate')
def rate_order(order_id: int, body: RatingBody, key: str):
    with db() as cx:
        row = cx.execute('SELECT * FROM orders WHERE id=?', (order_id,)).fetchone()
        if not row or not hmac.compare_digest(row['tracking_hash'], hashlib.sha256(key.encode()).hexdigest()):
            raise HTTPException(404, 'Commande introuvable')
        if row['status'] != 'delivered': raise HTTPException(409, 'La commande n’est pas terminée')
        if row['rating'] is not None: raise HTTPException(409, 'Cette commande a déjà été évaluée')
        cx.execute('UPDATE orders SET rating=?,rating_comment=? WHERE id=?', (body.rating, body.comment.strip(), order_id))
        return {'ok': True, 'rating': body.rating}


@APP.get('/api/admin/dashboard')
def admin_dashboard(request: Request):
    get_user(request, 'admin')
    with db() as cx:
        orders = [public_order(cx, o) for o in cx.execute('SELECT * FROM orders ORDER BY id DESC LIMIT 100').fetchall()]
        riders = [{k: u[k] for k in ('id','name','username','phone','online','lat','lng','last_location_at')} for u in
                  cx.execute("SELECT * FROM users WHERE role='rider' ORDER BY id").fetchall()]
        return {'orders': orders, 'riders': riders}


@APP.post('/api/admin/riders')
def create_rider(body: RiderCreate, request: Request):
    actor = get_user(request, 'admin')
    with db() as cx:
        try:
            cur = cx.execute('INSERT INTO users(name,username,password_hash,role,phone,created_at) VALUES(?,?,?,?,?,?)',
                             (body.name.strip(), body.username.strip().lower(), hash_password(body.password), 'rider', body.phone.strip(), utc_now()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, 'Identifiant déjà utilisé')
        cx.execute('INSERT INTO admin_audit(actor_id,action,object_id,reason,created_at) VALUES(?,?,?,?,?)',
                   (actor['id'], 'rider_create', str(cur.lastrowid), body.name.strip(), utc_now()))
        return {'id': cur.lastrowid, 'name': body.name.strip(), 'username': body.username.strip().lower()}


register_admin(APP, globals())
with db() as cx:
    try_dispatch(cx)
app = APP
