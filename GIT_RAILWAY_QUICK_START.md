# Quick Git & Railway Deployment Commands

## 🚀 Deploy to Railway in 5 Minutes

### Step 1: Commit Changes to Git
```powershell
cd "c:\Auto dashboard\Auto dashboard"

# Review what changed
git status

# Add all changes
git add .

# Commit
git commit -m "Production-ready: All security issues fixed, JWT auth, rate limiting"

# Push to GitHub
git push origin master
```

### Step 2: Deploy on Railway
1. Go to [railway.app](https://railway.app)
2. Click **"New Project"** → **"Deploy from GitHub repo"**
3. Select your repository → Railway auto-deploys!
4. Copy your Railway URL (e.g., `https://auto-dashboard-production.up.railway.app`)

### Step 3: Set Environment Variables
In Railway Dashboard → **Variables** → **RAW Editor**, paste:

```bash
# Copy all values from your .env.local file
# Update these two for production:
COOKIE_SECURE=true
ALLOWED_ORIGINS=https://your-railway-url.up.railway.app
```

See [RAILWAY_DEPLOYMENT_GUIDE.md](RAILWAY_DEPLOYMENT_GUIDE.md) for complete variable list.

### Step 4: Update Webhook URLs

**SignWell Dashboard:**
```
https://your-railway-url.up.railway.app/api/signwell/webhook
```

**Meta Developer Console:**
```
https://your-railway-url.up.railway.app/webhook
```

### ✅ Done!
Access your app at: `https://your-railway-url.up.railway.app/meta-login.html`

---

## 📝 Daily Git Workflow

### Make Changes & Deploy
```powershell
# Make your changes...

# Check what changed
git status

# Add specific files
git add backend/app.py
git add "Auto dashboard.html"

# Or add everything
git add .

# Commit with descriptive message
git commit -m "Fix: Description of what you fixed"

# Push to trigger automatic Railway deployment
git push origin master
```

### View Git History
```powershell
git log --oneline -10
```

### Undo Last Commit (before push)
```powershell
git reset --soft HEAD~1
```

### Discard Changes to a File
```powershell
git restore "filename.html"
```

### See What Changed in a File
```powershell
git diff backend/app.py
```

---

## 🔄 Railway Auto-Deploy

Every `git push` triggers Railway to:
1. ✅ Pull latest code
2. ✅ Install dependencies from `requirements.txt`
3. ✅ Run `playwright install chromium` (from `railway.json`)
4. ✅ Start with `gunicorn` (from `Procfile`)
5. ✅ Zero-downtime deployment

---

## 🚨 Troubleshooting

### "Please tell me who you are" error
```powershell
git config --global user.email "your-email@example.com"
git config --global user.name "Your Name"
```

### "Permission denied" on push
```powershell
# If using HTTPS, you may need a Personal Access Token
# GitHub → Settings → Developer settings → Personal access tokens
# Use token as password when prompted
```

### Railway build failed
```
# Check Railway logs in dashboard
# Common issues:
# - Missing environment variables
# - Syntax errors in code
# - Missing dependencies in requirements.txt
```

### Changes not showing on Railway
```
# 1. Verify git push succeeded
git push origin master

# 2. Check Railway deployment status in dashboard
# 3. View logs for errors
# 4. Trigger manual redeploy if needed (Railway dashboard)
```

---

## 📊 Git Status Legend

- **Untracked**: New files not in git yet (green when you `git add`)
- **Modified**: Existing files with changes (red)
- **Staged**: Files ready to commit (green after `git add`)
- **Deleted**: Files removed locally (red)

---

## ⚡ Pro Tips

### Create a .gitignore for Temporary Files
Already configured! See [.gitignore](.gitignore)

### Never Commit Secrets
✅ `.env.local` is already in `.gitignore`
✅ `.env.production` is already in `.gitignore`
✅ Use Railway environment variables instead

### Commit Message Best Practices
```
✅ Good: "Fix: Login authentication not working on Railway"
✅ Good: "Add: Rate limiting to prevent abuse"
✅ Good: "Update: Facebook API token"

❌ Bad: "changes"
❌ Bad: "updates"
❌ Bad: "stuff"
```

### View Railway Deployment Logs
```
# In Railway Dashboard → Deployments → View Logs
# Look for:
# - ✅ "Supabase client initialized successfully"
# - ⚙️  "SocketIO async_mode: eventlet"
# - 🔒 "CORS allowed origins: https://..."
```

---

## 🎯 Next Steps After Deployment

1. **Test everything** - Use the deployment guide checklist
2. **Monitor logs** - Watch for errors in Railway dashboard
3. **Update webhooks** - Point Meta & SignWell to Railway URL
4. **Test rate limiting** - Make sure it works in production
5. **Backup database** - Export Supabase data regularly

See full deployment guide: [RAILWAY_DEPLOYMENT_GUIDE.md](RAILWAY_DEPLOYMENT_GUIDE.md)
