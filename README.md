# Job Application & Recruitment Management Platform — Backend

FastAPI backend for the 3-day recruitment-platform task. The code is organized so Render's start command `uvicorn main:app --host 0.0.0.0 --port $PORT` can import `app.main` from the repository root.

## Features
- JWT bearer authentication and Candidate, Recruiter, Admin roles
- Recruiter/admin job creation, updates, publish/close workflow, search/filter/sort/pagination
- Candidate applications with a database uniqueness constraint preventing duplicate applications
- PDF and DOCX resume upload, text extraction, explainable keyword overlap score
- Application status history, recruiter notes, activity logs, notifications
- Interview scheduling, rescheduling/cancellation, recruiter overlap conflict check
- Recruitment overview analytics, health check, OpenAPI/Swagger docs, notification WebSocket endpoint
- PostgreSQL via `DATABASE_URL`; SQLite fallback for local quick start

## Local setup
1. Use Python 3.11 or 3.12.
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   # Windows: .venv\\Scripts\\activate
   # macOS/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   ```
3. Set environment variables. At minimum, set a random `SECRET_KEY`. For local quick start you can use the default SQLite URL, but change the key before sharing the service.
4. Run:
   ```bash
   uvicorn app.main:app --reload
   ```
5. Open `/docs` and register a Candidate and Recruiter. To create an Admin account, set `BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD` (minimum 12 characters) before starting the server. The admin is created once if that email does not already exist.

## Render deployment
- Upload the **contents** of this project to the repository root, not the ZIP file itself.
- This ZIP is intentionally flat for easier upload from a mobile phone: keep `main.py`, `requirements.txt`, and `render.yaml` together at the repository root.
- Create a PostgreSQL database and set `DATABASE_URL` to its internal connection URL. If using Render's PostgreSQL URL, the code converts `postgres://` and `postgresql://` to the psycopg v3 driver form.
- Set `SECRET_KEY` to a unique long random value, and optionally set `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD` (12+ characters) and `BOOTSTRAP_ADMIN_NAME` in the service environment. Do not put real secrets in GitHub.
- Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- On a free web service, local uploaded resume files may not persist across restarts/redeploys. For production, use object storage for resumes and a persistent database.

## API quick guide
All protected endpoints use `Authorization: Bearer <access_token>`.

### Auth
- `POST /auth/register` — create Candidate or Recruiter account (`name`, `email`, `password`, optional `role`)
- `POST /auth/login` — returns JWT token
- `GET /auth/me` — current user
- `GET /users?role=Candidate` — Admin only

### Jobs
- `POST /jobs` — Recruiter/Admin
- `GET /jobs?search=python&location=remote&status_filter=published&page=1&page_size=10&sort_by=newest`
- `GET /jobs/{job_id}`
- `PATCH /jobs/{job_id}` — owner recruiter or Admin
- `DELETE /jobs/{job_id}` — deletes if no applications, otherwise closes the job

### Applications
- `POST /jobs/{job_id}/apply` — Candidate, JSON `{ "cover_letter": "..." }`
- `POST /applications/{application_id}/resume` — multipart file field `file`, PDF/DOCX only
- `GET /applications` — role-scoped list, with optional `status_filter`, `job_id`, `candidate_id`, `search`, `page`, `page_size`
- `GET /applications/{application_id}`
- `PATCH /applications/{application_id}/status` — `{ "status": "Shortlisted", "recruiter_notes": "..." }`
- `PATCH /applications/{application_id}/notes` — `{ "recruiter_notes": "..." }`
- `GET /applications/{application_id}/match`
- `GET /applications/{application_id}/history`

Statuses: `Applied`, `Shortlisted`, `Interview`, `Selected`, `Rejected`.

### Interviews, notifications, analytics
- `POST /interviews` — `{ "application_id": 1, "scheduled_at": "2030-01-01T10:00:00Z", "duration_minutes": 30, "interview_type": "Online", "meeting_link": "https://...", "notes": "" }`
- `GET /interviews?upcoming_only=true`
- `PATCH /interviews/{interview_id}` — reschedule or set status to `Cancelled` / `Completed`
- `GET /notifications?unread_only=true`
- `PATCH /notifications/{notification_id}/read`
- `GET /analytics/overview` — Recruiter/Admin
- `GET /activity-logs` — Recruiter/Admin
- `WS /ws/notifications?token=<JWT>` — authenticated WebSocket connection with ping/pong; notifications are persisted and available through the REST endpoint.
- `GET /health`, `GET /docs`, `GET /openapi.json`

## Resume matching logic
Required skills are matched case-insensitively as exact keyword/phrase substrings in extracted resume text. If the job has no `required_skills`, the backend derives up to 20 candidate keywords from the job description after removing a small stop-word list. Score = matched keywords / total keywords × 100, rounded to the nearest integer. This is a transparent baseline heuristic, not an AI model, semantic similarity measure, or hiring recommendation. Scanned/image-only PDFs may return little or no text because OCR is not included.

## Important limitations for a 3-day prototype
- Uploads are stored locally; configure durable object storage for production.
- WebSocket currently establishes an authenticated connection but does not push cross-request notifications in real time; use a connection manager plus Redis/pub-sub for production real-time delivery.
- No email delivery, OCR, virus scanning, refresh tokens, password reset, or automated migrations are included.
- Interview overlap validation is application-level; for high concurrency use database exclusion constraints/transactions.
- Review privacy, retention, file security, and authorization requirements before production use.
