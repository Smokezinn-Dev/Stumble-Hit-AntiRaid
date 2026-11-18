# ============================================================
# COMMANDS_BRANDING.PY - PERSONALIZAÇÃO
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Permite personalizar a aparência do bot POR SERVIDOR:
#   • Apelido do bot (nickname)
#   • Cor dos embeds (hex)
#   • Rodapé (footer) dos embeds
# Cada servidor pode ter sua própria "marca".
# ============================================================

# discord = biblioteca principal
import discord
# commands = criar comandos com prefixo
from discord.ext import commands
# datetime = trabalhar com datas (timestamp dos embeds)
from datetime import datetime

# database = conexão MongoDB
from database import get_connection_async
# Embeds + função pra invalidar cache de branding.
from embeds import embed_success, embed_error, embed_warning, embed_info, invalidate_brand


# ============================================================
# COG
# ============================================================

class BrandingCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def _get(self, guild_id: int) -> dict:
        """Busca a config de branding do servidor usando Motor."""
        db = await get_connection_async()
        doc = await db["branding_config"].find_one({"guild_id": guild_id}) or {}
        return doc

    # ============================================================
    # COMANDO PRINCIPAL: >branding
    # ============================================================

    @commands.command(name="branding", aliases=["brand", "personalizar"])
    @commands.has_permissions(administrator=True)
    async def branding(self, ctx):
        """Painel de personalização. Mostra as configs atuais."""
        doc = await self._get(ctx.guild.id)
        embed = discord.Embed(
            title="🎨 PERSONALIZAÇÃO DO BOT",
            description=(
                "Deixe o bot único neste servidor!\n\n"
                "**Comandos:**\n"
                "• `>branding nickname <nome>` — apelido do bot\n"
                "• `>branding color <hex>` — cor dos embeds (ex: `#ff0000`)\n"
                "• `>branding footer <texto>` — rodapé dos embeds\n"
                "• `>branding reset` — reseta tudo"
            ),
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )
        # Thumbnail = ícone do servidor.
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        # Mostra os valores atuais (ou "*padrão*").
        nick = doc.get("nickname") or "*padrão*"
        color = doc.get("color")
        color_str = f"#{color:06x}" if color else "*padrão*"
        footer = doc.get("footer") or "*padrão*"

        embed.add_field(name="📛 Apelido", value=nick, inline=True)
        embed.add_field(name="🎨 Cor", value=color_str, inline=True)
        embed.add_field(name="📝 Footer", value=footer, inline=False)

        await ctx.send(embed=embed)

    # ============================================================
    # COMANDO: >brandingnickname
    # ============================================================

    @commands.command(name="brandingnickname", aliases=["botnick"])
    @commands.has_permissions(administrator=True)
    async def branding_nickname(self, ctx, *, nickname: str = None):
        """Define o apelido do bot nesse servidor."""
        # Sem arg → avisa como usar.
        if not nickname:
            return await ctx.send(embed=embed_error("❌ Use: `>brandingnickname <nome>` ou `reset` para remover."))

        # Reset.
        if nickname.lower() == "reset":
            db = await get_connection_async()
            await db["branding_config"].update_one(
                {"guild_id": ctx.guild.id},
                {"$unset": {"nickname": ""}}, upsert=True
            )
            # Remove o apelido do bot no Discord.
            try:
                await ctx.guild.me.edit(nick=None)
            except Exception:
                pass
            return await ctx.send(embed=embed_success("✅ Apelido resetado."))

        # Limite do Discord: 32 caracteres.
        if len(nickname) > 32:
            return await ctx.send(embed=embed_error("❌ Máximo 32 caracteres."))

        # Salva no banco.
        db = await get_connection_async()
        await db["branding_config"].update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"nickname": nickname}}, upsert=True
        )
        # Aplica no Discord.
        try:
            await ctx.guild.me.edit(nick=nickname)
        except Exception as e:
            return await ctx.send(embed=embed_error(f"❌ Erro ao mudar apelido: {e}"))

        await ctx.send(embed=embed_success(f"✅ Apelido do bot definido para **{nickname}**!"))

    # ============================================================
    # COMANDO: >brandingcolor
    # ============================================================

    @commands.command(name="brandingcolor", aliases=["botcolor"])
    @commands.has_permissions(administrator=True)
    async def branding_color(self, ctx, color: str = None):
        """Define a cor dos embeds do bot nesse servidor."""
        if not color:
            return await ctx.send(embed=embed_error("❌ Use: `>brandingcolor #ff0000` ou `reset`"))

        # Reset.
        if color.lower() == "reset":
            db = await get_connection_async()
            await db["branding_config"].update_one(
                {"guild_id": ctx.guild.id},
                {"$unset": {"color": ""}}, upsert=True
            )
            invalidate_brand(ctx.guild.id)  # limpa cache
            return await ctx.send(embed=embed_success("✅ Cor resetada."))

        # Remove o "#" e converte hex → inteiro.
        color = color.lstrip("#")
        try:
            color_int = int(color, 16)
        except ValueError:
            return await ctx.send(embed=embed_error("❌ Cor hex inválida. Use formato `#RRGGBB`."))

        db = await get_connection_async()
        await db["branding_config"].update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"color": color_int}}, upsert=True
        )
        invalidate_brand(ctx.guild.id)
        await ctx.send(embed=embed_success(f"✅ Cor dos embeds definida para `#{color_int:06x}`!"))

    # ============================================================
    # COMANDO: >brandingfooter
    # ============================================================

    @commands.command(name="brandingfooter", aliases=["botfooter"])
    @commands.has_permissions(administrator=True)
    async def branding_footer(self, ctx, *, footer: str = None):
        """Define o rodapé (footer) dos embeds."""
        if not footer:
            return await ctx.send(embed=embed_error("❌ Use: `>brandingfooter <texto>` ou `reset`"))

        # Reset.
        if footer.lower() == "reset":
            db = await get_connection_async()
            await db["branding_config"].update_one(
                {"guild_id": ctx.guild.id},
                {"$unset": {"footer": ""}}, upsert=True
            )
            invalidate_brand(ctx.guild.id)
            return await ctx.send(embed=embed_success("✅ Footer resetado."))

        # Limite do Discord: 200 chars no footer.
        if len(footer) > 200:
            return await ctx.send(embed=embed_error("❌ Máximo 200 caracteres."))

        db = await get_connection_async()
        await db["branding_config"].update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"footer": footer}}, upsert=True
        )
        invalidate_brand(ctx.guild.id)
        await ctx.send(embed=embed_success(f"✅ Rodapé definido: **{footer}**"))

    # ============================================================
    # COMANDO: >brandingreset
    # ============================================================

    @commands.command(name="brandingreset")
    @commands.has_permissions(administrator=True)
    async def branding_reset(self, ctx):
        """Reseta TODA a personalização."""
        db = await get_connection_async()
        await db["branding_config"].delete_one({"guild_id": ctx.guild.id})
        invalidate_brand(ctx.guild.id)
        # Remove apelido no Discord.
        try:
            await ctx.guild.me.edit(nick=None)
        except Exception:
            pass
        await ctx.send(embed=embed_warning("⚠️ Personalização resetada!"))


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("BrandingCommands") is None:
        await bot.add_cog(BrandingCommands(bot))