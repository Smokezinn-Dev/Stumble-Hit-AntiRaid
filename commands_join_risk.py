# ============================================================
# COMMANDS_JOIN_RISK.PY - v1.0
# SCORE DE RISCO NO JOIN (ADITIVO)
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Calcula um SCORE DE RISCO (0-100) pra cada novo membro que
# entra no servidor, baseado em várias características:
#   • Idade da conta (contas novas são suspeitas)
#   • Tem avatar? (sem avatar = suspeito)
#   • Nome suspeito? (caracteres especiais, zalgo, etc)
#   • Está em lista de suspeitos de raid?
#   • Frequência de joins recentes no servidor
#   • Padrões de raid detectados
#
# 📌 IMPORTANTE: ADITIVO, NÃO SUBSTITUI
# Este cog NÃO substitui o sistema de raid do commands_antinuke.py.
# Ele roda EM PARALELO, como uma segunda camada de defesa.
#
# Isso é intencional — o sistema antigo (baseado em heurísticas
# determinísticas) já parou uma raid profissional de um grupo
# conhecido. Não queremos mexer nele. Adicionamos só mais uma
# camada por cima.
#
# 📌 COMO FUNCIONA A AÇÃO POR FAIXA?
# O score vai de 0 a 100. Configuramos 3 faixas:
#   • Faixa 1 (70-84):  log         → só avisa no log
#   • Faixa 2 (85-94):  kick        → expulsa automaticamente
#   • Faixa 3 (95-100): ban         → bane automaticamente
#
# Cada faixa pode ter sua ação:
#   log / kick / ban / timeout / none
#
# 📌 EXEMPLO PRÁTICO:
# Alguém entra com:
#   • Conta criada há 2 horas (score +30)
#   • Sem avatar (score +15)
#   • Nome com caracteres invisíveis (score +25)
#   • Já reportado em raid recente (score +20)
#   Total: 90 → cai na faixa 2 (kick)
#
# 📌 COMO USAR?
# >joinrisk                       → Painel
# >joinrisk on/off                → Ativar/desativar
# >joinrisk threshold 70          → Limite pra "avisar"
# >joinrisk action 85 kick        → Ação na faixa 85-94
# >joinrisk action 95 ban         → Ação na faixa 95-100
# >joinrisk log #canal            → Canal de log
# >joinrisk stats                 → Últimos scores
# ============================================================

# ────────────────────────────────────────────────────────────
# BLOCO 1 — IMPORTS
# ────────────────────────────────────────────────────────────

# "re" = regex (buscar padrões em texto).
# Usado pra detectar nomes suspeitos.
import re

# "time" = medir tempo (rate limits, cooldowns).
import time

# "datetime" = trabalhar com datas.
# "timezone" = pra usar UTC.
# "timedelta" = diferença entre datas (idade de conta, etc).
from datetime import datetime, timedelta, timezone

# "typing" = documentação de tipos.
from typing import Dict, List, Optional, Tuple

# "discord" = biblioteca principal.
import discord

# "commands" = criar comandos e cogs.
# "tasks" = tarefas em loop (background).
from discord.ext import commands, tasks

# "defaultdict/deque" = dicionário com padrão + fila rápida.
# Usado pra rastrear joins recentes (detecção de mass join).
from collections import defaultdict, deque

# ─── Imports internos ───

# database = conexão MongoDB.
from config import ANTIRAID_ENABLED
from database import get_connection

# embeds = caixas coloridas prontas.
from embeds import embed_success, embed_error, embed_warning, embed_info

# embeds_professional = log profissional.
from embeds_professional import ProfessionalLog, fmt_reason

# utils = helpers (cache, etc).
from utils import TTLCache
from protection_safety import automatic_action_allowed


def _now() -> datetime:
    """
    Retorna a data/hora atual em UTC.

    📌 POR QUE UTC?
    Padrão do Discord. Evita bugs de fuso horário.
    """
    return datetime.now(timezone.utc)


# ============================================================
# BLOCO 2 — CONFIGURAÇÃO PADRÃO
# ============================================================

