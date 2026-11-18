# ============================================================
# COMMANDS_STAFF.PY - v6.0 (CARGOS AUTO APLICADOS)
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Sistema de STAFF (equipe) com METAS e PONTUAÇÃO.
# Membros da staff registram ações que fizeram (mediar briga,
# punir corretamente, atender ticket) e ganham pontos.
#
# Recursos:
#   • Metas configuráveis (staffmissionadd)
#   • Pontuação por ação
#   • Ranking da staff
#   • Cargos automáticos por pontos (ex: 100 pts = cargo)
#   • Auto-detecção de algumas ações
# ============================================================

import discord
from discord.ext import commands
from datetime import datetime, timezone
from typing import Optional

from database import get_connection_async
from embeds import embed_success, embed_error, embed_warning, embed_info
from utils import TTLCache


# ============================================================
# MISSÕES PADRÃO
# ============================================================
# Lista inicial de metas. Pode ser editada com comandos.

DEFAULT_MISSIONS = {
    "mediar_briga": {
        "name": "⚖️ Mediar briga e acalmar chat", "points": 2, "requires_proof": True,
        "description": "Print antes/depois", "category": "security", "emoji": "⚖️"
    },
    "punir_corretamente": {
        "name": "🚫 Punir corretamente: warn/kick/ban", "points": 1, "requires_proof": True,
        "description": "Com motivo válido", "category": "security", "emoji": "🚫"
    },
    "limpar_chat": {
        "name": "🧹 Limpar chat de spam/flood", "points": 1, "requires_proof": False,
        "description": "Usar comando limpar", "category": "security", "emoji": "🧹"
    },
    "animar_chat": {
        "name": "💬 Animar o chat", "points": 1, "requires_proof": False,
        "description": "Manter ativo", "category": "organization", "emoji": "💬"
    },
    "atender_ticket": {
        "name": "🎫 Atender ticket", "points": 2, "requires_proof": False,
        "description": "Responder ticket", "category": "organization", "emoji": "🎫"
    }
}


# ============================================================
# MANAGER (regras de negócio do staff)
# ============================================================

