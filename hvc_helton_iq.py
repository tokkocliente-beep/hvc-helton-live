"""
HVC HELTON - Conexao com IQ Option + Grafico de Candles
========================================================
"""

import time
from datetime import datetime
import os

# ==========================================================
# CONFIGURACOES - EDITE AQUI
# ==========================================================

EMAIL_IQ = os.environ.get("IQ_EMAIL")           # <-- coloque seu email da IQ Option
SENHA_IQ = os.environ.get("IQ_SENHA")           # <-- coloque sua senha da IQ Option

TIPO_CONTA = "PRACTICE"                          # "PRACTICE" = conta demo | "REAL" = conta real

ATIVO = "EURUSD"                                 # ativo que voce quer ver o grafico
TIMEFRAME_SEGUNDOS = 60                          # 60 = M1, 300 = M5, 900 = M15, 3600 = H1
QUANTIDADE_VELAS = 100                           # quantas velas buscar

# ==========================================================
# NAO PRECISA MEXER DAQUI PRA BAIXO
# ==========================================================

def conectar_iq(email, senha):
    from iqoptionapi.stable_api import IQ_Option

    print("Conectando na IQ Option...")
    iq = IQ_Option(email, senha)
    check, reason = iq.connect()

    if not check:
        print("ERRO ao conectar:", reason)
        return None

    print("Conectado com sucesso!")
    return iq


def buscar_candles(iq, ativo, timeframe, quantidade):
    print(f"\nBuscando {quantidade} velas de {ativo} (timeframe {timeframe}s)...")
    velas = iq.get_candles(ativo, timeframe, quantidade, time.time())

    if not velas:
        print("Nenhuma vela retornada. Verifique o ativo e o mercado.")
        return []

    print(f"{len(velas)} velas recebidas.\n")
    return velas


def mostrar_candles_no_console(velas):
    print("Ultimas 10 velas:")
    print("-" * 70)
    for v in velas[-10:]:
        hora = datetime.fromtimestamp(v["from"]).strftime("%H:%M:%S")
        print(
            f"{hora} | Abertura: {v['open']:.5f} | Fechamento: {v['close']:.5f} "
            f"| Maxima: {v['max']:.5f} | Minima: {v['min']:.5f} "
            f"| Volume: {v['volume']}"
        )
    print("-" * 70)


def gerar_grafico(velas, ativo):
    try:
        import pandas as pd
        import mplfinance as mpf
    except ImportError:
        print("\n(mplfinance/pandas nao instalados - grafico nao gerado.)")
        return

    dados = []
    for v in velas:
        dados.append({
            "Date": datetime.fromtimestamp(v["from"]),
            "Open": v["open"],
            "High": v["max"],
            "Low": v["min"],
            "Close": v["close"],
            "Volume": v["volume"],
        })

    df = pd.DataFrame(dados)
    df.set_index("Date", inplace=True)

    mpf.plot(df, type="candle", title=f"{ativo} - Grafico de Velas",
              style="charles", volume=True, savefig="grafico.png")
    print("\nGrafico salvo como 'grafico.png'.")


def main():
    iq = conectar_iq(EMAIL_IQ, SENHA_IQ)
    if iq is None:
        return

    iq.change_balance(TIPO_CONTA)
    print(f"Usando conta: {TIPO_CONTA}")

    velas = buscar_candles(iq, ATIVO, TIMEFRAME_SEGUNDOS, QUANTIDADE_VELAS)
    if not velas:
        return

    mostrar_candles_no_console(velas)
    gerar_grafico(velas, ATIVO)


if __name__ == "__main__":
    main()
