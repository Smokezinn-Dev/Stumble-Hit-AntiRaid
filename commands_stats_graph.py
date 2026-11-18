# ============================================================
# COMMANDS_STATS_GRAPH.PY - v1.0
# GRÁFICOS DE ESTATÍSTICAS COM PILLOW
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Gera uma imagem PNG com 3 gráficos diferentes mostrando o
# histórico de punições do servidor:
#   1. LINHA — punições por dia (últimos N dias)
#   2. BARRAS — top 5 membros mais punidos
#   3. PIZZA — distribuição por tipo (kick/ban/warn/timeout)
#
# 📌 POR QUE ISSO IMPORTA?
# Números em texto são chatos. Gráficos mostram padrões:
#   • Teve um pico de bans? Aconteceu algo naquele dia.
#   • Qual tipo de punição é mais usada? Kick? Timeout?
#   • Quem são os reincidentes? A staff tá sendo justa?
#
# 📌 COMO FUNCIONA?
# - Lê do banco: punishments, warns, antinuke_logs
# - Agrupa por dia/tipo/membro
# - Desenha os 3 gráficos em uma única imagem PNG (usando Pillow)
# - Envia no canal como arquivo
#
# 📌 IMPORTANTE:
# Tudo roda em THREAD SEPARADA (run_in_executor) pra não travar
# o event loop do bot. Pillow é blocking (CPU-bound), então
# precisa dessa proteção.
#
# 📌 COMO USAR?
# >statsgraph               → 30 dias (padrão)
# >statsgraph 7             → últimos 7 dias
# >statsgraph 90            → últimos 90 dias
# >statsgraph auto on/off   → envio automático semanal
# >statsgraph channel #canal → canal de envio automático
# ============================================================

# ────────────────────────────────────────────────────────────
# BLOCO 1 — IMPORTS
# ────────────────────────────────────────────────────────────

# "asyncio" = rodar coisas em paralelo.
import asyncio

# "io" = arquivos em memória (BytesIO).
# Usado pra montar a imagem PNG sem salvar em disco.
import io

# "math" = cálculos matemáticos (usado no gráfico de pizza).
import math

# "time" = medir tempo (cooldowns).
import time

# "datetime" = trabalhar com datas.
# "timedelta" = diferença entre datas.
from datetime import datetime, timedelta, timezone

# "typing" = documentação de tipos.
from typing import Dict, List, Optional, Tuple

# "discord" = biblioteca principal.
import discord

# "commands" = criar comandos e cogs.
# "tasks" = tarefas em loop (background).
from discord.ext import commands, tasks

# ─── Pillow (geração de imagens) ───
try:
    from PIL import Image, ImageDraw, ImageFont
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False
    Image = None
    ImageDraw = None
    ImageFont = None

# ─── Imports internos ───

# database = conexão MongoDB.
from database import get_connection_async

# embeds = caixas coloridas prontas.
from embeds import embed_success, embed_error, embed_warning, embed_info

# utils = helpers (cache, etc).
from utils import TTLCache


