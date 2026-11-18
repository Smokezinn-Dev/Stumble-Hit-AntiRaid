# ============================================================
# COMMANDS_ANTIESCALATION.PY - v10.0
# SISTEMA DE ANTI-ESCALADA DE PRIVILÉGIOS
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Fica de olho em TODA mudança de cargo no servidor e bloqueia
# se alguém tentar "subir de cargo" sem permissão. Ex:
#   • Alguém dando Admin pra si mesmo
#   • Alguém dando cargo perigoso sem permissão
#   • Alguém editando cargo pra adicionar permissão perigosa
#   • Um BOT dando Admin pra alguém (ou pra ele mesmo)
#
# v10.0 ADICIONA:
# - Regra: bot dando admin pra si/membro/bot (block_bot_admin_role)
# - Regra: alguém dando admin pra bot (block_admin_grant_to_bot)
# ============================================================

# asyncio = roda várias coisas ao mesmo tempo (assíncrono)
import asyncio
# time = lidar com tempo (timestamps, medir duração)
import time
# defaultdict/deque = dicionário com valor padrão + fila rápida
from collections import defaultdict, deque
# datetime/timedelta/timezone = trabalhar com datas/horas
from datetime import datetime, timedelta, timezone
# typing = só documenta os tipos (não muda comportamento)
from typing import Dict, List, Optional, Set

# discord = biblioteca principal
import discord
# commands = parte que cria comandos com prefixo e cogs
from discord.ext import commands

# ANTINUKE_ENABLED = flag global do config.py
from config import ANTINUKE_ENABLED
# Funções do database.py
# Motor: operações de banco aguardadas para não bloquear o event loop do Discord.
from database import get_connection_async, log_privilege_change, create_privilege_alert
# Embeds coloridos prontos
from embeds import embed_success, embed_error, embed_warning, embed_info
# Construtor de log "bonito" + formatador de motivo
from embeds_professional import ProfessionalLog, fmt_reason
# Cache com TTL (memória temporária)
from utils import TTLCache
from protection_safety import automatic_action_allowed


def _now() -> datetime:
    """Retorna a hora atual em UTC (fuso mundial)."""
    return datetime.now(timezone.utc)


# ============================================================
# CONFIGURAÇÃO PADRÃO
# ============================================================
# Todos os valores que podem ser ajustados pelo admin.
# True = ligado, False = desligado.

DEFAULT_CONFIG = {
    "enabled": True,                     # liga/desliga sistema
    "user_protection_enabled": True,     # protege usuários (não só bots)?
    "block_self_promotion": True,        # bloquear auto-promoção
    "block_privileged_role_grant": True, # bloquear dar cargo perigoso
    "block_role_creation_dangerous": True, # bloquear criar cargo perigoso
    "block_role_edit_escalation": True,  # bloquear editar cargo elevando
    "block_everyone_edit": True,         # bloquear editar @everyone
    "block_position_abuse": True,        # bloquear mover cargo acima
    "block_manage_roles_delegation": True, # bloquear delegar manage_roles
    "block_bot_bypass": True,            # bloquear bots burlando
    "block_cascade": True,               # bloquear cascata
    # 🆕 v10.0
    "block_bot_admin_role": True,        # bloquear bot dando admin
    "block_admin_grant_to_bot": True,    # bloquear dar admin pra bot

    # Permissões consideradas PERIGOSAS
    "dangerous_permissions": [
        "administrator", "ban_members", "kick_members",
        "manage_guild", "manage_roles", "manage_channels",
        "manage_webhooks", "manage_emojis_and_stickers",
        "manage_messages", "mention_everyone", "moderate_members",
    ],
    "protected_roles": [],               # cargos protegidos (por ID)
    "protected_roles_by_name": [         # cargos protegidos (por nome)
        "admin", "administrador", "owner", "dono",
        "mod", "moderador", "staff",
    ],
    "cascade_threshold": 3,              # N elos pra ser cascata
    "cascade_window": 30,                # janela em segundos
    "role_edit_threshold": 3,            # N edições = suspeito
    "role_edit_window": 30,
    "mass_grant_threshold": 5,           # N grants = massa
    "mass_grant_window": 15,
    "punishment": "revert",              # revert/strip/ban/kick
    "auto_revert": True,                 # desfaz a ação?
    "punish_cooldown": 60,               # cooldown entre punições
    "log_channel": 0,                    # ID do canal de log
    "log_webhook": "",                   # webhook pra logs
    "notify_owner": True,                # DM pro dono?
    "whitelist_users": [],               # IDs de users imunes
    "whitelist_roles": [],               # IDs de cargos imunes
    "repeat_offender_threshold": 3,
    "repeat_offender_window": 3600,
}


# ============================================================
# SCORE DE RISCO (0-100) POR TIPO DE AÇÃO
# ============================================================
# Quanto maior o número, mais grave é a ação.

ACTION_RISK = {
    "self_promotion": 95,               # auto-promoção
    "privilege_escalation": 90,         # tentativa de subir
    "cascade": 100,                     # cascata
    "manage_roles_delegation": 85,      # delegar manage_roles
    "bot_bypass": 95,                   # bot burlando
    "role_edit_escalation": 70,         # editar cargo elevando
    "position_abuse": 60,               # mover cargo acima
    "mass_grant": 75,                   # dar cargo em massa
    "privileged_role_grant": 65,        # dar cargo perigoso
    "role_creation_dangerous": 40,      # criar cargo perigoso
    "everyone_edit": 50,                # editar @everyone
    # 🆕 v10.0
    "bot_admin_role": 100,              # bot dando admin
    "admin_grant_to_bot": 95,           # dar admin pra bot
}


def _compute_risk_score(action: str, details: dict = None,
                        repeat_count: int = 0) -> int:
    """
    Calcula o score de risco (0-100) de uma ação.
    Soma: base + bônus de reincidência + bônus de permissão perigosa.
    """
    # Pega o score base da ação.
    base = ACTION_RISK.get(action, 50)
    # Bônus por reincidência: +5 cada, máximo +20.
    repeat_bonus = min(repeat_count * 5, 20)

    # Bônus por tipo de permissão ganha.
    perm_bonus = 0
    if details:
        perms = details.get("gained") or details.get("role") or ""
        if isinstance(perms, str):
            if "administrator" in perms.lower():
                perm_bonus += 15
            elif "manage_roles" in perms.lower():
                perm_bonus += 10
            elif "manage_guild" in perms.lower():
                perm_bonus += 8

    # Limita a 100 no máximo.
    return min(100, base + repeat_bonus + perm_bonus)


