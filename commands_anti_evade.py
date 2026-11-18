# ============================================================
# COMMANDS_ANTI_EVADE.PY - v1.0
# ANTI EVADIR MUTE (PERSISTÊNCIA DE TIMEOUTS)
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Resolve um problema grave do Discord: quando um usuário em
# timeout SAI do servidor, o Discord REMOVE o timeout
# automaticamente. Se ele voltar, entra limpo — sem o castigo.
#
# Este sistema:
#   1. Salva TODOS os timeouts ativos no MongoDB
#   2. Quando o usuário sai, o timeout continua salvo no banco
#   3. Quando ele volta, o bot REAPLICA o timeout com o tempo
#      que ainda faltava
#   4. Cada retorno restaura somente o timeout original que ainda
#      estiver válido; não existe escalada para kick ou ban.
#
# 📌 POR QUE ISSO IMPORTA?
# Sem isso, qualquer troll que toma timeout só precisa sair
# e voltar pra escapar. É uma das vulnerabilidades mais
# exploradas em raids profissionais.
#
# 📌 COMO FUNCIONA (fluxo técnico):
# 1. on_member_update: detecta quando um timeout é aplicado
#    → salva {guild_id, user_id, expires_at, motivo, moderador}
# 2. on_member_remove: quando o user sai, NÃO apaga o timeout
#    → só marca como "user saiu" pra saber que existe
# 3. on_member_join: quando o user volta, checa se tem timeout
#    salvo e ainda válido
#    → reaplica com o tempo restante
# 4. Task periódica: limpa timeouts expirados do banco
#
# 📌 REGRA DE PUNIÇÃO:
# - Este módulo persiste somente timeouts ativos.
# - Ao retornar, restaura o mesmo timeout pelo tempo original restante.
# - Não troca timeout por kick/ban e não multiplica a duração.
# - Banimentos já continuam ativos no Discord quando o membro sai.
# ============================================================

# ────────────────────────────────────────────────────────────
# BLOCO 1 — IMPORTS
# ────────────────────────────────────────────────────────────

# "asyncio" = rodar coisas em paralelo (assíncrono).
import asyncio

# "time" = medir tempo (cooldowns, rate limit).
import time

# "datetime" = trabalhar com datas.
# "timedelta" = diferença entre datas (ex: +30 minutos).
from datetime import datetime, timedelta, timezone

# "typing" = documentação de tipos.
from typing import Dict, Optional

# "discord" = biblioteca principal.
import discord

# "commands" = criar comandos e cogs.
# "tasks" = tarefas que rodam em loop (background).
from discord.ext import commands, tasks

# "defaultdict" = dicionário com valor padrão.
from collections import defaultdict

# ─── Imports internos ───

# database = conexão MongoDB.
from database import get_connection_async

# embeds = caixas coloridas prontas.
from embeds import embed_success, embed_error, embed_warning, embed_info

# embeds_professional = log profissional.
from embeds_professional import ProfessionalLog, fmt_reason

# utils = helpers (cache, etc).
from utils import TTLCache


def _now() -> datetime:
    """
    Retorna a data/hora atual em UTC.

    📌 POR QUE UTC?
    Padrão do Discord. E evita bugs de fuso horário quando
    comparamos datas no banco.
    """
    return datetime.now(timezone.utc)


# ============================================================
# BLOCO 2 — CONFIGURAÇÃO PADRÃO
# ============================================================

DEFAULT_CONFIG = {
    "enabled": True,

    # Canal de log.
    "log_channel": 0,

    # Webhook de log (mais rápido).
    "log_webhook": "",

    # Notificar o dono quando detectar evasão.
    "notify_owner": False,

    # Whitelist (users/cargos que ignoram).
    "whitelist_users": [],
    "whitelist_roles": [],

    # Ignorar admins (não persegue admins, evita auto-punição).
    "ignore_admins": True,
}


# ============================================================
# BLOCO 3 — MANAGER (config no MongoDB)
# ============================================================

