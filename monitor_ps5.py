"""
Monitor de preços do PS5 — só lojas confiáveis.

Fontes:
  - KaBuM!      (só vendido e entregue por KaBuM!)
  - Amazon      (só vendido e entregue por Amazon.com.br)
  - Magalu      (pelo comparador Zoom, quando a Magalu tem o menor preço)
  - AliExpress  (cuidado: lá quem vende são lojas de terceiros)

Como usar:
  py monitor_ps5.py            -> checa uma vez e mostra os preços
  py monitor_ps5.py --loop 60  -> fica checando a cada 60 minutos
  py monitor_ps5.py --telegram -> liga o Telegram (só precisa uma vez)

Se algum PS5 ficar abaixo do PRECO_ALVO, ou baixar de preço desde a última
checagem, você recebe uma mensagem no Telegram (veja LEIA-ME.txt).
"""

import csv
import json
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
PRECO_MINIMO_CONSOLE = 2500 # abaixo disso é acessório/jogo, não console
LOJAS_ZOOM = ["Magazine Luiza"]  # lojas aceitas vindas do comparador Zoom
# ==========================================================

PASTA = Path(__file__).resolve().parent
HISTORICO = PASTA / "historico.csv"
CONFIG = PASTA / "config.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

# palavras que indicam que NÃO é o console
NAO_E_CONSOLE = ["tampa", "capa", "controle", "drive de disco", "suporte", "base de",
                 "cabo", "headset", "fone", "case", "skin", "adesivo", "portal",
                 "collector", "edition -", "jogo", "vr2", "vr 2", "playstation vr"]


def baixar(url, tentativas=3):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "pt-BR,pt;q=0.9"})
    for n in range(tentativas):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", errors="replace")
        except Exception:
            if n == tentativas - 1:
                raise
            time.sleep(30)  # site ocupado: espera e tenta de novo


def parece_console(titulo, preco):
    t = titulo.lower()
    if preco < PRECO_MINIMO_CONSOLE:
        return False
    if not ("playstation" in t or "ps5" in t):
        return False
    return not any(p in t for p in NAO_E_CONSOLE)


def kabum():
    url = ("https://servicespub.prod.api.aws.grupokabum.com.br/catalog/v2/products?"
           + urllib.parse.urlencode({"query": "console playstation 5", "page_size": 40}))
    dados = json.loads(baixar(url))
    itens = []
    for p in dados.get("data", []):
        a = p["attributes"]
        if a.get("is_marketplace") or not a.get("available"):
            continue  # pula vendedor terceiro e produto sem estoque
        preco = a.get("price_with_discount") or a.get("price")
        oferta = a.get("offer")
        if oferta and oferta.get("price_with_discount"):
            preco = min(preco, oferta["price_with_discount"])
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


def aliexpress():
    html = baixar("https://pt.aliexpress.com/w/wholesale-playstation-5-console.html")
    itens = []
    for bloco in html.split('"productId":"')[1:]:
        pid = bloco[:bloco.find('"')]
        t = re.search(r'"displayTitle":"([^"]*)', bloco)
        p = re.search(r'"minPrice":([\d.]+)', bloco)
        if not t or not p:
            continue
        titulo, preco = t.group(1), float(p.group(1))
        if not parece_console(titulo, preco):
            continue
        itens.append({"loja": "AliExpress", "titulo": "⚠ confira o vendedor! " + titulo,
                      "preco": preco, "link": f"https://pt.aliexpress.com/item/{pid}.html",
                      "foto": ("https:" + f.group(1)) if (f := re.search(r'"imgUrl":"([^"]+)"', bloco)) else ""})
    return itens


CANAIS_TELEGRAM = ["LaPromotion"]   # canais públicos de oferta (o nome que vem depois de t.me/)
TG_VISTOS = PASTA / "telegram_vistos.txt"
NAO_E_CONSOLE_POST = ["jogo", "controle", "capa", "cupom mercado", "headset", "fone", "suporte",
                      "base de", "vr2", "portal", "skin", "cabo", "gift card", "ps plus",
                      "sorteio", "gta", "grand theft"]


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
            primeira = texto.split("\n")[0].lower()
            if not ("ps5" in primeira or "playstation 5" in primeira or "playstation®5" in primeira):
                continue
            if any(p_ in primeira for p_ in NAO_E_CONSOLE_POST):
                continue
            preco = re.search(r"R\$\s*([\d.]+(?:,\d{2})?)", texto)
            preco = float(preco.group(1).replace(".", "").replace(",", ".")) if preco else None
            if preco is not None and preco < PRECO_MINIMO_CONSOLE:
                continue
            foto = re.search(r"background-image:url\('([^']+)'\)", p.split("tgme_widget_message_photo_wrap", 1)[-1]) \
                if "tgme_widget_message_photo_wrap" in p else None
            novos.append({"id": post.group(1), "canal": canal, "texto": texto, "preco": preco,
                          "foto": foto.group(1) if foto else "", "link": f"https://t.me/{post.group(1)}"})
    return novos


def avisar_canais():
    try:
        novos = canais_telegram()
    except Exception as e:
        print(f"  Canais do Telegram: erro ({e})")
        return
    print(f"  Canais do Telegram: {len(novos)} post(s) novo(s) de PS5")
    for o in novos:
        topo = "🚨🚨🚨 PROMOÇÃO!!! 🚨🚨🚨" if o["preco"] and o["preco"] <= PRECO_SUPER else "📢 OFERTA DE PS5 NUM CANAL"
        notificar(f"{topo}\n(canal {o['canal']})\n\n{o['texto'][:700]}\n\n📎 Post original: {o['link']}", o["foto"])
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
        if oferta_id in vistos or not parece_console(titulo, preco):
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


