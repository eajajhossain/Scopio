# Deploying the Scopio live demo

This runs the **exact Docker Compose stack you verified locally** on a single small
cloud VM — `db/init.sql` (schema + RLS + roles + seed), the `app_rls` non-superuser
role, the `scopio` superuser used by the admin dashboard, the API, and the ARQ worker,
all identical to `docker compose up`. No managed-database surgery, nothing to re-wire.

Recommended host: **Oracle Cloud *Always Free*** (an ARM Ampere VM, free forever). A
~$5/mo Hetzner/DigitalOcean droplet is the drop-in fallback if Oracle's signup or ARM
capacity blocks you — every step below is identical on any Ubuntu 22.04+ box.

---

## 0. Secrets (generate once, keep safe)

These were generated for this deploy — **`SECRET_KEY` also encrypts stored SMTP
passwords, so if you lose it, connected email accounts must reconnect:**

```
SECRET_KEY=YmHmHTMqcU-gL5DB0wZ3PiQ2lEp-t6dOWqnQEBFdApv6yosh5JMM2I9YWQt8crEM
POSTGRES_PASSWORD=oj7QEfdZ4iBFz2KSsxiJShcL8vtAseJh
```

Regenerate anytime with:
`python -c "import secrets; print(secrets.token_urlsafe(48))"`

---

## 1. Provision the VM

1. Create an Oracle Cloud account → **Compute → Instances → Create instance**.
2. Image **Canonical Ubuntu 22.04**, shape **VM.Standard.A1.Flex** (Always Free:
   up to 4 OCPU / 24 GB). Add your SSH public key.
3. After it boots, note the **public IP**.
4. **Open the firewall** (two layers on Oracle):
   - VCN → Security List → add Ingress rules for TCP **80** and **443** (and **8000**
     if you'll skip the HTTPS proxy) from `0.0.0.0/0`.
   - On the VM itself: `sudo iptables -I INPUT -p tcp --dport 80 -j ACCEPT && sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT && sudo netfilter-persistent save`

## 2. Install Docker

```bash
ssh ubuntu@<PUBLIC_IP>
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && exit      # re-login so the group applies
```

## 3. Get the code + write the prod env

```bash
ssh ubuntu@<PUBLIC_IP>
git clone <your-repo-url> scopio && cd scopio
cp .env.example .env
nano .env
```

Set these in `.env` (leave the rest at defaults):

```dotenv
SECRET_KEY=YmHmHTMqcU-gL5DB0wZ3PiQ2lEp-t6dOWqnQEBFdApv6yosh5JMM2I9YWQt8crEM
POSTGRES_PASSWORD=oj7QEfdZ4iBFz2KSsxiJShcL8vtAseJh
GROQ_API_KEY=<your gsk_… key>
TAVILY_API_KEY=<your tvly-… key>
ADMIN_EMAILS=eajajhossain890@gmail.com
CORS_ORIGINS=https://<your-demo-domain>      # or http://<PUBLIC_IP>:8000 for IP-only
```

> `docker-compose.prod.yml` requires `SECRET_KEY` and `POSTGRES_PASSWORD` and will
> refuse to start without them. The `app_rls` role password stays fixed (it's a
> limited role and the DB port is never published in prod), and the admin dashboard's
> `ADMIN_DATABASE_URL` is derived from `POSTGRES_PASSWORD` automatically.

## 4. Launch

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
docker compose ps                       # api, worker, db, redis should be Up
curl -s http://localhost:8000/health    # -> {"status":"ok"} (or similar)
```

The API listens on `:8000`. `db`/`redis` are **not** published publicly. (The dev-only
Adminer service is not part of the prod override.)

## 5. Seed the demo account + connect your email (fully-live demo)

Because `ENVIRONMENT=production`, the "Skip (demo)" fallback is off — visitors log in.
Create one shared demo account and connect your SMTP (the app password is **encrypted
at rest** via Fernet keyed from `SECRET_KEY`):

```bash
# Register the demo tenant/user (returns a token)
TOKEN=$(curl -s -X POST http://localhost:8000/auth/register \
  -H 'content-type: application/json' \
  -d '{"email":"demo@scopio.app","password":"scopio-demo","name":"Demo",
       "company_name":"Scopio Demo","services":"Eco-friendly reusable cup supplier for cafes"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")

# Connect your Gmail (use a Gmail App Password, not your login password)
curl -s -X POST http://localhost:8000/auth/connect_email \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"email":"<your-gmail>","app_password":"<16-char app password>"}'

# Fully autonomous send/reply (default is "review" = draft + approve):
curl -s -X PATCH http://localhost:8000/outreach/mode \
  -H "authorization: Bearer $TOKEN" -H 'content-type: application/json' \
  -d '{"mode":"autonomous"}'
```

The worker's IMAP cron polls the connected inbox every 2 minutes and the agent replies
autonomously. **Note:** in autonomous mode any visitor using the demo account can send
real email from your connected address and consume your Groq/Tavily quotas — this is the
"fully live" trade-off you chose. Switch back to `review` anytime with the same endpoint.

## 6. (Recommended) HTTPS + a clean URL

A raw `http://<ip>:8000` link looks broken on a CV. Front it with Caddy for automatic
Let's Encrypt TLS. Get a free hostname first (e.g. DuckDNS → `scopio.duckdns.org`
pointed at your VM IP), then:

```bash
# /etc/caddy/Caddyfile
scopio.duckdns.org {
    reverse_proxy localhost:8000
}
```

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
sudo systemctl restart caddy
```

Update `CORS_ORIGINS=https://scopio.duckdns.org` in `.env` and re-run the compose
`up -d` command so the API accepts the HTTPS origin.

## 7. Update the README

Fill in the **Live Demo** section (URL + demo credentials) and drop in the demo GIF.
Record the GIF locally (target ≤ ~8 MB so GitHub inlines it):

- Windows: **ScreenToGif** (free) → export optimized GIF into `docs/demo.gif`.
- Show: enter a location → discovery pins on the map → "Deep research" enriches a
  business → an outreach draft is generated.

```bash
git add docs/demo.gif README.md && git commit -m "docs: live demo URL + GIF" && git push
```

---

## Maintenance

- Logs: `docker compose logs -f api worker`
- Update after a push: `git pull && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`
- Groq free keys expire → a 401 in logs means mint a new `GROQ_API_KEY` and re-run `up -d`.
- Postgres data persists in the `pgdata` volume across restarts/rebuilds.
