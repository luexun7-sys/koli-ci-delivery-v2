# Coding-agent instructions (Codex-ready)

## Objective
Independent Abidjan parcel delivery platform, **not a Yango clone using its branding**. French-first UX, mobile responsive. Three application sections: `/client`, `/rider`, `/admin`.

## Architecture
- `server.py`: FastAPI + SQLite, role sessions, token-based order tracking, auto-dispatch and status transitions.
- `static/index.html`: responsive French UI.
- `static/style.css`: styling.
- `static/app.js`: vanilla JS, Leaflet/OpenStreetMap; direct API calls.
- `tests/test_flow.py`: backend end-to-end HTTP tests.

## Requirements
1. Do not replace working functions with static image mockups.
2. Protect admin/rider operations with bearer authorization; customer tracking via secret token.
3. Never trust client-submitted fares or driver assignments.
4. Always test `python -m unittest discover -s tests -v` and `node --check static/app.js`.
5. Prefer French copy and Ivorian FCFA; clearly label demo prices as examples.
6. Do not claim background iOS GPS or live routing without real integrations.
7. Prioritize security, abuse throttles, database migrations and verified rider onboarding before production deployment.

## Next milestones
- PostgreSQL + row locking for scalable dispatch, WebSocket updates, rider identity verification.
- Real routing and geocoding provider account (Google Maps or Mapbox) and backend billing safeguards.
- Live push notifications; persistent iOS/Android background GPS (native mobile apps).
- Deployment scripts for HTTPS, environment variables, secure passwords, audit logs, PII retention and monitoring.
- Parcel types, photos, pricing zones, WhatsApp/SMS confirmation, Mobile Money collection only after compliance review.

## V2 details
- Docker host on `0.0.0.0`, single-worker SQLite; production persistence needs volume `/data`.
- Separate installable manifests for `/client`, `/rider`, `/admin`, with shared API and mobile-only customer tabs.
- New GPS freshness check (120 seconds), and completed jobs must not return rider GPS.
- Cloud accounts are not connected. Never claim public hosting or URLs exist until provisioned and checked.

