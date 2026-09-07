from flask import Flask, jsonify, render_template, request
from iqoptionapi.stable_api import IQ_Option
import time
import os

app = Flask(__name__)

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

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/candles")
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