def _severity_from_score(score: int) -> str:
    """Converte score (0-100) em texto de severidade."""
    if score >= 90:
        return "critical"
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


# ============================================================
# MANAGER (gerencia config no MongoDB)
# ============================================================

class AntiEscalationManager:
    # Cache de configs (evita ir no banco toda hora).
    _cache = TTLCache(max_size=200, ttl=60)

    @classmethod
    async def get_config(cls, guild_id: int) -> dict:
        """Pega a configuração do cache ou consulta o MongoDB via Motor.

        O retorno mantém o merge entre DEFAULT_CONFIG e os valores salvos;
        o cache só é preenchido depois que a leitura assíncrona termina.
        """
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached
        db = await get_connection_async()
        doc = await db["antiescalation_config"].find_one({"guild_id": guild_id}) or {}
        # Junta padrão + salvo no banco (o salvo sobrescreve).
        cfg = {**DEFAULT_CONFIG, **doc}
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def get_config_fresh(cls, guild_id: int) -> dict:
        # Ignora o valor antigo do cache e relê o documento diretamente no banco.
        db = await get_connection_async()
        doc = await db["antiescalation_config"].find_one({"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def update(cls, guild_id: int, key: str, value):
        """Aguarda a gravação e só então invalida o cache do servidor."""
        db = await get_connection_async()
        await db["antiescalation_config"].update_one(
            {"guild_id": guild_id}, {"$set": {key: value}}, upsert=True)
        cls._cache.invalidate(guild_id)

    @classmethod
    def invalidate(cls, guild_id: int):
        """Força recarregar a config na próxima consulta."""
        cls._cache.invalidate(guild_id)


# ============================================================
# HELPERS (funções pequenas usadas em vários lugares)
# ============================================================

def _role_has_dangerous_perms(role: discord.Role, cfg: dict) -> bool:
    """
    Verifica se um cargo tem ALGUMA permissão da lista perigosa.
    Ex: role.permissions.administrator = True → perigoso.
    """
    perms = role.permissions
    for p in cfg.get("dangerous_permissions", []):
        # getattr(perms, "administrator", False) pega o atributo.
        if getattr(perms, p, False):
            return True
    return False


def _role_has_administrator(role: discord.Role) -> bool:
    """Verifica especificamente se tem permissão administrator."""
    return role.permissions.administrator


def _is_protected_role(role: discord.Role, cfg: dict) -> bool:
    """Diz se um cargo é 'protegido' (por ID ou por nome)."""
    # Por ID.
    if role.id in cfg.get("protected_roles", []):
        return True
    # Por nome (case-insensitive).
    name_lower = role.name.lower().strip()
    for protected_name in cfg.get("protected_roles_by_name", []):
        if protected_name.lower() in name_lower:
            return True
    return False


def _is_whitelisted(cfg: dict, user_id: int,
                    member: Optional[discord.Member]) -> bool:
    """Diz se um user está na whitelist (por ID ou cargo)."""
    if user_id in cfg.get("whitelist_users", []):
        return True
    if member:
        wl_roles = set(cfg.get("whitelist_roles", []))
        if any(r.id in wl_roles for r in member.roles):
            return True
    return False


# ============================================================
# LOGGER VIA WEBHOOK (mais rápido que mandar pelo bot)
# ============================================================

class _WebhookLogger:
    # Cache de webhooks já abertos (reutiliza conexão).
    _cache: Dict[str, discord.Webhook] = {}

    @classmethod
    async def send(cls, url: str, embed: discord.Embed,
                   username: str = "🛡️ AntiEscalation",
                   avatar_url: Optional[str] = None) -> bool:
        """Envia um embed via webhook. True = sucesso."""
        if not url:
            return False
        try:
            wh = cls._cache.get(url)
            if wh is None:
                # Cria webhook a partir da URL.
                wh = discord.Webhook.from_url(url, client=None)
                cls._cache[url] = wh
            # wait=False = não espera confirmação (mais rápido).
            await wh.send(embed=embed, username=username,
                          avatar_url=avatar_url, wait=False)
            return True
        except Exception:
            # Se falhar, remove do cache (tenta de novo na próxima).
            cls._cache.pop(url, None)
            return False


# ============================================================
# CASCADE TRACKER (detecta "cascatas" de grants)
# ============================================================
# 📌 O QUE É CASCATA?
# A dá cargo pra B, B dá cargo pra C, C dá cargo pra D...
# Isso é uma tentativa de tomar conta do servidor em cadeia.
# Detectamos montando um "grafo" e procurando caminhos longos.

class CascadeTracker:
    def __init__(self):
        # Guarda todos os grants: {guild: {promotor: fila[(alvo, tempo)]}}
        self._grants: Dict[int, Dict[int, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=50))
        )
        # Cooldowns (evita punir várias vezes).
        self._cooldown: Dict[int, Dict[int, float]] = defaultdict(dict)
        # Grafo: {guild: {A: {B, C}}} = "A deu cargo pra B e C".
        self._graph: Dict[int, Dict[int, Set[int]]] = defaultdict(
            lambda: defaultdict(set)
        )
        # Grafo reverso (útil pra buscar pra trás).
        self._reverse: Dict[int, Dict[int, Set[int]]] = defaultdict(
            lambda: defaultdict(set)
        )
        # Strikes (reincidência).
        self._strikes: Dict[int, Dict[int, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=20))
        )

    def register_grant(self, guild_id: int, promoter_id: int,
                       target_id: int, window: float) -> int:
        """
        Registra um grant e devolve QUANTOS aconteceram na janela.
        Se passar de X, considera "grant em massa".
        """
        dq = self._grants[guild_id][promoter_id]
        now = time.time()
        # Remove entradas antigas.
        while dq and now - dq[0][1] > window:
            dq.popleft()
        dq.append((target_id, now))
        return len(dq)

    def add_chain_link(self, guild_id: int, promoter_id: int, target_id: int):
        """Adiciona uma "aresta" no grafo: promotor → alvo."""
        self._graph[guild_id][promoter_id].add(target_id)
        self._reverse[guild_id][target_id].add(promoter_id)

    def detect_cascade_bfs(self, guild_id: int, start_id: int,
                            threshold: int,
                            max_depth: int = 8) -> Optional[List[int]]:
        """
        Procura o maior caminho de grants a partir de start_id.
        Usa BFS (busca em largura).
        Retorna a lista do caminho se passou do threshold.
        """
        graph = self._graph[guild_id]
        best_path = [start_id]

        # Fila: (nó_atual, caminho_até_aqui).
        queue = deque([(start_id, [start_id])])
        visited = set()

        while queue:
            current, path = queue.popleft()

            # Se já visitamos e não é o começo, pula.
            if current in visited and len(path) > 1:
                continue
            visited.add(current)

            # Atualiza o "melhor" caminho se for maior.
            if len(path) > len(best_path):
                best_path = path

            # Não passa do limite de profundidade.
            if len(path) >= max_depth:
                continue

            # Visita cada "próximo" do nó atual.
            for nxt in graph.get(current, set()):
                # Evita ciclos.
                if nxt not in path:
                    queue.append((nxt, path + [nxt]))

        # Se o caminho passou do threshold, é cascata!
        if len(best_path) >= threshold:
            return best_path
        return None

    def register_strike(self, guild_id: int, user_id: int):
        """Registra que o user foi pego (pra contar reincidência)."""
        self._strikes[guild_id][user_id].append(time.time())

    def count_recent_strikes(self, guild_id: int, user_id: int,
                              window: float = 3600) -> int:
        """Conta quantas vezes foi pego na última hora."""
        dq = self._strikes[guild_id][user_id]
        now = time.time()
        # Remove antigas.
        while dq and now - dq[0] > window:
            dq.popleft()
        return len(dq)

    def is_on_cooldown(self, guild_id: int, user_id: int,
                        cooldown: float) -> bool:
        """Diz se ainda está em cooldown (não pode ser punido agora)."""
        last = self._cooldown[guild_id].get(user_id, 0)
        return (time.time() - last) < cooldown

    def set_cooldown(self, guild_id: int, user_id: int):
        """Marca que o user foi punido agora."""
        self._cooldown[guild_id][user_id] = time.time()

    def clear_user(self, guild_id: int, user_id: int):
        """Limpa os dados de um user (após punição)."""
        self._grants[guild_id].pop(user_id, None)
        self._graph[guild_id].pop(user_id, None)
        self._reverse[guild_id].pop(user_id, None)


