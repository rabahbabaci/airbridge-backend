# AirBridge Backend

Production backend repository for **AirBridge** — the door-to-gate departure decision engine.

This repo is intentionally scaffolded with **professional structure, documentation, and contracts only**.

- ✅ Architecture and product docs included
- ✅ API contracts defined
- ✅ Operational/security docs included
- ✅ Cursor-friendly project setup included
- ❌ No application code yet (by design)

---

## Purpose

This backend will power the full AirBridge app lifecycle:

1. Intake user trip inputs
2. Resolve transport + airport + flight context
3. Compute leave-home recommendation with confidence
4. Recompute on live changes (traffic/flight/TSA/gate)
5. Notify user when recommendation changes materially

---

## Repository Status

Current stage: **Planning / Design**

This repo is a blueprint before coding starts.

---

## Professional Folder Structure

```bash
airbridge-backend/
├── README.md
├── .gitignore
├── .env.example
├── CONTRIBUTING.md
├── LICENSE
├── CURSOR_SETUP.md
├── docs/
│   ├── architecture/
│   ├── api/
│   ├── product/
│   ├── operations/
│   └── security/
├── src/
├── tests/
├── scripts/
├── .vscode/
│   ├── settings.json
│   └── extensions.json
└── .github/workflows/
    └── ci-placeholder.yml
```

---

## Cursor Smooth Start

1. Open folder in Cursor
2. Read `CURSOR_SETUP.md`
3. Follow `docs/product/requirements.md`
4. Implement from `docs/api/contracts.md`

---

## Next Step (after planning sign-off)

1. Confirm API contracts (`docs/api/contracts.md`)
2. Confirm non-functional targets (`docs/operations/environments.md`)
3. Create initial service skeleton under `src/`
4. Add CI checks and schema validation
