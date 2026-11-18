# ============================================================
# COMMANDS_SLASH.PY - v8.0 (CORRIGIDO)
# SLASH COMMANDS PARA AÇÕES RÁPIDAS
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Cria SLASH COMMANDS (comando com "/" no início) pra ações
# rápidas de moderação. Diferente dos comandos com prefixo (>),
# os slash aparecem quando o user digita "/" no Discord.
#
# Também cria CONTEXT MENUS (aqueles menus que aparecem quando
# você clica com botão direito em um membro):
#   - Kick
#   - Ban
#   - Info
#   - Warn
#
# FIX v8.0.1:
# - Context menus movidos para FORA da classe (requisito discord.py)
# - Registrados no setup() via bot.tree.add_command()
# ============================================================

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timezone, timedelta

from database import get_connection_async
from embeds import embed_success, embed_error, embed_warning, embed_info


def _now():
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# CONTEXT MENUS (fora da classe - requisito discord.py)
# ============================================================
# 📌 O QUE SÃO?
# São as ações que aparecem quando você clica com botão direito
# em um usuário e vai em "Apps". Elas são aplicadas ao usuário
# clicado.
#
# PRECISAM estar FORA da classe por causa de como o discord.py
# funciona com decoradores.

@app_commands.context_menu(name="👢 Kick")
@app_commands.checks.has_permissions(kick_members=True)
@app_commands.default_permissions(kick_members=True)
async def ctx_kick(interaction: discord.Interaction, member: discord.Member):
    """Menu de contexto: expulsar."""
    # Hierarquia.
    if member.id == interaction.user.id or member.id == interaction.guild.owner_id:
        return await interaction.response.send_message(
            embed=embed_error("❌ Não é permitido aplicar essa ação nesse alvo."), ephemeral=True)
    if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message(
            embed=embed_error("❌ Cargo superior."), ephemeral=True)
    try:
        await member.kick(reason=f"Por {interaction.user}")
        await interaction.response.send_message(
            embed=embed_success(f"👢 {member.mention} expulso."))
    except Exception as e:
        await interaction.response.send_message(
            embed=embed_error(f"❌ {e}"), ephemeral=True)


@app_commands.context_menu(name="🔨 Ban")
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.default_permissions(ban_members=True)
async def ctx_ban(interaction: discord.Interaction, member: discord.Member):
    """Menu de contexto: banir."""
    if member.id in (interaction.user.id, interaction.guild.owner_id):
        return await interaction.response.send_message(
            embed=embed_error("❌ Não é permitido aplicar essa ação nesse alvo."), ephemeral=True)
    if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message(
            embed=embed_error("❌ Cargo superior."), ephemeral=True)
    try:
        await member.ban(reason=f"Por {interaction.user}")
        await interaction.response.send_message(
            embed=embed_success(f"🔨 {member.mention} banido."))
    except Exception as e:
        await interaction.response.send_message(
            embed=embed_error(f"❌ {e}"), ephemeral=True)


@app_commands.context_menu(name="👤 Info")
async def ctx_info(interaction: discord.Interaction, member: discord.Member):
    """Menu de contexto: info do user."""
    embed = discord.Embed(
        title=f"👤 {member.display_name}",
        color=member.color,
        timestamp=_now())
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="🆔", value=str(member.id), inline=True)
    embed.add_field(name="📅 Conta",
                    value=member.created_at.strftime("%d/%m/%Y"),
                    inline=True)
    if member.joined_at:
        embed.add_field(name="📥 Entrou",
                        value=member.joined_at.strftime("%d/%m/%Y"),
                        inline=True)
    await interaction.response.send_message(embed=embed, ephemeral=True)


@app_commands.context_menu(name="⚠️ Warn")
@app_commands.checks.has_permissions(kick_members=True)
@app_commands.default_permissions(kick_members=True)
async def ctx_warn(interaction: discord.Interaction, member: discord.Member):
    """Menu de contexto: warn rápido."""
    db = await get_connection_async()
    await db["warns"].insert_one({
        "user_id": member.id, "guild_id": interaction.guild.id,
        "moderator_id": interaction.user.id,
        "reason": f"Via context menu por {interaction.user}",
        "timestamp": _now(),
    })
    await interaction.response.send_message(
        embed=embed_warning(f"⚠️ {member.mention} advertido."))


# ============================================================
# COG COM SLASH COMMANDS (não-context menus)
# ============================================================

class SlashCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ============================================================
    # PUNIÇÕES
    # ============================================================

    @app_commands.command(name="kick", description="👢 Expulsar um membro")
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.default_permissions(kick_members=True)
    @app_commands.describe(member="Membro", reason="Motivo")
    async def slash_kick(self, interaction: discord.Interaction,
                          member: discord.Member,
                          reason: str = "Não especificado"):
        """Slash command: /kick."""
        # Impede auto-punição e punição do dono do servidor.
        if member.id in (interaction.user.id, interaction.guild.owner_id):
            return await interaction.response.send_message(
                embed=embed_error("❌ Não é permitido aplicar essa ação nesse alvo."), ephemeral=True)
        if (member.top_role >= interaction.user.top_role
                and interaction.user.id != interaction.guild.owner_id):
            return await interaction.response.send_message(
                embed=embed_error("❌ Cargo superior."), ephemeral=True)
        try:
            await member.kick(reason=reason)
            await interaction.response.send_message(embed=embed_success(
                f"👢 {member.mention} expulso.\n**Motivo:** {reason}"))
        except Exception as e:
            await interaction.response.send_message(
                embed=embed_error(f"❌ {e}"), ephemeral=True)

    @app_commands.command(name="ban", description="🔨 Banir um membro")
    @app_commands.checks.has_permissions(ban_members=True)
    @app_commands.default_permissions(ban_members=True)
    @app_commands.describe(member="Membro", reason="Motivo")
    async def slash_ban(self, interaction: discord.Interaction,
                         member: discord.Member,
                         reason: str = "Não especificado"):
        """Slash command: /ban."""
        if member.id in (interaction.user.id, interaction.guild.owner_id):
            return await interaction.response.send_message(
                embed=embed_error("❌ Não é permitido aplicar essa ação nesse alvo."), ephemeral=True)
        if (member.top_role >= interaction.user.top_role
                and interaction.user.id != interaction.guild.owner_id):
            return await interaction.response.send_message(
                embed=embed_error("❌ Cargo superior."), ephemeral=True)
        try:
            await member.ban(reason=reason)
            await interaction.response.send_message(embed=embed_success(
                f"🔨 {member.mention} banido.\n**Motivo:** {reason}"))
        except Exception as e:
            await interaction.response.send_message(
                embed=embed_error(f"❌ {e}"), ephemeral=True)

    @app_commands.command(name="timeout", description="⏰ Aplicar timeout")
    @app_commands.checks.has_permissions(moderate_members=True)
    @app_commands.default_permissions(moderate_members=True)
    @app_commands.describe(member="Membro", minutes="Duração em minutos",
                            reason="Motivo")
    async def slash_timeout(self, interaction: discord.Interaction,
                             member: discord.Member, minutes: int = 10,
                             reason: str = "Não especificado"):
        """Slash command: /timeout."""
        # Valida minutos (máx 28 dias).
        if minutes < 1 or minutes > 40320:
            return await interaction.response.send_message(
                embed=embed_error("❌ Minutos entre 1 e 40320."),
                ephemeral=True)
        if member.id in (interaction.user.id, interaction.guild.owner_id):
            return await interaction.response.send_message(
                embed=embed_error("❌ Não é permitido aplicar essa ação nesse alvo."), ephemeral=True)
        if member.top_role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
            return await interaction.response.send_message(
                embed=embed_error("❌ Cargo superior."), ephemeral=True)
        try:
            await member.timeout(
                _now() + timedelta(minutes=minutes), reason=reason)
            await interaction.response.send_message(embed=embed_success(
                f"⏰ {member.mention} em timeout por {minutes}min.\n"
                f"**Motivo:** {reason}"))
        except Exception as e:
            await interaction.response.send_message(
                embed=embed_error(f"❌ {e}"), ephemeral=True)

    @app_commands.command(name="warn", description="⚠️ Advertir um membro")
    @app_commands.checks.has_permissions(kick_members=True)
    @app_commands.default_permissions(kick_members=True)
    @app_commands.describe(member="Membro", reason="Motivo")
    async def slash_warn(self, interaction: discord.Interaction,
                          member: discord.Member,
                          reason: str = "Não especificado"):
        """Slash command: /warn."""
        db = await get_connection_async()
        await db["warns"].insert_one({
            "user_id": member.id, "guild_id": interaction.guild.id,
            "moderator_id": interaction.user.id, "reason": reason,
            "timestamp": _now(),
        })
        # Conta total.
        count = await db["warns"].count_documents(
            {"user_id": member.id, "guild_id": interaction.guild.id})
        await interaction.response.send_message(embed=embed_warning(
            f"⚠️ {member.mention} advertido.\n"
            f"**Motivo:** {reason}\n**Warns:** {count}/3"))

    # ============================================================
    # INFO
    # ============================================================

    @app_commands.command(name="userinfo", description="👤 Info de um usuário")
    @app_commands.describe(member="Membro (opcional)")
    async def slash_userinfo(self, interaction: discord.Interaction,
                              member: discord.Member = None):
        """Slash command: /userinfo."""
        member = member or interaction.user
        embed = discord.Embed(
            title=f"👤 {member.display_name}",
            color=member.color,
            timestamp=_now())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="🆔 ID", value=str(member.id), inline=True)
        embed.add_field(name="👑 Top cargo",
                        value=member.top_role.mention, inline=True)
        embed.add_field(name="📅 Conta",
                        value=member.created_at.strftime("%d/%m/%Y"),
                        inline=True)
        if member.joined_at:
            embed.add_field(name="📅 Entrou",
                            value=member.joined_at.strftime("%d/%m/%Y"),
                            inline=True)
        embed.add_field(name="🤖 Bot", value="✅" if member.bot else "❌",
                        inline=True)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="serverinfo", description="🖥️ Info do servidor")
    async def slash_serverinfo(self, interaction: discord.Interaction):
        """Slash command: /serverinfo."""
        g = interaction.guild
        embed = discord.Embed(
            title=f"🖥️ {g.name}",
            color=discord.Color.blue(),
            timestamp=_now())
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="🆔 ID", value=str(g.id), inline=True)
        embed.add_field(name="👑 Dono",
                        value=g.owner.mention if g.owner else "?",
                        inline=True)
        embed.add_field(name="👥 Membros", value=str(g.member_count),
                        inline=True)
        embed.add_field(name="📁 Canais", value=str(len(g.channels)),
                        inline=True)
        embed.add_field(name="👑 Cargos", value=str(len(g.roles)),
                        inline=True)
        embed.add_field(name="🚀 Boosts",
                        value=str(g.premium_subscription_count or 0),
                        inline=True)
        await interaction.response.send_message(embed=embed)

    # ============================================================
    # AÇÕES RÁPIDAS
    # ============================================================

    @app_commands.command(name="purge", description="🗑️ Limpar mensagens")
    @app_commands.checks.has_permissions(manage_messages=True)
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.describe(amount="Quantidade (1-100)")
    async def slash_purge(self, interaction: discord.Interaction,
                           amount: int = 10):
        """Slash command: /purge."""
        amount = max(1, min(amount, 100))
        # Deferrer = "vou responder depois" (pra demorar mais que 3s).
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=amount)
            await interaction.followup.send(
                embed=embed_success(
                    f"🗑️ {len(deleted)} mensagens apagadas."),
                ephemeral=True)
        except Exception as e:
            await interaction.followup.send(
                embed=embed_error(f"❌ {e}"), ephemeral=True)

    @app_commands.command(name="lock", description="🔒 Trancar canal")
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.default_permissions(manage_channels=True)
    async def slash_lock(self, interaction: discord.Interaction):
        """Slash command: /lock."""
        ow = interaction.channel.overwrites_for(
            interaction.guild.default_role)
        ow.send_messages = False
        await interaction.channel.set_permissions(
            interaction.guild.default_role, overwrite=ow)
        await interaction.response.send_message(
            embed=embed_success(
                f"🔒 {interaction.channel.mention} trancado."))

    @app_commands.command(name="unlock", description="🔓 Destrancar canal")
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.default_permissions(manage_channels=True)
    async def slash_unlock(self, interaction: discord.Interaction):
        """Slash command: /unlock."""
        ow = interaction.channel.overwrites_for(
            interaction.guild.default_role)
        ow.send_messages = None
        await interaction.channel.set_permissions(
            interaction.guild.default_role, overwrite=ow)
        await interaction.response.send_message(
            embed=embed_success(
                f"🔓 {interaction.channel.mention} destrancado."))


# ============================================================
# SETUP (registra COG + CONTEXT MENUS)
# ============================================================

async def setup(bot):
    """Chamado pelo main.py."""
    # Registra o Cog de slash commands.
    if bot.get_cog("SlashCommands") is None:
        await bot.add_cog(SlashCommands(bot))

    # Registra os context menus.
    # Remove antigos se existirem (evita duplicação em reload).
    for menu_name in ("👢 Kick", "🔨 Ban", "👤 Info", "⚠️ Warn"):
        try:
            existing = bot.tree.get_command(menu_name)
            if existing:
                bot.tree.remove_command(menu_name)
        except Exception:
            pass

    # Adiciona cada um.
    for cmd in (ctx_kick, ctx_ban, ctx_info, ctx_warn):
        try:
            bot.tree.add_command(cmd)
        except Exception as e:
            print(f"⚠️ Falha ao registrar context menu {cmd.name}: {e}")