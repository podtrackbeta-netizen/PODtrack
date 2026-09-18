# FieldPOD Gmail backend

A small Flask service that lets the app pull GE / Takkion tracker + POD
emails straight from a Gmail mailbox, download the attachment, and hand the
milestone data to the Inbox review-and-apply flow.

```
server/
  gmail_service.py   the HTTP service
  connect.py         one-shot Google sign-in
  tracker.py         xlsx (feeder-block) -> CSV, shared with the flatten script
  requirements.txt
  .env.example
```

## 1. Google Cloud — one time (~5 min)

1. https://console.cloud.google.com → create a project (e.g. "FieldPOD").
2. **APIs & Services → Library →** search *Gmail API* → **Enable**.
3. **APIs & Services → OAuth consent screen**:
   - User type **External**, fill the required name/email, **Save**.
   - **Audience → Test users → Add** the mailbox you'll read (e.g. the
     shared commissioning inbox). While the app is "Testing" only listed
     test users can connect — that's fine.
   - Scopes: you can leave it empty here; the service requests
     `gmail.readonly` at sign-in.
4. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type **Desktop app**.
   - **Download JSON** → save it as `server/credentials.json`.

`credentials.json` and `token.json` are secrets — keep them out of git
(`server/.gitignore` already lists them).

## 2. Install + connect

```bash
cd server
python -m pip install -r requirements.txt
python connect.py          # opens a browser, sign in, approve read-only Gmail
```

`connect.py` writes `server/token.json`. It auto-refreshes; you only redo
this if you revoke access or switch accounts (delete `token.json`).

## 3. Run

```bash
python gmail_service.py     # http://127.0.0.1:8000
```

Leave it running alongside the app / `standalone.html`. The standalone's
**Pull inbox** button calls it automatically when it's up (and falls back to
the built-in sample when it isn't).

## Endpoints

| Method + path | Purpose |
|---|---|
| `GET  /api/health` | `{ok, authed}` |
| `GET  /api/auth/status` | `{connected, email}` |
| `POST /api/auth/login` | run the consent flow (browser opens on this machine) |
| `POST /api/auth/logout` | delete the cached token |
| `GET  /api/inbox?query=&max=` | `{ok, emails:[InboxEmail…]}` — `query` overrides `GMAIL_QUERY`; `not_connected` if unauthenticated |
| `GET  /api/attachment/<messageId>/<attachmentId>?name=` | `{ok, name, text}` — `.xlsx` is flattened to CSV, `.csv/.tsv` decoded |

`InboxEmail` matches the app's type (`id`, `provider`, `from`, `fromName`,
`subject`, `date`, `snippet`, `body`, `imported`, `attachments[]`), plus
`messageId` for the attachment call. `attachments[].isTracker` flags likely
tracker files by name/mime.

## Wiring the real app (`src/`)

`src/lib/mail.ts` proxies here when `VITE_GMAIL_SERVICE` (or
`GMAIL_SERVICE_URL`) is set — default `http://127.0.0.1:8000`. `searchPodEmails`
→ `/api/inbox`, `fetchTrackerText` → `/api/attachment/...`. No `@/lib/app-data`
connector needed.

## Notes / hardening

- Read-only scope; the service never sends or deletes mail.
- Bound to `127.0.0.1`. If you host it, put it behind auth and set
  `ALLOWED_ORIGINS` to your app's origin.
- For an unattended/shared setup, swap the Desktop OAuth client for a
  **service account with domain-wide delegation** (Workspace only) so no
  interactive sign-in is needed — `load_credentials()` is the only function
  that changes.
