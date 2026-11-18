# ============================================================
# COMMANDS_STAFF_CONFIG.PY - v6.0
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Comandos de CONFIGURAÇÃO do sistema de staff.
# Permite ao admin:
#   • Definir canal de logs
#   • Definir cargo de staff
#   • Definir cargos excluídos
#   • Adicionar/remover metas
#   • Dar/remover pontos manualmente
#   • Configurar cargos automáticos
#   • Resetar pontos de um user
# ============================================================

import discord
from discord.ext import commands
import re
from datetime import datetime

from database import get_connection_async
from embeds import embed_success, embed_error, embed_warning, embed_info
from commands_staff import StaffManager, DEFAULT_MISSIONS


# ============================================================
# COG
# ============================================================

class StaffConfigCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ============================================================
    # COMANDO: >staffconfig
    # ============================================================
    # Sem arg: mostra as configs atuais.
    # Com arg: define uma config.

    @commands.command(name="staffconfig")
    @commands.has_permissions(administrator=True)
    async def staff_config(self, ctx, key: str = None, *, value: str = None):
        """Ver ou definir configurações do staff."""
        db = await get_connection_async()
        config = await db["staff_config"].find_one({"guild_id": ctx.guild.id}) or {}

        # ─── Sem key → mostra painel. ───
        if not key:
            embed = discord.Embed(
                title="⚙️ CONFIGURAÇÕES STAFF",
                color=discord.Color.blue(), timestamp=datetime.utcnow()
            )
            embed.add_field(
                name="📋 Canal de Logs",
                value=f"<#{config['log_channel']}>" if config.get("log_channel") else "❌",
                inline=False
            )
            embed.add_field(
                name="👑 Cargo Staff",
                value=f"<@&{config['staff_role']}>" if config.get("staff_role") else "❌",
                inline=False
            )
            embed.add_field(name="🚫 Excluídos",
                            value=str(len(config.get("excluded_roles", []))),
                            inline=True)
            embed.add_field(name="📋 Metas",
                            value=str(len(config.get("missions", DEFAULT_MISSIONS))),
                            inline=True)
            return await ctx.send(embed=embed)

        # ─── Valida a key. ───
        valid_keys = ["log_channel", "staff_role", "excluded_roles"]
        if key not in valid_keys:
            return await ctx.send(embed=embed_error(
                f"❌ Opções: {', '.join(valid_keys)}"))
        if not value:
            return await ctx.send(embed=embed_error("❌ Forneça um valor."))

        # ─── Processa o valor conforme a key. ───

        # Se for canal, extrai o ID de "<#123>".
        if key.endswith("_channel"):
            match = re.search(r'<#(\d+)>', value)
            if match:
                value = int(match.group(1))
            else:
                return await ctx.send(embed=embed_error("❌ Use: #canal"))

        # Se for cargo, extrai o ID de "<@&123>".
        elif key == "staff_role":
            match = re.search(r'<@&(\d+)>', value)
            if match:
                value = int(match.group(1))
            else:
                return await ctx.send(embed=embed_error("❌ Use: @cargo"))

        # Se for lista de cargos excluídos, extrai todos os IDs.
        elif key == "excluded_roles":
            role_ids = re.findall(r'<@&(\d+)>', value)
            value = [int(rid) for rid in role_ids] if role_ids else []

        # ─── Salva. ───
        await db["staff_config"].update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {key: value}}, upsert=True
        )
        # Invalida o cache da config.
        StaffManager.invalidate_config(ctx.guild.id)
        await ctx.send(embed=embed_success(f"✅ `{key}` configurado!"))

    # ============================================================
    # COMANDO: >staffmissionadd
    # ============================================================
    # Adiciona uma nova meta.

    @commands.command(name="staffmissionadd")
    @commands.has_permissions(administrator=True)
    async def staff_mission_add(self, ctx, key: str, name: str, points: int,
                                requires_proof: str = "true"):
        """Adiciona uma nova meta."""
        db = await get_connection_async()
        config = await db["staff_config"].find_one({"guild_id": ctx.guild.id}) or {}
        missions = config.get("missions", dict(DEFAULT_MISSIONS))

        # Já existe?
        if key in missions:
            return await ctx.send(embed=embed_error(f"❌ Meta `{key}` já existe!"))

        # Adiciona.
        missions[key] = {
            "name": name, "points": points,
            "requires_proof": requires_proof.lower() in ["true", "1", "sim", "on"],
            "description": "", "category": "organization", "emoji": "📌"
        }
        await db["staff_config"].update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"missions": missions}}, upsert=True
        )
        StaffManager.invalidate_config(ctx.guild.id)
        await ctx.send(embed=embed_success(f"✅ Meta `{key}` adicionada!"))

    # ============================================================
    # COMANDO: >staffmissionremove
    # ============================================================

    @commands.command(name="staffmissionremove")
    @commands.has_permissions(administrator=True)
    async def staff_mission_remove(self, ctx, key: str):
        """Remove uma meta."""
        db = await get_connection_async()
        config = await db["staff_config"].find_one({"guild_id": ctx.guild.id}) or {}
        missions = config.get("missions", dict(DEFAULT_MISSIONS))

        if key not in missions:
            return await ctx.send(embed=embed_error(
                f"❌ Meta `{key}` não encontrada!"))
        # Remove.
        del missions[key]
        await db["staff_config"].update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"missions": missions}}, upsert=True
        )
        StaffManager.invalidate_config(ctx.guild.id)
        await ctx.send(embed=embed_success(f"✅ Meta `{key}` removida!"))

    # ============================================================
    # COMANDO: >staffgive
    # ============================================================
    # Dá pontos manualmente.

    @commands.command(name="staffgive")
    @commands.has_permissions(administrator=True)
    async def staff_give(self, ctx, member: discord.Member, points: int, *,
                          reason: str = "Ajuste"):
        """Dá pontos a um staff (ajuste manual)."""
        if points < 0:
            return await ctx.send(embed=embed_error(
                "❌ Pontos devem ser positivos!"))
        # A pontuação e a verificação dos cargos automáticos são operações Motor.
        await StaffManager.add_points(
            ctx.guild.id, member.id, points, "ajuste_manual", "", reason)
        await ctx.send(embed=embed_success(
            f"✅ {points} pts dados a {member.mention}!"))

    # ============================================================
    # COMANDO: >staffremove
    # ============================================================
    # Remove pontos manualmente.

    @commands.command(name="staffremove")
    @commands.has_permissions(administrator=True)
    async def staff_remove(self, ctx, member: discord.Member, points: int, *,
                            reason: str = "Ajuste"):
        """Remove pontos de um staff."""
        if points < 0:
            return await ctx.send(embed=embed_error(
                "❌ Pontos devem ser positivos!"))
        # Passa negativo pro add_points.
        # A pontuação e a verificação dos cargos automáticos são operações Motor.
        await StaffManager.add_points(
            ctx.guild.id, member.id, -points, "ajuste_manual", "", reason)
        await ctx.send(embed=embed_warning(
            f"✅ {points} pts removidos de {member.mention}!"))

    # ============================================================
    # COMANDO: >staffauto
    # ============================================================
    # Configura cargo automático por pontos.

    @commands.command(name="staffauto")
    @commands.has_permissions(administrator=True)
    async def staff_auto_roles(self, ctx, points: int, role: discord.Role):
        """Define um cargo pra ganhar ao atingir N pontos."""
        db = await get_connection_async()
        await db["staff_auto_roles"].update_one(
            {"guild_id": ctx.guild.id, "points_required": points},
            {"$set": {"role_id": role.id}}, upsert=True
        )
        await ctx.send(embed=embed_success(
            f"✅ {role.mention} ao atingir {points} pts!"))

    # ============================================================
    # COMANDO: >staffreset
    # ============================================================
    # Zera os pontos de um staff.

    @commands.command(name="staffreset")
    @commands.has_permissions(administrator=True)
    async def staff_reset(self, ctx, member: discord.Member):
        """Reseta os pontos de um staff."""
        db = await get_connection_async()
        # Apaga os pontos.
        await db["staff_points"].delete_one(
            {"guild_id": ctx.guild.id, "user_id": member.id})
        # Apaga o histórico de ações.
        await db["staff_actions"].delete_many(
            {"guild_id": ctx.guild.id, "user_id": member.id})
        await ctx.send(embed=embed_success(
            f"✅ Pontos de {member.mention} resetados!"))


async def setup(bot):
    """Chamado pelo main.py."""
    await bot.add_cog(StaffConfigCommands(bot))