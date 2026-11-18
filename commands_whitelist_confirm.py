# ============================================================
# COMMANDS_WHITELIST_CONFIRM.PY - v1.1
# CONFIRMAÇÃO VIA DM PARA AÇÕES EM WHITELISTED
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Quando alguém tenta banir/kickar/timeout um membro ou bot que
# está na WHITELIST (confiável), o sistema:
#
# 1. NÃO executa a ação imediatamente
# 2. Envia DM para TODOS os admins online
# 3. Cria uma "confirmação pendente" que expira em X minutos
# 4. Se um admin aceitar → executa
# 5. Se todos negarem OU expirar → cancela
#
# Assim evita banir por engano alguém importante (staff, bot
# de confiança, etc).
#
# v1.1 FIX:
# - Import de Union (estava faltando)
# ============================================================

import asyncio
import secrets
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Union

import discord
from discord.ext import commands, tasks

from database import get_connection_async
from embeds import embed_success, embed_error, embed_warning, embed_info
from utils import TTLCache


def _now() -> datetime:
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# CONFIGURAÇÃO PADRÃO
# ============================================================

DEFAULT_CONFIG = {
    "enabled": True,
    "expires_minutes": 5,                # expira em X minutos
    "require_majority": False,           # precisa da maioria?
    "auto_cancel_on_timeout": True,
    "log_channel": 0,
    # Ações que precisam de confirmação.
    "actions_requiring_confirmation": ["ban", "kick", "timeout", "strip"],
}


# ============================================================
# MANAGER
# ============================================================

class WhitelistConfirmManager:
    _cache = TTLCache(max_size=200, ttl=60)

    @classmethod
    async def get_config(cls, guild_id: int) -> dict:
        """Obtém a configuração do cache ou consulta o MongoDB via Motor."""
        # Mantém a leitura do cache como primeiro passo para evitar consultas
        # desnecessárias ao banco e preservar o comportamento já existente.
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached

        # A conexão assíncrona evita bloquear o event loop do bot.
        db = await get_connection_async()
        doc = await db["whitelist_confirm_config"].find_one(
            {"guild_id": guild_id}) or {}

        # Os padrões continuam sendo mesclados com os dados persistidos;
        # os valores salvos no servidor têm precedência sobre os padrões.
        cfg = {**DEFAULT_CONFIG, **doc}
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def update(cls, guild_id: int, key: str, value):
        """Persiste uma configuração e invalida o cache após a gravação."""
        # Grava primeiro no MongoDB. Só depois invalida o cache para que uma
        # próxima leitura busque o valor novo, sem expor estado não persistido.
        db = await get_connection_async()
        await db["whitelist_confirm_config"].update_one(
            {"guild_id": guild_id}, {"$set": {key: value}}, upsert=True)
        cls._cache.invalidate(guild_id)

    @classmethod
    def invalidate(cls, guild_id: int):
        cls._cache.invalidate(guild_id)


# ============================================================
# VIEW DE CONFIRMAÇÃO (por DM)
# ============================================================
# Botões que aparecem na DM: Aprovar / Rejeitar.

class ConfirmView(discord.ui.View):
    def __init__(self, cog, confirm_id: str, guild: discord.Guild):
        super().__init__(timeout=None)
        self.cog = cog
        self.confirm_id = confirm_id
        self.guild = guild

    @discord.ui.button(label="✅ Aprovar", style=discord.ButtonStyle.success,
                        custom_id="confirm_approve")
    async def approve(self, interaction: discord.Interaction,
                       button: discord.ui.Button):
        await self.cog._handle_vote(interaction, self.confirm_id, True)

    @discord.ui.button(label="❌ Rejeitar", style=discord.ButtonStyle.danger,
                        custom_id="confirm_reject")
    async def reject(self, interaction: discord.Interaction,
                      button: discord.ui.Button):
        await self.cog._handle_vote(interaction, self.confirm_id, False)


# ============================================================
# COG
# ============================================================

