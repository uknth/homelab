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
| Portainer | codified | via API (needs codifying in role) | 🟢 live (AuthMethod=OAuth, SSO) |
| Kavita | codified | pending (0.8.2 OIDC support TBD) | ⏳ provider ready |
| Beszel | codified | pending (PocketBase OAuth) | ⏳ provider ready |
| Jellyfin | codified | pending (SSO plugin install) | ⏳ provider ready |
| Dozzle | n/a | trusted-header (`forward-proxy`) | ⏳ pending |

**Codification gap:** Portainer's OAuth settings were applied via the Portainer
API by hand; fold into the `portainer` role for full reproducibility.
