from flask import Flask, render_template, request, redirect, url_for, session, flash, abort
import os
import re
import psycopg
from psycopg.rows import dict_row
from pathlib import Path
from datetime import datetime, timedelta
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

app = Flask(__name__)
app.secret_key = os.getenv("SAMS_SECRET_KEY", "CHANGE-ME-SAMS-V3-PUBLIC-DEPLOYMENT")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax", SESSION_COOKIE_SECURE=True)

def _sql(sql):
    """Convert the small SQLite-flavoured query subset used by the V3 to PostgreSQL."""
    s = sql
    # SQLite positional placeholders -> psycopg placeholders.
    s = s.replace("?", "%s")

    # SQLite schema syntax -> PostgreSQL.
    s = s.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY")

    # INSERT OR IGNORE -> PostgreSQL ON CONFLICT DO NOTHING.
    m = re.match(r"(?is)^\s*INSERT\s+OR\s+IGNORE\s+INTO\s+(.+)$", s)
    if m:
        s = "INSERT INTO " + m.group(1)
        if "ON CONFLICT" not in s.upper():
            s = s.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"

    # The only INSERT OR REPLACE in this project is certifications.
    m = re.match(r"(?is)^\s*INSERT\s+OR\s+REPLACE\s+INTO\s+certifications\s*\((.*?)\)\s*VALUES\s*\((.*?)\)\s*$", s.strip().rstrip(";"))
    if m:
        cols = [c.strip() for c in m.group(1).split(",")]
        values = m.group(2)
        updates = [c for c in cols if c not in ("user_id", "training_id")]
        s = (
            f"INSERT INTO certifications ({', '.join(cols)}) VALUES ({values}) "
            "ON CONFLICT (user_id, training_id) DO UPDATE SET "
            + ", ".join(f"{c}=EXCLUDED.{c}" for c in updates)
        )
    return s

class PgDB:
    def __init__(self):
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL manquant. Ajoute l'URL Neon dans les variables d'environnement Render.")
        self.conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)

    def execute(self, sql, params=()):
        cur = self.conn.cursor()
        cur.execute(_sql(sql), params)
        return cur

    def executemany(self, sql, seq):
        cur = self.conn.cursor()
        cur.executemany(_sql(sql), seq)
        return cur

    def executescript(self, script):
        cur = self.conn.cursor()
        # The schema script contains no semicolons inside values/strings.
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(_sql(stmt))
        return cur

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

def db():
    return PgDB()

def now():
    return datetime.now().isoformat(timespec="seconds")