class WhitelistConfirmCommands(commands.Cog):
    """Sistema de confirmação por DM para ações em whitelisted."""

    # Cache global de resultados finais.
    _results: Dict[str, str] = {}

    def __init__(self, bot):
        self.bot = bot
        # Confirmações pendentes: {id: info}
        self._pending: Dict[str, dict] = {}
        # Inicia task de expiração.
        self._expire_task.start()

    def cog_unload(self):
        """Cancela a task."""
        try:
            self._expire_task.cancel()
        except Exception:
            pass

    # ============================================================
    # EXPIRAÇÃO
    # ============================================================

    @tasks.loop(seconds=30)
    async def _expire_task(self):
        """Roda a cada 30s e expira confirmações antigas."""
        now = _now()
        for cid in list(self._pending.keys()):
            info = self._pending[cid]
            if now >= info["expires_at"]:
                await self._finalize(cid, "expired")
                self._pending.pop(cid, None)

    @_expire_task.before_loop
    async def _before_expire(self):
        await self.bot.wait_until_ready()

    # ============================================================
    # API PÚBLICA
    # ============================================================

    async def request_confirmation(
        self,
        guild: discord.Guild,
        action: str,
        target: Union[discord.Member, discord.User],
        moderator: Union[discord.Member, discord.User],
        reason: str,
        original_coro_factory,
    ) -> bool:
        """
        Solicita confirmação por DM.
        Retorna True se aprovado, False se rejeitado/expirado.

        original_coro_factory: função async sem argumentos que executa
                                a ação original.
        """
        cfg = await WhitelistConfirmManager.get_config(guild.id)
        # Whitelist é uma barreira de segurança: qualquer falha ou
        # configuração incompleta deve cancelar, nunca executar como fallback.
        if not cfg.get("enabled"):
            return False
        if action not in cfg.get("actions_requiring_confirmation", []):
            return False

        # Gera ID único.
        cid = secrets.token_urlsafe(8)
        expires_at = _now() + timedelta(
            minutes=int(cfg.get("expires_minutes", 5)))

        # Guarda info da confirmação.
        info = {
            "guild_id": guild.id,
            "action": action,
            "target_id": target.id,
            "moderator_id": moderator.id,
            "reason": reason,
            "votes": {},
            "created_at": _now(),
            "expires_at": expires_at,
            "message_ids": {},
            "factory": original_coro_factory,
            "target_str": f"{target}",
        }
        self._pending[cid] = info

        view = ConfirmView(self, cid, guild)
        sent = 0

        # ─── Coleta os admins pra mandar DM. ───
        admin_ids = set()
        try:
            owner = guild.owner or await guild.fetch_member(guild.owner_id)
            if owner:
                admin_ids.add(owner.id)
        except Exception:
            pass

        # Adiciona todos que têm "administrator".
        for m in guild.members:
            if m.bot:
                continue
            if m.guild_permissions.administrator:
                admin_ids.add(m.id)

        # Envia DM pra cada um.
        for admin_id in admin_ids:
            admin = guild.get_member(admin_id)
            if not admin:
                continue
            embed = discord.Embed(
                title="🚨 Confirmação Necessária",
                description=(
                    f"Uma ação crítica foi solicitada em **{guild.name}** "
                    f"e precisa da sua confirmação porque o alvo está na "
                    f"**whitelist**."
                ),
                color=discord.Color.orange(),
                timestamp=_now())
            embed.add_field(
                name="Ação",
                value=f"`{action}`",
                inline=True)
            embed.add_field(
                name="Alvo",
                value=f"{target.mention}\n`{target.id}`",
                inline=True)
            embed.add_field(
                name="Moderador",
                value=f"{moderator.mention}\n`{moderator.id}`",
                inline=True)
            embed.add_field(
                name="Motivo",
                value=reason or "—",
                inline=False)
            embed.add_field(
                name="Expira",
                value=f"<t:{int(expires_at.timestamp())}:R>",
                inline=False)
            embed.set_footer(text=f"ID: {cid}")

            try:
                msg = await admin.send(embed=embed, view=view)
                info["message_ids"][admin_id] = msg.id
                sent += 1
            except Exception:
                pass

        # Ninguém recebeu a solicitação: falha fechada, sem punição.
        if sent == 0:
            self._pending.pop(cid, None)
            self._results.pop(cid, None)
            return False

        # ─── Aguarda a votação acabar (loop checa a cada 1s). ───
        while cid in self._pending:
            await asyncio.sleep(1)

        result = self._results.get(cid, "expired")
        self._results.pop(cid, None)

        # Se aprovado, executa.
        if result == "approved":
            await original_coro_factory()
            return True
        return False

    # ============================================================
    # VOTAÇÃO
    # ============================================================

    async def _handle_vote(self, interaction: discord.Interaction,
                            confirm_id: str, approved: bool):
        """Processa um voto."""
        # Já foi resolvido?
        if confirm_id not in self._pending:
            return await interaction.response.send_message(
                "⚠️ Esta confirmação já foi resolvida ou expirou.",
                ephemeral=True)

        info = self._pending[confirm_id]
        admin_id = interaction.user.id

        guild = self.bot.get_guild(info["guild_id"])
        if not guild:
            return await interaction.response.send_message(
                "❌ Servidor não encontrado.", ephemeral=True)

        # Verifica se é admin.
        admin = guild.get_member(admin_id)
        if not admin or (admin.id != guild.owner_id
                          and not admin.guild_permissions.administrator):
            return await interaction.response.send_message(
                "❌ Apenas administradores podem votar.", ephemeral=True)

        # Registra o voto.
        info["votes"][admin_id] = approved

        # ─── REJEIÇÃO = IMEDIATA. ───
        if not approved:
            self._results[confirm_id] = "rejected"
            await interaction.response.send_message(
                "❌ Você **rejeitou** a ação.", ephemeral=True)
            await self._finalize(confirm_id, "rejected")
            self._pending.pop(confirm_id, None)
            return

        # ─── APROVAÇÃO. ───
        cfg = await WhitelistConfirmManager.get_config(guild.id)
        total_admins = len(info["votes"])
        approvals = sum(1 for v in info["votes"].values() if v)

        # Se não precisa de maioria, 1 já basta.
        if not cfg.get("require_majority"):
            self._results[confirm_id] = "approved"
            await interaction.response.send_message(
                "✅ Você **aprovou**. Executando ação...", ephemeral=True)
            await self._finalize(confirm_id, "approved")
            self._pending.pop(confirm_id, None)
        else:
            # Precisa da maioria — só registra o voto.
            await interaction.response.send_message(
                f"✅ Seu voto foi registrado ({approvals}/{total_admins}).",
                ephemeral=True)

    # ============================================================
    # FINALIZAÇÃO
    # ============================================================

    async def _finalize(self, confirm_id: str, result: str):
        """Edita as DMs originais com o resultado."""
        info = self._pending.get(confirm_id)
        if not info:
            return

        self._results[confirm_id] = result

        guild = self.bot.get_guild(info["guild_id"])
        if not guild:
            return

        # Edita cada DM.
        for admin_id, msg_id in info.get("message_ids", {}).items():
            try:
                admin = guild.get_member(admin_id)
                if not admin:
                    continue
                msg = await admin.fetch_message(msg_id)
                # Cor conforme o resultado.
                color = (discord.Color.green() if result == "approved"
                         else discord.Color.red() if result == "rejected"
                         else discord.Color.greyple())
                await msg.edit(
                    embed=discord.Embed(
                        title="✅ Ação Executada" if result == "approved"
                              else "❌ Ação Cancelada",
                        description=(
                            f"Resultado: `{result}`\n"
                            f"Alvo: <@{info['target_id']}>"
                        ),
                        color=color,
                        timestamp=_now()),
                    view=None)
            except Exception:
                pass

    # ============================================================
    # COMANDOS
    # ============================================================

    @commands.group(name="confirmconfig", aliases=["cconf"],
                    invoke_without_command=True)
    @commands.has_permissions(administrator=True)
    async def confirm_config(self, ctx):
        """Painel de configuração."""
        cfg = await WhitelistConfirmManager.get_config(ctx.guild.id)
        embed = discord.Embed(
            title="🔐 Config de Confirmação por DM",
            description=(
                "Quando alguém tenta banir/kickar/timeout alguém na "
                "whitelist, todos os admins recebem DM para confirmar."
            ),
            color=discord.Color.blue(),
            timestamp=_now())
        embed.add_field(
            name="Status",
            value="✅ Ativo" if cfg.get("enabled") else "❌ Desativado",
            inline=True)
        embed.add_field(
            name="Expira em",
            value=f"`{cfg.get('expires_minutes')}min`",
            inline=True)
        embed.add_field(
            name="Requer maioria",
            value="✅" if cfg.get("require_majority") else "❌ (1 basta)",
            inline=True)
        embed.add_field(
            name="Ações protegidas",
            value=", ".join(f"`{a}`" for a in
                            cfg.get("actions_requiring_confirmation", [])),
            inline=False)
        embed.add_field(
            name="Comandos",
            value=(
                "`>cconf on/off`\n"
                "`>cconf expires <min>`\n"
                "`>cconf majority on/off`\n"
                "`>cconf log #canal`"
            ),
            inline=False)
        await ctx.send(embed=embed)

    @confirm_config.command(name="on")
    @commands.has_permissions(administrator=True)
    async def cconf_on(self, ctx):
        """Liga a confirmação."""
        await WhitelistConfirmManager.update(ctx.guild.id, "enabled", True)
        await ctx.send(embed=embed_success("✅ Confirmação ativada."))

    @confirm_config.command(name="off")
    @commands.has_permissions(administrator=True)
    async def cconf_off(self, ctx):
        """Desliga a confirmação."""
        await WhitelistConfirmManager.update(ctx.guild.id, "enabled", False)
        await ctx.send(embed=embed_success("✅ Confirmação desativada."))

    @confirm_config.command(name="expires")
    @commands.has_permissions(administrator=True)
    async def cconf_expires(self, ctx, minutes: int):
        """Define tempo de expiração."""
        if minutes < 1 or minutes > 60:
            return await ctx.send(embed=embed_error("❌ Entre 1 e 60 min."))
        await WhitelistConfirmManager.update(ctx.guild.id, "expires_minutes", minutes)
        await ctx.send(embed=embed_success(f"✅ Expira em `{minutes}min`"))

    @confirm_config.command(name="majority")
    @commands.has_permissions(administrator=True)
    async def cconf_majority(self, ctx, state: str):
        """Liga/desliga exigência de maioria."""
        enabled = state.lower() in ("on", "true", "1", "sim")
        await WhitelistConfirmManager.update(ctx.guild.id, "require_majority",
                                               enabled)
        await ctx.send(embed=embed_success(
            f"✅ Maioria: {'ON' if enabled else 'OFF'}"))

    @confirm_config.command(name="log")
    @commands.has_permissions(administrator=True)
    async def cconf_log(self, ctx, channel: discord.TextChannel):
        """Define canal de log."""
        await WhitelistConfirmManager.update(ctx.guild.id, "log_channel", channel.id)
        await ctx.send(embed=embed_success(f"✅ Log em {channel.mention}"))

    @commands.command(name="pendingconfirms", aliases=["pconf"])
    @commands.has_permissions(administrator=True)
    async def pending_confirms(self, ctx):
        """Lista confirmações pendentes."""
        pend = {cid: i for cid, i in self._pending.items()
                if i["guild_id"] == ctx.guild.id}
        if not pend:
            return await ctx.send(embed=embed_info(
                "📋 Nenhuma confirmação pendente."))

        embed = discord.Embed(
            title="⏳ Confirmações Pendentes",
            color=discord.Color.orange(),
            timestamp=_now())
        for cid, info in list(pend.items())[:10]:
            remaining = (info["expires_at"] - _now()).total_seconds()
            embed.add_field(
                name=f"`{cid}` — {info['action']}",
                value=(
                    f"**Alvo:** <@{info['target_id']}>\n"
                    f"**Mod:** <@{info['moderator_id']}>\n"
                    f"**Expira em:** {int(remaining)}s\n"
                    f"**Votos:** {len(info['votes'])}"
                ),
                inline=False)
        await ctx.send(embed=embed)


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("WhitelistConfirmCommands") is None:
        await bot.add_cog(WhitelistConfirmCommands(bot))