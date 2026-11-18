# ============================================================
# COMMANDS_EVENT_LOGS.PY - v1.1
# LOGS DE EVENTOS DO SERVIDOR
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Registra TODOS os eventos do servidor e envia pra canais
# específicos configurados em `>logs context <cat> #canal`.
#
# Eventos: join, leave, nick, roles, avatar, boost, mensagens
# editadas/deletadas, canais criados/deletados/editados,
# cargos, voz, threads, convites, emojis, etc.
#
# v1.1 (CORREÇÃO):
# - Fix: CategoryChannel não tem .topic/.nsfw/.slowmode
# - Fix: VoiceChannel não tem .topic
# - Agora detecta o tipo de canal antes de acessar atributos
# - Adicionado: detecção de mudança de POSIÇÃO
# - Adicionado: detecção de bitrate/user_limit de voz
# ============================================================

import asyncio
import time
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

import discord
from discord.ext import commands, tasks

from database import get_connection_async
from embeds import embed_success, embed_error, embed_warning, embed_info
from embeds_professional import ProfessionalLog, fmt_reason, fmt_user
from utils import TTLCache


def _now() -> datetime:
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# CONFIGURAÇÃO PADRÃO
# ============================================================
# Cada flag liga/desliga um tipo de log.

DEFAULT_CONFIG = {
    "enabled": True,

    # ─── Ações de moderação (comandos) ───
    "log_mod_kick": True,
    "log_mod_ban": True,
    "log_mod_unban": True,
    "log_mod_timeout": True,
    "log_mod_warn": True,
    "log_mod_strike": True,
    "log_mod_purge": True,
    "log_mod_silent": True,

    # ─── Membros ───
    "log_member_join": True,
    "log_member_leave": True,
    "log_member_nick": True,
    "log_member_roles": True,
    "log_member_avatar": True,
    "log_member_boost": True,

    # ─── Mensagens ───
    "log_message_edit": True,
    "log_message_delete": True,
    "log_message_bulk_delete": True,

    # ─── Canais ───
    "log_channel_create": True,
    "log_channel_delete": True,
    "log_channel_update": True,

    # ─── Cargos ───
    "log_role_create": True,
    "log_role_delete": True,
    "log_role_update": True,

    # ─── Voz ───
    "log_voice_join": True,
    "log_voice_leave": True,
    "log_voice_move": True,
    "log_voice_mute": True,

    # ─── Threads ───
    "log_thread_create": True,
    "log_thread_delete": True,

    # ─── Convites ───
    "log_invite_create": True,
    "log_invite_delete": True,

    # ─── Emojis ───
    "log_emoji_create": True,
    "log_emoji_delete": True,

    # ─── Comandos de moderação ───
    "log_mod_commands": True,
    "log_all_commands": False,

    # ─── Suspeitas / watch ───
    "log_suspicious": True,
    "suspicious_join_window": 10,
    "suspicious_join_threshold": 5,
    "suspicious_mention_threshold": 8,
    "suspicious_link_threshold": 4,

    # ─── Ignorar ───
    "ignore_bots": True,
    "ignore_channels": [],
    "ignore_users": [],

    # ─── Filtros ───
    "min_message_length": 3,
    "ignore_prefixes": ["!", ".", "?", "-", "+"],
}


# ============================================================
# MANAGER
# ============================================================

class EventLogsManager:
    _cache = TTLCache(max_size=200, ttl=60)

    @classmethod
    async def get_config(cls, guild_id: int) -> dict:
        """Obtém a configuração do cache ou do MongoDB sem bloquear o event loop.

        O cache continua sendo consultado primeiro para evitar leituras repetidas.
        Quando não existe uma entrada válida, a consulta usa Motor e é aguardada
        com ``await``; assim, o bot pode continuar processando outros eventos
        enquanto o MongoDB responde.
        """
        # Primeiro tenta aproveitar a configuração já carregada em memória.
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached

        # Obtém a conexão assíncrona; a função precisa ser aguardada porque
        # retorna o banco Motor associado ao event loop atual.
        db = await get_connection_async()

        # find_one() do Motor retorna uma coroutine: await aguarda o resultado
        # sem bloquear o processamento dos demais eventos do Discord.
        doc = await db["event_logs_config"].find_one({"guild_id": guild_id}) or {}

        # Combina os padrões com os valores persistidos, preservando as opções
        # salvas e preenchendo somente as chaves ainda ausentes.
        cfg = {**DEFAULT_CONFIG, **doc}

        # Armazena o resultado para as próximas leituras e o devolve ao chamador.
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def update(cls, guild_id: int, key: str, value):
        """Persiste uma opção e só depois invalida o cache correspondente.

        A invalidação ocorre após a gravação terminar: desse modo, uma leitura
        seguinte não recarrega do banco um valor antigo enquanto a escrita ainda
        está pendente.
        """
        # Abre/acessa o banco por meio da API Motor, sem recorrer ao PyMongo.
        db = await get_connection_async()

        # Aguarda a atualização com upsert: cria o documento da guilda caso
        # ainda não exista, mantendo a estrutura de armazenamento anterior.
        await db["event_logs_config"].update_one(
            {"guild_id": guild_id}, {"$set": {key: value}}, upsert=True)

        # A escrita foi confirmada pelo driver; agora força a próxima leitura
        # a buscar os dados atualizados no MongoDB.
        cls._cache.invalidate(guild_id)

    @classmethod
    def invalidate(cls, guild_id: int):
        cls._cache.invalidate(guild_id)


# ============================================================
# HELPERS
# ============================================================

