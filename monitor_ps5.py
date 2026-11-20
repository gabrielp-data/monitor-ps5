"""
Monitor de ofertas de consoles PS5.

Fontes:
  - KaBuM!      (só vendido e entregue por KaBuM!)
  - Amazon      (busca com filtro de vendedor Amazon.com.br)
  - Magalu      (pelo comparador Zoom, quando a Magalu tem o menor preço)
  - Pelando e canais do Telegram (ofertas postadas pela comunidade)

Como usar:
  py monitor_ps5.py            -> checa uma vez e mostra os preços
  py monitor_ps5.py --loop 60  -> fica checando a cada 60 minutos
  py monitor_ps5.py --telegram -> liga o Telegram (só precisa uma vez)

Os alertas usam o teto PRECO_ALVO. Veja as regras no LEIA-ME.txt.
"""

import csv
import json
import math
import unicodedata
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# ===================== CONFIGURE AQUI =====================
PRECO_ALVO = 4000.00        # avisa quando algum PS5 ficar igual ou abaixo disso (R$)
PRECO_SUPER = 3000.00       # abaixo disso: aviso especial de PROMOÇÃO 🚨
PRECO_REVISAR = 2500        # preço incomum: conferir, sem esconder a oferta
QUEDA_MINIMA = 100          # reais de queda para repetir um aviso dentro do alvo
PRECO_GTA_VI = None         # opcional: preço do jogo comprado separadamente
INCLUIR_ALIEXPRESS = False  # preço da variante, moeda e tributos a confirmar
LOJAS_ZOOM = ["Magazine Luiza"]  # lojas aceitas vindas do comparador Zoom
# ==========================================================

PASTA = Path(__file__).resolve().parent
HISTORICO = PASTA / "historico.csv"
CONFIG = PASTA / "config.json"
FILA = PASTA / "alertas_pendentes.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

def baixar(url, tentativas=3):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"})
    for n in range(tentativas):
        try:
            with urllib.request.urlopen(req, timeout=12) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception:
            if n == tentativas - 1:
                raise
            time.sleep(2 ** n)


def normalizar(texto):
    texto = texto.casefold().replace("®", " ")
    return "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))


PS5 = re.compile(r"\b(?:ps\s*5|play\s*station\s*5)\b")
ACESSORIO = re.compile(r"\b(?:controles?|controllers?|dualsense|jogos?|capas?|tampas?|"
                       r"carregador|suporte|base|cabos?|headset|fone|case|skin|adesivo|"
                       r"portal|vr\s*2|leitor de disco|drive de disco|gift card|volante)\b")


def parece_console(titulo, preco):
    if not isinstance(preco, (int, float)) or not math.isfinite(preco) or preco <= 0:
        return False
    t = normalizar(titulo)
    if not PS5.search(t) or re.search(r"\b(?:para|for)\s+(?:(?:o|console)\s+)*" + PS5.pattern, t):
        return False
    if re.search(r"\b(?:sorteio|aluguel|caixa vazia|sem console|somente caixa|apenas a caixa)\b", t):
        return False
    if re.search(r"^\W*(?:ssd|carcaca|adaptador|cooler|ventoinha|fonte|placa|midia|conta)\b", t):
        return False
    partes = re.split(r"\s*\+\s*|\s+(?:com|acompanha|inclui)\s+", t)
    principal = partes[0]
    if not PS5.search(principal):
        # Também há anúncios no formato "Bundle GTA VI + PlayStation 5 Slim".
        return (bool(re.match(r"\s*(?:bundle|kit|pacote)\b", principal))
                and any(PS5.search(p) and not ACESSORIO.search(p) for p in partes[1:]))
    if re.search(r"\b(?:gta|grand theft auto)\b", principal) and not re.search(r"\b(?:console|bundle|kit|pacote|slim)\b", principal):
        return False
    hardware = (re.search(r"\b(?:console|slim|edicao digital|\d+\s*(?:gb|tb))\b", principal)
                or re.match(r"^\W*(?:sony\s+)?" + PS5.pattern + r"\s+(?:pro|digital|disc|disk)\b", principal))
    so_modelo = PS5.fullmatch(principal.strip(" 🎮🔥-!"))
    return bool(hardware or so_modelo) and not ACESSORIO.search(principal)


