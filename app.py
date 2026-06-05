# -*- coding: utf-8 -*-
"""
Gerador de Relatorio de Projecao Financeira - Acumulacao + Resgate
Rode com:  streamlit run app.py
"""
import io
from datetime import date

import streamlit as st
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle, Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader

# ======================= AJUSTES VISUAIS =======================
EMPRESA     = "Vinícius Lage Investimentos"   # aparece na faixa azul superior
AZUL_ESCURO = colors.HexColor("#1F3B5C")
CINZA_LINHA = colors.HexColor("#C7D0D9")
CINZA_ZEBRA = colors.HexColor("#F2F5F8")
LARANJA     = "#E8853A"
AMARELO_XP  = "#FFD200"
VERMELHO    = "#C0504D"
AZUL_GRAF   = "#1F6FB2"
VERDE_LINHA = "#3C8C5A"
FOOTER_TEXT = EMPRESA
# ===============================================================


def brl(v, casas=2):
    s = f"{v:,.{casas}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return "R$ " + s


def pct(v):
    return f"{v*100:.2f}".replace(".", ",") + "% a.a"


# --------------------------- MOTORES ---------------------------
def projetar_acumulacao(P0, aporte_mensal, taxa_juros_aa, crescimento_aporte_aa,
                        inflacao_aa, anos):
    """Fase de acumulacao (mesma logica da planilha / app antigo).
    Aporte no inicio do mes, reajustado 1x por ano. 1o mes = patrimonio parado.
    Retorna arrays indexados por mes 0..anos*12."""
    r_m = (1 + taxa_juros_aa) ** (1 / 12) - 1
    M = int(round(anos * 12))
    R = [0.0, aporte_mensal]
    for m in range(2, M + 1):
        R.append(R[m - 1] * (1 + crescimento_aporte_aa) if (m - 1) % 12 == 0 else R[m - 1])
    V = [P0, P0]
    for m in range(2, M + 1):
        V.append((V[m - 1] + R[m]) * (1 + r_m))
    nominal = [V[m] for m in range(M + 1)]
    real = [P0] + [V[m] / (1 + inflacao_aa) ** (m / 12) for m in range(1, M + 1)]
    invest = [P0] + [P0 + sum(R[1:m + 1]) for m in range(1, M + 1)]
    aporte = [0.0] + [R[m] for m in range(1, M + 1)]
    return nominal, real, invest, aporte


def projetar_resgate(P_start, resgate_mensal, taxa_juros_aa, reajuste_resgate_aa,
                     inflacao_aa, anos, meses_offset, modo_real=True):
    """Fase de resgate. Retira no inicio do mes e o que sobra rende.
    Piso em zero (nunca negativo). Reajuste anual do resgate.
    'meses_offset' = meses ja decorridos na acumulacao, usado para deflacionar
    o patrimonio real de forma continua desde o inicio.
    Indice 0 = ponto de virada (= patrimonio final da acumulacao).

    modo_real=True: 'resgate_mensal' esta em valores de hoje. O programa infla
      esse valor ate o ano do resgate (mantem poder de compra). Nesse modo,
      'reajuste_resgate_aa' representa crescimento REAL anual da renda (0 = constante).
    modo_real=False: 'resgate_mensal' e um valor nominal fixo, reajustado por
      'reajuste_resgate_aa' ao ano (comportamento nominal puro)."""
    r_m = (1 + taxa_juros_aa) ** (1 / 12) - 1
    M = int(round(anos * 12))
    anos_acum = meses_offset / 12
    # resgate planejado por mes (m=1..M); reajuste a cada 12 meses (mes 13, 25, ...)
    R = [0.0]
    for m in range(1, M + 1):
        j = (m - 1) // 12  # ano (0-based) dentro da fase de resgate
        if modo_real:
            R.append(resgate_mensal * (1 + inflacao_aa) ** (anos_acum + j)
                     * (1 + reajuste_resgate_aa) ** j)
        else:
            R.append(resgate_mensal * (1 + reajuste_resgate_aa) ** j)
    V = [P_start, P_start]   # 1o mes parado (igual a planilha/relatorio de resgates)
    resg = [0.0, 0.0]        # nada retirado no 1o mes
    for m in range(2, M + 1):
        saldo = V[m - 1]
        retira = min(saldo, R[m])   # nao retira mais do que existe
        resg.append(retira)
        V.append(max((saldo - R[m]) * (1 + r_m), 0.0))
    nominal = [V[m] for m in range(M + 1)]
    real = [V[m] / (1 + inflacao_aa) ** ((meses_offset + m) / 12) for m in range(M + 1)]
    return nominal, real, resg