def avisar_pelando():
    try:
        novas = pelando()
    except Exception as e:
        print(f"  Pelando: erro ({e})")
        return
    print(f"  Pelando: {len(novas)} oferta(s) nova(s) de console")
    for o in novas:
        topo = "🚨🚨🚨 PROMOÇÃO!!! 🚨🚨🚨" if o["preco"] <= PRECO_SUPER else "🔥 OFERTA NOVA DE PS5"
        notificar(f"{topo}\n(postada no Pelando)\n\n🏪 {o['loja']}\n💰 {brl(o['preco'])}\n\n"
                  f"{o['titulo'][:150]}\n\n🔗 {o['link'] or o['pelando']}\n"
                  f"💬 Comentários: {o['pelando']}", o["foto"])
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


def enviar_foto(chat_id, foto_url, legenda):
    """Baixa a foto da loja e manda o arquivo pro Telegram."""
    req = urllib.request.Request(foto_url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as r:
        imagem = r.read()
    limite = "----monitorps5"
    partes = []
    for nome, valor in [("chat_id", str(chat_id)), ("caption", legenda)]:
        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{nome}"\r\n\r\n{valor}\r\n'.encode())
    partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="photo"; filename="ps5.jpg"\r\n'
                  f'Content-Type: image/jpeg\r\n\r\n'.encode() + imagem + b"\r\n")
    partes.append(f"--{limite}--\r\n".encode())
    token = ler_config().get("telegram_token", "")
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendPhoto", data=b"".join(partes),
                                 headers={"Content-Type": f"multipart/form-data; boundary={limite}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def notificar(texto, foto=""):
    """Manda a mensagem pro seu Telegram (com foto, se tiver)."""
    chat_id = ler_config().get("telegram_chat_id", "")
    try:
        if foto:
            try:
                enviar_foto(chat_id, foto, texto[:1000])
                print("  (aviso com foto enviado no Telegram)")
                return
            except Exception:
                pass  # foto falhou: manda só o texto
        telegram("sendMessage", chat_id=chat_id, text=texto)
        print("  (aviso enviado no Telegram)")
    except Exception as e:
        print(f"  (não consegui mandar no Telegram: {e})")


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
    notificar("✅ Monitor de PS5 ligado! Vou te avisar aqui quando tiver promoção.")
    print("Pronto! Chat configurado.")


def brl(v):
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def checar():
    print(f"\n=== Checando preços do PS5 — {datetime.now():%d/%m/%Y %H:%M} ===")
    itens = []
    fontes = [("KaBuM!", kabum), ("Amazon", amazon),
              ("Magalu", magalu_via_zoom), ("AliExpress", aliexpress)]
    for nome, fonte in fontes:
        try:
            achados = fonte()
            print(f"  {nome}: {len(achados)} console(s) encontrado(s)")
            itens += achados
        except Exception as e:
            print(f"  {nome}: erro ({e})")

    avisar_pelando()
    avisar_canais()

    if not itens:
        print("Nenhum preço encontrado agora.")
        return

    antes = ultimo_preco_por_link()
    itens.sort(key=lambda i: i["preco"])
    alertas = []
    print()
    for i in itens:
        marca, motivos = "", []
        anterior = antes.get(i["link"])
        if anterior and i["preco"] < anterior - 0.5:
            marca = f"  ⬇ BAIXOU (era {brl(anterior)})"
            motivos.append(f"⬇ BAIXOU! Era {brl(anterior)}")
        # abaixo do alvo: avisa quando entra abaixo do alvo (não repete toda hora)
        if i["preco"] <= PRECO_ALVO and (anterior is None or anterior > PRECO_ALVO or motivos):
            marca += "  ★ ABAIXO DO ALVO"
            motivos.append(f"★ Abaixo do seu alvo de {brl(PRECO_ALVO)}")
        super_promo = i["preco"] <= PRECO_SUPER and (anterior is None or anterior > PRECO_SUPER or motivos)
        if super_promo:
            marca += "  🚨 PROMOÇÃO"
            alertas.append((
                f"🚨🚨🚨 PROMOÇÃO!!! 🚨🚨🚨\n"
                f"PS5 POR MENOS DE {brl(PRECO_SUPER)}!\n\n"
                f"🏪 {i['loja']}\n💰 {brl(i['preco'])}\n\n"
                f"{i['titulo'][:150]}\n\n🔗 {i['link']}\n\n"
                f"Corre antes que acabe! (mas confira se é o console mesmo)",
                i.get("foto", "")))
        elif motivos:
            alertas.append((
                f"🎮 PROMOÇÃO DE PS5 — {i['loja']}\n\n"
                f"💰 {brl(i['preco'])}\n" + "\n".join(motivos) + "\n\n"
                f"{i['titulo'][:150]}\n\n🔗 {i['link']}",
                i.get("foto", "")))
        print(f"{brl(i['preco']):>13}  {i['loja']:<7} {i['titulo'][:70]}{marca}")
        print(f"{'':>13}  {i['link']}")

    salvar_historico(itens)
    mais_barato = itens[0]
    print(f"\nMais barato agora: {brl(mais_barato['preco'])} na {mais_barato['loja']}")
    print(f"Seu preço-alvo: {brl(PRECO_ALVO)}")

    for texto, foto in alertas:
        notificar(texto, foto)


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
