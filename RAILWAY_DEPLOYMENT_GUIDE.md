# Railway Deployment Guide

## ✅ Pre-Deployment Checklist

All 15 production issues have been resolved:
- ✅ JWT authentication with HTTP-only cookies
- ✅ CORS configuration
- ✅ WSGI/Gunicorn with eventlet worker
- ✅ N+1 database query optimization
- ✅ SSRF protection in export-pdf
- ✅ Streamlit URL configuration
- ✅ SessionStorage auth migration
- ✅ Console.log cleanup for production
- ✅ Rate limiting (flask-limiter)
- ✅ SignWell webhook verification
- ✅ MAX_CONTENT_LENGTH configuration
- ✅ Debug endpoints gating
- ✅ datetime.utcnow() deprecation fix
- ✅ Powerbroker.html typo fix
- ✅ Eventlet import side-effects fix

## 📦 Step 1: Git Setup & Push

### 1.1 Review Changes
```bash
cd "c:\Auto dashboard\Auto dashboard"
git status
```

### 1.2 Add All Changes
```bash
git add .
```

### 1.3 Commit Changes
```bash
git commit -m "Production-ready deployment: All 15 security issues resolved + JWT auth + Rate limiting"
```

### 1.4 Push to GitHub
```bash
git push origin master
```

## 🚂 Step 2: Railway Setup