def _is_ignored(cfg: dict, user_id: Optional[int] = None,
                 channel_id: Optional[int] = None) -> bool:
    """Diz se um user/canal tá na lista de ignorados."""
    if user_id and user_id in cfg.get("ignore_users", []):
        return True
    if channel_id and channel_id in cfg.get("ignore_channels", []):
        return True
    return False


def _format_duration(seconds: int) -> str:
    """Formata segundos em texto. Ex: 3600 → '1h'."""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}min"
    if seconds < 86400:
        return f"{seconds // 3600}h"
    return f"{seconds // 86400}d"


async def _send_event_log(cog, guild: discord.Guild, category: str,
                            log_type: str, title: str,
                            description: str, severity: str,
                            target: Optional[discord.Member] = None,
                            executor: Optional[discord.Member] = None,
                            extra_fields: List[dict] = None) -> bool:
    """
    Envia log de evento pra categoria contextual do LogsManager.
    Retorna True se conseguiu.
    """
    if not cog or not guild:
        return False

    cfg = await EventLogsManager.get_config(guild.id)
    # Sistema desligado?
    if not cfg.get("enabled"):
        return False

    # Esse tipo específico tá ligado?
    if not cfg.get(f"log_{log_type}", True):
        return False

    # Monta o log profissional.
    log = ProfessionalLog(
        title=title,
        description=description,
        severity=severity,
        footer="Stumble Hit AntiRaid • Logs de Eventos")

    if target:
        log.set_target(target)
    if executor:
        log.set_executor(executor)

    # Adiciona campos extras.
    if extra_fields:
        for f in extra_fields[:10]:
            log.add_field(
                f.get("name", "—"),
                f.get("value", "—"),
                inline=f.get("inline", True))

    embed = log.build()

    # Envia pro canal contextual.
    try:
        from commands_logs import LogsManager
        logs_cfg = await LogsManager.get_config(guild.id)
        ctx_channels = logs_cfg.get("context_channels", {})
        # Pega o canal específico da categoria.
        channel_id = ctx_channels.get(category)
        # Se não tem, tenta o canal padrão.
        if not channel_id:
            channel_id = logs_cfg.get("channel_id")
        if not channel_id:
            return False
        channel = guild.get_channel(int(channel_id))
        if not channel:
            return False
        await channel.send(embed=embed)
        return True
    except Exception:
        return False


# ============================================================
# COG
# ============================================================

class EventLogsCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Rastreamento de joins (pra detectar raid).
        self._joins: Dict[int, deque] = defaultdict(
            lambda: deque(maxlen=50))
        # Rastreamento de menções (pra detectar spam).
        self._mentions: Dict[int, Dict[int, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=20)))
        # Rastreamento de links.
        self._links: Dict[int, Dict[int, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=20)))
        # Rastreamento de uso de comandos.
        self._cmd_uses: Dict[int, Dict[int, deque]] = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=30)))

    # ============================================================
    # LISTENER: USO DE COMANDOS DE MODERAÇÃO
    # ============================================================

    @commands.Cog.listener()
    async def on_command_completion(self, ctx):
        """Chamado quando um comando termina com sucesso."""
        if not ctx.guild:
            return
        cfg = await EventLogsManager.get_config(ctx.guild.id)
        if not cfg.get("enabled") or not cfg.get("log_mod_commands"):
            return

        # Lista dos comandos considerados "de moderação".
        MOD_CMDS = {
            "kick", "ban", "softban", "tempban", "unban", "massban",
            "timeout", "untimeout", "mute", "unmute",
            "warn", "warns", "clearwarns", "strike", "unstrike",
            "jail", "unjail", "strip", "restore",
            "silentban", "silentkick",
            "purge", "purgebots", "purgechannel", "nuke",
            "purgelink", "purgeimage", "purgeattach", "purgeregex",
            "purgeafter", "purgebefore", "purgemention", "purgeembed",
            "purgeinvite", "purgebot", "purgeuser", "purgepinguser",
            "purgenuke", "purgemulti", "purgeage",
            "lock", "unlock", "lockdown", "unlockdown",
            "hide", "reveal", "freeze", "unfreeze", "slowmode",
            "templock", "hideall", "showall",
            "addrole", "removerole", "temprole", "createrole",
            "deleterole", "rolecolor", "roleall", "roleunall",
            "dehoist", "rolepersist",
            "moveall", "disconnect", "voicekick", "voiceban",
            "voiceunban", "vcmute", "vcunmute",
            "block", "unblock",
            "whitelist", "unwhitelist",
            "setconfig", "resetconfig", "setperm", "removeperm",
        }

        # Se não é mod e nem log_all_commands, sai.
        if ctx.command.name not in MOD_CMDS and not cfg.get("log_all_commands"):
            return

        key = ctx.guild.id
        dq = self._cmd_uses[key][ctx.author.id]
        now = time.time()
        dq.append((ctx.command.name, now))

        # Monta campos extras.
        extra = [
            {"name": "📍 Canal", "value": ctx.channel.mention, "inline": True},
            {"name": "🎯 Comando", "value": f"`{ctx.command.name}`",
             "inline": True},
        ]
        if ctx.message and ctx.message.content:
            content = ctx.message.content[:200]
            extra.append({"name": "💬 Conteúdo", "value": f"`{content}`",
                          "inline": False})

        # Alvo = primeiro mentionado (se tiver).
        target = None
        for arg in ctx.message.mentions[:1] if ctx.message else []:
            target = arg

        # Envia log.
        await _send_event_log(
            self, ctx.guild, "mod", "mod_commands",
            title=f"Comando de Moderação Usado",
            description=(
                f"Um comando de moderação foi executado no servidor."
            ),
            severity="low",
            executor=ctx.author,
            target=target,
            extra_fields=extra)

        # Detecta uso suspeito (10+ comandos em 30s).
        recent = [t for (c, t) in dq if now - t <= 30]
        if len(recent) >= 10:
            await _send_event_log(
                self, ctx.guild, "suspicious", "suspicious",
                title="Uso Suspeito de Comandos",
                description=(
                    f"Um membro executou **{len(recent)} comandos** "
                    f"de moderação em **30 segundos**. Isso pode "
                    f"indicar comprometimento de conta."
                ),
                severity="high",
                executor=ctx.author,
                extra_fields=[
                    {"name": "Total", "value": f"`{len(recent)}`",
                     "inline": True},
                ])

    # ============================================================
    # LISTENER: MEMBROS
    # ============================================================

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Chamado quando alguém entra no servidor."""
        if member.bot:
            return  # não loga bots entrando
        cfg = await EventLogsManager.get_config(member.guild.id)
        if _is_ignored(cfg, member.id):
            return

        # Registra o join.
        now = time.time()
        dq = self._joins[member.guild.id]
        dq.append((member.id, now))

        await _send_event_log(
            self, member.guild, "member", "member_join",
            title="Membro Entrou",
            description=f"Um novo membro entrou no servidor.",
            severity="low",
            target=member,
            extra_fields=[
                {"name": "📅 Conta Criada",
                 "value": f"<t:{int(member.created_at.timestamp())}:R>",
                 "inline": True},
                {"name": "🆔 ID", "value": f"`{member.id}`",
                 "inline": True},
                {"name": "👥 Total",
                 "value": f"`{member.guild.member_count}`",
                 "inline": True},
            ])

        # Checa se é raid (muitos joins em pouco tempo).
        window = cfg.get("suspicious_join_window", 10)
        threshold = cfg.get("suspicious_join_threshold", 5)
        recent = [uid for (uid, t) in dq if now - t <= window]
        if len(recent) >= threshold:
            await _send_event_log(
                self, member.guild, "suspicious", "suspicious",
                title="Possível Raid Detectada",
                description=(
                    f"**{len(recent)} membros** entraram em apenas "
                    f"**{window} segundos**. Isso é um forte indício "
                    f"de raid em andamento."
                ),
                severity="high",
                target=member,
                extra_fields=[
                    {"name": "⚡ Frequência",
                     "value": f"`{len(recent)}` joins / `{window}s`",
                     "inline": True},
                    {"name": "🛡️ Ação Recomendada",
                     "value": "Use `>antiraid lockdown` se necessário",
                     "inline": False},
                ])

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member):
        """Chamado quando um membro sai (ou é kickado)."""
        if member.bot:
            return
        cfg = await EventLogsManager.get_config(member.guild.id)
        if _is_ignored(cfg, member.id):
            return

        # Se foi KICK, o anti-nuke já loga. Pra não duplicar, checa.
        try:
            async for entry in member.guild.audit_logs(
                    limit=5,
                    action=discord.AuditLogAction.kick):
                if (entry.target.id == member.id
                        and (discord.utils.utcnow()
                             - entry.created_at).total_seconds() < 5):
                    return  # foi kick, não é saída voluntária
        except Exception:
            pass

        await _send_event_log(
            self, member.guild, "member", "member_leave",
            title="Membro Saiu",
            description=f"Um membro saiu do servidor.",
            severity="low",
            target=member,
            extra_fields=[
                {"name": "📅 Entrou Em",
                 "value": (f"<t:{int(member.joined_at.timestamp())}:R>"
                           if member.joined_at else "—"),
                 "inline": True},
                {"name": "👥 Total",
                 "value": f"`{member.guild.member_count}`",
                 "inline": True},
            ])

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member,
                                 after: discord.Member):
        """Chamado quando algo do membro muda (nick, roles, avatar, boost)."""
        cfg = await EventLogsManager.get_config(after.guild.id)
        if _is_ignored(cfg, after.id):
            return

        # ─── Nick alterado. ───
        if before.nick != after.nick:
            await _send_event_log(
                self, after.guild, "member", "member_nick",
                title="Apelido Alterado",
                description="O apelido de um membro foi alterado.",
                severity="low",
                target=after,
                extra_fields=[
                    {"name": "Antes",
                     "value": f"`{before.nick or before.name}`",
                     "inline": True},
                    {"name": "Depois",
                     "value": f"`{after.nick or after.name}`",
                     "inline": True},
                ])

        # ─── Cargos alterados. ───
        if before.roles != after.roles:
            added = [r for r in after.roles if r not in before.roles]
            removed = [r for r in before.roles if r not in after.roles]

            if added or removed:
                # Tenta achar quem fez (via audit log).
                executor = None
                try:
                    async for entry in after.guild.audit_logs(
                            limit=3,
                            action=discord.AuditLogAction.member_role_update):
                        if (entry.target.id == after.id
                                and (discord.utils.utcnow()
                                     - entry.created_at).total_seconds() < 5):
                            executor = (after.guild.get_member(entry.user_id)
                                        if entry.user_id else None)
                            break
                except Exception:
                    pass

                # Monta lista de cargos adicionados/removidos.
                fields = []
                if added:
                    fields.append({
                        "name": "➕ Adicionados",
                        "value": " ".join(r.mention for r in added[:5]),
                        "inline": False})
                if removed:
                    fields.append({
                        "name": "➖ Removidos",
                        "value": " ".join(r.mention for r in removed[:5]),
                        "inline": False})

                await _send_event_log(
                    self, after.guild, "member", "member_roles",
                    title="Cargos Alterados",
                    description=f"Os cargos de um membro foram modificados.",
                    severity="low",
                    target=after,
                    executor=executor,
                    extra_fields=fields)

        # ─── Avatar alterado. ───
        if before.display_avatar != after.display_avatar:
            await _send_event_log(
                self, after.guild, "member", "member_avatar",
                title="Avatar Alterado",
                description="Um membro alterou o avatar.",
                severity="low",
                target=after)

        # ─── Novo boost. ───
        if not before.premium_since and after.premium_since:
            await _send_event_log(
                self, after.guild, "member", "member_boost",
                title="Novo Boost",
                description=f"Um membro acabou de dar **boost** no servidor! 🚀",
                severity="low",
                target=after)
        # ─── Boost removido. ───
        elif before.premium_since and not after.premium_since:
            await _send_event_log(
                self, after.guild, "member", "member_boost",
                title="Boost Perdido",
                description="Um membro removeu o boost do servidor.",
                severity="low",
                target=after)

    # ============================================================
    # LISTENER: MENSAGENS
    # ============================================================

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message,
                                after: discord.Message):
        """Chamado quando uma mensagem é editada."""
        if not before.guild or before.author.bot:
            return
        # Se o conteúdo NÃO mudou (ex: só atualizou embed), ignora.
        if before.content == after.content:
            return

        cfg = await EventLogsManager.get_config(before.guild.id)
        if _is_ignored(cfg, before.author.id, before.channel.id):
            return

        # Se a mensagem é muito curta, ignora.
        min_len = int(cfg.get("min_message_length", 3))
        if len(before.content or "") < min_len:
            return

        await _send_event_log(
            self, before.guild, "message", "message_edit",
            title="Mensagem Editada",
            description="Um membro editou uma mensagem.",
            severity="low",
            target=before.author,
            extra_fields=[
                {"name": "📍 Canal", "value": before.channel.mention,
                 "inline": True},
                {"name": "📎 Link",
                 "value": f"[Ir para mensagem]({after.jump_url})",
                 "inline": True},
                {"name": "Antes",
                 "value": (before.content or "*vazio*")[:400],
                 "inline": False},
                {"name": "Depois",
                 "value": (after.content or "*vazio*")[:400],
                 "inline": False},
            ])

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """Chamado quando uma mensagem é deletada."""
        if not message.guild or message.author.bot:
            return

        cfg = await EventLogsManager.get_config(message.guild.id)
        if _is_ignored(cfg, message.author.id, message.channel.id):
            return

        # Pequeno delay pro Discord atualizar o audit log.
        await asyncio.sleep(0.5)
        # Tenta descobrir quem deletou.
        executor = None
        try:
            async for entry in message.guild.audit_logs(
                    limit=3,
                    action=discord.AuditLogAction.message_delete):
                if (entry.target.id == message.author.id
                        and (discord.utils.utcnow()
                             - entry.created_at).total_seconds() < 3):
                    executor = (message.guild.get_member(entry.user_id)
                                if entry.user_id else None)
                    break
        except Exception:
            pass

        # Se foi o próprio bot, ignora.
        if executor and executor.id == self.bot.user.id:
            return

        fields = [
            {"name": "📍 Canal", "value": message.channel.mention,
             "inline": True},
        ]
        # Se tem conteúdo, mostra.
        if message.content:
            fields.append({
                "name": "💬 Conteúdo",
                "value": message.content[:400] or "*vazio*",
                "inline": False})
        # Se tem anexos, mostra os nomes.
        if message.attachments:
            fields.append({
                "name": "📎 Anexos",
                "value": "\n".join(a.filename
                                    for a in message.attachments[:5]),
                "inline": False})

        await _send_event_log(
            self, message.guild, "message", "message_delete",
            title="Mensagem Deletada",
            description="Uma mensagem foi deletada.",
            severity="low",
            target=message.author,
            executor=executor,
            extra_fields=fields)

    @commands.Cog.listener()
    async def on_bulk_message_delete(self, messages: List[discord.Message]):
        """Chamado quando VÁRIAS mensagens são deletadas (bulk delete)."""
        if not messages or not messages[0].guild:
            return
        guild = messages[0].guild

        cfg = await EventLogsManager.get_config(guild.id)
        if not cfg.get("log_message_bulk_delete"):
            return

        # Tenta achar quem fez.
        executor = None
        try:
            async for entry in guild.audit_logs(
                    limit=3,
                    action=discord.AuditLogAction.message_bulk_delete):
                if (discord.utils.utcnow()
                        - entry.created_at).total_seconds() < 5:
                    executor = (guild.get_member(entry.user_id)
                                if entry.user_id else None)
                    break
        except Exception:
            pass

        # Preview das primeiras 5 mensagens.
        preview = "\n".join(
            f"• **{m.author}**: {(m.content or '*vazio*')[:80]}"
            for m in messages[:5])

        await _send_event_log(
            self, guild, "message", "message_bulk_delete",
            title="Bulk Delete de Mensagens",
            description=(
                f"**{len(messages)} mensagens** foram deletadas de uma vez."
            ),
            severity="medium",
            executor=executor,
            extra_fields=[
                {"name": "📍 Canal",
                 "value": messages[0].channel.mention, "inline": True},
                {"name": "🗑️ Total",
                 "value": f"`{len(messages)}`", "inline": True},
                {"name": "📋 Preview", "value": preview or "—",
                 "inline": False},
            ])

    # ============================================================
    # LISTENER: CANAIS
    # ============================================================

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel):
        """Chamado quando um canal é criado."""
        cfg = await EventLogsManager.get_config(channel.guild.id)
        if not cfg.get("log_channel_create"):
            return

        # Tenta achar quem criou.
        executor = await self._get_audit_executor(
            channel.guild, discord.AuditLogAction.channel_create, channel.id)

        await _send_event_log(
            self, channel.guild, "channel", "channel_create",
            title="Canal Criado",
            description="Um novo canal foi criado.",
            severity="low",
            executor=executor,
            extra_fields=[
                {"name": "📁 Canal", "value": channel.mention,
                 "inline": True},
                {"name": "🏷️ Tipo", "value": f"`{channel.type}`",
                 "inline": True},
            ])

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        """Chamado quando um canal é deletado."""
        cfg = await EventLogsManager.get_config(channel.guild.id)
        if not cfg.get("log_channel_delete"):
            return

        executor = await self._get_audit_executor(
            channel.guild, discord.AuditLogAction.channel_delete, channel.id)

        await _send_event_log(
            self, channel.guild, "channel", "channel_delete",
            title="Canal Deletado",
            description="Um canal foi deletado.",
            severity="medium",
            executor=executor,
            extra_fields=[
                {"name": "📁 Nome", "value": f"`{channel.name}`",
                 "inline": True},
                {"name": "🏷️ Tipo", "value": f"`{channel.type}`",
                 "inline": True},
            ])

    # ═══════════════════════════════════════════════════════════
    # ⚠️ CORRIGIDO v1.1 — CategoryChannel não tem .topic
    # ═══════════════════════════════════════════════════════════
    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        """Chamado quando um canal é editado."""
        cfg = await EventLogsManager.get_config(after.guild.id)
        if not cfg.get("log_channel_update"):
            return

        # Detecta o tipo do canal — cada tipo tem atributos diferentes.
        is_text = isinstance(after, discord.TextChannel)
        is_voice = isinstance(after, discord.VoiceChannel)
        is_category = isinstance(after, discord.CategoryChannel)

        changes = []

        # ─── Nome (existe em todos). ───
        if before.name != after.name:
            changes.append({
                "name": "Nome",
                "value": f"`{before.name}` → `{after.name}`",
                "inline": False})

        # ─── Tópico (SÓ texto). ───
        if is_text:
            before_topic = getattr(before, "topic", None) or ""
            after_topic = getattr(after, "topic", None) or ""
            if before_topic != after_topic:
                changes.append({
                    "name": "Tópico",
                    "value": f"`{before_topic[:100]}` → "
                             f"`{after_topic[:100]}`",
                    "inline": False})

        # ─── Slowmode (SÓ texto). ───
        if is_text:
            before_sm = getattr(before, "slowmode_delay", 0)
            after_sm = getattr(after, "slowmode_delay", 0)
            if before_sm != after_sm:
                changes.append({
                    "name": "Slowmode",
                    "value": f"`{before_sm}s` → `{after_sm}s`",
                    "inline": True})

        # ─── NSFW (SÓ texto). ───
        if is_text:
            before_nsfw = getattr(before, "nsfw", False)
            after_nsfw = getattr(after, "nsfw", False)
            if before_nsfw != after_nsfw:
                changes.append({
                    "name": "NSFW",
                    "value": f"`{before_nsfw}` → `{after_nsfw}`",
                    "inline": True})

        # ─── Bitrate (SÓ voz). ───
        if is_voice:
            before_br = getattr(before, "bitrate", 0)
            after_br = getattr(after, "bitrate", 0)
            if before_br != after_br:
                changes.append({
                    "name": "Bitrate",
                    "value": f"`{before_br}` → `{after_br}`",
                    "inline": True})

        # ─── User limit (SÓ voz). ───
        if is_voice:
            before_ul = getattr(before, "user_limit", 0)
            after_ul = getattr(after, "user_limit", 0)
            if before_ul != after_ul:
                changes.append({
                    "name": "Limite de usuários",
                    "value": f"`{before_ul}` → `{after_ul}`",
                    "inline": True})

        # ─── Posição (todos). ───
        if before.position != after.position:
            changes.append({
                "name": "Posição",
                "value": f"`{before.position}` → `{after.position}`",
                "inline": True})

        # Se nada mudou, sai.
        if not changes:
            return

        # Tenta achar quem mudou.
        executor = await self._get_audit_executor(
            after.guild, discord.AuditLogAction.channel_update, after.id)

        # Tipo do canal.
        tipo = (
            "📁 Categoria" if is_category
            else "🔊 Voz" if is_voice
            else "💬 Texto"
        )

        await _send_event_log(
            self, after.guild, "channel", "channel_update",
            title="Canal Atualizado",
            description=f"Um canal ({tipo}) foi modificado.",
            severity="low",
            executor=executor,
            extra_fields=[
                {"name": "📁 Canal",
                 "value": f"{after.mention} `({after.id})`",
                 "inline": True},
            ] + changes)

    # ============================================================
    # LISTENER: CARGOS
    # ============================================================

    @commands.Cog.listener()
    async def on_guild_role_create(self, role):
        """Chamado quando um cargo é criado."""
        cfg = await EventLogsManager.get_config(role.guild.id)
        if not cfg.get("log_role_create"):
            return

        executor = await self._get_audit_executor(
            role.guild, discord.AuditLogAction.role_create, role.id)

        await _send_event_log(
            self, role.guild, "role", "role_create",
            title="Cargo Criado",
            description="Um novo cargo foi criado.",
            severity="low",
            executor=executor,
            extra_fields=[
                {"name": "🏷️ Cargo", "value": role.mention,
                 "inline": True},
                {"name": "🎨 Cor", "value": str(role.color),
                 "inline": True},
                {"name": "📊 Posição", "value": f"`{role.position}`",
                 "inline": True},
            ])

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        """Chamado quando um cargo é deletado."""
        cfg = await EventLogsManager.get_config(role.guild.id)
        if not cfg.get("log_role_delete"):
            return

        executor = await self._get_audit_executor(
            role.guild, discord.AuditLogAction.role_delete, role.id)

        await _send_event_log(
            self, role.guild, "role", "role_delete",
            title="Cargo Deletado",
            description="Um cargo foi deletado.",
            severity="medium",
            executor=executor,
            extra_fields=[
                {"name": "🏷️ Nome", "value": f"`{role.name}`",
                 "inline": True},
                {"name": "👥 Membros",
                 "value": f"`{len(role.members)}`", "inline": True},
            ])

    @commands.Cog.listener()
    async def on_guild_role_update(self, before, after):
        """Chamado quando um cargo é editado."""
        cfg = await EventLogsManager.get_config(after.guild.id)
        if not cfg.get("log_role_update"):
            return

        changes = []
        # Nome.
        if before.name != after.name:
            changes.append({
                "name": "Nome",
                "value": f"`{before.name}` → `{after.name}`",
                "inline": False})
        # Cor.
        if before.color != after.color:
            changes.append({
                "name": "Cor",
                "value": f"`{before.color}` → `{after.color}`",
                "inline": True})
        # Permissões.
        if before.permissions != after.permissions:
            gained = []
            for perm, value in after.permissions:
                if value and not getattr(before.permissions, perm, False):
                    gained.append(perm)
            lost = []
            for perm, value in before.permissions:
                if value and not getattr(after.permissions, perm, False):
                    lost.append(perm)
            if gained:
                changes.append({
                    "name": "🟢 Adquiridas",
                    "value": ", ".join(f"`{p}`" for p in gained[:8]),
                    "inline": False})
            if lost:
                changes.append({
                    "name": "🔴 Removidas",
                    "value": ", ".join(f"`{p}`" for p in lost[:8]),
                    "inline": False})

        if not changes:
            return

        executor = await self._get_audit_executor(
            after.guild, discord.AuditLogAction.role_update, after.id)

        await _send_event_log(
            self, after.guild, "role", "role_update",
            title="Cargo Atualizado",
            description="Um cargo foi modificado.",
            severity="low",
            executor=executor,
            extra_fields=[{"name": "🏷️ Cargo", "value": after.mention,
                            "inline": True}] + changes)

    # ============================================================
    # LISTENER: VOZ
    # ============================================================

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """Chamado quando alguém muda de estado de voz."""
        if member.bot:
            return
        cfg = await EventLogsManager.get_config(member.guild.id)
        if _is_ignored(cfg, member.id):
            return

        # ─── Entrou em canal de voz. ───
        if before.channel is None and after.channel is not None:
            await _send_event_log(
                self, member.guild, "voice", "voice_join",
                title="Entrou em Canal de Voz",
                description="Um membro entrou em canal de voz.",
                severity="low",
                target=member,
                extra_fields=[
                    {"name": "🔊 Canal",
                     "value": after.channel.mention, "inline": True},
                ])

        # ─── Saiu de canal de voz. ───
        elif before.channel is not None and after.channel is None:
            await _send_event_log(
                self, member.guild, "voice", "voice_leave",
                title="Saiu de Canal de Voz",
                description="Um membro saiu de canal de voz.",
                severity="low",
                target=member,
                extra_fields=[
                    {"name": "🔊 Canal",
                     "value": before.channel.mention, "inline": True},
                ])

        # ─── Moveu de canal. ───
        elif (before.channel is not None and after.channel is not None
                and before.channel != after.channel):
            await _send_event_log(
                self, member.guild, "voice", "voice_move",
                title="Moveu de Canal",
                description="Um membro mudou de canal de voz.",
                severity="low",
                target=member,
                extra_fields=[
                    {"name": "De", "value": before.channel.mention,
                     "inline": True},
                    {"name": "Para", "value": after.channel.mention,
                     "inline": True},
                ])

        # ─── Mutado/desmutado. ───
        if before.mute != after.mute:
            await _send_event_log(
                self, member.guild, "voice", "voice_mute",
                title=("Mutado" if after.mute else "Desmutado"),
                description="Estado de mute alterado.",
                severity="low",
                target=member)

        # ─── Ensurdecido/desensurdecido. ───
        if before.deaf != after.deaf:
            await _send_event_log(
                self, member.guild, "voice", "voice_mute",
                title=("Ensurdecido" if after.deaf else "Desensurdecido"),
                description="Estado de deafen alterado.",
                severity="low",
                target=member)

    # ============================================================
    # LISTENER: CONVITES
    # ============================================================

    @commands.Cog.listener()
    async def on_invite_create(self, invite):
        """Chamado quando um convite é criado."""
        if not invite.guild:
            return
        cfg = await EventLogsManager.get_config(invite.guild.id)
        if not cfg.get("log_invite_create"):
            return

        await _send_event_log(
            self, invite.guild, "invite", "invite_create",
            title="Convite Criado",
            description="Um novo convite foi criado.",
            severity="low",
            executor=invite.inviter,
            extra_fields=[
                {"name": "🔗 Código", "value": f"`{invite.code}`",
                 "inline": True},
                {"name": "📍 Canal",
                 "value": invite.channel.mention if invite.channel else "—",
                 "inline": True},
                {"name": "⏰ Expira",
                 "value": (f"<t:{int(invite.expires_at.timestamp())}:R>"
                           if invite.expires_at else "Nunca"),
                 "inline": True},
                {"name": "📊 Usos Máx",
                 "value": f"`{invite.max_uses or '∞'}`", "inline": True},
            ])

    @commands.Cog.listener()
    async def on_invite_delete(self, invite):
        """Chamado quando um convite é removido."""
        if not invite.guild:
            return
        cfg = await EventLogsManager.get_config(invite.guild.id)
        if not cfg.get("log_invite_delete"):
            return

        await _send_event_log(
            self, invite.guild, "invite", "invite_delete",
            title="Convite Deletado",
            description="Um convite foi removido.",
            severity="low",
            extra_fields=[
                {"name": "🔗 Código", "value": f"`{invite.code}`",
                 "inline": True},
            ])

    # ============================================================
    # LISTENER: THREADS
    # ============================================================

    @commands.Cog.listener()
    async def on_thread_create(self, thread):
        """Chamado quando uma thread é criada."""
        if not thread.guild:
            return
        cfg = await EventLogsManager.get_config(thread.guild.id)
        if not cfg.get("log_thread_create"):
            return

        await _send_event_log(
            self, thread.guild, "thread", "thread_create",
            title="Thread Criada",
            description="Uma nova thread foi criada.",
            severity="low",
            executor=thread.owner,
            extra_fields=[
                {"name": "🧵 Thread", "value": thread.mention,
                 "inline": True},
                {"name": "📍 Canal pai",
                 "value": (thread.parent.mention if thread.parent else "—"),
                 "inline": True},
            ])

    @commands.Cog.listener()
    async def on_thread_delete(self, thread):
        """Chamado quando uma thread é deletada."""
        if not thread.guild:
            return
        cfg = await EventLogsManager.get_config(thread.guild.id)
        if not cfg.get("log_thread_delete"):
            return

        await _send_event_log(
            self, thread.guild, "thread", "thread_delete",
            title="Thread Deletada",
            description="Uma thread foi removida.",
            severity="low",
            extra_fields=[
                {"name": "🧵 Nome", "value": f"`{thread.name}`",
                 "inline": True},
                {"name": "📍 Canal pai",
                 "value": (thread.parent.mention if thread.parent else "—"),
                 "inline": True},
            ])

    # ============================================================
    # LISTENER: EMOJIS
    # ============================================================

    @commands.Cog.listener()
    async def on_guild_emojis_update(self, guild, before, after):
        """Chamado quando um emoji é adicionado/removido."""
        cfg = await EventLogsManager.get_config(guild.id)
        if _is_ignored(cfg):
            return

        # Compara IDs antes e depois.
        before_ids = {e.id for e in before}
        after_ids = {e.id for e in after}

        added = [e for e in after if e.id not in before_ids]
        removed = [e for e in before if e.id not in after_ids]

        # Loga cada adição.
        for emoji in added:
            if not cfg.get("log_emoji_create"):
                continue
            await _send_event_log(
                self, guild, "emoji", "emoji_create",
                title="Emoji Adicionado",
                description="Um novo emoji foi adicionado.",
                severity="low",
                extra_fields=[
                    {"name": "😀 Nome", "value": f"`{emoji.name}`",
                     "inline": True},
                    {"name": "🆔 ID", "value": f"`{emoji.id}`",
                     "inline": True},
                ])

        # Loga cada remoção.
        for emoji in removed:
            if not cfg.get("log_emoji_delete"):
                continue
            await _send_event_log(
                self, guild, "emoji", "emoji_delete",
                title="Emoji Removido",
                description="Um emoji foi removido.",
                severity="low",
                extra_fields=[
                    {"name": "😀 Nome", "value": f"`{emoji.name}`",
                     "inline": True},
                ])

    # ============================================================
    # HELPERS: AUDIT EXECUTOR
    # ============================================================

    async def _get_audit_executor(self, guild: discord.Guild,
                                    action: discord.AuditLogAction,
                                    target_id: int) -> Optional[discord.Member]:
        """Descobre quem executou uma ação (via audit log)."""
        try:
            async for entry in guild.audit_logs(limit=5, action=action):
                if entry.target.id == target_id:
                    # Só considera se foi recente (últimos 5s).
                    if (discord.utils.utcnow()
                            - entry.created_at).total_seconds() < 5:
                        if entry.user_id:
                            return guild.get_member(entry.user_id)
                    break
        except Exception:
            pass
        return None

    # ============================================================
    # COMANDOS DE CONFIG
    # ============================================================

    @commands.group(name="eventlogs", aliases=["elogs", "evlog"],
                    invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def eventlogs(self, ctx):
        """Painel de configuração dos logs de eventos."""
        cfg = await EventLogsManager.get_config(ctx.guild.id)

        def onoff(v):
            return "✅" if v else "❌"

        embed = discord.Embed(
            title="📋 Logs de Eventos",
            description=(
                "Configure quais eventos são logados.\n"
                "Os canais são configurados em `>logs context`."
            ),
            color=discord.Color.blurple(),
            timestamp=_now())

        # Cada bloco mostra o status dos logs dessa categoria.
        embed.add_field(
            name="🛡️ Moderação",
            value=(
                f"{onoff(cfg['log_mod_kick'])} Kick\n"
                f"{onoff(cfg['log_mod_ban'])} Ban\n"
                f"{onoff(cfg['log_mod_unban'])} Unban\n"
                f"{onoff(cfg['log_mod_timeout'])} Timeout\n"
                f"{onoff(cfg['log_mod_warn'])} Warn\n"
                f"{onoff(cfg['log_mod_strike'])} Strike\n"
                f"{onoff(cfg['log_mod_purge'])} Purge"
            ),
            inline=True)
        embed.add_field(
            name="👥 Membros",
            value=(
                f"{onoff(cfg['log_member_join'])} Join\n"
                f"{onoff(cfg['log_member_leave'])} Leave\n"
                f"{onoff(cfg['log_member_nick'])} Nick\n"
                f"{onoff(cfg['log_member_roles'])} Roles\n"
                f"{onoff(cfg['log_member_avatar'])} Avatar\n"
                f"{onoff(cfg['log_member_boost'])} Boost"
            ),
            inline=True)
        embed.add_field(
            name="💬 Mensagens",
            value=(
                f"{onoff(cfg['log_message_edit'])} Edit\n"
                f"{onoff(cfg['log_message_delete'])} Delete\n"
                f"{onoff(cfg['log_message_bulk_delete'])} Bulk Delete"
            ),
            inline=True)
        embed.add_field(
            name="📁 Canais",
            value=(
                f"{onoff(cfg['log_channel_create'])} Create\n"
                f"{onoff(cfg['log_channel_delete'])} Delete\n"
                f"{onoff(cfg['log_channel_update'])} Update"
            ),
            inline=True)
        embed.add_field(
            name="🏷️ Cargos",
            value=(
                f"{onoff(cfg['log_role_create'])} Create\n"
                f"{onoff(cfg['log_role_delete'])} Delete\n"
                f"{onoff(cfg['log_role_update'])} Update"
            ),
            inline=True)
        embed.add_field(
            name="🔊 Voz",
            value=(
                f"{onoff(cfg['log_voice_join'])} Join\n"
                f"{onoff(cfg['log_voice_leave'])} Leave\n"
                f"{onoff(cfg['log_voice_move'])} Move"
            ),
            inline=True)
        embed.add_field(
            name="🎯 Comandos de Moderação",
            value=(
                f"{onoff(cfg['log_mod_commands'])} Log mod commands\n"
                f"{onoff(cfg['log_all_commands'])} Log ALL commands"
            ),
            inline=False)
        embed.add_field(
            name="🚨 Suspeitas",
            value=(
                f"{onoff(cfg['log_suspicious'])} Ativo\n"
                f"**Join watch:** `{cfg['suspicious_join_threshold']}` / "
                f"`{cfg['suspicious_join_window']}s`"
            ),
            inline=False)
        embed.add_field(
            name="📌 Comandos",
            value=(
                "`>eventlogs on/off`\n"
                "`>eventlogs toggle <key>`\n"
                "`>eventlogs ignoreuser @user`\n"
                "`>eventlogs ignorechannel #canal`\n"
                "`>eventlogs reset`"
            ),
            inline=False)

        await ctx.send(embed=embed)

    @eventlogs.command(name="on")
    @commands.has_permissions(administrator=True)
    async def elogs_on(self, ctx):
        """Liga os logs de eventos."""
        await EventLogsManager.update(ctx.guild.id, "enabled", True)
        await ctx.send(embed=embed_success(
            "✅ Logs de eventos ativados."))

    @eventlogs.command(name="off")
    @commands.has_permissions(administrator=True)
    async def elogs_off(self, ctx):
        """Desliga os logs de eventos."""
        await EventLogsManager.update(ctx.guild.id, "enabled", False)
        await ctx.send(embed=embed_success(
            "✅ Logs de eventos desativados."))

    @eventlogs.command(name="toggle")
    @commands.has_permissions(administrator=True)
    async def elogs_toggle(self, ctx, key: str):
        """Alterna um log específico (on/off)."""
        cfg = await EventLogsManager.get_config(ctx.guild.id)
        # Adiciona prefixo "log_" se não tiver.
        if not key.startswith("log_"):
            key = f"log_{key}"
        if key not in DEFAULT_CONFIG:
            return await ctx.send(embed=embed_error(
                f"❌ Chave `{key}` não existe."))
        current = cfg.get(key, True)
        new_value = not current
        await EventLogsManager.update(ctx.guild.id, key, new_value)
        await ctx.send(embed=embed_success(
            f"✅ `{key}` → {'ON' if new_value else 'OFF'}"))

    @eventlogs.command(name="ignoreuser")
    @commands.has_permissions(administrator=True)
    async def elogs_ignore_user(self, ctx, member: discord.Member):
        """Adiciona um user à lista de ignorados."""
        cfg = await EventLogsManager.get_config(ctx.guild.id)
        users = list(cfg.get("ignore_users", []))
        if member.id not in users:
            users.append(member.id)
            await EventLogsManager.update(ctx.guild.id, "ignore_users", users)
        await ctx.send(embed=embed_success(
            f"✅ {member.mention} ignorado nos logs."))

    @eventlogs.command(name="unignoreuser")
    @commands.has_permissions(administrator=True)
    async def elogs_unignore_user(self, ctx, member: discord.Member):
        """Remove um user dos ignorados."""
        cfg = await EventLogsManager.get_config(ctx.guild.id)
        users = [u for u in cfg.get("ignore_users", []) if u != member.id]
        await EventLogsManager.update(ctx.guild.id, "ignore_users", users)
        await ctx.send(embed=embed_success(
            f"✅ {member.mention} removido do ignore."))

    @eventlogs.command(name="ignorechannel")
    @commands.has_permissions(administrator=True)
    async def elogs_ignore_channel(self, ctx, channel: discord.TextChannel):
        """Adiciona um canal à lista de ignorados."""
        cfg = await EventLogsManager.get_config(ctx.guild.id)
        chans = list(cfg.get("ignore_channels", []))
        if channel.id not in chans:
            chans.append(channel.id)
            await EventLogsManager.update(ctx.guild.id, "ignore_channels", chans)
        await ctx.send(embed=embed_success(
            f"✅ {channel.mention} ignorado nos logs."))

    @eventlogs.command(name="reset")
    @commands.has_permissions(administrator=True)
    async def elogs_reset(self, ctx):
        """Reseta a config de logs de eventos."""
        # O reset também usa Motor: aguarda a remoção antes de limpar o cache
        # e antes de confirmar ao administrador que a configuração foi apagada.
        db = await get_connection_async()
        await db["event_logs_config"].delete_one(
            {"guild_id": ctx.guild.id})
        EventLogsManager.invalidate(ctx.guild.id)
        await ctx.send(embed=embed_warning(
            "⚠️ Configuração de logs de eventos resetada."))


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("EventLogsCommands") is None:
        await bot.add_cog(EventLogsCommands(bot))