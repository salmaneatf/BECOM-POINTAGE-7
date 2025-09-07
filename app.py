import os
from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from flask import Flask, render_template, request, redirect, url_for, flash, send_file
from flask_login import LoginManager, login_user, login_required, logout_user, current_user, UserMixin
from flask_bcrypt import Bcrypt
from sqlalchemy import create_engine, Column, Integer, String, Date, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import sessionmaker, declarative_base, relationship
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm

# ---------------------- Config ----------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
DB_PATH = os.environ.get("DATABASE_URL", "sqlite:///becom.db")

bcrypt = Bcrypt(app)

engine = create_engine(DB_PATH, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base = declarative_base()

login_manager = LoginManager(app)
login_manager.login_view = "login"

# ---------------------- Models ----------------------
class User(Base, UserMixin):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    username = Column(String, unique=True, nullable=False)  # convention: nom.prenom en minuscules sans espaces
    password_hash = Column(String, nullable=False)
    role = Column(String, default="employee")  # 'employee' | 'admin'
    pointages = relationship("Pointage", back_populates="user")

    def get_id(self):
        return str(self.id)

class Pointage(Base):
    __tablename__ = "pointages"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    day = Column(Date, nullable=False)
    shift = Column(String, nullable=False)  # 'jour' | 'nuit' | 'deplacement'
    status = Column(String, default="en_attente")  # 'en_attente' | 'valide' | 'refuse'
    created_at = Column(DateTime, default=datetime.utcnow)
    validated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    validated_at = Column(DateTime, nullable=True)

    user = relationship("User", foreign_keys=[user_id], back_populates="pointages")
    validator = relationship("User", foreign_keys=[validated_by])

    __table_args__ = (
        UniqueConstraint("user_id", "day", name="uniq_user_day"),
    )

Base.metadata.create_all(engine)

# ---------------------- Auth helpers ----------------------
@login_manager.user_loader
def load_user(user_id):
    with SessionLocal() as db:
        return db.get(User, int(user_id))

def normalize_username(first_name, last_name):
    base = f"{last_name.strip()}.{first_name.strip()}".lower()
    return ".".join([seg.replace(" ", "").replace("-", "") for seg in base.split(".")])

def create_user_if_missing(first_name, last_name, password, role="employee"):
    with SessionLocal() as db:
        from flask_bcrypt import generate_password_hash as _gph  # not used; keep for typing
        username = normalize_username(first_name, last_name)
        existing = db.query(User).filter_by(username=username).first()
        if existing:
            return existing
        pw_hash = bcrypt.generate_password_hash(password).decode("utf-8")
        user = User(first_name=first_name, last_name=last_name, username=username, password_hash=pw_hash, role=role)
        db.add(user)
        db.commit()
        return user

# Seed an admin if none exists
with SessionLocal() as db:
    if not db.query(User).filter_by(role="admin").first():
        # default admin: admin / becom2025!
        admin = create_user_if_missing("admin", "admin", "becom2025!", role="admin")
        print("Admin créé:", admin.username, "(mdp: becom2025!)")

# ---------------------- Routes ----------------------
@app.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifiant = request.form.get("identifiant", "").strip().lower()
        password = request.form.get("password", "")
        with SessionLocal() as db:
            user = db.query(User).filter_by(username=identifiant).first()
            if user and bcrypt.check_password_hash(user.password_hash, password):
                login_user(user)
                return redirect(url_for("dashboard"))
        flash("Identifiants invalides.", "danger")
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    # month navigation
    try:
        month = int(request.args.get("month", datetime.utcnow().month))
        year = int(request.args.get("year", datetime.utcnow().year))
    except Exception:
        month = datetime.utcnow().month
        year = datetime.utcnow().year

    # compute first/last day grid
    first_day = date(year, month, 1)
    from calendar import monthrange
    last_day = date(year, month, monthrange(year, month)[1])

    with SessionLocal() as db:
        # fetch all pointages for user in month
        pts = db.query(Pointage).filter(Pointage.user_id==current_user.id,
                                        Pointage.day>=first_day, Pointage.day<=last_day).all()
    existing = {p.day: p for p in pts}

    # build days list
    days = []
    d = first_day
    while d <= last_day:
        days.append(d)
        d = d.fromordinal(d.toordinal()+1)

    return render_template("dashboard.html", days=days, month=month, year=year, existing=existing)

@app.post("/pointe")
@login_required
def pointe():
    # from AJAX
    day_str = request.form.get("day")
    shift = request.form.get("shift")
    if shift not in ("jour", "nuit", "deplacement"):
        return {"ok": False, "error": "Choix invalide"}, 400
    try:
        chosen = datetime.strptime(day_str, "%Y-%m-%d").date()
    except Exception:
        return {"ok": False, "error": "Date invalide"}, 400

    with SessionLocal() as db:
        # ensure not duplicate
        p = db.query(Pointage).filter_by(user_id=current_user.id, day=chosen).first()
        if p:
            # allow update only if not validated
            if p.status == "valide":
                return {"ok": False, "error": "Jour déjà validé, modification impossible."}, 400
            p.shift = shift
            p.status = "en_attente"
        else:
            p = Pointage(user_id=current_user.id, day=chosen, shift=shift, status="en_attente")
            db.add(p)
        db.commit()
    return {"ok": True}

# ---------------------- Admin ----------------------
def is_admin():
    return current_user.is_authenticated and current_user.role == "admin"

@app.get("/admin")
@login_required
def admin_panel():
    if not is_admin():
        flash("Accès réservé à l'admin.", "danger")
        return redirect(url_for("dashboard"))

    # filters
    from calendar import monthrange
    year = int(request.args.get("year", datetime.utcnow().year))
    month = int(request.args.get("month", datetime.utcnow().month))
    first_day = date(year, month, 1)
    last_day = date(year, month, monthrange(year, month)[1])
    with SessionLocal() as db:
        users = db.query(User).order_by(User.last_name, User.first_name).all()
        pointages = (
            db.query(Pointage)
            .filter(Pointage.day>=first_day, Pointage.day<=last_day)
            .order_by(Pointage.day.asc())
            .all()
        )
    return render_template("admin.html", users=users, pointages=pointages, year=year, month=month)

@app.post("/admin/valide")
@login_required
def admin_valide():
    if not is_admin():
        return {"ok": False, "error": "Non autorisé"}, 403
    pid = int(request.form.get("pid"))
    action = request.form.get("action")
    if action not in ("valide", "refuse"):
        return {"ok": False, "error": "Action invalide"}, 400
    with SessionLocal() as db:
        p = db.get(Pointage, pid)
        if not p:
            return {"ok": False, "error": "Pointage introuvable"}, 404
        p.status = action
        p.validated_by = current_user.id
        p.validated_at = datetime.utcnow()
        db.commit()
    return {"ok": True}

# ---------------------- PDF Export ----------------------
def generate_monthly_pdf_for_user(db, user, year, month, out_dir):
    from calendar import monthrange
    first_day = date(year, month, 1)
    last_day = date(year, month, monthrange(year, month)[1])
    pts = (
        db.query(Pointage)
        .filter(Pointage.user_id==user.id, Pointage.day>=first_day, Pointage.day<=last_day)
        .order_by(Pointage.day.asc())
        .all()
    )

    # PDF path
    safe_name = f"{user.last_name.upper()}_{user.first_name.capitalize()}"
    filename = os.path.join(out_dir, f"BECoM-{safe_name}-{year}-{month:02d}.pdf")

    c = canvas.Canvas(filename, pagesize=A4)
    width, height = A4
    y = height - 2*cm

    c.setFont("Helvetica-Bold", 16)
    c.drawString(2*cm, y, f"Récapitulatif de pointage - {month:02d}/{year}")
    y -= 1*cm

    c.setFont("Helvetica", 12)
    c.drawString(2*cm, y, f"Employé : {user.first_name.capitalize()} {user.last_name.upper()} (identifiant: {user.username})")
    y -= 0.7*cm

    c.drawString(2*cm, y, f"Généré le : {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    y -= 1*cm

    # Table header
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2*cm, y, "Date")
    c.drawString(7*cm, y, "Choix")
    c.drawString(11*cm, y, "Statut")
    y -= 0.5*cm
    c.line(2*cm, y, 19*cm, y)
    y -= 0.3*cm

    c.setFont("Helvetica", 11)
    if not pts:
        c.drawString(2*cm, y, "Aucun pointage pour ce mois.")
    else:
        for p in pts:
            if y < 3*cm:
                c.showPage()
                y = height - 2*cm
            c.drawString(2*cm, y, p.day.strftime("%d/%m/%Y"))
            c.drawString(7*cm, y, p.shift.capitalize() if p.shift!="deplacement" else "Déplacement")
            st = {"en_attente":"En attente","valide":"Validé","refuse":"Refusé"}[p.status]
            c.drawString(11*cm, y, st)
            y -= 0.55*cm

    c.showPage()
    c.save()
    return filename

@app.get("/admin/export_pdfs")
@login_required
def export_pdfs():
    if not is_admin():
        flash("Accès réservé à l'admin.", "danger")
        return redirect(url_for("dashboard"))

    year = int(request.args.get("year", datetime.utcnow().year))
    month = int(request.args.get("month", datetime.utcnow().month))

    out_dir = os.path.join("exports", f"{year}-{month:02d}")
    os.makedirs(out_dir, exist_ok=True)

    files = []
    with SessionLocal() as db:
        for user in db.query(User).filter_by(role="employee").all():
            files.append(generate_monthly_pdf_for_user(db, user, year, month, out_dir))

    # Zip all PDFs for convenience
    zip_name = os.path.join(out_dir, f"recap-{year}-{month:02d}.zip")
    import zipfile
    with zipfile.ZipFile(zip_name, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(f, arcname=os.path.basename(f))

    return send_file(zip_name, as_attachment=True)

# ---------------------- CLI Helpers ----------------------
@app.cli.command("create-user")
def cli_create_user():
    """Créer rapidement un employé. Usage:
    flask --app app.py create-user
    """
    first = input("Prénom: ").strip()
    last = input("Nom: ").strip()
    password = input("Mot de passe: ").strip()
    role = input("Rôle [employee/admin] (défaut employee): ").strip() or "employee"
    u = create_user_if_missing(first, last, password, role=role)
    print("Créé:", u.username, "rôle:", u.role)

# ---------------------- Run ----------------------
if __name__ == "__main__":
    # Debug server for local dev
    app.run(host="0.0.0.0", port=5000, debug=True)
