from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from uuid import uuid4

import jwt
from docx import Document as DocxDocument
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from pypdf import PdfReader
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine, func, or_
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

APP_NAME = "Job Application & Recruitment Management Platform API"
SECRET_KEY = os.getenv("SECRET_KEY", "development-only-change-this-secret-before-deploying")
JWT_ALGORITHM = "HS256"
TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "1440"))
MAX_RESUME_BYTES = int(os.getenv("MAX_RESUME_MB", "5")) * 1024 * 1024
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", "uploads"))
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def normalize_database_url(value: str) -> str:
    if value.startswith("postgres://"):
        return "postgresql+psycopg://" + value[len("postgres://"):]
    if value.startswith("postgresql://") and "+psycopg" not in value:
        return "postgresql+psycopg://" + value[len("postgresql://"):]
    return value

DATABASE_URL = normalize_database_url(os.getenv("DATABASE_URL", "sqlite:///./recruitment.db"))
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(180), index=True)
    company: Mapped[str] = mapped_column(String(180))
    location: Mapped[str] = mapped_column(String(180), default="Remote")
    employment_type: Mapped[str] = mapped_column(String(50), default="Full-time")
    description: Mapped[str] = mapped_column(Text)
    required_skills: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (UniqueConstraint("job_id", "candidate_id", name="uq_job_candidate_application"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    candidate_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    resume_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    resume_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    resume_text: Mapped[str] = mapped_column(Text, default="")
    matching_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    matching_skills: Mapped[str] = mapped_column(Text, default="")
    missing_keywords: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(30), default="Applied", index=True)
    cover_letter: Mapped[str] = mapped_column(Text, default="")
    recruiter_notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

class Interview(Base):
    __tablename__ = "interviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)
    recruiter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_minutes: Mapped[int] = mapped_column(Integer, default=30)
    interview_type: Mapped[str] = mapped_column(String(30), default="Online")
    meeting_link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="Scheduled")
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class ActivityLog(Base):
    __tablename__ = "activity_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    application_id: Mapped[Optional[int]] = mapped_column(ForeignKey("applications.id"), nullable=True, index=True)
    action: Mapped[str] = mapped_column(String(100))
    details: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

class Notification(Base):
    __tablename__ = "notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(180))
    message: Mapped[str] = mapped_column(Text)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

Base.metadata.create_all(bind=engine)

app = FastAPI(title=APP_NAME, version="1.0.0", description="Recruitment workflow API with JWT auth, resume keyword matching, interviews, activity logs, notifications and analytics.")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "*").split(","), allow_credentials=False, allow_methods=["*"], allow_headers=["*"])
security = HTTPBearer(auto_error=False)

class UserCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    role: str = "Candidate"

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class JobCreate(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    company: str = Field(min_length=2, max_length=180)
    location: str = "Remote"
    employment_type: str = "Full-time"
    description: str = Field(min_length=10)
    required_skills: list[str] = []
    status: str = "draft"

class JobUpdate(BaseModel):
    title: Optional[str] = None
    company: Optional[str] = None
    location: Optional[str] = None
    employment_type: Optional[str] = None
    description: Optional[str] = None
    required_skills: Optional[list[str]] = None
    status: Optional[str] = None

class ApplicationCreate(BaseModel):
    cover_letter: str = ""

class ApplicationStatusUpdate(BaseModel):
    status: str
    recruiter_notes: Optional[str] = None

class InterviewCreate(BaseModel):
    application_id: int
    scheduled_at: datetime
    duration_minutes: int = Field(default=30, ge=15, le=240)
    interview_type: str = "Online"
    meeting_link: Optional[str] = None
    notes: str = ""

class InterviewUpdate(BaseModel):
    scheduled_at: Optional[datetime] = None
    duration_minutes: Optional[int] = Field(default=None, ge=15, le=240)
    interview_type: Optional[str] = None
    meeting_link: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None

class NotesUpdate(BaseModel):
    recruiter_notes: str = Field(max_length=10000)

class ReadModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class UserOut(ReadModel):
    id: int
    name: str
    email: EmailStr
    role: str
    is_active: bool

class JobOut(ReadModel):
    id: int
    title: str
    company: str
    location: str
    employment_type: str
    description: str
    required_skills: str
    status: str
    created_by: int
    created_at: datetime

class ApplicationOut(ReadModel):
    id: int
    job_id: int
    candidate_id: int
    resume_filename: Optional[str]
    matching_score: Optional[int]
    matching_skills: str
    missing_keywords: str
    status: str
    cover_letter: str
    recruiter_notes: str
    created_at: datetime
    updated_at: datetime

class InterviewOut(ReadModel):
    id: int
    application_id: int
    recruiter_id: int
    scheduled_at: datetime
    duration_minutes: int
    interview_type: str
    meeting_link: Optional[str]
    status: str
    notes: str


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 210000).hex()
    return f"pbkdf2_sha256$210000${salt}${digest}"

