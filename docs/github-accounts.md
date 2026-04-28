# GitHub Accounts

## Goal

Support two GitHub accounts without letting an agent accidentally push from the wrong identity.

## Recommended Repo Layout

Keep repos grouped by account:

```text
~/work/personal/<repo>
~/work/client/<repo>
```

Then map them explicitly in `worker/config.yml`:

```yaml
repos:
  personal-app:
    path: /home/you/work/personal/personal-app
    github_account: personal
    allowed_intents:
      - repo_status
      - codex_task

  client-api:
    path: /home/you/work/client/client-api
    github_account: client
    allowed_intents:
      - repo_status
      - claude_task
```

## SSH Config

Use different host aliases:

```sshconfig
Host github-personal
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519_personal
  IdentitiesOnly yes

Host github-client
  HostName github.com
  User git
  IdentityFile ~/.ssh/id_ed25519_client
  IdentitiesOnly yes
```

Repo remotes then look like:

```text
git@github-personal:username/repo.git
git@github-client:org/repo.git
```

## Git Identity

Set local identity per repo:

```bash
git config user.name "Your Name"
git config user.email "personal@example.com"
```

For the second account:

```bash
git config user.name "Your Name"
git config user.email "client@example.com"
```

## Worker Policy

The worker does not accept arbitrary repo paths. It accepts repo names and resolves them through config.

This lets n8n say:

```json
{
  "repo": "client-api",
  "intent": "repo_status"
}
```

without exposing:

```text
/home/you/work/client/client-api
```

