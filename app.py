import os, uuid
from datetime import date, datetime
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for, session,
                   flash, abort, send_from_directory, g)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.getenv("SECRET_KEY", "dev-change-me"),
    SQLALCHEMY_DATABASE_URI=os.getenv("DATABASE_URL", "sqlite:///sales.db").replace("postgres://", "postgresql://", 1),
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,
)
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
db = SQLAlchemy(app)

STAGES = ["Lead", "Qualified", "Proposal", "Negotiation", "Won", "Lost"]
OPEN = STAGES[:4]
DOC_KINDS = ["RFP", "Quotation", "Proposal", "Contract", "Official document", "Other"]
ACT_KINDS = ["Call", "Meeting", "Email", "Follow-up", "Demo"]
ROLES = ["rep", "manager", "admin"]


class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password = db.Column(db.String(300), nullable=False)
    role = db.Column(db.String(20), default="rep")
    commission_rate = db.Column(db.Float, default=5.0)   # % of deal amount
    monthly_target = db.Column(db.Float, default=0.0)
    active = db.Column(db.Boolean, default=True)


class Client(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    industry = db.Column(db.String(100))
    contact_name = db.Column(db.String(120))
    email = db.Column(db.String(200))
    phone = db.Column(db.String(60))
    address = db.Column(db.String(300))
    notes = db.Column(db.Text)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    owner = db.relationship("User")
    deals = db.relationship("Deal", backref="client", order_by="Deal.id.desc()")


class Deal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    client_id = db.Column(db.Integer, db.ForeignKey("client.id"), nullable=False)
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    amount = db.Column(db.Float, default=0)
    stage = db.Column(db.String(30), default="Lead")
    expected_close = db.Column(db.Date)
    closed_at = db.Column(db.Date)
    commission_rate = db.Column(db.Float)  # optional override; else owner's rate
    notes = db.Column(db.Text)
    team_terms = db.Column(db.Text)        # teaming agreement wording
    owner = db.relationship("User")
    members = db.relationship("DealMember", backref="deal", cascade="all,delete-orphan")
    documents = db.relationship("Document", backref="deal", cascade="all,delete-orphan", order_by="Document.id.desc()")
    activities = db.relationship("Activity", backref="deal", cascade="all,delete-orphan", order_by="Activity.due")

    @property
    def rate(self):
        return self.commission_rate if self.commission_rate is not None else (self.owner.commission_rate or 0)

    @property
    def pool(self):  # commission formula: amount x rate%
        return (self.amount or 0) * self.rate / 100


class DealMember(db.Model):  # teaming agreement: each member's share of the pool (%)
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey("deal.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    share = db.Column(db.Float, default=0)
    user = db.relationship("User")

    @property
    def payout(self):
        return self.deal.pool * (self.share or 0) / 100


class Document(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey("deal.id"), nullable=False)
    kind = db.Column(db.String(40))
    filename = db.Column(db.String(300))
    stored = db.Column(db.String(300))
    uploaded_by = db.Column(db.Integer, db.ForeignKey("user.id"))
    created = db.Column(db.DateTime, default=datetime.utcnow)
    uploader = db.relationship("User")


class Activity(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    deal_id = db.Column(db.Integer, db.ForeignKey("deal.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    kind = db.Column(db.String(30))
    note = db.Column(db.String(500))
    due = db.Column(db.Date)
    done = db.Column(db.Boolean, default=False)
    user = db.relationship("User")


with app.app_context():
    db.create_all()
    if not User.query.first():
        db.session.add(User(name="Admin", email=os.getenv("ADMIN_EMAIL", "admin@company.com").lower(), role="admin",
                            password=generate_password_hash(os.getenv("ADMIN_PASSWORD", "admin1234"))))
        db.session.commit()


# ---------- helpers ----------
@app.template_filter("money")
def money(v):
    return f"{(v or 0):,.2f}"


app.jinja_env.globals.update(STAGES=STAGES, OPEN=OPEN, DOC_KINDS=DOC_KINDS, ACT_KINDS=ACT_KINDS, today=date.today)


@app.before_request
def load_user():
    g.user = db.session.get(User, session["uid"]) if "uid" in session else None
    if not g.user and request.endpoint not in ("login", "static"):
        return redirect(url_for("login"))


def admin_only(f):
    @wraps(f)
    def w(*a, **k):
        if g.user.role != "admin":
            abort(403)
        return f(*a, **k)
    return w


def can_edit(deal):
    return g.user.role in ("admin", "manager") or deal.owner_id == g.user.id


def fill(obj, f, text=(), nums=(), dates=()):
    for n in text:
        setattr(obj, n, f.get(n, "").strip())
    for n in nums:
        v = f.get(n, "").strip()
        setattr(obj, n, float(v) if v else None)
    for n in dates:
        v = f.get(n, "").strip()
        setattr(obj, n, date.fromisoformat(v) if v else None)


def users():
    return User.query.filter_by(active=True).order_by(User.name).all()


# ---------- auth ----------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = User.query.filter_by(email=request.form["email"].strip().lower(), active=True).first()
        if u and check_password_hash(u.password, request.form["password"]):
            session["uid"] = u.id
            return redirect(url_for("dashboard"))
        flash("Wrong email or password.")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- dashboard ----------
@app.route("/")
def dashboard():
    first = date.today().replace(day=1)
    deals = Deal.query.all()
    by_stage = {s: [d for d in deals if d.stage == s] for s in STAGES}
    won_month = sum(d.amount for d in by_stage["Won"] if d.closed_at and d.closed_at >= first)
    board = {}
    for d in by_stage["Won"]:
        board[d.owner.name] = board.get(d.owner.name, 0) + d.amount
    tasks = (Activity.query.filter_by(done=False, user_id=g.user.id).order_by(Activity.due).limit(8).all())
    closing = sorted([d for d in deals if d.stage in OPEN and d.expected_close], key=lambda d: d.expected_close)[:6]
    closed = len(by_stage["Won"]) + len(by_stage["Lost"])
    win_rate = round(100 * len(by_stage["Won"]) / closed) if closed else 0
    return render_template("dashboard.html", by_stage=by_stage, won_month=won_month, win_rate=win_rate,
                           board=sorted(board.items(), key=lambda x: -x[1]), tasks=tasks, closing=closing,
                           pipeline=sum(d.amount for s in OPEN for d in by_stage[s]))


# ---------- clients ----------
CLIENT_FIELDS = ["name", "industry", "contact_name", "email", "phone", "address", "notes"]


@app.route("/clients")
def clients():
    q = request.args.get("q", "")
    rows = Client.query.filter(Client.name.ilike(f"%{q}%")).order_by(Client.name).all()
    return render_template("clients.html", rows=rows, q=q)


@app.route("/clients/new", methods=["GET", "POST"])
@app.route("/clients/<int:id>/edit", methods=["GET", "POST"])
def client_form(id=None):
    c = db.get_or_404(Client, id) if id else Client(owner_id=g.user.id)
    if request.method == "POST":
        fill(c, request.form, text=CLIENT_FIELDS)
        db.session.add(c); db.session.commit()
        return redirect(url_for("client", id=c.id))
    return render_template("client_form.html", c=c)


@app.route("/clients/<int:id>")
def client(id):
    return render_template("client.html", c=db.get_or_404(Client, id))


# ---------- deals ----------
@app.route("/deals")
def deals():
    q = Deal.query
    if request.args.get("stage"):
        q = q.filter_by(stage=request.args["stage"])
    if request.args.get("mine"):
        q = q.filter(Deal.members.any(user_id=g.user.id) | (Deal.owner_id == g.user.id))
    return render_template("deals.html", rows=q.order_by(Deal.id.desc()).all())


@app.route("/deals/new", methods=["GET", "POST"])
@app.route("/deals/<int:id>/edit", methods=["GET", "POST"])
def deal_form(id=None):
    d = db.get_or_404(Deal, id) if id else Deal(owner_id=g.user.id, client_id=request.args.get("client_id", type=int))
    if id and not can_edit(d):
        abort(403)
    if request.method == "POST":
        fill(d, request.form, text=["title", "notes"], nums=["amount", "commission_rate"], dates=["expected_close"])
        d.client_id = int(request.form["client_id"])
        if g.user.role != "rep" and request.form.get("owner_id"):
            d.owner_id = int(request.form["owner_id"])
        if not id:
            d.members.append(DealMember(user_id=d.owner_id, share=100))
        db.session.add(d); db.session.commit()
        return redirect(url_for("deal", id=d.id))
    return render_template("deal_form.html", d=d, clients=Client.query.order_by(Client.name).all(), users=users())


@app.route("/deals/<int:id>")
def deal(id):
    d = db.get_or_404(Deal, id)
    return render_template("deal.html", d=d, users=users(), editable=can_edit(d),
                           share_total=sum(m.share or 0 for m in d.members))


@app.post("/deals/<int:id>/stage")
def deal_stage(id):
    d = db.get_or_404(Deal, id)
    if not can_edit(d):
        abort(403)
    d.stage = request.form["stage"]
    d.closed_at = date.today() if d.stage in ("Won", "Lost") else None
    db.session.commit()
    return redirect(url_for("deal", id=id))


@app.post("/deals/<int:id>/team")
def deal_team(id):
    d = db.get_or_404(Deal, id)
    if not can_edit(d):
        abort(403)
    rows = [(int(u), float(s)) for u, s in zip(request.form.getlist("user_id"), request.form.getlist("share")) if u and s]
    if len({u for u, _ in rows}) != len(rows) or abs(sum(s for _, s in rows) - 100) > 0.01:
        flash("Team shares must be unique people and add up to exactly 100%.")
        return redirect(url_for("deal", id=id))
    d.members[:] = [DealMember(user_id=u, share=s) for u, s in rows]
    d.team_terms = request.form.get("team_terms", "").strip()
    db.session.commit()
    flash("Teaming agreement saved.")
    return redirect(url_for("deal", id=id))


@app.post("/deals/<int:id>/upload")
def upload(id):
    d = db.get_or_404(Deal, id)
    f = request.files.get("file")
    if not f or not f.filename:
        flash("Choose a file to upload.")
        return redirect(url_for("deal", id=id))
    stored = f"{uuid.uuid4().hex}_{secure_filename(f.filename)}"
    f.save(os.path.join(UPLOAD_DIR, stored))
    db.session.add(Document(deal_id=id, kind=request.form["kind"], filename=f.filename, stored=stored, uploaded_by=g.user.id))
    db.session.commit()
    return redirect(url_for("deal", id=id))


@app.route("/docs/<int:id>")
def download(id):
    doc = db.get_or_404(Document, id)
    return send_from_directory(UPLOAD_DIR, doc.stored, as_attachment=True, download_name=doc.filename)


@app.post("/docs/<int:id>/delete")
def doc_delete(id):
    doc = db.get_or_404(Document, id)
    if not can_edit(doc.deal):
        abort(403)
    did = doc.deal_id
    try:
        os.remove(os.path.join(UPLOAD_DIR, doc.stored))
    except OSError:
        pass
    db.session.delete(doc); db.session.commit()
    return redirect(url_for("deal", id=did))


@app.post("/deals/<int:id>/activity")
def activity(id):
    a = Activity(deal_id=id, user_id=g.user.id)
    fill(a, request.form, text=["kind", "note"], dates=["due"])
    db.session.add(a); db.session.commit()
    return redirect(url_for("deal", id=id))


@app.post("/activity/<int:id>/done")
def activity_done(id):
    a = db.get_or_404(Activity, id)
    a.done = not a.done
    db.session.commit()
    return redirect(request.referrer or url_for("dashboard"))


# ---------- commissions ----------
@app.route("/commissions")
def commissions():
    won = Deal.query.filter_by(stage="Won").order_by(Deal.closed_at.desc()).all()
    rows = [(d, m) for d in won for m in d.members if g.user.role != "rep" or m.user_id == g.user.id]
    totals = {}
    for d, m in rows:
        totals[m.user.name] = totals.get(m.user.name, 0) + m.payout
    return render_template("commissions.html", rows=rows, totals=sorted(totals.items(), key=lambda x: -x[1]))


# ---------- team ----------
@app.route("/team")
def team():
    first = date.today().replace(day=1)
    stats = {}
    for u in User.query.order_by(User.name).all():
        won = Deal.query.filter_by(stage="Won", owner_id=u.id)
        stats[u.id] = (sum(d.amount for d in won.all() if d.closed_at and d.closed_at >= first),
                       Deal.query.filter(Deal.owner_id == u.id, Deal.stage.in_(OPEN)).count())
    return render_template("team.html", rows=User.query.order_by(User.name).all(), stats=stats)


@app.route("/team/new", methods=["GET", "POST"])
@app.route("/team/<int:id>/edit", methods=["GET", "POST"])
@admin_only
def user_form(id=None):
    u = db.get_or_404(User, id) if id else User()
    if request.method == "POST":
        fill(u, request.form, text=["name", "role"], nums=["commission_rate", "monthly_target"])
        u.email = request.form["email"].strip().lower()
        u.active = "active" in request.form or not id
        if request.form.get("password"):
            u.password = generate_password_hash(request.form["password"])
        elif not id:
            flash("Set a password for the new user.")
            return render_template("user_form.html", u=u, roles=ROLES)
        db.session.add(u); db.session.commit()
        return redirect(url_for("team"))
    return render_template("user_form.html", u=u, roles=ROLES)


if __name__ == "__main__":
    app.run(debug=True)