def categoria(titulo):
    t = normalizar(titulo)
    modelo = "PS5 Pro" if re.search(PS5.pattern + r"\s+pro\b", t) else "PS5 Slim" if "slim" in t else "PS5"
    if "digital" in t:
        return modelo + " digital"
    if re.search(r"\b(?:disc|disk|disco|leitor|fisic[ao])\b", t):
        return modelo + " com leitor"
    return modelo + " (edição a confirmar)"


def kabum():
    url = ("https://servicespub.prod.api.aws.grupokabum.com.br/catalog/v2/products?"
           + urllib.parse.urlencode({"query": "console playstation 5", "page_size": 40}))
    dados = json.loads(baixar(url))
    itens = []
    for p in dados.get("data", []):
        a = p["attributes"]
        if a.get("is_marketplace") not in (False, 0) or not a.get("available"):
            continue  # pula vendedor terceiro e produto sem estoque
        preco = a.get("price_with_discount") or a.get("price")
        oferta = a.get("offer")
        if oferta and oferta.get("price_with_discount"):
            preco = min(preco or oferta["price_with_discount"], oferta["price_with_discount"])
        if not parece_console(a["title"], preco):
            continue
        itens.append({
            "loja": "KaBuM!",
            "titulo": a["title"].strip(),
            "preco": float(preco),
            "link": f"https://www.kabum.com.br/produto/{p['id']}/{a.get('product_link', '')}",
            "foto": (a.get("images") or [""])[0],
        })
    return itens


def amazon():
    # p_6:A1ZZFT5FULY4LN = filtro "vendido pela Amazon.com.br"
    url = "https://www.amazon.com.br/s?k=console+playstation+5&rh=p_6%3AA1ZZFT5FULY4LN"
    html = baixar(url)
    if "s-search-result" not in html:
        raise RuntimeError("Amazon bloqueou por enquanto, tenta de novo na próxima checagem")
    itens, vistos = [], set()
    for bloco in html.split('data-asin="')[1:]:
        asin = bloco[:bloco.find('"')]
        if not asin or asin in vistos:
            continue
        t = re.search(r'<h2[^>]*aria-label="([^"]*)"', bloco)
        p = re.search(r'a-offscreen">R\$\s*([\d.]+,\d{2})', bloco)
        if not t or not p:
            continue
        titulo = t.group(1)
        if "patrocinado" in titulo.lower():
            continue
        preco = float(p.group(1).replace(".", "").replace(",", "."))
        if not parece_console(titulo, preco):
            continue
        vistos.add(asin)
        foto = re.search(r'class="s-image"[^>]*src="([^"]+)"', bloco)
        itens.append({"loja": "Amazon", "titulo": titulo.strip(), "preco": preco,
                      "link": f"https://www.amazon.com.br/dp/{asin}",
                      "foto": foto.group(1) if foto else ""})
    return itens


def magalu_via_zoom():
    # A Magalu bloqueia programas, então usamos o comparador Zoom,
    # que mostra a loja com o menor preço de cada PS5.
    html = baixar("https://www.zoom.com.br/search?q=playstation%205")
    itens = []
    if 'data-testid="product-card::card"' not in html:
        raise RuntimeError("Zoom não retornou os cartões esperados")
    for card in html.split('data-testid="product-card::card"')[1:]:
        card = card[card.find(">") + 1:][:5000]
        textos = [t.strip() for t in re.sub(r"<[^>]+>", "\n", card).split("\n") if t.strip()]
        loja = next((t[4:] for t in textos if t.startswith("Via ")), "")
        preco_txt = next((t for t in textos if t.startswith("R$")), None)
        link = re.search(r'href="(/console-de-video-game/[^"?]+)', card)
        if not textos or not preco_txt or not link:
            continue
        if not any(l.lower() in loja.lower() for l in LOJAS_ZOOM):
            continue
        preco = float(re.sub(r"[^\d,]", "", preco_txt).replace(",", "."))
        titulo = textos[0]
        if not parece_console(titulo, preco):
            continue
        itens.append({"loja": "Magalu" if "magazine" in loja.lower() else loja,
                      "titulo": titulo, "preco": preco,
                      "link": "https://www.zoom.com.br" + link.group(1),
                      "foto": (re.findall(r'<img[^>]*src="(https://image[^"]+)"', card) or [""])[0]})
    return itens