### 2.1 Create Railway Account
1. Go to [railway.app](https://railway.app)
2. Sign up with GitHub
3. Authorize Railway to access your repositories

### 2.2 Deploy from GitHub
1. Click **"New Project"**
2. Select **"Deploy from GitHub repo"**
3. Choose your `Auto dashboard` repository
4. Railway will automatically detect the Flask app

### 2.3 Configure Build (Automatic)
Railway will use:
- `requirements.txt` for dependencies
- `Procfile` for start command
- `railway.json` for build configuration

## ⚙️ Step 3: Environment Variables

Set these in Railway Dashboard → Variables:

### Required Variables
```bash
# Supabase
VITE_SUPABASE_URL=https://urmklmzfdjslzahbfvkl.supabase.co
VITE_SUPABASE_SERVICE_ROLE_KEY=<your-service-role-key>

# Meta/Facebook API
META_APP_ID=1374336741109403
META_APP_SECRET=ca57447d436108c0452657bb084f8632
META_PAGE_ID=775140625692611
META_PAGE_ACCESS_TOKEN=<your-updated-token>
META_LEAD_FORM_ID=1459691498852435
META_WEBHOOK_VERIFY_TOKEN=insurance_dashboard_webhook
FB_PIXEL_ID=2251357192000496
FB_PIXEL_TOKEN=<your-pixel-token>

# JWT Authentication
JWT_SECRET_KEY=<your-secret-key>
ADMIN_EMAIL=policy@meta.com
ADMIN_PASSWORD_HASH=<your-bcrypt-hash>

# SignWell
SIGNWELL_API_KEY=<your-api-key>
SIGNWELL_WEBHOOK_SECRET=<your-webhook-secret>

# Security & Configuration
COOKIE_SECURE=true
ALLOWED_ORIGINS=https://<your-railway-app>.up.railway.app
MAX_UPLOAD_MB=32

# Email (MS Office)
MS_OFFICE_EMAIL=eldho.george@kmibrokers.com
MS_OFFICE_EMAIL_PASSWORD=<your-password>
MS_OFFICE_SMTP_SERVER=smtp.office365.com
MS_OFFICE_SMTP_PORT=587
DELIVERY_EMAIL=eldho.george@kmibrokers.com

# Azure/MS Graph
AZURE_TENANT_ID=<your-tenant-id>
AZURE_CLIENT_ID=<your-client-id>
AZURE_CLIENT_SECRET=<your-client-secret>
MS_GRAPH_REFRESH_TOKEN=<your-refresh-token>

# Google Cloud / Vertex AI
GOOGLE_CLOUD_PROJECT=vertex-ai-document-praser
GOOGLE_CLOUD_PROJECT_ID=vertex-ai-document-praser
GOOGLE_CLOUD_LOCATION=us-central1
VERTEX_AI_MODEL=gemini-2.0-flash

# Zoho Sign
ZOHO_SIGN_CLIENT_ID=<your-client-id>
ZOHO_SIGN_CLIENT_SECRET=<your-client-secret>
ZOHO_SIGN_REFRESH_TOKEN=<your-refresh-token>
ZOHO_SIGN_API_BASE=https://sign.zohocloud.ca/api/v1
```

### Important: Update After First Deployment
After Railway assigns your URL (e.g., `https://your-app.up.railway.app`):

1. **Update ALLOWED_ORIGINS:**
   ```
   ALLOWED_ORIGINS=https://your-app.up.railway.app,http://localhost:5000
   ```

2. **Update SignWell Webhook URL** in SignWell Dashboard:
   ```
   https://your-app.up.railway.app/api/signwell/webhook
   ```

3. **Update Meta Webhook URL** in Meta Developer Console:
   ```
   https://your-app.up.railway.app/webhook
   ```

### Optional (If deploying Streamlit separately)
```bash
STREAMLIT_AUTO_URL=https://auto-coverpage.up.railway.app
STREAMLIT_TENANT_URL=https://tenant-coverpage.up.railway.app
```

## 🔒 Step 4: Security Verification

After deployment, verify security features:

### 4.1 Test JWT Authentication
```bash
curl https://your-app.up.railway.app/api/login \
  -X POST \
  -H "Content-Type: application/json" \
  -d '{"email":"policy@meta.com","password":"policy@123"}'
```

Should return HTTP-only cookie in response headers.

### 4.2 Test CORS
Access your Railway URL in a browser and check the Network tab:
- Should see `Access-Control-Allow-Origin` header
- Credentials should be included in requests

### 4.3 Test Rate Limiting
Make multiple rapid requests to `/api/login`:
```bash
for i in {1..10}; do curl https://your-app.up.railway.app/api/login -X POST; done
```

Should return `429 Too Many Requests` after 5 attempts.

### 4.4 Test Endpoints
- ✅ `/api/leads/from-facebook` - Should return leads with JWT
- ✅ `/api/health` - Should be public
- ✅ `/api/export-pdf` - Should have SSRF protection

## 📝 Step 5: Post-Deployment

### 5.1 Monitor Logs
In Railway Dashboard → Deployments → View Logs

Look for:
```
⚙️  SocketIO async_mode: eventlet
🔒 CORS allowed origins: https://your-app.up.railway.app
✅ Supabase client initialized successfully
📦 Max upload size: 32MB
```

### 5.2 Test Meta Dashboard
1. Navigate to `https://your-app.up.railway.app/meta-login.html`
2. Login with `policy@meta.com` / `policy@123`
3. Click "Load Leads" → Should see 136+ leads
4. Click "Process Leads" → Should open Auto dashboard with lead data

### 5.3 Test Auto Dashboard
1. Navigate to `https://your-app.up.railway.app/Auto%20dashboard.html`
2. Login if needed
3. Fill out form and click "Save Data"
4. Should save to Supabase successfully

## 🔄 Continuous Deployment

Railway automatically redeploys on every `git push` to master:

```bash
# Make changes locally
git add .
git commit -m "Description of changes"
git push origin master

# Railway will automatically:
# 1. Pull latest code
# 2. Install dependencies
# 3. Run build command
# 4. Restart with new code
```

## 🚨 Troubleshooting

### Build Fails
- Check Railway logs for missing dependencies
- Verify `requirements.txt` is in root directory
- Check Python version (should be 3.13)

### App Won't Start
- Verify `Procfile` is correct
- Check environment variables are set
- Look for errors in Railway logs

### CORS Errors
- Update `ALLOWED_ORIGINS` to include Railway URL
- Restart deployment after updating env vars

### Rate Limiting Too Aggressive
- Update rate limits in `backend/app.py` lines 71-77
- Commit and push changes

### Webhook Not Working
- Update webhook URLs in Meta/SignWell dashboards
- Verify `SIGNWELL_WEBHOOK_SECRET` is set correctly

## 📊 Environment Variables Summary

Copy `.env.local` values to Railway, but update these:

| Variable | Local Value | Railway Value |
|----------|-------------|---------------|
| `COOKIE_SECURE` | `false` | `true` |
| `ALLOWED_ORIGINS` | `http://localhost:5000` | `https://your-app.up.railway.app` |
| `STREAMLIT_AUTO_URL` | `http://localhost:8502` | (Deploy separately if needed) |
| `STREAMLIT_TENANT_URL` | `http://localhost:8503` | (Deploy separately if needed) |

## ✅ Deployment Complete!

Your production-ready Flask app is now live on Railway with:
- ✅ JWT authentication
- ✅ CORS protection
- ✅ Rate limiting
- ✅ SSRF protection
- ✅ Secure cookies
- ✅ Input validation
- ✅ Webhook verification
- ✅ All 15 security issues resolved
