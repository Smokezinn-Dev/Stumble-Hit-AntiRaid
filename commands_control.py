# ============================================================
# COMMANDS_CONTROL.PY - CONTROLE TOTAL (BOT MODERAÇÃO)
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Painel central pra ligar/desligar módulos e comandos do bot.
# Tem uma interface com MENU DROPDOWN (Select) pra marcar/desmarcar
# cada módulo. Também permite:
#   • Mudar prefixo (> pra !, ?, etc)
#   - Ativar modo manutenção (só admin usa)
#   - Dar "bypass" pra cargo/user (ignora tudo)
#   - Desativar comandos específicos por canal
# ============================================================

# discord = biblioteca principal
import discord
# commands = criar comandos com prefixo e cogs
from discord.ext import commands
# app_commands = slash commands
from discord import app_commands
# datetime = trabalhar com datas
from datetime import datetime
# typing = só documentação
from typing import Optional

# config = prefixo padrão
from config import PREFIX
# database = conexão MongoDB
from database import get_connection
# Embeds coloridos prontos.
from embeds import embed_success, embed_error, embed_warning, embed_info
# GuildGate = controlador de config por guild (com cache).
from utils import GuildGate


# ============================================================
# DESCRIÇÕES DOS MÓDULOS (aparecem no menu)
# ============================================================

MODULE_DESCRIPTIONS = {
    "staff": "📋 Sistema de staff",
    "automod": "🤖 Filtros automáticos",
    "antinuke": "🚨 Proteção contra nuke/raid",
    "cases": "📂 Cases, notas e appeals",
    "backup": "💾 Backup/restore",
    "branding": "🎨 Personalização do bot",
}


# ============================================================
# COMPONENTES DE UI (Select + View)
# ============================================================
# 📌 O QUE É?
# "Select" = menu dropdown onde o usuário escolhe uma opção.
# "View" = container que agrupa componentes de UI.
# Quando alguém clica, o "callback" é chamado.

class ControlModuleSelect(discord.ui.Select):
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        # Pega o estado atual dos módulos.
        state = GuildGate.get_module_state(guild_id)
        options = []
        # Monta uma opção por módulo (com ✅ ou ❌).
        for mod, desc in MODULE_DESCRIPTIONS.items():
            is_on = state.get(mod, True)
            options.append(discord.SelectOption(
                label=f"{'✅' if is_on else '❌'} {mod}",
                value=mod,
                description=desc[:100]  # limita a 100 chars
            ))
        # Discord limita a 25 opções.
        super().__init__(placeholder="Selecione um módulo para alternar...", options=options[:25])

    async def callback(self, interaction: discord.Interaction):
        """Chamado quando o user seleciona um módulo."""
        module = self.values[0]
        # Pega estado atual e inverte.
        state = GuildGate.get_module_state(self.guild_id)
        current = state.get(module, True)
        GuildGate.set_module(self.guild_id, module, not current)
        # Avisa (ephemeral = só quem clicou vê).
        new_state = "ativado" if not current else "desativado"
        await interaction.response.send_message(
            embed=embed_success(f"✅ Módulo **{module}** {new_state}!"),
            ephemeral=True
        )