DEFAULT_CONFIG = {
    "enabled": True,

    # ─── Limites das faixas ───
    # Faixa 1 (avisar): score >= threshold1 e < threshold2
    # Faixa 2 (kick):   score >= threshold2 e < threshold3
    # Faixa 3 (ban):    score >= threshold3
    "threshold1": 70,      # avisar
    "threshold2": 85,      # kick
    "threshold3": 95,      # ban

    # ─── Ação por faixa ───
    # Opções: log / kick / ban / timeout / none
    "action1": "log",      # faixa 1: só avisa
    "action2": "kick",     # faixa 2: kicka
    "action3": "ban",      # faixa 3: bana

    # ─── Pontos por característica ───
    # Cada característica adiciona X pontos ao score.
    "points": {
        "account_age_1h": 40,       # conta criada há <1h
        "account_age_1d": 30,       # conta criada há <24h
        "account_age_7d": 15,       # conta criada há <7d
        "no_avatar": 15,             # sem avatar
        "suspicious_name": 25,       # nome com caractere suspeito
        "default_avatar": 10,        # avatar padrão do Discord
        "no_roles": 5,               # entrou sem nenhum cargo
        "recent_raid": 30,           # servidor teve raid recente
        "mass_join": 20,             # muitos joins em pouco tempo
        "in_suspect_list": 35,       # já tá em lista de suspeitos
    },

    # ─── Janela de tempo ───
    "mass_join_window": 15,           # segundos pra considerar "mass join"
    "mass_join_threshold": 5,          # joins necessários pra "mass join"
    "recent_raid_window_minutes": 30,  # janela pra considerar "raid recente"

    # ─── Limpeza ───
    # Quanto tempo guardar os logs de score (dias).
    "log_retention_days": 30,

    # ─── Canal de log ───
    "log_channel": 0,

    # ─── Webhook ───
    "log_webhook": "",

    # ─── Notificações ───
    "notify_owner": False,
    "notify_on_critical": True,   # só notifica owner em score 95+

    # ─── Whitelist ───
    # Users/cargos que ignoram o score (ex: bots parceiros, VIPs).
    "whitelist_users": [],
    "whitelist_roles": [],

    # ─── Ignorar admins ───
    "ignore_admins": True,

    # ─── Modo de teste ───
    # Se True, mostra o score no log mas NÃO aplica ação.
    # Útil pra calibrar os thresholds antes de ativar.
    "dry_run": True,  # inicia em observação; staff precisa habilitar ações automáticas após calibrar
}


# ============================================================
# BLOCO 3 — MANAGER (config no MongoDB)
# ============================================================