def mes_esgotamento(res_nominal):
    """Retorna o indice de mes (1-based no resgate) em que zerou, ou None."""
    for k in range(1, len(res_nominal)):
        if res_nominal[k] <= 0:
            return k
    return None


def duracao_carteira(P_start, resgate_mensal, taxa_juros_aa, reajuste_resgate_aa,
                     inflacao_aa, anos_resg, meses_offset, modo_real,
                     horizonte_stress=40):
    """Estima por quanto tempo a carteira dura na fase de resgate.
    Projeta ate H = max(40, prazo cadastrado) anos para testar sustentabilidade.
    Retorna (texto, esgota_bool, meses)."""
    H = max(horizonte_stress, anos_resg)
    rn, _, _ = projetar_resgate(P_start, resgate_mensal, taxa_juros_aa,
                                reajuste_resgate_aa, inflacao_aa, H, meses_offset, modo_real)
    esg = mes_esgotamento(rn)
    if esg is None:
        return f"Não esgota em {H} anos (resgate sustentável)", False, None
    anos = esg / 12
    texto = f"{anos:.1f} anos  ({esg} meses)".replace(".", ",")
    return texto, True, esg



# --------------------------- GRAFICO ---------------------------
def gerar_grafico(anos_acum, anos_resg, acc_nom, acc_real, res_nom, res_real):
    total = anos_acum + anos_resg
    anos = list(range(0, total + 1))

    nom, rea = [], []
    for a in anos:
        if a <= anos_acum:
            nom.append(acc_nom[a * 12])
            rea.append(acc_real[a * 12])
        else:
            k = (a - anos_acum) * 12
            nom.append(res_nom[k])
            rea.append(res_real[k])

    fig, ax = plt.subplots(figsize=(12.6, 4.6), dpi=160)
    ax.plot(anos, nom, color=AZUL_GRAF, lw=2.6, label="Patrimônio Nominal")
    ax.plot(anos, rea, color=LARANJA, lw=2.6, label="Patrimônio Real")

    # linha vertical na virada (inicio dos resgates)
    ax.axvline(anos_acum, color=VERDE_LINHA, lw=1.6, ls="--", alpha=0.9)
    topo = max(max(nom), max(rea))
    ax.annotate("Início dos resgates", (anos_acum, topo), textcoords="offset points",
                xytext=(6, -4), fontsize=8.5, color=VERDE_LINHA, va="top")

    # rotulos: virada e fim de cada serie
    pares = [(anos_acum, acc_nom[anos_acum * 12], AZUL_GRAF),
             (total, nom[-1], AZUL_GRAF),
             (total, rea[-1], LARANJA)]
    for x, val, cor in pares:
        ax.annotate(brl(val, 0), (x, val), textcoords="offset points",
                    xytext=(6, 0), va="center", fontsize=8, color=cor,
                    bbox=dict(boxstyle="round,pad=0.25", fc="white", ec=cor, lw=0.8))

    ax.set_xlabel("Ano", fontsize=9)
    ax.set_ylabel("Valor (R$)", fontsize=9)
    ax.set_xlim(0, total)
    if total <= 20:
        passo = 1
    elif total <= 40:
        passo = 2
    else:
        passo = 5
    ax.set_xticks(list(range(0, total + 1, passo)))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda x, _: f"{x:,.0f}".replace(",", ".")))
    ax.grid(True, color="#E3E8ED", lw=0.7)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    ax.legend(loc="upper left", fontsize=8.5, frameon=False, ncol=2,
              bbox_to_anchor=(0.0, -0.13))
    ax.margins(x=0.12)
    fig.tight_layout()
    return fig


