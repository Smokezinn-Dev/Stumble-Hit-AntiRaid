# ============================================================
# COMMANDS_MODERATION_EXTRA2.PY - v2.0
# COMANDOS EXTRAS DE MODERAÇÃO
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Comandos avançados de moderação:
#   • jail/unjail — prende/solta (remove e restaura cargos)
#   • strip/restore — remove/restaura TODOS os cargos
#   • hideall/showall — esconde/mostra todos os canais
#   • roleall/roleunall — dá/remove cargo em TODOS
#   • vcmute/vcunmute — muta em voz
#   • dehoist — prefixa nicks com caracteres especiais
#   • snipe/editsnipe — mostra msg deletada/editada
#   • report, note2
#   • silentban, silentkick (sem avisar no canal)
#
# v2.0 OTIMIZA:
# - hideall / showall agora em PARALELO
# - roleall / roleunall agora em PARALELO
# - dehoist em PARALELO
# ============================================================

import asyncio
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

import discord
from discord.ext import commands

from database import get_connection_async
from embeds import embed_success, embed_error, embed_warning, embed_info
from utils import run_parallel


def _now():
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# COG
# ============================================================

class ModerationExtra2(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Cache de mensagens deletadas/editadas (pra snipe).
        self._deleted: Dict[int, List[dict]] = {}
        self._edited: Dict[int, List[dict]] = {}

    # ============================================================
    # LISTENERS DE SNIPE
    # ============================================================
    # Guarda as últimas mensagens deletadas/editadas por canal.

    @commands.Cog.listener()
    async def on_message_delete(self, message: discord.Message):
        """Guarda a mensagem deletada (pra >snipe)."""
        if not message.guild or message.author.bot:
            return
        self._deleted.setdefault(message.channel.id, [])
        self._deleted[message.channel.id].append({
            "author": message.author,
            "content": message.content,
            "attachments": [a.url for a in message.attachments],
            "timestamp": _now(),
        })
        # Mantém só as últimas 10.
        self._deleted[message.channel.id] = self._deleted[message.channel.id][-10:]

    @commands.Cog.listener()
    async def on_message_edit(self, before: discord.Message,
                                after: discord.Message):
        """Guarda a edição (pra >editsnipe)."""
        if not before.guild or before.author.bot:
            return
        if before.content == after.content:
            return
        self._edited.setdefault(before.channel.id, [])
        self._edited[before.channel.id].append({
            "author": before.author,
            "before": before.content,
            "after": after.content,
            "timestamp": _now(),
        })
        self._edited[before.channel.id] = self._edited[before.channel.id][-10:]

    # ============================================================
    # SNIPE
    # ============================================================

    @commands.command(name="snipe")
    @commands.has_permissions(manage_messages=True)
    async def snipe(self, ctx, index: int = 1):
        """Mostra mensagem deletada recentemente."""
        msgs = self._deleted.get(ctx.channel.id, [])
        if not msgs:
            return await ctx.send(embed=embed_info(
                "📭 Nenhuma mensagem deletada recente."))
        # Valida o índice.
        if index < 1 or index > len(msgs):
            return await ctx.send(embed=embed_error(
                f"❌ Índice inválido. Total: {len(msgs)}"))

        msg = msgs[-index]
        embed = discord.Embed(
            description=msg["content"] or "*sem texto*",
            color=discord.Color.blue(),
            timestamp=msg["timestamp"])
        embed.set_author(
            name=str(msg["author"]),
            icon_url=msg["author"].display_avatar.url)
        if msg["attachments"]:
            embed.add_field(name="Anexos",
                            value="\n".join(msg["attachments"][:3]),
                            inline=False)
        embed.set_footer(text=f"Mensagem #{index} de {len(msgs)}")
        await ctx.send(embed=embed)

    @commands.command(name="editsnipe", aliases=["esnipe"])
    @commands.has_permissions(manage_messages=True)
    async def editsnipe(self, ctx, index: int = 1):
        """Mostra mensagem editada recentemente."""
        msgs = self._edited.get(ctx.channel.id, [])
        if not msgs:
            return await ctx.send(embed=embed_info(
                "📭 Nenhuma edição recente."))
        if index < 1 or index > len(msgs):
            return await ctx.send(embed=embed_error(
                f"❌ Índice inválido. Total: {len(msgs)}"))

        msg = msgs[-index]
        embed = discord.Embed(
            color=discord.Color.orange(),
            timestamp=msg["timestamp"])
        embed.set_author(
            name=str(msg["author"]),
            icon_url=msg["author"].display_avatar.url)
        embed.add_field(name="Antes", value=msg["before"] or "*vazio*",
                        inline=False)
        embed.add_field(name="Depois", value=msg["after"] or "*vazio*",
                        inline=False)
        embed.set_footer(text=f"Edição #{index} de {len(msgs)}")
        await ctx.send(embed=embed)

    # ============================================================
    # JAIL / UNJAIL
    # ============================================================
    # 📌 JAIL = prender. Remove todos os cargos (salvando pra depois)
    # e aplica timeout de 24h.

    @commands.command(name="jail")
    @commands.has_permissions(moderate_members=True)
    async def jail(self, ctx, member: discord.Member, *,
                    reason: str = "Jail"):
        """Prende o membro removendo todos os cargos (salvando pra depois)."""
        # Hierarquia: não pode prender quem tem cargo maior.
        if member.top_role >= ctx.author.top_role and \
                ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=embed_error("❌ Cargo superior."))

        # Obtém a conexão Motor sem bloquear o event loop do Discord.
        db = await get_connection_async()
        # Salva os cargos atuais antes de removê-los; assim, a restauração
        # continua disponível mesmo se uma ação posterior do Discord falhar.
        role_ids = [r.id for r in member.roles if not r.is_default()]
        await db["jail_saved"].update_one(
            {"guild_id": ctx.guild.id, "user_id": member.id},
            {"$set": {"roles": role_ids, "saved_at": _now()}},
            upsert=True)

        try:
            # Remove todos os cargos abaixo do topo do bot.
            removable = [r for r in member.roles
                         if not r.is_default() and r < ctx.guild.me.top_role]
            if removable:
                await member.remove_roles(*removable,
                                            reason=f"Jail: {reason}")
            # Timeout de 24h.
            await member.timeout(
                discord.utils.utcnow() + timedelta(hours=24),
                reason=f"Jail: {reason}")
            await ctx.send(embed=embed_success(
                f"🔒 {member.mention} preso.\n**Motivo:** {reason}"))
        except Exception as e:
            await ctx.send(embed=embed_error(f"❌ {e}"))

    @commands.command(name="unjail")
    @commands.has_permissions(moderate_members=True)
    async def unjail(self, ctx, member: discord.Member):
        """Solta o membro restaurando os cargos salvos."""
        # Busca o registro persistido de jail de forma assíncrona.
        db = await get_connection_async()
        doc = await db["jail_saved"].find_one(
            {"guild_id": ctx.guild.id, "user_id": member.id})
        if not doc:
            return await ctx.send(embed=embed_warning(
                f"⚠️ {member.mention} não estava preso."))

        # Pega os cargos salvos (filtra os que ainda existem).
        roles = [ctx.guild.get_role(rid) for rid in doc.get("roles", [])]
        roles = [r for r in roles if r and r < ctx.guild.me.top_role]

        try:
            # Remove timeout.
            await member.timeout(None)
            # Restaura cargos.
            if roles:
                await member.add_roles(*roles, reason="Unjail")
            # Apaga o registro.
            db["jail_saved"].delete_one(
                {"guild_id": ctx.guild.id, "user_id": member.id})
            await ctx.send(embed=embed_success(
                f"🔓 {member.mention} solto. {len(roles)} cargos "
                f"restaurados."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"❌ {e}"))

    # ============================================================
    # STRIP / RESTORE
    # ============================================================
    # STRIP = remove TODOS os cargos (mais severo que jail).

    @commands.command(name="strip")
    @commands.has_permissions(administrator=True)
    async def strip(self, ctx, member: discord.Member, *,
                     reason: str = "Strip de cargos"):
        """Remove TODOS os cargos do membro (salva)."""
        # Salva primeiro os cargos para manter a funcionalidade de restore.
        db = await get_connection_async()
        role_ids = [r.id for r in member.roles if not r.is_default()]
        await db["strip_saved"].update_one(
            {"guild_id": ctx.guild.id, "user_id": member.id},
            {"$set": {"roles": role_ids, "saved_at": _now()}},
            upsert=True)

        try:
            removable = [r for r in member.roles
                         if not r.is_default() and r < ctx.guild.me.top_role]
            if removable:
                await member.remove_roles(*removable,
                                            reason=f"Strip: {reason}")
            await ctx.send(embed=embed_success(
                f"🧹 {member.mention} teve {len(removable)} cargos "
                f"removidos.\n**Motivo:** {reason}"))
        except Exception as e:
            await ctx.send(embed=embed_error(f"❌ {e}"))

    @commands.command(name="restore")
    @commands.has_permissions(administrator=True)
    async def restore(self, ctx, member: discord.Member):
        """Restaura cargos removidos por strip/jail."""
        # Mantém a prioridade original: procura strip antes de jail.
        db = await get_connection_async()
        doc = await db["strip_saved"].find_one(
            {"guild_id": ctx.guild.id, "user_id": member.id})
        if doc is None:
            doc = await db["jail_saved"].find_one(
                {"guild_id": ctx.guild.id, "user_id": member.id})
        if not doc:
            return await ctx.send(embed=embed_warning(
                f"⚠️ Nenhum cargo salvo para {member.mention}."))

        roles = [ctx.guild.get_role(rid) for rid in doc.get("roles", [])]
        roles = [r for r in roles if r and r < ctx.guild.me.top_role]

        try:
            if roles:
                await member.add_roles(*roles, reason="Restore")
            # Limpa ambos os registros.
            # Remove os dois possíveis registros somente após a restauração.
            await db["strip_saved"].delete_one(
                {"guild_id": ctx.guild.id, "user_id": member.id})
            await db["jail_saved"].delete_one(
                {"guild_id": ctx.guild.id, "user_id": member.id})
            await ctx.send(embed=embed_success(
                f"✅ {len(roles)} cargos restaurados em {member.mention}."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"❌ {e}"))

    # ============================================================
    # HIDE ALL / SHOW ALL — PARALELO
    # ============================================================
    # 📌 hideall = esconde todos os canais.
    # showall = mostra todos.

    @commands.command(name="hideall")
    @commands.has_permissions(administrator=True)
    async def hideall(self, ctx):
        """Esconde todos os canais do @everyone (paralelo)."""
        channels = list(ctx.guild.channels)
        default_role = ctx.guild.default_role

        stats = {"ok": 0, "fail": 0}

        async def _worker(ch):
            """Esconde 1 canal."""
            try:
                ow = ch.overwrites_for(default_role)
                ow.view_channel = False
                await ch.set_permissions(default_role, overwrite=ow,
                                           reason="Hideall")
                stats["ok"] += 1
            except Exception:
                stats["fail"] += 1

        # 6 canais ao mesmo tempo, com retry.
        await run_parallel(
            channels, _worker,
            concurrency=6,
            retry=2,
        )

        await ctx.send(embed=embed_success(
            f"🙈 {stats['ok']} canais escondidos."
            + (f" ({stats['fail']} falhas)" if stats['fail'] else "")))

    @commands.command(name="showall")
    @commands.has_permissions(administrator=True)
    async def showall(self, ctx):
        """Mostra todos os canais para @everyone (paralelo)."""
        channels = list(ctx.guild.channels)
        default_role = ctx.guild.default_role

        stats = {"ok": 0, "fail": 0}

        async def _worker(ch):
            """Mostra 1 canal."""
            try:
                ow = ch.overwrites_for(default_role)
                ow.view_channel = None  # herda padrão
                await ch.set_permissions(default_role, overwrite=ow,
                                           reason="Showall")
                stats["ok"] += 1
            except Exception:
                stats["fail"] += 1

        await run_parallel(
            channels, _worker,
            concurrency=6,
            retry=2,
        )

        await ctx.send(embed=embed_success(
            f"👁️ {stats['ok']} canais visíveis."
            + (f" ({stats['fail']} falhas)" if stats['fail'] else "")))

    # ============================================================
    # ROLE ALL / UNALL — PARALELO
    # ============================================================
    # 📌 roleall = dá um cargo pra TODOS os membros.
    # roleunall = remove um cargo de TODOS.

    @commands.command(name="roleall")
    @commands.has_permissions(administrator=True)
    async def roleall(self, ctx, role: discord.Role):
        """Adiciona um cargo a todos os membros (humans) em paralelo."""
        # Hierarquia: cargo precisa ser abaixo do topo do bot.
        if role >= ctx.guild.me.top_role:
            return await ctx.send(embed=embed_error(
                "❌ Cargo acima do meu."))

        # Alvos: humans sem o cargo.
        targets = [m for m in ctx.guild.members
                    if not m.bot and role not in m.roles]

        if not targets:
            return await ctx.send(embed=embed_info(
                "ℹ️ Todos os membros já têm esse cargo."))

        msg = await ctx.send(embed=embed_info(
            f"⏳ Adicionando {role.mention} em `{len(targets)}` membros..."))

        stats = {"ok": 0, "fail": 0}

        async def _worker(m: discord.Member):
            """Adiciona o cargo em 1 membro."""
            try:
                await m.add_roles(role, reason="Roleall")
                stats["ok"] += 1
            except Exception:
                stats["fail"] += 1

        # 5 membros ao mesmo tempo.
        await run_parallel(
            targets, _worker,
            concurrency=5,
            retry=2,
        )

        await msg.edit(embed=embed_success(
            f"✅ {role.mention} adicionado em `{stats['ok']}` membros."
            + (f" ({stats['fail']} falhas)" if stats['fail'] else "")))

    @commands.command(name="roleunall")
    @commands.has_permissions(administrator=True)
    async def roleunall(self, ctx, role: discord.Role):
        """Remove um cargo de todos os membros em paralelo."""
        if role >= ctx.guild.me.top_role:
            return await ctx.send(embed=embed_error(
                "❌ Cargo acima do meu."))

        # Alvos: quem TEM o cargo.
        targets = [m for m in ctx.guild.members if role in m.roles]

        if not targets:
            return await ctx.send(embed=embed_info(
                "ℹ️ Nenhum membro tem esse cargo."))

        msg = await ctx.send(embed=embed_info(
            f"⏳ Removendo {role.mention} de `{len(targets)}` membros..."))

        stats = {"ok": 0, "fail": 0}

        async def _worker(m: discord.Member):
            try:
                await m.remove_roles(role, reason="Roleunall")
                stats["ok"] += 1
            except Exception:
                stats["fail"] += 1

        await run_parallel(
            targets, _worker,
            concurrency=5,
            retry=2,
        )

        await msg.edit(embed=embed_success(
            f"✅ {role.mention} removido de `{stats['ok']}` membros."
            + (f" ({stats['fail']} falhas)" if stats['fail'] else "")))

    # ============================================================
    # VC MUTE / UNMUTE
    # ============================================================

    @commands.command(name="vcmute")
    @commands.has_permissions(mute_members=True)
    async def vcmute(self, ctx, member: discord.Member):
        """Muta o membro em canais de voz."""
        if not member.voice:
            return await ctx.send(embed=embed_error(
                "❌ Membro não está em voz."))
        try:
            await member.edit(mute=True, reason=f"Por {ctx.author}")
            await ctx.send(embed=embed_success(
                f"🔇 {member.mention} mutado em voz."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"❌ {e}"))

    @commands.command(name="vcunmute")
    @commands.has_permissions(mute_members=True)
    async def vcunmute(self, ctx, member: discord.Member):
        """Desmuta em voz."""
        if not member.voice:
            return await ctx.send(embed=embed_error(
                "❌ Membro não está em voz."))
        try:
            await member.edit(mute=False, reason=f"Por {ctx.author}")
            await ctx.send(embed=embed_success(
                f"🔊 {member.mention} desmutado."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"❌ {e}"))

    # ============================================================
    # DEHOIST — PARALELO
    # ============================================================
    # 📌 DEHOIST = adiciona um prefixo em nicks que começam com
    # caractere especial (pra não ficar no topo da lista).

    @commands.command(name="dehoist")
    @commands.has_permissions(manage_nicknames=True)
    async def dehoist(self, ctx, *, prefix: str = "!"):
        """Adiciona prefixo em membros que começam com caractere especial (paralelo)."""
        # Regex: começa com NÃO alfanumérico.
        pattern = re.compile(r"^[^a-zA-Z0-9]")

        # Alvos: humans, não é owner, nome começa com char especial.
        targets = [
            m for m in ctx.guild.members
            if not m.bot
            and m.id != ctx.guild.owner_id
            and pattern.match(m.display_name or "")
        ]

        if not targets:
            return await ctx.send(embed=embed_info(
                "✨ Nenhum membro precisa de dehoist."))

        msg = await ctx.send(embed=embed_info(
            f"⏳ Dehoistando `{len(targets)}` membros..."))

        stats = {"ok": 0, "fail": 0}

        async def _worker(m: discord.Member):
            """Aplica o prefixo em 1 membro."""
            try:
                new_nick = f"{prefix}{m.display_name}"[:32]
                await m.edit(nick=new_nick, reason="Dehoist")
                stats["ok"] += 1
            except Exception:
                stats["fail"] += 1

        await run_parallel(
            targets, _worker,
            concurrency=5,
            retry=2,
        )

        await msg.edit(embed=embed_success(
            f"✅ `{stats['ok']}` membros dehoistados."
            + (f" ({stats['fail']} falhas)" if stats['fail'] else "")))

    # ============================================================
    # SILENT (sem avisar no canal)
    # ============================================================

    @commands.command(name="silentban")
    @commands.has_permissions(ban_members=True)
    async def silentban(self, ctx, member: discord.Member, *,
                        reason: str = "Silent ban"):
        """Bane sem enviar mensagem no canal."""
        try:
            await member.ban(reason=reason, delete_message_days=1)
            # Apaga a mensagem do comando.
            await ctx.message.delete()
        except Exception as e:
            await ctx.send(embed=embed_error(f"❌ {e}"), delete_after=5)

    @commands.command(name="silentkick")
    @commands.has_permissions(kick_members=True)
    async def silentkick(self, ctx, member: discord.Member, *,
                         reason: str = "Silent kick"):
        """Expulsa sem enviar mensagem no canal."""
        try:
            await member.kick(reason=reason)
            await ctx.message.delete()
        except Exception as e:
            await ctx.send(embed=embed_error(f"❌ {e}"), delete_after=5)

    # ============================================================
    # REPORT
    # ============================================================

    @commands.command(name="report")
    async def report(self, ctx, member: discord.Member, *, reason: str):
        """Reporta um membro para os admins."""
        # Persiste o report antes de enviar a confirmação ao usuário.
        db = await get_connection_async()
        await db["reports"].insert_one({
            "guild_id": ctx.guild.id,
            "reporter_id": ctx.author.id,
            "target_id": member.id,
            "reason": reason,
            "channel_id": ctx.channel.id,
            "timestamp": _now(),
            "resolved": False,
        })
        await ctx.send(embed=embed_success(
            f"✅ Report enviado para a equipe. Obrigado!"))

        # Se tem canal de log, avisa lá também.
        # Consulta a configuração do servidor sem bloquear o event loop.
        cfg = await db["guild_config"].find_one(
            {"guild_id": ctx.guild.id}) or {}
        log_ch = cfg.get("log_channel")
        if log_ch:
            ch = ctx.guild.get_channel(log_ch)
            if ch:
                try:
                    embed = discord.Embed(
                        title="📢 Novo Report",
                        description=(
                            f"**Reportado:** {member.mention}\n"
                            f"**Por:** {ctx.author.mention}\n"
                            f"**Canal:** {ctx.channel.mention}"
                        ),
                        color=discord.Color.orange(),
                        timestamp=_now())
                    embed.add_field(name="Motivo", value=reason, inline=False)
                    await ch.send(embed=embed)
                except Exception:
                    pass

    # ============================================================
    # NOTE2 (sistema rápido de notas)
    # ============================================================

    @commands.group(name="note2", aliases=["n2"], invoke_without_command=True)
    @commands.has_permissions(kick_members=True)
    async def note2(self, ctx):
        """Sistema rápido de notas por usuário."""
        await ctx.send(embed=embed_info(
            "Uso: `>note2 add @user <texto>` | `>note2 list @user` | "
            "`>note2 clear @user`"))

    @note2.command(name="add")
    @commands.has_permissions(kick_members=True)
    async def note2_add(self, ctx, member: discord.Member, *, text: str):
        """Adiciona uma nota."""
        # Grava a nota mantendo os mesmos campos e vínculo com o servidor.
        db = await get_connection_async()
        await db["quick_notes"].insert_one({
            "guild_id": ctx.guild.id,
            "user_id": member.id,
            "author_id": ctx.author.id,
            "text": text,
            "timestamp": _now(),
        })
        await ctx.send(embed=embed_success(
            f"✅ Nota adicionada em {member.mention}."))

    @note2.command(name="list")
    @commands.has_permissions(kick_members=True)
    async def note2_list(self, ctx, member: discord.Member):
        """Lista as notas de um usuário."""
        # Ordena pelas mais recentes e materializa no máximo 15 registros,
        # preservando o limite original antes de montar o embed.
        db = await get_connection_async()
        notes = await db["quick_notes"].find(
            {"guild_id": ctx.guild.id, "user_id": member.id}
        ).sort("timestamp", -1).limit(15).to_list(length=15)
        if not notes:
            return await ctx.send(embed=embed_info(
                f"📋 {member.mention} não tem notas."))
        embed = discord.Embed(
            title=f"📝 Notas de {member.display_name}",
            color=discord.Color.blue(),
            timestamp=_now())
        embed.set_thumbnail(url=member.display_avatar.url)
        # Mostra as 8 mais recentes.
        for n in notes[:8]:
            ts = n["timestamp"].strftime("%d/%m %H:%M")
            embed.add_field(
                name=f"<@{n['author_id']}> — {ts}",
                value=n["text"][:200],
                inline=False)
        await ctx.send(embed=embed)

    @note2.command(name="clear")
    @commands.has_permissions(administrator=True)
    async def note2_clear(self, ctx, member: discord.Member):
        """Limpa todas as notas de um usuário."""
        # Remove todas as notas do usuário apenas neste servidor.
        db = await get_connection_async()
        r = await db["quick_notes"].delete_many(
            {"guild_id": ctx.guild.id, "user_id": member.id})
        await ctx.send(embed=embed_success(
            f"✅ {r.deleted_count} notas removidas."))


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("ModerationExtra2") is None:
        await bot.add_cog(ModerationExtra2(bot))