# Regras de confiança do AliExpress (a Sony não tem loja oficial lá):
ALI_NOTA_MINIMA = 4.7        # estrelas
ALI_VENDAS_MINIMAS = 50      # quantos já venderam
ALI_PRECO_MINIMO = 3300      # PS5 novo mais barato que isso no Ali = quase sempre golpe


def aliexpress():
    """Só passa anúncio Choice (AliExpress garante entrega/devolução) OU
    com nota alta E muitas vendas. Preço baixo demais é descartado."""
    html = baixar("https://pt.aliexpress.com/w/wholesale-playstation-5-console.html")
    itens = []
    for bloco in html.split('"productId":"')[1:]:
        pid = bloco[:bloco.find('"')]
        bloco = bloco[:6000]
        t = re.search(r'"displayTitle":"([^"]*)', bloco)
        p = re.search(r'"minPrice":([\d.]+)', bloco)
        if not t or not p:
            continue
        titulo, preco = t.group(1), float(p.group(1))
        if preco < ALI_PRECO_MINIMO or not parece_console(titulo, preco):
            continue
        nota = re.search(r'"starRating":([\d.]+)', bloco)
        nota = float(nota.group(1)) if nota else 0
        vendas = re.search(r'"tradeDesc":"([\d.,]+)\+?\s', bloco)
        vendas = int(re.sub(r"\D", "", vendas.group(1))) if vendas else 0
        choice = '"isChoice":"true"' in bloco or '"isChoice":true' in bloco
        confiavel = choice or (nota >= ALI_NOTA_MINIMA and vendas >= ALI_VENDAS_MINIMAS)
        if not confiavel:
            continue
        selo = "Choice" if choice else f"nota {nota}, {vendas} vendidos"
        itens.append({"loja": "AliExpress", "titulo": f"[{selo}] " + titulo,
                      "preco": preco, "link": f"https://pt.aliexpress.com/item/{pid}.html",
                      "foto": ("https:" + f.group(1)) if (f := re.search(r'"imgUrl":"([^"]+)"', bloco)) else ""})
    return itens


CANAIS_TELEGRAM = ["LaPromotion"]   # canais públicos de oferta (o nome que vem depois de t.me/)
TG_VISTOS = PASTA / "telegram_vistos.txt"


def canais_telegram():
    """Lê a página pública dos canais (t.me/s/...) e pega posts de console PS5 das últimas 6 horas."""
    import html as H
    from datetime import timezone, timedelta
    vistos = set(TG_VISTOS.read_text().split()) if TG_VISTOS.exists() else set()
    limite = datetime.now(timezone.utc) - timedelta(hours=6)
    novos = []
    for canal in CANAIS_TELEGRAM:
        pagina = baixar(f"https://t.me/s/{canal}?q=ps5")
        for p in pagina.split('class="tgme_widget_message_wrap')[1:]:
            post = re.search(r'data-post="([^"]+)"', p)
            texto = re.search(r'tgme_widget_message_text[^>]*>(.*?)</div>', p, re.S)
            quando = re.search(r'datetime="([^"]+)"', p)
            if not post or not texto or not quando:
                continue
            if post.group(1) in vistos or datetime.fromisoformat(quando.group(1)) < limite:
                continue
            texto = re.sub(r"<br\s*/?>", "\n", texto.group(1))
            texto = H.unescape(re.sub(r"<[^>]+>", "", texto)).strip()
            primeira = texto.split("\n")[0]
            preco = preco_do_post(texto)
            if not parece_console(primeira, preco) or preco > PRECO_ALVO:
                continue
            foto = re.search(r"background-image:url\('([^']+)'\)", p.split("tgme_widget_message_photo_wrap", 1)[-1]) \
                if "tgme_widget_message_photo_wrap" in p else None
            novos.append({"id": post.group(1), "canal": canal, "texto": texto, "preco": preco,
                          "foto": foto.group(1) if foto else "", "link": f"https://t.me/{post.group(1)}"})
    return novos


