# Kanagatly License Manager (vendor-only)

A **local, localhost-only** web panel that the *vendor* runs on their own
machine to **create and monitor** licensed Kanagatly VMS clients. It replaces
hand-running `backend/tools/issue_license.py` with a point-and-click UI.

It holds the Ed25519 **private signing key**. It is **NOT** deployed to
customers, and it binds to `127.0.0.1` only — never `0.0.0.0`.

It reuses the exact same crypto the VMS uses to verify licenses
(`backend/app/licensing.py`), so every license minted here verifies
byte-identically inside the customer's app.

---

## Run it

**Easiest — the launcher script** (sets up the venv/deps on first run and opens
the browser for you):

```bash
./license-manager/run.sh
```

Or manually — the panel reuses the backend's `cryptography`, so the simplest
path is the backend's virtualenv (it already has fastapi + uvicorn + cryptography):

```bash
cd /path/to/dahua_mahua
source backend/.venv/bin/activate      # or your own venv with requirements.txt
python license-manager/app.py
```

It prints:

```
[license-manager] Kanagatly License Manager running at http://127.0.0.1:7070
```

Open **http://127.0.0.1:7070**.

If you prefer a fresh venv:

```bash
python -m venv license-manager/.venv
source license-manager/.venv/bin/activate
pip install -r license-manager/requirements.txt
python license-manager/app.py
```

---

## First run — the keypair

On first launch, if `vendor-keys/private_key.pem` is missing, the panel
**generates a fresh Ed25519 keypair** into `license-manager/vendor-keys/`:

- `private_key.pem` — the **signing key**. **KEEP SAFE. NEVER COMMIT / SHARE.**
  Anyone with this file can mint valid licenses for your product. Back it up
  offline. It is `.gitignore`d and never exposed over the API.
- `public_key.pem` — the **verification key**. Safe to distribute.

> Already have a keypair from `issue_license.py genkey`? Drop your existing
> `private_key.pem` + `public_key.pem` into `license-manager/vendor-keys/`
> before the first run, so the panel signs with your established key.

### Install the public key into the VMS (once)

For the VMS to trust your licenses, copy the public key into it:

```bash
cp license-manager/vendor-keys/public_key.pem backend/licensing/public_key.pem
```

The panel's header has a **Copy PEM** button for the same purpose. The VMS
loads that file (or the `LICENSE_PUBLIC_KEY` env var) to verify every license.

---

## Workflow (per customer)

1. **Customer sends you their machine fingerprint.** They get it from the VMS
   License screen (a 32-char hex string; it's `licensing.machine_fingerprint()`).
2. **Create the client** in the panel: paste the fingerprint, set name/company,
   pick an expiry date (or check *Perpetual*), tick feature checkboxes, set
   camera/NVR caps, add notes → **Create & mint license**.
3. The panel mints a **signed `.lic`** and stores the client. It **self-verifies**
   the license against the public key before saving (same check as
   `issue_license.py`) — a mint that wouldn't verify is refused.
4. **Send the customer the `.lic`** (Download .lic button). They **upload it** in
   the VMS License screen. The VMS verifies the signature, checks the fingerprint
   matches their machine, and checks expiry.
5. **Renew / extend:** *Re-issue* re-mints a fresh license (new expiry / features)
   bound to the same machine. Send them the new `.lic`.

The clients table shows live status: **Active**, **Expiring soon** (< 30 days),
**Expired**, or **Perpetual**, with days-left.

---

## API (all on `127.0.0.1:7070`)

| Method | Path | Purpose |
| ------ | ---- | ------- |
| `GET`  | `/api/pubkey` | public key PEM (text) |
| `GET`  | `/api/clients` | list clients + computed status/days-left |
| `GET`  | `/api/clients/{id}` | one client incl. its license JSON |
| `POST` | `/api/clients` | create + mint (name, company, hardware_id, expires\|null, features[], max_cameras, max_nvrs, notes) |
| `PUT`  | `/api/clients/{id}` | edit + **re-mint** (new expiry/features) |
| `DELETE` | `/api/clients/{id}` | remove from registry |
| `GET`  | `/api/clients/{id}/license.lic` | download the `.lic` file |

The private key is **never** served over the API.

---

## Files

```
license-manager/
├── app.py                 # FastAPI app (127.0.0.1:7070), reuses app.licensing
├── static/index.html      # self-contained dark UI (no build, no CDN)
├── requirements.txt
├── README.md
├── .gitignore             # ignores data/, vendor-keys/, *.db, *.lic
├── data/clients.db        # SQLite registry           (gitignored, auto-created)
└── vendor-keys/           # private_key.pem + public_key.pem (gitignored)
```

The registry (`data/clients.db`) stores each client's name, company,
hardware_id, issued/expires dates, features, max_cameras, max_nvrs, notes, and
the last minted signed license blob.

## License format

Minted licenses match `backend/app/licensing.py` exactly. The signed fields are
`customer, site_id, issued, expires, features, max_cameras, max_nvrs,
hardware_id`, plus a base64 Ed25519 `sig` over the canonical (sorted-keys,
compact) JSON of those fields. `expires: null` = perpetual. `customer` is set
from the client's company (falling back to name); `site_id` is left blank.
