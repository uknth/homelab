# Authentication — Single Sign-On with Authentik

**Every user-facing service sits behind one identity provider: Authentik.** There is exactly
one place to log in, one set of credentials, one MFA policy. No service ships its own separate
login for end users where it can be avoided.

Authentik runs on **util01** (server + worker + PostgreSQL + Redis), reached at
`auth.puhome.net`. nginx on **gw01** is the enforcement point.

## Why Authentik (vs. Authelia / Keycloak)

- **Authelia** is lighter but config-file driven and OIDC-provider support is newer/leaner.
- **Keycloak** is heavyweight and Java — overkill for a homelab.
- **Authentik** gives a UI-driven provider catalog, embedded forward-auth outpost, OIDC + SAML
  + LDAP + proxy providers in one, and good app integration docs. Best fit for "one SSO for
  everything" without hand-writing config for each app.

## Three integration tiers

Not every app authenticates the same way. Each service uses the highest tier it supports:

| Tier | Mechanism | Use when | Examples |
|---|---|---|---|
| 1. Native OIDC/SAML | App delegates login to Authentik directly | app has first-class OIDC/OAuth support | **Paperless-ngx (live)**, Portainer, n8n (planned) |
| 2. Forward-auth (proxy) | nginx `auth_request` → Authentik outpost; app sees a trusted header | app has no SSO but a proxy can gate it | Glance, Dozzle, Uptime Kuma, Prowlarr/Sonarr/Radarr UIs |
| 3. Local auth + OIDC plugin | app keeps its own session but authenticates against Authentik | app forces its own login and can't be proxied cleanly | Jellyfin (SSO plugin) |

Tier 2 is the default for anything that would otherwise be wide open. Tier 1 is preferred
where available (cleaner logout, per-app authorization). Tier 3 is the exception, documented
per-service.

## Forward-auth flow (Tier 2)

```
client ─▶ nginx @gw01 (service.puhome.net)
             │  auth_request → Authentik embedded outpost (auth.puhome.net)
             │      ├─ no valid session → 302 to Authentik login
             │      └─ valid session   → 200 + identity headers
             ▼
           upstream app (util01 / cmp01)   ← receives X-authentik-* headers
```

Concretely, every protected nginx vhost includes a shared snippet that:
1. Exposes `/outpost.goauthentik.io/` proxied to the Authentik outpost.
2. Adds `auth_request /outpost.goauthentik.io/auth/nginx;` to the app's `location`.
3. On 401, redirects to the Authentik sign-in flow.
4. Passes `X-authentik-username` / `-email` / `-groups` upstream for apps that read them.

This snippet lives in the `services/network/nginx` role and is applied per-vhost via a flag
(e.g. `nginx_sites[*].sso: true`), so protecting a new service is a one-line change.

## What stays outside SSO

- **Authentik itself** (`auth.puhome.net`) — it is the login; it can't sit behind itself.
- **Infrastructure endpoints** not exposed to users: Blocky/DNS, the certbot ACME path,
  container-to-container API calls (e.g. arr apps talking to each other, Diun→ntfy). These use
  network scoping / API tokens, not user SSO.
- **Break-glass:** SSH to hosts remains key-based and independent of Authentik, so a broken
  Authentik never locks you out of the fleet.

## Bootstrap / secrets

- `vault_authentik_secret_key`, `vault_authentik_postgres_password`, and the OIDC client
  secrets per Tier-1 app live in the vault (`vault_*`).
- Authentik's own admin account (`akadmin`) is created on first boot; its bootstrap password /
  token comes from the vault and should be rotated after first login.
- Tier-1 apps each need an OIDC provider + application defined in Authentik; where practical
  these are declared as code via Authentik **blueprints** (YAML) templated by the role, so the
  provider config is reproducible rather than click-configured.

## Dependency ordering

Authentik must be up **before** the services it protects. It deploys in the util01 phase
(observability/platform), ahead of the compute apps — see
[`../plan/roadmap.md`](../plan/roadmap.md).


## OIDC rollout status (2026-08-22)

Reproducible OIDC provider creation lives in `services/auth/authentik_config`
(`authentik_oidc_apps` list) — it bakes in the two API-creation gotchas that
otherwise break every provider: **`grant_types`** (`authorization_code`+`refresh_token`)
and **scope `property_mappings`** (openid/email/profile), plus fixed client
credentials from the vault so providers are reproducible.

| App | Authentik provider | App-side config | Status |
|---|---|---|---|
| Paperless | codified | role env (allauth OIDC) | 🟢 live |
| Portainer | codified | **codified in role** (API config) | 🟢 live |
| Kavita | codified | oidcConfig via API | 🟢 live (0.9.x; in-place upgrade; authority set) |
| Beszel | codified | **codified in role** (PocketBase API + `USER_CREATION`) | 🟢 live & verified (sso:false + USER_CREATION + email_verified — see note) |
| Jellyfin | codified | SSO plugin (installed+configured via API) | 🟢 live (login button added; /sso/OID/start/authentik) |
| Dozzle | n/a | trusted-header (`forward-proxy`) | 🟢 live (auto-login via Remote-* headers) |

