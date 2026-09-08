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

# o objeto `iq` é compartilhado por toda requisição que chega no servidor —
# se o Render/gunicorn processar mais de uma requisição ao mesmo tempo (mais
# de 1 thread), duas chamadas simultâneas na biblioteca da IQ Option podem
# corromper o estado interno dela (ela não foi feita pra ser usada de vários
# lugares ao mesmo tempo). Esse lock garante que só uma requisição por vez
# fala com a IQ Option — as outras esperam a vez, mas ninguém pisa no
# estado da outra.
iq_lock = threading.Lock()


def iq_conectar():
    """Conecta (ou reconecta) na IQ Option. Chamado no boot e sempre que uma
    chamada à API falhar por sessão expirada/caída."""
    check, reason = iq.connect()
    if check:
        iq.change_balance("PRACTICE")
        print("Conectado com sucesso na IQ Option!")
    else:
        print("Erro ao conectar:", reason)
    return check


iq_conectar()


def iq_get_candles_seguro(ativo, timeframe, qtd, fim):
    """
    Wrapper em volta de iq.get_candles() com duas proteções:
    1. Lock — evita duas requisições mexendo na conexão ao mesmo tempo.
    2. Reconexão automática — se a sessão da IQ Option caiu (comum depois de
       várias horas rodando), detecta que a chamada falhou/voltou vazia,
       tenta reconectar, e refaz a chamada UMA vez antes de desistir. Sem
       isso, depois que a sessão cai, o app fica servindo lista vazia pra
       sempre até alguém reiniciar o servidor manualmente no Render.
    """
    with iq_lock:
        try:
            if not iq.check_connect():
                iq_conectar()
            velas = iq.get_candles(ativo, timeframe, qtd, fim)
            if velas:
                return velas
        except Exception as e:
            print("get_candles falhou, tentando reconectar:", e)

        # não veio nada (ou deu exceção) — tenta reconectar e refazer 1 vez
        try:
            if iq_conectar():
                return iq.get_candles(ativo, timeframe, qtd, fim)
        except Exception as e:
            print("get_candles falhou de novo mesmo após reconectar:", e)
        return []


TIMEFRAME = 60
QTD_VELAS_POLL = 100          # atualização ao vivo (rápido, chamado a cada poucos segundos)
QTD_VELAS_MAX = 35000         # trava de segurança — cobre até 500 velas de H1 (30000min)
QTD_VELAS_MAX_OTC = 1500      # pares OTC são sintéticos — só as velas recentes são confiáveis
LOTE_POR_CHAMADA = 1000       # a IQ Option limita quantas velas vêm numa única chamada

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


def buscar_velas_em_blocos(ativo, qtd_total):
    """
    A IQ Option limita quantas velas vêm numa única chamada de get_candles
    (na prática, ~1000 por vez). Pra buscar um histórico maior (ex: 12000,
    pro app abrir com bastante vela em qualquer timeframe), busca em vários
    blocos de LOTE_POR_CHAMADA, andando pra trás no tempo a cada chamada
    (usando o timestamp da vela mais antiga já recebida como novo "fim").

    Se algum bloco no meio do caminho falhar (timeout, reconexão da IQ
    Option, etc.), NÃO derruba a busca inteira — devolve o que já
    conseguiu buscar até ali, pra pelo menos aparecer alguma vela em vez
    do gráfico ficar totalmente vazio.
    """
    todas = []
    fim = time.time()
    while len(todas) < qtd_total:
        restante = qtd_total - len(todas)
        lote = min(LOTE_POR_CHAMADA, restante)
        velas = iq_get_candles_seguro(ativo, TIMEFRAME, lote, fim)
        if not velas:
            break  # IQ Option não tem mais histórico pra trás (ou falhou de vez)
        todas = velas + todas
        mais_antiga = velas[0]["from"]
        if fim <= mais_antiga:
            break  # não avançou pra trás, evita loop infinito
        fim = mais_antiga - 1

    # blinda contra blocos sobrepostos ou fora de ordem (a lib do gráfico no
    # frontend se recusa a desenhar — fica tudo em branco, sem erro visível —
    # se receber velas com tempo repetido ou fora de ordem crescente)
    unicas = {}
    for v in todas:
        unicas[v["from"]] = v
    return [unicas[t] for t in sorted(unicas.keys())]


@app.route("/candles")
@login_required
def candles():
    ativo = request.args.get("ativo", "EURUSD-OTC")
    qtd = request.args.get("qtd", type=int) or QTD_VELAS_POLL
    qtd = min(max(qtd, 1), QTD_VELAS_MAX)

    # Pares OTC são sintéticos (a IQ Option gera o movimento artificialmente
    # pra manter o ativo negociável fora do horário real de mercado) — buscar
    # muito histórico passado desses pares às vezes vem "regenerado" de forma
    # diferente do que realmente foi mostrado ao vivo, fazendo o gráfico não
    # bater com a plataforma oficial. Pra OTC, trava a profundidade de
    # histórico num valor bem mais raso (só as velas recentes são confiáveis).
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
        })
    return jsonify(dados)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