def _now() -> datetime:
    """Retorna a data/hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# BLOCO 2 — PALETA DE CORES E CONSTANTES
# ============================================================
# 📌 POR QUE DEFINIR AQUI?
# Porque assim fica fácil trocar as cores se quiser.
# São cores "amigáveis" pro dark mode do Discord.

# Cores em RGB (tuplas).
CORES = {
    "fundo":      (30, 31, 34),      # fundo escuro
    "card":       (43, 45, 49),      # card de cada gráfico
    "texto":      (230, 232, 235),   # texto principal (quase branco)
    "texto_dim":  (150, 153, 158),   # texto secundário (cinza)

    # Cores dos gráficos.
    "linha":      (88, 101, 242),    # azul Discord (blurple)
    "barra":      (87, 242, 135),    # verde Discord
    "pizza1":     (237, 66, 69),     # vermelho (kick)
    "pizza2":     (254, 231, 92),    # amarelo (warn)
    "pizza3":     (88, 101, 242),    # azul (timeout)
    "pizza4":     (235, 69, 158),    # rosa (ban)
    "pizza5":     (150, 153, 158),   # cinza (outros)

    "grid":       (60, 62, 68),      # linhas de grade
}

# Tamanho da imagem final (em pixels).
IMG_WIDTH = 1200
IMG_HEIGHT = 800

# Padding entre os cards (espaçamento).
PADDING = 20

# Altura da barra de título superior.
HEADER_HEIGHT = 70


# ============================================================
# BLOCO 3 — CONFIGURAÇÃO PADRÃO
# ============================================================

DEFAULT_CONFIG = {
    "enabled": True,

    # Dias padrão ao rodar ">statsgraph" sem argumento.
    "default_days": 30,

    # Envio automático semanal?
    "auto_enabled": False,

    # Dia da semana do envio automático.
    # 0=segunda, 1=terça, ..., 6=domingo
    "auto_weekday": 0,

    # Hora do envio (UTC, 0-23).
    "auto_hour": 9,

    # Canal de envio automático.
    "auto_channel": 0,

    # Última vez que enviou (pra não spammar).
    "last_sent_at": None,
}


# ============================================================
# BLOCO 4 — MANAGER (config no MongoDB)
# ============================================================

class StatsGraphManager:
    # Cache de configs.
    _cache = TTLCache(max_size=200, ttl=60)

    @classmethod
    async def get_config(cls, guild_id: int) -> dict:
        """Pega a configuração do cache ou consulta o MongoDB usando Motor."""
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached

        # Recupera a conexão Motor sem bloquear o event loop do bot.
        db = await get_connection_async()
        # A leitura é assíncrona; aguardar aqui garante que cfg só seja montada após a resposta.
        doc = await db["stats_graph_config"].find_one({"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}

        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def update(cls, guild_id: int, key: str, value):
        """Persiste uma configuração com Motor e invalida o cache após a escrita."""
        # Obtém a instância assíncrona do banco usada pelo restante dos módulos migrados.
        db = await get_connection_async()
        # Aguarda o Mongo confirmar a atualização antes de prosseguir com o fluxo do comando.
        await db["stats_graph_config"].update_one(
            {"guild_id": guild_id},
            {"$set": {key: value}},
            upsert=True,
        )
        # Só invalida o cache depois da persistência para não perder a configuração atual.
        cls._cache.invalidate(guild_id)

    @classmethod
    def invalidate(cls, guild_id: int):
        """Força recarregar na próxima consulta."""
        cls._cache.invalidate(guild_id)


# ============================================================
# BLOCO 5 — CARREGADOR DE FONTES
# ============================================================
# 📌 POR QUE ISSO EXISTE?
# O Pillow vem com uma fonte "padrão" (load_default) que é
# horrível e minúscula. Precisamos carregar fontes TrueType
# de sistema pra ficar legível.

def _carregar_fonte(tamanho: int) -> "ImageFont.FreeTypeFont":
    """
    Tenta carregar uma fonte TrueType. Se não conseguir, usa
    a padrão do Pillow.

    📌 ORDEM DE TENTATIVA:
    1. Fontes comuns em Linux (DejaVu, Liberation)
    2. Fontes comuns em Windows (arial, segoeui)
    3. Fontes comuns em macOS (Helvetica)
    4. Fallback: ImageFont.load_default()
    """
    if not _HAS_PIL:
        return None

    # Lista de fontes comuns por ordem de preferência.
    fontes = [
        "arial.ttf",                     # Windows
        "segoeui.ttf",                   # Windows moderno
        "calibri.ttf",                   # Windows
        "DejaVuSans-Bold.ttf",           # Linux
        "DejaVuSans.ttf",                # Linux
        "LiberationSans-Regular.ttf",    # Linux
        "Helvetica.ttc",                 # macOS
        "HelveticaNeue.ttc",             # macOS
    ]

    for nome in fontes:
        try:
            return ImageFont.truetype(nome, tamanho)
        except Exception:
            continue

    # Fallback: fonte padrão.
    try:
        return ImageFont.load_default()
    except Exception:
        return None


# ============================================================
# BLOCO 6 — BUSCA DE DADOS
# ============================================================
# 📌 COMO FUNCIONA?
# Lê o banco e agrupa por dia/tipo/membro. Retorna dicts
# prontos pra desenhar.

async def _buscar_dados(guild_id: int, dias: int) -> Dict:
    """
    Busca dados de punições e agrupa.

    📌 FONTES DE DADOS:
    - punishments (ban/kick/warn/timeout aplicados por comandos)
    - warns (avisos aplicados)
    - antinuke_logs (punições automáticas do anti-nuke)

    📌 RETORNA:
    {
        "por_dia":      [(data, total), ...],
        "por_tipo":     {"kick": 5, "ban": 2, ...},
        "top_users":    [(user_id, count), ...],
        "total":        int,
        "periodo_dias": int,
    }
    """
    # A busca dos três conjuntos de dados é I/O de rede e deve usar Motor.
    db = await get_connection_async()
    cutoff = _now() - timedelta(days=dias)

    # ─── 1. Lê punições ───
    # Motor devolve um cursor assíncrono; to_list materializa os documentos sem bloquear o loop.
    punicoes = await db["punishments"].find({
        "guild_id": guild_id,
        "timestamp": {"$gte": cutoff},
    }, {"timestamp": 1, "type": 1, "user_id": 1}).to_list(length=None)

    # ─── 2. Lê warns (do sistema de avisos) ───
    # Mantém os mesmos filtros e projeção; somente a materialização passa a ser assíncrona.
    warns = await db["warn_system_warns"].find({
        "guild_id": guild_id,
        "timestamp": {"$gte": cutoff},
    }, {"timestamp": 1, "user_id": 1}).to_list(length=None)

    # ─── 3. Lê logs do anti-nuke ───
    # A terceira consulta também é aguardada antes da etapa de agregação em memória.
    an_logs = await db["antinuke_logs"].find({
        "guild_id": guild_id,
        "timestamp": {"$gte": cutoff},
    }, {"timestamp": 1, "action_type": 1, "user_id": 1}).to_list(length=None)

    # ─── Agrupa por dia ───
    por_dia: Dict[str, int] = {}
    for _ in range(dias):
        dia = (_now() - timedelta(days=_)).strftime("%Y-%m-%d")
        por_dia[dia] = 0

    # ─── Agrupa por tipo ───
    por_tipo: Dict[str, int] = {}

    # ─── Agrupa por usuário ───
    por_user: Dict[int, int] = {}

    # ─── Processa punições ───
    for p in punicoes:
        ts = p.get("timestamp")
        if not ts:
            continue
        # Normaliza timezone.
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        dia = ts.strftime("%Y-%m-%d")
        if dia in por_dia:
            por_dia[dia] += 1

        tipo = p.get("type", "outros")
        por_tipo[tipo] = por_tipo.get(tipo, 0) + 1

        uid = p.get("user_id")
        if uid:
            por_user[uid] = por_user.get(uid, 0) + 1

    # ─── Processa warns ───
    for w in warns:
        ts = w.get("timestamp")
        if not ts:
            continue
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        dia = ts.strftime("%Y-%m-%d")
        if dia in por_dia:
            por_dia[dia] += 1

        por_tipo["warn"] = por_tipo.get("warn", 0) + 1

        uid = w.get("user_id")
        if uid:
            por_user[uid] = por_user.get(uid, 0) + 1

    # ─── Processa logs do anti-nuke ───
    for a in an_logs:
        ts = a.get("timestamp")
        if not ts:
            continue
        if hasattr(ts, "tzinfo") and ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        dia = ts.strftime("%Y-%m-%d")
        if dia in por_dia:
            por_dia[dia] += 1

        tipo = a.get("action_type", "outros")
        # Normaliza nomes de ação pra tipos legíveis.
        tipo_legivel = {
            "ban": "ban",
            "kick": "kick",
            "chan_del": "canal",
            "role_del": "cargo",
            "bot_add": "bot_add",
        }.get(tipo, "outros")
        por_tipo[tipo_legivel] = por_tipo.get(tipo_legivel, 0) + 1

        uid = a.get("user_id")
        if uid:
            por_user[uid] = por_user.get(uid, 0) + 1

    # ─── Ordena por dia (do mais antigo pro mais recente) ───
    por_dia_lista = sorted(por_dia.items(), key=lambda x: x[0])

    # ─── Top 5 users ───
    top_users = sorted(por_user.items(), key=lambda x: x[1],
                        reverse=True)[:5]

    # ─── Total ───
    total = sum(por_dia.values())

    return {
        "por_dia": por_dia_lista,
        "por_tipo": por_tipo,
        "top_users": top_users,
        "total": total,
        "periodo_dias": dias,
    }


# ============================================================
# BLOCO 7 — DESENHO DOS GRÁFICOS
# ============================================================
# 📌 COMO FUNCIONA?
# Cada função desenha 1 dos 3 gráficos em uma área (card)
# da imagem principal. Retorna a coordenada Y onde terminou.

def _desenhar_header(img, draw, titulo: str, subtitulo: str,
                       fonte_titulo, fonte_sub):
    """Desenha o cabeçalho no topo da imagem."""
    # Fundo do header.
    draw.rectangle(
        [(0, 0), (IMG_WIDTH, HEADER_HEIGHT)],
        fill=CORES["card"])

    # Título.
    draw.text(
        (20, 12), titulo,
        fill=CORES["texto"], font=fonte_titulo)

    # Subtítulo (à direita).
    if subtitulo:
        # Pega largura do subtítulo pra alinhar à direita.
        bbox = draw.textbbox((0, 0), subtitulo, font=fonte_sub)
        w = bbox[2] - bbox[0]
        draw.text(
            (IMG_WIDTH - w - 20, 25), subtitulo,
            fill=CORES["texto_dim"], font=fonte_sub)


def _desenhar_grafico_linha(img, draw, dados: List[Tuple[str, int]],
                              x: int, y: int, w: int, h: int,
                              fonte_titulo, fonte_label):
    """
    Desenha um gráfico de LINHA (punições por dia).

    📌 COMO FUNCIONA:
    1. Desenha o card (retângulo escuro)
    2. Desenha o título
    3. Desenha os eixos X e Y
    4. Desenha a linha conectando os pontos
    5. Desenha os labels dos eixos
    """
    # ─── Card de fundo ───
    draw.rectangle(
        [(x, y), (x + w, y + h)],
        fill=CORES["card"], outline=CORES["grid"], width=1)

    # ─── Título do card ───
    draw.text(
        (x + 15, y + 12), "📈 Punições por dia",
        fill=CORES["texto"], font=fonte_titulo)

    # ─── Área de desenho (com padding interno) ───
    padding_interno = 50
    gx = x + padding_interno
    gy = y + 50
    gw = w - padding_interno - 25
    gh = h - 50 - 40

    if not dados:
        draw.text((gx + gw // 2 - 50, gy + gh // 2),
                   "Sem dados", fill=CORES["texto_dim"],
                   font=fonte_label)
        return

    # ─── Limites do gráfico ───
    max_valor = max(v for _, v in dados)
    if max_valor == 0:
        max_valor = 1  # evita divisão por zero

    # ─── Desenha grade horizontal (5 linhas) ───
    for i in range(6):
        ly = gy + int(gh * i / 5)
        draw.line([(gx, ly), (gx + gw, ly)],
                   fill=CORES["grid"], width=1)

        # Label Y (valor).
        valor_y = int(max_valor * (5 - i) / 5)
        draw.text((x + 10, ly - 8), str(valor_y),
                   fill=CORES["texto_dim"], font=fonte_label)

    # ─── Pontos da linha ───
    n = len(dados)
    pontos = []
    for i, (_, valor) in enumerate(dados):
        # Posição X.
        if n == 1:
            px = gx + gw // 2
        else:
            px = gx + int(gw * i / (n - 1))
        # Posição Y (invertida, porque Y cresce pra baixo).
        py = gy + gh - int(gh * valor / max_valor)
        pontos.append((px, py))

    # ─── Desenha a linha conectando os pontos ───
    if len(pontos) >= 2:
        draw.line(pontos, fill=CORES["linha"], width=3)

    # ─── Desenha os círculos nos pontos ───
    for px, py in pontos:
        draw.ellipse(
            [(px - 4, py - 4), (px + 4, py + 4)],
            fill=CORES["linha"], outline=CORES["texto"], width=1)

    # ─── Labels do eixo X (mostra 5 datas espaçadas) ───
    if n >= 5:
        indices = [0, n // 4, n // 2, 3 * n // 4, n - 1]
    elif n >= 2:
        indices = list(range(n))
    else:
        indices = [0]

    for i in indices:
        if i >= len(dados):
            continue
        _, _ = dados[i]
        px = gx + int(gw * i / max(n - 1, 1))
        # Mostra só o dia/mês.
        data_str = dados[i][0][5:]  # "YYYY-MM-DD" → "MM-DD"
        draw.text(
            (px - 15, gy + gh + 8), data_str,
            fill=CORES["texto_dim"], font=fonte_label)


def _desenhar_grafico_barras(img, draw, dados: List[Tuple[int, int]],
                                x: int, y: int, w: int, h: int,
                                fonte_titulo, fonte_label, guild):
    """
    Desenha um gráfico de BARRAS (top 5 membros mais punidos).
    """
    # ─── Card de fundo ───
    draw.rectangle(
        [(x, y), (x + w, y + h)],
        fill=CORES["card"], outline=CORES["grid"], width=1)

    # ─── Título ───
    draw.text(
        (x + 15, y + 12), "🏆 Top 5 mais punidos",
        fill=CORES["texto"], font=fonte_titulo)

    if not dados:
        draw.text((x + w // 2 - 50, y + h // 2),
                   "Sem dados", fill=CORES["texto_dim"],
                   font=fonte_label)
        return

    # ─── Área de desenho ───
    padding_interno = 40
    gx = x + padding_interno
    gy = y + 50
    gw = w - padding_interno - 20
    gh = h - 50 - 40

    # ─── Limites ───
    max_valor = max(v for _, v in dados)
    if max_valor == 0:
        max_valor = 1

    # ─── Altura de cada barra ───
    n = len(dados)
    altura_barra = min(40, (gh - 10) // max(n, 1))
    espaco = (gh - altura_barra * n) // (n + 1)

    # ─── Desenha cada barra ───
    for i, (uid, valor) in enumerate(dados):
        by = gy + espaco + i * (altura_barra + espaco)
        # Largura proporcional ao valor.
        bw = int(gw * 0.6 * valor / max_valor)

        # Barra.
        draw.rectangle(
            [(gx + 150, by), (gx + 150 + bw, by + altura_barra)],
            fill=CORES["barra"])

        # Nome do user (à esquerda da barra).
        nome = f"User {uid}"
        try:
            membro = guild.get_member(uid)
            if membro:
                nome = membro.display_name[:18]
        except Exception:
            pass
        draw.text(
            (gx, by + altura_barra // 2 - 8), nome,
            fill=CORES["texto"], font=fonte_label)

        # Valor (à direita da barra).
        draw.text(
            (gx + 150 + bw + 8, by + altura_barra // 2 - 8),
            str(valor),
            fill=CORES["texto"], font=fonte_label)


def _desenhar_grafico_pizza(img, draw, dados: Dict[str, int],
                              x: int, y: int, w: int, h: int,
                              fonte_titulo, fonte_label):
    """
    Desenha um gráfico de PIZZA (distribuição por tipo).

    📌 COMO FUNCIONA:
    1. Calcula o ângulo de cada "fatia" baseado na proporção
    2. Usa draw.pieslice pra desenhar cada fatia
    3. Desenha a legenda ao lado
    """
    # ─── Card de fundo ───
    draw.rectangle(
        [(x, y), (x + w, y + h)],
        fill=CORES["card"], outline=CORES["grid"], width=1)

    # ─── Título ───
    draw.text(
        (x + 15, y + 12), "🥧 Distribuição por tipo",
        fill=CORES["texto"], font=fonte_titulo)

    if not dados:
        draw.text((x + w // 2 - 50, y + h // 2),
                   "Sem dados", fill=CORES["texto_dim"],
                   font=fonte_label)
        return

    # ─── Total ───
    total = sum(dados.values())
    if total == 0:
        return

    # ─── Calcula tamanho da pizza ───
    pizza_size = min(w, h) - 80
    pizza_x = x + 30
    pizza_y = y + (h - pizza_size) // 2 + 10
    pizza_box = [
        (pizza_x, pizza_y),
        (pizza_x + pizza_size, pizza_y + pizza_size),
    ]

    # ─── Cores para cada fatia ───
    cores_lista = [
        CORES["pizza1"], CORES["pizza2"], CORES["pizza3"],
        CORES["pizza4"], CORES["pizza5"],
    ]

    # ─── Ordena por quantidade ───
    itens = sorted(dados.items(), key=lambda x: x[1], reverse=True)[:5]

    # ─── Desenha cada fatia ───
    angulo_inicio = -90  # começa do topo
    for i, (nome, valor) in enumerate(itens):
        # Ângulo proporcional (360 graus = círculo completo).
        angulo = 360 * valor / total
        cor = cores_lista[i % len(cores_lista)]

        draw.pieslice(
            pizza_box,
            start=angulo_inicio,
            end=angulo_inicio + angulo,
            fill=cor,
            outline=CORES["card"],
            width=2)

        angulo_inicio += angulo

    # ─── Desenha círculo interno (efeito "donut") ───
    inner_size = int(pizza_size * 0.5)
    inner_x = pizza_x + (pizza_size - inner_size) // 2
    inner_y = pizza_y + (pizza_size - inner_size) // 2
    draw.ellipse(
        [(inner_x, inner_y),
         (inner_x + inner_size, inner_y + inner_size)],
        fill=CORES["card"])

    # ─── Total no centro ───
    draw.text(
        (inner_x + inner_size // 2 - 15,
         inner_y + inner_size // 2 - 10),
        str(total),
        fill=CORES["texto"], font=fonte_titulo)

    # ─── Legenda (à direita) ───
    legenda_x = pizza_x + pizza_size + 40
    legenda_y = pizza_y + 20
    for i, (nome, valor) in enumerate(itens):
        cor = cores_lista[i % len(cores_lista)]

        # Quadradinho da cor.
        draw.rectangle(
            [(legenda_x, legenda_y + 2),
             (legenda_x + 15, legenda_y + 17)],
            fill=cor)

        # Texto: "kick (5)"
        pct = (valor / total) * 100
        texto = f"{nome} ({valor} • {pct:.0f}%)"
        draw.text(
            (legenda_x + 25, legenda_y),
            texto,
            fill=CORES["texto"], font=fonte_label)

        legenda_y += 28


# ============================================================
# BLOCO 8 — MONTAGEM DA IMAGEM FINAL
# ============================================================

def _montar_imagem(guild, dados: Dict) -> io.BytesIO:
    """
    Monta a imagem final com os 3 gráficos.

    📌 LAYOUT:
    ┌────────────────────────────────────────┐
    │  HEADER (título + subtítulo)           │
    ├────────────────────┬───────────────────┤
    │  GRÁFICO DE LINHA  │  GRÁFICO DE       │
    │  (punições/dia)    │  BARRAS (top 5)   │
    ├────────────────────┴───────────────────┤
    │  GRÁFICO DE PIZZA (distribuição)       │
    └────────────────────────────────────────┘
    """
    if not _HAS_PIL:
        raise RuntimeError("Pillow não instalado")

    # ─── Cria a imagem base ───
    img = Image.new("RGB", (IMG_WIDTH, IMG_HEIGHT), CORES["fundo"])
    draw = ImageDraw.Draw(img)

    # ─── Carrega fontes ───
    fonte_titulo = _carregar_fonte(24)
    fonte_sub = _carregar_fonte(14)
    fonte_label = _carregar_fonte(14)
    fonte_header = _carregar_fonte(26)

    # ─── Header ───
    titulo = f"📊 Estatísticas de Moderação"
    subtitulo = (f"{guild.name} • "
                  f"últimos {dados['periodo_dias']} dias • "
                  f"{dados['total']} ações")

    _desenhar_header(img, draw, titulo, subtitulo,
                       fonte_header, fonte_sub)

    # ─── Calcula coordenadas dos cards ───
    # Linha de cima: 2 cards lado a lado.
    card_y = HEADER_HEIGHT + PADDING
    card_h = 340
    card_w = (IMG_WIDTH - PADDING * 3) // 2

    # Gráfico de linha (esquerda).
    _desenhar_grafico_linha(
        img, draw, dados["por_dia"],
        PADDING, card_y, card_w, card_h,
        fonte_titulo, fonte_label)

    # Gráfico de barras (direita).
    _desenhar_grafico_barras(
        img, draw, dados["top_users"],
        PADDING * 2 + card_w, card_y, card_w, card_h,
        fonte_titulo, fonte_label, guild)

    # ─── Gráfico de pizza (embaixo, largura total) ───
    card2_y = card_y + card_h + PADDING
    card2_h = IMG_HEIGHT - card2_y - PADDING

    _desenhar_grafico_pizza(
        img, draw, dados["por_tipo"],
        PADDING, card2_y, IMG_WIDTH - PADDING * 2, card2_h,
        fonte_titulo, fonte_label)

    # ─── Rodapé ───
    rodape = (f"Stumble Hit AntiRaid • Stats Graph v1.0 • "
                f"Gerado em {_now().strftime('%d/%m/%Y %H:%M')} UTC")
    draw.text(
        (IMG_WIDTH - 20 - 400, IMG_HEIGHT - 20),
        rodape,
        fill=CORES["texto_dim"], font=fonte_sub)

    # ─── Salva em BytesIO ───
    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)

    return buffer


# ============================================================
# BLOCO 9 — COG PRINCIPAL
# ============================================================

class StatsGraphCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # ─── Task que envia automaticamente ───
        self._auto_task.start()

    def cog_unload(self):
        try:
            self._auto_task.cancel()
        except Exception:
            pass

    # ============================================================
    # TASK: ENVIO AUTOMÁTICO
    # ============================================================

    @tasks.loop(minutes=30)
    async def _auto_task(self):
        """
        Roda a cada 30min. Se for dia/hora do envio automático,
        envia o gráfico pro canal configurado.
        """
        now = _now()

        for guild in self.bot.guilds:
            try:
                # Cada guilda é consultada com Motor para a tarefa não bloquear outros eventos.
                cfg = await StatsGraphManager.get_config(guild.id)
                if not cfg.get("enabled") or not cfg.get("auto_enabled"):
                    continue

                # Checa se é o dia certo.
                if now.weekday() != int(cfg.get("auto_weekday", 0)):
                    continue

                # Checa se é a hora certa.
                if now.hour != int(cfg.get("auto_hour", 9)):
                    continue

                # Checa se já enviou hoje (evita spammar).
                last = cfg.get("last_sent_at")
                if last:
                    if hasattr(last, "tzinfo") and last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    if last.date() == now.date():
                        continue

                # Canal configurado.
                channel_id = cfg.get("auto_channel")
                if not channel_id:
                    continue
                channel = guild.get_channel(int(channel_id))
                if not channel:
                    continue

                # Gera e envia.
                await self._enviar_grafico_auto(guild, channel,
                                                   int(cfg.get("default_days", 30)))

                # Marca como enviado.
                await StatsGraphManager.update(guild.id, "last_sent_at", now)

            except Exception:
                continue

    @_auto_task.before_loop
    async def _before_auto(self):
        await self.bot.wait_until_ready()

    async def _enviar_grafico_auto(self, guild, channel, dias: int):
        """Gera e envia o gráfico automaticamente."""
        if not _HAS_PIL:
            return

        try:
            # Busca os dados com Motor; o desenho continua isolado na thread de trabalho.
            dados = await _buscar_dados(guild.id, dias)

            # Gera em thread separada.
            loop = asyncio.get_event_loop()
            img_bytes = await loop.run_in_executor(
                None, _montar_imagem, guild, dados)

            file = discord.File(
                img_bytes,
                filename=f"stats_{dias}d_{_now().strftime('%Y%m%d')}.png")

            await channel.send(
                content=f"📊 **Estatísticas semanais** "
                        f"(últimos {dias} dias)",
                file=file)
        except Exception:
            pass

    # ============================================================
    # COMANDO PRINCIPAL
    # ============================================================

    @commands.group(name="statsgraph", aliases=["sg", "statsg"],
                    invoke_without_command=True)
    @commands.has_permissions(kick_members=True)
    async def statsgraph(self, ctx, dias: int = None):
        """
        Gera e envia o gráfico de estatísticas.

        📌 USO:
        >statsgraph         → 30 dias (padrão)
        >statsgraph 7       → últimos 7 dias
        >statsgraph 90      → últimos 90 dias
        """
        # Sem args → painel (se for admin) ou gráfico padrão.
        if dias is None:
            if ctx.author.guild_permissions.administrator:
                return await self._show_panel(ctx)
            cfg = await StatsGraphManager.get_config(ctx.guild.id)
            dias = int(cfg.get("default_days", 30))

        # Valida.
        if dias < 1 or dias > 365:
            return await ctx.send(embed=embed_error(
                "❌ Use entre 1 e 365 dias."))

        # Checa Pillow.
        if not _HAS_PIL:
            return await ctx.send(embed=embed_error(
                "❌ Pillow não instalado. Instale com "
                "`pip install Pillow`"))

        msg = await ctx.send(embed=embed_info(
            f"⏳ Gerando gráfico dos últimos {dias} dias..."))

        try:
            # Busca dados.
            # A consulta ao Mongo é assíncrona; apenas o processamento Pillow vai para executor.
            dados = await _buscar_dados(ctx.guild.id, dias)

            if dados["total"] == 0:
                return await msg.edit(embed=embed_warning(
                    f"⚠️ Nenhuma punição registrada nos "
                    f"últimos {dias} dias."))

            # Gera em thread separada (Pillow é blocking).
            loop = asyncio.get_event_loop()
            img_bytes = await loop.run_in_executor(
                None, _montar_imagem, ctx.guild, dados)

            # Envia.
            file = discord.File(
                img_bytes,
                filename=f"stats_{dias}d.png")

            await msg.delete()
            await ctx.send(
                content=f"📊 **Estatísticas dos últimos {dias} dias** "
                        f"({dados['total']} ações)",
                file=file)

        except Exception as e:
            await msg.edit(embed=embed_error(f"❌ Erro: {e}"))

    async def _show_panel(self, ctx):
        """Painel de configuração (só admins)."""
        cfg = await StatsGraphManager.get_config(ctx.guild.id)

        def onoff(v):
            return "✅" if v else "❌"

        channel_str = (f"<#{cfg['auto_channel']}>"
                        if cfg.get("auto_channel") else "❌")

        dias_semana = ["Segunda", "Terça", "Quarta", "Quinta",
                        "Sexta", "Sábado", "Domingo"]
        dia_nome = dias_semana[int(cfg.get("auto_weekday", 0))]

        embed = discord.Embed(
            title="📊 Stats Graph",
            description=(
                "Gera gráficos (PNG) com estatísticas de moderação: "
                "punições por dia, top 5 infratores e distribuição por tipo."
            ),
            color=discord.Color.blurple(),
            timestamp=_now())

        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.add_field(
            name="📈 Configurações",
            value=(
                f"**Status:** {onoff(cfg['enabled'])}\n"
                f"**Dias padrão:** `{cfg['default_days']}`\n"
                f"**Pillow:** {onoff(_HAS_PIL)}"
            ),
            inline=True)

        embed.add_field(
            name="🤖 Automático",
            value=(
                f"**Ativo:** {onoff(cfg['auto_enabled'])}\n"
                f"**Dia:** `{dia_nome}`\n"
                f"**Hora (UTC):** `{cfg['auto_hour']}h`\n"
                f"**Canal:** {channel_str}"
            ),
            inline=True)

        embed.add_field(
            name="📌 Comandos",
            value=(
                "`>statsgraph` — 30 dias (ou painel)\n"
                "`>statsgraph 7` — últimos 7 dias\n"
                "`>statsgraph 90` — últimos 90 dias\n"
                "`>statsgraph auto on/off` — envio automático\n"
                "`>statsgraph weekday 0-6` — dia da semana\n"
                "`>statsgraph hour 0-23` — hora (UTC)\n"
                "`>statsgraph channel #canal` — canal\n"
                "`>statsgraph default 30` — dias padrão"
            ),
            inline=False)

        embed.set_footer(text="Stumble Hit AntiRaid • Stats Graph v1.0")
        await ctx.send(embed=embed)

    # ─── Auto on/off ───

    @statsgraph.command(name="auto")
    @commands.has_permissions(administrator=True)
    async def sg_auto(self, ctx, state: str):
        """Liga/desliga envio automático semanal."""
        enabled = state.lower() in ("on", "true", "1", "sim")
        await StatsGraphManager.update(ctx.guild.id, "auto_enabled", enabled)
        await ctx.send(embed=embed_success(
            f"✅ Envio automático: {'ON' if enabled else 'OFF'}"))

    # ─── Weekday ───

    @statsgraph.command(name="weekday", aliases=["dia"])
    @commands.has_permissions(administrator=True)
    async def sg_weekday(self, ctx, dia: int):
        """
        Define o dia da semana do envio automático.
        📌 0=Segunda, 1=Terça, ..., 6=Domingo
        """
        if dia < 0 or dia > 6:
            return await ctx.send(embed=embed_error(
                "❌ Use entre 0 (segunda) e 6 (domingo)."))

        await StatsGraphManager.update(ctx.guild.id, "auto_weekday", dia)

        nomes = ["Segunda", "Terça", "Quarta", "Quinta",
                  "Sexta", "Sábado", "Domingo"]
        await ctx.send(embed=embed_success(
            f"✅ Envio automático: toda **{nomes[dia]}**"))

    # ─── Hour ───

    @statsgraph.command(name="hour", aliases=["hora"])
    @commands.has_permissions(administrator=True)
    async def sg_hour(self, ctx, hora: int):
        """Define a hora do envio (UTC)."""
        if hora < 0 or hora > 23:
            return await ctx.send(embed=embed_error(
                "❌ Use entre 0 e 23."))

        await StatsGraphManager.update(ctx.guild.id, "auto_hour", hora)
        await ctx.send(embed=embed_success(
            f"✅ Envio automático às `{hora}:00` UTC"))

    # ─── Channel ───

    @statsgraph.command(name="channel", aliases=["canal"])
    @commands.has_permissions(administrator=True)
    async def sg_channel(self, ctx, canal: discord.TextChannel):
        """Define o canal do envio automático."""
        await StatsGraphManager.update(ctx.guild.id, "auto_channel", canal.id)
        await ctx.send(embed=embed_success(
            f"✅ Envio automático em {canal.mention}"))

    # ─── Default days ───

    @statsgraph.command(name="default", aliases=["padrao"])
    @commands.has_permissions(administrator=True)
    async def sg_default(self, ctx, dias: int):
        """Define quantos dias mostrar por padrão."""
        if dias < 1 or dias > 365:
            return await ctx.send(embed=embed_error(
                "❌ Use entre 1 e 365."))

        await StatsGraphManager.update(ctx.guild.id, "default_days", dias)
        await ctx.send(embed=embed_success(
            f"✅ Dias padrão: `{dias}`"))

    # ─── Teste ───

    @statsgraph.command(name="test", aliases=["testar"])
    @commands.has_permissions(administrator=True)
    async def sg_test(self, ctx):
        """Testa a geração do gráfico (envia no canal)."""
        return await self.statsgraph(ctx, 30)


# ============================================================
# BLOCO 10 — SETUP
# ============================================================

async def setup(bot):
    """Chamado pelo main.py pra carregar esse cog."""
    if bot.get_cog("StatsGraphCommands") is None:
        await bot.add_cog(StatsGraphCommands(bot))