class StaffManager:
    # Cache de config (evita ir no banco).
    _cfg_cache = TTLCache(max_size=200, ttl=120)

    @staticmethod
    async def get_config(guild_id: int) -> dict:
        """Pega config do servidor (cache OU banco)."""
        cached = StaffManager._cfg_cache.get(guild_id)
        if cached is not None:
            return cached
        # Obtém o banco Motor; a aquisição do banco e a consulta não bloqueiam o event loop.
        db = await get_connection_async()
        # find_one é assíncrono no Motor, portanto seu resultado precisa ser aguardado.
        doc = await db["staff_config"].find_one({"guild_id": guild_id}) or {}
        StaffManager._cfg_cache.set(guild_id, doc)
        return doc

    @staticmethod
    def invalidate_config(guild_id: int):
        """Limpa cache da config."""
        StaffManager._cfg_cache.invalidate(guild_id)

    @staticmethod
    async def get_points(guild_id: int, user_id: int) -> int:
        """Pega a pontuação de um user."""
        # Abre o acesso assíncrono ao banco sem criar um cliente PyMongo síncrono.
        db = await get_connection_async()
        # Consulta o documento de pontuação do membro usando a API Motor.
        doc = await db["staff_points"].find_one({"guild_id": guild_id, "user_id": user_id})
        return doc["points"] if doc else 0

    @staticmethod
    async def get_missions(guild_id: int) -> dict:
        """Pega as missões configuradas (ou padrão)."""
        # Reaproveita a leitura assíncrona/cacheada da configuração do servidor.
        config = await StaffManager.get_config(guild_id)
        return config.get("missions", DEFAULT_MISSIONS)

    @staticmethod
    async def add_points(guild_id: int, user_id: int, points: int,
                   mission_key: str, proof: str = "", notes: str = "") -> list:
        """
        Adiciona/remove pontos de um user.
        Registra a ação e devolve lista de cargos que devem ser aplicados.
        """
        # Usa o cliente Motor para que as gravações não bloqueiem o loop do Discord.
        db = await get_connection_async()
        # Incrementa a pontuação do membro; upsert cria o documento se ainda não existir.
        await db["staff_points"].update_one(
            {"guild_id": guild_id, "user_id": user_id},
            {"$inc": {"points": points},
             "$set": {"last_updated": datetime.now(timezone.utc)}},
            upsert=True
        )
        # Registra a ação.
        # Registra o evento separadamente para preservar o histórico detalhado da staff.
        await db["staff_actions"].insert_one({
            "guild_id": guild_id, "user_id": user_id,
            "mission_key": mission_key, "points": points,
            "proof_url": proof, "notes": notes,
            "approved": True, "timestamp": datetime.now(timezone.utc)
        })
        # Verifica se bateu algum cargo automático.
        # Após salvar os pontos, consulta quais cargos automáticos foram alcançados.
        return await StaffManager.check_auto_roles(guild_id, user_id)

    @staticmethod
    async def check_auto_roles(guild_id: int, user_id: int) -> list:
        """Verifica quais cargos automáticos o user merece agora."""
        # Recupera a pontuação atual antes de comparar com os requisitos dos cargos.
        points = await StaffManager.get_points(guild_id, user_id)
        # Obtém o banco Motor e cria o cursor de cargos que o membro já alcançou.
        db = await get_connection_async()
        cursor = db["staff_auto_roles"].find(
            {"guild_id": guild_id, "points_required": {"$lte": points}}
        )
        # Motor usa cursor assíncrono; to_list materializa os documentos de forma aguardada.
        roles = await cursor.to_list(length=None)
        return [r["role_id"] for r in roles]

    @staticmethod
    async def get_ranking(guild_id: int, limit: int = 15) -> list:
        """Retorna o ranking (top N)."""
        # Obtém a coleção de pontuações pela conexão assíncrona.
        db = await get_connection_async()
        # Mantém a ordenação decrescente e o limite original do ranking.
        cursor = db["staff_points"].find(
            {"guild_id": guild_id, "points": {"$gt": 0}}
        ).sort("points", -1).limit(limit)
        # Converte o cursor Motor em lista antes de montar a resposta do comando.
        docs = await cursor.to_list(length=limit)
        return [(d["user_id"], d["points"]) for d in docs]

    @staticmethod
    async def get_actions(guild_id: int, user_id: int, limit: int = 10) -> list:
        """Histórico de ações de um user."""
        # Usa Motor para consultar somente ações aprovadas deste membro.
        db = await get_connection_async()
        # Preserva a ordenação mais recente primeiro e o limite original do histórico.
        cursor = db["staff_actions"].find(
            {"guild_id": guild_id, "user_id": user_id, "approved": True}
        ).sort("timestamp", -1).limit(limit)
        # Materializa os resultados de maneira assíncrona para o embed do Discord.
        return await cursor.to_list(length=limit)


# ============================================================
# NOTA SOBRE O BANCO DE DADOS
# ============================================================
# As funções de StaffManager que acessam o MongoDB são async.
# Por isso, todo chamador assíncrono deste arquivo usa await.
# Consultas find() retornam cursores Motor, materializados com to_list().
# Essa conversão evita chamadas PyMongo síncronas neste fluxo de Staff.
# ============================================================

# ============================================================
# COG
# ============================================================

class StaffCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # ============================================================
    # HELPERS
    # ============================================================

    async def _is_staff(self, member: discord.Member) -> bool:
        """Diz se um membro é staff (tem cargo ou permissão)."""
        config = await StaffManager.get_config(member.guild.id)
        role_id = config.get("staff_role")
        if role_id:
            return member.get_role(int(role_id)) is not None
        # Fallback: permissão de gerenciar mensagens.
        return member.guild_permissions.manage_messages

    async def _is_excluded(self, member: discord.Member) -> bool:
        """Diz se o membro está excluído (não conta pontos)."""
        config = await StaffManager.get_config(member.guild.id)
        excluded = config.get("excluded_roles", [])
        return any(member.get_role(int(r)) for r in excluded)

    async def _log_action(self, guild, action, target, moderator, reason):
        """Loga uma ação no canal de log (se configurado)."""
        try:
            config = await StaffManager.get_config(guild.id)
            log_ch = config.get("log_channel")
            if not log_ch:
                return
            channel = guild.get_channel(int(log_ch))
            if not channel:
                return
            embed = discord.Embed(
                title=f"📋 Staff Log - {action}",
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc)
            )
            if target:
                embed.add_field(name="Alvo",
                                value=getattr(target, "mention", str(target)),
                                inline=True)
            if moderator:
                embed.add_field(name="Moderador",
                                value=moderator.mention, inline=True)
            embed.add_field(name="Motivo", value=str(reason)[:500],
                            inline=False)
            await channel.send(embed=embed)
        except Exception:
            pass

    async def _apply_auto_roles(self, member: discord.Member,
                                  role_ids: list) -> list:
        """Aplica os cargos automáticos. Retorna os que conseguiu."""
        applied = []
        for rid in role_ids:
            role = member.guild.get_role(int(rid))
            # Só aplica se não tem ainda.
            if role and role not in member.roles:
                try:
                    await member.add_roles(role, reason="Staff auto-role")
                    applied.append(role)
                except Exception:
                    pass
        return applied

    # ============================================================
    # LISTENERS (auto-detecção de ações)
    # ============================================================

    @commands.Cog.listener()
    async def on_message(self, message):
        """Detecta uso de >purge ou >limpar e dá pontos."""
        if message.author.bot or not message.guild:
            return
        if not await self._is_staff(message.author) or await self._is_excluded(message.author):
            return
        content = message.content.lower()
        # Se usou purge/limpar, ganha ponto.
        if content.startswith(">purge") or content.startswith(">limpar"):
            await StaffManager.add_points(
                message.guild.id, message.author.id, 1,
                "limpar_chat", "", "Auto-detectado")

    @commands.Cog.listener()
    async def on_command_completion(self, ctx):
        """Dá pontos quando um comando de punição é usado."""
        if ctx.author.bot or not ctx.guild:
            return
        if not await self._is_staff(ctx.author) or await self._is_excluded(ctx.author):
            return
        # Se usou kick/ban/timeout/warn, ganha ponto.
        if ctx.command.name in ["kick", "ban", "timeout", "warn"]:
            await StaffManager.add_points(
                ctx.guild.id, ctx.author.id, 1,
                "punir_corretamente", "", f"Auto: {ctx.command.name}")

    # ============================================================
    # COMANDOS
    # ============================================================

    @commands.command(name="staff")
    async def staff_menu(self, ctx):
        """Menu principal da staff."""
        if not await self._is_staff(ctx.author):
            return await ctx.send(embed=embed_error("❌ Você não é staff!"))
        points = await StaffManager.get_points(ctx.guild.id, ctx.author.id)
        missions_count = len(await StaffManager.get_missions(ctx.guild.id))
        embed = discord.Embed(
            title="🛡️ CENTRAL DE STAFF",
            description="Sistema de metas e pontuação",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        embed.add_field(name="⭐ Sua Pontuação",
                        value=f"**{points} pontos**", inline=True)
        embed.add_field(name="📋 Metas",
                        value=f"**{missions_count} metas**", inline=True)
        embed.add_field(
            name="📌 Comandos",
            value="• `>staffmetas` - Ver todas\n"
                  "• `>staffadd <meta> [prova]` - Registrar\n"
                  "• `>staffranking` - Ranking\n"
                  "• `>staffhistory` - Histórico",
            inline=False
        )
        await ctx.send(embed=embed)

    @commands.command(name="staffmetas")
    async def staff_missions(self, ctx):
        """Lista as metas disponíveis."""
        if not await self._is_staff(ctx.author):
            return await ctx.send(embed=embed_error("❌ Você não é staff!"))
        missions = await StaffManager.get_missions(ctx.guild.id)
        embed = discord.Embed(
            title="📋 METAS STAFF",
            description="Registre suas ações e acumule pontos!",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc)
        )
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        # Separa por categoria.
        security, organization = [], []
        for key, mission in missions.items():
            if not mission:
                continue
            name = mission.get("name", key)
            pts = mission.get("points", 0)
            # Emoji de prova requerida.
            proof = " 📎" if mission.get("requires_proof", False) else ""
            line = f"• {name}: **+{pts} pts**{proof}"
            # Coloca na categoria certa.
            (security if mission.get("category") == "security"
                     else organization).append(line)

        if security:
            embed.add_field(name="🛡️ SEGURANÇA",
                            value="\n".join(security), inline=False)
        if organization:
            embed.add_field(name="📈 ORGANIZAÇÃO",
                            value="\n".join(organization), inline=False)
        embed.set_footer(text="Use >staffadd <meta> [prova]")
        await ctx.send(embed=embed)

    @commands.command(name="staffadd")
    async def staff_add(self, ctx, mission_key: str, *, proof: str = ""):
        """Registra uma ação e ganha pontos."""
        if not await self._is_staff(ctx.author):
            return await ctx.send(embed=embed_error("❌ Você não é staff!"))
        if await self._is_excluded(ctx.author):
            return await ctx.send(embed=embed_error("❌ Você está excluído!"))

        missions = await StaffManager.get_missions(ctx.guild.id)
        # Valida a meta.
        if mission_key not in missions:
            return await ctx.send(embed=embed_error(
                f"❌ Meta `{mission_key}` não encontrada!"))
        mission = missions[mission_key]
        if not mission:
            return await ctx.send(embed=embed_error(
                f"❌ Meta `{mission_key}` inválida!"))

        # Se requer prova, precisa passar.
        if mission.get("requires_proof", False) and not proof:
            return await ctx.send(embed=embed_warning(
                f"⚠️ Esta meta requer prova! Use: `>staffadd {mission_key} [link]`"))

        points = mission.get("points", 1)

        # Adiciona os pontos.
        new_role_ids = await StaffManager.add_points(
            ctx.guild.id, ctx.author.id, points, mission_key, proof)

        # Monta resposta.
        embed = discord.Embed(
            title="✅ AÇÃO REGISTRADA!",
            description=f"**{mission.get('name', mission_key)}** registrada!",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(name="📊 Pontos", value=f"+{points}", inline=True)
        embed.add_field(name="⭐ Total",
                        value=f"{await StaffManager.get_points(ctx.guild.id, ctx.author.id)} pts",
                        inline=True)
        if proof:
            embed.add_field(name="📎 Prova", value=proof, inline=False)

        # ─── APLICA CARGOS AUTOMÁTICOS. ───
        if new_role_ids:
            applied = await self._apply_auto_roles(ctx.author, new_role_ids)
            if applied:
                embed.add_field(name="🏅 NOVOS CARGOS!",
                                value="\n".join(r.mention for r in applied),
                                inline=False)

        await ctx.send(embed=embed)
        await self._log_action(ctx.guild, "Staff Action", ctx.author, None,
                               f"{mission.get('name', mission_key)}: +{points} pts")

    @commands.command(name="staffranking")
    async def staff_ranking(self, ctx):
        """Mostra o ranking da staff."""
        if not await self._is_staff(ctx.author):
            return await ctx.send(embed=embed_error("❌ Você não é staff!"))
        ranking = await StaffManager.get_ranking(ctx.guild.id, 15)
        if not ranking:
            return await ctx.send(embed=embed_info("📊 Nenhum staff registrado."))
        embed = discord.Embed(title="🏆 RANKING STAFF",
                              color=discord.Color.gold(),
                              timestamp=datetime.now(timezone.utc))
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        # Medalhas.
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
        for i, (uid, pts) in enumerate(ranking):
            user = ctx.guild.get_member(uid)
            name = user.display_name if user else f"ID: {uid}"
            medal = medals[i] if i < 10 else f"{i+1}."
            embed.add_field(name=f"{medal} {name}",
                            value=f"⭐ **{pts} pts**", inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="staffhistory")
    async def staff_history(self, ctx, member: discord.Member = None):
        """Histórico de ações de um staff."""
        if not await self._is_staff(ctx.author):
            return await ctx.send(embed=embed_error("❌ Você não é staff!"))
        member = member or ctx.author
        actions = await StaffManager.get_actions(ctx.guild.id, member.id, 10)
        if not actions:
            return await ctx.send(embed=embed_info(
                f"📋 {member.mention} não tem ações."))
        total = await StaffManager.get_points(ctx.guild.id, member.id)
        embed = discord.Embed(
            title=f"📋 HISTÓRICO DE {member.display_name.upper()}",
            description=f"⭐ Total: **{total} pontos**",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc)
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        for action in actions[:8]:
            mission_key = action.get("mission_key", "?")
            points = action.get("points", 0)
            timestamp = action.get("timestamp", datetime.now(timezone.utc))
            proof = action.get("proof_url") or "Sem prova"
            ts_str = timestamp.strftime("%d/%m %H:%M") if hasattr(timestamp, "strftime") else str(timestamp)
            embed.add_field(
                name=f"📌 {mission_key}",
                value=f"📊 +{points} pts\n📅 {ts_str}\n📎 {proof[:60]}",
                inline=False
            )
        await ctx.send(embed=embed)


async def setup(bot):
    """Chamado pelo main.py."""
    await bot.add_cog(StaffCommands(bot))