def preco_do_post(texto):
    padrao = r"R\$\s*(\d+(?:\.\d{3})*(?:,\d{2})?)"
    texto = re.sub(r"\b\d+\s*x\s*(?:sem juros\s*)?(?:(?:de|por)\s*)?" + padrao, "", texto, flags=re.I)
    texto = re.sub(padrao + r"\s*(?:por parcela|/\s*m[eê]s)", "", texto, flags=re.I)
    por = re.findall(r"\bpor\s*:?\s*" + padrao, texto, re.I)
    valores = por or re.findall(padrao, texto, re.I)
    if len(set(valores)) != 1:
        return None
    return float(valores[0].replace(".", "").replace(",", "."))


def avisar_canais(fila):
    try:
        novos = canais_telegram()
    except Exception as e:
        print(f"  Canais do Telegram: erro ({e})")
        return
    print(f"  Canais do Telegram: {len(novos)} post(s) novo(s) de PS5")
    for o in novos:
        linhas = [l.strip() for l in o["texto"].split("\n") if l.strip()]
        nome = re.sub(r"^[^\wÀ-ú]+", "", linhas[0]) if linhas else "PS5"
        cupom = re.search(r"[Cc]upom:\s*([A-Za-z0-9]+)", o["texto"])
        cupom = cupom.group(1) if cupom else ""
        link = re.search(r"https?://\S+", o["texto"])
        link = link.group(0) if link else o["link"]
        lojas = {"meli.la": "MercadoLivre", "mercadolivre": "MercadoLivre", "amzn": "Amazon",
                 "amazon": "Amazon", "shopee": "Shopee", "magalu": "Magalu",
                 "magazineluiza": "Magalu", "kabum": "KaBuM", "vivo": "LojaVivo"}
        loja = next((v for k, v in lojas.items() if k in link), o["canal"])
        texto = montar_oferta(nome, o["preco"], loja, link, cupom,
                              promo=bool(o["preco"] and o["preco"] <= PRECO_SUPER),
                              extras=[f"Canal @{o['canal']} · vendedor a confirmar"])
        enfileirar(fila, "telegram:" + o["id"], texto, o["foto"], link, cupom)
        with open(TG_VISTOS, "a") as f:
            f.write(o["id"] + "\n")


PELANDO_VISTOS = PASTA / "pelando_vistos.txt"


def pelando():
    """Ofertas de PS5 que a comunidade posta no Pelando (as mesmas que rodam no X).
    Manda cada oferta nova UMA vez só."""
    import html as H
    pagina = H.unescape(baixar("https://www.pelando.com.br/busca/console-ps5"))
    vistos = set(PELANDO_VISTOS.read_text().split()) if PELANDO_VISTOS.exists() else set()
    inicio = re.compile(r'\{"id":\[0,"([0-9a-f-]{36})"\],"slug":\[0,"([^"]+)"\],"title":\[0,"([^"]*)"\]')
    achados = list(inicio.finditer(pagina))
    if not achados:
        raise RuntimeError("Pelando não retornou anúncios reconhecíveis")
    novas = []
    for n, m in enumerate(achados):
        fim = achados[n + 1].start() if n + 1 < len(achados) else m.end() + 8000
        bloco = pagina[m.end():fim]
        oferta_id, slug, titulo = m.groups()
        preco = re.search(r'"price":\[0,([\d.]+)\]', bloco)
        status = re.search(r'"status":\[0,"(\w+)"\]', bloco)
        if not preco or not status or status.group(1) != "active":
            continue
        preco = float(preco.group(1))
        if oferta_id in vistos or preco > PRECO_ALVO or not parece_console(titulo, preco):
            continue
        loja = re.search(r'"store":\[0,\{"id":\[0,"\d+"\],"name":\[0,"([^"]+)"', bloco)
        foto = re.search(r'(https://media\.pelando\.com\.br/[^"]*/0x300/[^"]*)', bloco)
        link_loja = re.search(r'"sourceUrl":\[0,"([^"]+)"', bloco)
        novas.append({
            "id": oferta_id, "titulo": titulo, "preco": preco,
            "loja": loja.group(1) if loja else "?",
            "foto": foto.group(1) if foto else "",
            "link": link_loja.group(1) if link_loja else "",
            "pelando": f"https://www.pelando.com.br/d/{slug}",
        })
    return novas