MESES_PT = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
            "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


# --------------------------- FAIXAS ----------------------------
def desenhar_faixas(c, W, H, nome_cliente):
    barra_h = 60
    c.setFillColor(AZUL_ESCURO)
    c.rect(0, H - barra_h, W, barra_h, fill=1, stroke=0)
    c.setFillColor(colors.HexColor(AMARELO_XP))
    c.rect(0, H - barra_h - 3, W, 3, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(22, H - 22, EMPRESA.upper())
    c.setFont("Helvetica-Oblique", 8.5)
    c.drawRightString(W - 22, H - 22, f"Data do Relatório: {date.today().strftime('%d/%m/%Y')}")
    c.setFont("Helvetica-Bold", 16)
    c.drawCentredString(W / 2, H - 46, f"Relatório de Projeção Financeira  -  {nome_cliente}")
    c.setFillColor(colors.HexColor(AMARELO_XP))
    c.rect(0, 26, W, 3, fill=1, stroke=0)
    c.setFillColor(AZUL_ESCURO)
    c.rect(0, 0, W, 26, fill=1, stroke=0)
    if FOOTER_TEXT:
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Oblique", 9)
        c.drawCentredString(W / 2, 9, FOOTER_TEXT)
    c.setFillColor(colors.black)
    return H - barra_h - 22


# ----------------------- TABELAS MENSAIS -----------------------
def montar_tabela_mensal(linhas_dados, cabecalho_txt, larguras, fase):
    """linhas_dados: lista de listas [ano, mes, c2, c3, c4, c5] (ano como str)."""
    estilo_cab = ParagraphStyle("cab", fontName="Helvetica-Bold", fontSize=6.6,
                                textColor=colors.white, alignment=1, leading=8)
    cabecalho = [Paragraph(t, estilo_cab) for t in cabecalho_txt]
    dados = [cabecalho] + linhas_dados
    tm = Table(dados, colWidths=larguras, repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), AZUL_ESCURO),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 6.8),
        ("LEADING", (0, 0), (-1, -1), 8),
        ("ALIGN", (0, 0), (1, -1), "CENTER"),
        ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.4, CINZA_LINHA),
        ("ROWBACKGROUNDS", (1, 1), (-1, -1), [colors.white, CINZA_ZEBRA]),
        ("BACKGROUND", (0, 1), (0, -1), colors.white),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("TOPPADDING", (0, 0), (-1, -1), 1.1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1.1),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]
    # mescla celulas da coluna Ano (blocos do mesmo ano)
    i = 1
    n = len(linhas_dados)
    while i <= n:
        ano_atual = linhas_dados[i - 1][0]
        j = i
        while j < n and linhas_dados[j][0] == ano_atual:
            j += 1
        if j > i:
            estilo.append(("SPAN", (0, i), (0, j)))
        i = j + 1
    tm.setStyle(TableStyle(estilo))
    return tm


def linhas_fase(nominal, real, fluxo, total_inicial, P_ref, eh_acumulacao):
    """Gera as linhas (sem cabecalho) de uma fase inteira, mes a mes."""
    M = len(nominal) - 1
    linhas = []
    acumulado = 0.0
    for m in range(1, M + 1):
        ano = (m - 1) // 12 + 1
        mes = MESES_PT[(m - 1) % 12]
        acumulado += fluxo[m]
        linhas.append([
            str(ano), mes,
            brl(fluxo[m]),
            brl(acumulado),
            brl(nominal[m]),
            brl(real[m]),
        ])
    return linhas