class JoinRiskManager:
    # Cache de configs.
    _cache = TTLCache(max_size=200, ttl=60)

    @classmethod
    def get_config(cls, guild_id: int) -> dict:
        """Pega a config do servidor (cache OU banco)."""
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached

        db = get_connection()
        doc = db["join_risk_config"].find_one({"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}

        # Merge dos "points" (o doc pode ter só parte).
        if "points" in doc:
            cfg["points"] = {**DEFAULT_CONFIG["points"], **doc["points"]}

        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    def get_config_fresh(cls, guild_id: int) -> dict:
        db = get_connection()
        doc = db["join_risk_config"].find_one({"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}
        if "points" in doc:
            cfg["points"] = {**DEFAULT_CONFIG["points"], **doc["points"]}
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    def update(cls, guild_id: int, key: str, value):
        """Salva uma config no banco."""
        db = get_connection()
        db["join_risk_config"].update_one(
            {"guild_id": guild_id},
            {"$set": {key: value}},
            upsert=True,
        )
        cls._cache.invalidate(guild_id)

    @classmethod
    def invalidate(cls, guild_id: int):
        """Força recarregar na próxima consulta."""
        cls._cache.invalidate(guild_id)


# ============================================================
# BLOCO 4 — HELPERS (funções auxiliares)
# ============================================================

# Regex que detecta caracteres suspeitos em nomes.
# Inclui: invisíveis (zero-width), RTL/LTR overrides, zalgo.
_SUSPICIOUS_NAME_RX = re.compile(
    r"[\u200B-\u200F\u202A-\u202E\u2060-\u206F\uFEFF\u0300-\u036f]"
)


def _is_whitelisted(cfg: dict, user_id: int,
                     member: Optional[discord.Member]) -> bool:
    """
    Diz se o user tá na whitelist.

    📌 ORDEM:
    1. ID direto
    2. Algum cargo dele
    3. Se ignore_admins e ele é admin
    """
    if user_id in cfg.get("whitelist_users", []):
        return True

    if member:
        wl_roles = set(cfg.get("whitelist_roles", []))
        if any(r.id in wl_roles for r in member.roles):
            return True

        if cfg.get("ignore_admins", True):
            if member.guild_permissions.administrator:
                return True

    return False


def _has_suspicious_name(name: str) -> bool:
    """Diz se um nome tem caracteres suspeitos."""
    if not name:
        return False
    return bool(_SUSPICIOUS_NAME_RX.search(name))


# ============================================================
# BLOCO 5 — COG PRINCIPAL
# ============================================================

class JoinRiskCommands(commands.Cog):
    """
    Score de Risco no Join — ADITIVO ao sistema de raid existente.

    📌 IMPORTANTE:
    Este cog NÃO modifica o commands_antinuke.py. Ele escuta o
    mesmo evento (on_member_join) e roda EM PARALELO.
    """

    def __init__(self, bot):
        self.bot = bot

        # ─── Rastreio de joins recentes (pra mass join) ───
        # Estrutura: {guild_id: deque[(timestamp, user_id)]}
        self._joins: Dict[int, deque] = defaultdict(
            lambda: deque(maxlen=100)
        )

        # ─── Cache em memória dos últimos scores ───
        # Estrutura: {guild_id: deque[(user_id, score, timestamp)]}
        self._recent_scores: Dict[int, deque] = defaultdict(
            lambda: deque(maxlen=50)
        )

        # ─── Task de limpeza (roda a cada 1h) ───
        self._cleanup_task.start()

    def cog_unload(self):
        """Cancela a task ao descarregar."""
        try:
            self._cleanup_task.cancel()
        except Exception:
            pass

    # ============================================================
    # TASK: LIMPEZA PERIÓDICA
    # ============================================================

    @tasks.loop(hours=1)
    async def _cleanup_task(self):
        """
        Roda a cada hora. Limpa logs antigos do banco.

        📌 POR QUE?
        Mesmo com TTL configurado no database.py, essa task é
        uma segunda garantia e mantém o banco leve.
        """
        try:
            db = get_connection()
            cutoff = _now() - timedelta(days=30)
            db["join_risk_logs"].delete_many({
                "timestamp": {"$lt": cutoff},
            })
        except Exception:
            pass

    @_cleanup_task.before_loop
    async def _before_cleanup(self):
        await self.bot.wait_until_ready()

    # ============================================================
    # CÁLCULO DO SCORE
    # ============================================================

    async def _calcular_score(self, member: discord.Member,
                                cfg: dict) -> Tuple[int, List[dict]]:
        """
        Calcula o score de risco (0-100) de um novo membro.

        📌 RETORNA:
        Uma tupla (score, motivos) onde:
        - score: int de 0 a 100
        - motivos: lista de dicts {"motivo": str, "pontos": int}

        📌 COMO FUNCIONA:
        Cada característica suspeita adiciona X pontos. No final,
        o total é limitado a 100 (cap).
        """
        points_cfg = cfg.get("points", DEFAULT_CONFIG["points"])
        motivos = []
        total = 0

        now = _now()

        # ─── 1. Idade da conta ───
        if member.created_at:
            # created_at pode vir "naive" em algumas versões.
            created = member.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)

            idade = now - created
            horas = idade.total_seconds() / 3600

            if horas < 1:
                pts = points_cfg.get("account_age_1h", 40)
                total += pts
                motivos.append({
                    "motivo": f"Conta criada há {int(idade.total_seconds() // 60)}min",
                    "pontos": pts,
                })
            elif horas < 24:
                pts = points_cfg.get("account_age_1d", 30)
                total += pts
                motivos.append({
                    "motivo": f"Conta criada há {int(horas)}h",
                    "pontos": pts,
                })
            elif idade.days < 7:
                pts = points_cfg.get("account_age_7d", 15)
                total += pts
                motivos.append({
                    "motivo": f"Conta criada há {idade.days}d",
                    "pontos": pts,
                })

        # ─── 2. Avatar ───
        # Sem avatar (nem global nem de servidor).
        if not member.avatar and not member.guild_avatar:
            # Se tem avatar default (aquele padrão do Discord).
            pts = points_cfg.get("no_avatar", 15)
            total += pts
            motivos.append({
                "motivo": "Sem avatar",
                "pontos": pts,
            })

        # ─── 3. Nome suspeito ───
        if _has_suspicious_name(member.name):
            pts = points_cfg.get("suspicious_name", 25)
            total += pts
            motivos.append({
                "motivo": "Nome com caracteres suspeitos",
                "pontos": pts,
            })
        elif member.nick and _has_suspicious_name(member.nick):
            pts = points_cfg.get("suspicious_name", 25)
            total += pts
            motivos.append({
                "motivo": "Apelido com caracteres suspeitos",
                "pontos": pts,
            })

        # ─── 4. Mass join ───
        now_ts = time.time()
        dq = self._joins[member.guild.id]

        # Adiciona esse join.
        dq.append((now_ts, member.id))

        # Remove joins antigos.
        window = float(cfg.get("mass_join_window", 15))
        while dq and now_ts - dq[0][0] > window:
            dq.popleft()

        # Conta joins na janela.
        threshold = int(cfg.get("mass_join_threshold", 5))
        if len(dq) >= threshold:
            pts = points_cfg.get("mass_join", 20)
            total += pts
            motivos.append({
                "motivo": f"Mass join ({len(dq)} joins em {int(window)}s)",
                "pontos": pts,
            })

        # ─── 5. Raid recente ───
        try:
            db = get_connection()
            cutoff = now - timedelta(
                minutes=int(cfg.get("recent_raid_window_minutes", 30)))
            raid_recente = db["antinuke_logs"].find_one({
                "guild_id": member.guild.id,
                "action_type": {"$in": ["ban", "kick", "chan_del",
                                         "role_del", "bot_add"]},
                "timestamp": {"$gte": cutoff},
            })
            if raid_recente:
                pts = points_cfg.get("recent_raid", 30)
                total += pts
                motivos.append({
                    "motivo": "Servidor teve raid recente",
                    "pontos": pts,
                })
        except Exception:
            pass

        # ─── 6. Suspeitos recentes (raid_suspects) ───
        try:
            from commands_raid_suspects import RaidSuspectsManager
            # Aqui a gente chama o manager pra saber se o user tá
            # em algum log recente de suspeitas.
            db = get_connection()
            cutoff = now - timedelta(minutes=30)
            suspeita = db["raid_suspects_log"].find_one({
                "guild_id": member.guild.id,
                "user_id": member.id,
                "timestamp": {"$gte": cutoff},
            })
            if suspeita:
                pts = points_cfg.get("in_suspect_list", 35)
                total += pts
                motivos.append({
                    "motivo": "Estava em lista de suspeitos",
                    "pontos": pts,
                })
        except Exception:
            pass

        # ─── Cap final em 100 ───
        score_final = min(100, total)

        return score_final, motivos

    # ============================================================
    # LISTENER: JOIN
    # ============================================================

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """
        Chamado quando alguém entra no servidor.

        📌 FLUXO:
        1. Ignora bots, se sistema tá off, ou whitelist
        2. Calcula o score
        3. Determina a faixa de ação
        4. Aplica a ação (log/kick/ban/timeout)
        5. Salva no banco + envia log
        """
        # ─── Filtros rápidos ───
        if member.bot or not ANTIRAID_ENABLED:
            return

        cfg = JoinRiskManager.get_config(member.guild.id)
        if not cfg.get("enabled"):
            return

        if _is_whitelisted(cfg, member.id, member):
            return

        # ─── Calcula score ───
        score, motivos = await self._calcular_score(member, cfg)

        # ─── Determina faixa e ação ───
        threshold1 = int(cfg.get("threshold1", 70))
        threshold2 = int(cfg.get("threshold2", 85))
        threshold3 = int(cfg.get("threshold3", 95))

        faixa = None
        acao = "none"

        if score >= threshold3:
            faixa = "3"
            acao = cfg.get("action3", "ban")
        elif score >= threshold2:
            faixa = "2"
            acao = cfg.get("action2", "kick")
        elif score >= threshold1:
            faixa = "1"
            acao = cfg.get("action1", "log")
        else:
            # Score baixo, tudo bem.
            # Só salva no cache pra estatísticas.
            self._recent_scores[member.guild.id].append(
                (member.id, score, time.time()))
            return

        # ─── Guarda em cache ───
        self._recent_scores[member.guild.id].append(
            (member.id, score, time.time()))

        # ─── Modo dry_run: só loga, não age ───
        if cfg.get("dry_run"):
            await self._salvar_log(member, score, motivos, faixa, "dry_run")
            await self._enviar_log(
                member, score, motivos, faixa, "dry_run (não aplicado)",
                cfg)
            return

        # Revalida config local, toggle global e whitelist imediatamente
        # antes da punição; evita ação atrasada com cache antigo.
        try:
            cfg = JoinRiskManager.get_config_fresh(member.guild.id)
        except Exception:
            return
        if (not cfg.get("enabled", False)
                or cfg.get("dry_run", False)
                or _is_whitelisted(cfg, member.id, member)
                or not await automatic_action_allowed(member.guild.id, member)
                or member.guild.get_member(member.id) is None):
            return

        # A configuração pode ter mudado enquanto o score era calculado.
        # Recalcula a faixa e a ação com os valores atuais (não reutiliza `acao`).
        threshold1 = int(cfg.get("threshold1", 70))
        threshold2 = int(cfg.get("threshold2", 85))
        threshold3 = int(cfg.get("threshold3", 95))
        if score >= threshold3:
            faixa, acao = "3", cfg.get("action3", "ban")
        elif score >= threshold2:
            faixa, acao = "2", cfg.get("action2", "kick")
        elif score >= threshold1:
            faixa, acao = "1", cfg.get("action1", "log")
        else:
            return
        if acao not in {"log", "kick", "ban", "timeout", "none"}:
            acao = "log"

        # ─── Aplica a ação ───
        resultado = None
        try:
            if acao == "kick":
                await member.kick(
                    reason=f"Join Risk: score {score}/100")
                resultado = "kick"
            elif acao == "ban":
                await member.ban(
                    reason=f"Join Risk: score {score}/100",
                    delete_message_days=1)
                resultado = "ban"
            elif acao == "timeout":
                # Timeout de 10 minutos pra conta suspeita.
                until = discord.utils.utcnow() + timedelta(minutes=10)
                await member.timeout(
                    until, reason=f"Join Risk: score {score}/100")
                resultado = "timeout"
            elif acao == "log":
                resultado = "log"
            else:
                resultado = acao
        except Exception as e:
            resultado = f"falha ({e})"

        # ─── Salva log no banco ───
        await self._salvar_log(member, score, motivos, faixa, resultado)

        # ─── Envia log pro canal ───
        await self._enviar_log(member, score, motivos, faixa,
                                 resultado, cfg)

        # ─── Notifica owner se for crítico ───
        if cfg.get("notify_owner") and cfg.get("notify_on_critical"):
            if score >= threshold3:
                await self._notify_owner(member, score, motivos, cfg)

    async def _salvar_log(self, member: discord.Member, score: int,
                            motivos: List[dict], faixa: str,
                            resultado: str):
        """Salva o log do score no banco."""
        try:
            db = get_connection()
            db["join_risk_logs"].insert_one({
                "guild_id": member.guild.id,
                "user_id": member.id,
                "username": str(member),
                "score": score,
                "motivos": motivos,
                "faixa": faixa,
                "resultado": resultado,
                "timestamp": _now(),
            })
        except Exception:
            pass

    # ============================================================
    # LOG
    # ============================================================

    async def _enviar_log(self, member: discord.Member, score: int,
                            motivos: List[dict], faixa: str,
                            resultado: str, cfg: dict):
        """Envia o log pro canal configurado."""
        # Severidade baseada no score.
        if score >= 95:
            severity = "critical"
        elif score >= 85:
            severity = "high"
        elif score >= 70:
            severity = "medium"
        else:
            severity = "low"

        titulo = f"📊 Join Risk: {score}/100"
        descricao = (
            f"Um novo membro foi analisado pelo sistema de score "
            f"de risco e caiu na **faixa {faixa}**."
        )

        log = ProfessionalLog(
            title=titulo,
            description=descricao,
            severity=severity,
            footer="Stumble Hit AntiRaid • Join Risk Score")

        log.set_target(member)
        log.add_field("🎯 Score", f"`{score}/100`")
        log.add_field("📂 Faixa", f"`{faixa}`")
        log.add_field("⚖️ Resultado", f"`{resultado}`")

        # Motivos.
        if motivos:
            linhas = []
            for m in motivos[:10]:
                linhas.append(f"• {m['motivo']} (+{m['pontos']})")
            log.add_field("🔍 Motivos", "\n".join(linhas), inline=False)

        # Conta da criação.
        if member.created_at:
            created = member.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            idade = _now() - created
            log.add_field(
                "📅 Idade da Conta",
                f"<t:{int(created.timestamp())}:R> ({idade.days}d)",
                inline=True)

        embed = log.build()

        # 1. Webhook (mais rápido).
        wh_url = cfg.get("log_webhook", "")
        if wh_url:
            try:
                wh = discord.Webhook.from_url(wh_url, client=None)
                await wh.send(embed=embed,
                              username="📊 Join Risk", wait=False)
                return
            except Exception:
                pass

        # 2. Canal.
        log_ch = cfg.get("log_channel")
        if log_ch:
            channel = member.guild.get_channel(int(log_ch))
            if channel:
                try:
                    await channel.send(embed=embed)
                    return
                except Exception:
                    pass

        # 3. Fallback: canal do anti-nuke.
        try:
            from commands_antinuke import AntiNukeManager
            an_cfg = AntiNukeManager.get_config(member.guild.id)
            fallback = an_cfg.get("log_channel")
            if fallback:
                channel = member.guild.get_channel(int(fallback))
                if channel:
                    await channel.send(embed=embed)
        except Exception:
            pass

    async def _notify_owner(self, member: discord.Member, score: int,
                              motivos: List[dict], cfg: dict):
        """Manda DM pro dono."""
        try:
            owner = member.guild.owner or \
                    await member.guild.fetch_member(member.guild.id)
            await owner.send(embed=discord.Embed(
                title="📊 Join Risk Crítico",
                description=(
                    f"**Servidor:** {member.guild.name}\n"
                    f"**User:** {member.mention} (`{member.id}`)\n"
                    f"**Score:** `{score}/100`\n"
                    f"**Motivos:**\n"
                    + "\n".join(f"• {m['motivo']}" for m in motivos[:5])
                ),
                color=discord.Color.red(),
                timestamp=_now()))
        except Exception:
            pass

    # ============================================================
    # COMANDOS (grupo >joinrisk)
    # ============================================================

    @commands.group(name="joinrisk", aliases=["jr", "riscoscore"],
                    invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def joinrisk(self, ctx):
        """Painel principal do Score de Risco no Join."""
        cfg = JoinRiskManager.get_config(ctx.guild.id)

        def onoff(v):
            return "✅" if v else "❌"

        log_ch = cfg.get("log_channel")
        log_str = f"<#{log_ch}>" if log_ch else "❌"

        embed = discord.Embed(
            title="📊 Join Risk Score",
            description=(
                "Calcula um score de risco (0-100) pra cada novo "
                "membro baseado em características suspeitas.\n\n"
                "**Aditivo:** NÃO substitui o sistema de raid do "
                "anti-nuke. Roda em paralelo."
            ),
            color=discord.Color.purple(),
            timestamp=_now())

        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.add_field(
            name="📊 Configurações",
            value=(
                f"**Status:** {onoff(cfg['enabled'])}\n"
                f"**Dry run:** {onoff(cfg['dry_run'])}\n"
                f"**Log:** {log_str}"
            ),
            inline=True)

        embed.add_field(
            name="🎯 Faixas",
            value=(
                f"**1 ({cfg['threshold1']}-{cfg['threshold2']-1}):** "
                f"`{cfg['action1']}`\n"
                f"**2 ({cfg['threshold2']}-{cfg['threshold3']-1}):** "
                f"`{cfg['action2']}`\n"
                f"**3 ({cfg['threshold3']}+):** "
                f"`{cfg['action3']}`"
            ),
            inline=True)

        # Pontos.
        points = cfg.get("points", {})
        linhas = [f"• `{k}`: +{v}" for k, v in points.items()]
        embed.add_field(
            name="⚖️ Pontos por Característica",
            value="\n".join(linhas) or "*nenhum*",
            inline=False)

        embed.add_field(
            name="📌 Comandos",
            value=(
                "`>joinrisk on/off` — liga/desliga\n"
                "`>joinrisk dryrun on/off` — modo teste\n"
                "`>joinrisk threshold <1/2/3> <n>` — ajusta faixa\n"
                "`>joinrisk action <1/2/3> <acao>` — ação da faixa\n"
                "`>joinrisk points <carac> <n>` — pontos\n"
                "`>joinrisk log #canal` — canal de log\n"
                "`>joinrisk webhook <URL>` — webhook\n"
                "`>joinrisk whitelist @user` — whitelist\n"
                "`>joinrisk unwhitelist @user` — remove\n"
                "`>joinrisk notify on/off` — notificar owner\n"
                "`>joinrisk stats` — estatísticas\n"
                "`>joinrisk test @user` — testa score"
            ),
            inline=False)

        embed.set_footer(text="Stumble Hit AntiRaid • Join Risk Score v1.0")
        await ctx.send(embed=embed)

    # ─── Ligar/desligar ───

    @joinrisk.command(name="on")
    @commands.has_permissions(administrator=True)
    async def jr_on(self, ctx):
        """Ativa o Join Risk Score."""
        JoinRiskManager.update(ctx.guild.id, "enabled", True)
        await ctx.send(embed=embed_success(
            "✅ Join Risk Score **ativado**!"))

    @joinrisk.command(name="off")
    @commands.has_permissions(administrator=True)
    async def jr_off(self, ctx):
        """Desativa o Join Risk Score."""
        JoinRiskManager.update(ctx.guild.id, "enabled", False)
        await ctx.send(embed=embed_success(
            "✅ Join Risk Score **desativado**!"))

    # ─── Dry run ───

    @joinrisk.command(name="dryrun", aliases=["testmode"])
    @commands.has_permissions(administrator=True)
    async def jr_dryrun(self, ctx, state: str):
        """
        Modo teste: mostra o score no log mas NÃO aplica ação.

        📌 ÚTIL PRA:
        - Calibrar thresholds antes de ativar
        - Ver se algum membro legítimo tá sendo pego
        """
        enabled = state.lower() in ("on", "true", "1", "sim")
        JoinRiskManager.update(ctx.guild.id, "dry_run", enabled)
        msg = ("logs serão enviados mas ação NÃO aplicada"
               if enabled else "ações aplicadas normalmente")
        await ctx.send(embed=embed_success(
            f"✅ Dry run: {msg}"))

    # ─── Threshold ───

    @joinrisk.command(name="threshold", aliases=["limite"])
    @commands.has_permissions(administrator=True)
    async def jr_threshold(self, ctx, faixa: str, valor: int):
        """
        Ajusta o limite de uma faixa.

        📌 EXEMPLO:
        >joinrisk threshold 1 70    → faixa 1 começa em 70
        >joinrisk threshold 2 85    → faixa 2 começa em 85
        >joinrisk threshold 3 95    → faixa 3 começa em 95
        """
        if faixa not in ("1", "2", "3"):
            return await ctx.send(embed=embed_error(
                "❌ Use: `1`, `2` ou `3`"))

        if valor < 0 or valor > 100:
            return await ctx.send(embed=embed_error(
                "❌ Use entre 0 e 100."))

        # Valida ordem.
        cfg = JoinRiskManager.get_config(ctx.guild.id)
        t1 = int(cfg.get("threshold1", 70))
        t2 = int(cfg.get("threshold2", 85))
        t3 = int(cfg.get("threshold3", 95))

        if faixa == "1":
            if valor >= t2:
                return await ctx.send(embed=embed_error(
                    f"❌ Faixa 1 deve ser < faixa 2 ({t2})."))
        elif faixa == "2":
            if valor <= t1:
                return await ctx.send(embed=embed_error(
                    f"❌ Faixa 2 deve ser > faixa 1 ({t1})."))
            if valor >= t3:
                return await ctx.send(embed=embed_error(
                    f"❌ Faixa 2 deve ser < faixa 3 ({t3})."))
        elif faixa == "3":
            if valor <= t2:
                return await ctx.send(embed=embed_error(
                    f"❌ Faixa 3 deve ser > faixa 2 ({t2})."))

        JoinRiskManager.update(ctx.guild.id, f"threshold{faixa}", valor)
        await ctx.send(embed=embed_success(
            f"✅ Faixa {faixa}: `{valor}`"))

    # ─── Action ───

    @joinrisk.command(name="action", aliases=["acao"])
    @commands.has_permissions(administrator=True)
    async def jr_action(self, ctx, faixa: str, acao: str):
        """
        Ajusta a ação de uma faixa.

        📌 AÇÕES:
        - log      → só avisa no log
        - kick     → expulsa
        - ban      → bane
        - timeout  → timeout 10min
        - none     → não faz nada
        """
        if faixa not in ("1", "2", "3"):
            return await ctx.send(embed=embed_error(
                "❌ Use: `1`, `2` ou `3`"))

        acao = acao.lower().strip()
        validos = ("log", "kick", "ban", "timeout", "none")
        if acao not in validos:
            return await ctx.send(embed=embed_error(
                f"❌ Use um dos: {', '.join(f'`{v}`' for v in validos)}"))

        JoinRiskManager.update(ctx.guild.id, f"action{faixa}", acao)
        await ctx.send(embed=embed_success(
            f"✅ Faixa {faixa} → `{acao}`"))

    # ─── Points ───

    @joinrisk.command(name="points", aliases=["pontos"])
    @commands.has_permissions(administrator=True)
    async def jr_points(self, ctx, caracteristica: str, valor: int):
        """
        Ajusta os pontos de uma característica.

        📌 CARACTERÍSTICAS DISPONÍVEIS:
        - account_age_1h
        - account_age_1d
        - account_age_7d
        - no_avatar
        - suspicious_name
        - default_avatar
        - no_roles
        - recent_raid
        - mass_join
        - in_suspect_list
        """
        if valor < 0 or valor > 100:
            return await ctx.send(embed=embed_error(
                "❌ Use entre 0 e 100."))

        cfg = JoinRiskManager.get_config(ctx.guild.id)
        points = dict(cfg.get("points", {}))

        if caracteristica not in points:
            return await ctx.send(embed=embed_error(
                f"❌ Característica inválida.\n"
                f"Use: `{', '.join(points.keys())}`"))

        points[caracteristica] = valor
        JoinRiskManager.update(ctx.guild.id, "points", points)
        await ctx.send(embed=embed_success(
            f"✅ `{caracteristica}` = `{valor}` pontos"))

    # ─── Log ───

    @joinrisk.command(name="log")
    @commands.has_permissions(administrator=True)
    async def jr_log(self, ctx, canal: discord.TextChannel):
        """Define o canal de log."""
        JoinRiskManager.update(ctx.guild.id, "log_channel", canal.id)
        await ctx.send(embed=embed_success(f"✅ Log em {canal.mention}"))

    # ─── Webhook ───

    @joinrisk.command(name="webhook")
    @commands.has_permissions(administrator=True)
    async def jr_webhook(self, ctx, url: str):
        """Define o webhook de log."""
        if not url.startswith("https://discord.com/api/webhooks/"):
            return await ctx.send(embed=embed_error(
                "❌ URL de webhook inválida."))

        JoinRiskManager.update(ctx.guild.id, "log_webhook", url)

        try:
            wh = discord.Webhook.from_url(url, client=None)
            await wh.send(embed=discord.Embed(
                title="✅ Webhook configurado",
                description="Logs do Join Risk vão aparecer aqui.",
                color=discord.Color.green(),
                timestamp=_now()))
            await ctx.send(embed=embed_success("✅ Webhook salvo!"))
        except Exception as e:
            await ctx.send(embed=embed_warning(
                f"⚠️ Webhook salvo, mas teste falhou: `{e}`"))

    # ─── Whitelist ───

    @joinrisk.command(name="whitelist", aliases=["wl"])
    @commands.has_permissions(administrator=True)
    async def jr_whitelist(self, ctx, alvo: str):
        """Adiciona user/cargo à whitelist."""
        # Cargo?
        if alvo.startswith("<@&") and alvo.endswith(">"):
            rid = int(alvo[3:-1])
            cfg = JoinRiskManager.get_config(ctx.guild.id)
            roles = list(cfg.get("whitelist_roles", []))
            if rid not in roles:
                roles.append(rid)
                JoinRiskManager.update(ctx.guild.id, "whitelist_roles", roles)
            return await ctx.send(embed=embed_success(
                f"✅ <@&{rid}> na whitelist."))

        # User?
        if alvo.startswith("<@") and alvo.endswith(">"):
            uid = int(alvo[2:-1].split("!")[-1])
            cfg = JoinRiskManager.get_config(ctx.guild.id)
            users = list(cfg.get("whitelist_users", []))
            if uid not in users:
                users.append(uid)
                JoinRiskManager.update(ctx.guild.id, "whitelist_users", users)
            return await ctx.send(embed=embed_success(
                f"✅ <@{uid}> na whitelist."))

        await ctx.send(embed=embed_error(
            "❌ Use: `>joinrisk whitelist @user` ou `@cargo`"))

    @joinrisk.command(name="unwhitelist", aliases=["unwl"])
    @commands.has_permissions(administrator=True)
    async def jr_unwhitelist(self, ctx, alvo: str):
        """Remove da whitelist."""
        # Cargo?
        if alvo.startswith("<@&") and alvo.endswith(">"):
            rid = int(alvo[3:-1])
            cfg = JoinRiskManager.get_config(ctx.guild.id)
            roles = [r for r in cfg.get("whitelist_roles", []) if r != rid]
            JoinRiskManager.update(ctx.guild.id, "whitelist_roles", roles)
            return await ctx.send(embed=embed_success(
                f"✅ <@&{rid}> removido."))

        # User?
        if alvo.startswith("<@") and alvo.endswith(">"):
            uid = int(alvo[2:-1].split("!")[-1])
            cfg = JoinRiskManager.get_config(ctx.guild.id)
            users = [u for u in cfg.get("whitelist_users", []) if u != uid]
            JoinRiskManager.update(ctx.guild.id, "whitelist_users", users)
            return await ctx.send(embed=embed_success(
                f"✅ <@{uid}> removido."))

        await ctx.send(embed=embed_error(
            "❌ Use: `>joinrisk unwhitelist @user` ou `@cargo`"))

    # ─── Notify ───

    @joinrisk.command(name="notify")
    @commands.has_permissions(administrator=True)
    async def jr_notify(self, ctx, state: str):
        """Liga/desliga DM pro dono quando score for crítico (95+)."""
        enabled = state.lower() in ("on", "true", "1", "sim")
        JoinRiskManager.update(ctx.guild.id, "notify_owner", enabled)
        await ctx.send(embed=embed_success(
            f"✅ Notificar owner: {'ON' if enabled else 'OFF'}"))

    # ─── Estatísticas ───

    @joinrisk.command(name="stats", aliases=["stat"])
    @commands.has_permissions(administrator=True)
    async def jr_stats(self, ctx):
        """Estatísticas do Join Risk."""
        try:
            db = get_connection()

            total = db["join_risk_logs"].count_documents({
                "guild_id": ctx.guild.id,
            })

            # Últimas 24h.
            cutoff = _now() - timedelta(hours=24)
            ultimas_24h = db["join_risk_logs"].count_documents({
                "guild_id": ctx.guild.id,
                "timestamp": {"$gte": cutoff},
            })

            # Por faixa.
            por_faixa = list(db["join_risk_logs"].aggregate([
                {"$match": {"guild_id": ctx.guild.id}},
                {"$group": {"_id": "$faixa", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
            ]))

            # Score médio.
            scores = list(db["join_risk_logs"].aggregate([
                {"$match": {"guild_id": ctx.guild.id}},
                {"$group": {"_id": None, "avg": {"$avg": "$score"},
                            "max": {"$max": "$score"}}},
            ]))
            stats = scores[0] if scores else {}

        except Exception:
            total = 0
            ultimas_24h = 0
            por_faixa = []
            stats = {}

        embed = discord.Embed(
            title="📊 Join Risk — Stats",
            color=discord.Color.purple(),
            timestamp=_now())

        embed.add_field(
            name="📈 Totais",
            value=(
                f"**Total:** `{total}`\n"
                f"**Últimas 24h:** `{ultimas_24h}`\n"
                f"**Score médio:** `{stats.get('avg', 0):.1f}`\n"
                f"**Score máximo:** `{stats.get('max', 0)}`"
            ),
            inline=True)

        if por_faixa:
            linhas = [f"• Faixa `{f['_id']}`: `{f['count']}`"
                       for f in por_faixa]
            embed.add_field(
                name="📂 Por Faixa",
                value="\n".join(linhas),
                inline=True)

        # Últimos scores em cache.
        ultimos = list(self._recent_scores.get(ctx.guild.id, []))[-10:]
        if ultimos:
            linhas = []
            for uid, score, ts in reversed(ultimos):
                ago = int(time.time() - ts)
                linhas.append(f"• <@{uid}> → `{score}` (há {ago}s)")
            embed.add_field(
                name="🕒 Últimos Scores",
                value="\n".join(linhas),
                inline=False)

        await ctx.send(embed=embed)

    # ─── Testar score ───

    @joinrisk.command(name="test", aliases=["testar", "check"])
    @commands.has_permissions(administrator=True)
    async def jr_test(self, ctx, user: discord.Member):
        """
        Calcula o score de um user SEM aplicar ação.

        📌 ÚTIL PRA:
        - Calibrar thresholds
        - Ver o que tá disparando o score
        """
        cfg = JoinRiskManager.get_config(ctx.guild.id)
        score, motivos = await self._calcular_score(user, cfg)

        threshold1 = int(cfg.get("threshold1", 70))
        threshold2 = int(cfg.get("threshold2", 85))
        threshold3 = int(cfg.get("threshold3", 95))

        if score >= threshold3:
            faixa = "3"
            acao = cfg.get("action3", "ban")
        elif score >= threshold2:
            faixa = "2"
            acao = cfg.get("action2", "kick")
        elif score >= threshold1:
            faixa = "1"
            acao = cfg.get("action1", "log")
        else:
            faixa = "nenhuma (score baixo)"
            acao = "nenhuma"

        embed = discord.Embed(
            title=f"📊 Score de {user.display_name}",
            description=f"**Score: {score}/100**",
            color=discord.Color.purple(),
            timestamp=_now())

        embed.set_thumbnail(url=user.display_avatar.url)

        embed.add_field(name="📂 Faixa", value=f"`{faixa}`", inline=True)
        embed.add_field(name="⚖️ Ação", value=f"`{acao}`", inline=True)

        if motivos:
            linhas = [f"• {m['motivo']} (+{m['pontos']})"
                       for m in motivos]
            embed.add_field(
                name="🔍 Motivos",
                value="\n".join(linhas),
                inline=False)
        else:
            embed.add_field(
                name="🔍 Motivos",
                value="*Score baixo, nenhum motivo crítico.*",
                inline=False)

        await ctx.send(embed=embed)


# ============================================================
# BLOCO 6 — SETUP
# ============================================================

async def setup(bot):
    """
    Chamado pelo main.py pra carregar esse cog.
    """
    if bot.get_cog("JoinRiskCommands") is None:
        await bot.add_cog(JoinRiskCommands(bot))