def avisar_pelando(fila):
    try:
        novas = pelando()
    except Exception as e:
        print(f"  Pelando: erro ({e})")
        return
    print(f"  Pelando: {len(novas)} oferta(s) nova(s) de console")
    for o in novas:
        link = o["link"] or o["pelando"]
        texto = montar_oferta(o["titulo"], o["preco"], o["loja"], link,
                              promo=o["preco"] <= PRECO_SUPER,
                              extras=[f"Oferta da comunidade · vendedor a confirmar: {o['pelando']}"])
        enfileirar(fila, "pelando:" + o["id"], texto, o["foto"], link)
        with open(PELANDO_VISTOS, "a") as f:
            f.write(o["id"] + "\n")


def ultimo_preco_por_link():
    precos = {}
    if HISTORICO.exists():
        with open(HISTORICO, encoding="utf-8") as f:
            for linha in csv.DictReader(f):
                precos[linha["link"]] = float(linha["preco"])
    return precos


def salvar_historico(itens):
    novo = not HISTORICO.exists()
    agora = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(HISTORICO, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if novo:
            w.writerow(["data", "loja", "preco", "titulo", "link"])
        for i in itens:
            w.writerow([agora, i["loja"], f"{i['preco']:.2f}", i["titulo"], i["link"]])


def ler_config():
    cfg = {}
    if CONFIG.exists():
        cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    # no GitHub, o token fica guardado em "Secrets" (variáveis de ambiente)
    import os
    if os.environ.get("TELEGRAM_TOKEN"):
        cfg["telegram_token"] = os.environ["TELEGRAM_TOKEN"]
    if os.environ.get("TELEGRAM_CHAT_ID"):
        cfg["telegram_chat_id"] = os.environ["TELEGRAM_CHAT_ID"]
    return cfg


def telegram(metodo, **dados):
    token = ler_config().get("telegram_token", "")
    if not token or "COLE" in token:
        raise RuntimeError("falta colocar o token do bot no config.json")
    url = f"https://api.telegram.org/bot{token}/{metodo}"
    corpo = urllib.parse.urlencode(dados).encode()
    with urllib.request.urlopen(urllib.request.Request(url, data=corpo), timeout=30) as r:
        return json.loads(r.read().decode())


def montar_oferta(nome, preco, loja, link, cupom="", promo=False, extras=()):
    """Monta a mensagem no estilo dos canais de oferta."""
    from html import escape as esc
    tag = re.sub(r"[^\wÀ-ú]", "", loja) or "Loja"
    linhas = []
    if promo:
        linhas += [f"🚨 <b>PS5 ATÉ {brl(PRECO_SUPER)}</b>", ""]
    linhas += [f"💥 <b>{esc(nome[:150])}</b>", ""]
    if preco:
        linhas.append(f"💰 Preço: <b>{brl(preco)}</b>")
        linhas.append(categoria(nome))
        if re.search(r"\b(?:gta|grand theft auto)\s*(?:vi|6)\b", normalizar(nome)):
            linhas.append("O título menciona GTA VI. Confira o conteúdo do kit.")
        elif PRECO_GTA_VI is not None:
            linhas.append(f"Console + GTA VI à parte: {brl(preco + PRECO_GTA_VI)} antes do frete.")
        if preco < PRECO_REVISAR:
            linhas.append("Preço incomum. Confira se é o console completo e se o valor é à vista.")
        linhas.append("Confira vendedor, frete, cupom e forma de pagamento na loja.")
    linhas += [esc(e) for e in extras]
    linhas.append(f"🏬 Loja: #{tag}")
    if cupom:
        linhas.append(f"🎟 Cupom: <code>{esc(cupom)}</code>")
    linhas += ["", "🔗 PEGAR OFERTA: 👇", esc(link), "", "⚠️ Ofertas por tempo limitado."]
    return "\n".join(linhas)


def botoes(link, cupom=""):
    linha = []
    if link:
        linha.append([{"text": "🛒 PEGAR OFERTA", "url": link}])
    if cupom:
        linha.append([{"text": "📋 COPIAR CUPOM", "copy_text": {"text": cupom}}])
    return json.dumps({"inline_keyboard": linha}) if linha else ""


def enviar_foto(chat_id, foto_url, legenda, teclado=""):
    """Baixa a foto da loja e manda o arquivo pro Telegram."""
    req = urllib.request.Request(foto_url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        imagem = r.read()
    limite = "----monitorps5"
    partes = []
    campos = [("chat_id", str(chat_id)), ("caption", legenda), ("parse_mode", "HTML")]
    if teclado:
        campos.append(("reply_markup", teclado))
    for nome, valor in campos:
        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{nome}"\r\n\r\n{valor}\r\n'.encode())
    partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="photo"; filename="ps5.jpg"\r\n'
                  f'Content-Type: image/jpeg\r\n\r\n'.encode() + imagem + b"\r\n")
    partes.append(f"--{limite}--\r\n".encode())
    token = ler_config().get("telegram_token", "")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", data=b"".join(partes),
                                 headers={"Content-Type": f"multipart/form-data; boundary={limite}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def notificar(texto, foto="", link="", cupom=""):
    """Manda a mensagem pro seu Telegram (com foto e botões, se tiver)."""
    chat_id = ler_config().get("telegram_chat_id", "")
    teclado = botoes(link, cupom)
    try:
        if foto:
            try:
                enviar_foto(chat_id, foto, texto[:1000], teclado)
                print("  (aviso com foto enviado no Telegram)")
                return True
            except Exception:
                pass  # foto falhou: manda só o texto
        extra = {"reply_markup": teclado} if teclado else {}
        telegram("sendMessage", chat_id=chat_id, text=texto, parse_mode="HTML",
                 disable_web_page_preview="true", **extra)
        print("  (aviso enviado no Telegram)")
        return True
    except Exception as e:
        print(f"  Falha no Telegram ({type(e).__name__}).")
        return False


def salvar_fila(fila):
    tmp = FILA.with_suffix(".tmp")
    tmp.write_text(json.dumps(fila, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(FILA)


def enfileirar(fila, chave, texto, foto, link, cupom="", loja=None):
    fila[chave] = dict(criado=time.time(), texto=texto, foto=foto, link=link, cupom=cupom)
    if loja:
        fila[chave]["loja"] = loja
    salvar_fila(fila)


def entregar(fila):
    for chave, aviso in list(fila.items()):
        if time.time() - aviso["criado"] > 6 * 3600:
            print("  Aviso pendente expirou após seis horas.")
        elif not notificar(aviso["texto"], aviso["foto"], aviso["link"], aviso["cupom"]):
            continue
        del fila[chave]
        salvar_fila(fila)


def configurar_telegram():
    """Descobre seu chat_id (depois que você mandou /start pro bot) e manda um teste."""
    cfg = ler_config()
    resp = telegram("getUpdates")
    chats = [u["message"]["chat"]["id"] for u in resp.get("result", []) if "message" in u]
    if not chats:
        print("Não achei mensagem. Abra seu bot no Telegram, mande /start e rode de novo.")
        return
    cfg["telegram_chat_id"] = str(chats[-1])
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    if notificar("✅ Monitor de PS5 ligado! Vou te avisar aqui quando tiver promoção."):
        print("Pronto! Chat configurado.")


def brl(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def checar():
    print(f"\n=== Checando preços do PS5 — {datetime.now():%d/%m/%Y %H:%M} ===")
    itens = []
    fila = json.loads(FILA.read_text(encoding="utf-8")) if FILA.exists() else {}
    fontes = [("KaBuM!", kabum), ("Amazon", amazon), ("Magalu", magalu_via_zoom)]
    if INCLUIR_ALIEXPRESS:
        fontes.append(("AliExpress", aliexpress))
    for nome, fonte in fontes:
        try:
            achados = fonte()
            print(f"  {nome}: {len(achados)} console(s) encontrado(s)")
            itens += achados
            links = {item["link"] for item in achados}
            vencidos = [k for k, v in fila.items() if v.get("loja") == nome and v["link"] not in links]
            for chave in vencidos:
                del fila[chave]
            if vencidos:
                salvar_fila(fila)
        except Exception as e:
            print(f"  {nome}: erro ({e})")

    avisar_pelando(fila)
    avisar_canais(fila)

    if not itens:
        print("Nenhum preço encontrado agora.")
        entregar(fila)
        return

    antes = ultimo_preco_por_link()
    itens.sort(key=lambda i: i["preco"])
    melhores = {}
    print()
    for i in itens:
        marca, motivos = "", []
        anterior = antes.get(i["link"])
        chave = "loja:" + i["link"]
        atualizar = chave in fila and anterior != i["preco"]
        if atualizar and i["preco"] > PRECO_ALVO:
            del fila[chave]
            salvar_fila(fila)
        if anterior and anterior - i["preco"] >= QUEDA_MINIMA and i["preco"] <= PRECO_ALVO:
            marca = f"  ⬇ BAIXOU (era {brl(anterior)})"
            motivos.append(f"⬇ BAIXOU! Era {brl(anterior)}")
        # abaixo do alvo: avisa quando entra abaixo do alvo (não repete toda hora)
        if i["preco"] <= PRECO_ALVO and (anterior is None or anterior > PRECO_ALVO or motivos or atualizar):
            marca += "  ★ ABAIXO DO ALVO"
            motivos.append(f"★ Abaixo do seu alvo de {brl(PRECO_ALVO)}")
        super_promo = i["preco"] <= PRECO_SUPER and (anterior is None or anterior > PRECO_SUPER or motivos)
        if super_promo:
            marca += "  🚨 PROMOÇÃO"
        if super_promo or motivos:
            texto = montar_oferta(i["titulo"], i["preco"], i["loja"], i["link"],
                                  promo=super_promo, extras=motivos)
            enfileirar(fila, chave, texto, i.get("foto", ""), i["link"], loja=i["loja"])
        melhores.setdefault(categoria(i["titulo"]), i)
        print(f"{brl(i['preco']):>13}  {i['loja']:<7} {i['titulo'][:70]}{marca}")
        print(f"{'':>13}  {i['link']}")

    salvar_historico(itens)
    mais_barato = itens[0]
    print(f"\nMais barato agora: {brl(mais_barato['preco'])} na {mais_barato['loja']}")
    print(f"Seu preço-alvo: {brl(PRECO_ALVO)}")

    for modelo, item in melhores.items():
        print(f"{modelo}: {brl(item['preco'])} ({item['loja']})")
    entregar(fila)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) >= 2 and sys.argv[1] == "--telegram":
        try:
            configurar_telegram()
        except Exception as e:
            print(f"Erro: {e}. Veja o LEIA-ME.txt")
    elif len(sys.argv) >= 3 and sys.argv[1] == "--loop":
        minutos = int(sys.argv[2])
        while True:
            checar()
            print(f"\n(próxima checagem em {minutos} min — Ctrl+C para parar)")
            time.sleep(minutos * 60)
    else:
        checar()
