# ============================================================
# COMMANDS_BOT_WHITELIST_PROTECTION.PY - v1.2
# PROTEÇÃO ESPECIAL PARA BOTS NA WHITELIST
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Bots whitelisted (confiáveis) TÊM proteção especial:
#   • NUNCA são banidos direto
#   • Só podem ser EXULSOS (kick) com confirmação via DM
#   • Modo "never_kick": nunca são expulsos
#   • Threshold proporcional (whitelisted = normal + 1)
#
# Isso evita que um falso positivo do anti-nuke bane um bot
# confiável por engano.
#
# v1.2 FIX:
# - Removidas TODAS as f-strings aninhadas (SyntaxError < 3.12)
# ============================================================

import asyncio
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Union

import discord
from discord.ext import commands, tasks

from config import BOT_APPROVAL_TIMEOUT
from database import get_connection_async
from embeds import embed_success, embed_error, embed_warning, embed_info
from embeds_professional import ProfessionalLog, fmt_reason
from utils import TTLCache


def _now() -> datetime:
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# CONFIGURAÇÃO PADRÃO
# ============================================================

DEFAULT_CONFIG = {
    "enabled": True,                    # sistema ligado
    "modo": "threshold",                # threshold ou immune (never_kick legado)
    # Mantido por compatibilidade com configs antigas; ignorado.
    # Bots whitelisted no modo threshold SEMPRE exigem confirmação.
    "auto_kick_whitelisted": False,
    "expira_confirmacao_min": max(1, int(BOT_APPROVAL_TIMEOUT / 60)),  # ambiente usa segundos; painel usa minutos
    "notificar_owner": True,            # DM pro dono
    "cargos_confirmar": [],             # cargos que podem confirmar
    "users_confirmar": [],              # users que podem confirmar
    # Exceções: cargo X pode confirmar, EXCETO user Y.
    "cargos_confirmar_exceto": {},
    "users_confirmar_exceto": {},
    "log_canal": 0,                     # canal de log
    "warn_sem_permissao": True,         # dá warn em quem tenta sem permissão
}


# ============================================================
# MANAGER
# ============================================================