# ============================================================
# COG (módulo principal)
# ============================================================

class AntiEscalationCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._tracker = CascadeTracker()  # rastreador de cascatas

    # ============================================================
    # ENVIO DE LOG
    # ============================================================

    async def _send_log(self, guild: discord.Guild, cfg: dict,
                        embed: discord.Embed):
        """
        Manda um log. Tenta na ordem:
        1. Webhook (se configurado)
        2. Canal configurado (log_channel)
        3. Fallback: canal do LogsManager
        """
        # 1. Webhook.
        wh_url = cfg.get("log_webhook", "")
        if wh_url:
            sent = await _WebhookLogger.send(
                wh_url, embed,
                username="🛡️ AntiEscalation",
                avatar_url=guild.me.display_avatar.url if guild.me else None)
            if sent:
                return

        # 2. Canal configurado.
        log_ch = cfg.get("log_channel")
        if log_ch:
            channel = guild.get_channel(int(log_ch))
            if channel:
                try:
                    await channel.send(embed=embed)
                    return
                except Exception:
                    pass

        # 3. Fallback (pega do LogsManager se existir).
        try:
            from commands_logs import LogsManager
            logs_cfg = await LogsManager.get_config(guild.id)
            ctx_channels = logs_cfg.get("context_channels", {})
            fallback_id = (ctx_channels.get("antinuke")
                           or ctx_channels.get("antiescalation")
                           or logs_cfg.get("channel_id"))
            if fallback_id:
                ch = guild.get_channel(int(fallback_id))
                if ch:
                    try:
                        await ch.send(embed=embed)
                    except Exception:
                        pass
        except Exception:
            pass

    async def _notify_owner(self, guild: discord.Guild, cfg: dict,
                            title: str, description: str):
        """Manda DM pro dono do servidor."""
        if not cfg.get("notify_owner"):
            return
        try:
            owner = guild.owner or await guild.fetch_member(guild.owner_id)
            await owner.send(embed=discord.Embed(
                title=title, description=description,
                color=discord.Color.dark_red(), timestamp=_now()))
        except Exception:
            pass

    def _color(self, severity: str) -> discord.Color:
        """Cor do embed conforme severidade."""
        return {
            "low": discord.Color.yellow(),
            "medium": discord.Color.orange(),
            "high": discord.Color.red(),
            "critical": discord.Color.dark_red(),
        }.get(severity, discord.Color.orange())

    # ============================================================
    # REVERT (desfazer ações)
    # ============================================================

    async def _revert_role_add(self, member, role, reason):
        """Remove um cargo que foi adicionado indevidamente."""
        try:
            if role in member.roles:
                await member.remove_roles(role,
                                           reason=f"AntiEsc revert: {reason}")
        except Exception:
            pass

    async def _revert_role_create(self, role, reason):
        """Deleta um cargo criado indevidamente."""
        try:
            await role.delete(reason=f"AntiEsc revert: {reason}")
        except Exception:
            pass

    async def _revert_role_edit(self, role, original_perms, reason):
        """Restaura as permissões originais de um cargo."""
        try:
            await role.edit(permissions=original_perms,
                            reason=f"AntiEsc revert: {reason}")
        except Exception:
            pass

    async def _revert_role_position(self, role, original_position, reason):
        """Restaura a posição original de um cargo."""
        try:
            await role.edit(position=original_position,
                            reason=f"AntiEsc revert: {reason}")
        except Exception:
            pass

    # ============================================================
    # PUNIÇÃO (aplica a punição configurada)
    # ============================================================

    async def _punish(self, guild, user_id, action, reason, cfg,
                       details=None, chain=None):
        """
        Aplica a punição configurada (revert/strip/ban/kick).
        Também:
        - Calcula score de risco
        - Registra o strike
        - Cria alerta no banco
        - Envia log profissional
        - Notifica o dono
        """
        member = guild.get_member(user_id)
        try:
            cfg = await AntiEscalationManager.get_config_fresh(guild.id)
        except Exception:
            return
        if not cfg.get("enabled", False):
            return
        if member:
            if (not member.bot
                    and not cfg.get("user_protection_enabled", True)):
                return
            if _is_whitelisted(cfg, user_id, member):
                return
            if not await automatic_action_allowed(guild.id, member):
                return
        punishment = cfg.get("punishment", "revert")

        # Conta reincidência.
        repeat = self._tracker.count_recent_strikes(
            guild.id, user_id,
            float(cfg.get("repeat_offender_window", 3600)))
        # Calcula o score.
        score = _compute_risk_score(action, details, repeat)
        severity = _severity_from_score(score)

        # ─── Aplica a punição (se não for "revert") ───
        if member and punishment != "revert":
            try:
                if punishment == "strip":
                    # Remove TODOS os cargos perigosos.
                    dangerous = [
                        r for r in member.roles
                        if not r.is_default()
                        and _role_has_dangerous_perms(r, cfg)
                    ]
                    if dangerous:
                        await member.remove_roles(
                            *dangerous, reason=f"AntiEsc: {reason}")
                elif punishment == "ban":
                    await guild.ban(member,
                                     reason=f"AntiEsc: {reason}",
                                     delete_message_days=1)
                elif punishment == "kick":
                    await member.kick(reason=f"AntiEsc: {reason}")
            except Exception:
                pass

        # Registra cooldown e strike.
        self._tracker.set_cooldown(guild.id, user_id)
        self._tracker.register_strike(guild.id, user_id)

        # Cria alerta no banco.
        create_privilege_alert(
            guild.id, user_id, severity, reason,
            {**(details or {}), "risk_score": score,
             "repeat_count": repeat, "chain": chain})

        # ─── Monta o log profissional ───
        log = ProfessionalLog(
            title=f"Anti-Escalation ({severity.upper()})",
            description=(
                "O sistema detectou uma tentativa de escalada de "
                "privilégios e aplicou as medidas de segurança "
                "automaticamente."
            ),
            severity=severity,
            footer="Stumble Hit AntiRaid • Anti-Escalation Ativo")

        log.set_executor(member)
        log.add_field("Ação", f"`{action}`")
        log.add_field("Risco", f"`{score}/100`")
        log.add_field("Reincidência", f"`{repeat}x`")
        log.add_field("Punição", f"`{punishment}`")

        # Se tem cascata, mostra a cadeia.
        if chain:
            log.add_field(
                "Cadeia Detectada",
                " → ".join(f"<@{u}>" for u in chain[:8]),
                inline=False)
        # Detalhes adicionais.
        if details:
            lines = [f"• **{k}:** `{v}`"
                     for k, v in list(details.items())[:6]]
            if lines:
                log.add_field("Detalhes", "\n".join(lines), inline=False)

        log.add_field("Motivo", fmt_reason(reason), inline=False)
        await self._send_log(guild, cfg, log.build())

        # Notifica o dono.
        await self._notify_owner(
            guild, cfg,
            "🚨 Anti-escalada acionada",
            f"**User:** <@{user_id}>\n**Ação:** `{action}`\n"
            f"**Risco:** `{score}/100`")

    async def _protection_still_allowed(self, guild, moderator_id: int) -> bool:
        """Revalida módulo, toggle humano e whitelist antes de cada revert."""
        try:
            cfg = await AntiEscalationManager.get_config_fresh(guild.id)
        except Exception:
            return False
        if not cfg.get("enabled", False):
            return False
        member = guild.get_member(moderator_id)
        if member is None:
            return False
        if not member.bot and not cfg.get("user_protection_enabled", True):
            return False
        if _is_whitelisted(cfg, moderator_id, member):
            return False
        return await automatic_action_allowed(guild.id, member)

    # ============================================================
    # REGRAS EXISTENTES (as v9.0 que já funcionavam)
    # ============================================================

    async def _rule_self_promotion(self, guild, moderator_id, member,
                                    role, cfg) -> bool:
        """
        REGRA: alguém deu cargo perigoso PRA SI MESMO.
        Retorna True se bloqueou.
        """
        if not cfg.get("block_self_promotion"):
            return False
        # Só age se quem deu é a mesma pessoa que recebeu.
        if moderator_id != member.id:
            return False
        # Só se o cargo for perigoso.
        if not _role_has_dangerous_perms(role, cfg):
            return False
        reason = f"Auto-promoção: tentou dar {role.name} em si mesmo"
        # Desfaz.
        if cfg.get("auto_revert") and await self._protection_still_allowed(guild, moderator_id):
            await self._revert_role_add(member, role, reason)
        # Pune.
        await self._punish(guild, moderator_id, "self_promotion", reason,
                            cfg, {"role": role.name, "role_id": role.id})
        return True

    async def _rule_privileged_grant(self, guild, moderator_id, member,
                                      role, cfg) -> bool:
        """REGRA: alguém deu cargo perigoso sem ter permissão."""
        if not cfg.get("block_privileged_role_grant"):
            return False
        if not _role_has_dangerous_perms(role, cfg):
            return False

        mod = guild.get_member(moderator_id)
        if mod is None:
            return False
        # Owner pode tudo.
        if mod.id == guild.owner_id:
            return False
        # Admin só é bloqueado se o cargo for acima do top dele.
        if mod.guild_permissions.administrator:
            if role >= mod.top_role:
                reason = f"Deu cargo acima do próprio top: {role.name}"
                if cfg.get("auto_revert") and await self._protection_still_allowed(guild, moderator_id):
                    await self._revert_role_add(member, role, reason)
                await self._punish(guild, moderator_id,
                                    "privileged_role_grant",
                                    reason, cfg, {"role": role.name})
                return True
            return False

        # Não-admin dando cargo perigoso → bloqueia.
        reason = f"Sem permissão para dar cargo perigoso: {role.name}"
        if cfg.get("auto_revert") and await self._protection_still_allowed(guild, moderator_id):
            await self._revert_role_add(member, role, reason)
        await self._punish(guild, moderator_id, "privileged_role_grant",
                            reason, cfg,
                            {"role": role.name, "target": str(member)})
        return True

    async def _rule_mass_grant(self, guild, moderator_id, member,
                                role, cfg) -> bool:
        """REGRA: muita gente recebendo cargo do mesmo mod em pouco tempo."""
        window = float(cfg.get("mass_grant_window", 15))
        threshold = int(cfg.get("mass_grant_threshold", 5))
        # Registra e pega quantos tem na janela.
        count = self._tracker.register_grant(
            guild.id, moderator_id, member.id, window)
        if count >= threshold:
            self._tracker.clear_user(guild.id, moderator_id)
            reason = f"Grant em massa: {count} em {window}s"
            await self._punish(guild, moderator_id, "mass_grant", reason, cfg,
                                {"role": role.name, "count": count})
            return True
        return False

    async def _rule_role_creation(self, guild, moderator_id, role, cfg) -> bool:
        """REGRA: criou um cargo JÁ com permissão perigosa."""
        if not cfg.get("block_role_creation_dangerous"):
            return False
        if not _role_has_dangerous_perms(role, cfg):
            return False
        mod = guild.get_member(moderator_id)
        if mod is None:
            return False
        # Owner e admin podem.
        if moderator_id == guild.owner_id:
            return False
        if mod.guild_permissions.administrator:
            return False
        reason = f"Criou cargo com permissões perigosas: {role.name}"
        # Deleta o cargo.
        if cfg.get("auto_revert") and await self._protection_still_allowed(guild, moderator_id):
            await self._revert_role_create(role, reason)
        await self._punish(guild, moderator_id, "role_creation_dangerous",
                            reason, cfg,
                            {"role": role.name, "role_id": role.id})
        return True

    async def _rule_role_edit(self, guild, moderator_id, role,
                               before, after, cfg) -> bool:
        """REGRA: editou cargo ADICIONANDO permissão perigosa."""
        if not cfg.get("block_role_edit_escalation"):
            return False
        # Compara permissões antes/depois.
        before_obj = discord.Permissions(before.get("permissions", 0))
        after_obj = discord.Permissions(after.get("permissions", 0))
        gained = []
        for p in cfg.get("dangerous_permissions", []):
            # Se não tinha antes E tem agora = "ganhou" permissão.
            if (not getattr(before_obj, p, False)
                    and getattr(after_obj, p, False)):
                gained.append(p)
        if not gained:
            return False
        mod = guild.get_member(moderator_id)
        # Owner e admin podem.
        if mod and (mod.id == guild.owner_id
                    or mod.guild_permissions.administrator):
            return False
        reason = f"Editou cargo adicionando: {', '.join(gained)}"
        if cfg.get("auto_revert") and await self._protection_still_allowed(guild, moderator_id):
            await self._revert_role_edit(role, before_obj, reason)
        await self._punish(guild, moderator_id, "role_edit_escalation",
                            reason, cfg,
                            {"role": role.name,
                             "gained": ", ".join(gained)})
        return True

    async def _rule_everyone_edit(self, guild, moderator_id, role,
                                   before, after, cfg) -> bool:
        """REGRA: editou @everyone dando permissões perigosas."""
        if not cfg.get("block_everyone_edit"):
            return False
        # Só age se for o @everyone.
        if role.id != guild.default_role.id:
            return False
        before_perms = discord.Permissions(before.get("permissions", 0))
        after_perms = discord.Permissions(after.get("permissions", 0))
        gained = []
        for p in cfg.get("dangerous_permissions", []):
            if (not getattr(before_perms, p, False)
                    and getattr(after_perms, p, False)):
                gained.append(p)
        if not gained:
            return False
        mod = guild.get_member(moderator_id)
        if mod and (mod.id == guild.owner_id
                    or mod.guild_permissions.administrator):
            return False
        reason = f"Editou @everyone: {', '.join(gained)}"
        if cfg.get("auto_revert") and await self._protection_still_allowed(guild, moderator_id):
            try:
                await role.edit(permissions=before_perms,
                                reason=f"AntiEsc revert: {reason}")
            except Exception:
                pass
        await self._punish(guild, moderator_id, "everyone_edit", reason,
                            cfg, {"gained": ", ".join(gained)})
        return True

    async def _rule_position_abuse(self, guild, moderator_id, role,
                                    before, after, cfg) -> bool:
        """REGRA: moveu um cargo ACIMA do top dele."""
        if not cfg.get("block_position_abuse"):
            return False
        before_pos = before.get("position", 0)
        after_pos = after.get("position", 0)
        # Só age se a posição AUMENTOU.
        if after_pos <= before_pos:
            return False
        mod = guild.get_member(moderator_id)
        if mod is None:
            return False
        if mod.id == guild.owner_id or mod.guild_permissions.administrator:
            return False
        # Se moveu pra cima do top dele, é abuso.
        if after_pos >= mod.top_role.position:
            reason = (f"Movi {role.name} para {after_pos} "
                      f"(top: {mod.top_role.position})")
            if cfg.get("auto_revert") and await self._protection_still_allowed(guild, moderator_id):
                await self._revert_role_position(role, before_pos, reason)
            await self._punish(guild, moderator_id, "position_abuse", reason,
                                cfg, {"role": role.name,
                                      "before": before_pos,
                                      "after": after_pos})
            return True
        return False

    async def _rule_manage_roles_delegation(self, guild, moderator_id,
                                             member, role, cfg) -> bool:
        """REGRA: deu cargo que TEM manage_roles (delegação de poder)."""
        if not cfg.get("block_manage_roles_delegation"):
            return False
        if not role.permissions.manage_roles:
            return False
        mod = guild.get_member(moderator_id)
        if mod is None:
            return False
        if mod.id == guild.owner_id or mod.guild_permissions.administrator:
            return False
        reason = f"Delegou manage_roles via {role.name} para {member}"
        if cfg.get("auto_revert") and await self._protection_still_allowed(guild, moderator_id):
            await self._revert_role_add(member, role, reason)
        await self._punish(guild, moderator_id, "manage_roles_delegation",
                            reason, cfg,
                            {"role": role.name, "target": str(member)})
        return True

    async def _rule_cascade(self, guild, moderator_id, target_id, cfg) -> bool:
        """REGRA: detectou cascata (A→B→C→D...)."""
        if not cfg.get("block_cascade"):
            return False
        # Adiciona essa ligação no grafo.
        self._tracker.add_chain_link(guild.id, moderator_id, target_id)
        threshold = int(cfg.get("cascade_threshold", 3))
        # Procura caminho longo.
        chain = self._tracker.detect_cascade_bfs(
            guild.id, moderator_id, threshold)
        if chain:
            reason = f"Cascata detectada ({len(chain)} elos)"
            await self._punish(guild, moderator_id, "cascade", reason, cfg,
                                {"chain_len": len(chain)}, chain=chain)
            return True
        return False

    # ============================================================
    # 🆕 REGRAS v10.0 — ADMIN ROLE (bot dando/tirando admin)
    # ============================================================

    async def _rule_bot_admin_role(self, guild, moderator_id, member,
                                     role, cfg) -> bool:
        """
        REGRA: BOT deu cargo com permissão administrator pra alguém.
        Cobre:
          - Bot dando admin pra SI MESMO
          - Bot dando admin pra MEMBRO
          - Bot dando admin pra OUTRO BOT
        """
        if not cfg.get("block_bot_admin_role"):
            return False

        # Só age se quem DEU for bot.
        mod = guild.get_member(moderator_id)
        if not mod or not mod.bot:
            return False

        # Ignora o próprio bot do sistema.
        if moderator_id == self.bot.user.id:
            return False

        # Ignora owner (não é bot, mas por segurança).
        if moderator_id == guild.owner_id:
            return False

        # Whitelisted? Deixa passar.
        if _is_whitelisted(cfg, moderator_id, mod):
            return False

        # O cargo dado tem administrator? Se não, não é problema.
        if not _role_has_administrator(role):
            return False

        # ═══ DETECTADO! ═══
        # Descobre pra quem o bot deu o cargo.
        target_tipo = ("a si mesmo"
                       if moderator_id == member.id
                       else ("a outro bot" if member.bot
                             else "a um membro"))

        reason = (f"Bot deu cargo ADMIN ({role.name}) {target_tipo}")

        # Desfaz.
        if cfg.get("auto_revert") and await self._protection_still_allowed(guild, moderator_id):
            await self._revert_role_add(member, role, reason)

        # Pune o BOT.
        await self._punish(
            guild, moderator_id, "bot_admin_role", reason, cfg,
            {
                "bot": str(mod),
                "role": role.name,
                "role_id": role.id,
                "target": str(member),
                "target_id": member.id,
                "target_tipo": target_tipo,
            })

        return True

    async def _rule_admin_grant_to_bot(self, guild, moderator_id, member,
                                         role, cfg) -> bool:
        """
        REGRA: um USER deu cargo com administrator pra um BOT.
        Isso é grave porque o bot pode virar admin do servidor.
        """
        if not cfg.get("block_admin_grant_to_bot"):
            return False

        # Só age se o ALVO for bot.
        if not member.bot:
            return False

        # Ignora o próprio bot do sistema.
        if member.id == self.bot.user.id:
            return False

        # Cargo dado tem administrator? Se não, tudo bem.
        if not _role_has_administrator(role):
            return False

        # Quem deu é owner? Pode.
        if moderator_id == guild.owner_id:
            return False

        # Quem deu é whitelisted? Pode.
        mod = guild.get_member(moderator_id)
        if _is_whitelisted(cfg, moderator_id, mod):
            return False

        # Bot alvo é whitelisted no antinuke? Deixa passar (é de confiança).
        try:
            from commands_antinuke import AntiNukeManager
            an_cfg = AntiNukeManager.get_config(guild.id)
            if (member.id in an_cfg.get("whitelist_bots", [])
                    or member.id in an_cfg.get("whitelist_users", [])):
                return False
        except Exception:
            pass

        # ═══ DETECTADO! ═══
        reason = (f"Deu cargo ADMIN ({role.name}) para bot "
                  f"{member.name}")

        if cfg.get("auto_revert") and await self._protection_still_allowed(guild, moderator_id):
            await self._revert_role_add(member, role, reason)

        await self._punish(
            guild, moderator_id, "admin_grant_to_bot", reason, cfg,
            {
                "role": role.name,
                "role_id": role.id,
                "bot": str(member),
                "bot_id": member.id,
            })

        return True

    # ============================================================
    # LISTENER: AUDIT LOG (chamado pelo Discord quando algo muda)
    # ============================================================

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry: discord.AuditLogEntry):
        """
        Chamado quando QUALQUER coisa acontece no servidor.
        Aqui filtramos só o que interessa (role update, member_role_update).
        """
        if not entry.guild or entry.user_id is None:
            return
        # Ignora se o próprio bot fez.
        if entry.user_id == self.bot.user.id:
            return
        guild = entry.guild
        moderator_id = entry.user_id
        cfg = await AntiEscalationManager.get_config_fresh(guild.id)

        # Sistema desligado? Sai.
        if not cfg.get("enabled") or not ANTINUKE_ENABLED:
            return

        # Verifica se "user protection" está ligado.
        # Bots administradores continuam protegidos mesmo com OFF.
        mod_member = guild.get_member(moderator_id)
        is_bot_mod = bool(mod_member and mod_member.bot)

        if not is_bot_mod and not cfg.get("user_protection_enabled", True):
            return
        # A whitelist central (humanos e bots) deve ser respeitada antes
        # de qualquer revert/strip, não apenas dentro da função de punição.
        if mod_member and not await automatic_action_allowed(guild.id, mod_member):
            return

        # Whitelist local? Sai.
        if _is_whitelisted(cfg, moderator_id, mod_member):
            return
        # Cooldown? Sai.
        cooldown = float(cfg.get("punish_cooldown", 60))
        if self._tracker.is_on_cooldown(guild.id, moderator_id, cooldown):
            return

        # Atalho pra AuditLogAction (evita escrever por extenso).
        A = discord.AuditLogAction
        try:
            # Se foi mudança de cargo de um membro.
            if entry.action == A.member_role_update:
                await self._handle_member_role_update(
                    entry, guild, moderator_id, cfg)
            # Se foi criação de cargo.
            elif entry.action == A.role_create:
                role = entry.target
                if isinstance(role, discord.Role):
                    await self._rule_role_creation(
                        guild, moderator_id, role, cfg)
                    log_privilege_change(
                        guild.id, moderator_id, moderator_id,
                        "role_create", role.id, "role",
                        {"role_name": role.name})
            # Se foi edição de cargo.
            elif entry.action == A.role_update:
                await self._handle_role_update(
                    entry, guild, moderator_id, cfg)
        except Exception:
            pass

    async def _handle_member_role_update(self, entry, guild,
                                          moderator_id, cfg):
        """Processa uma mudança de cargo (member_role_update)."""
        target = entry.target
        if not isinstance(target, discord.Member):
            return

        # Pega os cargos antes e depois.
        changes = entry.changes
        before = changes.before.roles if hasattr(changes, "before") else []
        after = changes.after.roles if hasattr(changes, "after") else []
        # Converte em conjuntos de IDs.
        before_ids = ({r.id for r in before}
                      if isinstance(before, list) else set())
        after_ids = ({r.id for r in after}
                     if isinstance(after, list) else set())
        # Diferença = cargos ADICIONADOS.
        added = after_ids - before_ids

        # Pra cada cargo adicionado, checa TODAS as regras.
        for rid in added:
            role = guild.get_role(rid)
            if role is None:
                continue

            # 🆕 v10.0 — regras admin role rodam PRIMEIRO.
            if await self._rule_bot_admin_role(
                    guild, moderator_id, target, role, cfg):
                return
            if await self._rule_admin_grant_to_bot(
                    guild, moderator_id, target, role, cfg):
                return

            # Regras existentes.
            if await self._rule_self_promotion(
                    guild, moderator_id, target, role, cfg):
                return
            if await self._rule_privileged_grant(
                    guild, moderator_id, target, role, cfg):
                return
            if await self._rule_manage_roles_delegation(
                    guild, moderator_id, target, role, cfg):
                return
            if await self._rule_mass_grant(
                    guild, moderator_id, target, role, cfg):
                return
            if await self._rule_cascade(
                    guild, moderator_id, target.id, cfg):
                return

            # Se chegou aqui, tá tudo ok. Só registra no banco.
            log_privilege_change(
                guild.id, target.id, moderator_id,
                "role_add", role.id, "role",
                {"role_name": role.name})

    async def _handle_role_update(self, entry, guild, moderator_id, cfg):
        """Processa edição de cargo (role_update)."""
        role = entry.target
        if not isinstance(role, discord.Role):
            return

        # Monta dicts de antes/depois.
        changes = entry.changes
        before = {}
        after = {}
        for change in changes:
            attr = getattr(change, "attr", None)
            if attr == "permissions":
                # Permissões vêm em objeto, extrai o valor numérico.
                before["permissions"] = (
                    change.before.value
                    if hasattr(change.before, "value")
                    else int(change.before or 0))
                after["permissions"] = (
                    change.after.value
                    if hasattr(change.after, "value")
                    else int(change.after or 0))
            elif attr == "position":
                before["position"] = int(change.before or 0)
                after["position"] = int(change.after or 0)

        # Se mudou permissões, checa as regras.
        if "permissions" in before or "permissions" in after:
            if await self._rule_everyone_edit(
                    guild, moderator_id, role, before, after, cfg):
                return
            if await self._rule_role_edit(
                    guild, moderator_id, role, before, after, cfg):
                return
        # Se mudou posição, checa abuso.
        if "position" in before or "position" in after:
            if await self._rule_position_abuse(
                    guild, moderator_id, role, before, after, cfg):
                return

        # Registra no banco.
        log_privilege_change(
            guild.id, moderator_id, moderator_id,
            "role_update", role.id, "role",
            {"role_name": role.name, "before": before, "after": after})

    # ============================================================
    # COMANDOS (>antiescalation)
    # ============================================================

    @commands.command(name="antiescalation", aliases=["ae", "antipromo"])
    @commands.has_permissions(administrator=True)
    async def antiescalation(self, ctx, feature: str = None,
                              state: str = None, *, extra: str = None):
        """Comando principal. Sem arg = painel. Com arg = configurar."""
        cfg = await AntiEscalationManager.get_config(ctx.guild.id)

        # Sem argumento → mostra painel.
        if not feature:
            return await self._show_panel(ctx, cfg)
        # on/off.
        if feature == "on":
            await AntiEscalationManager.update(ctx.guild.id, "enabled", True)
            return await ctx.send(embed=embed_success(
                "✅ Anti-escalada **ativada**!"))
        if feature == "off":
            await AntiEscalationManager.update(ctx.guild.id, "enabled", False)
            return await ctx.send(embed=embed_success(
                "✅ Anti-escalada **desativada**!"))
        # punishment: revert/strip/ban/kick.
        if feature in ("punishment", "punicao"):
            if state not in ("revert", "strip", "ban", "kick"):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `revert`, `strip`, `ban`, `kick`"))
            await AntiEscalationManager.update(ctx.guild.id, "punishment", state)
            return await ctx.send(embed=embed_success(
                f"✅ Punição: `{state}`"))
        # autorevert: on/off.
        if feature in ("autorevert", "revert"):
            if state not in ("on", "off"):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `on` ou `off`"))
            await AntiEscalationManager.update(
                ctx.guild.id, "auto_revert", state == "on")
            return await ctx.send(embed=embed_success(
                f"✅ Auto-revert: `{state}`"))
        # log: define canal de log.
        if feature in ("log", "logchannel"):
            if not state or not state.startswith("<#"):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `>ae log #canal`"))
            # Extrai o ID de dentro de "<#123>".
            await AntiEscalationManager.update(
                ctx.guild.id, "log_channel", int(state[2:-1]))
            return await ctx.send(embed=embed_success(f"✅ Log em {state}"))
        # webhook: define URL.
        if feature == "webhook":
            if not state or not state.startswith(
                    "https://discord.com/api/webhooks/"):
                return await ctx.send(embed=embed_error("❌ URL inválida."))
            await AntiEscalationManager.update(ctx.guild.id, "log_webhook", state)
            return await ctx.send(embed=embed_success("✅ Webhook salvo!"))
        # protectrole: adiciona cargo protegido.
        if feature in ("protectrole", "protect"):
            if not state or not state.startswith("<@&"):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `>ae protectrole @cargo`"))
            rid = int(state[3:-1])
            protected = list(cfg.get("protected_roles", []))
            if rid not in protected:
                protected.append(rid)
                await AntiEscalationManager.update(
                    ctx.guild.id, "protected_roles", protected)
            return await ctx.send(embed=embed_success(
                f"✅ {state} protegido."))
        # unprotectrole: remove da proteção.
        if feature in ("unprotectrole", "unprotect"):
            if not state or not state.startswith("<@&"):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `>ae unprotectrole @cargo`"))
            rid = int(state[3:-1])
            protected = [r for r in cfg.get("protected_roles", [])
                         if r != rid]
            await AntiEscalationManager.update(
                ctx.guild.id, "protected_roles", protected)
            return await ctx.send(embed=embed_success(
                f"✅ {state} removido."))
        # whitelist.
        if feature == "whitelist":
            return await self._handle_whitelist(ctx, cfg, state, extra)
        # stats.
        if feature == "stats":
            return await self._show_stats(ctx)
        # cascadethreshold: N elos.
        if feature == "cascadethreshold":
            try:
                await AntiEscalationManager.update(
                    ctx.guild.id, "cascade_threshold", int(state))
                return await ctx.send(embed=embed_success(
                    f"✅ Cascade threshold: `{state}`"))
            except Exception:
                return await ctx.send(embed=embed_error(
                    "❌ Número inválido."))
        # massgrant: N grants.
        if feature == "massgrant":
            try:
                await AntiEscalationManager.update(
                    ctx.guild.id, "mass_grant_threshold", int(state))
                return await ctx.send(embed=embed_success(
                    f"✅ Mass grant: `{state}`"))
            except Exception:
                return await ctx.send(embed=embed_error(
                    "❌ Número inválido."))
        # risk: simula score.
        if feature == "risk":
            try:
                action = state
                score = _compute_risk_score(action, {})
                sev = _severity_from_score(score)
                return await ctx.send(embed=embed_info(
                    f"🎯 Ação `{action}`: score **{score}/100** "
                    f"(`{sev}`)"))
            except Exception:
                return await ctx.send(embed=embed_error(
                    "❌ Ação inválida."))

        # Lista de features booleanas (on/off).
        features = [
            "block_self_promotion", "block_privileged_role_grant",
            "block_role_creation_dangerous", "block_role_edit_escalation",
            "block_everyone_edit", "block_position_abuse",
            "block_manage_roles_delegation", "block_bot_bypass",
            "block_cascade",
            # 🆕 v10.0
            "block_bot_admin_role", "block_admin_grant_to_bot",
        ]
        if feature in features:
            if not state:
                return await ctx.send(embed=embed_error(
                    "❌ Use: `on` ou `off`"))
            enabled = state.lower() in ("on", "true", "1", "sim")
            await AntiEscalationManager.update(ctx.guild.id, feature, enabled)
            return await ctx.send(embed=embed_success(
                f"✅ `{feature}` {'ON' if enabled else 'OFF'}"))

        # Se não bateu em nada, avisa.
        return await ctx.send(embed=embed_error(
            "❌ Feature inválida. Use `>ae`"))

    async def _handle_whitelist(self, ctx, cfg, target, extra):
        """Gerencia whitelist (user/role/list)."""
        if not target:
            return await ctx.send(embed=embed_error(
                "❌ Use: `>ae whitelist user/role @x`"))
        # list: mostra tudo.
        if target.lower() == "list":
            embed = discord.Embed(
                title="📋 WHITELIST ANTI-ESCALADA",
                color=discord.Color.blurple())
            users = cfg.get("whitelist_users", [])
            roles = cfg.get("whitelist_roles", [])
            embed.add_field(
                name=f"👤 Users ({len(users)})",
                value="\n".join(f"• <@{u}>" for u in users[:15])
                or "*Nenhum*",
                inline=False)
            embed.add_field(
                name=f"👑 Roles ({len(roles)})",
                value="\n".join(f"• <@&{r}>" for r in roles[:15])
                or "*Nenhum*",
                inline=False)
            return await ctx.send(embed=embed)
        # user: adiciona user.
        if target.lower() in ("user", "u") and extra:
            if not (extra.startswith("<@") and extra.endswith(">")):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `user @user`"))
            # Extrai o ID (trata menção com "!" também).
            uid = int(extra[2:-1].split("!")[-1])
            wl = list(cfg.get("whitelist_users", []))
            if uid not in wl:
                wl.append(uid)
                await AntiEscalationManager.update(
                    ctx.guild.id, "whitelist_users", wl)
            return await ctx.send(embed=embed_success(
                f"✅ <@{uid}> na whitelist."))
        # role: adiciona cargo.
        if target.lower() in ("role", "r") and extra:
            if not (extra.startswith("<@&") and extra.endswith(">")):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `role @cargo`"))
            rid = int(extra[3:-1])
            wl = list(cfg.get("whitelist_roles", []))
            if rid not in wl:
                wl.append(rid)
                await AntiEscalationManager.update(
                    ctx.guild.id, "whitelist_roles", wl)
            return await ctx.send(embed=embed_success(
                f"✅ <@&{rid}> na whitelist."))
        return await ctx.send(embed=embed_error("❌ Use: `user/role @x`"))

    async def _show_stats(self, ctx):
        """Mostra estatísticas de alertas do anti-escalation."""
        db = await get_connection_async()
        # Conta alertas.
        total = await db["privilege_alerts"].count_documents(
            {"guild_id": ctx.guild.id})
        unresolved = await db["privilege_alerts"].count_documents(
            {"guild_id": ctx.guild.id, "resolved": False})
        # Agrupa por severidade (aggregate = pipeline do Mongo).
        by_sev = await db["privilege_alerts"].aggregate([
            {"$match": {"guild_id": ctx.guild.id}},
            {"$group": {"_id": "$severity", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}}
        ]).to_list(length=None)
        embed = discord.Embed(
            title="📊 ANTI-ESCALADA STATS",
            color=discord.Color.blurple())
        embed.add_field(name="Total alertas", value=str(total), inline=True)
        embed.add_field(name="Não resolvidos", value=str(unresolved),
                        inline=True)
        if by_sev:
            embed.add_field(
                name="Por severidade",
                value="\n".join(f"• `{x['_id']}`: {x['count']}"
                                 for x in by_sev),
                inline=False)
        await ctx.send(embed=embed)

    async def _show_panel(self, ctx, cfg):
        """Mostra o painel principal do >ae."""
        # Funçãozinha pra transformar True/False em ✅/❌.
        def onoff(v):
            return "✅" if v else "❌"

        user_prot = cfg.get("user_protection_enabled", True)

        embed = discord.Embed(
            title="🚨 ANTI-ESCALADA DE PRIVILÉGIOS v10.0",
            description=(
                f"**Status:** {onoff(cfg['enabled'])}\n"
                f"**Proteção contra usuários:** {onoff(user_prot)}\n"
                f"**Punição:** `{cfg['punishment']}`\n"
                f"**Auto-revert:** {onoff(cfg.get('auto_revert'))}\n"
                f"**Cascata (BFS):** {onoff(cfg.get('block_cascade'))}"
            ),
            color=discord.Color.dark_red(),
            timestamp=_now())
        embed.add_field(
            name="🛡️ Regras (Gerais)",
            value=(
                f"{onoff(cfg['block_self_promotion'])} Auto-promoção\n"
                f"{onoff(cfg['block_privileged_role_grant'])} Dar cargo perigoso\n"
                f"{onoff(cfg['block_role_creation_dangerous'])} Criar cargo perigoso\n"
                f"{onoff(cfg['block_role_edit_escalation'])} Editar cargo elevando\n"
                f"{onoff(cfg['block_everyone_edit'])} Editar @everyone\n"
                f"{onoff(cfg['block_position_abuse'])} Mover cargo acima\n"
                f"{onoff(cfg['block_manage_roles_delegation'])} Delegar manage_roles\n"
                f"{onoff(cfg['block_cascade'])} Cascata (BFS)"
            ),
            inline=False)
        embed.add_field(
            name="🤖 Regras Admin Role (v10.0)",
            value=(
                f"{onoff(cfg.get('block_bot_admin_role'))} Bot dando admin "
                f"(si/membro/bot)\n"
                f"{onoff(cfg.get('block_admin_grant_to_bot'))} Alguém dando "
                f"admin pra bot"
            ),
            inline=False)
        embed.add_field(
            name="🎯 Controle",
            value=(
                "`>protecao off antiescalation` — desativar users\n"
                "`>protecao on antiescalation` — reativar"
            ),
            inline=False)
        await ctx.send(embed=embed)


async def setup(bot):
    """Chamado pelo main.py pra carregar esse cog."""
    # Só adiciona se ainda não existe.
    if bot.get_cog("AntiEscalationCommands") is None:
        await bot.add_cog(AntiEscalationCommands(bot))