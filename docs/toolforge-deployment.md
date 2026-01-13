# Toolforge Deployment Guide

This document describes how to deploy career-images to [Toolforge](https://wikitech.wikimedia.org/wiki/Help:Toolforge), the Wikimedia Cloud Services platform.

## Overview

Toolforge is Wikimedia's free hosting platform for community tools. This project is designed to run there with:
- **Web frontend**: Flask app serving the review interface
- **Database**: MariaDB on ToolsDB for persistent storage
- **Data pipeline**: Fetcher script to populate career data from Wikidata

## Prerequisites

### Codebase Configuration (Already Included)

The repository includes all necessary Toolforge configuration:

| File | Purpose |
|------|---------|
| `Procfile` | Tells Build Service how to run the app (`gunicorn`) |
| `service.template` | Webservice settings (type, health check path) |
| `app.py` | Includes `/healthz` health check endpoint |
| `db.py` | `MariaDBDatabase` class for ToolsDB integration |
| `pyproject.toml` | Includes `pymysql` and `toolforge` dependencies |

### Your Requirements

- A Wikimedia account (any wiki login works)
- Basic familiarity with SSH and command line
- ~30 minutes for initial setup

## Account Setup (One-time)

### 1. Create a Wikimedia Developer Account

1. Go to [toolsadmin.wikimedia.org/register](https://toolsadmin.wikimedia.org/register/)
2. Click "Login using Wikimedia account" (use your Wikipedia/Commons login)
3. Create an **LDAP username** (for Gerrit/Toolsadmin login)
4. Create a **UNIX shell username** (for SSH access - this becomes your SSH login)
5. Set a password and agree to terms of service

**Important**: Your UNIX shell username cannot be changed later. Choose carefully.

### 2. Request Toolforge Membership

1. Log in to [toolsadmin.wikimedia.org](https://toolsadmin.wikimedia.org/)
2. Click your username → "Manage projects"
3. Request membership in the "toolforge" project
4. Wait for approval (typically 1-7 days)

You'll receive a notification when approved.

### 3. Add SSH Key

1. Generate an SSH key if you don't have one:
   ```bash
   ssh-keygen -t ed25519 -C "your-email@example.com"
   ```

2. Copy your public key:
   ```bash
   cat ~/.ssh/id_ed25519.pub
   ```

3. Add it at [toolsadmin.wikimedia.org](https://toolsadmin.wikimedia.org/) → Settings → SSH keys

4. Test the connection:
   ```bash
   ssh <your-shell-username>@login.toolforge.org
   ```

### 4. Create a Tool Account

1. SSH to Toolforge: `ssh <username>@login.toolforge.org`
2. Or use Toolsadmin web interface: Tools → "Create new tool"
3. Choose a tool name (e.g., `career-images`)
   - Tool names must be lowercase, alphanumeric, with hyphens allowed
   - The URL will be `https://<toolname>.toolforge.org/`
4. Fill in description: "Tool to improve diversity in Wikipedia career article images"
5. Log out and back in to access the new tool account

## Deployment

### 1. Connect to Toolforge

```bash
ssh <your-username>@login.toolforge.org
```

### 2. Switch to Tool Account

```bash
become <toolname>
# e.g., become career-images

# Verify you're in the tool account
whoami
# Should show: tools.<toolname>
```

### 3. Clone the Repository

```bash
cd ~
git clone https://github.com/tieguy/career-images.git
cd career-images
```

### 4. Initialize the Database

The database schema is created automatically on first run, but you need to populate the career data:

```bash
# Access a shell with Python environment
toolforge webservice buildservice shell

# Inside the shell, run the fetcher
launcher python fetcher.py fetch --limit 100  # Start with 100 for testing

# For full dataset (~4000 careers + pageviews, takes ~30 min)
launcher python fetcher.py fetch

# Exit the shell
exit
```

### 5. Start the Web Service

```bash
toolforge webservice buildservice start
```

The Build Service will:
- Detect Python from `pyproject.toml`
- Install all dependencies automatically
- Run gunicorn per the `Procfile`
- Set up health checks per `service.template`

### 6. Verify Deployment

Your tool will be available at:
```
https://<toolname>.toolforge.org/
```

Check health endpoint:
```bash
curl https://<toolname>.toolforge.org/healthz
# Should return: OK
```

Check service status:
```bash
toolforge webservice status
```

## Database Details

### Automatic Configuration

The application automatically:
- Detects Toolforge via presence of `~/replica.my.cnf`
- Reads credentials from that file (auto-created with tool account)
- Connects to ToolsDB at `tools.db.svc.wikimedia.cloud`
- Creates database named `s<number>__careers` (based on tool's UID)
- Initializes schema on first connection

**No manual database setup is required.**

### Database Schema

Two tables are created:
- `careers` - Career entries with pageviews, review status, notes
- `career_images` - Images associated with careers

### Accessing the Database Directly

```bash
# From within tool account
become <toolname>
sql tools

# Then:
USE s12345__careers;  # Replace with your actual DB name
SELECT COUNT(*) FROM careers;
```

## Common Operations

### Service Management

```bash
# Check service status
toolforge webservice status

# View recent logs
toolforge webservice logs

# View more logs
toolforge webservice logs -n 100

# Restart service (after code updates)
toolforge webservice restart

# Stop service
toolforge webservice stop

# Start service
toolforge webservice buildservice start
```

### Updating the Code

```bash
become <toolname>
cd ~/career-images
git pull origin main
toolforge webservice restart
```

### Refreshing Career Data

```bash
become <toolname>
toolforge webservice buildservice shell

# Inside shell:
launcher python fetcher.py fetch          # Full refresh
launcher python fetcher.py resume         # Continue interrupted fetch
launcher python fetcher.py stats          # Show current data stats

exit
```

### Accessing Python Shell

```bash
toolforge webservice buildservice shell
launcher python
```

## Environment Differences

| Aspect | Local Development | Toolforge |
|--------|-------------------|-----------|
| Database | SQLite (`careers.db`) | MariaDB (ToolsDB) |
| Detection | No `~/replica.my.cnf` | Has `~/replica.my.cnf` |
| URL | `http://localhost:5000` | `https://<tool>.toolforge.org` |
| Python | Your local version | Buildpack-managed |
| Persistence | Local filesystem | NFS home directory |

## Push-to-Deploy (Optional)

Toolforge supports automatic deployment when you push to a Git repository:

1. Set up integration with Wikimedia GitLab or GitHub
2. Configure webhook in your repository settings
3. Pushes to main branch trigger automatic redeploy

See [Toolforge push-to-deploy documentation](https://wikitech.wikimedia.org/wiki/Help:Toolforge/Build_Service#Push-to-deploy) for detailed setup.

## Troubleshooting

### Service Won't Start

```bash
# Check logs for errors
toolforge webservice logs

# Common issues:
# - Syntax error in Procfile
# - Missing dependency in pyproject.toml
# - Port binding issues (use 0.0.0.0, not localhost)
```

### Database Connection Fails

```bash
# Verify config file exists
ls -la ~/replica.my.cnf

# Check database name (should be s<number>__careers)
become <toolname>
echo $USER  # Shows tools.s<number>
```

### Health Check Fails

- Verify `/healthz` endpoint returns HTTP 200
- Check `service.template` has `health-check-path: /healthz`
- Ensure app starts quickly (health check timeout is limited)

### Import Errors

```bash
# Access shell and test imports
toolforge webservice buildservice shell
launcher python -c "import flask; import pymysql; print('OK')"
```

### "Permission denied" Errors

- Make sure you ran `become <toolname>` before operations
- Check file permissions in tool home directory

### Fetcher Timeouts

The Wikidata/Wikipedia APIs have rate limits. If fetcher fails:
```bash
# Resume from where it left off
launcher python fetcher.py resume
```

## Monitoring

### Check Tool Status

- Web: Visit `https://<toolname>.toolforge.org/`
- Health: `curl https://<toolname>.toolforge.org/healthz`
- Logs: `toolforge webservice logs`

### Toolforge Status Page

Check for platform-wide issues: [Toolforge Status](https://wikitech.wikimedia.org/wiki/Help:Toolforge#Known_issues)

## Security Notes

- Never commit `~/replica.my.cnf` or database credentials
- The tool runs in a sandboxed container
- HTTPS is automatic and required
- User data should follow Wikimedia privacy policies

## References

- [Toolforge Quickstart](https://wikitech.wikimedia.org/wiki/Help:Toolforge/Quickstart)
- [Toolforge Build Service](https://wikitech.wikimedia.org/wiki/Help:Toolforge/Build_Service)
- [Toolforge ToolsDB](https://wikitech.wikimedia.org/wiki/Help:Toolforge/ToolsDB)
- [Toolforge Web Service](https://wikitech.wikimedia.org/wiki/Help:Toolforge/Web)
- [python-toolforge library](https://python-toolforge.readthedocs.io/)
- [Toolforge FAQ](https://wikitech.wikimedia.org/wiki/Help:Toolforge/FAQ)
