# Environment Setup Guide

This document explains **where to obtain each environment variable** required by the **Human‑in‑the‑Loop (HITL) Job Outreach & Application Screener Agent** and provides a **sample `.env` file** (with all values omitted for security).

---

## 1️⃣ OpenAI (Free‑tier LLM)
- **Variable**: `OPENAI_API_KEY`
- **How to get**: Sign‑up at https://platform.openai.com/account/api-keys and create a new secret key. The free tier includes `gpt‑4o‑mini` which the project uses as the primary model.

---

## 2️⃣ Real Email Dispatch (SMTP)
| Variable | What it is | Where to find it |
|----------|------------|-------------------|
| `SMTP_HOST` | Mail server host (e.g., `smtp.gmail.com`) | Provider documentation (Gmail, Outlook, etc.) |
| `SMTP_PORT` | Port for TLS – usually `587` | Provider docs |
| `SMTP_USER` | The email address that will send the messages | Your own email account |
| `SMTP_PASS` | App‑specific password or SMTP credential (not your main password) | Generate in your email account security settings |
| `REAL_DISPATCH` | Set to `true` to actually send emails; `false` keeps everything sandboxed | **Never** commit the value – just set it in `.env` |

---

## 3️⃣ LinkedIn Outreach
Two options are supported:
### a) LinkedIn API (preferred when available)
- **Variable**: `LINKEDIN_API_TOKEN`
- **How to get**: Create a LinkedIn Developer App at https://www.linkedin.com/developers/, request the `r_messaging` permission, and generate a **User Access Token** (valid 60 days).
### b) Selenium / Browser‑automation fallback
- **Variable**: `LINKEDIN_SELENIUM_COOKIE`
- **How to get**: See the **"How to obtain a Selenium cookie"** section below.
- **What it does**: The Selenium worker launches a headless Chrome instance, injects the cookie, navigates to the target profile or messaging page, and programmatically types the drafted note. The note is posted **as your own LinkedIn account** because the session carries your authentication cookie.
- **Limitations**: Requires Chrome/Chromium in the container, may be blocked by LinkedIn rate‑limits, and may need occasional manual CAPTCHA solving if LinkedIn flags the automation. For a demo it works fine, but for production you should aim for the official API.

---

## 4️⃣ Search Service (Job‑board Scraper)
- **Variable**: `TAVILY_API_KEY` (optional)
- **How to get**: Sign‑up at https://tavily.com/, copy the key from the dashboard. If the key is absent the code falls back to a simple DuckDuckGo request.

---

## 5️⃣ Supabase (Remote Storage)
- **Variable**: `SUPABASE_URL`
  - *Where*: Settings → API → **Project URL** on the Supabase dashboard.
- **Variable**: `SUPABASE_KEY`
  - *Where*: Settings → API → **Service Role**. Click **Create new secret**; this is the *service‑role* key that has full read/write access to both the storage bucket and the auto‑created `job_posts` table.
- **Optional**: `SUPABASE_BUCKET` (defaults to `resumes`) and `SUPABASE_TABLE` (defaults to `job_posts`). Change only if you created custom bucket/table names.
- **Why the Service‑Role key?** The agent needs **write** permissions to upload resumes, store scraped job postings, and write the final approved outreach JSON. The anon key is read‑only and will cause permission errors.

---

## 6️⃣ LangSmith (Optional Tracing)
- **Variable**: `LANGSMITH_API_KEY`
- **How to get**: Sign‑up at https://smith.langchain.com/, copy the API key from the dashboard. This enables end‑to‑end tracing of LangGraph executions.

---

## 7️⃣ Miscellaneous
- `ENABLE_ENCRYPTION` – set to `false` (we rely on Supabase TLS). 
- `APP_PORT` / `UI_PORT` – ports for FastAPI and Streamlit; default `8000` and `8501`.

---

## 🔐 How to Obtain a LinkedIn Selenium Cookie
The Selenium fallback works by injecting the **`li_at`** authentication cookie that browsers use for a logged‑in LinkedIn session.

### Step‑by‑step
1. **Log in** to LinkedIn with your normal account in a desktop browser (Chrome/Edge/Firefox).
2. Open **Developer Tools** (`F12` or `Ctrl+Shift+I`).
3. Navigate to the **Application** (or **Storage**) tab → **Cookies** → `https://www.linkedin.com`.
4. Locate the cookie named **`li_at`**. It will have a long alphanumeric value.
5. **Copy the entire value** (do **not** include the name, only the value).
6. Paste it into your `.env` file like:
   ```dotenv
   LINKEDIN_SELENIUM_COOKIE=li_at=YOUR_COOKIE_VALUE_HERE
   ```

### Cookie Expiration
- The `li_at` cookie expires when you **log out**, **clear cookies**, or after a period of inactivity (typically 30‑90 days). When it expires the Selenium dispatcher will receive an authentication error.
- **What to do**: Provide a UI setting in the Streamlit app (under *Settings → LinkedIn*) where users can paste a fresh cookie. The backend exposes a FastAPI endpoint `POST /settings/linkedin_cookie` that updates the environment at runtime (the new value is stored in memory and also written back to `.env` for persistence).

### Refresh workflow
1. When a dispatch fails with `AuthenticationError`, the UI shows a **toast** prompting the user to re‑enter the cookie.
2. The user opens the *LinkedIn Settings* panel, pastes the new `li_at` value, and clicks **Save**.
3. The app calls the `/settings/linkedin_cookie` endpoint, which updates the environment variable and retries the dispatch.

---

## 📄 Sample `.env` (values omitted)
```dotenv
# LLM
OPENAI_API_KEY=your_openai_key

# Real email dispatch
REAL_DISPATCH=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your_email@example.com
SMTP_PASS=your_smtp_app_password

# LinkedIn – choose ONE
#LINKEDIN_API_TOKEN=your_linkedin_api_token
LINKEDIN_SELENIUM_COOKIE=li_at=YOUR_COOKIE_VALUE_HERE

# Search (optional)
#TAVILY_API_KEY=your_tavily_key

# Supabase – required
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=service_role_XXXXXXXXXXXXXXXXXXXXXXXXXXXX
# Optional custom names
#SUPABASE_BUCKET=resumes
#SUPABASE_TABLE=job_posts

# LangSmith (optional)
#LANGSMITH_API_KEY=your_langsmith_key

# Misc
ENABLE_ENCRYPTION=false
APP_PORT=8000
UI_PORT=8501
```

> **Never commit the real values** – keep them only in your local `.env` file. The repository’s `.gitignore` already excludes any `*.env` file.

---

### What to Do Next
1. Create a file named `.env` in the project root and paste the *sample* above, replacing the placeholders with the real secrets you already have.
2. Run `docker compose up --build` to start the API, Streamlit UI, and the scheduler.
3. The app will now upload resumes to Supabase, scrape job listings, and allow you to send outreach via SMTP and LinkedIn (API or Selenium).

Feel free to reach out if you need help installing Chrome/Chromedriver for the Selenium fallback.
