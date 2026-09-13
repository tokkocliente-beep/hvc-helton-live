from flask import Flask, jsonify, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from iqoptionapi.stable_api import IQ_Option
import time
import os
import threading

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "troque-esta-chave-em-producao")

# ---------------------------------------------------------------------------
# Banco de dados — SQLite local, simples, sem depender de nenhum serviço
# externo (Neon, etc.). Vive num arquivo dentro do próprio servidor. Único
# efeito colateral: reinicia zerado toda vez que o Render reconstrói o
# serviço (deploy novo) — os logins cadastrados precisam ser feitos de novo.
# Como é só uso pessoal, isso não deve ser um problema de verdade.
# ---------------------------------------------------------------------------
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///usuarios.db"
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
# Conexão com a IQ Option — uma conexão só, compartilhada, usada pra buscar
# as velas do gráfico pra qualquer pessoa logada.
# ---------------------------------------------------------------------------
EMAIL_IQ = os.environ.get("IQ_EMAIL")
SENHA_IQ = os.environ.get("IQ_SENHA")

iq = IQ_Option(EMAIL_IQ, SENHA_IQ)

# só uma requisição por vez fala com a IQ Option (a biblioteca não foi feita
# pra ser usada de vários lugares ao mesmo tempo)
iq_lock = threading.Lock()


def iq_conectar():
    check, reason = iq.connect()
    if check:
        iq.change_balance("PRACTICE")
        print("Conectado com sucesso na IQ Option!")
    else:
        print("Erro ao conectar:", reason)
    return check


try:
    iq_conectar()
except Exception as e:
    print("Falha ao conectar na IQ Option no boot (vai tentar de novo na primeira requisição):", e)


def iq_get_candles_seguro(ativo, timeframe, qtd, fim):
    """Busca velas com reconexão automática se a sessão tiver caído."""
    with iq_lock:
        try:
            if not iq.check_connect():
                iq_conectar()
            velas = iq.get_candles(ativo, timeframe, qtd, fim)
            if velas:
                return velas
        except Exception as e:
            print("get_candles falhou, tentando reconectar:", e)

        try:
            if iq_conectar():
                return iq.get_candles(ativo, timeframe, qtd, fim)
        except Exception as e:
            print("get_candles falhou de novo mesmo após reconectar:", e)
        return []


TIMEFRAME = 60
QTD_VELAS_POLL = 100
QTD_VELAS_MAX = 35000
QTD_VELAS_MAX_OTC = 1500
LOTE_POR_CHAMADA = 1000


def buscar_velas_em_blocos(ativo, qtd_total):
    """Busca em vários blocos de LOTE_POR_CHAMADA, andando pra trás no
    tempo, pra contornar o limite de ~1000 velas por chamada da IQ Option."""
    todas = []
    fim = time.time()
    while len(todas) < qtd_total:
        restante = qtd_total - len(todas)
        lote = min(LOTE_POR_CHAMADA, restante)
        velas = iq_get_candles_seguro(ativo, TIMEFRAME, lote, fim)
        if not velas:
            break
        todas = velas + todas
        mais_antiga = velas[0]["from"]
        if fim <= mais_antiga:
            break
        fim = mais_antiga - 1

    unicas = {}
    for v in todas:
        unicas[v["from"]] = v
    return [unicas[t] for t in sorted(unicas.keys())]


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
# Rotas do app
# ---------------------------------------------------------------------------
@app.route("/")
@login_required
def home():
    return render_template("index.html")


@app.route("/candles")
@login_required
def candles():
    ativo = request.args.get("ativo", "EURUSD-OTC")
    qtd = request.args.get("qtd", type=int) or QTD_VELAS_POLL
    qtd = min(max(qtd, 1), QTD_VELAS_MAX)

    if "OTC" in ativo.upper():
        qtd = min(qtd, QTD_VELAS_MAX_OTC)

    if qtd <= LOTE_POR_CHAMADA:
        velas = iq_get_candles_seguro(ativo, TIMEFRAME, qtd, time.time())
    else:
        velas = buscar_velas_em_blocos(ativo, qtd)

    dados = []
    for v in velas:
        dados.append({
            "time": v["from"],
            "open": v["open"],
            "high": v["max"],
            "low": v["min"],
            "close": v["close"],
            "volume": v.get("volume", 0),
        })
    return jsonify(dados)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)
