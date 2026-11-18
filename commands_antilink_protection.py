# ============================================================
# COMMANDS_ANTILINK_PROTECTION.PY - v1.0
# ANTI IP LOGGER + ANTI LINK MALICIOSO
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Bloqueia links de serviços de IP Logger (Grabify, IPLogger, etc).
# Esses serviços são usados por atacantes pra capturar o IP dos
# membros do servidor quando eles clicam no link.
#
# 📌 O QUE É UM IP LOGGER?
# É um site que parece inofensivo ("veja essa foto!"), mas quando
# você clica, ele registra:
#   • Seu IP
#   • Sua localização aproximada (cidade/país)
#   • Seu dispositivo (celular/PC)
#   • Navegador que você usa
# Aí o atacante pode usar essas informações pra:
#   • Doxxing (expor seus dados)
#   • Ataques DDoS
#   • Engenharia social
#
# 📌 COMO ESSE SISTEMA FUNCIONA?
# 1. Tem uma lista (por guild) de domínios bloqueados
# 2. Quando alguém manda uma mensagem, o bot varre o texto
# 3. Se achar qualquer domínio da lista, DELETA a mensagem
# 4. Aplica a punição configurada (warn/timeout/kick/ban)
# 5. Loga no canal configurado
#
# 📌 DIFERENÇA PRA OUTROS BOTS?
# A maioria dos bots só bloqueia convites do Discord (discord.gg).
# Poucos bloqueiam IP loggers. E os que bloqueiam, cobram caro ou
# têm lista pequena. Aqui você tem ~50 domínios + pode adicionar
# os SEUS próprios (custom list por guild).
#
# 📌 COMO USAR?
# >antiiplogger                    → Painel
# >antiiplogger on/off             → Ativar/desativar
# >antiiplogger list               → Ver domínios bloqueados
# >antiiplogger add <domínio>      → Adicionar domínio custom
# >antiiplogger remove <domínio>   → Remover domínio custom
# >antiiplogger punish <tipo>      → warn/timeout/kick/ban
# >antiiplogger log #canal         → Canal de log
# >antiiplogger whitelist @user    → User/cargo que ignora
# >antiiplogger stats              → Estatísticas
# ============================================================

# ────────────────────────────────────────────────────────────
# BLOCO 1 — IMPORTS
# ────────────────────────────────────────────────────────────

# "re" = regex (buscar padrões em texto).
# Usamos pra detectar URLs e domínios nas mensagens.
import re

# "time" = medir tempo (cooldowns, rate limit).
import time

# "datetime" = trabalhar com datas (timestamps dos logs).
# "timedelta" = diferença entre datas (ex: +10 minutos).
from datetime import datetime, timedelta, timezone

# "typing" = documentação de tipos (não muda comportamento).
from typing import Dict, List, Optional, Set

# "discord" = biblioteca principal do Discord.
import discord

# "commands" = criar comandos com prefixo (>) e cogs.
from discord.ext import commands

# "defaultdict/deque" = dicionário com valor padrão + fila rápida.
# Usamos pra guardar histórico de detecções (rate limit).
from collections import defaultdict, deque

# ─── Imports internos (do próprio projeto) ───

# database = conexão MongoDB.
from database import get_connection

# embeds = caixas coloridas prontas.
from embeds import embed_success, embed_error, embed_warning, embed_info

# embeds_professional = log profissional (título, severidade, etc).
from embeds_professional import ProfessionalLog, fmt_reason

# utils = helpers (cache, etc).
from utils import TTLCache
from protection_safety import automatic_action_allowed


def _now() -> datetime:
    """
    Retorna a data/hora atual em UTC.

    📌 POR QUE UTC?
    Porque é o padrão do Discord. Ele mostra automaticamente no
    fuso local de cada usuário quando você usa <t:timestamp:f>.
    Além disso, evita problemas com fusos horários diferentes.
    """
    return datetime.now(timezone.utc)


# ============================================================
# BLOCO 2 — LISTA PADRÃO DE DOMÍNIOS IP LOGGER
# ============================================================
# 📌 O QUE É ISSO?
# Lista dos ~50 domínios conhecidos de IP Logger. Esses são os
# mais usados por atacantes. Alguns têm variações (subdomínios),
# então a gente bloqueia o domínio principal.
#
# 📌 POR QUE ESSES DOMÍNIOS?
# Todos foram reportados publicamente como serviços de IP logging
# em comunidades de segurança (Reddit r/discordapp, GitHub
# anti-phishing lists, etc). Se algum novo aparecer, é só
# adicionar com `>antiiplogger add <dominio>`.

