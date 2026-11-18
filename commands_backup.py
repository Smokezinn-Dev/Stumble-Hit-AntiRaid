# ============================================================
# COMMANDS_BACKUP.PY - v8.0
# BACKUP E RESTORE INTELIGENTE + PERFORMANCE AVASSALADORA
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Salva uma "foto" completa do servidor no banco de dados:
#   • Todos os canais (nome, tipo, permissões)
#   • Todos os cargos (nome, cor, permissões)
#   • Membros e quais cargos eles têm
#   • Configs do bot (antinuke, automod, etc)
#   • Opcionalmente, textos de canais específicos
#
# Aí se algo der ruim, você pode RESTAURAR tudo de volta.
#
# v8.0 OTIMIZA (RESTORE):
# - Criação de CATEGORIAS em paralelo
# - Criação de CANAIS em paralelo (com mapa de categorias já pronto)
# - Aplicação de OVERWRITES em paralelo (dentro de cada canal)
# - Reatribuição de CARGOS aos membros em paralelo
# - Reposicionamento de canais em paralelo
# - Retry automático em 429/5xx em todas as fases
# ============================================================

import discord
from discord.ext import commands, tasks
from discord import app_commands
import asyncio
import json
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from config import (
    BACKUP_ENABLED, BACKUP_AUTO_HOUR, BACKUP_MAX_PER_GUILD,
    BACKUP_LOG_WEBHOOK,
)
from database import get_connection_async
from embeds import embed_success, embed_error, embed_warning, embed_info
from utils import safe_object_id, TTLCache, run_parallel


def _now() -> datetime:
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# CACHE DE CONFIG (evita ir no banco toda hora)
# ============================================================

class BackupConfigCache:
    _cache = TTLCache(max_size=200, ttl=120)

    @classmethod
    async def get(cls, guild_id: int) -> dict:
        """Pega config do servidor (cache OU banco) sem bloquear o loop."""
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached

        # Obtém a conexão Motor e aguarda a leitura no MongoDB.
        db = await get_connection_async()
        doc = await db["backup_config"].find_one({"guild_id": guild_id}) or {}
        cls._cache.set(guild_id, doc)
        return doc

    @classmethod
    def invalidate(cls, guild_id: int):
        cls._cache.invalidate(guild_id)


# ============================================================
# WEBHOOK LOGGER (envio rápido de logs)
# ============================================================

class _WebhookLogger:
    _cache: Dict[str, discord.Webhook] = {}

    @classmethod
    async def send(cls, url: str, embed: discord.Embed,
                   username: str = "💾 Backup",
                   avatar_url: Optional[str] = None) -> bool:
        """Envia via webhook. True = sucesso."""
        if not url:
            return False
        try:
            wh = cls._cache.get(url)
            if wh is None:
                wh = discord.Webhook.from_url(url, client=None)
                cls._cache[url] = wh
            await wh.send(embed=embed, username=username,
                          avatar_url=avatar_url, wait=False)
            return True
        except Exception:
            cls._cache.pop(url, None)
            return False


# ============================================================
# RESTORE VIA WEBHOOK (10x mais rápido que bot.send)
# ============================================================
# 📌 O QUE É ISSO?
# Quando restauramos mensagens de um canal, se usarmos o bot pra
# enviar cada mensagem, leva ~0.6s por mensagem (rate limit).
# Com webhook, leva ~0.15s. É 4x mais rápido.

class WebhookRestorer:
    @staticmethod
    async def get_or_create(channel: discord.TextChannel,
                            reason: str = "Backup restore"
                            ) -> Optional[discord.Webhook]:
        """Pega um webhook existente do bot OU cria um novo."""
        try:
            # Procura webhook que foi criado pelo bot.
            for wh in await channel.webhooks():
                if wh and wh.user and wh.user.id == channel.guild.me.id:
                    return wh
            # Não tem → cria.
            wh = await channel.create_webhook(
                name="GT Backup Restore", reason=reason)
            return wh
        except Exception:
            return None

    @staticmethod
    async def send_batch(webhook: discord.Webhook,
                          messages: List[dict],
                          delay: float = 0.15) -> int:
        """Envia um lote de mensagens via webhook. Retorna quantas foram."""
        sent = 0
        for m in messages:
            try:
                # Monta um embed com o conteúdo original.
                embed = discord.Embed(
                    description=m["content"][:2000],
                    color=discord.Color.greyple(),
                    timestamp=datetime.fromisoformat(m["timestamp"])
                    if m.get("timestamp") else _now())
                embed.set_author(
                    name=m.get("author", "Desconhecido")[:80],
                    icon_url=m.get("avatar_url"))
                # Marca se era fixada.
                if m.get("pinned"):
                    embed.set_footer(text="📌 Fixada")
                # Envia como se fosse o autor original.
                await webhook.send(
                    embed=embed,
                    username=m.get("author", "Desconhecido")[:80],
                    avatar_url=m.get("avatar_url"),
                    wait=False)
                sent += 1
                # Delay entre mensagens (evita rate limit).
                await asyncio.sleep(delay)
            except Exception:
                pass
        return sent


