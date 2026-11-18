# ============================================================
# COMMANDS_CASES.PY - CASES, NOTES E APPEALS
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Sistema de "processos" de moderação. Quando alguém é punido,
# cria um CASE (processo) com número sequencial.
#
# Também tem:
#   • NOTES: anotações sobre usuários
#   • APPEALS: recursos (usuário pede revisão da punição)
#
# Cada case tem um número (#1, #2, #3...) que pode ser consultado.
# ============================================================

# discord = biblioteca principal
import discord
# commands = criar comandos com prefixo e cogs
from discord.ext import commands
# app_commands = slash commands
from discord import app_commands
# datetime/timezone = trabalhar com datas
from datetime import datetime, timezone
# typing = só documentação de tipos
from typing import Optional

# database = conexão MongoDB
from database import get_connection_async
# Embeds coloridos prontos.
from embeds import embed_success, embed_error, embed_warning, embed_info


def _now():
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# COG
# ============================================================

class CasesCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ============================================================
    # HELPERS INTERNOS
    # ============================================================

    async def _next_case_id(self, guild_id: int) -> int:
        """
        Calcula o próximo número de case.
        Pega o último case e soma 1. Se não tem nenhum, começa em 1.
        """
        db = await get_connection_async()
        # sort=[("case_id", -1)] ordena do MAIOR pro menor, pega 1.
        last = await db["mod_cases"].find_one({"guild_id": guild_id}, sort=[("case_id", -1)])
        return (last["case_id"] + 1) if last else 1

    async def _create_case(self, guild_id: int, user_id: int, moderator_id: int,
                           action: str, reason: str) -> int:
        """Cria um novo case e retorna o número."""
        db = await get_connection_async()
        case_id = await self._next_case_id(guild_id)
        await db["mod_cases"].insert_one({
            "guild_id": guild_id,
            "case_id": case_id,       # número sequencial
            "user_id": user_id,        # quem foi punido
            "moderator_id": moderator_id,  # quem puniu
            "action": action,          # kick/ban/warn/etc
            "reason": reason,          # motivo
            "notes": [],               # notas (adicionadas depois)
            "timestamp": _now(),
            "active": True             # se já foi fechado
        })
        return case_id

    # ============================================================
    # COMANDOS DE CASE
    # ============================================================

    @commands.command(name="case")
    @commands.has_permissions(kick_members=True)
    async def case(self, ctx, case_id: int = None, action: str = None, *, extra: str = None):
        """
        Consulta/edita um case.
        Uso:
          >case 5           → ver case 5
          >case 5 note ...  → adicionar nota
          >case 5 close     → fechar case
        """
        # Sem ID → avisa como usar.
        if case_id is None:
            return await ctx.send(embed=embed_error(
                "❌ Uso: `>case <id>` | `>case <id> note <texto>` | `>case <id> close`"))

        db = await get_connection_async()
        case = await db["mod_cases"].find_one({"guild_id": ctx.guild.id, "case_id": case_id})
        if not case:
            return await ctx.send(embed=embed_error(f"❌ Case #{case_id} não encontrado."))

        # ─── Adicionar nota. ───
        if action == "note" and extra:
            await db["mod_cases"].update_one(
                {"_id": case["_id"]},
                {"$push": {"notes": {"author_id": ctx.author.id, "text": extra, "timestamp": _now()}}}
            )
            return await ctx.send(embed=embed_success(f"✅ Nota adicionada ao case #{case_id}."))

        # ─── Fechar case. ───
        if action == "close":
            await db["mod_cases"].update_one({"_id": case["_id"]}, {"$set": {"active": False}})
            return await ctx.send(embed=embed_success(f"✅ Case #{case_id} fechado."))

        # ─── Mostrar detalhes. ───
        # Tenta pegar o membro (ou mostra a menção).
        user = ctx.guild.get_member(case["user_id"]) or f"<@{case['user_id']}>"
        mod = ctx.guild.get_member(case["moderator_id"]) or f"<@{case['moderator_id']}>"

        embed = discord.Embed(
            title=f"📂 CASE #{case_id}",
            # Cor diferente se tá fechado.
            color=discord.Color.blue() if case.get("active") else discord.Color.greyple(),
            timestamp=case.get("timestamp", _now())
        )
        embed.add_field(name="👤 Usuário", value=str(user), inline=True)
        embed.add_field(name="🛡️ Moderador", value=str(mod), inline=True)
        embed.add_field(name="🎯 Ação", value=case.get("action", "?"), inline=True)
        embed.add_field(name="📝 Motivo", value=case.get("reason", "—"), inline=False)
        # Mostra as notas se tiver.
        notes = case.get("notes", [])
        if notes:
            lines = [f"• <@{n['author_id']}>: {n['text']}" for n in notes[:5]]
            embed.add_field(name=f"📌 Notas ({len(notes)})", value="\n".join(lines)[:1000], inline=False)
        embed.set_footer(text=f"ID interno: {case['_id']}")
        await ctx.send(embed=embed)

    @commands.command(name="cases")
    @commands.has_permissions(kick_members=True)
    async def cases(self, ctx, member: discord.Member = None):
        """Lista cases (de um membro específico OU do servidor inteiro)."""
        db = await get_connection_async()
        query = {"guild_id": ctx.guild.id}
        # Se passou um membro, filtra por ele.
        if member:
            query["user_id"] = member.id
        docs = await db["mod_cases"].find(query).sort("timestamp", -1).limit(15).to_list(length=15)
        if not docs:
            return await ctx.send(embed=embed_info("📋 Nenhum case encontrado."))
        embed = discord.Embed(
            title=f"📂 CASES{' de ' + member.display_name if member else ''}",
            color=discord.Color.blue(),
            timestamp=_now()
        )
        # Mostra até 10.
        for c in docs[:10]:
            icon = "🟢" if c.get("active") else "⚫"
            embed.add_field(
                name=f"{icon} #{c['case_id']} — {c.get('action', '?')}",
                value=f"<@{c['user_id']}> | {c.get('reason', '—')[:80]}",
                inline=False
            )
        await ctx.send(embed=embed)

    # ============================================================
    # COMANDOS DE NOTE
    # ============================================================
    # 📌 Notas são tipo "lembretes" sobre um usuário:
    # "Fulano já foi avisado 3x sobre spam", etc.

    @commands.command(name="note")
    @commands.has_permissions(kick_members=True)
    async def note(self, ctx, action: str = None, member: discord.Member = None, *, text: str = None):
        """
        Gerencia notas sobre usuários.
        Uso:
          >note add @user <texto>
          >note list @user
        """
        if not action or not member:
            return await ctx.send(embed=embed_error(
                "❌ Uso: `>note add @user <texto>` | `>note list @user` | `>note remove @user <id>`"))

        db = await get_connection_async()

        # ─── Adicionar nota. ───
        if action == "add" and text:
            await db["user_notes"].insert_one({
                "guild_id": ctx.guild.id,
                "user_id": member.id,
                "author_id": ctx.author.id,  # quem escreveu
                "text": text,
                "timestamp": _now()
            })
            return await ctx.send(embed=embed_success(f"✅ Nota adicionada em {member.mention}."))

        # ─── Listar notas. ───
        if action == "list":
            notes = await db["user_notes"].find(
                {"guild_id": ctx.guild.id, "user_id": member.id}
            ).sort("timestamp", -1).limit(20).to_list(length=20)
            if not notes:
                return await ctx.send(embed=embed_info(f"📋 {member.mention} não tem notas."))
            embed = discord.Embed(
                title=f"📝 Notas de {member.display_name}",
                color=discord.Color.blue(),
                timestamp=_now()
            )
            for n in notes[:10]:
                ts = n.get("timestamp", _now())
                ts_str = ts.strftime("%d/%m %H:%M") if hasattr(ts, "strftime") else str(ts)
                embed.add_field(
                    name=f"`{str(n['_id'])[:8]}` — {ts_str}",
                    value=f"<@{n['author_id']}>: {n['text'][:200]}",
                    inline=False
                )
            return await ctx.send(embed=embed)

        # Nada bateu.
        await ctx.send(embed=embed_error("❌ Ação inválida."))

    # ============================================================
    # COMANDOS DE APPEAL
    # ============================================================
    # 📌 Appeal = recurso. A pessoa punida pode pedir revisão.
    # Staff pode aceitar ou rejeitar.

    @commands.command(name="appeal")
    async def appeal(self, ctx, *, text: str = None):
        """
        Cria/gerencia appeals.
        Uso:
          >appeal <motivo>         → cria appeal
          >appeal accept <id>      → staff aceita
          >appeal reject <id>      → staff rejeita
          >appeal list             → lista pendentes
        """
        db = await get_connection_async()

        # ─── Se é staff E passou texto, pode ser comando admin. ───
        if ctx.author.guild_permissions.kick_members and text:
            parts = text.split(maxsplit=1)
            # "accept 5" ou "reject 5".
            if parts[0].lower() in ("accept", "reject") and len(parts) == 2:
                try:
                    appeal_id = int(parts[1].split()[0])
                except Exception:
                    return await ctx.send(embed=embed_error("❌ ID inválido."))
                status = "accepted" if parts[0].lower() == "accept" else "rejected"
                await db["appeals"].update_one(
                    {"guild_id": ctx.guild.id, "appeal_id": appeal_id},
                    {"$set": {"status": status, "handled_by": ctx.author.id, "handled_at": _now()}}
                )
                return await ctx.send(embed=embed_success(f"✅ Appeal #{appeal_id} → **{status}**"))

            # "list" → lista pendentes.
            if parts[0].lower() == "list":
                appeals = await db["appeals"].find(
                    {"guild_id": ctx.guild.id, "status": "pending"}
                ).sort("created_at", -1).limit(15).to_list(length=15)
                if not appeals:
                    return await ctx.send(embed=embed_info("📋 Nenhum appeal pendente."))
                embed = discord.Embed(title="📋 Appeals Pendentes",
                                      color=discord.Color.orange(),
                                      timestamp=_now())
                for a in appeals:
                    embed.add_field(
                        name=f"#{a['appeal_id']} — <@{a['user_id']}>",
                        value=a.get("text", "—")[:200],
                        inline=False
                    )
                return await ctx.send(embed=embed)

        # ─── Criação de appeal (usuário comum). ───
        if not text:
            return await ctx.send(embed=embed_error(
                "❌ Uso: `>appeal <motivo do recurso>`"))

        # Calcula próximo ID.
        last = await db["appeals"].find_one({"guild_id": ctx.guild.id}, sort=[("appeal_id", -1)])
        appeal_id = (last["appeal_id"] + 1) if last else 1

        # Salva no banco como "pending".
        await db["appeals"].insert_one({
            "guild_id": ctx.guild.id,
            "appeal_id": appeal_id,
            "user_id": ctx.author.id,
            "text": text,
            "status": "pending",
            "created_at": _now()
        })
        await ctx.send(embed=embed_success(
            f"✅ Appeal enviado! Um staff vai analisar.\n**ID:** `{appeal_id}`"))


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("CasesCommands") is None:
        await bot.add_cog(CasesCommands(bot))