def desenhar_paginas_mensais(c, W, H, nome_cliente, titulo_fase, linhas, cabecalho, larguras, fase):
    """Distribui as linhas em paginas, duas colunas (esquerda/direita) por pagina."""
    por_coluna = 30
    por_pagina = por_coluna * 2
    larg_total = sum(larguras)
    gap = 26
    x_esq = (W - (larg_total * 2 + gap)) / 2
    x_dir = x_esq + larg_total + gap

    n = len(linhas)
    inicio = 0
    while inicio < n:
        c.showPage()
        yb = desenhar_faixas(c, W, H, nome_cliente)
        bloco = linhas[inicio:inicio + por_pagina]
        ano_ini = bloco[0][0]
        ano_fim = bloco[-1][0]
        c.setFont("Helvetica-Bold", 12)
        c.drawCentredString(W / 2, yb - 2, f"{titulo_fase}  -  Anos {ano_ini} a {ano_fim}")
        ytop = yb - 18

        esq = bloco[:por_coluna]
        dir_ = bloco[por_coluna:]
        t_esq = montar_tabela_mensal(esq, cabecalho, larguras, fase)
        tw, th = t_esq.wrapOn(c, W, H)
        t_esq.drawOn(c, x_esq, ytop - th)
        if dir_:
            t_dir = montar_tabela_mensal(dir_, cabecalho, larguras, fase)
            tw2, th2 = t_dir.wrapOn(c, W, H)
            t_dir.drawOn(c, x_dir, ytop - th2)
        inicio += por_pagina