class ControlViewFull(discord.ui.View):
    """View com o menu de módulos. Timeout de 180s."""
    def __init__(self, guild_id: int):
        super().__init__(timeout=180)
        self.guild_id = guild_id
        # Adiciona o select no menu.
        self.add_item(ControlModuleSelect(guild_id))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Bloqueia quem não é admin."""
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                embed=embed_error("❌ Apenas administradores."), ephemeral=True)
            return False
        return True


# ============================================================
# COG
# ============================================================

class ControlCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ============================================================
    # COMANDO PRINCIPAL: >control
    # ============================================================

    @commands.command(name="control", aliases=["painel", "panel"])
    @commands.has_permissions(administrator=True)
    async def control(self, ctx):
        """Painel de controle (mostra todos os comandos)."""
        embed = discord.Embed(
            title="🎛️ CONTROLE TOTAL",
            description=(
                "Este painel controla **tudo** do bot de moderação neste servidor.\n\n"
                "**Comandos disponíveis:**\n"
                "• `>control` — Este painel\n"
                "• `>controlmodules` — Ativar/desativar módulos\n"
                "• `>controlcommands` — Desativar comandos\n"
                "• `>controlprefix <novo>` — Mudar prefixo\n"
                "• `>controlmaintenance on/off` — Modo manutenção\n"
                "• `>controlbypass @cargo` — Cargo que ignora tudo\n"
                "• `>controlstate` — Estado atual"
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        await ctx.send(embed=embed)

    # ============================================================
    # COMANDO: >controlmodules
    # ============================================================

    @commands.command(name="controlmodules", aliases=["modules"])
    @commands.has_permissions(administrator=True)
    async def control_modules(self, ctx):
        """Painel interativo pra ligar/desligar módulos."""
        state = GuildGate.get_module_state(ctx.guild.id)
        embed = discord.Embed(
            title="🎛️ MÓDULOS",
            description="Selecione abaixo para alternar:",
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )
        # Separa em ativos/desativados.
        on = [k for k, v in state.items() if v]
        off = [k for k, v in state.items() if not v]
        if on:
            embed.add_field(name="✅ Ativos", value=", ".join(f"`{m}`" for m in on), inline=False)
        if off:
            embed.add_field(name="❌ Desativados", value=", ".join(f"`{m}`" for m in off), inline=False)
        # Envia com a view (que tem o select).
        await ctx.send(embed=embed, view=ControlViewFull(ctx.guild.id))

    # ============================================================
    # SLASH COMMAND: /control
    # ============================================================

    @app_commands.command(name="control", description="🎛️ Painel de controle total")
    @app_commands.default_permissions(administrator=True)
    async def control_slash(self, interaction: discord.Interaction):
        """Versão slash. Envia painel ephemeral com view."""
        embed = discord.Embed(
            title="🎛️ CONTROLE TOTAL",
            description="Use `>control modules` para gerenciar módulos.",
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )
        await interaction.response.send_message(embed=embed, view=ControlViewFull(interaction.guild.id), ephemeral=True)

    # ============================================================
    # COMANDO: >controltoggle (versão sem menu)
    # ============================================================

    @commands.command(name="controltoggle")
    @commands.has_permissions(administrator=True)
    async def control_toggle(self, ctx, module: str, state: str = "on"):
        """Liga/desliga um módulo por comando (sem menu)."""
        module = module.lower()
        # Valida.
        if module not in MODULE_DESCRIPTIONS:
            return await ctx.send(embed=embed_error(
                f"❌ Módulo inválido. Use um de:\n`{', '.join(MODULE_DESCRIPTIONS.keys())}`"))
        enabled = state.lower() in ("on", "true", "1", "sim", "ativar")
        GuildGate.set_module(ctx.guild.id, module, enabled)
        await ctx.send(embed=embed_success(
            f"✅ Módulo **{module}** {'ativado' if enabled else 'desativado'}!"))

    # ============================================================
    # COMANDO: >controlcommands
    # ============================================================

    @commands.command(name="controlcommands", aliases=["disabledcmds"])
    @commands.has_permissions(administrator=True)
    async def control_commands(self, ctx):
        """Lista os comandos desativados."""
        disabled = GuildGate.get_disabled_commands(ctx.guild.id)
        embed = discord.Embed(
            title="📋 Comandos Desativados",
            description=(
                "**Como usar:**\n"
                "• `>controlcmd <nome> off` — desativa\n"
                "• `>controlcmd <nome> on` — reativa\n"
                "• `>controlcmd <nome> channel #canal` — desativa em canal\n"
            ),
            color=discord.Color.blue(),
            timestamp=datetime.utcnow()
        )
        if disabled:
            embed.add_field(name="Desativados", value=", ".join(f"`{c}`" for c in sorted(disabled)), inline=False)
        else:
            embed.add_field(name="Desativados", value="*Nenhum*", inline=False)
        await ctx.send(embed=embed)

    # ============================================================
    # COMANDO: >controlcmd
    # ============================================================

    @commands.command(name="controlcmd")
    @commands.has_permissions(administrator=True)
    async def control_cmd(self, ctx, command: str, action: str = "off", channel: discord.TextChannel = None):
        """Ativa/desativa um comando específico (opcionalmente por canal)."""
        command = command.lower()
        action = action.lower()

        # Desativar em canal específico.
        if action == "channel" and channel:
            GuildGate.set_channel_lock(ctx.guild.id, command, channel.id, True)
            return await ctx.send(embed=embed_success(
                f"✅ Comando `{command}` desativado em {channel.mention}"))
        # Reativar em canal.
        if action == "unchannel" and channel:
            GuildGate.set_channel_lock(ctx.guild.id, command, channel.id, False)
            return await ctx.send(embed=embed_success(
                f"✅ Comando `{command}` liberado em {channel.mention}"))

        # Desativar/reativar globalmente.
        disabled = action in ("off", "disable", "desativar")
        GuildGate.set_command(ctx.guild.id, command, disabled)
        await ctx.send(embed=embed_success(
            f"✅ Comando `{command}` {'desativado' if disabled else 'reativado'}!"))

    # ============================================================
    # COMANDO: >controlprefix
    # ============================================================

    @commands.command(name="controlprefix")
    @commands.has_permissions(administrator=True)
    async def control_prefix(self, ctx, prefix: str = None):
        """Muda o prefixo do servidor (ex: > pra !)."""
        # Sem arg → mostra o atual.
        if not prefix:
            current = GuildGate.get_prefix(ctx.guild.id, PREFIX)
            return await ctx.send(embed=embed_info(f"🔧 Prefixo atual: `{current}`"))
        # Limite de 3 chars.
        if len(prefix) > 3:
            return await ctx.send(embed=embed_error("❌ Prefixo muito longo (máx 3)."))
        GuildGate.set_prefix(ctx.guild.id, prefix)
        await ctx.send(embed=embed_success(f"✅ Prefixo alterado para `{prefix}`"))

    # ============================================================
    # COMANDO: >controlmaintenance
    # ============================================================

    @commands.command(name="controlmaintenance", aliases=["maintenance", "manut"])
    @commands.has_permissions(administrator=True)
    async def control_maintenance(self, ctx, state: str = "on"):
        """Liga/desliga modo manutenção (só admin usa)."""
        enabled = state.lower() in ("on", "true", "1", "sim", "ativar")
        GuildGate.set_maintenance(ctx.guild.id, enabled)
        await ctx.send(embed=embed_warning(
            f"🛠️ Modo manutenção **{'ativado' if enabled else 'desativado'}**!"))

    # ============================================================
    # COMANDO: >controlbypass
    # ============================================================

    @commands.command(name="controlbypass")
    @commands.has_permissions(administrator=True)
    async def control_bypass(self, ctx, target: str = None, action: str = "add"):
        """Dá bypass pra um cargo/user (ignora manutenção e comandos off)."""
        if target is None:
            return await ctx.send(embed=embed_info(
                "Uso: `>controlbypass @cargo/user add/remove`"))

        # Se é cargo.
        if target.startswith("<@&") and target.endswith(">"):
            role_id = int(target[3:-1])
            if action.lower() in ("add", "adicionar"):
                GuildGate.add_bypass(ctx.guild.id, "role", role_id)
                return await ctx.send(embed=embed_success(f"✅ Cargo <@&{role_id}> agora ignora manutenção/bloqueios."))
            GuildGate.remove_bypass(ctx.guild.id, "role", role_id)
            return await ctx.send(embed=embed_success(f"✅ Cargo <@&{role_id}> removido."))

        # Se é user.
        if target.startswith("<@") and target.endswith(">"):
            user_id = int(target[2:-1].split("!")[-1])
            if action.lower() in ("add", "adicionar"):
                GuildGate.add_bypass(ctx.guild.id, "user", user_id)
                return await ctx.send(embed=embed_success(f"✅ Usuário <@{user_id}> agora ignora tudo."))
            GuildGate.remove_bypass(ctx.guild.id, "user", user_id)
            return await ctx.send(embed=embed_success(f"✅ Usuário <@{user_id}> removido."))

    # ============================================================
    # COMANDO: >controlstate
    # ============================================================

    @commands.command(name="controlstate", aliases=["cstate"])
    @commands.has_permissions(administrator=True)
    async def control_state(self, ctx):
        """Mostra o estado completo do controle."""
        state = GuildGate.get_module_state(ctx.guild.id)
        disabled = GuildGate.get_disabled_commands(ctx.guild.id)
        prefix = GuildGate.get_prefix(ctx.guild.id, PREFIX)
        maint = GuildGate.is_maintenance(ctx.guild.id)
        bypass_roles = GuildGate.get_bypass_roles(ctx.guild.id)
        bypass_users = GuildGate.get_bypass_users(ctx.guild.id)

        embed = discord.Embed(title="📊 ESTADO ATUAL", color=discord.Color.blue(),
                              timestamp=datetime.utcnow())
        embed.add_field(name="🔧 Prefixo", value=f"`{prefix}`", inline=True)
        embed.add_field(name="🛠️ Manutenção", value="✅ ON" if maint else "❌ OFF", inline=True)
        embed.add_field(name="📋 Comandos desativados", value=str(len(disabled)), inline=True)
        embed.add_field(
            name="Módulos ON",
            value=", ".join(f"`{m}`" for m, v in state.items() if v) or "*Nenhum*",
            inline=False
        )
        embed.add_field(
            name="Módulos OFF",
            value=", ".join(f"`{m}`" for m, v in state.items() if not v) or "*Nenhum*",
            inline=False
        )
        if bypass_roles:
            embed.add_field(name="Cargos bypass", value=", ".join(f"<@&{r}>" for r in bypass_roles), inline=False)
        if bypass_users:
            embed.add_field(name="Users bypass", value=", ".join(f"<@{u}>" for u in bypass_users), inline=False)
        await ctx.send(embed=embed)


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("ControlCommands") is None:
        await bot.add_cog(ControlCommands(bot))