class AntiEvadeManager:
    # Cache de configs.
    _cache = TTLCache(max_size=200, ttl=60)

    @classmethod
    def get_cached_config(cls, guild_id: int) -> dict:
        """Retorna somente o cache; usado por construtores síncronos de UI."""
        cached = cls._cache.get(guild_id)
        return cached if cached is not None else dict(DEFAULT_CONFIG)

    @classmethod
    async def get_config(cls, guild_id: int) -> dict:
        """Pega a config do servidor (cache OU banco)."""
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached

        # Usa Motor para que a leitura do banco não bloqueie o loop do Discord.
        db = await get_connection_async()
        doc = await db["anti_evade_config"].find_one(
            {"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}

        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def get_config_fresh(cls, guild_id: int) -> dict:
        """Lê sempre do MongoDB antes de decisões de punição."""
        # Esta leitura ignora o cache porque alimenta decisões de proteção.
        db = await get_connection_async()
        doc = await db["anti_evade_config"].find_one(
            {"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def update(cls, guild_id: int, key: str, value):
        """Salva uma config no banco."""
        # Aguarda a persistência antes de invalidar o cache local.
        db = await get_connection_async()
        await db["anti_evade_config"].update_one(
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

def _is_whitelisted(cfg: dict, user_id: int,
                     member: Optional[discord.Member]) -> bool:
    """
    Diz se o user tá na whitelist.

    📌 ORDEM:
    1. ID direto
    2. Algum cargo dele
    3. Se ignore_admins e ele é admin
    """
    try:
        user_id = int(user_id)
        wl_users = {int(x) for x in cfg.get("whitelist_users", [])}
        wl_roles = {int(x) for x in cfg.get("whitelist_roles", [])}
    except (TypeError, ValueError):
        return False
    if user_id in wl_users:
        return True

    if member:
        if any(r.id in wl_roles for r in member.roles):
            return True

        if cfg.get("ignore_admins", True):
            if member.guild_permissions.administrator:
                return True

    return False


def _tempo_restante_str(expires_at: datetime) -> str:
    """
    Formata o tempo restante do timeout como string legível.

    📌 EXEMPLO:
    >>> _tempo_restante_str(_now() + timedelta(minutes=5))
    "5min"
    """
    delta = expires_at - _now()
    segundos = int(delta.total_seconds())

    if segundos <= 0:
        return "expirado"
    if segundos < 60:
        return f"{segundos}s"
    if segundos < 3600:
        return f"{segundos // 60}min"
    if segundos < 86400:
        h = segundos // 3600
        m = (segundos % 3600) // 60
        return f"{h}h {m}min"
    return f"{segundos // 86400}d"


# ============================================================
# BLOCO 5 — COG PRINCIPAL
# ============================================================

class AntiEvadeCommands(commands.Cog):
    """Anti Evadir Mute — persiste timeouts e reaplica ao voltar."""

    def __init__(self, bot):
        self.bot = bot

        # ─── Cache em memória (evita ir no banco toda hora) ───
        # Estrutura: {(guild_id, user_id): {expires_at, motivo, ...}}
        self._cache_timeouts: Dict[tuple, dict] = {}

        # ─── Contador de evasões por user ───
        # Estrutura: {(guild_id, user_id): count}
        self._evasoes: Dict[tuple, int] = defaultdict(int)

        # ─── Task que roda a cada 5 min pra limpar ───
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

    @tasks.loop(minutes=5)
    async def _cleanup_task(self):
        """
        Roda a cada 5 minutos. Limpa timeouts expirados do banco.

        📌 POR QUE?
        Mesmo com TTL do MongoDB (configurado no database.py),
        essa task é uma segunda garantia. E também atualiza
        o cache em memória.
        """
        try:
            # A limpeza periódica usa operações assíncronas do Motor.
            db = await get_connection_async()
            now = _now()

            # Remove do banco os que já expiraram.
            result = await db["active_timeouts"].delete_many({
                "expires_at": {"$lt": now},
            })

            # Remove do cache em memória.
            chaves_para_remover = []
            for chave, info in self._cache_timeouts.items():
                if info["expires_at"] < now:
                    chaves_para_remover.append(chave)
            for chave in chaves_para_remover:
                del self._cache_timeouts[chave]

        except Exception:
            pass

    @_cleanup_task.before_loop
    async def _before_cleanup(self):
        """Espera o bot ficar pronto antes de começar."""
        await self.bot.wait_until_ready()

    # ============================================================
    # LISTENER: DETECTA TIMEOUT APLICADO
    # ============================================================

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member,
                                 after: discord.Member):
        """
        Chamado quando algo do membro muda.

        📌 O QUE NOS INTERESSA:
        - timed_out_until: quando alguém recebe timeout
        - Se mudou de "sem timeout" pra "com timeout": SALVA
        - Se mudou de "com timeout" pra "sem timeout": pode ser
          manual (moderador removeu) ou expirou — em ambos, REMOVE
        """
        # Checa se mudou o estado de timeout.
        before_timeout = before.timed_out_until
        after_timeout = after.timed_out_until

        # Se não mudou, ignora.
        if before_timeout == after_timeout:
            return

        # Remoção manual/expiração precisa limpar o registro mesmo com a
        # proteção desligada; caso contrário, um timeout revogado poderia
        # voltar a ser aplicado quando o usuário reentrasse futuramente.
        if after_timeout is None:
            await self._remover_timeout(after.guild.id, after.id)
            return

        try:
            cfg = await AntiEvadeManager.get_config_fresh(after.guild.id)
            an_cfg = self._get_fresh_antinuke_config(after.guild.id)
        except Exception:
            return
        if not cfg.get("enabled", False):
            return
        if not an_cfg.get("user_protection_enabled", True):
            # A proteção humana foi desligada: não manter uma punição
            # pendente para reaplicá-la em uma futura reentrada.
            await self._remover_timeout(after.guild.id, after.id)
            return
        if after.bot or self._is_globally_whitelisted(an_cfg, after):
            await self._remover_timeout(after.guild.id, after.id)
            return

        # ─── Timeout foi APLICADO ───
        await self._salvar_timeout(
            after, after_timeout, cfg,
            protection_epoch=int(an_cfg.get("user_protection_epoch", 0)))

    @staticmethod
    def _get_fresh_antinuke_config(guild_id: int) -> dict:
        """Obtém a whitelist central e o toggle global sem usar cache antigo."""
        from commands_antinuke import AntiNukeManager
        return AntiNukeManager.get_config_fresh(guild_id)

    @staticmethod
    def _is_globally_whitelisted(cfg: dict, member: discord.Member) -> bool:
        """Whitelist central: IDs de humanos/bots e cargos são imunes."""
        try:
            uid = int(member.id)
            users = {int(x) for x in cfg.get("whitelist_users", [])}
            bots = {int(x) for x in cfg.get("whitelist_bots", [])}
            roles = {int(x) for x in cfg.get("whitelist_roles", [])}
            role_exclusions = {int(x) for x in cfg.get(
                "whitelist_role_exclusions", [])}
        except (TypeError, ValueError):
            return True  # dado inválido: falha fechada, não pune
        # Whitelist humana e de bots são independentes. Um ID em
        # whitelist_users não torna um bot imune.
        if member.bot:
            return uid in bots
        if uid in users:
            return True
        if uid in role_exclusions:
            return False
        return any(role.id in roles for role in member.roles)

    async def _salvar_timeout(self, member: discord.Member,
                                expires_at: datetime, cfg: dict,
                                protection_epoch: int = 0):
        """
        Salva um timeout ativo no banco.

        📌 FLUXO:
        1. Verifica se o user tá na whitelist
        2. Salva no MongoDB (upsert)
        3. Salva no cache em memória
        4. Loga (opcional)
        """
        # Whitelist?
        if _is_whitelisted(cfg, member.id, member):
            return

        # Garante que expires_at tem timezone (UTC).
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)

        # Salva no banco.
        try:
            db = await get_connection_async()
            await db["active_timeouts"].update_one(
                {"guild_id": member.guild.id, "user_id": member.id},
                {"$set": {
                    "guild_id": member.guild.id,
                    "user_id": member.id,
                    "username": str(member),
                    "expires_at": expires_at,
                    "punishment_type": "timeout",
                    "protection_epoch": int(protection_epoch),
                    "saved_at": _now(),
                }},
                upsert=True,
            )
        except Exception:
            # Sem persistência confirmada, não manter estado apenas em cache:
            # isso criaria comportamento diferente após reinicialização.
            self._cache_timeouts.pop((member.guild.id, member.id), None)
            return

        # Salva no cache somente após persistência confirmada.
        chave = (member.guild.id, member.id)
        self._cache_timeouts[chave] = {
            "expires_at": expires_at,
            "username": str(member),
            "punishment_type": "timeout",
            "protection_epoch": int(protection_epoch),
        }

    async def _remover_timeout(self, guild_id: int, user_id: int):
        """Remove um timeout do banco e do cache."""
        try:
            db = await get_connection_async()
            await db["active_timeouts"].delete_one(
                {"guild_id": guild_id, "user_id": user_id})
        except Exception:
            pass

        self._cache_timeouts.pop((guild_id, user_id), None)

    # ============================================================
    # LISTENER: USUÁRIO VOLTOU AO SERVIDOR
    # ============================================================

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """
        Chamado quando alguém entra no servidor.

        📌 FLUXO:
        1. Ignora bots
        2. Verifica se tem timeout salvo pro user
        3. Se ainda está válido (não expirou):
           - Incrementa contador de evasões
           - Aplica a ação configurada
        """
        if member.bot:
            return

        try:
            cfg = await AntiEvadeManager.get_config_fresh(member.guild.id)
            an_cfg = self._get_fresh_antinuke_config(member.guild.id)
        except Exception:
            return  # sem confirmação do estado atual, nenhuma punição
        if not cfg.get("enabled", False):
            return
        if not an_cfg.get("user_protection_enabled", True):
            # Não deixar timeout pendente para uma futura reativação.
            await self._remover_timeout(member.guild.id, member.id)
            return

        # Ambas as whitelists são respeitadas (local e central).
        if (_is_whitelisted(cfg, member.id, member)
                or self._is_globally_whitelisted(an_cfg, member)):
            await self._remover_timeout(member.guild.id, member.id)
            return

        # Um toggle OFF/ON invalida qualquer timeout salvo no ciclo anterior,
        # mesmo se a limpeza no Mongo falhou durante a alteração.
        current_epoch = int(an_cfg.get("user_protection_epoch", 0))

        # Procura timeout salvo.
        chave = (member.guild.id, member.id)
        info = self._cache_timeouts.get(chave)

        # Não tem no cache, tenta no banco.
        if info is None:
            try:
                # Consulta o timeout persistido somente quando não está em cache.
                db = await get_connection_async()
                doc = await db["active_timeouts"].find_one({
                    "guild_id": member.guild.id,
                    "user_id": member.id,
                })
                if doc:
                    expires = doc.get("expires_at")
                    if expires and expires.tzinfo is None:
                        expires = expires.replace(tzinfo=timezone.utc)
                    info = {
                        "expires_at": expires,
                        "username": doc.get("username", str(member)),
                        "protection_epoch": int(doc.get("protection_epoch", 0)),
                    }
                    self._cache_timeouts[chave] = info
            except Exception:
                pass

        # Não tem timeout salvo, tudo bem.
        if info is None:
            return
        if int(info.get("protection_epoch", 0)) != current_epoch:
            await self._remover_timeout(member.guild.id, member.id)
            return

        # Timeout expirou enquanto ele estava fora?
        expires_at = info["expires_at"]
        if expires_at <= _now():
            await self._remover_timeout(member.guild.id, member.id)
            return

        # ─── EVASÃO DETECTADA! ───
        await self._processar_evasao(
            member, expires_at, cfg, protection_epoch=current_epoch)

    async def _processar_evasao(self, member: discord.Member,
                                  expires_at: datetime, cfg: dict,
                                  protection_epoch: int = 0):
        """Reaplica exatamente o timeout ainda vigente, sem agravar a pena.

        O Anti-Evade só registra timeouts ativos. Banimentos já persistem no
        Discord mesmo quando o membro sai; kicks não são punições persistentes.
        Portanto, a evasão de timeout deve restaurar o tempo original restante.
        """
        guild = member.guild
        chave = (guild.id, member.id)

        # Última validação imediatamente antes da chamada de moderação.
        try:
            fresh_cfg = await AntiEvadeManager.get_config_fresh(guild.id)
            an_cfg = self._get_fresh_antinuke_config(guild.id)
        except Exception:
            return
        if not fresh_cfg.get("enabled", False):
            return
        if not an_cfg.get("user_protection_enabled", True):
            # Toggle OFF durante o processamento: descarta o timeout salvo.
            await self._remover_timeout(guild.id, member.id)
            return
        if int(an_cfg.get("user_protection_epoch", 0)) != int(protection_epoch):
            await self._remover_timeout(guild.id, member.id)
            return
        if (_is_whitelisted(fresh_cfg, member.id, member)
                or self._is_globally_whitelisted(an_cfg, member)):
            await self._remover_timeout(guild.id, member.id)
            return

        # Se a punição original já expirou durante o processamento, não age.
        remaining = int((expires_at - _now()).total_seconds())
        if remaining <= 0:
            await self._remover_timeout(guild.id, member.id)
            return

        self._evasoes[chave] += 1
        total_evasoes = self._evasoes[chave]
        tempo_restante = _tempo_restante_str(expires_at)
        resultado = "timeout não aplicado"
        try:
            # Nova leitura imediatamente antes da operação externa reduz a
            # janela de corrida com o botão de desativar proteção humana.
            final_cfg = await AntiEvadeManager.get_config_fresh(guild.id)
            final_an_cfg = self._get_fresh_antinuke_config(guild.id)
            if not final_cfg.get("enabled", False):
                return
            if not final_an_cfg.get("user_protection_enabled", True):
                # Última leitura já viu o toggle OFF: não reaplicar e limpar
                # o registro para impedir punição atrasada após reativação.
                await self._remover_timeout(guild.id, member.id)
                return
            if int(final_an_cfg.get("user_protection_epoch", 0)) != int(protection_epoch):
                await self._remover_timeout(guild.id, member.id)
                return
            if (_is_whitelisted(final_cfg, member.id, member)
                    or self._is_globally_whitelisted(final_an_cfg, member)):
                await self._remover_timeout(guild.id, member.id)
                return
            await member.timeout(
                discord.utils.utcnow() + timedelta(seconds=remaining),
                reason=f"Anti Evade: restauração do timeout original ({total_evasoes} evasão(ões))")
            resultado = f"timeout original restaurado ({remaining}s restantes)"
        except Exception as e:
            resultado = f"falha ({type(e).__name__})"

        # Persistência mantém a data original; não multiplica nem troca a ação.
        try:
            db = await get_connection_async()
            await db["active_timeouts"].update_one(
                {"guild_id": guild.id, "user_id": member.id},
                {"$set": {
                    "guild_id": guild.id,
                    "user_id": member.id,
                    "username": str(member),
                    "punishment_type": "timeout",
                    "protection_epoch": int(protection_epoch),
                    "expires_at": expires_at,
                    "saved_at": _now(),
                    "evasions": total_evasoes,
                }},
                upsert=True,
            )
            self._cache_timeouts[chave] = {
                "expires_at": expires_at,
                "username": str(member),
                "punishment_type": "timeout",
                "protection_epoch": int(protection_epoch),
            }
        except Exception:
            pass

        await self._enviar_log(
            guild, member, expires_at, tempo_restante,
            total_evasoes, "timeout", resultado, False, fresh_cfg)

        if fresh_cfg.get("notify_owner"):
            await self._notify_owner(
                guild, member, "timeout", total_evasoes, fresh_cfg)

    # ============================================================
    # LOG
    # ============================================================

    async def _enviar_log(self, guild: discord.Guild,
                            member: discord.Member, expires_at: datetime,
                            tempo_restante: str, total_evasoes: int,
                            action: str, resultado: str,
                            escalou: bool, cfg: dict):
        """Envia log pro canal/webhook configurado."""
        severidade = "high" if escalou else "medium"

        titulo = "🔄 Evasão de Mute Detectada"

        descricao = (
            "Um usuário saiu do servidor durante um timeout e voltou. "
            "O sistema verificou o estado atual e restaurou somente o "
            "tempo que ainda restava da punição original."
        )

        log = ProfessionalLog(
            title=titulo,
            description=descricao,
            severity=severidade,
            footer="Stumble Hit AntiRaid • Anti Evadir Mute")

        log.set_target(member)
        log.add_field("⏰ Timeout Original",
                       f"expirava <t:{int(expires_at.timestamp())}:R>")
        log.add_field("🕒 Tempo Restante", f"`{tempo_restante}`")
        log.add_field("🔁 Total de Evasões", f"`{total_evasoes}x`")
        log.add_field("⚖️ Ação Aplicada", f"`{action}`")

        embed = log.build()

        # 1. Webhook (mais rápido).
        wh_url = cfg.get("log_webhook", "")
        if wh_url:
            try:
                wh = discord.Webhook.from_url(wh_url, client=None)
                await wh.send(embed=embed,
                              username="🔄 Anti Evade", wait=False)
                return
            except Exception:
                pass

        # 2. Canal.
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
                              member: discord.Member, action: str,
                              total_evasoes: int, cfg: dict):
        """Manda DM pro dono."""
        try:
            owner = guild.owner or await guild.fetch_member(guild.owner_id)
            await owner.send(embed=discord.Embed(
                title="Timeout restaurado após retorno",
                description=(
                    f"**Servidor:** {guild.name}\n"
                    f"**User:** {member.mention}\n"
                    f"**Evasões:** `{total_evasoes}x`\n"
                    f"**Ação:** `{action}`"
                ),
                color=discord.Color.orange(),
                timestamp=_now()))
        except Exception:
            pass

    # ============================================================
    # COMANDOS (grupo >antievade)
    # ============================================================

    @commands.group(name="antievade", aliases=["ae2", "antievade2"],
                    invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def antievade(self, ctx):
        """
        Painel principal do Anti Evadir Mute.

        📌 SEM ARGUMENTO:
        Mostra o painel com todas as configs.
        """
        cfg = await AntiEvadeManager.get_config(ctx.guild.id)

        def onoff(v):
            return "✅" if v else "❌"

        log_ch = cfg.get("log_channel")
        log_str = f"<#{log_ch}>" if log_ch else "❌"

        embed = discord.Embed(
            title="Anti-Evade | Persistência de timeout",
            description=(
                "Mantém o registro do timeout quando um membro sai e, "
                "se ele retornar antes do fim, reaplica apenas o período restante."
            ),
            color=discord.Color.orange(),
            timestamp=_now())

        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.add_field(
            name="📊 Configurações",
            value=(
                f"**Status:** {onoff(cfg['enabled'])}\n"
                "**Punição:** restaurar timeout original\n"
                "**Duração:** somente o tempo restante original\n"
                f"**Log:** {log_str}"
            ),
            inline=True)

        embed.add_field(
            name="🛡️ Extras",
            value=(
                f"**Ignorar admins:** {onoff(cfg['ignore_admins'])}\n"
                f"**Notificar owner:** {onoff(cfg['notify_owner'])}\n"
                f"**Whitelist users:** `{len(cfg.get('whitelist_users', []))}`\n"
                f"**Whitelist cargos:** `{len(cfg.get('whitelist_roles', []))}`"
            ),
            inline=True)

        embed.add_field(
            name="📌 Comandos",
            value=(
                "`>antievade on/off` — liga/desliga\n"
                "`>antievade log #canal` — canal de log\n"
                "`>antievade webhook <URL>` — webhook\n"
                "`>antievade whitelist @user` — whitelist\n"
                "`>antievade unwhitelist @user` — remove\n"
                "`>antievade admins on/off` — ignorar admins\n"
                "`>antievade notify on/off` — notificar owner\n"
                "`>antievade list` — timeouts ativos\n"
                "`>antievade clear @user` — limpa timeout de user\n"
                "`>antievade stats` — estatísticas"
            ),
            inline=False)

        embed.set_footer(text="Stumble Hit AntiRaid  •  Anti-Evade")
        await ctx.send(embed=embed)

    # ─── Ligar/desligar ───

    @antievade.command(name="on")
    @commands.has_permissions(administrator=True)
    async def ae_on(self, ctx):
        """Ativa o Anti Evadir Mute."""
        await AntiEvadeManager.update(ctx.guild.id, "enabled", True)
        await ctx.send(embed=embed_success(
            "✅ Anti Evadir Mute **ativado**!"))

    @antievade.command(name="off")
    @commands.has_permissions(administrator=True)
    async def ae_off(self, ctx):
        """Desativa o Anti Evadir Mute."""
        await AntiEvadeManager.update(ctx.guild.id, "enabled", False)
        await ctx.send(embed=embed_success(
            "✅ Anti Evadir Mute **desativado**!"))

    # ─── Ação ───

    @antievade.command(name="action", aliases=["acao"])
    @commands.has_permissions(administrator=True)
    async def ae_action(self, ctx, tipo: str = None):
        """Compatibilidade: Anti-Evade só restaura o timeout original."""
        return await ctx.send(embed=embed_warning(
            "⚠️ O Anti-Evade não permite mais escolher kick/ban. "
            "Ele restaura exclusivamente o timeout original pelo tempo restante."))

    @antievade.command(name="multiplier", aliases=["multiplicador"])
    @commands.has_permissions(administrator=True)
    async def ae_multiplier(self, ctx, valor: float = None):
        """Compatibilidade: a duração não pode ser multiplicada."""
        return await ctx.send(embed=embed_warning(
            "⚠️ Multiplicador desativado. O Anti-Evade usa somente o tempo restante original."))

    @antievade.command(name="maxevasions", aliases=["maxevasoes"])
    @commands.has_permissions(administrator=True)
    async def ae_maxevasions(self, ctx, n: int = None):
        """Compatibilidade: não há escalada por quantidade de evasões."""
        return await ctx.send(embed=embed_warning(
            "⚠️ Escalada desativada. Cada retorno restaura somente o timeout original restante."))

    @antievade.command(name="escalate", aliases=["escalar"])
    @commands.has_permissions(administrator=True)
    async def ae_escalate(self, ctx, tipo: str = None):
        """Compatibilidade: não há ação de escalada."""
        return await ctx.send(embed=embed_warning(
            "⚠️ Escalada para kick/ban desativada. O Anti-Evade só reaplica timeout."))

    # ─── Canal de log ───

    @antievade.command(name="log")
    @commands.has_permissions(administrator=True)
    async def ae_log(self, ctx, canal: discord.TextChannel):
        """Define o canal de log."""
        await AntiEvadeManager.update(ctx.guild.id, "log_channel", canal.id)
        await ctx.send(embed=embed_success(f"✅ Log em {canal.mention}"))

    # ─── Webhook ───

    @antievade.command(name="webhook")
    @commands.has_permissions(administrator=True)
    async def ae_webhook(self, ctx, url: str):
        """Define o webhook de log."""
        if not url.startswith("https://discord.com/api/webhooks/"):
            return await ctx.send(embed=embed_error(
                "❌ URL de webhook inválida."))

        await AntiEvadeManager.update(ctx.guild.id, "log_webhook", url)

        try:
            wh = discord.Webhook.from_url(url, client=None)
            await wh.send(embed=discord.Embed(
                title="✅ Webhook configurado",
                description="Logs do Anti Evadir Mute vão aparecer aqui.",
                color=discord.Color.green(),
                timestamp=_now()))
            await ctx.send(embed=embed_success("✅ Webhook salvo!"))
        except Exception as e:
            await ctx.send(embed=embed_warning(
                f"⚠️ Webhook salvo, mas teste falhou: `{e}`"))

    # ─── Whitelist ───

    @antievade.command(name="whitelist", aliases=["wl"])
    @commands.has_permissions(administrator=True)
    async def ae_whitelist(self, ctx, alvo: str):
        """Adiciona user/cargo à whitelist (ignoram essa proteção)."""
        # Cargo?
        if alvo.startswith("<@&") and alvo.endswith(">"):
            rid = int(alvo[3:-1])
            cfg = await AntiEvadeManager.get_config(ctx.guild.id)
            roles = list(cfg.get("whitelist_roles", []))
            if rid not in roles:
                roles.append(rid)
                await AntiEvadeManager.update(
                    ctx.guild.id, "whitelist_roles", roles)
            return await ctx.send(embed=embed_success(
                f"✅ <@&{rid}> na whitelist."))

        # User?
        if alvo.startswith("<@") and alvo.endswith(">"):
            uid = int(alvo[2:-1].split("!")[-1])
            cfg = await AntiEvadeManager.get_config(ctx.guild.id)
            users = list(cfg.get("whitelist_users", []))
            if uid not in users:
                users.append(uid)
                await AntiEvadeManager.update(
                    ctx.guild.id, "whitelist_users", users)
            return await ctx.send(embed=embed_success(
                f"✅ <@{uid}> na whitelist."))

        await ctx.send(embed=embed_error(
            "❌ Use: `>antievade whitelist @user` ou `@cargo`"))

    @antievade.command(name="unwhitelist", aliases=["unwl"])
    @commands.has_permissions(administrator=True)
    async def ae_unwhitelist(self, ctx, alvo: str):
        """Remove da whitelist."""
        # Cargo?
        if alvo.startswith("<@&") and alvo.endswith(">"):
            rid = int(alvo[3:-1])
            cfg = await AntiEvadeManager.get_config(ctx.guild.id)
            roles = [r for r in cfg.get("whitelist_roles", []) if r != rid]
            await AntiEvadeManager.update(ctx.guild.id, "whitelist_roles", roles)
            return await ctx.send(embed=embed_success(
                f"✅ <@&{rid}> removido da whitelist."))

        # User?
        if alvo.startswith("<@") and alvo.endswith(">"):
            uid = int(alvo[2:-1].split("!")[-1])
            cfg = await AntiEvadeManager.get_config(ctx.guild.id)
            users = [u for u in cfg.get("whitelist_users", []) if u != uid]
            await AntiEvadeManager.update(ctx.guild.id, "whitelist_users", users)
            return await ctx.send(embed=embed_success(
                f"✅ <@{uid}> removido da whitelist."))

        await ctx.send(embed=embed_error(
            "❌ Use: `>antievade unwhitelist @user` ou `@cargo`"))

    # ─── Ignorar admins ───

    @antievade.command(name="admins")
    @commands.has_permissions(administrator=True)
    async def ae_admins(self, ctx, state: str):
        """Liga/desliga ignorar admins (evita auto-punição)."""
        enabled = state.lower() in ("on", "true", "1", "sim")
        await AntiEvadeManager.update(ctx.guild.id, "ignore_admins", enabled)
        await ctx.send(embed=embed_success(
            f"✅ Ignorar admins: {'ON' if enabled else 'OFF'}"))

    # ─── Notificar owner ───

    @antievade.command(name="notify")
    @commands.has_permissions(administrator=True)
    async def ae_notify(self, ctx, state: str):
        """Liga/desliga DM pro dono."""
        enabled = state.lower() in ("on", "true", "1", "sim")
        await AntiEvadeManager.update(ctx.guild.id, "notify_owner", enabled)
        await ctx.send(embed=embed_success(
            f"✅ Notificar owner: {'ON' if enabled else 'OFF'}"))

    # ─── Listar timeouts ativos ───

    @antievade.command(name="list", aliases=["lista"])
    @commands.has_permissions(administrator=True)
    async def ae_list(self, ctx):
        """
        Lista os timeouts ativos salvos no banco.

        📌 ÚTIL PRA:
        - Saber quem tá com timeout ativo
        - Ver quem já tentou evadir
        """
        try:
            db = await get_connection_async()
            # Mantém a ordenação decrescente e o limite original de 30.
            docs = await db["active_timeouts"].find(
                {"guild_id": ctx.guild.id}
            ).sort("expires_at", -1).limit(30).to_list(length=30)
        except Exception:
            docs = []

        if not docs:
            return await ctx.send(embed=embed_info(
                "📋 Nenhum timeout ativo."))

        embed = discord.Embed(
            title=f"Timeouts ativos · {len(docs)}",
            color=discord.Color.orange(),
            timestamp=_now())

        for d in docs[:15]:
            uid = d.get("user_id")
            expires = d.get("expires_at")

            # Normaliza timezone.
            if expires and expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)

            # Tempo restante.
            if expires:
                restante = _tempo_restante_str(expires)
                ts = f"<t:{int(expires.timestamp())}:R>"
            else:
                restante = "?"
                ts = "?"

            evasoes = d.get("evasions", 0)

            embed.add_field(
                name=f"👤 <@{uid}>",
                value=(
                    f"**Expira:** {ts}\n"
                    f"**Restante:** `{restante}`\n"
                    f"**Evasões:** `{evasoes}x`"
                ),
                inline=True)

        await ctx.send(embed=embed)

    # ─── Limpar timeout de user ───

    @antievade.command(name="clear", aliases=["limpar"])
    @commands.has_permissions(administrator=True)
    async def ae_clear(self, ctx, user: discord.Member):
        """
        Limpa o timeout ativo de um usuário do banco.

        📌 ÚTIL PRA:
        - Perdoar um user que evadiu por engano
        - Resetar contador de evasões
        """
        await self._remover_timeout(ctx.guild.id, user.id)
        self._evasoes.pop((ctx.guild.id, user.id), None)

        # Também remove timeout ativo no Discord (se tiver).
        try:
            if user.timed_out_until:
                await user.timeout(None, reason="Anti Evade: reset manual")
        except Exception:
            pass

        await ctx.send(embed=embed_success(
            f"✅ Timeout ativo de {user.mention} limpo."))

    # ─── Estatísticas ───

    @antievade.command(name="stats", aliases=["stat"])
    @commands.has_permissions(administrator=True)
    async def ae_stats(self, ctx):
        """Estatísticas do Anti Evadir Mute."""
        try:
            db = await get_connection_async()
            colecoes = await db.list_collection_names()

            # Total de timeouts ativos.
            total_ativos = await db["active_timeouts"].count_documents(
                {"guild_id": ctx.guild.id})

            # Total de evasões detectadas (via logs).
            total_evasoes = await db["anti_evade_logs"].count_documents({
                "guild_id": ctx.guild.id,
            }) if "anti_evade_logs" in colecoes else 0

            # Top evasores: pipeline e limite permanecem iguais.
            top_evasores = await db["anti_evade_logs"].aggregate([
                {"$match": {"guild_id": ctx.guild.id}},
                {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
                {"$sort": {"count": -1}},
                {"$limit": 5},
            ]).to_list(length=5) if "anti_evade_logs" in colecoes else []

        except Exception:
            total_ativos = 0
            total_evasoes = 0
            top_evasores = []

        embed = discord.Embed(
            title="Anti-Evade | Resumo",
            color=discord.Color.orange(),
            timestamp=_now())

        embed.add_field(
            name="📈 Totais",
            value=(
                f"**Timeouts ativos:** `{total_ativos}`\n"
                f"**Evasões detectadas:** `{total_evasoes}`"
            ),
            inline=True)

        if top_evasores:
            linhas = [f"• <@{u['_id']}> → {u['count']}x"
                       for u in top_evasores]
            embed.add_field(
                name="🚨 Top Evasores",
                value="\n".join(linhas),
                inline=False)

        await ctx.send(embed=embed)


# ============================================================
# BLOCO 6 — SETUP
# ============================================================

async def setup(bot):
    """
    Chamado pelo main.py pra carregar esse cog.
    """
    if bot.get_cog("AntiEvadeCommands") is None:
        await bot.add_cog(AntiEvadeCommands(bot))