def setting(conn, key, default=""):
    row = conn.execute("SELECT value FROM site_settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def module_enabled(conn, key):
    row = conn.execute("SELECT enabled FROM modules WHERE key=?", (key,)).fetchone()
    return bool(row and row["enabled"])

def audit(conn, action, target=""):
    conn.execute(
        "INSERT INTO audit_logs(user_id,action,target,created_at) VALUES(?,?,?,?)",
        (session.get("user_id"), action, target, now())
    )

def notify(conn, user_id, title, message, level="info"):
    conn.execute(
        "INSERT INTO notifications(user_id,title,message,level,created_at,is_read) VALUES(?,?,?,?,?,0)",
        (user_id, title, message, level, now())
    )

def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS site_settings(
      key TEXT PRIMARY KEY,
      value TEXT NOT NULL DEFAULT '',
      label TEXT NOT NULL DEFAULT '',
      group_name TEXT NOT NULL DEFAULT 'general',
      input_type TEXT NOT NULL DEFAULT 'text'
    );

    CREATE TABLE IF NOT EXISTS modules(
      key TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      enabled INTEGER NOT NULL DEFAULT 1,
      sort_order INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS navigation(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      module_key TEXT,
      label TEXT NOT NULL,
      endpoint TEXT NOT NULL,
      icon TEXT NOT NULL DEFAULT '•',
      sort_order INTEGER NOT NULL DEFAULT 0,
      visible INTEGER NOT NULL DEFAULT 1,
      admin_only INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS custom_pages(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      slug TEXT UNIQUE NOT NULL,
      title TEXT NOT NULL,
      subtitle TEXT NOT NULL DEFAULT '',
      body TEXT NOT NULL DEFAULT '',
      visible INTEGER NOT NULL DEFAULT 1,
      sort_order INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS grades(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT UNIQUE NOT NULL,
      level INTEGER NOT NULL DEFAULT 0,
      badge TEXT NOT NULL DEFAULT 'EMS',
      hourly_rate INTEGER NOT NULL DEFAULT 0,
      color TEXT NOT NULL DEFAULT '#df2031'
    );

    CREATE TABLE IF NOT EXISTS divisions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT UNIQUE NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      lead_name TEXT NOT NULL DEFAULT '',
      radio_channel TEXT NOT NULL DEFAULT '',
      color TEXT NOT NULL DEFAULT '#df2031'
    );

    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      username TEXT UNIQUE NOT NULL,
      password_hash TEXT NOT NULL,
      full_name TEXT NOT NULL,
      matricule TEXT UNIQUE NOT NULL,
      grade_id INTEGER,
      division_id INTEGER,
      role TEXT NOT NULL DEFAULT 'employee',
      active INTEGER NOT NULL DEFAULT 1,
      discord_id TEXT UNIQUE,
      discord_username TEXT,
      discord_authorized INTEGER NOT NULL DEFAULT 0,
      callsign TEXT NOT NULL DEFAULT '',
      phone TEXT NOT NULL DEFAULT '',
      notes TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      FOREIGN KEY(grade_id) REFERENCES grades(id),
      FOREIGN KEY(division_id) REFERENCES divisions(id)
    );

    CREATE TABLE IF NOT EXISTS shifts(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      start_time TEXT NOT NULL,
      end_time TEXT,
      pause_start TEXT,
      pause_minutes INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'available',
      current_call_id INTEGER,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS emergency_calls(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      code TEXT UNIQUE NOT NULL,
      priority TEXT NOT NULL DEFAULT 'P2',
      location TEXT NOT NULL,
      reason TEXT NOT NULL,
      caller TEXT NOT NULL DEFAULT '',
      details TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL DEFAULT 'open',
      created_at TEXT NOT NULL,
      closed_at TEXT,
      created_by INTEGER,
      FOREIGN KEY(created_by) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS call_assignments(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      call_id INTEGER NOT NULL,
      user_id INTEGER NOT NULL,
      assigned_at TEXT NOT NULL,
      UNIQUE(call_id,user_id),
      FOREIGN KEY(call_id) REFERENCES emergency_calls(id) ON DELETE CASCADE,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS vehicles(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      unit_code TEXT UNIQUE NOT NULL,
      model TEXT NOT NULL,
      plate TEXT NOT NULL DEFAULT '',
      vehicle_type TEXT NOT NULL DEFAULT 'Ambulance',
      status TEXT NOT NULL DEFAULT 'available',
      assigned_user_id INTEGER,
      mileage INTEGER NOT NULL DEFAULT 0,
      notes TEXT NOT NULL DEFAULT '',
      FOREIGN KEY(assigned_user_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS incident_reports(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      call_id INTEGER,
      author_id INTEGER NOT NULL,
      title TEXT NOT NULL,
      patient_name TEXT NOT NULL DEFAULT '',
      location TEXT NOT NULL DEFAULT '',
      injuries TEXT NOT NULL DEFAULT '',
      treatment TEXT NOT NULL DEFAULT '',
      transport TEXT NOT NULL DEFAULT '',
      summary TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      FOREIGN KEY(call_id) REFERENCES emergency_calls(id),
      FOREIGN KEY(author_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS patients(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      full_name TEXT NOT NULL,
      birth_date TEXT NOT NULL DEFAULT '',
      phone TEXT NOT NULL DEFAULT '',
      blood_type TEXT NOT NULL DEFAULT '',
      allergies TEXT NOT NULL DEFAULT '',
      medical_notes TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS medical_records(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      patient_id INTEGER NOT NULL,
      staff_id INTEGER NOT NULL,
      record_type TEXT NOT NULL,
      diagnosis TEXT NOT NULL,
      treatment TEXT NOT NULL,
      notes TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      FOREIGN KEY(patient_id) REFERENCES patients(id) ON DELETE CASCADE,
      FOREIGN KEY(staff_id) REFERENCES users(id)
    );

    CREATE TABLE IF NOT EXISTS trainings(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT UNIQUE NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      required_hours INTEGER NOT NULL DEFAULT 0,
      active INTEGER NOT NULL DEFAULT 1
    );

    CREATE TABLE IF NOT EXISTS certifications(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      training_id INTEGER NOT NULL,
      issued_by TEXT NOT NULL,
      issued_at TEXT NOT NULL,
      expires_at TEXT,
      UNIQUE(user_id,training_id),
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
      FOREIGN KEY(training_id) REFERENCES trainings(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS planning(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      event_type TEXT NOT NULL,
      start_at TEXT NOT NULL,
      end_at TEXT,
      location TEXT NOT NULL DEFAULT '',
      description TEXT NOT NULL DEFAULT '',
      created_by TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS leave_requests(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      start_date TEXT NOT NULL,
      end_date TEXT NOT NULL,
      reason TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending',
      reviewer_note TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS promotions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      proposed_grade_id INTEGER NOT NULL,
      reason TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending',
      proposed_by TEXT NOT NULL,
      created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
      FOREIGN KEY(proposed_grade_id) REFERENCES grades(id)
    );

    CREATE TABLE IF NOT EXISTS rules(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      content TEXT NOT NULL,
      category TEXT NOT NULL DEFAULT 'Général',
      sort_order INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS announcements(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      title TEXT NOT NULL,
      content TEXT NOT NULL,
      priority TEXT NOT NULL DEFAULT 'normal',
      author TEXT NOT NULL,
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS applications(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      rp_name TEXT NOT NULL,
      discord_name TEXT NOT NULL,
      age INTEGER NOT NULL,
      experience TEXT NOT NULL,
      motivation TEXT NOT NULL,
      availability TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending',
      reviewer_note TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS sanctions(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      type TEXT NOT NULL,
      reason TEXT NOT NULL,
      author TEXT NOT NULL,
      created_at TEXT NOT NULL,
      active INTEGER NOT NULL DEFAULT 1,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS notifications(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER NOT NULL,
      title TEXT NOT NULL,
      message TEXT NOT NULL,
      level TEXT NOT NULL DEFAULT 'info',
      created_at TEXT NOT NULL,
      is_read INTEGER NOT NULL DEFAULT 0,
      FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS audit_logs(
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id INTEGER,
      action TEXT NOT NULL,
      target TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      FOREIGN KEY(user_id) REFERENCES users(id)
    );
    """)

    # Migration V3.1: accès par ID Discord autorisé, sans bot ni API Discord.
    user_columns = {
        row["column_name"]
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='users'"
        ).fetchall()
    }
    if "discord_authorized" not in user_columns:
        conn.execute("ALTER TABLE users ADD COLUMN discord_authorized INTEGER NOT NULL DEFAULT 0")

    # Évite de verrouiller la Direction lors d'une migration depuis une ancienne V3.
    bootstrap_admin = conn.execute("SELECT id, discord_id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
    authorized_admins = conn.execute("SELECT COUNT(*) AS n FROM users WHERE role='admin' AND discord_authorized=1").fetchone()["n"]
    if bootstrap_admin and authorized_admins == 0:
        bootstrap_id = bootstrap_admin["discord_id"] or "111111111111111111"
        conn.execute("UPDATE users SET discord_id=?, discord_authorized=1 WHERE id=?", (bootstrap_id, bootstrap_admin["id"]))

    defaults = [
      ("site_name","SAMS","Nom du site","identity","text"),
      ("site_full_name","San Andreas Medical Services","Nom complet","identity","text"),
      ("site_subtitle","Emergency Medical Services","Sous-titre","identity","text"),
      ("login_title","Portail du personnel","Titre connexion","identity","text"),
      ("login_description","Accès sécurisé aux services internes du SAMS.","Description connexion","identity","textarea"),
      ("primary_color","#df2031","Couleur principale","theme","color"),
      ("accent_color","#27ca78","Couleur service","theme","color"),
      ("background_color","#07090c","Fond","theme","color"),
      ("surface_color","#101318","Cartes","theme","color"),
      ("text_color","#f4f6f9","Texte","theme","color"),
      ("logo_symbol","✚","Symbole du logo","identity","text"),
      ("dashboard_welcome","Centre de commandement SAMS","Titre dashboard","content","text"),
      ("application_intro","Rejoins le SAMS et participe aux secours de San Andreas.","Intro candidatures","content","textarea"),
      ("footer_text","Portail interne SAMS / EMS","Pied de page","content","text"),
    ]
    for row in defaults:
        conn.execute("INSERT OR IGNORE INTO site_settings(key,value,label,group_name,input_type) VALUES(?,?,?,?,?)", row)

    modules = [
      ("operations","Centre opérationnel","Appels 911, unités et statuts",1,10),
      ("fleet","Flotte","Véhicules et affectations",1,20),
      ("medical","Dossiers médicaux","Patients et comptes rendus",1,30),
      ("reports","Rapports","Rapports d'intervention",1,40),
      ("training","Formations","Formations et certifications",1,50),
      ("planning","Planning","Réunions, gardes et formations",1,60),
      ("leave","Congés","Demandes d'absence",1,70),
      ("rules","Règlement","Règles internes",1,80),
      ("announcements","Annonces","Communication Direction",1,90),
      ("sanctions","Sanctions","Discipline interne",1,100),
    ]
    for m in modules:
        conn.execute("INSERT OR IGNORE INTO modules(key,name,description,enabled,sort_order) VALUES(?,?,?,?,?)", m)

    if conn.execute("SELECT COUNT(*) n FROM navigation").fetchone()["n"] == 0:
        nav = [
          (None,"Dashboard","dashboard","⌂",1,1,0),
          ("operations","Opérations","operations","🚨",2,1,0),
          (None,"Personnel","personnel","👥",3,1,0),
          ("fleet","Flotte","fleet","🚑",4,1,0),
          ("medical","Médical","medical","✚",5,1,0),
          ("reports","Rapports","reports","📝",6,1,0),
          ("training","Formations","training","🎓",7,1,0),
          ("planning","Planning","planning","▣",8,1,0),
          ("leave","Congés","leave","☕",9,1,0),
          ("rules","Règlement","rules","☷",10,1,0),
          ("announcements","Annonces","announcements","◉",11,1,0),
          ("sanctions","Sanctions","sanctions","⚠",12,1,0),
          (None,"Administration","admin","⚙",100,1,1),
          (None,"Studio du site","site_studio","✎",101,1,1),
        ]
        conn.executemany("INSERT INTO navigation(module_key,label,endpoint,icon,sort_order,visible,admin_only) VALUES(?,?,?,?,?,?,?)", nav)

    if conn.execute("SELECT COUNT(*) n FROM grades").fetchone()["n"] == 0:
        grades = [
          ("Recrue EMS",10,"REC",0,"#9ca3af"),("EMT",20,"EMT",0,"#60a5fa"),
          ("Paramedic",30,"PMD",0,"#22c55e"),("Senior Paramedic",40,"SPM",0,"#10b981"),
          ("Instructeur",50,"INS",0,"#eab308"),("Médecin",60,"MED",0,"#8b5cf6"),
          ("Chirurgien",70,"CHR",0,"#a855f7"),("Superviseur",80,"SUP",0,"#f97316"),
          ("Directeur adjoint",90,"D.A",0,"#ef4444"),("Directeur",100,"DIR",0,"#dc2626"),
        ]
        conn.executemany("INSERT INTO grades(name,level,badge,hourly_rate,color) VALUES(?,?,?,?,?)", grades)

    if conn.execute("SELECT COUNT(*) n FROM divisions").fetchone()["n"] == 0:
        divs = [
          ("Direction","Direction générale et supervision.","Direction SAMS","CMD","#ef4444"),
          ("EMS / Intervention","Secours préhospitaliers et interventions.","Alex Morgan","EMS-1","#22c55e"),
          ("Médecine","Consultations et suivi médical.","Taylor Brooks","MED-1","#8b5cf6"),
          ("Chirurgie","Interventions chirurgicales.","Casey Miller","MED-2","#a855f7"),
          ("Formation","Formation et certifications.","Sam Carter","TRAIN","#eab308"),
          ("Recrutement","Candidatures et intégration.","Direction SAMS","ADMIN","#60a5fa"),
        ]
        conn.executemany("INSERT INTO divisions(name,description,lead_name,radio_channel,color) VALUES(?,?,?,?,?)", divs)

    grade_map = {r["name"]: r["id"] for r in conn.execute("SELECT * FROM grades")}
    div_map = {r["name"]: r["id"] for r in conn.execute("SELECT * FROM divisions")}

    if conn.execute("SELECT COUNT(*) n FROM users").fetchone()["n"] == 0:
        demo = [
          ("direction","Direction SAMS","SAMS-001","Directeur adjoint","Direction","admin","CMD-01","111111111111111111",1),
          ("medic01","Alex Morgan","SAMS-014","Senior Paramedic","EMS / Intervention","employee","MEDIC-01","222222222222222222",0),
          ("medic02","Jordan Hayes","SAMS-021","EMT","EMS / Intervention","employee","MEDIC-02","333333333333333333",0),
          ("medic03","Taylor Brooks","SAMS-033","Médecin","Médecine","employee","DOC-01","444444444444444444",0),
          ("medic04","Casey Miller","SAMS-045","Chirurgien","Chirurgie","employee","DOC-02","555555555555555555",0),
          ("medic05","Sam Carter","SAMS-058","Instructeur","Formation","employee","TRAIN-01","666666666666666666",0),
        ]
        for username, fullname, matricule, grade, division, role, callsign, discord_id, authorized in demo:
            conn.execute("""INSERT INTO users(username,password_hash,full_name,matricule,grade_id,division_id,role,callsign,discord_id,discord_authorized,created_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                         (username,generate_password_hash("SAMS2026!"),fullname,matricule,grade_map[grade],div_map[division],role,callsign,discord_id,authorized,now()))

    if conn.execute("SELECT COUNT(*) n FROM vehicles").fetchone()["n"] == 0:
        vehicles = [
          ("AMB-01","Ambulance Type III","SAMS01","Ambulance","available",None,12450,""),
          ("AMB-02","Ambulance Type III","SAMS02","Ambulance","maintenance",None,18920,"Révision freinage"),
          ("SUV-01","SUV Supervisor","SAMS10","Supervision","available",None,8430,""),
          ("AIR-01","Medical Helicopter","SAMS-A1","Air Rescue","available",None,5200,""),
        ]
        conn.executemany("INSERT INTO vehicles(unit_code,model,plate,vehicle_type,status,assigned_user_id,mileage,notes) VALUES(?,?,?,?,?,?,?,?)", vehicles)

    if conn.execute("SELECT COUNT(*) n FROM trainings").fetchone()["n"] == 0:
        trainings = [
          ("Conduite ambulance","Conduite prioritaire et sécurité routière.",5,1),
          ("Soins avancés","Protocoles ALS et prise en charge critique.",10,1),
          ("Air Rescue","Interventions héliportées.",15,1),
          ("Chirurgie","Certification bloc opératoire.",20,1),
          ("Instructeur","Formation des nouvelles recrues.",25,1),
        ]
        conn.executemany("INSERT INTO trainings(name,description,required_hours,active) VALUES(?,?,?,?)", trainings)

    if conn.execute("SELECT COUNT(*) n FROM rules").fetchone()["n"] == 0:
        rules = [
          ("Respect et professionnalisme","Comportement professionnel obligatoire.","Général",1),
          ("Prise de service","La prise de service sur le portail est obligatoire.","Service",2),
          ("Confidentialité","Les dossiers patients sont confidentiels.","Médical",3),
        ]
        conn.executemany("INSERT INTO rules(title,content,category,sort_order) VALUES(?,?,?,?)", rules)

    if conn.execute("SELECT COUNT(*) n FROM announcements").fetchone()["n"] == 0:
        conn.execute("INSERT INTO announcements(title,content,priority,author,created_at) VALUES(?,?,?,?,?)",
                     ("Bienvenue sur SAMS V3","Le centre opérationnel et le Studio du site sont maintenant disponibles.","important","Direction SAMS",now()))

    conn.commit()
    conn.close()

def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return wrapper

def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        conn = db()
        u = conn.execute("SELECT role,active FROM users WHERE id=?", (session["user_id"],)).fetchone()
        conn.close()
        if not u or not u["active"] or u["role"]!="admin":
            abort(403)
        return fn(*a, **kw)
    return wrapper

def current_user():
    if not session.get("user_id"):
        return None
    conn = db()
    u = conn.execute("""SELECT u.*,g.name grade_name,g.badge grade_badge,g.color grade_color,
                        d.name division_name,d.radio_channel
                        FROM users u LEFT JOIN grades g ON g.id=u.grade_id
                        LEFT JOIN divisions d ON d.id=u.division_id WHERE u.id=?""",
                     (session["user_id"],)).fetchone()
    conn.close()
    return u

@app.context_processor
def global_context():
    conn = db()
    settings = {r["key"]: r["value"] for r in conn.execute("SELECT key,value FROM site_settings")}
    mods = {r["key"]: bool(r["enabled"]) for r in conn.execute("SELECT key,enabled FROM modules")}
    nav = conn.execute("SELECT * FROM navigation WHERE visible=1 ORDER BY sort_order,id").fetchall()
    custom_nav = conn.execute("SELECT * FROM custom_pages WHERE visible=1 ORDER BY sort_order,id").fetchall()
    unread = 0
    if session.get("user_id"):
        unread = conn.execute("SELECT COUNT(*) n FROM notifications WHERE user_id=? AND is_read=0",(session["user_id"],)).fetchone()["n"]
    conn.close()
    return {"current_user": current_user(), "site": settings, "mods": mods, "nav_items": nav, "custom_nav_pages": custom_nav, "unread_notifications": unread}

@app.route("/", methods=["GET","POST"])
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    if request.method=="POST":
        discord_id=request.form.get("discord_id","").strip()
        password=request.form.get("password","")
        conn=db()
        u=conn.execute("""SELECT * FROM users
                          WHERE discord_id=? AND active=1 AND discord_authorized=1""",(discord_id,)).fetchone()
        if u and check_password_hash(u["password_hash"],password):
            session.clear(); session["user_id"]=u["id"]; audit(conn,"Connexion par ID Discord",u["full_name"]); conn.commit(); conn.close()
            return redirect(url_for("dashboard"))
        conn.close(); flash("Accès refusé : ID Discord non autorisé, compte suspendu ou mot de passe incorrect.","error")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    conn=db(); uid=session["user_id"]
    active=conn.execute("SELECT * FROM shifts WHERE user_id=? AND end_time IS NULL ORDER BY id DESC LIMIT 1",(uid,)).fetchone()
    on_duty=conn.execute("""SELECT s.*,u.full_name,u.callsign,g.name grade_name,d.name division_name
                            FROM shifts s JOIN users u ON u.id=s.user_id
                            LEFT JOIN grades g ON g.id=u.grade_id LEFT JOIN divisions d ON d.id=u.division_id
                            WHERE s.end_time IS NULL ORDER BY s.start_time""").fetchall()
    calls=conn.execute("SELECT * FROM emergency_calls WHERE status!='closed' ORDER BY CASE priority WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,id DESC LIMIT 6").fetchall()
    announcements=conn.execute("SELECT * FROM announcements ORDER BY id DESC LIMIT 4").fetchall()
    upcoming=conn.execute("SELECT * FROM planning WHERE start_at>=? ORDER BY start_at LIMIT 4",(now(),)).fetchall()
    stats={
      "on_duty":len(on_duty),
      "open_calls":conn.execute("SELECT COUNT(*) n FROM emergency_calls WHERE status!='closed'").fetchone()["n"],
      "fleet_available":conn.execute("SELECT COUNT(*) n FROM vehicles WHERE status='available'").fetchone()["n"],
      "fleet_total":conn.execute("SELECT COUNT(*) n FROM vehicles").fetchone()["n"],
      "pending_leave":conn.execute("SELECT COUNT(*) n FROM leave_requests WHERE status='pending'").fetchone()["n"],
      "pending_apps":conn.execute("SELECT COUNT(*) n FROM applications WHERE status='pending'").fetchone()["n"],
    }
    conn.close()
    return render_template("dashboard.html",active_shift=active,on_duty=on_duty,calls=calls,announcements=announcements,upcoming=upcoming,stats=stats)

@app.route("/service/<action>",methods=["POST"])
@login_required
def service(action):
    conn=db(); uid=session["user_id"]
    s=conn.execute("SELECT * FROM shifts WHERE user_id=? AND end_time IS NULL ORDER BY id DESC LIMIT 1",(uid,)).fetchone()
    if action=="start" and not s:
        conn.execute("INSERT INTO shifts(user_id,start_time,status) VALUES(?,?,?)",(uid,now(),"available")); audit(conn,"Prise de service")
    elif action=="stop" and s:
        conn.execute("UPDATE shifts SET end_time=?,status='off',current_call_id=NULL WHERE id=?",(now(),s["id"])); audit(conn,"Fin de service")
    elif action in ("available","intervention","transport","hospital","pause") and s:
        conn.execute("UPDATE shifts SET status=? WHERE id=?",(action,s["id"])); audit(conn,"Statut unité",action)
    conn.commit(); conn.close(); return redirect(request.referrer or url_for("dashboard"))

@app.route("/operations",methods=["GET","POST"])
@login_required
def operations():
    conn=db()
    if request.method=="POST":
        action=request.form["action"]
        if action=="create_call":
            code="A-"+datetime.now().strftime("%H%M%S")
            conn.execute("""INSERT INTO emergency_calls(code,priority,location,reason,caller,details,status,created_at,created_by)
                          VALUES(?,?,?,?,?,?, 'open',?,?)""",
                         (code,request.form["priority"],request.form["location"],request.form["reason"],
                          request.form.get("caller",""),request.form.get("details",""),now(),session["user_id"]))
            audit(conn,"Création appel 911",code); flash("Appel créé.","success")
        elif action=="assign":
            call_id=int(request.form["call_id"]); user_id=int(request.form["user_id"])
            conn.execute("INSERT OR IGNORE INTO call_assignments(call_id,user_id,assigned_at) VALUES(?,?,?)",(call_id,user_id,now()))
            conn.execute("UPDATE shifts SET status='intervention',current_call_id=? WHERE user_id=? AND end_time IS NULL",(call_id,user_id))
            notify(conn,user_id,"Nouvelle intervention","Tu as été affecté à un appel 911.","urgent")
            audit(conn,"Affectation unité",f"appel {call_id} / user {user_id}")
        elif action=="close":
            cid=int(request.form["call_id"]); conn.execute("UPDATE emergency_calls SET status='closed',closed_at=? WHERE id=?",(now(),cid))
            conn.execute("UPDATE shifts SET status='available',current_call_id=NULL WHERE current_call_id=?",(cid,))
            audit(conn,"Clôture appel",str(cid))
        elif action=="status":
            conn.execute("UPDATE emergency_calls SET status=? WHERE id=?",(request.form["status"],int(request.form["call_id"])))
        conn.commit(); conn.close(); return redirect(url_for("operations"))
    calls=conn.execute("SELECT * FROM emergency_calls ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'dispatched' THEN 1 ELSE 2 END,id DESC LIMIT 100").fetchall()
    units=conn.execute("""SELECT s.*,u.full_name,u.callsign,u.id user_id,g.name grade_name,d.name division_name
                         FROM shifts s JOIN users u ON u.id=s.user_id LEFT JOIN grades g ON g.id=u.grade_id
                         LEFT JOIN divisions d ON d.id=u.division_id WHERE s.end_time IS NULL ORDER BY u.callsign""").fetchall()
    assigns=conn.execute("""SELECT ca.call_id,u.full_name,u.callsign FROM call_assignments ca JOIN users u ON u.id=ca.user_id""").fetchall()
    assignment_map={}
    for a in assigns: assignment_map.setdefault(a["call_id"],[]).append(a)
    conn.close(); return render_template("operations.html",calls=calls,units=units,assignment_map=assignment_map)

@app.route("/personnel")
@login_required
def personnel():
    conn=db()
    users=conn.execute("""SELECT u.*,g.name grade_name,g.badge grade_badge,g.color grade_color,d.name division_name,
                         EXISTS(SELECT 1 FROM shifts s WHERE s.user_id=u.id AND s.end_time IS NULL) on_duty,
                         (SELECT COUNT(*) FROM certifications c WHERE c.user_id=u.id) cert_count
                         FROM users u LEFT JOIN grades g ON g.id=u.grade_id LEFT JOIN divisions d ON d.id=u.division_id
                         WHERE u.active=1 ORDER BY g.level DESC,u.full_name""").fetchall()
    conn.close(); return render_template("personnel.html",users=users)

@app.route("/fleet",methods=["GET","POST"])
@login_required
def fleet():
    conn=db()
    if request.method=="POST" and current_user()["role"]=="admin":
        action=request.form["action"]
        if action=="save":
            vid=int(request.form.get("id") or 0)
            vals=(request.form["unit_code"],request.form["model"],request.form.get("plate",""),request.form["vehicle_type"],
                  request.form["status"],int(request.form["mileage"] or 0),request.form.get("notes",""))
            if vid:
                conn.execute("UPDATE vehicles SET unit_code=?,model=?,plate=?,vehicle_type=?,status=?,mileage=?,notes=? WHERE id=?",vals+(vid,))
            else:
                conn.execute("INSERT INTO vehicles(unit_code,model,plate,vehicle_type,status,mileage,notes) VALUES(?,?,?,?,?,?,?)",vals)
        elif action=="delete":
            conn.execute("DELETE FROM vehicles WHERE id=?",(int(request.form["id"]),))
        audit(conn,"Gestion flotte",action); conn.commit(); conn.close(); return redirect(url_for("fleet"))
    vehicles=conn.execute("""SELECT v.*,u.full_name assigned_name,u.callsign FROM vehicles v LEFT JOIN users u ON u.id=v.assigned_user_id ORDER BY v.unit_code""").fetchall()
    conn.close(); return render_template("fleet.html",vehicles=vehicles)

@app.route("/medical",methods=["GET","POST"])
@login_required
def medical():
    conn=db()
    if request.method=="POST":
        conn.execute("""INSERT INTO patients(full_name,birth_date,phone,blood_type,allergies,medical_notes,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                     (request.form["full_name"],request.form.get("birth_date",""),request.form.get("phone",""),
                      request.form.get("blood_type",""),request.form.get("allergies",""),request.form.get("medical_notes",""),now(),now()))
        audit(conn,"Création patient",request.form["full_name"]); conn.commit(); conn.close(); return redirect(url_for("medical"))
    q=request.args.get("q","")
    if q:
        patients=conn.execute("SELECT * FROM patients WHERE full_name LIKE ? OR phone LIKE ? ORDER BY updated_at DESC",(f"%{q}%",f"%{q}%")).fetchall()
    else:
        patients=conn.execute("SELECT * FROM patients ORDER BY updated_at DESC LIMIT 100").fetchall()
    conn.close(); return render_template("medical.html",patients=patients,q=q)

@app.route("/medical/<int:pid>",methods=["GET","POST"])
@login_required
def patient(pid):
    conn=db(); p=conn.execute("SELECT * FROM patients WHERE id=?",(pid,)).fetchone()
    if not p: conn.close(); abort(404)
    if request.method=="POST":
        action=request.form["action"]
        if action=="record":
            conn.execute("""INSERT INTO medical_records(patient_id,staff_id,record_type,diagnosis,treatment,notes,created_at)
                           VALUES(?,?,?,?,?,?,?)""",(pid,session["user_id"],request.form["record_type"],request.form["diagnosis"],request.form["treatment"],request.form.get("notes",""),now()))
        elif action=="edit":
            conn.execute("""UPDATE patients SET full_name=?,birth_date=?,phone=?,blood_type=?,allergies=?,medical_notes=?,updated_at=? WHERE id=?""",
                         (request.form["full_name"],request.form.get("birth_date",""),request.form.get("phone",""),request.form.get("blood_type",""),
                          request.form.get("allergies",""),request.form.get("medical_notes",""),now(),pid))
        audit(conn,"Modification dossier patient",p["full_name"]); conn.commit(); conn.close(); return redirect(url_for("patient",pid=pid))
    recs=conn.execute("""SELECT r.*,u.full_name staff_name,g.name grade_name FROM medical_records r JOIN users u ON u.id=r.staff_id
                        LEFT JOIN grades g ON g.id=u.grade_id WHERE r.patient_id=? ORDER BY r.id DESC""",(pid,)).fetchall()
    conn.close(); return render_template("patient.html",patient=p,records=recs)

@app.route("/reports",methods=["GET","POST"])
@login_required
def reports():
    conn=db()
    if request.method=="POST":
        conn.execute("""INSERT INTO incident_reports(call_id,author_id,title,patient_name,location,injuries,treatment,transport,summary,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                     (int(request.form["call_id"]) if request.form.get("call_id") else None,session["user_id"],request.form["title"],
                      request.form.get("patient_name",""),request.form.get("location",""),request.form.get("injuries",""),
                      request.form.get("treatment",""),request.form.get("transport",""),request.form.get("summary",""),now()))
        audit(conn,"Rapport intervention",request.form["title"]); conn.commit(); conn.close(); return redirect(url_for("reports"))
    rows=conn.execute("""SELECT r.*,u.full_name author_name,e.code call_code FROM incident_reports r
                        JOIN users u ON u.id=r.author_id LEFT JOIN emergency_calls e ON e.id=r.call_id ORDER BY r.id DESC""").fetchall()
    calls=conn.execute("SELECT id,code FROM emergency_calls ORDER BY id DESC LIMIT 100").fetchall()
    conn.close(); return render_template("reports.html",reports=rows,calls=calls)

@app.route("/training",methods=["GET","POST"])
@login_required
def training():
    conn=db()
    if request.method=="POST" and current_user()["role"]=="admin":
        action=request.form["action"]
        if action=="training_save":
            tid=int(request.form.get("id") or 0); vals=(request.form["name"],request.form.get("description",""),int(request.form.get("required_hours") or 0),1)
            if tid: conn.execute("UPDATE trainings SET name=?,description=?,required_hours=?,active=? WHERE id=?",vals+(tid,))
            else: conn.execute("INSERT INTO trainings(name,description,required_hours,active) VALUES(?,?,?,?)",vals)
        elif action=="certify":
            conn.execute("""INSERT OR REPLACE INTO certifications(user_id,training_id,issued_by,issued_at,expires_at)
                           VALUES(?,?,?,?,?)""",(int(request.form["user_id"]),int(request.form["training_id"]),current_user()["full_name"],now(),request.form.get("expires_at") or None))
            notify(conn,int(request.form["user_id"]),"Nouvelle certification","Une certification vient de t'être attribuée.","success")
        audit(conn,"Gestion formation",action); conn.commit(); conn.close(); return redirect(url_for("training"))
    trainings=conn.execute("SELECT * FROM trainings ORDER BY active DESC,name").fetchall()
    certs=conn.execute("""SELECT c.*,u.full_name,t.name training_name FROM certifications c JOIN users u ON u.id=c.user_id JOIN trainings t ON t.id=c.training_id ORDER BY c.id DESC""").fetchall()
    users=conn.execute("SELECT id,full_name FROM users WHERE active=1 ORDER BY full_name").fetchall()
    conn.close(); return render_template("training.html",trainings=trainings,certs=certs,users=users)

@app.route("/planning",methods=["GET","POST"])
@login_required
def planning():
    conn=db()
    if request.method=="POST" and current_user()["role"]=="admin":
        action=request.form["action"]
        if action=="save":
            eid=int(request.form.get("id") or 0); vals=(request.form["title"],request.form["event_type"],request.form["start_at"],request.form.get("end_at") or None,request.form.get("location",""),request.form.get("description",""),current_user()["full_name"])
            if eid: conn.execute("UPDATE planning SET title=?,event_type=?,start_at=?,end_at=?,location=?,description=?,created_by=? WHERE id=?",vals+(eid,))
            else: conn.execute("INSERT INTO planning(title,event_type,start_at,end_at,location,description,created_by) VALUES(?,?,?,?,?,?,?)",vals)
        elif action=="delete": conn.execute("DELETE FROM planning WHERE id=?",(int(request.form["id"]),))
        audit(conn,"Gestion planning",action); conn.commit(); conn.close(); return redirect(url_for("planning"))
    events=conn.execute("SELECT * FROM planning ORDER BY start_at").fetchall(); conn.close()
    return render_template("planning.html",events=events)

@app.route("/leave",methods=["GET","POST"])
@login_required
def leave():
    conn=db()
    if request.method=="POST":
        action=request.form["action"]
        if action=="request":
            conn.execute("INSERT INTO leave_requests(user_id,start_date,end_date,reason,status,created_at) VALUES(?,?,?,?, 'pending',?)",
                         (session["user_id"],request.form["start_date"],request.form["end_date"],request.form["reason"],now()))
            audit(conn,"Demande congé",request.form["start_date"])
        elif action=="review" and current_user()["role"]=="admin":
            rid=int(request.form["id"]); status=request.form["status"]
            req=conn.execute("SELECT user_id FROM leave_requests WHERE id=?",(rid,)).fetchone()
            conn.execute("UPDATE leave_requests SET status=?,reviewer_note=? WHERE id=?",(status,request.form.get("reviewer_note",""),rid))
            if req: notify(conn,req["user_id"],"Demande de congé",f"Ta demande est maintenant : {status}.","info")
            audit(conn,"Traitement congé",str(rid))
        conn.commit(); conn.close(); return redirect(url_for("leave"))
    if current_user()["role"]=="admin":
        rows=conn.execute("""SELECT l.*,u.full_name,u.matricule FROM leave_requests l JOIN users u ON u.id=l.user_id ORDER BY l.id DESC""").fetchall()
    else:
        rows=conn.execute("""SELECT l.*,u.full_name,u.matricule FROM leave_requests l JOIN users u ON u.id=l.user_id WHERE l.user_id=? ORDER BY l.id DESC""",(session["user_id"],)).fetchall()
    conn.close(); return render_template("leave.html",requests=rows)

@app.route("/rules")
@login_required
def rules():
    conn=db(); rows=conn.execute("SELECT * FROM rules ORDER BY sort_order,id").fetchall(); conn.close()
    return render_template("rules.html",rules=rows)

@app.route("/announcements")
@login_required
def announcements():
    conn=db(); rows=conn.execute("SELECT * FROM announcements ORDER BY id DESC").fetchall(); conn.close()
    return render_template("announcements.html",announcements=rows)

@app.route("/sanctions")
@login_required
def sanctions():
    conn=db()
    if current_user()["role"]=="admin":
        rows=conn.execute("""SELECT s.*,u.full_name,u.matricule FROM sanctions s JOIN users u ON u.id=s.user_id ORDER BY s.id DESC""").fetchall()
    else:
        rows=conn.execute("""SELECT s.*,u.full_name,u.matricule FROM sanctions s JOIN users u ON u.id=s.user_id WHERE s.user_id=? ORDER BY s.id DESC""",(session["user_id"],)).fetchall()
    conn.close(); return render_template("sanctions.html",sanctions=rows)

@app.route("/notifications")
@login_required
def notifications():
    conn=db(); rows=conn.execute("SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 100",(session["user_id"],)).fetchall()
    conn.execute("UPDATE notifications SET is_read=1 WHERE user_id=?",(session["user_id"],)); conn.commit(); conn.close()
    return render_template("notifications.html",notifications=rows)

@app.route("/profile",methods=["GET","POST"])
@login_required
def profile():
    conn=db()
    if request.method=="POST":
        conn.execute("UPDATE users SET phone=?,callsign=? WHERE id=?",(request.form.get("phone",""),request.form.get("callsign",""),session["user_id"]))
        audit(conn,"Modification profil"); conn.commit(); flash("Profil enregistré.","success")
    u=conn.execute("""SELECT u.*,g.name grade_name,d.name division_name FROM users u LEFT JOIN grades g ON g.id=u.grade_id LEFT JOIN divisions d ON d.id=u.division_id WHERE u.id=?""",(session["user_id"],)).fetchone()
    conn.close(); return render_template("profile.html",user=u)

@app.route("/apply",methods=["GET","POST"])
def apply():
    if request.method=="POST":
        conn=db(); conn.execute("""INSERT INTO applications(rp_name,discord_name,age,experience,motivation,availability,status,created_at)
                                  VALUES(?,?,?,?,?,?, 'pending',?)""",
                               (request.form["rp_name"],request.form["discord_name"],int(request.form["age"]),request.form["experience"],request.form["motivation"],request.form["availability"],now()))
        conn.commit(); conn.close(); return render_template("apply_success.html")
    return render_template("apply.html")

@app.route("/pages/<slug>")
@login_required
def custom_page(slug):
    conn=db(); page=conn.execute("SELECT * FROM custom_pages WHERE slug=? AND visible=1",(slug,)).fetchone(); conn.close()
    if not page: abort(404)
    return render_template("custom_page.html",page=page)

@app.route("/admin",methods=["GET","POST"])
@admin_required
def admin():
    conn=db()
    if request.method=="POST":
        action=request.form["action"]
        if action=="user_add":
            conn.execute("""INSERT INTO users(username,password_hash,full_name,matricule,grade_id,division_id,role,callsign,discord_id,discord_authorized,notes,created_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (request.form["username"],generate_password_hash(request.form["password"]),request.form["full_name"],request.form["matricule"],
                          int(request.form["grade_id"]),int(request.form["division_id"]),request.form["role"],request.form.get("callsign",""),
                          request.form.get("discord_id") or None,1 if request.form.get("discord_authorized") else 0,request.form.get("notes",""),now()))
        elif action=="user_edit":
            conn.execute("""UPDATE users SET full_name=?,matricule=?,grade_id=?,division_id=?,role=?,callsign=?,discord_id=?,discord_authorized=?,notes=? WHERE id=?""",
                         (request.form["full_name"],request.form["matricule"],int(request.form["grade_id"]),int(request.form["division_id"]),request.form["role"],
                          request.form.get("callsign",""),request.form.get("discord_id") or None,1 if request.form.get("discord_authorized") else 0,request.form.get("notes",""),int(request.form["id"])))
        elif action=="user_toggle":
            conn.execute("UPDATE users SET active=CASE active WHEN 1 THEN 0 ELSE 1 END WHERE id=?",(int(request.form["id"]),))
        elif action=="grade_save":
            gid=int(request.form.get("id") or 0); vals=(request.form["name"],int(request.form["level"]),request.form["badge"],int(request.form.get("hourly_rate") or 0),request.form.get("color","#df2031"))
            if gid: conn.execute("UPDATE grades SET name=?,level=?,badge=?,hourly_rate=?,color=? WHERE id=?",vals+(gid,))
            else: conn.execute("INSERT INTO grades(name,level,badge,hourly_rate,color) VALUES(?,?,?,?,?)",vals)
        elif action=="division_save":
            did=int(request.form.get("id") or 0); vals=(request.form["name"],request.form.get("description",""),request.form.get("lead_name",""),request.form.get("radio_channel",""),request.form.get("color","#df2031"))
            if did: conn.execute("UPDATE divisions SET name=?,description=?,lead_name=?,radio_channel=?,color=? WHERE id=?",vals+(did,))
            else: conn.execute("INSERT INTO divisions(name,description,lead_name,radio_channel,color) VALUES(?,?,?,?,?)",vals)
        elif action=="sanction":
            uid=int(request.form["user_id"]); conn.execute("INSERT INTO sanctions(user_id,type,reason,author,created_at,active) VALUES(?,?,?,?,?,1)",(uid,request.form["type"],request.form["reason"],current_user()["full_name"],now()))
            notify(conn,uid,"Sanction / avertissement",request.form["reason"],"warning")
        elif action=="promotion":
            uid=int(request.form["user_id"]); gid=int(request.form["grade_id"])
            conn.execute("INSERT INTO promotions(user_id,proposed_grade_id,reason,status,proposed_by,created_at) VALUES(?,?,?,'pending',?,?)",(uid,gid,request.form["reason"],current_user()["full_name"],now()))
            notify(conn,uid,"Proposition de promotion","Une proposition de promotion a été enregistrée.","info")
        elif action=="promotion_review":
            pid=int(request.form["id"]); status=request.form["status"]
            p=conn.execute("SELECT * FROM promotions WHERE id=?",(pid,)).fetchone()
            conn.execute("UPDATE promotions SET status=? WHERE id=?",(status,pid))
            if p and status=="accepted":
                conn.execute("UPDATE users SET grade_id=? WHERE id=?",(p["proposed_grade_id"],p["user_id"]))
                notify(conn,p["user_id"],"Promotion validée","Ton grade a été mis à jour.","success")
        elif action=="application_review":
            conn.execute("UPDATE applications SET status=?,reviewer_note=? WHERE id=?",(request.form["status"],request.form.get("reviewer_note",""),int(request.form["id"])))
        audit(conn,"Administration",action); conn.commit(); conn.close(); return redirect(url_for("admin"))
    users=conn.execute("""SELECT u.*,g.name grade_name,d.name division_name FROM users u LEFT JOIN grades g ON g.id=u.grade_id LEFT JOIN divisions d ON d.id=u.division_id ORDER BY active DESC,g.level DESC,u.full_name""").fetchall()
    grades=conn.execute("SELECT * FROM grades ORDER BY level DESC").fetchall()
    divisions=conn.execute("SELECT * FROM divisions ORDER BY name").fetchall()
    apps=conn.execute("SELECT * FROM applications ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END,id DESC").fetchall()
    promotions=conn.execute("""SELECT p.*,u.full_name,g.name grade_name FROM promotions p JOIN users u ON u.id=p.user_id JOIN grades g ON g.id=p.proposed_grade_id ORDER BY p.id DESC""").fetchall()
    logs=conn.execute("""SELECT l.*,u.full_name FROM audit_logs l LEFT JOIN users u ON u.id=l.user_id ORDER BY l.id DESC LIMIT 100""").fetchall()
    conn.close(); return render_template("admin.html",users=users,grades=grades,divisions=divisions,applications=apps,promotions=promotions,logs=logs)

@app.route("/studio",methods=["GET","POST"])
@admin_required
def site_studio():
    conn=db()
    if request.method=="POST":
        action=request.form["action"]
        if action=="setting":
            conn.execute("UPDATE site_settings SET value=? WHERE key=?",(request.form["value"],request.form["key"]))
        elif action=="module":
            conn.execute("UPDATE modules SET enabled=?,name=?,description=?,sort_order=? WHERE key=?",
                         (1 if request.form.get("enabled") else 0,request.form["name"],request.form["description"],int(request.form["sort_order"]),request.form["key"]))
        elif action=="nav":
            conn.execute("UPDATE navigation SET label=?,icon=?,sort_order=?,visible=?,admin_only=? WHERE id=?",
                         (request.form["label"],request.form["icon"],int(request.form["sort_order"]),1 if request.form.get("visible") else 0,1 if request.form.get("admin_only") else 0,int(request.form["id"])))
        elif action=="page_save":
            pid=int(request.form.get("id") or 0); vals=(request.form["slug"],request.form["title"],request.form.get("subtitle",""),request.form.get("body",""),1 if request.form.get("visible") else 0,int(request.form.get("sort_order") or 0))
            if pid: conn.execute("UPDATE custom_pages SET slug=?,title=?,subtitle=?,body=?,visible=?,sort_order=? WHERE id=?",vals+(pid,))
            else: conn.execute("INSERT INTO custom_pages(slug,title,subtitle,body,visible,sort_order) VALUES(?,?,?,?,?,?)",vals)
        elif action=="page_delete":
            conn.execute("DELETE FROM custom_pages WHERE id=?",(int(request.form["id"]),))
        elif action=="rule_save":
            rid=int(request.form.get("id") or 0); vals=(request.form["title"],request.form["content"],request.form["category"],int(request.form.get("sort_order") or 0))
            if rid: conn.execute("UPDATE rules SET title=?,content=?,category=?,sort_order=? WHERE id=?",vals+(rid,))
            else: conn.execute("INSERT INTO rules(title,content,category,sort_order) VALUES(?,?,?,?)",vals)
        elif action=="rule_delete":
            conn.execute("DELETE FROM rules WHERE id=?",(int(request.form["id"]),))
        elif action=="announcement_save":
            aid=int(request.form.get("id") or 0); vals=(request.form["title"],request.form["content"],request.form["priority"],current_user()["full_name"],now())
            if aid: conn.execute("UPDATE announcements SET title=?,content=?,priority=?,author=?,created_at=? WHERE id=?",vals+(aid,))
            else: conn.execute("INSERT INTO announcements(title,content,priority,author,created_at) VALUES(?,?,?,?,?)",vals)
        elif action=="announcement_delete":
            conn.execute("DELETE FROM announcements WHERE id=?",(int(request.form["id"]),))
        audit(conn,"Studio du site",action); conn.commit(); conn.close(); flash("Modification enregistrée.","success"); return redirect(url_for("site_studio"))
    settings=conn.execute("SELECT * FROM site_settings ORDER BY group_name,label").fetchall()
    modules=conn.execute("SELECT * FROM modules ORDER BY sort_order").fetchall()
    nav=conn.execute("SELECT * FROM navigation ORDER BY sort_order,id").fetchall()
    pages=conn.execute("SELECT * FROM custom_pages ORDER BY sort_order,id").fetchall()
    rules_rows=conn.execute("SELECT * FROM rules ORDER BY sort_order,id").fetchall()
    anns=conn.execute("SELECT * FROM announcements ORDER BY id DESC").fetchall()
    conn.close()
    return render_template("studio.html",settings=settings,modules=modules,nav=nav,pages=pages,rules=rules_rows,announcements=anns)

@app.errorhandler(403)
def e403(_): return render_template("error.html",code=403,message="Accès Direction uniquement."),403
@app.errorhandler(404)
def e404(_): return render_template("error.html",code=404,message="Page introuvable."),404

# Initialise automatiquement le schéma au démarrage du service (y compris avec Gunicorn).
if DATABASE_URL:
    init_db()

if __name__=="__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")), debug=False)
