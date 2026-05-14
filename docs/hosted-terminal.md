# Hosted Terminal

This setup exposes a real shell running on this workstation through a local
`ttyd` service, then publishes it through the existing Cloudflare Tunnel on its
own hostname.

## Local Service

- Service: `terminal-dagent.service`
- Control script: `scripts/terminalctl`
- Local URL: `http://127.0.0.1:7681`
- Config file: `~/.config/dagent/terminal.env`
- Binary: `~/.local/bin/ttyd`

Install and start it:

```bash
scripts/terminalctl up
scripts/terminalctl doctor
```

Useful commands:

```bash
scripts/terminalctl status
scripts/terminalctl logs
scripts/terminalctl restart
scripts/terminalctl creds
```

By default the service:

- binds only to `127.0.0.1`
- relies on Cloudflare Access for remote protection
- launches your login shell from your home directory
- stays available as long as the user service is running

Edit `~/.config/dagent/terminal.env` if you want to change the bind address,
port, title, start directory, or optional `ttyd` basic-auth credentials.

## Cloudflare Hostname

Suggested hostname:

```text
terminal.divyeshvishwakarma.com
```

This workstation runs `cloudflared` as a host system service using a
remotely-managed tunnel token, so the hostname and Access policy must be added
from the Cloudflare dashboard or API instead of a local YAML file.

Current tunnel details from this machine:

```text
Tunnel ID: 76cfb69c-d1c9-47d3-9af0-6628f42c29fb
Origin service for terminal: http://127.0.0.1:7681
```

The tunnel already serves:

- `ssh.divyeshvishwakarma.com`
- `dlogs.divyeshvishwakarma.com`
- `ntfy.divyeshvishwakarma.com`
- `n8n.divyeshvishwakarma.com`
- `dagent.divyeshvishwakarma.com`
- `vscode.divyeshvishwakarma.com`

## Add The Public Hostname

In Cloudflare Zero Trust:

```text
Zero Trust > Networks > Connectors > Cloudflare Tunnels
Open tunnel: 76cfb69c-d1c9-47d3-9af0-6628f42c29fb
Public hostnames > Add a public hostname
```

Use these values:

```text
Subdomain: terminal
Domain: divyeshvishwakarma.com
Path: leave blank
Type: HTTP
URL: 127.0.0.1:7681
```

Save the hostname before creating the Access app.

## Set Up Access

Create a dedicated Access application for the terminal:

```text
Zero Trust > Access controls > Applications > Create new application
Application type: Self-hosted and private
Add public hostname
Hostname: terminal.divyeshvishwakarma.com
Name: Hosted Terminal
Session Duration: 30m
```

Recommended policy for the terminal:

```text
Action: Allow
Include: Emails > divyesh1099@gmail.com
Exclude: leave blank
Require: leave blank for now unless WARP is already deployed
Policy Session Duration: 30m
```

Do not add a Bypass policy for this hostname. This service is a full shell on
the host machine, so it should stay behind Cloudflare Access.

## Turn On Independent MFA

If independent MFA is not already enabled for your Zero Trust organization,
turn it on first:

```text
Zero Trust > Access controls > Access settings
Allow multi-factor authentication (MFA):
  Enable: Security key, Biometrics, Authenticator application
  Authentication duration: 0m
Use identity provider MFA: Off
```

For the terminal application itself, use custom MFA settings:

```text
Zero Trust > Access controls > Applications > Hosted Terminal > Configure
Authentication > MFA
Custom MFA settings
Allowed methods:
  Security key
  Biometrics
  Authenticator application
Authentication duration: 0m
```

Notes:

- `0m` means Access will require MFA on every new login to the app.
- Leave `Use identity provider MFA` off if you want Cloudflare to prompt for a
  real second factor instead of accepting Google MFA as the only MFA event.
- If you want the strongest browser-based setup, enroll both a hardware key and
  a TOTP app so you still have a recovery path.

## Enroll MFA Devices

After you enable independent MFA, enroll your authenticators through the Access
App Launcher:

```text
https://<your-team-name>.cloudflareaccess.com/AddMfaDevice
```

Recommended enrollment order:

1. Security key
2. Biometrics
3. Google Authenticator or another TOTP app

If you only want Google Authenticator, you can enroll just the authenticator
application, but a hardware key is stronger and is worth keeping as a backup.

## Optional Hardening With WARP

Once the basic setup works, add a device check so the terminal only opens from
devices running your Cloudflare One Client:

```text
Zero Trust > Traffic policies > Traffic settings
Enable: Allow Secure Web Gateway to proxy traffic

Zero Trust > Reusable components > Posture checks
Add check: WARP

Zero Trust > Access controls > Applications > Hosted Terminal > Configure
Policies > Edit Allow policy
Require: WARP
```

Do this only after WARP is installed and enrolled on the device you will use,
or you can lock yourself out.

If you later move `cloudflared` into the Docker Compose container, use this
service URL instead:

```text
host.docker.internal:7681
```

After that, the hosted shell should open at:

```text
https://terminal.divyeshvishwakarma.com/
```

You will pass through Cloudflare Access first. If you later set
`TERMINAL_USERNAME` and `TERMINAL_PASSWORD` in `~/.config/dagent/terminal.env`,
`ttyd` will prompt for those too.