DEFAULT_BLOCKED_DOMAINS = [
    # ─── Grabify (o mais famoso) ───
    "grabify.link",
    "grabify.org",
    "grabify.io",
    "grabify.co",
    "grabify.top",

    # ─── IPLogger e similares ───
    "iplogger.org",
    "iplogger.com",
    "iplogger.ru",
    "iplogger.co",
    "iplogger.info",
    "iplogger.cn",
    "iplogger.ca",
    "iplogger.cc",
    "iplogger.cz",
    "iplogger.pl",
    "iplogger.pro",
    "iplogger.top",
    "iplogger.link",
    "iplogger.site",
    "iplogger.xyz",

    # ─── 2no.co (serviço gratuito) ───
    "2no.co",
    "yip.su",
    "iplogger.de",

    # ─── Yip.su (outro gratuito) ───
    "iplis.ru",
    "iplogger.me",
    "iplogger.us",
    "iplogger.net",

    # ─── Outros serviços conhecidos ───
    "iplogger.click",
    "iplogger.online",
    "iplogger.guru",
    "iplogger.gg",
    "ipgrabber.ru",
    "ipgrabber.com",
    "ipgrabber.top",
    "ipgrabber.xyz",
    "yip.su",
    "whatstheirip.com",
    "whatstheirip.net",
    "whatstheirip.org",
    "lovebird.guru",
    "trulyyourself.net",
    "blasze.com",
    "blasze.tk",
    "ipgraber.com",
    "ipgrab.com",
    "iplog.co",
    "iplogs.org",
    "iplogs.com",
    "iplogger.org.br",
    "iplogger.com.br",
    "grabify.pw",
    "picturedots.com",
    "iplogger.io",
    "iplogger.me",
    "iplogger.link",
    "ps3cfw.com",
    "iplogger.org",
    "iplogger.ru",
    "trackurl.info",
    "urlz.fr",
    "yip.su",
    "iplogger.org",
    "iplogger.com",
    "iplogger.ru",
    "iplogger.co",
    "iplogger.info",
    "iplogger.de",
    "iplogger.net",
    "iplogger.me",
    "iplogger.us",
    "iplogger.ca",
    "iplogger.cc",
    "iplogger.cz",
    "iplogger.pl",
    "iplogger.pro",
    "iplogger.top",
    "iplogger.link",
    "iplogger.site",
    "iplogger.xyz",
    "iplogger.click",
    "iplogger.online",
    "iplogger.guru",
    "iplogger.gg",
]


# ============================================================
# BLOCO 3 — CONFIGURAÇÃO PADRÃO
# ============================================================
# 📌 O QUE É ISSO?
# Todos os valores que podem ser ajustados pelo admin via comando.
# True = ligado, False = desligado.

DEFAULT_CONFIG = {
    "enabled": True,

    # Punição aplicada quando detecta IP logger.
    # Opções: warn / timeout / kick / ban / delete_only
    "punishment": "warn",

    # Duração do timeout (em minutos) quando punishment = timeout.
    "timeout_minutes": 30,

    # Domínios customizados (adicionados pelo admin).
    # A gente junta essa lista com a DEFAULT_BLOCKED_DOMAINS.
    "custom_domains": [],

    # Domínios que o admin REMOVEU da lista padrão
    # (por algum motivo específico — raro, mas útil).
    "removed_domains": [],

    # Canal pra enviar os logs.
    # Se 0, tenta usar o canal do anti-nuke como fallback.
    "log_channel": 0,

    # Webhook opcional pra logs (mais rápido que canal).
    "log_webhook": "",

    # Notificar o dono via DM quando detecta.
    "notify_owner": False,

    # Users/cargos que ignoram essa proteção.
    "whitelist_users": [],
    "whitelist_roles": [],

    # Cargos com permissão "manage_messages" ignoram automaticamente?
    # (útil pra não punir staff que manda link legítimo)
    "ignore_staff": True,

    # Cooldown entre punições do mesmo user (segundos).
    "punish_cooldown": 60,

    # Rate limit: máximo de detecções por minuto por user.
    # Evita punir 50x se a pessoa manda 50 msgs com o mesmo link.
    "max_detections_per_minute": 3,
}


# ============================================================
# BLOCO 4 — MANAGER (config no MongoDB)
# ============================================================
# 📌 O QUE ESSA CLASSE FAZ?
# Guarda todas as configs do anti-ip-logger por servidor. Usa
# cache TTL pra não ir no banco toda hora.