class BotWhitelistManager:
    _cache = TTLCache(max_size=200, ttl=60)

    @classmethod
    async def get_config(cls, guild_id: int) -> dict:
        """Pega config (cache OU banco)."""
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached
        db = await get_connection_async()
        doc = await db["bot_whitelist_protection_config"].find_one(
            {"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def get_config_fresh(cls, guild_id: int) -> dict:
        """Lê diretamente do Mongo antes de decidir imunidade/punição."""
        db = await get_connection_async()
        doc = await db["bot_whitelist_protection_config"].find_one(
            {"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def update(cls, guild_id: int, key: str, value):
        """Salva uma config."""
        db = await get_connection_async()
        # Aguarda a persistência para não anunciar uma configuração ainda não salva.
        await db["bot_whitelist_protection_config"].update_one(
            {"guild_id": guild_id}, {"$set": {key: value}}, upsert=True)
        cls._cache.invalidate(guild_id)

    @classmethod
    def invalidate(cls, guild_id: int):
        cls._cache.invalidate(guild_id)


# ============================================================
# PERMISSÕES (quem pode confirmar)
# ============================================================

def _pode_confirmar(cfg: dict, member: discord.Member) -> bool:
    """
    Diz se um membro pode confirmar expulsão de bot whitelisted.
    Regras:
    - Owner sempre pode.
    - Users na lista podem (a não ser que estejam na exceção).
    - Cargos na lista podem (a não ser que o user esteja na exceção).
    """
    if member.id == member.guild.owner_id:
        return True

    # Checa se o user tá na lista direta.
    if member.id in cfg.get("users_confirmar", []):
        exceto = cfg.get("users_confirmar_exceto", {}).get(
            str(member.id), [])
        if member.id not in exceto:
            return True

    # Checa se algum cargo dele pode confirmar.
    cargos = set(cfg.get("cargos_confirmar", []))
    exceto_por_cargo = cfg.get("cargos_confirmar_exceto", {})
    for r in member.roles:
        if r.id in cargos:
            # Se o user está na exceção desse cargo, pula.
            excepted = exceto_por_cargo.get(str(r.id), [])
            if member.id not in excepted:
                return True
    return False


# ============================================================
# VIEW DE CONFIRMAÇÃO (botões por DM)
# ============================================================

class BotKickConfirmView(discord.ui.View):
    def __init__(self, cog, confirm_id: str, guild: discord.Guild):
        super().__init__(timeout=None)
        self.cog = cog
        self.confirm_id = confirm_id
        self.guild = guild

    @discord.ui.button(label="✅ Expulsar bot",
                        style=discord.ButtonStyle.danger,
                        custom_id="botkick_yes")
    async def confirmar(self, interaction: discord.Interaction,
                         button: discord.ui.Button):
        """Botão de CONFIRMAR."""
        await self.cog._processar_voto(interaction, self.confirm_id, True)

    @discord.ui.button(label="❌ Cancelar",
                        style=discord.ButtonStyle.secondary,
                        custom_id="botkick_no")
    async def cancelar(self, interaction: discord.Interaction,
                        button: discord.ui.Button):
        """Botão de CANCELAR."""
        await self.cog._processar_voto(interaction, self.confirm_id, False)


# ============================================================
# COG
# ============================================================

class BotWhitelistProtectionCommands(commands.Cog):
    """Proteção especial para bots na whitelist."""

    def __init__(self, bot):
        self.bot = bot
        # Confirmações pendentes: {id: info}
        self._pendentes: Dict[str, dict] = {}
        # Resultados finais: {id: "aprovado"/"rejeitado"/"expirou"}
        self._resultados: Dict[str, str] = {}
        # Task que expira confirmações antigas.
        self._expire_task.start()

    def cog_unload(self):
        try:
            self._expire_task.cancel()
        except Exception:
            pass

    # ============================================================
    # EXPIRAÇÃO AUTOMÁTICA
    # ============================================================

    @tasks.loop(seconds=30)
    async def _expire_task(self):
        """Roda a cada 30s e expira confirmações antigas."""
        now = _now()
        for cid in list(self._pendentes.keys()):
            info = self._pendentes[cid]
            if now >= info["expires_at"]:
                await self._finalizar(cid, "expirou")
                self._pendentes.pop(cid, None)

    @_expire_task.before_loop
    async def _before_expire(self):
        await self.bot.wait_until_ready()

    # ============================================================
    # API PÚBLICA (chamada pelo anti-nuke)
    # ============================================================

    async def proteger_bot_whitelisted(
        self,
        guild: discord.Guild,
        bot_member: discord.Member,
        motivo: str,
        action_type: str = "unknown",
        count_action: int = 0,
        count_global: int = 0,
    ) -> bool:
        """
        Aplica a proteção especial a um bot whitelisted.
        Decide entre:
        - immune/never_kick: imunidade total
        - threshold: pede confirmação via DM antes do kick
        """
        try:
            cfg = await BotWhitelistManager.get_config_fresh(guild.id)
        except Exception:
            return False
        if not cfg.get("enabled"):
            return False

        modo = cfg.get("modo", "threshold")

        # Modo imune: não aplica qualquer ação automática ao bot.
        if modo in ("immune", "never_kick"):
            await self._logar_bloqueado(
                guild, bot_member, motivo, action_type,
                "Modo **never_kick** ativo — bot NÃO pode ser expulso "
                "sem liberação manual do admin.")
            return False

        # Regra definida pelo administrador: bot na whitelist em modo
        # threshold nunca recebe kick automático. Toda expulsão exige voto.
        # A opção antiga auto_kick_whitelisted é ignorada por segurança.
        return await self._pedir_confirmacao(
            guild, bot_member, motivo, action_type,
            count_action, count_global)

    # ============================================================
    # PEDIR CONFIRMAÇÃO POR DM
    # ============================================================

    async def _pedir_confirmacao(
        self,
        guild: discord.Guild,
        bot_member: discord.Member,
        motivo: str,
        action_type: str,
        count_action: int,
        count_global: int,
    ) -> bool:
        """Envia DM pra quem pode confirmar. Aguarda resposta."""
        try:
            cfg = await BotWhitelistManager.get_config_fresh(guild.id)
        except Exception:
            return False

        # Lista quem pode confirmar.
        confirmadores = await self._listar_confirmadores(guild, cfg)
        if not confirmadores:
            await self._logar_bloqueado(
                guild, bot_member, motivo, action_type,
                "Nenhum confirmador configurado — ação cancelada.")
            return False

        # Gera um ID único pra essa confirmação.
        cid = secrets.token_urlsafe(8)
        expires_at = _now() + timedelta(
            minutes=int(cfg.get("expira_confirmacao_min", 5)))

        # Guarda info da confirmação.
        info = {
            "guild_id": guild.id,
            "bot_id": bot_member.id,
            "motivo": motivo,
            "action_type": action_type,
            "count_action": count_action,
            "count_global": count_global,
            "votes": {},
            "message_ids": {},
            "expires_at": expires_at,
        }
        self._pendentes[cid] = info

        # Cria a view com botões.
        view = BotKickConfirmView(self, cid, guild)

        # Monta o embed.
        log = ProfessionalLog(
            title="⚠️ Confirmação Necessária",
            description=(
                f"Um **bot na whitelist** executou ações suspeitas e "
                f"pode ser **expulso** do servidor.\n\n"
                f"Precisamos da sua confirmação."
            ),
            severity="high",
            footer="Stumble Hit AntiRaid • Proteção de Bot Whitelisted")
        log.set_target(bot_member)
        log.add_field("Ação Detectada", f"`{action_type}`")
        log.add_field(
            "Contadores",
            f"Ação: `{count_action}` | Global: `{count_global}`",
            inline=False)
        log.add_field("Motivo", fmt_reason(motivo), inline=False)
        log.add_field(
            "⏰ Expira em",
            f"<t:{int(expires_at.timestamp())}:R>",
            inline=False)
        log.add_field(
            "⚠️ Importante",
            "Este bot está na whitelist. Ele **NÃO será banido**, "
            "apenas expulso (kick).",
            inline=False)
        embed = log.build()
        embed.set_footer(text=f"ID: {cid}")

        # Envia pra cada confirmador.
        enviados = 0
        for conf in confirmadores:
            try:
                msg = await conf.send(embed=embed, view=view)
                info["message_ids"][conf.id] = msg.id
                enviados += 1
            except Exception:
                pass

        # Ninguém recebeu? Cancela.
        if enviados == 0:
            self._pendentes.pop(cid, None)
            await self._logar_bloqueado(
                guild, bot_member, motivo, action_type,
                "Nenhum admin pôde ser notificado — ação cancelada.")
            return False

        # ─── Aguarda até alguém votar ou expirar. ───
        # (Loop que checa a cada 1s se ainda tá pendente.)
        while cid in self._pendentes:
            await asyncio.sleep(1)

        resultado = self._resultados.get(cid, "expirou")
        self._resultados.pop(cid, None)

        # Aprovado → revalida o modo e a whitelist antes de executar.
        if resultado == "aprovado":
            try:
                live_cfg = await BotWhitelistManager.get_config_fresh(guild.id)
                from commands_antinuke import AntiNukeManager
                live_antinuke = AntiNukeManager.get_config_fresh(guild.id)
            except Exception:
                return False
            live_member = guild.get_member(bot_member.id)
            if live_member is None:
                return False
            is_still_whitelisted = bot_member.id in {
                int(x) for x in live_antinuke.get("whitelist_bots", [])
            }
            if not live_antinuke.get("enabled", False):
                return False
            if not is_still_whitelisted:
                # A autorização foi revogada enquanto a confirmação estava
                # pendente. O bot passa a seguir a regra normal: banimento.
                try:
                    await guild.ban(
                        live_member,
                        reason=f"AntiNuke: whitelist de bot revogada ({motivo})",
                        delete_message_days=0)
                    return True
                except Exception:
                    return False
            if (not live_cfg.get("enabled", True)
                    or str(live_cfg.get("modo", "threshold")).lower()
                    in ("immune", "never_kick")):
                return False
            try:
                await live_member.kick(
                    reason=f"Bot Whitelist Protection: {motivo}")
            except Exception:
                return False
            await self._logar_kick_confirmado(
                guild, live_member, motivo, action_type)
            return True

        # Rejeitado/expirado → só loga.
        await self._logar_bloqueado(
            guild, bot_member, motivo, action_type,
            f"Confirmação {resultado}.")
        return False

    async def _listar_confirmadores(self, guild: discord.Guild,
                                      cfg: dict) -> List[discord.Member]:
        """Monta lista de quem pode confirmar (owner + users + cargos)."""
        confirmadores = []
        seen = set()

        # Owner sempre.
        try:
            owner = guild.owner or await guild.fetch_member(guild.owner_id)
            if owner:
                confirmadores.append(owner)
                seen.add(owner.id)
        except Exception:
            pass

        # Users específicos.
        for uid in cfg.get("users_confirmar", []):
            if uid in seen:
                continue
            m = guild.get_member(uid)
            if m and _pode_confirmar(cfg, m):
                confirmadores.append(m)
                seen.add(uid)

        # Membros dos cargos.
        for rid in cfg.get("cargos_confirmar", []):
            role = guild.get_role(rid)
            if not role:
                continue
            for m in role.members:
                if m.id in seen or m.bot:
                    continue
                if _pode_confirmar(cfg, m):
                    confirmadores.append(m)
                    seen.add(m.id)

        return confirmadores

    # ============================================================
    # VOTOS
    # ============================================================

    async def _processar_voto(self, interaction: discord.Interaction,
                                confirm_id: str, aprovado: bool):
        """Processa um voto (aprovar ou rejeitar)."""
        # Confirmação já foi resolvida?
        if confirm_id not in self._pendentes:
            return await interaction.response.send_message(
                "⚠️ Esta confirmação já foi resolvida ou expirou.",
                ephemeral=True)

        info = self._pendentes[confirm_id]
        guild = self.bot.get_guild(info["guild_id"])
        if not guild:
            return await interaction.response.send_message(
                "❌ Servidor não encontrado.", ephemeral=True)

        try:
            cfg = await BotWhitelistManager.get_config_fresh(guild.id)
        except Exception:
            return await interaction.response.send_message(
                "❌ Não foi possível validar as permissões atuais.",
                ephemeral=True)
        autor = guild.get_member(interaction.user.id)

        # Verifica se pode votar.
        if not autor or not _pode_confirmar(cfg, autor):
            await self._tentar_sem_permissao(guild, interaction.user, info)
            return await interaction.response.send_message(
                "❌ Você **não tem permissão** para confirmar.",
                ephemeral=True)

        # Registra o voto.
        info["votes"][interaction.user.id] = aprovado

        # ─── REJEITADO. ───
        if not aprovado:
            self._resultados[confirm_id] = "rejeitado"
            await interaction.response.send_message(
                "❌ Você **cancelou** a expulsão.", ephemeral=True)
            await self._finalizar(confirm_id, "rejeitado")
            self._pendentes.pop(confirm_id, None)
            return

        # ─── APROVADO. ───
        self._resultados[confirm_id] = "aprovado"
        await interaction.response.send_message(
            "✅ Você **aprovou** a expulsão. Executando...",
            ephemeral=True)
        await self._finalizar(confirm_id, "aprovado")
        self._pendentes.pop(confirm_id, None)

    async def _finalizar(self, confirm_id: str, resultado: str):
        """Finaliza uma confirmação (edita as mensagens originais)."""
        info = self._pendentes.get(confirm_id)
        if not info:
            return

        guild = self.bot.get_guild(info["guild_id"])
        if not guild:
            return

        # Edita cada DM que foi enviada.
        for admin_id, msg_id in info.get("message_ids", {}).items():
            try:
                admin = guild.get_member(admin_id)
                if not admin:
                    continue
                msg = await admin.fetch_message(msg_id)
                # Cor conforme resultado.
                color = (discord.Color.green() if resultado == "aprovado"
                         else discord.Color.red() if resultado == "rejeitado"
                         else discord.Color.greyple())
                # Edita removendo os botões.
                await msg.edit(
                    embed=discord.Embed(
                        title=f"Resultado: {resultado.title()}",
                        description=(
                            f"Expulsão do bot <@{info['bot_id']}> "
                            f"foi **{resultado}**."
                        ),
                        color=color,
                        timestamp=_now()),
                    view=None)
            except Exception:
                pass

    # ============================================================
    # TENTATIVA SEM PERMISSÃO
    # ============================================================

    async def _tentar_sem_permissao(self, guild: discord.Guild,
                                      user: discord.User,
                                      info: dict):
        """Loga uma tentativa de confirmar sem permissão + avisa."""
        cfg = await BotWhitelistManager.get_config(guild.id)

        # Log.
        log = ProfessionalLog(
            title="Tentativa Não Autorizada",
            description=(
                "Um usuário tentou confirmar uma expulsão de bot "
                "whitelisted sem ter permissão."
            ),
            severity="medium",
            footer="Stumble Hit AntiRaid • Proteção Bot Whitelisted")
        if isinstance(user, discord.Member):
            log.set_target(user)
        log.add_field("Bot Alvo", f"<@{info['bot_id']}>")
        await self._logar_canal(guild, cfg, log.build())

        # Notifica owner.
        if cfg.get("notificar_owner"):
            try:
                owner = guild.owner or await guild.fetch_member(
                    guild.owner_id)
                if owner:
                    await owner.send(embed=discord.Embed(
                        title="🚨 Tentativa não autorizada",
                        description=(
                            f"**{user}** (`{user.id}`) tentou confirmar "
                            f"expulsão de bot em **{guild.name}** "
                            f"sem permissão."
                        ),
                        color=discord.Color.orange(),
                        timestamp=_now()))
            except Exception:
                pass

        # Dá warn automático (se o sistema de warns estiver ativo).
        if cfg.get("warn_sem_permissao"):
            try:
                warn_cog = self.bot.get_cog("WarnSystemCommands")
                if warn_cog:
                    user_member = guild.get_member(user.id)
                    if user_member:
                        await warn_cog.aplicar_warn(
                            guild=guild,
                            user=user_member,
                            regra="manual_sistema",
                            motivo=(
                                "Tentou confirmar expulsão de bot "
                                "whitelisted sem permissão"),
                            aplicado_por=None,
                        )
            except Exception:
                pass

    # ============================================================
    # LOGS
    # ============================================================

    async def _logar_canal(self, guild, cfg, embed):
        """Envia um embed pro canal de log (ou do anti-nuke como fallback)."""
        canal_id = cfg.get("log_canal")
        # Se não tem, pega do anti-nuke.
        if not canal_id:
            try:
                from commands_antinuke import AntiNukeManager
                an_cfg = AntiNukeManager.get_config(guild.id)
                canal_id = an_cfg.get("log_channel")
            except Exception:
                pass
        if not canal_id:
            return
        canal = guild.get_channel(int(canal_id))
        if canal:
            try:
                await canal.send(embed=embed)
            except Exception:
                pass

    async def _logar_kick_automatico(self, guild, bot_member, motivo,
                                        action_type):
        """Log de kick automático."""
        cfg = await BotWhitelistManager.get_config(guild.id)
        log = ProfessionalLog(
            title="Bot Whitelisted Expulso (Auto)",
            description=(
                "Um bot na whitelist foi expulso automaticamente "
                "(auto_kick ativado)."
            ),
            severity="high",
            footer="Stumble Hit AntiRaid • Auto-Kick Whitelisted")
        log.set_target(bot_member)
        log.add_field("Ação Detectada", f"`{action_type}`")
        log.add_field("Motivo", fmt_reason(motivo), inline=False)
        await self._logar_canal(guild, cfg, log.build())

    async def _logar_kick_confirmado(self, guild, bot_member, motivo,
                                       action_type):
        """Log de kick confirmado por admin."""
        cfg = await BotWhitelistManager.get_config(guild.id)
        log = ProfessionalLog(
            title="Bot Whitelisted Expulso (Confirmado)",
            description=(
                "Um bot na whitelist foi expulso após confirmação "
                "de um admin."
            ),
            severity="high",
            footer="Stumble Hit AntiRaid • Proteção Bot Whitelisted")
        log.set_target(bot_member)
        log.add_field("Ação Detectada", f"`{action_type}`")
        log.add_field("Motivo", fmt_reason(motivo), inline=False)
        await self._logar_canal(guild, cfg, log.build())

    async def _logar_bloqueado(self, guild, bot_member, motivo,
                                 action_type, detalhe: str):
        """Log de ação bloqueada (proteção evitou kick)."""
        cfg = await BotWhitelistManager.get_config(guild.id)
        log = ProfessionalLog(
            title="Ação Bloqueada (Bot Whitelisted)",
            description=(
                "Um bot na whitelist executou ações suspeitas, mas "
                "a proteção especial evitou a expulsão."
            ),
            severity="medium",
            footer="Stumble Hit AntiRaid • Proteção Bot Whitelisted")
        log.set_target(bot_member)
        log.add_field("Ação Detectada", f"`{action_type}`")
        log.add_field("Motivo", fmt_reason(motivo), inline=False)
        log.add_field("Detalhe", detalhe, inline=False)
        await self._logar_canal(guild, cfg, log.build())

    # ============================================================
    # COMANDOS (>botprotect)
    # ============================================================

    @commands.group(name="botprotect", aliases=["bprotect", "bwl"],
                    invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def botprotect(self, ctx):
        """Painel de proteção de bots whitelisted."""
        cfg = await BotWhitelistManager.get_config(ctx.guild.id)

        # Função auxiliar pra ✅/❌.
        def onoff(v):
            return "✅" if v else "❌"

        modo = cfg.get("modo", "threshold")
        modo_label = {
            "threshold": "📊 Limites elevados (normal + 1)",
            "immune": "🔒 Imunidade total",
            "never_kick": "🔒 Imunidade total (legado)",
        }.get(modo, modo)

        # Monta strings formatadas.
        log_canal = cfg.get("log_canal")
        if log_canal:
            log_str = f"<#{log_canal}>"
        else:
            log_str = "❌"

        auto_kick_str = "sempre exige confirmação"

        cargos_confirmar = cfg.get("cargos_confirmar", [])
        users_confirmar = cfg.get("users_confirmar", [])

        if cargos_confirmar:
            cargos_str = ", ".join(f"<@&{r}>" for r in cargos_confirmar)
        else:
            cargos_str = "*nenhum*"

        if users_confirmar:
            users_str = ", ".join(f"<@{u}>" for u in users_confirmar)
        else:
            users_str = "*nenhum*"

        embed = discord.Embed(
            title="Proteção de bots na whitelist",
            description=(
                f"**Status:** {onoff(cfg['enabled'])}\n"
                f"**Modo:** {modo_label}\n"
                f"**Expulsão:** {auto_kick_str}\n"
                f"**Notificar owner:** {onoff(cfg['notificar_owner'])}\n"
                f"**Warn sem permissão:** {onoff(cfg['warn_sem_permissao'])}\n"
                f"**Canal de log:** {log_str}"
            ),
            color=discord.Color.purple(),
            timestamp=_now())

        embed.add_field(
            name="🔐 Quem pode confirmar",
            value=(
                f"**Cargos:** {cargos_str}\n"
                f"**Users:** {users_str}"
            ),
            inline=False)

        embed.add_field(
            name="📌 Comandos",
            value=(
                "`>botprotect on/off`\n"
                "`>botprotect modo threshold/immune`\n"
                "`>botprotect autokick on/off`\n"
                "`>botprotect log #canal`\n"
                "`>botprotect confirm addrole @cargo`\n"
                "`>botprotect confirm removerole @cargo`\n"
                "`>botprotect confirm adduser @user`\n"
                "`>botprotect confirm removeuser @user`\n"
                "`>botprotect confirm exceptrole @cargo @user`\n"
                "`>botprotect confirm exceptuser @admin @user`\n"
                "`>botprotect notify on/off`\n"
                "`>botprotect warn on/off`\n"
                "`>botprotect test @bot` — testa proteção"
            ),
            inline=False)

        await ctx.send(embed=embed)

    @botprotect.command(name="on")
    @commands.has_permissions(administrator=True)
    async def bprotect_on(self, ctx):
        """Ativa a proteção."""
        await BotWhitelistManager.update(ctx.guild.id, "enabled", True)
        await ctx.send(embed=embed_success("✅ Proteção ativada."))

    @botprotect.command(name="off")
    @commands.has_permissions(administrator=True)
    async def bprotect_off(self, ctx):
        """Desativa a proteção."""
        await BotWhitelistManager.update(ctx.guild.id, "enabled", False)
        await ctx.send(embed=embed_success("✅ Proteção desativada."))

    @botprotect.command(name="modo")
    @commands.has_permissions(administrator=True)
    async def bprotect_modo(self, ctx, modo: str):
        """Define o modo (threshold ou immune)."""
        if modo not in ("threshold", "immune", "never_kick"):
            return await ctx.send(embed=embed_error(
                "❌ Use: `threshold` ou `immune`"))
        if modo == "never_kick":
            modo = "immune"
        await BotWhitelistManager.update(ctx.guild.id, "modo", modo)
        label = ("Limites elevados (mínimo normal + 1)"
                 if modo == "threshold"
                 else "Imunidade total")
        await ctx.send(embed=embed_success(f"✅ Modo: {label}"))

    @botprotect.command(name="autokick")
    @commands.has_permissions(administrator=True)
    async def bprotect_autokick(self, ctx, state: str):
        """Compatibilidade legada: auto-kick foi desativado por política."""
        await BotWhitelistManager.update(ctx.guild.id,
                                     "auto_kick_whitelisted", False)
        await ctx.send(embed=embed_warning(
            "⚠️ Auto-kick está desativado permanentemente para bots na whitelist. "
            "Quando o limite é excedido, a expulsão sempre exige confirmação."))

    @botprotect.command(name="log")
    @commands.has_permissions(administrator=True)
    async def bprotect_log(self, ctx, canal: discord.TextChannel):
        """Define canal de log."""
        await BotWhitelistManager.update(ctx.guild.id, "log_canal", canal.id)
        await ctx.send(embed=embed_success(f"✅ Log em {canal.mention}"))

    @botprotect.command(name="notify")
    @commands.has_permissions(administrator=True)
    async def bprotect_notify(self, ctx, state: str):
        """Liga/desliga notificar owner."""
        enabled = state.lower() in ("on", "true", "1", "sim")
        await BotWhitelistManager.update(ctx.guild.id, "notificar_owner", enabled)
        await ctx.send(embed=embed_success(
            f"✅ Notificar owner: {'ON' if enabled else 'OFF'}"))

    @botprotect.command(name="warn")
    @commands.has_permissions(administrator=True)
    async def bprotect_warn(self, ctx, state: str):
        """Liga/desliga warn sem permissão."""
        enabled = state.lower() in ("on", "true", "1", "sim")
        await BotWhitelistManager.update(ctx.guild.id, "warn_sem_permissao",
                                     enabled)
        await ctx.send(embed=embed_success(
            f"✅ Warn sem permissão: {'ON' if enabled else 'OFF'}"))

    # ═══════════════════════════════════════════════════════════
    # Grupo >botprotect confirm
    # ═══════════════════════════════════════════════════════════

    @botprotect.group(name="confirm", invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def bprotect_confirm(self, ctx):
        """Grupo de comandos pra configurar quem pode confirmar."""
        await ctx.send(embed=embed_info(
            "Use: `>botprotect confirm addrole/removerole/adduser/"
            "removeuser/exceptrole/exceptuser @x`"))

    @bprotect_confirm.command(name="addrole")
    @commands.has_permissions(administrator=True)
    async def bprotect_confirm_addrole(self, ctx, role: discord.Role):
        """Adiciona cargo que pode confirmar."""
        cfg = await BotWhitelistManager.get_config(ctx.guild.id)
        roles = list(cfg.get("cargos_confirmar", []))
        if role.id not in roles:
            roles.append(role.id)
            await BotWhitelistManager.update(ctx.guild.id, "cargos_confirmar",
                                         roles)
        await ctx.send(embed=embed_success(
            f"✅ {role.mention} pode confirmar."))

    @bprotect_confirm.command(name="removerole")
    @commands.has_permissions(administrator=True)
    async def bprotect_confirm_removerole(self, ctx, role: discord.Role):
        """Remove cargo."""
        cfg = await BotWhitelistManager.get_config(ctx.guild.id)
        roles = [r for r in cfg.get("cargos_confirmar", []) if r != role.id]
        await BotWhitelistManager.update(ctx.guild.id, "cargos_confirmar", roles)
        await ctx.send(embed=embed_success(
            f"✅ {role.mention} NÃO pode confirmar."))

    @bprotect_confirm.command(name="adduser")
    @commands.has_permissions(administrator=True)
    async def bprotect_confirm_adduser(self, ctx, user: discord.Member):
        """Adiciona user que pode confirmar."""
        cfg = await BotWhitelistManager.get_config(ctx.guild.id)
        users = list(cfg.get("users_confirmar", []))
        if user.id not in users:
            users.append(user.id)
            await BotWhitelistManager.update(ctx.guild.id, "users_confirmar",
                                         users)
        await ctx.send(embed=embed_success(
            f"✅ {user.mention} pode confirmar."))

    @bprotect_confirm.command(name="removeuser")
    @commands.has_permissions(administrator=True)
    async def bprotect_confirm_removeuser(self, ctx, user: discord.Member):
        """Remove user."""
        cfg = await BotWhitelistManager.get_config(ctx.guild.id)
        users = [u for u in cfg.get("users_confirmar", []) if u != user.id]
        await BotWhitelistManager.update(ctx.guild.id, "users_confirmar", users)
        await ctx.send(embed=embed_success(
            f"✅ {user.mention} NÃO pode confirmar."))

    @bprotect_confirm.command(name="exceptrole")
    @commands.has_permissions(administrator=True)
    async def bprotect_confirm_exceptrole(self, ctx, role: discord.Role,
                                            user: discord.Member):
        """Exceção: cargo X pode confirmar, EXCETO user Y."""
        cfg = await BotWhitelistManager.get_config(ctx.guild.id)
        exceto = dict(cfg.get("cargos_confirmar_exceto", {}))
        lista = list(exceto.get(str(role.id), []))
        if user.id not in lista:
            lista.append(user.id)
        exceto[str(role.id)] = lista
        await BotWhitelistManager.update(ctx.guild.id,
                                     "cargos_confirmar_exceto", exceto)
        await ctx.send(embed=embed_success(
            f"✅ {role.mention} pode confirmar, EXCETO {user.mention}."))

    @bprotect_confirm.command(name="exceptuser")
    @commands.has_permissions(administrator=True)
    async def bprotect_confirm_exceptuser(self, ctx, admin: discord.Member,
                                            user: discord.Member):
        """Exceção: admin X pode confirmar, EXCETO user Y."""
        cfg = await BotWhitelistManager.get_config(ctx.guild.id)
        exceto = dict(cfg.get("users_confirmar_exceto", {}))
        lista = list(exceto.get(str(admin.id), []))
        if user.id not in lista:
            lista.append(user.id)
        exceto[str(admin.id)] = lista
        await BotWhitelistManager.update(ctx.guild.id,
                                     "users_confirmar_exceto", exceto)
        await ctx.send(embed=embed_success(
            f"✅ {admin.mention} pode confirmar, EXCETO {user.mention}."))

    @bprotect_confirm.command(name="list")
    @commands.has_permissions(administrator=True)
    async def bprotect_confirm_list(self, ctx):
        """Lista quem pode confirmar."""
        cfg = await BotWhitelistManager.get_config(ctx.guild.id)
        embed = discord.Embed(
            title="Quem pode confirmar expulsões",
            color=discord.Color.purple(),
            timestamp=_now())
        cargos = cfg.get("cargos_confirmar", [])
        users = cfg.get("users_confirmar", [])
        exceto_cargo = cfg.get("cargos_confirmar_exceto", {})
        exceto_user = cfg.get("users_confirmar_exceto", {})

        if cargos:
            cargos_str = "\n".join(f"• <@&{r}>" for r in cargos)
        else:
            cargos_str = "*nenhum*"

        if users:
            users_str = "\n".join(f"• <@{u}>" for u in users)
        else:
            users_str = "*nenhum*"

        embed.add_field(
            name=f"👑 Cargos ({len(cargos)})",
            value=cargos_str,
            inline=False)
        embed.add_field(
            name=f"👤 Users ({len(users)})",
            value=users_str,
            inline=False)

        # Exceções.
        if exceto_cargo:
            linhas = []
            for rid, lista in exceto_cargo.items():
                users_part = ", ".join(f"<@{u}>" for u in lista)
                linhas.append(f"<@&{rid}> exceto: {users_part}")
            embed.add_field(
                name="⚡ Exceções (cargos)",
                value="\n".join(linhas),
                inline=False)

        if exceto_user:
            linhas = []
            for uid, lista in exceto_user.items():
                users_part = ", ".join(f"<@{u}>" for u in lista)
                linhas.append(f"<@{uid}> exceto: {users_part}")
            embed.add_field(
                name="⚡ Exceções (users)",
                value="\n".join(linhas),
                inline=False)

        await ctx.send(embed=embed)

    @botprotect.command(name="test")
    @commands.has_permissions(administrator=True)
    async def bprotect_test(self, ctx, bot: discord.Member = None):
        """Testa a proteção SEM realmente expulsar."""
        if not bot or not bot.bot:
            return await ctx.send(embed=embed_error(
                "❌ Mencione um bot. Uso: `>botprotect test @bot`"))

        # Verifica se está na whitelist do anti-nuke.
        cfg_an = None
        try:
            from commands_antinuke import AntiNukeManager
            cfg_an = AntiNukeManager.get_config(ctx.guild.id)
        except Exception:
            pass

        whitelisted = False
        if cfg_an:
            whitelisted = (bot.id in cfg_an.get("whitelist_bots", []) or
                            bot.id in cfg_an.get("whitelist_users", []))

        embed = discord.Embed(
            title="Verificação de proteção",
            color=discord.Color.blue(),
            timestamp=_now())
        embed.add_field(name="Bot", value=bot.mention, inline=True)
        embed.add_field(
            name="Whitelisted?",
            value="✅ SIM" if whitelisted else "❌ NÃO",
            inline=True)
        cfg = await BotWhitelistManager.get_config(ctx.guild.id)
        embed.add_field(
            name="Modo",
            value=f"`{cfg.get('modo')}`",
            inline=True)
        embed.add_field(
            name="Expulsão",
            value="Sempre exige confirmação",
            inline=True)

        # Simula o resultado esperado.
        if whitelisted and cfg.get("modo") in ("immune", "never_kick"):
            resultado = "**Imunidade total** (nenhuma proteção automática)"
        elif whitelisted:
            resultado = "Será **expulso com confirmação**"
        else:
            resultado = "Não é whitelisted — proteção não se aplica"

        embed.add_field(
            name="Resultado esperado",
            value=resultado,
            inline=False)
        await ctx.send(embed=embed)


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("BotWhitelistProtectionCommands") is None:
        await bot.add_cog(BotWhitelistProtectionCommands(bot))