def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, rounds, salt, digest = stored.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
        return hmac.compare_digest(check, digest)
    except Exception:
        return False

def create_token(user: User) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(user.id), "role": user.role, "iat": now, "exp": now + timedelta(minutes=TOKEN_MINUTES)}, SECRET_KEY, algorithm=JWT_ALGORITHM)

def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(security), db: Session = Depends(get_db)) -> User:
    if not credentials:
        raise HTTPException(status_code=401, detail="Bearer token required")
    try:
        payload = jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[JWT_ALGORITHM])
        uid = int(payload["sub"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    user = db.get(User, uid)
    if not user or not user.is_active:
        raise HTTPException(status_code=401, detail="User not found or inactive")
    return user

def require_roles(*roles: str):
    def dependency(user: User = Depends(current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return user
    return dependency

def log_activity(db: Session, actor_id: int | None, action: str, details: str = "", application_id: int | None = None):
    db.add(ActivityLog(actor_id=actor_id, action=action, details=details, application_id=application_id))

def notify(db: Session, user_id: int, title: str, message: str):
    db.add(Notification(user_id=user_id, title=title, message=message))

def skills_for(job: Job) -> list[str]:
    raw = [x.strip() for x in job.required_skills.split(",") if x.strip()]
    return list(dict.fromkeys(raw))

def score_resume(resume_text: str, job: Job) -> dict:
    """Simple, explainable case-insensitive keyword overlap: matched required keywords / total keywords * 100."""
    keywords = skills_for(job)
    if not keywords:
        words = re.findall(r"[A-Za-z][A-Za-z+#.]{2,}", job.description.lower())
        stop = {"the", "and", "for", "with", "you", "will", "are", "our", "this", "that", "from", "have", "has", "job", "work", "your", "who", "can", "all", "not", "but", "their", "they", "about", "into", "team"}
        keywords = list(dict.fromkeys(w for w in words if w not in stop))[:20]
    text = resume_text.lower()
    matched = [keyword for keyword in keywords if keyword.lower() in text]
    missing = [keyword for keyword in keywords if keyword.lower() not in text]
    score = round((len(matched) / len(keywords)) * 100) if keywords else 0
    return {"score": score, "matched": matched, "missing": missing, "logic": "case-insensitive exact keyword/phrase overlap; score = matched keywords / total extracted keywords × 100"}

def can_view_application(user: User, application: Application, db: Session) -> bool:
    if user.role == "Admin" or user.id == application.candidate_id:
        return True
    job = db.get(Job, application.job_id)
    return user.role == "Recruiter" and bool(job and job.created_by == user.id)

@app.get("/")
def root():
    return {"message": APP_NAME, "docs": "/docs", "health": "/health"}

@app.get("/health")
def health(db: Session = Depends(get_db)):
    db.execute(func.count(User.id).select() if False else __import__("sqlalchemy").text("SELECT 1"))
    return {"status": "ok", "database": "connected", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.post("/auth/register", response_model=UserOut, status_code=201)
def register(data: UserCreate, db: Session = Depends(get_db)):
    role = data.role.strip().title()
    if role not in {"Candidate", "Recruiter"}:
        raise HTTPException(status_code=400, detail="Self-registration is allowed for Candidate or Recruiter only")
    email = data.email.lower()
    if db.query(User).filter(func.lower(User.email) == email).first():
        raise HTTPException(status_code=409, detail="Email is already registered")
    user = User(name=data.name.strip(), email=email, password_hash=hash_password(data.password), role=role)
    db.add(user); db.commit(); db.refresh(user)
    return user

@app.post("/auth/login")
def login(data: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(func.lower(User.email) == data.email.lower()).first()
    if not user or not verify_password(data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    return {"access_token": create_token(user), "token_type": "bearer", "expires_in": TOKEN_MINUTES * 60, "user": UserOut.model_validate(user).model_dump()}

@app.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user

@app.get("/users", response_model=list[UserOut])
def list_users(role: Optional[str] = None, user: User = Depends(require_roles("Admin")), db: Session = Depends(get_db)):
    q = db.query(User)
    if role: q = q.filter(User.role == role.title())
    return q.order_by(User.created_at.desc()).all()

@app.post("/jobs", response_model=JobOut, status_code=201)
def create_job(data: JobCreate, user: User = Depends(require_roles("Admin", "Recruiter")), db: Session = Depends(get_db)):
    if data.status not in {"draft", "published", "closed"}: raise HTTPException(status_code=400, detail="status must be draft, published or closed")
    job = Job(title=data.title, company=data.company, location=data.location, employment_type=data.employment_type, description=data.description, required_skills=", ".join(data.required_skills), status=data.status, created_by=user.id)
    db.add(job); db.commit(); db.refresh(job)
    log_activity(db, user.id, "job.created", f"Created job #{job.id}: {job.title}"); db.commit()
    return job

@app.get("/jobs", response_model=list[JobOut])
def list_jobs(search: Optional[str] = None, location: Optional[str] = None, employment_type: Optional[str] = None, status_filter: str = "published", sort_by: str = "newest", page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100), db: Session = Depends(get_db)):
    q = db.query(Job)
    if status_filter != "all": q = q.filter(Job.status == status_filter)
    if search: q = q.filter(or_(Job.title.ilike(f"%{search}%"), Job.company.ilike(f"%{search}%"), Job.description.ilike(f"%{search}%"), Job.required_skills.ilike(f"%{search}%")))
    if location: q = q.filter(Job.location.ilike(f"%{location}%"))
    if employment_type: q = q.filter(Job.employment_type.ilike(f"%{employment_type}%"))
    q = q.order_by(Job.title.asc() if sort_by == "title" else Job.created_at.asc() if sort_by == "oldest" else Job.created_at.desc())
    return q.offset((page - 1) * page_size).limit(page_size).all()

@app.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job: raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.patch("/jobs/{job_id}", response_model=JobOut)
def update_job(job_id: int, data: JobUpdate, user: User = Depends(require_roles("Admin", "Recruiter")), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job: raise HTTPException(status_code=404, detail="Job not found")
    if user.role != "Admin" and job.created_by != user.id: raise HTTPException(status_code=403, detail="You can only edit your own jobs")
    payload = data.model_dump(exclude_unset=True)
    if "status" in payload and payload["status"] not in {"draft", "published", "closed"}: raise HTTPException(status_code=400, detail="Invalid job status")
    if "required_skills" in payload: payload["required_skills"] = ", ".join(payload["required_skills"] or [])
    for key, value in payload.items(): setattr(job, key, value)
    db.commit(); db.refresh(job)
    log_activity(db, user.id, "job.updated", f"Updated job #{job.id}"); db.commit()
    return job

@app.delete("/jobs/{job_id}")
def delete_job(job_id: int, user: User = Depends(require_roles("Admin", "Recruiter")), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job: raise HTTPException(status_code=404, detail="Job not found")
    if user.role != "Admin" and job.created_by != user.id: raise HTTPException(status_code=403, detail="You can only delete your own jobs")
    if db.query(Application).filter(Application.job_id == job_id).first():
        job.status = "closed"; db.commit(); return {"message": "Job has applications, so it was closed instead of deleted"}
    db.delete(job); db.commit(); return {"message": "Job deleted"}

@app.post("/jobs/{job_id}/apply", response_model=ApplicationOut, status_code=201)
def apply_to_job(job_id: int, data: ApplicationCreate, user: User = Depends(require_roles("Candidate")), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job or job.status != "published": raise HTTPException(status_code=404, detail="Published job not found")
    existing = db.query(Application).filter_by(job_id=job_id, candidate_id=user.id).first()
    if existing: raise HTTPException(status_code=409, detail="You have already applied to this job")
    application = Application(job_id=job_id, candidate_id=user.id, cover_letter=data.cover_letter)
    db.add(application); db.flush()
    log_activity(db, user.id, "application.created", f"Applied to job #{job.id}", application.id)
    notify(db, job.created_by, "New application received", f"{user.name} applied for {job.title} (application #{application.id}).")
    db.commit(); db.refresh(application)
    return application

async def extract_resume(file: UploadFile) -> tuple[str, bytes]:
    filename = file.filename or "resume"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".pdf", ".docx"}: raise HTTPException(status_code=400, detail="Resume must be PDF or DOCX")
    content = await file.read(MAX_RESUME_BYTES + 1)
    if not content: raise HTTPException(status_code=400, detail="Uploaded file is empty")
    if len(content) > MAX_RESUME_BYTES: raise HTTPException(status_code=413, detail=f"Resume exceeds {MAX_RESUME_BYTES // (1024*1024)} MB limit")
    try:
        if suffix == ".pdf":
            temp = UPLOAD_DIR / f"parse-{uuid4().hex}.pdf"; temp.write_bytes(content)
            try: text = "\n".join(page.extract_text() or "" for page in PdfReader(str(temp)).pages)
            finally: temp.unlink(missing_ok=True)
        else:
            import io
            doc = DocxDocument(io.BytesIO(content)); text = "\n".join(p.text for p in doc.paragraphs)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not read resume. Ensure it is a valid PDF or DOCX document.")
    return text.strip(), content

@app.post("/applications/{application_id}/resume", response_model=ApplicationOut)
async def upload_resume(application_id: int, file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    application = db.get(Application, application_id)
    if not application: raise HTTPException(status_code=404, detail="Application not found")
    if user.role != "Admin" and application.candidate_id != user.id: raise HTTPException(status_code=403, detail="Only the candidate who owns this application can upload a resume")
    resume_text, content = await extract_resume(file)
    filename = Path(file.filename or "resume").name
    stored_name = f"application-{application.id}-{uuid4().hex}{Path(filename).suffix.lower()}"
    stored_path = UPLOAD_DIR / stored_name; stored_path.write_bytes(content)
    application.resume_path = str(stored_path); application.resume_filename = filename; application.resume_text = resume_text
    job = db.get(Job, application.job_id); result = score_resume(resume_text, job)
    application.matching_score = result["score"]; application.matching_skills = ", ".join(result["matched"]); application.missing_keywords = ", ".join(result["missing"])
    log_activity(db, user.id, "resume.uploaded", f"Resume uploaded and scored: {result['score']}%", application.id)
    if job: notify(db, job.created_by, "Resume uploaded", f"Resume for application #{application.id} is ready. Match score: {result['score']}%.")
    db.commit(); db.refresh(application)
    return application

@app.get("/applications", response_model=list[ApplicationOut])
def list_applications(status_filter: Optional[str] = None, job_id: Optional[int] = None, candidate_id: Optional[int] = None, search: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100), user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(Application)
    if user.role == "Candidate": q = q.filter(Application.candidate_id == user.id)
    elif user.role == "Recruiter":
        owned_jobs = db.query(Job.id).filter(Job.created_by == user.id)
        q = q.filter(Application.job_id.in_(owned_jobs))
    if status_filter: q = q.filter(Application.status == status_filter)
    if job_id: q = q.filter(Application.job_id == job_id)
    if candidate_id and user.role in {"Admin", "Recruiter"}: q = q.filter(Application.candidate_id == candidate_id)
    if search:
        q = q.join(Job, Application.job_id == Job.id).filter(or_(Job.title.ilike(f"%{search}%"), Application.resume_text.ilike(f"%{search}%"), Application.cover_letter.ilike(f"%{search}%")))
    return q.order_by(Application.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

@app.get("/applications/{application_id}", response_model=ApplicationOut)
def get_application(application_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    application = db.get(Application, application_id)
    if not application: raise HTTPException(status_code=404, detail="Application not found")
    if not can_view_application(user, application, db): raise HTTPException(status_code=403, detail="Not allowed to view this application")
    return application

@app.patch("/applications/{application_id}/status", response_model=ApplicationOut)
def update_application_status(application_id: int, data: ApplicationStatusUpdate, user: User = Depends(require_roles("Admin", "Recruiter")), db: Session = Depends(get_db)):
    allowed = {"Applied", "Shortlisted", "Interview", "Selected", "Rejected"}
    if data.status not in allowed: raise HTTPException(status_code=400, detail=f"status must be one of {', '.join(sorted(allowed))}")
    application = db.get(Application, application_id)
    if not application: raise HTTPException(status_code=404, detail="Application not found")
    job = db.get(Job, application.job_id)
    if user.role != "Admin" and (not job or job.created_by != user.id): raise HTTPException(status_code=403, detail="You can only manage applications for your own jobs")
    previous = application.status; application.status = data.status
    if data.recruiter_notes is not None: application.recruiter_notes = data.recruiter_notes
    log_activity(db, user.id, "application.status_changed", f"{previous} -> {data.status}", application.id)
    notify(db, application.candidate_id, "Application status updated", f"Your application for {job.title if job else 'a job'} changed from {previous} to {data.status}.")
    db.commit(); db.refresh(application)
    return application

@app.patch("/applications/{application_id}/notes", response_model=ApplicationOut)
def update_notes(application_id: int, data: NotesUpdate, user: User = Depends(require_roles("Admin", "Recruiter")), db: Session = Depends(get_db)):
    application = db.get(Application, application_id)
    if not application: raise HTTPException(status_code=404, detail="Application not found")
    job = db.get(Job, application.job_id)
    if user.role != "Admin" and (not job or job.created_by != user.id): raise HTTPException(status_code=403, detail="You can only edit notes for your own jobs")
    application.recruiter_notes = data.recruiter_notes
    log_activity(db, user.id, "application.notes_updated", "Recruiter notes updated", application.id)
    db.commit(); db.refresh(application); return application

@app.get("/applications/{application_id}/match")
def get_match(application_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    application = db.get(Application, application_id)
    if not application: raise HTTPException(status_code=404, detail="Application not found")
    if not can_view_application(user, application, db): raise HTTPException(status_code=403, detail="Not allowed")
    return {"application_id": application.id, "score": application.matching_score, "matched_keywords": [x for x in application.matching_skills.split(", ") if x], "missing_keywords": [x for x in application.missing_keywords.split(", ") if x], "scoring_logic": "case-insensitive exact keyword/phrase overlap; matched keywords divided by total required/extracted keywords multiplied by 100; this is a basic heuristic, not an AI or hiring recommendation"}

@app.get("/applications/{application_id}/history")
def application_history(application_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    application = db.get(Application, application_id)
    if not application: raise HTTPException(status_code=404, detail="Application not found")
    if not can_view_application(user, application, db): raise HTTPException(status_code=403, detail="Not allowed")
    return db.query(ActivityLog).filter(ActivityLog.application_id == application_id).order_by(ActivityLog.created_at.asc()).all()

@app.post("/interviews", response_model=InterviewOut, status_code=201)
def create_interview(data: InterviewCreate, user: User = Depends(require_roles("Admin", "Recruiter")), db: Session = Depends(get_db)):
    application = db.get(Application, data.application_id)
    if not application: raise HTTPException(status_code=404, detail="Application not found")
    job = db.get(Job, application.job_id)
    if user.role != "Admin" and (not job or job.created_by != user.id): raise HTTPException(status_code=403, detail="You can only schedule interviews for your own jobs")
    start = data.scheduled_at
    if start.tzinfo is None: start = start.replace(tzinfo=timezone.utc)
    end = start + timedelta(minutes=data.duration_minutes)
    existing = db.query(Interview).filter(Interview.recruiter_id == user.id, Interview.status == "Scheduled").all()
    for item in existing:
        item_start = item.scheduled_at if item.scheduled_at.tzinfo else item.scheduled_at.replace(tzinfo=timezone.utc)
        item_end = item_start + timedelta(minutes=item.duration_minutes)
        if start < item_end and end > item_start: raise HTTPException(status_code=409, detail="This recruiter already has an interview in the requested time slot")
    interview = Interview(application_id=application.id, recruiter_id=user.id, scheduled_at=start, duration_minutes=data.duration_minutes, interview_type=data.interview_type, meeting_link=data.meeting_link, notes=data.notes)
    db.add(interview); application.status = "Interview"
    log_activity(db, user.id, "interview.scheduled", f"Interview scheduled for {start.isoformat()}", application.id)
    notify(db, application.candidate_id, "Interview scheduled", f"An interview has been scheduled for {start.isoformat()}.")
    db.commit(); db.refresh(interview); return interview

@app.get("/interviews", response_model=list[InterviewOut])
def list_interviews(upcoming_only: bool = False, application_id: Optional[int] = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(Interview)
    if user.role == "Candidate":
        app_ids = db.query(Application.id).filter(Application.candidate_id == user.id)
        q = q.filter(Interview.application_id.in_(app_ids))
    elif user.role == "Recruiter": q = q.filter(Interview.recruiter_id == user.id)
    if application_id: q = q.filter(Interview.application_id == application_id)
    if upcoming_only: q = q.filter(Interview.scheduled_at >= datetime.now(timezone.utc), Interview.status == "Scheduled")
    return q.order_by(Interview.scheduled_at.asc()).all()

@app.patch("/interviews/{interview_id}", response_model=InterviewOut)
def update_interview(interview_id: int, data: InterviewUpdate, user: User = Depends(require_roles("Admin", "Recruiter")), db: Session = Depends(get_db)):
    interview = db.get(Interview, interview_id)
    if not interview: raise HTTPException(status_code=404, detail="Interview not found")
    if user.role != "Admin" and interview.recruiter_id != user.id: raise HTTPException(status_code=403, detail="You can only update interviews you scheduled")
    payload = data.model_dump(exclude_unset=True)
    if "status" in payload and payload["status"] not in {"Scheduled", "Completed", "Cancelled"}: raise HTTPException(status_code=400, detail="Invalid interview status")
    new_start = payload.get("scheduled_at", interview.scheduled_at)
    if new_start and new_start.tzinfo is None: new_start = new_start.replace(tzinfo=timezone.utc)
    duration = payload.get("duration_minutes", interview.duration_minutes)
    if new_start and payload.get("status", interview.status) == "Scheduled":
        for other in db.query(Interview).filter(Interview.recruiter_id == interview.recruiter_id, Interview.status == "Scheduled", Interview.id != interview.id).all():
            other_start = other.scheduled_at if other.scheduled_at.tzinfo else other.scheduled_at.replace(tzinfo=timezone.utc)
            if new_start < other_start + timedelta(minutes=other.duration_minutes) and new_start + timedelta(minutes=duration) > other_start:
                raise HTTPException(status_code=409, detail="This recruiter already has an interview in the requested time slot")
    payload["scheduled_at"] = new_start if "scheduled_at" in payload else payload.get("scheduled_at")
    for key, value in payload.items():
        if value is not None: setattr(interview, key, value)
    application = db.get(Application, interview.application_id)
    if application:
        if interview.status == "Cancelled": notify(db, application.candidate_id, "Interview cancelled", f"Interview #{interview.id} was cancelled.")
        else: notify(db, application.candidate_id, "Interview updated", f"Interview #{interview.id} was updated.")
        log_activity(db, user.id, "interview.updated", f"Interview #{interview.id} status: {interview.status}", application.id)
    db.commit(); db.refresh(interview); return interview

@app.get("/notifications")
def list_notifications(unread_only: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = db.query(Notification).filter(Notification.user_id == user.id)
    if unread_only: q = q.filter(Notification.is_read.is_(False))
    return q.order_by(Notification.created_at.desc()).limit(100).all()

@app.patch("/notifications/{notification_id}/read")
def mark_notification_read(notification_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    notification = db.get(Notification, notification_id)
    if not notification or notification.user_id != user.id: raise HTTPException(status_code=404, detail="Notification not found")
    notification.is_read = True; db.commit(); return {"message": "Notification marked as read"}

@app.get("/analytics/overview")
def analytics(user: User = Depends(require_roles("Admin", "Recruiter")), db: Session = Depends(get_db)):
    if user.role == "Admin":
        jobs_q = db.query(Job); apps_q = db.query(Application); interviews_q = db.query(Interview)
    else:
        owned_ids = db.query(Job.id).filter(Job.created_by == user.id)
        jobs_q = db.query(Job).filter(Job.created_by == user.id)
        apps_q = db.query(Application).filter(Application.job_id.in_(owned_ids))
        interviews_q = db.query(Interview).filter(Interview.recruiter_id == user.id)
    status_counts = {row[0]: row[1] for row in apps_q.with_entities(Application.status, func.count(Application.id)).group_by(Application.status).all()}
    return {"total_jobs": jobs_q.count(), "published_jobs": jobs_q.filter(Job.status == "published").count(), "total_applications": apps_q.count(), "application_status_counts": status_counts, "scheduled_interviews": interviews_q.filter(Interview.status == "Scheduled").count(), "selected_candidates": status_counts.get("Selected", 0), "rejected_candidates": status_counts.get("Rejected", 0)}

@app.get("/activity-logs")
def list_activity_logs(application_id: Optional[int] = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=100), user: User = Depends(require_roles("Admin", "Recruiter")), db: Session = Depends(get_db)):
    q = db.query(ActivityLog)
    if user.role == "Recruiter":
        owned_ids = db.query(Application.id).join(Job, Application.job_id == Job.id).filter(Job.created_by == user.id)
        q = q.filter(or_(ActivityLog.application_id.in_(owned_ids), ActivityLog.actor_id == user.id))
    if application_id: q = q.filter(ActivityLog.application_id == application_id)
    return q.order_by(ActivityLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

@app.websocket("/ws/notifications")
async def notification_socket(websocket: WebSocket, token: str = Query(...)):
    await websocket.accept()
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[JWT_ALGORITHM])
        user_id = int(payload["sub"])
    except Exception:
        await websocket.send_json({"error": "Invalid or expired token"}); await websocket.close(code=1008); return
    await websocket.send_json({"type": "connected", "user_id": user_id, "note": "WebSocket connection established. Poll GET /notifications for persisted notifications; live push requires a shared connection manager in multi-worker production."})
    try:
        while True:
            await websocket.receive_text()
            await websocket.send_json({"type": "ping", "timestamp": datetime.now(timezone.utc).isoformat()})
    except WebSocketDisconnect:
        return

# Optional admin bootstrap: set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD as Render environment variables.
@app.on_event("startup")
def bootstrap_admin():
    email = os.getenv("BOOTSTRAP_ADMIN_EMAIL", "").strip().lower()
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD", "")
    if not email or not password or len(password) < 12:
        return
    db = SessionLocal()
    try:
        if not db.query(User).filter(func.lower(User.email) == email).first():
            db.add(User(name=os.getenv("BOOTSTRAP_ADMIN_NAME", "Platform Admin"), email=email, password_hash=hash_password(password), role="Admin"))
            db.commit()
    finally:
        db.close()