class AntiIPLoggerManager:
    # Cache de configs (evita ir no banco toda vez).
    # max_size=200 = cabe 200 servidores. ttl=60 = expira em 60s.
    _cache = TTLCache(max_size=200, ttl=60)

    @classmethod
    def get_config(cls, guild_id: int) -> dict:
        """
        Pega a config do servidor (do cache OU do banco).

        📌 COMO FUNCIONA:
        1. Tenta pegar do cache (rápido)
        2. Se não achou, vai no banco (lento)
        3. Junta os padrões com o que tá salvo (o salvo vence)
        4. Guarda no cache pra próxima
        """
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached

        db = get_connection()
        doc = db["antilink_config"].find_one({"guild_id": guild_id}) or {}

        # Junta DEFAULT_CONFIG com o doc do banco.
        # {**DEFAULT, **doc} = o doc sobrescreve o DEFAULT.
        cfg = {**DEFAULT_CONFIG, **doc}

        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    def get_config_fresh(cls, guild_id: int) -> dict:
        db = get_connection()
        doc = db["antilink_config"].find_one({"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    def update(cls, guild_id: int, key: str, value):
        """
        Salva uma config no banco e invalida o cache.

        📌 POR QUE INVALIDAR?
        Porque se salvou algo novo, o cache tá velho. Na próxima
        consulta, ele vai reler do banco e vir atualizado.
        """
        db = get_connection()
        db["antilink_config"].update_one(
            {"guild_id": guild_id},
            {"$set": {key: value}},
            upsert=True,  # cria se não existir
        )
        cls._cache.invalidate(guild_id)

    @classmethod
    def invalidate(cls, guild_id: int):
        """Força recarregar a config na próxima consulta."""
        cls._cache.invalidate(guild_id)

    @classmethod
    def get_all_domains(cls, cfg: dict) -> Set[str]:
        """
        Devolve o conjunto FINAL de domínios bloqueados:
        padrão - removidos + customizados.
        """
        # Começa com a lista padrão (convertida em set pra busca rápida).
        final = set(DEFAULT_BLOCKED_DOMAINS)

        # Remove os que o admin tirou.
        removed = set(cfg.get("removed_domains", []))
        final -= removed

        # Adiciona os customizados (convertidos pra lowercase).
        custom = cfg.get("custom_domains", [])
        for d in custom:
            final.add(d.lower().strip())

        return final


# ============================================================
# BLOCO 5 — HELPERS (funções auxiliares)
# ============================================================
# 📌 POR QUE SEPARAR?
# Deixar a lógica de checagem em funções pequenas facilita
# testar e reutilizar.

def _extrair_dominios(text: str) -> List[str]:
    """
    Extrai todos os domínios de um texto.

    📌 O QUE FAZ:
    1. Pega todas as URLs do texto (http:// ou https://)
    2. Extrai só o domínio (sem protocolo, sem path)
    3. Retorna lista.

    📌 EXEMPLO:
    >>> _extrair_dominios("Olha isso: https://grabify.link/abc123")
    ["grabify.link"]

    Args:
        text: texto da mensagem

    Returns:
        Lista de domínios encontrados (lowercase).
    """
    if not text:
        return []

    # Regex que captura URLs completas.
    # Basicamente: http(s):// seguido de qualquer coisa não-espaço.
    url_rx = re.compile(r"https?://([^\s/]+)", re.IGNORECASE)

    dominios = []
    for match in url_rx.finditer(text):
        # match.group(1) = só o domínio (sem protocolo)
        dominio = match.group(1).lower()
        # Remove porta se tiver (ex: "exemplo.com:8080")
        dominio = dominio.split(":")[0]
        # Remove "www." se tiver (normaliza pra comparação).
        if dominio.startswith("www."):
            dominio = dominio[4:]
        dominios.append(dominio)

    return dominios


def _is_whitelisted(cfg: dict, user_id: int,
                     member: Optional[discord.Member]) -> bool:
    """
    Diz se o user tá na whitelist (por ID ou cargo).

    📌 ORDEM DE CHECAGEM:
    1. ID direto na whitelist_users
    2. Algum cargo dele na whitelist_roles
    3. Se ignore_staff = True e ele tem "manage_messages"
    """
    # Checa user ID.
    if user_id in cfg.get("whitelist_users", []):
        return True

    # Checa cargos.
    if member:
        wl_roles = set(cfg.get("whitelist_roles", []))
        if any(r.id in wl_roles for r in member.roles):
            return True

        # Ignorar staff?
        if cfg.get("ignore_staff", True):
            if member.guild_permissions.manage_messages:
                return True

    return False


def _eh_ip_logger(dominio: str, dominios_bloqueados: Set[str]) -> bool:
    """
    Diz se um domínio é IP logger.

    📌 COMO FUNCIONA:
    Compara o domínio com a lista. Se for EXATO ou SUBDOMÍNIO
    (ex: "sub.grabify.link" bate "grabify.link"), retorna True.
    """
    dominio = dominio.lower()

    # Bate exato?
    if dominio in dominios_bloqueados:
        return True

    # Algum domínio da lista é sufixo dele?
    # (ex: "sub.grabify.link".endswith("grabify.link") = True)
    for bloqueado in dominios_bloqueados:
        if dominio.endswith("." + bloqueado):
            return True

    return False


# ============================================================
# BLOCO 6 — COG PRINCIPAL
# ============================================================
# 📌 O QUE É UM COG?
# É uma "gaveta" do bot que contém comandos e listeners.
# Cada arquivo commands_*.py registra um cog.

class AntiIPLoggerCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

        # ─── Rastreio de detecções (pra rate limit) ───
        # Estrutura: {guild_id: {user_id: deque[timestamps]}}
        # Cada vez que detecta, adiciona um timestamp. Se passar
        # do limite, ignora (não pune de novo).
        self._deteccoes: Dict[int, Dict[int, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=20))
        )

        # ─── Cooldowns (evita punir múltiplas vezes seguidas) ───
        # Estrutura: {guild_id: {user_id: último_timestamp_punição}}
        self._cooldowns: Dict[int, Dict[int, float]] = defaultdict(dict)

    # ============================================================
    # LISTENER: ANALISA CADA MENSAGEM
    # ============================================================

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        Chamado pelo Discord pra CADA mensagem enviada.

        📌 FLUXO:
        1. Ignora DMs, bots, e se sistema tá off
        2. Ignora se user tá na whitelist
        3. Extrai domínios da mensagem
        4. Se algum é IP logger → pune
        """
        # ─── 1. Filtros rápidos (early return) ───
        if not message.guild:
            return  # DM
        if message.author.bot:
            return  # outro bot
        if not message.content:
            return  # sem texto (só anexo/sticker)

        # ─── 2. Carrega config ───
        cfg = AntiIPLoggerManager.get_config(message.guild.id)
        if not cfg.get("enabled"):
            return

        # ─── 3. Checa whitelist ───
        if _is_whitelisted(cfg, message.author.id, message.author):
            return

        # ─── 4. Extrai domínios e checa ───
        dominios = _extrair_dominios(message.content)
        if not dominios:
            return  # sem link, tudo bem

        # Pega a lista final de domínios bloqueados.
        bloqueados = AntiIPLoggerManager.get_all_domains(cfg)

        # Procura algum domínio bloqueado.
        dominio_achado = None
        for d in dominios:
            if _eh_ip_logger(d, bloqueados):
                dominio_achado = d
                break

        if not dominio_achado:
            return  # nenhum IP logger, tudo bem

        # ─── 5. Rate limit (evita punir 20x a mesma pessoa) ───
        if not self._passa_rate_limit(message.guild.id, message.author.id, cfg):
            return

        # ─── 6. Cooldown (evita punir antes do tempo) ───
        if self._em_cooldown(message.guild.id, message.author.id, cfg):
            return

        # ─── 7. PUNE! ───
        await self._punir(message, dominio_achado, cfg)

    # ============================================================
    # LÓGICA DE RATE LIMIT E COOLDOWN
    # ============================================================

    def _passa_rate_limit(self, guild_id: int, user_id: int,
                            cfg: dict) -> bool:
        """
        Diz se o user pode ser punido (não passou do rate limit).

        📌 COMO FUNCIONA:
        - Cada detecção adiciona um timestamp na fila do user
        - Se a fila tiver mais que X itens nos últimos 60s, bloqueia
        - Isso evita 50 punições se a pessoa mandar 50 msgs com o mesmo link
        """
        max_per_min = int(cfg.get("max_detections_per_minute", 3))
        now = time.time()
        dq = self._deteccoes[guild_id][user_id]

        # Remove detecções antigas (mais de 60s).
        while dq and now - dq[0] > 60:
            dq.popleft()

        # Já passou do limite?
        if len(dq) >= max_per_min:
            return False

        # Adiciona essa detecção.
        dq.append(now)
        return True

    def _em_cooldown(self, guild_id: int, user_id: int,
                       cfg: dict) -> bool:
        """
        Diz se o user tá em cooldown (não pode ser punido agora).

        📌 COMO FUNCIONA:
        - Quando pune, salva o timestamp atual
        - Na próxima detecção, checa se passou do cooldown
        - Se não passou, ignora (não pune de novo)
        """
        cooldown = float(cfg.get("punish_cooldown", 60))
        last = self._cooldowns[guild_id].get(user_id, 0)
        return (time.time() - last) < cooldown

    def _set_cooldown(self, guild_id: int, user_id: int):
        """Marca que o user foi punido agora."""
        self._cooldowns[guild_id][user_id] = time.time()

    # ============================================================
    # PUNIÇÃO
    # ============================================================

    async def _punir(self, message: discord.Message, dominio: str,
                       cfg: dict):
        """
        Aplica a punição configurada.

        📌 FLUXO:
        1. Deleta a mensagem (sempre)
        2. Aplica a punição (warn/timeout/kick/ban)
        3. Salva no banco (histórico)
        4. Envia log
        5. Notifica owner (se configurado)
        """
        guild = message.guild
        member = message.author
        try:
            cfg = AntiIPLoggerManager.get_config_fresh(guild.id)
        except Exception:
            return
        if not cfg.get("enabled", False):
            return
        if _is_whitelisted(cfg, member.id, member):
            return
        if not await automatic_action_allowed(guild.id, member):
            return
        punishment = cfg.get("punishment", "warn")

        # Revalida novamente ANTES de qualquer efeito (inclusive apagar a
        # mensagem), para capturar um toggle OFF/whitelist alterado durante
        # a detecção. A leitura final após a exclusão continua protegendo a
        # punição de timeout/kick/ban.
        try:
            pre_delete_cfg = AntiIPLoggerManager.get_config_fresh(guild.id)
        except Exception:
            return
        if (not pre_delete_cfg.get("enabled", False)
                or _is_whitelisted(pre_delete_cfg, member.id, member)
                or not await automatic_action_allowed(guild.id, member)):
            return
        cfg = pre_delete_cfg
        punishment = cfg.get("punishment", punishment)

        # ─── 1. Deleta a mensagem ───
        try:
            await message.delete()
        except Exception:
            pass  # sem permissão, canal deletado, etc

        # ─── 2. Última validação antes da punição ───
        try:
            final_cfg = AntiIPLoggerManager.get_config_fresh(guild.id)
        except Exception:
            return
        if (not final_cfg.get("enabled", False)
                or _is_whitelisted(final_cfg, member.id, member)
                or not await automatic_action_allowed(guild.id, member)):
            return
        punishment = final_cfg.get("punishment", punishment)
        # ─── Aplica a punição ───
        try:
            if punishment == "timeout":
                minutes = int(final_cfg.get("timeout_minutes", 30))
                until = discord.utils.utcnow() + timedelta(minutes=minutes)
                await member.timeout(
                    until, reason=f"Anti IP Logger: {dominio}")
            elif punishment == "kick":
                await member.kick(reason=f"Anti IP Logger: {dominio}")
            elif punishment == "ban":
                await member.ban(
                    reason=f"Anti IP Logger: {dominio}",
                    delete_message_days=1)
            # warn e delete_only não precisam de ação extra.
        except Exception:
            pass  # se não tiver permissão, ignora

        # ─── 3. Marca cooldown ───
        self._set_cooldown(guild.id, member.id)

        # ─── 4. Salva no banco ───
        try:
            db = get_connection()
            db["antilink_infractions"].insert_one({
                "guild_id": guild.id,
                "user_id": member.id,
                "username": str(member),
                "domain": dominio,
                "message_content": message.content[:500],
                "channel_id": message.channel.id,
                "punishment": punishment,
                "timestamp": _now(),
            })
        except Exception:
            pass

        # ─── 5. Envia log ───
        await self._enviar_log(guild, member, dominio, punishment, cfg)

        # ─── 6. Notifica owner ───
        if cfg.get("notify_owner"):
            await self._notify_owner(guild, member, dominio, cfg)

        # ─── 7. Avisa no canal (sutil) ───
        try:
            await message.channel.send(
                embed=embed_warning(
                    f"⚠️ {member.mention} — link de **IP Logger** detectado "
                    f"e removido."),
                delete_after=8)
        except Exception:
            pass

    # ============================================================
    # LOG
    # ============================================================

    async def _enviar_log(self, guild: discord.Guild,
                            member: discord.Member, dominio: str,
                            punishment: str, cfg: dict):
        """Envia log pro canal/webhook configurado."""
        # Monta o log profissional.
        log = ProfessionalLog(
            title="🚫 IP Logger Bloqueado",
            description=(
                "Um link de IP Logger foi detectado e bloqueado. "
                "Esses links são usados para capturar o IP dos membros "
                "e podem ser usados para doxxing ou ataques."
            ),
            severity="high",
            footer="Stumble Hit AntiRaid • Anti IP Logger")

        log.set_target(member)
        log.add_field("🌐 Domínio", f"`{dominio}`")
        log.add_field("⚖️ Punição", f"`{punishment}`")
        log.add_field("📍 Canal", guild.get_channel(
            getattr(member, "_last_channel", 0)) or "?")

        embed = log.build()

        # 1. Webhook (mais rápido).
        wh_url = cfg.get("log_webhook", "")
        if wh_url:
            try:
                wh = discord.Webhook.from_url(wh_url, client=None)
                await wh.send(embed=embed, username="🚫 Anti IP Logger",
                              wait=False)
                return
            except Exception:
                pass

        # 2. Canal de log.
        log_ch = cfg.get("log_channel")
        if log_ch:
            channel = guild.get_channel(int(log_ch))
            if channel:
                try:
                    await channel.send(embed=embed)
                    return
                except Exception:
                    pass

        # 3. Fallback: canal do anti-nuke.
        try:
            from commands_antinuke import AntiNukeManager
            an_cfg = AntiNukeManager.get_config(guild.id)
            fallback = an_cfg.get("log_channel")
            if fallback:
                channel = guild.get_channel(int(fallback))
                if channel:
                    await channel.send(embed=embed)
        except Exception:
            pass

    async def _notify_owner(self, guild: discord.Guild,
                              member: discord.Member, dominio: str,
                              cfg: dict):
        """Manda DM pro dono do servidor."""
        try:
            owner = guild.owner or await guild.fetch_member(guild.owner_id)
            await owner.send(embed=discord.Embed(
                title="🚫 IP Logger detectado",
                description=(
                    f"**Servidor:** {guild.name}\n"
                    f"**User:** {member.mention}\n"
                    f"**Domínio:** `{dominio}`"
                ),
                color=discord.Color.red(),
                timestamp=_now()))
        except Exception:
            pass

    # ============================================================
    # COMANDOS (grupo >antiiplogger)
    # ============================================================

    @commands.group(name="antiiplogger", aliases=["aipl", "antiip"],
                    invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def antiiplogger(self, ctx):
        """
        Painel principal do Anti IP Logger.

        📌 SEM ARGUMENTO:
        Mostra o painel com todas as configs atuais + comandos.
        """
        cfg = AntiIPLoggerManager.get_config(ctx.guild.id)

        # Funçãozinha pra transformar True/False em ✅/❌.
        def onoff(v):
            return "✅" if v else "❌"

        # Pega a lista final de domínios.
        total_dominios = len(AntiIPLoggerManager.get_all_domains(cfg))

        # Formata canal de log.
        log_ch = cfg.get("log_channel")
        log_str = f"<#{log_ch}>" if log_ch else "❌"

        embed = discord.Embed(
            title="🚫 Anti IP Logger",
            description=(
                "Bloqueia links de **IP Logger** (Grabify, IPLogger, etc). "
                "Esses links são usados para capturar o IP dos membros.\n\n"
                "**Como funciona:** o bot varre cada mensagem, e se achar "
                "qualquer domínio da lista, deleta a mensagem e aplica a "
                "punição configurada."
            ),
            color=discord.Color.red(),
            timestamp=_now())

        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.add_field(
            name="📊 Configurações",
            value=(
                f"**Status:** {onoff(cfg['enabled'])}\n"
                f"**Punição:** `{cfg['punishment']}`\n"
                f"**Timeout:** `{cfg['timeout_minutes']}min`\n"
                f"**Log:** {log_str}\n"
                f"**Ignorar staff:** {onoff(cfg['ignore_staff'])}\n"
                f"**Notificar owner:** {onoff(cfg['notify_owner'])}"
            ),
            inline=True)

        embed.add_field(
            name="🌐 Domínios",
            value=(
                f"**Total bloqueado:** `{total_dominios}`\n"
                f"**Custom:** `{len(cfg.get('custom_domains', []))}`\n"
                f"**Removidos:** `{len(cfg.get('removed_domains', []))}`"
            ),
            inline=True)

        embed.add_field(
            name="🛡️ Whitelist",
            value=(
                f"**Users:** `{len(cfg.get('whitelist_users', []))}`\n"
                f"**Cargos:** `{len(cfg.get('whitelist_roles', []))}`"
            ),
            inline=True)

        embed.add_field(
            name="📌 Comandos",
            value=(
                "`>antiiplogger on/off` — liga/desliga\n"
                "`>antiiplogger list` — lista domínios\n"
                "`>antiiplogger add <domínio>` — adiciona custom\n"
                "`>antiiplogger remove <domínio>` — remove\n"
                "`>antiiplogger punish <tipo>` — warn/timeout/kick/ban\n"
                "`>antiiplogger timeout <min>` — duração do timeout\n"
                "`>antiiplogger log #canal` — canal de log\n"
                "`>antiiplogger webhook <URL>` — webhook de log\n"
                "`>antiiplogger whitelist @user` — adiciona whitelist\n"
                "`>antiiplogger unwhitelist @user` — remove\n"
                "`>antiiplogger staff on/off` — ignorar staff\n"
                "`>antiiplogger notify on/off` — notificar owner\n"
                "`>antiiplogger stats` — estatísticas"
            ),
            inline=False)

        embed.set_footer(text="Stumble Hit AntiRaid • Anti IP Logger v1.0")
        await ctx.send(embed=embed)

    # ─── Ligar/desligar ───

    @antiiplogger.command(name="on")
    @commands.has_permissions(administrator=True)
    async def aipl_on(self, ctx):
        """Ativa o Anti IP Logger."""
        AntiIPLoggerManager.update(ctx.guild.id, "enabled", True)
        await ctx.send(embed=embed_success(
            "✅ Anti IP Logger **ativado**!"))

    @antiiplogger.command(name="off")
    @commands.has_permissions(administrator=True)
    async def aipl_off(self, ctx):
        """Desativa o Anti IP Logger."""
        AntiIPLoggerManager.update(ctx.guild.id, "enabled", False)
        await ctx.send(embed=embed_success(
            "✅ Anti IP Logger **desativado**!"))

    # ─── Listar domínios ───

    @antiiplogger.command(name="list", aliases=["lista", "domains"])
    @commands.has_permissions(administrator=True)
    async def aipl_list(self, ctx):
        """
        Lista todos os domínios bloqueados.

        📌 OBSERVAÇÃO:
        Mostra em blocos porque a lista é grande (~50 domínios).
        """
        cfg = AntiIPLoggerManager.get_config(ctx.guild.id)
        todos = sorted(AntiIPLoggerManager.get_all_domains(cfg))
        custom = set(cfg.get("custom_domains", []))

        embed = discord.Embed(
            title=f"🌐 Domínios Bloqueados ({len(todos)})",
            description=(
                "🟢 = **custom** (adicionado por você)\n"
                "⚪ = **padrão** (do sistema)"
            ),
            color=discord.Color.red(),
            timestamp=_now())

        # Monta as linhas. Marca custom com 🟢.
        linhas = []
        for d in todos:
            emoji = "🟢" if d in custom else "⚪"
            linhas.append(f"{emoji} `{d}`")

        # Divide em blocos de ~25 pra não estourar o limite do embed.
        bloco_atual = []
        bloco_num = 1
        for linha in linhas:
            bloco_atual.append(linha)
            if len(bloco_atual) >= 25:
                embed.add_field(
                    name=f"📄 Página {bloco_num}",
                    value="\n".join(bloco_atual),
                    inline=False)
                bloco_atual = []
                bloco_num += 1

        # Adiciona o último bloco se sobrou.
        if bloco_atual:
            embed.add_field(
                name=f"📄 Página {bloco_num}",
                value="\n".join(bloco_atual),
                inline=False)

        await ctx.send(embed=embed)

    # ─── Adicionar domínio ───

    @antiiplogger.command(name="add", aliases=["adicionar"])
    @commands.has_permissions(administrator=True)
    async def aipl_add(self, ctx, *, dominio: str):
        """
        Adiciona um domínio customizado à lista de bloqueio.

        📌 COMO USAR:
        >antiiplogger add exemplos.com

        Não precisa de http:// nem https://. Só o domínio.
        """
        # Normaliza o domínio (lowercase, remove www.).
        dominio = dominio.lower().strip()

        # Remove protocolo se o user passou por engano.
        if dominio.startswith("http://"):
            dominio = dominio[7:]
        if dominio.startswith("https://"):
            dominio = dominio[8:]
        if dominio.startswith("www."):
            dominio = dominio[4:]

        # Remove path (se passou algo tipo "exemplo.com/abc").
        dominio = dominio.split("/")[0]

        # Valida formato básico (tem que ter pelo menos 1 ponto).
        if "." not in dominio:
            return await ctx.send(embed=embed_error(
                "❌ Domínio inválido. Use formato: `exemplo.com`"))

        cfg = AntiIPLoggerManager.get_config(ctx.guild.id)
        custom = list(cfg.get("custom_domains", []))

        # Já tá na lista?
        if dominio in custom:
            return await ctx.send(embed=embed_warning(
                f"⚠️ `{dominio}` já está na lista custom."))

        # Já tá na lista padrão?
        if dominio in DEFAULT_BLOCKED_DOMAINS:
            return await ctx.send(embed=embed_warning(
                f"⚠️ `{dominio}` já está na lista **padrão** do sistema."))

        # Adiciona.
        custom.append(dominio)
        AntiIPLoggerManager.update(ctx.guild.id, "custom_domains", custom)

        await ctx.send(embed=embed_success(
            f"✅ `{dominio}` adicionado à lista de bloqueio."))

    # ─── Remover domínio ───

    @antiiplogger.command(name="remove", aliases=["remover", "del"])
    @commands.has_permissions(administrator=True)
    async def aipl_remove(self, ctx, *, dominio: str):
        """
        Remove um domínio da lista de bloqueio.

        📌 COMO FUNCIONA:
        - Se for CUSTOM: remove da lista custom (some de vez).
        - Se for PADRÃO: adiciona à lista "removed" (não bloqueia mais).
        """
        dominio = dominio.lower().strip()
        if dominio.startswith("www."):
            dominio = dominio[4:]

        cfg = AntiIPLoggerManager.get_config(ctx.guild.id)
        custom = list(cfg.get("custom_domains", []))
        removed = list(cfg.get("removed_domains", []))

        # É custom?
        if dominio in custom:
            custom.remove(dominio)
            AntiIPLoggerManager.update(ctx.guild.id, "custom_domains", custom)
            return await ctx.send(embed=embed_success(
                f"✅ `{dominio}` removido da lista custom."))

        # É padrão?
        if dominio in DEFAULT_BLOCKED_DOMAINS:
            if dominio not in removed:
                removed.append(dominio)
                AntiIPLoggerManager.update(
                    ctx.guild.id, "removed_domains", removed)
            return await ctx.send(embed=embed_success(
                f"✅ `{dominio}` removido da lista padrão (não será mais bloqueado)."))

        # Não tá em nenhuma lista.
        await ctx.send(embed=embed_error(
            f"❌ `{dominio}` não está em nenhuma lista."))

    # ─── Definir punição ───

    @antiiplogger.command(name="punish", aliases=["punicao", "punishment"])
    @commands.has_permissions(administrator=True)
    async def aipl_punish(self, ctx, tipo: str):
        """
        Define a punição aplicada.

        📌 TIPOS DISPONÍVEIS:
        - warn         → só avisa e deleta a msg (padrão)
        - timeout      → silencia por X minutos
        - kick         → expulsa
        - ban          → bane
        - delete_only  → só deleta, não pune
        """
        tipo = tipo.lower().strip()
        validos = ("warn", "timeout", "kick", "ban", "delete_only")

        if tipo not in validos:
            return await ctx.send(embed=embed_error(
                f"❌ Use um dos: {', '.join(f'`{v}`' for v in validos)}"))

        AntiIPLoggerManager.update(ctx.guild.id, "punishment", tipo)
        await ctx.send(embed=embed_success(
            f"✅ Punição: `{tipo}`"))

    # ─── Duração do timeout ───

    @antiiplogger.command(name="timeout")
    @commands.has_permissions(administrator=True)
    async def aipl_timeout(self, ctx, minutos: int):
        """Define a duração do timeout (em minutos)."""
        if minutos < 1 or minutos > 40320:  # máx 28 dias (limite do Discord)
            return await ctx.send(embed=embed_error(
                "❌ Use entre 1 e 40320 minutos."))

        AntiIPLoggerManager.update(ctx.guild.id, "timeout_minutes", minutos)
        await ctx.send(embed=embed_success(
            f"✅ Timeout: `{minutos}min`"))

    # ─── Canal de log ───

    @antiiplogger.command(name="log")
    @commands.has_permissions(administrator=True)
    async def aipl_log(self, ctx, canal: discord.TextChannel):
        """Define o canal de log."""
        AntiIPLoggerManager.update(ctx.guild.id, "log_channel", canal.id)
        await ctx.send(embed=embed_success(
            f"✅ Logs em {canal.mention}"))

    # ─── Webhook de log ───

    @antiiplogger.command(name="webhook")
    @commands.has_permissions(administrator=True)
    async def aipl_webhook(self, ctx, url: str):
        """
        Define o webhook de log.

        📌 POR QUE WEBHOOK?
        É 3-4x mais rápido que mandar via bot. Útil em raids
        quando o bot já tá ocupado com outras coisas.
        """
        if not url.startswith("https://discord.com/api/webhooks/"):
            return await ctx.send(embed=embed_error(
                "❌ URL de webhook inválida. Use o formato:\n"
                "`https://discord.com/api/webhooks/...`"))

        AntiIPLoggerManager.update(ctx.guild.id, "log_webhook", url)

        # Testa o webhook.
        try:
            wh = discord.Webhook.from_url(url, client=None)
            await wh.send(embed=discord.Embed(
                title="✅ Webhook configurado",
                description="Logs do Anti IP Logger vão aparecer aqui.",
                color=discord.Color.green(),
                timestamp=_now()))
            await ctx.send(embed=embed_success(
                "✅ Webhook salvo e testado!"))
        except Exception as e:
            await ctx.send(embed=embed_warning(
                f"⚠️ Webhook salvo, mas teste falhou: `{e}`"))

    # ─── Whitelist ───

    @antiiplogger.command(name="whitelist", aliases=["wl"])
    @commands.has_permissions(administrator=True)
    async def aipl_whitelist(self, ctx, alvo: str):
        """
        Adiciona um user ou cargo à whitelist.
        Whitelist = ignoram a proteção (não são punidos).
        """
        # É cargo?
        if alvo.startswith("<@&") and alvo.endswith(">"):
            rid = int(alvo[3:-1])
            cfg = AntiIPLoggerManager.get_config(ctx.guild.id)
            roles = list(cfg.get("whitelist_roles", []))
            if rid not in roles:
                roles.append(rid)
                AntiIPLoggerManager.update(
                    ctx.guild.id, "whitelist_roles", roles)
            return await ctx.send(embed=embed_success(
                f"✅ <@&{rid}> na whitelist."))

        # É user?
        if alvo.startswith("<@") and alvo.endswith(">"):
            uid = int(alvo[2:-1].split("!")[-1])
            cfg = AntiIPLoggerManager.get_config(ctx.guild.id)
            users = list(cfg.get("whitelist_users", []))
            if uid not in users:
                users.append(uid)
                AntiIPLoggerManager.update(
                    ctx.guild.id, "whitelist_users", users)
            return await ctx.send(embed=embed_success(
                f"✅ <@{uid}> na whitelist."))

        await ctx.send(embed=embed_error(
            "❌ Use: `>antiiplogger whitelist @user` ou `@cargo`"))

    @antiiplogger.command(name="unwhitelist", aliases=["unwl"])
    @commands.has_permissions(administrator=True)
    async def aipl_unwhitelist(self, ctx, alvo: str):
        """Remove da whitelist."""
        # É cargo?
        if alvo.startswith("<@&") and alvo.endswith(">"):
            rid = int(alvo[3:-1])
            cfg = AntiIPLoggerManager.get_config(ctx.guild.id)
            roles = [r for r in cfg.get("whitelist_roles", []) if r != rid]
            AntiIPLoggerManager.update(
                ctx.guild.id, "whitelist_roles", roles)
            return await ctx.send(embed=embed_success(
                f"✅ <@&{rid}> removido da whitelist."))

        # É user?
        if alvo.startswith("<@") and alvo.endswith(">"):
            uid = int(alvo[2:-1].split("!")[-1])
            cfg = AntiIPLoggerManager.get_config(ctx.guild.id)
            users = [u for u in cfg.get("whitelist_users", []) if u != uid]
            AntiIPLoggerManager.update(
                ctx.guild.id, "whitelist_users", users)
            return await ctx.send(embed=embed_success(
                f"✅ <@{uid}> removido da whitelist."))

        await ctx.send(embed=embed_error(
            "❌ Use: `>antiiplogger unwhitelist @user` ou `@cargo`"))

    # ─── Ignorar staff ───

    @antiiplogger.command(name="staff", aliases=["ignorestaff"])
    @commands.has_permissions(administrator=True)
    async def aipl_staff(self, ctx, state: str):
        """
        Liga/desliga ignorar staff (quem tem "manage_messages").
        Se ligado, staff NÃO é punida por mandar link.
        """
        enabled = state.lower() in ("on", "true", "1", "sim")
        AntiIPLoggerManager.update(ctx.guild.id, "ignore_staff", enabled)
        await ctx.send(embed=embed_success(
            f"✅ Ignorar staff: {'ON' if enabled else 'OFF'}"))

    # ─── Notificar owner ───

    @antiiplogger.command(name="notify")
    @commands.has_permissions(administrator=True)
    async def aipl_notify(self, ctx, state: str):
        """Liga/desliga DM pro dono quando detecta."""
        enabled = state.lower() in ("on", "true", "1", "sim")
        AntiIPLoggerManager.update(ctx.guild.id, "notify_owner", enabled)
        await ctx.send(embed=embed_success(
            f"✅ Notificar owner: {'ON' if enabled else 'OFF'}"))

    # ─── Estatísticas ───

    @antiiplogger.command(name="stats", aliases=["stat"])
    @commands.has_permissions(administrator=True)
    async def aipl_stats(self, ctx):
        """Mostra estatísticas do Anti IP Logger."""
        db = get_connection()

        # Total de detecções.
        total = db["antilink_infractions"].count_documents(
            {"guild_id": ctx.guild.id})

        # Últimas 24h.
        cutoff = _now() - timedelta(hours=24)
        ultimas_24h = db["antilink_infractions"].count_documents({
            "guild_id": ctx.guild.id,
            "timestamp": {"$gte": cutoff},
        })

        # Agrupa por domínio (top 5).
        por_dominio = list(db["antilink_infractions"].aggregate([
            {"$match": {"guild_id": ctx.guild.id}},
            {"$group": {"_id": "$domain", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 5},
        ]))

        # Agrupa por user (top 5).
        por_user = list(db["antilink_infractions"].aggregate([
            {"$match": {"guild_id": ctx.guild.id}},
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 5},
        ]))

        embed = discord.Embed(
            title="📊 Anti IP Logger — Stats",
            color=discord.Color.red(),
            timestamp=_now())

        embed.add_field(
            name="📈 Totais",
            value=(
                f"**Total:** `{total}`\n"
                f"**Últimas 24h:** `{ultimas_24h}`"
            ),
            inline=True)

        if por_dominio:
            linhas = [f"• `{d['_id']}` → {d['count']}"
                       for d in por_dominio]
            embed.add_field(
                name="🌐 Top Domínios",
                value="\n".join(linhas),
                inline=True)

        if por_user:
            linhas = [f"• <@{u['_id']}> → {u['count']}"
                       for u in por_user]
            embed.add_field(
                name="👥 Top Users",
                value="\n".join(linhas),
                inline=False)

        await ctx.send(embed=embed)

    # ─── Testar domínio ───

    @antiiplogger.command(name="test", aliases=["testar", "check"])
    @commands.has_permissions(administrator=True)
    async def aipl_test(self, ctx, *, url: str):
        """
        Testa se uma URL seria bloqueada.

        📌 ÚTIL PRA:
        - Testar se um domínio custom tá funcionando
        - Confirmar se um link específico é IP logger
        """
        cfg = AntiIPLoggerManager.get_config(ctx.guild.id)
        dominios = _extrair_dominios(url)

        if not dominios:
            return await ctx.send(embed=embed_error(
                "❌ Não achei nenhum domínio nessa URL."))

        bloqueados = AntiIPLoggerManager.get_all_domains(cfg)

        embed = discord.Embed(
            title="🔍 Teste de URL",
            description=f"**URL:** `{url}`",
            color=discord.Color.blue(),
            timestamp=_now())

        for d in dominios:
            is_blocked = _eh_ip_logger(d, bloqueados)
            emoji = "🚫 **BLOQUEADO**" if is_blocked else "✅ **Liberado**"
            embed.add_field(name=f"Domínio: `{d}`", value=emoji,
                             inline=False)

        await ctx.send(embed=embed)


# ============================================================
# BLOCO 7 — SETUP
# ============================================================

async def setup(bot):
    """
    Chamado pelo main.py pra carregar esse cog.

    📌 COMO FUNCIONA:
    O main.py chama essa função dentro do setup_hook dele.
    A gente só adiciona o cog se ele ainda não existe.
    """
    if bot.get_cog("AntiIPLoggerCommands") is None:
        await bot.add_cog(AntiIPLoggerCommands(bot))