# --------------------------- PDF -------------------------------
def gerar_pdf_bytes(nome_cliente, P0, aporte_mensal, crescimento_aporte_aa,
                    anos_acum, resgate_mensal, reajuste_resgate_aa, anos_resg,
                    taxa_juros_aa, inflacao_aa, modo_real=True):
    M_acum = int(round(anos_acum * 12))
    acc_nom, acc_real, acc_inv, acc_aporte = projetar_acumulacao(
        P0, aporte_mensal, taxa_juros_aa, crescimento_aporte_aa, inflacao_aa, anos_acum)
    P_start = acc_nom[M_acum]

    res_nom, res_real, res_resg = projetar_resgate(
        P_start, resgate_mensal, taxa_juros_aa, reajuste_resgate_aa,
        inflacao_aa, anos_resg, M_acum, modo_real)

    buf_pdf = io.BytesIO()
    W, H = landscape(A4)
    c = canvas.Canvas(buf_pdf, pagesize=landscape(A4))

    # ---------------- PAGINA 1 ----------------
    y = desenhar_faixas(c, W, H, nome_cliente)

    # --- tabela de parametros (esquerda) ---
    sep = lambda txt: [txt, ""]
    if modo_real:
        label_resgate = "Renda mensal (valores de hoje)"
        label_reajuste = "Crescimento real anual da renda"
    else:
        label_resgate = "Resgate mensal (nominal)"
        label_reajuste = "Reajuste nominal anual dos resgates"
    dados_param = [
        ["Descrição", "Valor"],
        ["ACUMULAÇÃO", ""],
        ["Patrimônio atual", brl(P0, 0)],
        ["Aporte mensal", brl(aporte_mensal, 0)],
        ["Crescimento anual dos aportes", pct(crescimento_aporte_aa)],
        ["Anos de acumulação", str(anos_acum)],
        ["RESGATE", ""],
        [label_resgate, brl(resgate_mensal, 0)],
        [label_reajuste, pct(reajuste_resgate_aa)],
        ["Anos de resgate", str(anos_resg)],
        ["GERAIS", ""],
        ["Taxa de juros anual", pct(taxa_juros_aa)],
        ["Inflação anual", pct(inflacao_aa)],
    ]
    tp = Table(dados_param, colWidths=[200, 130])
    estilo_p = [
        ("BACKGROUND", (0, 0), (-1, 0), AZUL_ESCURO),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("ALIGN", (1, 1), (1, -1), "CENTER"),
        ("GRID", (0, 0), (-1, -1), 0.5, CINZA_LINHA),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
    ]
    for ri, linha in enumerate(dados_param):
        if linha[1] == "" and linha[0] in ("ACUMULAÇÃO", "RESGATE", "GERAIS"):
            estilo_p += [
                ("SPAN", (0, ri), (-1, ri)),
                ("BACKGROUND", (0, ri), (-1, ri), colors.HexColor("#E7ECF1")),
                ("FONTNAME", (0, ri), (-1, ri), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, ri), (-1, ri), AZUL_ESCURO),
                ("ALIGN", (0, ri), (-1, ri), "LEFT"),
            ]
    tp.setStyle(TableStyle(estilo_p))

    # --- tabela de resultados (direita) ---
    linhas_res = [["", "Nominal", "Real"]]
    linhas_res.append([f"Na aposentadoria (ano {anos_acum})",
                       brl(acc_nom[M_acum]), brl(acc_real[M_acum])])
    marcos = [m for m in range(5, anos_resg + 1, 5)]
    if anos_resg not in marcos:
        marcos.append(anos_resg)
    for mk in marcos:
        linhas_res.append([f"Após {mk} anos de resgate",
                           brl(res_nom[mk * 12]), brl(res_real[mk * 12])])
    tr = Table(linhas_res, colWidths=[150, 105, 105])
    estilo_r = [
        ("FONTNAME", (1, 0), (-1, 0), "Helvetica-Bold"),
        ("TEXTCOLOR", (1, 0), (-1, 0), AZUL_ESCURO),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (1, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LINEBELOW", (1, 0), (-1, 0), 1.0, AZUL_ESCURO),
        ("GRID", (1, 1), (-1, -1), 0.5, CINZA_LINHA),
        ("ROWBACKGROUNDS", (1, 1), (-1, -1), [colors.white, CINZA_ZEBRA]),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
        ("LEFTPADDING", (0, 0), (0, -1), 9),
        ("RIGHTPADDING", (1, 0), (-1, -1), 9),
    ]
    for i in range(1, len(linhas_res)):
        estilo_r += [
            ("BACKGROUND", (0, i), (0, i), AZUL_ESCURO),
            ("TEXTCOLOR", (0, i), (0, i), colors.white),
            ("FONTNAME", (0, i), (0, i), "Helvetica-Bold"),
        ]
    tr.setStyle(TableStyle(estilo_r))

    # posiciona as duas tabelas lado a lado
    margem = 26
    x_res = W - margem - 360
    c.setFont("Helvetica-Bold", 11)
    c.drawString(margem + 70, y - 2, "Parâmetros de Entrada")
    c.drawString(x_res, y - 2, "Resultados")
    y_tab = y - 14
    twp, thp = tp.wrapOn(c, W, H)
    tp.drawOn(c, margem, y_tab - thp)
    twr, thr = tr.wrapOn(c, W, H)
    tr.drawOn(c, x_res, y_tab - thr)

    # --- caixa "Duracao estimada da carteira" (abaixo de Resultados, lado direito) ---
    txt_dur, esgota, meses_dur = duracao_carteira(
        P_start, resgate_mensal, taxa_juros_aa, reajuste_resgate_aa,
        inflacao_aa, anos_resg, M_acum, modo_real)
    box_w, box_h = 360, 42
    box_x = x_res
    box_top = y_tab - thr - 14
    c.setFillColor(colors.HexColor("#EAF1F8"))
    c.setStrokeColor(AZUL_ESCURO)
    c.setLineWidth(1.0)
    c.roundRect(box_x, box_top - box_h, box_w, box_h, 6, stroke=1, fill=1)
    c.setFillColor(AZUL_ESCURO)
    c.setFont("Helvetica-Bold", 10.5)
    c.drawCentredString(box_x + box_w / 2, box_top - 15, "Duração estimada da carteira")
    c.setFillColor(colors.HexColor("#B05A1F"))
    c.setFont("Helvetica-Bold", 11.5)
    c.drawCentredString(box_x + box_w / 2, box_top - 31, txt_dur)
    c.setFillColor(colors.black)

    # conteudo abaixo comeca no mais baixo entre as duas colunas (esq: params, dir: box)
    bottom_esq = y_tab - thp
    bottom_dir = box_top - box_h
    y_nota = min(bottom_esq, bottom_dir) - 12

    # no modo real, traduz a renda de hoje para o nominal no inicio e fim do resgate
    if modo_real:
        nom_ini = resgate_mensal * (1 + inflacao_aa) ** anos_acum
        nom_fim = resgate_mensal * (1 + inflacao_aa) ** (anos_acum + anos_resg - 1) \
            * (1 + reajuste_resgate_aa) ** (anos_resg - 1)
        c.setFillColor(AZUL_ESCURO)
        c.setFont("Helvetica-Oblique", 8.5)
        c.drawCentredString(W / 2, y_nota,
                            f"Renda nominal pretendida: {brl(nom_ini, 0)}/mes no 1o ano e "
                            f"{brl(nom_fim, 0)}/mes no ultimo, mantendo o poder de compra de "
                            f"{brl(resgate_mensal, 0)} de hoje.")
        c.setFillColor(colors.black)
        y_nota -= 10
    fig = gerar_grafico(anos_acum, anos_resg, acc_nom, acc_real, res_nom, res_real)
    buf_img = io.BytesIO()
    fig.savefig(buf_img, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf_img.seek(0)
    img = ImageReader(buf_img)
    iw, ih = img.getSize()
    disp_w = W - 2 * margem
    disp_h = disp_w * ih / iw
    topo_graf = y_nota - 14
    if disp_h > topo_graf - 40:
        disp_h = topo_graf - 40
        disp_w = disp_h * iw / ih
    c.drawImage(img, (W - disp_w) / 2, topo_graf - disp_h, width=disp_w, height=disp_h,
                preserveAspectRatio=True, mask="auto")

    # ---------------- PAGINAS MENSAIS ----------------
    larguras = [26, 30, 80, 86, 92, 86]
    cab_acum = ["Ano", "Mês", "Aporte Mensal", "Total Aportado",
                "Patrimônio Nominal", "Patrimônio Real"]
    cab_resg = ["Ano", "Mês", "Resgate Mensal", "Total Resgatado",
                "Patrimônio Nominal", "Patrimônio Real"]

    linhas_acum = linhas_fase(acc_nom, acc_real, acc_aporte, 0.0, P0, True)
    desenhar_paginas_mensais(c, W, H, nome_cliente, "Evolução Mensal - Acumulação",
                             linhas_acum, cab_acum, larguras, "acum")

    linhas_resg = linhas_fase(res_nom, res_real, res_resg, 0.0, P_start, False)
    desenhar_paginas_mensais(c, W, H, nome_cliente, "Evolução Mensal - Resgates",
                             linhas_resg, cab_resg, larguras, "resg")

    c.save()
    buf_pdf.seek(0)
    return buf_pdf.getvalue()


# ============================ INTERFACE ============================
st.set_page_config(page_title="Projecao Financeira - Acumulacao e Resgate", layout="wide")
st.title("Gerador de Relatorio de Projecao Financeira")
st.caption("Fase de acumulacao (aportes) + fase de resgate (retiradas)")

with st.sidebar:
    st.header("Cliente")
    nome = st.text_input("Nome do cliente", value="", placeholder="Nome do cliente")

    st.header("Acumulacao")
    P0 = st.number_input("Patrimonio atual (R$)", min_value=0.0, value=None, step=1000.0, placeholder="0")
    aporte = st.number_input("Aporte mensal (R$)", min_value=0.0, value=None, step=100.0, placeholder="0")
    cresc_in = st.number_input("Crescimento anual dos aportes (%)", value=3.0, step=0.5)
    anos_acum = st.number_input("Anos de acumulacao", min_value=1, max_value=70, value=None, step=1, placeholder="0")

    st.header("Resgate")
    modo_opcao = st.radio(
        "Definir o resgate em",
        ["Valores de hoje (real)", "Valor nominal"],
        help="Valores de hoje: voce digita a renda no poder de compra de hoje e o "
             "programa infla ate o ano do resgate. Valor nominal: valor fixo lancado "
             "diretamente no futuro.",
    )
    modo_real = modo_opcao.startswith("Valores de hoje")
    if modo_real:
        label_resgate = "Renda mensal desejada hoje (R$)"
        label_reaj = "Crescimento real anual da renda (%)"
    else:
        label_resgate = "Resgate mensal nominal (R$)"
        label_reaj = "Reajuste nominal anual (%)"
    resgate = st.number_input(label_resgate, min_value=0.0, value=None, step=100.0, placeholder="0")
    reaj_in = st.number_input(label_reaj, value=0.0, step=0.5)
    anos_resg = st.number_input("Anos de resgate", min_value=1, max_value=70, value=None, step=1, placeholder="0")

    st.header("Gerais")
    juros_in = st.number_input("Taxa de juros anual (%)", value=12.0, step=0.5)
    inflacao_in = st.number_input("Inflacao anual (%)", value=6.0, step=0.5)

obrigatorios = [P0, aporte, anos_acum, resgate, anos_resg, juros_in, cresc_in, reaj_in, inflacao_in]
if any(v is None for v in obrigatorios):
    st.info("Preencha as premissas na barra lateral para gerar a projecao e o PDF.")
    st.stop()

juros = juros_in / 100
cresc = cresc_in / 100
reaj = reaj_in / 100
inflacao = inflacao_in / 100
anos_acum = int(anos_acum)
anos_resg = int(anos_resg)

M_acum = anos_acum * 12
acc_nom, acc_real, acc_inv, acc_aporte = projetar_acumulacao(P0, aporte, juros, cresc, inflacao, anos_acum)
P_start = acc_nom[M_acum]
res_nom, res_real, res_resg = projetar_resgate(P_start, resgate, juros, reaj, inflacao, anos_resg, M_acum, modo_real)

col1, col2, col3 = st.columns(3)
col1.metric("Patrimonio na aposentadoria", brl(acc_nom[M_acum]))
col2.metric("Patrimonio ao fim dos resgates", brl(res_nom[-1]))
col3.metric("Patrimonio real ao fim", brl(res_real[-1]))

if modo_real:
    nom_ini = resgate * (1 + inflacao) ** anos_acum
    nom_fim = resgate * (1 + inflacao) ** (anos_acum + anos_resg - 1) * (1 + reaj) ** (anos_resg - 1)
    st.caption(
        f"Renda nominal pretendida: {brl(nom_ini, 0)}/mes no 1o ano de resgate e "
        f"{brl(nom_fim, 0)}/mes no ultimo ano, mantendo o poder de compra de "
        f"{brl(resgate, 0)} de hoje."
    )

txt_dur, esgota, meses_dur = duracao_carteira(P_start, resgate, juros, reaj, inflacao, anos_resg, M_acum, modo_real)
if esgota:
    st.error(f"Duracao estimada da carteira: {txt_dur}")
else:
    st.success(f"Duracao estimada da carteira: {txt_dur}")

st.pyplot(gerar_grafico(anos_acum, anos_resg, acc_nom, acc_real, res_nom, res_real))

nome_pdf = nome.strip() if nome.strip() else "Cliente"
pdf_bytes = gerar_pdf_bytes(nome_pdf, P0, aporte, cresc, anos_acum, resgate, reaj, anos_resg, juros, inflacao, modo_real)
st.download_button(
    "Baixar relatorio em PDF",
    data=pdf_bytes,
    file_name=f"Relatorio_Projecao_{nome_pdf}.pdf",
    mime="application/pdf",
)