**Codified in roles:** Portainer + Beszel OAuth (via API, idempotent). **Still via ad-hoc API (codify next):** Jellyfin (wizard+plugin+SSO config) and Kavita (admin+oidcConfig).

### Tier-1 OIDC apps must NOT also be forward-authed (2026-08-22)

A Tier-1 app does its **own** OIDC round-trip to Authentik and needs its OAuth
**callback** URL reachable. If that app's vhost is *also* `sso: true` (Tier-2
forward-auth), nginx intercepts the callback and 302s it to the Authentik outpost
before the app can consume the `code` — the login silently fails.

This bit **Beszel**: its callback `https://metrics.puhome.net/api/oauth2-redirect`
was behind forward-auth, so PocketBase never saw the code (blank/again-login loop).
Fix: **`metrics` and `docker` (Portainer) are now `sso: false`** in
`hosts/host_vars/gw01/vars.yml`, matching Paperless/Jellyfin/Kavita. Rule of thumb:
**Tier-1 (own OIDC) ⇒ `sso: false`; Tier-2 (forward-auth) / trusted-header ⇒ `sso: true`.**

**Beszel had a *second*, independent blocker.** Even with the callback reachable,
the OIDC token exchange failed with `403 "Only superusers can perform this action"`
on `POST /api/collections/users/auth-with-oauth2`: Beszel's `users` collection ships
`createRule: null` (superuser-only), and with **zero** users existing every OIDC login
tried to *create* a user and was denied — the popup died blank. Fix: set
**`USER_CREATION=true`** in the Beszel compose (`beszel_user_creation`, codified in the
role). Beszel then relaxes the rule to `createRule: "@request.context = 'oauth2'"`, so
OIDC (and only OIDC) auto-provisions the user. The auto-created user must be verified —
`authRule: verified=true` — which Authentik satisfies via `email_verified`. Diagnosis
came straight from PocketBase's own request logs (`/api/logs`, superuser).

**And a *fourth* layer.** With creation allowed, it then failed `400 "email cannot
be blank"`. PocketBase's generic OIDC extractor **drops the email claim unless
`email_verified` is true**, and Authentik's stock *email* scope mapping hardcodes
`email_verified: False`. Fix (codified in `authentik_config`): patch that scope
mapping to `email_verified: bool(request.user.email)`. This is a shared default, so
it also hardens OIDC for any other strict consumer. **Net: Beszel OIDC took four
independent fixes** — `sso:false`, `USER_CREATION=true`, the create rule (auto), and
`email_verified`.

### Jellyfin admin (2026-08-22)

The wizard admin is **`admin`** / `vault_jellyfin_admin_password` (manual-login form,
not the Authentik button). Authentik SSO users land as **non-admin** by default;
`uknth` was promoted to admin via the API. This promotion is **not yet codified** — a
Jellyfin rebuild resets it. Codify-next: the SSO plugin's admin-role mapping (grant
Jellyfin admin from an Authentik group) alongside the wizard/plugin config.

## Known blocker: .NET HttpClient GitHub downloads on cmp01

Jellyfin-SSO-plugin install and Kavita 0.9.x's first-boot migration both fail the
same way: a **.NET HttpClient download from GitHub times out (100s)** on cmp01,
while `curl` from the same container fetches the file in <1s (IPv4, DNS fine).
**Root cause (found 2026-08-22):** GitHub's CDN (`*.githubusercontent.com`) round-robins
four Fastly anycast edges `185.199.108-111.133`; **`.109.133` is blackholed** over the WAN
(20s timeout, 0 B/s) while the others are fast — likely a broken route on one of the dual-WAN
ISPs. Any client hitting `.109` hangs; curl retries, .NET (single-shot) times out.
**Fix:** Blocky pins `githubusercontent.com` -> `185.199.110.133` (a working edge) —
`blocky_pinned_hosts`. This unblocks the Jellyfin plugin install and Kavita 0.9.x.
The proper long-term fix is at the Omada router (repair the WAN route to `.109`).

### Jellyfin native-client login (2026-08-23)

SSO (the Authentik button) only works in the **web** player; native clients
(TV/phone apps) can't do the OAuth redirect and need a username/password. The
`uknth` SSO account had no local password, so native logins were denied. Fix: set
a local password on `uknth` (currently = the Jellyfin admin password) — web SSO
still works, native clients use `uknth` + password. Not codified (per-user secret);
recorded here.

### Jellyfin SSO kept demoting the admin (2026-08-23)

`uknth` reverted to non-admin after each Authentik login: the SSO plugin had
`EnableAuthorization: true` with an **empty `AdminRoles`** (and wasn't even
requesting the `groups` scope), so every login recomputed roles, matched no admin
group, and demoted the user. Fix: set **`EnableAuthorization: false`** so the
plugin stops managing roles, then promote `uknth` once (sticks). Runtime change to
`SSO-Auth.xml` (not codified). Group-based alternative for multi-user later: add
the `groups` scope to the provider + plugin and set `AdminRoles` to an Authentik
admin group.
