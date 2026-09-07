from flask import Flask, jsonify, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from iqoptionapi.stable_api import IQ_Option
import time
import os

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "troque-esta-chave-em-producao")

# ---------------------------------------------------------------------------
# Banco de dados (Postgres via Neon, ou SQLite local como fallback pra testes)
# ---------------------------------------------------------------------------
database_url = os.environ.get("DATABASE_URL", "sqlite:///usuarios.db")
# Neon/Render às vezes fornecem a URL como "postgres://" — SQLAlchemy mais novo
# exige "postgresql://"
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

login_manager = LoginManager(app)
login_manager.login_view = "login"


class Usuario(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)

    def set_senha(self, senha):
        self.senha_hash = generate_password_hash(senha)

    def checar_senha(self, senha):
        return check_password_hash(self.senha_hash, senha)


@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))


with app.app_context():
    db.create_all()

# ---------------------------------------------------------------------------
# Conexão com a IQ Option
# ---------------------------------------------------------------------------
EMAIL_IQ = os.environ.get("IQ_EMAIL")
SENHA_IQ = os.environ.get("IQ_SENHA")

iq = IQ_Option(EMAIL_IQ, SENHA_IQ)
check, reason = iq.connect()
if check:
    iq.change_balance("PRACTICE")
    print("Conectado com sucesso na IQ Option!")
else:
    print("Erro ao conectar:", reason)

TIMEFRAME = 60
QTD_VELAS = 100

# ---------------------------------------------------------------------------
# Autenticação
# ---------------------------------------------------------------------------
@app.route("/registrar", methods=["GET", "POST"])
def registrar():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")
        confirmar = request.form.get("confirmar", "")

        if not email or not senha:
            flash("Preencha email e senha.")
            return redirect(url_for("registrar"))
        if senha != confirmar:
            flash("As senhas não são iguais.")
            return redirect(url_for("registrar"))
        if len(senha) < 6:
            flash("A senha precisa ter pelo menos 6 caracteres.")
            return redirect(url_for("registrar"))
        if Usuario.query.filter_by(email=email).first():
            flash("Já existe uma conta com esse email.")
            return redirect(url_for("registrar"))

        novo = Usuario(email=email)
        novo.set_senha(senha)
        db.session.add(novo)
        db.session.commit()

        login_user(novo)
        return redirect(url_for("home"))

    return render_template("registrar.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        senha = request.form.get("senha", "")

        usuario = Usuario.query.filter_by(email=email).first()
        if usuario and usuario.checar_senha(senha):
            login_user(usuario)
            return redirect(url_for("home"))

        flash("Email ou senha incorretos.")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("login"))

# ---------------------------------------------------------------------------
# Rotas do app (protegidas por login)
# ---------------------------------------------------------------------------
@app.route("/")
@login_required
def home():
    return render_template("index.html")


@app.route("/candles")
@login_required
def candles():
    ativo = request.args.get("ativo", "EURUSD-OTC")
    velas = iq.get_candles(ativo, TIMEFRAME, QTD_VELAS, time.time())
    dados = []
    for v in velas:
        dados.append({
            "time": v["from"],
            "open": v["open"],
            "high": v["max"],
            "low": v["min"],
            "close": v["close"],
        })
    return jsonify(dados)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