# ============================================================
# COG
# ============================================================

class BackupCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Se backup auto tá ligado, inicia a task diária.
        if BACKUP_ENABLED:
            self.auto_backup.start()

    def cog_unload(self):
        """Chamado quando o cog é descarregado."""
        try:
            self.auto_backup.cancel()
        except Exception:
            pass

    # ============================================================
    # AUTO BACKUP DIÁRIO
    # ============================================================

    @tasks.loop(hours=24)
    async def auto_backup(self):
        """Roda a cada 24h e faz backup automático (se for a hora certa)."""
        try:
            now = _now()
            # Só roda se for a hora configurada (BACKUP_AUTO_HOUR).
            if now.hour != BACKUP_AUTO_HOUR:
                return
            # Faz backup de cada servidor.
            for guild in self.bot.guilds:
                try:
                    await self._create_backup(
                        guild, author=None,
                        name=f"auto-{now.strftime('%Y%m%d')}")
                except Exception:
                    pass
        except Exception:
            pass

    @auto_backup.before_loop
    async def before_auto(self):
        """Espera o bot ficar pronto antes de começar."""
        await self.bot.wait_until_ready()

    # ============================================================
    # CRIAR BACKUP
    # ============================================================

    async def _create_backup(self, guild: discord.Guild, author,
                             name: str = None) -> str:
        """
        Cria um backup completo. Retorna o ID (string).
        Salva: canais, cargos, membros, configs e textos (se marcados).
        """
        # Usa a conexão assíncrona para que as operações de backup não
        # bloqueiem o event loop enquanto o MongoDB responde.
        db = await get_connection_async()
        now = _now()
        name = name or f"backup-{now.strftime('%Y%m%d-%H%M%S')}"

        # ─── CANAIS ───
        channels_data = []
        for ch in guild.channels:
            ch_entry = {
                "id": ch.id,
                "name": ch.name,
                "type": str(ch.type),
                "position": ch.position,
                "category_id": ch.category.id if ch.category else None,
                "topic": getattr(ch, "topic", None),
                "nsfw": getattr(ch, "nsfw", False),
                "slowmode": getattr(ch, "slowmode_delay", 0),
                "overwrites": [],
            }
            # Salva os overwrites (permissões) do canal.
            for target, ow in ch.overwrites.items():
                allow, deny = ow.pair()
                ch_entry["overwrites"].append({
                    "target_id": target.id,
                    "target_type": "role" if isinstance(target, discord.Role)
                                   else "member",
                    "allow": allow.value,
                    "deny": deny.value,
                })
            channels_data.append(ch_entry)

        # ─── CARGOS ───
        roles_data = []
        for role in guild.roles:
            # Pula o @everyone.
            if role.is_default():
                continue
            roles_data.append({
                "id": role.id,
                "name": role.name,
                "color": role.color.value,
                "permissions": role.permissions.value,
                "hoist": role.hoist,
                "mentionable": role.mentionable,
                "position": role.position,
            })

        # ─── MEMBROS ───
        # Salva quais cargos cada membro tem (não salva bots).
        members_data = []
        for m in guild.members:
            if m.bot:
                continue
            member_roles = [r.id for r in m.roles if not r.is_default()]
            if member_roles:
                members_data.append({
                    "user_id": m.id,
                    "username": str(m),
                    "roles": member_roles,
                })

        # ─── CONFIGS ───
        # Salva configs de várias coleções.
        configs = {}
        for col in [
            "guild_config", "staff_config", "automod_config",
            "antinuke_config", "antiescalation_config",
            "branding_config", "control_config",
        ]:
            try:
                doc = await db[col].find_one({"guild_id": guild.id})
                if doc:
                    doc.pop("_id", None)  # _id não é serializável
                    configs[col] = doc
            except Exception:
                pass

        # ─── CONFIG BACKUP ───
        # Pega quais canais salvam texto e limite.
        backup_cfg = await BackupConfigCache.get(guild.id)
        text_channels_ids = backup_cfg.get("text_channels", [])
        text_channel_limit = int(backup_cfg.get("text_channel_limit", 50))

        # ─── METADADOS ───
        meta = {
            "guild_id": guild.id,
            "guild_name": guild.name,
            "name": name,
            "created_at": now,
            "created_by": author.id if author else None,
            "created_by_name": str(author) if author else "AUTO",
            "channels": channels_data,
            "roles": roles_data,
            "members": members_data,
            "configs": configs,
            "text_channels_ids": text_channels_ids,
        }

        # Insere no banco.
        result = await db["backups"].insert_one(meta)
        backup_id = result.inserted_id

        # ─── TEXTOS DOS CANAIS MARCADOS ───
        # Pra cada canal marcado com >backuptext, salva N msgs.
        for ch_id in text_channels_ids:
            ch = guild.get_channel(ch_id)
            if not ch or not isinstance(ch, discord.TextChannel):
                continue
            messages = []
            try:
                # Pega as últimas N mensagens (do mais novo pro mais antigo).
                async for msg in ch.history(
                        limit=text_channel_limit, oldest_first=False):
                    if msg.author.bot:
                        continue  # pula mensagens de bot
                    messages.append({
                        "author": str(msg.author),
                        "avatar_url": (msg.author.display_avatar.url
                                       if msg.author.display_avatar else None),
                        "content": msg.content[:2000],
                        "timestamp": msg.created_at.isoformat(),
                        "pinned": msg.pinned,
                    })
                # Reverte pra ficar em ordem cronológica.
                messages.reverse()
            except Exception:
                pass
            # Se tem mensagens, salva.
            if messages:
                await db["backup_texts"].insert_one({
                    "backup_id": str(backup_id),
                    "guild_id": guild.id,
                    "channel_id": ch_id,
                    "channel_name": ch.name,
                    "messages": messages,
                })

        # ─── LIMITA BACKUPS ───
        # Se passou do limite, deleta os mais antigos.
        total = await db["backups"].count_documents({"guild_id": guild.id})
        if total > BACKUP_MAX_PER_GUILD:
            # O cursor do Motor é materializado de forma assíncrona e
            # mantém a mesma ordenação/quantidade de backups antigos.
            old = await db["backups"].find({"guild_id": guild.id}).sort(
                "created_at", 1
            ).limit(total - BACKUP_MAX_PER_GUILD).to_list(length=None)
            for old_doc in old:
                await db["backups"].delete_one({"_id": old_doc["_id"]})
                await db["backup_texts"].delete_many(
                    {"backup_id": str(old_doc["_id"])})

        # ─── LOG WEBHOOK ───
        if BACKUP_LOG_WEBHOOK:
            await _WebhookLogger.send(
                BACKUP_LOG_WEBHOOK,
                discord.Embed(
                    title="💾 Backup criado",
                    description=(
                        f"**Servidor:** {guild.name}\n"
                        f"**Nome:** {name}\n"
                        f"**ID:** `{backup_id}`\n"
                        f"**Canais:** {len(channels_data)}\n"
                        f"**Cargos:** {len(roles_data)}\n"
                        f"**Membros:** {len(members_data)}"
                    ),
                    color=discord.Color.green(),
                    timestamp=now))

        return str(backup_id)

    # ============================================================
    # COMANDOS - PAINEL E INFO
    # ============================================================

    @commands.command(name="backup", aliases=["bkp"])
    @commands.has_permissions(administrator=True)
    async def backup(self, ctx):
        """Painel do sistema de backup."""
        embed = discord.Embed(
            title="💾 SISTEMA DE BACKUP v8.0",
            description=(
                "**Comandos disponíveis:**\n\n"
                "• `>backupcreate [nome]` — Cria um backup\n"
                "• `>backuplist` — Lista backups\n"
                "• `>backupinfo <id>` — Detalhes\n"
                "• `>backuprestore <id> [modo]` — Restaura\n"
                "• `>backupdelete <id>` — Apaga backup\n\n"
                "**Modos de restore:**\n"
                "`all` / `canais` / `cargos` / `membros` / `config` / `textos`\n\n"
                "**Texto de canais (performance):**\n"
                "• `>backuptext #canal` — Marca canal\n"
                "• `>backuptext #canal remove` — Desmarca\n"
                "• `>backuptextlimit <n>` — Limite (10-500)\n"
                "• `>backuptextlist` — Lista canais\n\n"
                "⚡ **Restore usa webhook + paralelismo** → muito mais rápido!"
            ),
            color=discord.Color.blurple(),
            timestamp=_now())
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        await ctx.send(embed=embed)

    @commands.command(name="backupcreate")
    @commands.has_permissions(administrator=True)
    async def backup_create(self, ctx, *, name: str = None):
        """Cria um backup manualmente."""
        msg = await ctx.send(embed=embed_info("⏳ Criando backup..."))
        try:
            bkp_id = await self._create_backup(ctx.guild, ctx.author, name)
        except Exception as e:
            return await msg.edit(embed=embed_error(f"❌ Falha: {e}"))

        # Busca o que foi criado pra mostrar resumo.
        db = await get_connection_async()
        oid = safe_object_id(bkp_id)
        doc = await db["backups"].find_one({"_id": oid}) if oid else None
        if not doc:
            return await msg.edit(embed=embed_error("❌ Backup criado mas não encontrado."))

        embed = discord.Embed(
            title="✅ BACKUP CRIADO!",
            description=f"**{doc.get('name', name or 'sem nome')}**",
            color=discord.Color.green(),
            timestamp=_now())
        embed.add_field(name="🆔 ID", value=f"`{bkp_id}`", inline=False)
        embed.add_field(name="📁 Canais",
                        value=str(len(doc.get("channels", []))), inline=True)
        embed.add_field(name="👑 Cargos",
                        value=str(len(doc.get("roles", []))), inline=True)
        embed.add_field(name="👥 Membros",
                        value=str(len(doc.get("members", []))), inline=True)
        texts = await db["backup_texts"].count_documents({"backup_id": bkp_id})
        embed.add_field(name="📝 Textos salvos", value=str(texts), inline=True)
        await msg.edit(embed=embed)

    @commands.command(name="backuplist")
    @commands.has_permissions(administrator=True)
    async def backup_list(self, ctx):
        """Lista os backups disponíveis (até 20)."""
        db = await get_connection_async()
        # Mantém o limite de 20 e a ordenação do mais recente para o antigo.
        docs = await db["backups"].find({"guild_id": ctx.guild.id}).sort(
            "created_at", -1
        ).limit(20).to_list(length=20)
        if not docs:
            return await ctx.send(embed=embed_info("📋 Nenhum backup."))

        embed = discord.Embed(
            title="💾 BACKUPS DISPONÍVEIS",
            color=discord.Color.blurple(),
            timestamp=_now())
        for d in docs:
            bkp_id = str(d["_id"])
            ts = d.get("created_at", _now())
            ts_str = ts.strftime("%d/%m/%Y %H:%M") if hasattr(ts, "strftime") else str(ts)
            texts = await db["backup_texts"].count_documents({"backup_id": bkp_id})
            embed.add_field(
                name=f"`{bkp_id[:8]}` — {d.get('name', 'sem nome')}",
                value=(f"📅 {ts_str}\n"
                       f"👤 {d.get('created_by_name', '?')}\n"
                       f"📝 {texts} canal(is) com texto"),
                inline=False)
        embed.set_footer(
            text=f"Total: {len(docs)} | >backupinfo <id>")
        await ctx.send(embed=embed)

    @commands.command(name="backupinfo")
    @commands.has_permissions(administrator=True)
    async def backup_info(self, ctx, backup_id: str):
        """Detalhes de um backup específico."""
        oid = safe_object_id(backup_id)
        if not oid:
            return await ctx.send(embed=embed_error("❌ ID inválido."))
        db = await get_connection_async()
        doc = await db["backups"].find_one({"_id": oid, "guild_id": ctx.guild.id})
        if not doc:
            return await ctx.send(embed=embed_error("❌ Backup não encontrado."))

        texts = await db["backup_texts"].find({"backup_id": backup_id}).to_list(length=None)
        embed = discord.Embed(
            title=f"💾 {doc.get('name', 'sem nome')}",
            color=discord.Color.blurple(),
            timestamp=_now())
        embed.add_field(name="🆔", value=f"`{backup_id}`", inline=False)
        embed.add_field(name="📅 Criado", value=str(doc.get("created_at")),
                        inline=True)
        embed.add_field(name="👤 Por", value=doc.get("created_by_name", "?"),
                        inline=True)
        embed.add_field(name="📁 Canais",
                        value=str(len(doc.get("channels", []))), inline=True)
        embed.add_field(name="👑 Cargos",
                        value=str(len(doc.get("roles", []))), inline=True)
        embed.add_field(name="👥 Membros",
                        value=str(len(doc.get("members", []))), inline=True)
        if texts:
            lines = [f"• `{t['channel_name']}` — {len(t['messages'])} msgs"
                     for t in texts]
            embed.add_field(name="📝 Textos salvos",
                            value="\n".join(lines)[:1000], inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="backupdelete")
    @commands.has_permissions(administrator=True)
    async def backup_delete(self, ctx, backup_id: str):
        """Deleta um backup."""
        oid = safe_object_id(backup_id)
        if not oid:
            return await ctx.send(embed=embed_error("❌ ID inválido."))
        db = await get_connection_async()
        result = await db["backups"].delete_one(
            {"_id": oid, "guild_id": ctx.guild.id})
        # Deleta também os textos associados depois da operação principal.
        await db["backup_texts"].delete_many({"backup_id": backup_id})
        if result.deleted_count > 0:
            await ctx.send(embed=embed_success("✅ Backup removido."))
        else:
            await ctx.send(embed=embed_error("❌ Backup não encontrado."))

    # ============================================================
    # CONFIG DE TEXTO (quais canais salvam texto)
    # ============================================================

    @commands.command(name="backuptext")
    @commands.has_permissions(administrator=True)
    async def backup_text_channel(self, ctx, channel: discord.TextChannel = None,
                                   action: str = "add"):
        """Marca/desmarca um canal pra salvar texto nos backups."""
        if channel is None:
            return await ctx.send(embed=embed_error(
                "❌ Use: `>backuptext #canal [remove]`"))

        db = await get_connection_async()
        cfg = await BackupConfigCache.get(ctx.guild.id)
        ids = set(cfg.get("text_channels", []))

        # Remove.
        if action.lower() in ("remove", "remover", "del"):
            ids.discard(channel.id)
            await db["backup_config"].update_one(
                {"guild_id": ctx.guild.id},
                {"$set": {"text_channels": list(ids)}}, upsert=True)
            BackupConfigCache.invalidate(ctx.guild.id)
            return await ctx.send(embed=embed_success(
                f"✅ {channel.mention} não salva mais texto."))

        # Adiciona.
        ids.add(channel.id)
        await db["backup_config"].update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"text_channels": list(ids)}}, upsert=True)
        BackupConfigCache.invalidate(ctx.guild.id)
        await ctx.send(embed=embed_success(
            f"✅ {channel.mention} agora salva texto nos backups.\n"
            f"**Total:** {len(ids)} canal(is) marcados."))

    @commands.command(name="backuptextlimit")
    @commands.has_permissions(administrator=True)
    async def backup_text_limit(self, ctx, limit: int):
        """Define quantas mensagens por canal salvar (10-500)."""
        if limit < 10 or limit > 500:
            return await ctx.send(embed=embed_error(
                "❌ Limite: 10 a 500 mensagens."))
        db = await get_connection_async()
        await db["backup_config"].update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"text_channel_limit": limit}}, upsert=True)
        BackupConfigCache.invalidate(ctx.guild.id)
        await ctx.send(embed=embed_success(
            f"✅ Limite: **{limit}** mensagens por canal."))

    @commands.command(name="backuptextlist")
    @commands.has_permissions(administrator=True)
    async def backup_text_list(self, ctx):
        """Lista os canais que salvam texto."""
        cfg = await BackupConfigCache.get(ctx.guild.id)
        ids = cfg.get("text_channels", [])
        limit = cfg.get("text_channel_limit", 50)
        if not ids:
            return await ctx.send(embed=embed_info(
                "📋 Nenhum canal marcado. Use `>backuptext #canal`"))
        embed = discord.Embed(
            title="📝 Canais que salvam texto",
            color=discord.Color.blue(),
            timestamp=_now())
        embed.add_field(
            name="Canais",
            value="\n".join(f"• <#{c}>" for c in ids),
            inline=False)
        embed.set_footer(text=f"Limite por canal: {limit} msgs")
        await ctx.send(embed=embed)

    # ============================================================
    # RESTORE (v8.0 — PARALELO)
    # ============================================================
    # 📌 COMO FUNCIONA?
    # Reconstrói tudo o que foi salvo no backup. Dá pra restaurar:
    #   - TUDO (all)
    #   - Só canais
    #   - Só cargos
    #   - Só membros (reatribuir cargos)
    #   - Só configs
    #   - Só textos
    # Tudo em paralelo pra ser rápido.

    @commands.command(name="backuprestore")
    @commands.has_permissions(administrator=True)
    async def backup_restore(self, ctx, backup_id: str = None, *,
                              mode: str = "all"):
        """Restaura um backup. Modo: all/canais/cargos/membros/config/textos."""
        if not backup_id:
            return await ctx.send(embed=embed_error(
                "❌ Use: `>backuprestore <id> [all/canais/cargos/membros/config/textos]`"))

        oid = safe_object_id(backup_id)
        if not oid:
            return await ctx.send(embed=embed_error("❌ ID inválido."))
        db = await get_connection_async()
        doc = await db["backups"].find_one({"_id": oid, "guild_id": ctx.guild.id})
        if not doc:
            return await ctx.send(embed=embed_error("❌ Backup não encontrado."))

        # Valida o modo.
        mode = (mode or "all").lower().strip()
        valid_modes = ("all", "tudo", "canais", "channels", "cargos", "roles",
                       "membros", "members", "config", "textos", "texts")
        if mode not in valid_modes:
            return await ctx.send(embed=embed_error(
                "❌ Modo inválido. Use: all, canais, cargos, membros, "
                "config, textos"))

        # Pede confirmação.
        await ctx.send(embed=embed_warning(
            f"⚠️ **RESTORE INICIADO** — modo `{mode}`\n"
            f"Digite `CONFIRMAR` em 20 segundos."))

        def check(m):
            return (m.author.id == ctx.author.id and
                    m.channel.id == ctx.channel.id and
                    m.content.upper() == "CONFIRMAR")

        try:
            await self.bot.wait_for("message", check=check, timeout=20)
        except asyncio.TimeoutError:
            return await ctx.send(embed=embed_info("Cancelado."))

        # Contador de estatísticas.
        stats = {"roles": 0, "channels": 0, "categories": 0,
                 "members": 0, "texts": 0, "configs": 0}

        msg = await ctx.send(embed=embed_info("⏳ Restaurando..."))

        # ─── CARGOS ───
        if mode in ("all", "tudo", "cargos", "roles"):
            # Pega cargos que já existem (por nome).
            existing = {r.name.lower(): r for r in ctx.guild.roles}
            # Filtra os que NÃO existem (vamos criar).
            roles_to_create = [
                r for r in doc.get("roles", [])
                if r["name"].lower() not in existing
            ]

            async def _create_role(r):
                """Cria 1 cargo."""
                try:
                    perms = discord.Permissions(r["permissions"])
                    await ctx.guild.create_role(
                        name=r["name"],
                        color=discord.Color(r["color"]),
                        permissions=perms,
                        hoist=r.get("hoist", False),
                        mentionable=r.get("mentionable", False),
                        reason="Backup restore")
                    stats["roles"] += 1
                except Exception:
                    pass

            if roles_to_create:
                # 3 ao mesmo tempo, com retry.
                await run_parallel(
                    roles_to_create, _create_role,
                    concurrency=3, retry=2)

            # Reaplica posições (batch, precisa ser sequencial).
            try:
                sorted_roles = sorted(doc.get("roles", []),
                                       key=lambda x: x.get("position", 0))
                positions = {}
                for r in sorted_roles:
                    role = discord.utils.get(ctx.guild.roles, name=r["name"])
                    if role:
                        positions[role] = r.get("position", role.position)
                if positions:
                    await ctx.guild.edit_role_positions(positions=positions)
            except Exception:
                pass

        # ─── CANAIS ───
        if mode in ("all", "tudo", "canais", "channels"):
            # 1) Categorias em PARALELO.
            cat_map: Dict[int, discord.CategoryChannel] = {}
            existing_cats = {c.name.lower(): c for c in ctx.guild.categories}

            cats_to_create = []
            for ch in doc.get("channels", []):
                # Só categorias.
                if "category" not in str(ch.get("type", "")).lower():
                    continue
                # Já existe? Salva no mapa.
                if ch["name"].lower() in existing_cats:
                    cat_map[ch["id"]] = existing_cats[ch["name"].lower()]
                    continue
                cats_to_create.append(ch)

            async def _create_cat(ch):
                """Cria 1 categoria."""
                try:
                    new_cat = await ctx.guild.create_category(
                        ch["name"], reason="Backup restore")
                    cat_map[ch["id"]] = new_cat
                    stats["categories"] += 1
                except Exception:
                    pass

            if cats_to_create:
                await run_parallel(
                    cats_to_create, _create_cat,
                    concurrency=3, retry=2)

            # 2) Canais (não-categoria) em PARALELO.
            existing_channels = {c.name.lower(): c for c in ctx.guild.channels}
            created_map: Dict[int, discord.abc.GuildChannel] = {}

            channels_to_create = []
            for ch in doc.get("channels", []):
                # Pula categorias (já criadas).
                if "category" in str(ch.get("type", "")).lower():
                    continue
                # Já existe? Pula.
                if ch["name"].lower() in existing_channels:
                    continue
                channels_to_create.append(ch)

            async def _create_ch(ch):
                """Cria 1 canal."""
                try:
                    if "voice" in str(ch.get("type", "")).lower():
                        new_ch = await ctx.guild.create_voice_channel(
                            ch["name"], reason="Backup restore")
                    else:
                        new_ch = await ctx.guild.create_text_channel(
                            ch["name"],
                            topic=ch.get("topic"),
                            nsfw=ch.get("nsfw", False),
                            slowmode_delay=ch.get("slowmode", 0),
                            reason="Backup restore")
                    created_map[ch["id"]] = new_ch
                    stats["channels"] += 1
                except Exception:
                    pass

            if channels_to_create:
                await run_parallel(
                    channels_to_create, _create_ch,
                    concurrency=3, retry=2)

            # 3) Overwrites — dentro de cada canal, em paralelo.
            async def _apply_channel_overwrites(ch_data):
                """Aplica os overwrites de um canal."""
                new_ch = created_map.get(ch_data["id"])
                if not new_ch:
                    return
                ows = ch_data.get("overwrites", [])
                if not ows:
                    return

                async def _apply_ow(ow):
                    """Aplica 1 overwrite."""
                    try:
                        # Descobre o alvo (cargo ou membro).
                        if ow["target_type"] == "role":
                            target = ctx.guild.get_role(ow["target_id"])
                        else:
                            target = ctx.guild.get_member(ow["target_id"])
                        if not target:
                            return
                        # Converte e aplica.
                        allow = discord.Permissions(ow["allow"])
                        deny = discord.Permissions(ow["deny"])
                        await new_ch.set_permissions(
                            target,
                            overwrite=discord.PermissionOverwrite.from_pair(
                                allow, deny))
                    except Exception:
                        pass

                # 3 ao mesmo tempo por canal.
                await run_parallel(ows[:30], _apply_ow,
                                    concurrency=3, retry=1)

            # Aplica overwrites em até 4 canais simultâneos.
            created_channels_data = [
                ch for ch in doc.get("channels", [])
                if ch["id"] in created_map
            ]
            if created_channels_data:
                await run_parallel(
                    created_channels_data, _apply_channel_overwrites,
                    concurrency=4, retry=1)

            # 4) Reposiciona canais em PARALELO.
            repos_items = []
            for ch in doc.get("channels", []):
                new_ch = created_map.get(ch["id"])
                if not new_ch:
                    continue
                cat = cat_map.get(ch.get("category_id"))
                repos_items.append((new_ch, ch.get("position", 0), cat))

            async def _repos(item):
                """Reposiciona 1 canal."""
                channel, pos, cat = item
                try:
                    await channel.edit(category=cat, position=pos)
                except Exception:
                    pass

            if repos_items:
                await run_parallel(repos_items, _repos,
                                    concurrency=4, retry=1)

        # ─── MEMBROS ───
        if mode in ("all", "tudo", "membros", "members"):
            # Monta a lista de (membro, [cargos pra adicionar]).
            member_jobs = []
            for m_data in doc.get("members", []):
                member = ctx.guild.get_member(m_data["user_id"])
                if not member:
                    continue
                roles_to_add = []
                for role_id in m_data.get("roles", []):
                    role = ctx.guild.get_role(role_id)
                    # Se não achou por ID, tenta por nome.
                    if not role:
                        role_name = next(
                            (r["name"] for r in doc.get("roles", [])
                             if r["id"] == role_id), "")
                        role = discord.utils.get(ctx.guild.roles,
                                                  name=role_name)
                    # Se achou e não tem, adiciona.
                    if role and role not in member.roles:
                        roles_to_add.append(role)
                if roles_to_add:
                    member_jobs.append((member, roles_to_add))

            async def _add_roles(job):
                """Adiciona cargos a 1 membro."""
                member, roles = job
                try:
                    await member.add_roles(*roles, reason="Backup restore")
                    stats["members"] += 1
                except Exception:
                    pass

            if member_jobs:
                await run_parallel(member_jobs, _add_roles,
                                    concurrency=4, retry=2)

        # ─── CONFIG ───
        if mode in ("all", "tudo", "config"):
            for col, data in (doc.get("configs") or {}).items():
                try:
                    data["guild_id"] = ctx.guild.id
                    await db[col].update_one(
                        {"guild_id": ctx.guild.id},
                        {"$set": data}, upsert=True)
                    stats["configs"] += 1
                except Exception:
                    pass

        # ─── TEXTOS (via webhook) ───
        if mode in ("all", "tudo", "textos", "texts"):
            texts = await db["backup_texts"].find({"backup_id": backup_id}).to_list(length=None)

            async def _restore_text(t):
                """Restaura mensagens de 1 canal."""
                # Busca o canal por ID.
                ch = ctx.guild.get_channel(t["channel_id"])
                # Se não achou, tenta por nome.
                if not ch or not isinstance(ch, discord.TextChannel):
                    ch = discord.utils.get(
                        ctx.guild.text_channels, name=t["channel_name"])
                if not ch:
                    return
                # Tenta criar/usar webhook.
                webhook = await WebhookRestorer.get_or_create(
                    ch, reason="Backup restore")
                # Se não conseguiu webhook, faz via bot.send (mais lento).
                if webhook is None:
                    for m in t.get("messages", []):
                        try:
                            await ch.send(embed=discord.Embed(
                                description=m["content"],
                                color=discord.Color.greyple(),
                                timestamp=datetime.fromisoformat(
                                    m["timestamp"]) if m.get("timestamp")
                                else _now()))
                            stats["texts"] += 1
                            await asyncio.sleep(0.6)  # rate limit
                        except Exception:
                            pass
                else:
                    # Envia em lote via webhook (rápido).
                    sent = await WebhookRestorer.send_batch(
                        webhook, t.get("messages", []), delay=0.15)
                    stats["texts"] += sent
                    # Remove webhook.
                    try:
                        await webhook.delete(reason="Restore concluído")
                    except Exception:
                        pass

            if texts:
                # 2 canais em paralelo (mais que isso estoura rate).
                await run_parallel(texts, _restore_text,
                                    concurrency=2, retry=0)

        # ─── RESULTADO ───
        embed = discord.Embed(
            title="✅ RESTORE CONCLUÍDO",
            description=f"Modo: `{mode}`",
            color=discord.Color.green(),
            timestamp=_now())
        embed.add_field(name="👑 Cargos", value=str(stats["roles"]), inline=True)
        embed.add_field(name="📁 Categorias",
                        value=str(stats["categories"]), inline=True)
        embed.add_field(name="📁 Canais",
                        value=str(stats["channels"]), inline=True)
        embed.add_field(name="👥 Membros",
                        value=str(stats["members"]), inline=True)
        embed.add_field(name="⚙️ Configs",
                        value=str(stats["configs"]), inline=True)
        embed.add_field(name="📝 Textos",
                        value=str(stats["texts"]), inline=True)
        await msg.edit(embed=embed)

        # ─── LOG WEBHOOK ───
        if BACKUP_LOG_WEBHOOK:
            await _WebhookLogger.send(
                BACKUP_LOG_WEBHOOK,
                discord.Embed(
                    title="♻️ Restore executado",
                    description=(
                        f"**Servidor:** {ctx.guild.name}\n"
                        f"**Por:** {ctx.author}\n"
                        f"**Modo:** `{mode}`\n"
                        f"**ID:** `{backup_id}`"
                    ),
                    color=discord.Color.orange(),
                    timestamp=_now()))


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("BackupCommands") is None:
        await bot.add_cog(BackupCommands(bot))