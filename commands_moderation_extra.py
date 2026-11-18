# ============================================================
# COMMANDS_MODERATION_EXTRA.PY - v9.0
# PURGE AVANÇADO + TEMPLOCK + ROLEPERSIST + STATS
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Comandos extras de moderação:
#   • Purges avançados (por link, imagem, regex, idade, etc)
#   • >templock (trava canal temporariamente)
#   • >rolepersist (mantém cargo mesmo se sair/voltar)
#   • >nuke (deleta e recria o canal)
#   • Stats de purge
#
# v9.0 OTIMIZA:
# - Todos os purges usam batch paralelo quando > 1 canal
# - purgemulti rodando em PARALELO (era sequencial)
# - Fallback de purge com retry em 429/5xx
# ============================================================

import discord
from discord.ext import commands
from discord import app_commands
import re
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List, Callable

from config import BACKUP_LOG_WEBHOOK
from database import get_connection_async
from embeds import embed_success, embed_error, embed_warning, embed_info
from utils import RateLimiter, run_parallel


def _now() -> datetime:
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ─── Regex ───
# URL (http/https).
URL_REGEX = re.compile(r"https?://\S+", re.IGNORECASE)
# Convite do Discord.
INVITE_REGEX = re.compile(
    r"(discord\.(gg|io|me|li)|discordapp\.com/invite)/[a-zA-Z0-9]+",
    re.IGNORECASE)
# Menção (<@123> ou <@&123>).
MENTION_REGEX = re.compile(r"<@[!&]?\d+>")

# Limites de purge.
PURGE_MAX = 500
PURGE_SAFE = 200
PURGE_DELAY = 0.05


# ============================================================
# WEBHOOK LOGGER (log de purge via webhook)
# ============================================================

class _WebhookLogger:
    _cache: Dict[str, discord.Webhook] = {}

    @classmethod
    async def send(cls, url: str, embed: discord.Embed,
                   username: str = "🗑️ Purge",
                   avatar_url: Optional[str] = None) -> bool:
        """Envia um embed via webhook."""
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


async def _log_purge(guild_id: int, channel_id: int, moderator_id: int,
                     method: str, count: int, reason: str = ""):
    """Salva o registro de um purge no banco."""
    try:
        # Abre a conexão Motor dentro da rotina assíncrona.
        db = await get_connection_async()
        # Aguarda a gravação para não deixar a operação de log pendente.
        await db["purge_logs"].insert_one({
            "guild_id": guild_id,
            "channel_id": channel_id,
            "moderator_id": moderator_id,
            "method": method,
            "count": count,
            "reason": reason,
            "timestamp": _now(),
        })
    except Exception:
        pass


async def _safe_purge(channel: discord.TextChannel,
                      limit: int,
                      check: Optional[Callable] = None,
                      before=None, after=None) -> int:
    """Purge com retry em rate limit. Retorna quantas apagou."""
    for attempt in range(3):
        try:
            deleted = await channel.purge(
                limit=limit, check=check, before=before, after=after,
                bulk=True)
            return len(deleted)
        except discord.HTTPException as e:
            # 429 = rate limit.
            if e.status == 429 and attempt < 2:
                await asyncio.sleep(1.2 * (attempt + 1))
                continue
            return 0
        except Exception:
            return 0
    return 0


def _clamp(amount: int) -> int:
    """Limita o número de mensagens a apagar."""
    return max(1, min(amount, PURGE_MAX))


# ============================================================
# COG
# ============================================================

class ModerationExtra(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Rate limiter pra comandos (5 por 10s).
        self._rl = RateLimiter(rate=5, per=10.0)

    async def _send_purge_log(self, ctx, method: str, count: int,
                               reason: str = ""):
        """Salva + envia log de purge via webhook."""
        await _log_purge(ctx.guild.id, ctx.channel.id, ctx.author.id,
                         method, count, reason)

        if BACKUP_LOG_WEBHOOK:
            await _WebhookLogger.send(
                BACKUP_LOG_WEBHOOK,
                discord.Embed(
                    title="🗑️ Purge executado",
                    description=(
                        f"**Moderador:** {ctx.author.mention}\n"
                        f"**Canal:** {ctx.channel.mention}\n"
                        f"**Método:** `{method}`\n"
                        f"**Apagadas:** `{count}` mensagens\n"
                        f"**Motivo:** {reason or '—'}"
                    ),
                    color=discord.Color.orange(),
                    timestamp=_now()))

    # ============================================================
    # PURGES SIMPLES (por tipo de mensagem)
    # ============================================================

    @commands.command(name="purgelink")
    @commands.has_permissions(manage_messages=True)
    async def purge_link(self, ctx, amount: int = 50):
        """Apaga mensagens que contêm links."""
        amount = _clamp(amount)

        def check(m):
            return bool(URL_REGEX.search(m.content or ""))

        count = await _safe_purge(ctx.channel, amount, check=check)
        await ctx.send(embed=embed_success(
            f"🗑️ {count} mensagens com link apagadas."), delete_after=5)
        await self._send_purge_log(ctx, "link", count)

    @commands.command(name="purgeimage")
    @commands.has_permissions(manage_messages=True)
    async def purge_image(self, ctx, amount: int = 50):
        """Apaga mensagens que contêm imagem."""
        amount = _clamp(amount)

        def check(m):
            if not m.attachments:
                return False
            # Verifica se algum anexo é imagem.
            return any(
                a.content_type and "image" in a.content_type
                for a in m.attachments)

        count = await _safe_purge(ctx.channel, amount, check=check)
        await ctx.send(embed=embed_success(
            f"🗑️ {count} mensagens com imagem apagadas."), delete_after=5)
        await self._send_purge_log(ctx, "image", count)

    @commands.command(name="purgeattach", aliases=["purgefile"])
    @commands.has_permissions(manage_messages=True)
    async def purge_attach(self, ctx, amount: int = 50):
        """Apaga mensagens com QUALQUER anexo."""
        amount = _clamp(amount)

        def check(m):
            return bool(m.attachments)

        count = await _safe_purge(ctx.channel, amount, check=check)
        await ctx.send(embed=embed_success(
            f"🗑️ {count} mensagens com anexo apagadas."), delete_after=5)
        await self._send_purge_log(ctx, "attachment", count)

    @commands.command(name="purgeregex")
    @commands.has_permissions(manage_messages=True)
    async def purge_regex(self, ctx, amount: int, pattern: str):
        """Apaga mensagens que batem com um regex."""
        amount = _clamp(amount)
        try:
            rx = re.compile(pattern, re.IGNORECASE)
        except re.error as e:
            return await ctx.send(embed=embed_error(
                f"❌ Regex inválida: `{e}`"), delete_after=8)

        def check(m):
            return bool(rx.search(m.content or ""))

        count = await _safe_purge(ctx.channel, amount, check=check)
        await ctx.send(embed=embed_success(
            f"🗑️ {count} mensagens apagadas por regex."), delete_after=5)
        await self._send_purge_log(ctx, "regex", count,
                                    reason=f"pattern: {pattern[:60]}")

    @commands.command(name="purgeafter")
    @commands.has_permissions(manage_messages=True)
    async def purge_after(self, ctx, message_id: int):
        """Apaga mensagens ENVIADAS APÓS uma mensagem específica."""
        try:
            msg = await ctx.channel.fetch_message(message_id)
        except Exception:
            return await ctx.send(embed=embed_error(
                "❌ Mensagem não encontrada."), delete_after=6)

        count = await _safe_purge(ctx.channel, PURGE_MAX, after=msg)
        await ctx.send(embed=embed_success(
            f"🗑️ {count} mensagens apagadas após `{message_id}`."),
            delete_after=5)
        await self._send_purge_log(ctx, "after", count)

    @commands.command(name="purgebefore")
    @commands.has_permissions(manage_messages=True)
    async def purge_before(self, ctx, message_id: int):
        """Apaga mensagens ENVIADAS ANTES de uma mensagem específica."""
        try:
            msg = await ctx.channel.fetch_message(message_id)
        except Exception:
            return await ctx.send(embed=embed_error(
                "❌ Mensagem não encontrada."), delete_after=6)

        count = await _safe_purge(ctx.channel, PURGE_MAX, before=msg)
        await ctx.send(embed=embed_success(
            f"🗑️ {count} mensagens apagadas antes de `{message_id}`."),
            delete_after=5)
        await self._send_purge_log(ctx, "before", count)

    @commands.command(name="purgemention", aliases=["purgeping"])
    @commands.has_permissions(manage_messages=True)
    async def purge_mention(self, ctx, amount: int = 50):
        """Apaga mensagens com menção a user OU cargo."""
        amount = _clamp(amount)

        def check(m):
            return (bool(m.mentions) or bool(m.role_mentions)
                    or bool(MENTION_REGEX.search(m.content or "")))

        count = await _safe_purge(ctx.channel, amount, check=check)
        await ctx.send(embed=embed_success(
            f"🗑️ {count} mensagens com menção apagadas."), delete_after=5)
        await self._send_purge_log(ctx, "mention", count)

    @commands.command(name="purgeembed")
    @commands.has_permissions(manage_messages=True)
    async def purge_embed(self, ctx, amount: int = 50):
        """Apaga mensagens que TÊM embed."""
        amount = _clamp(amount)

        def check(m):
            return bool(m.embeds)

        count = await _safe_purge(ctx.channel, amount, check=check)
        await ctx.send(embed=embed_success(
            f"🗑️ {count} mensagens com embed apagadas."), delete_after=5)
        await self._send_purge_log(ctx, "embed", count)

    @commands.command(name="purgeinvite")
    @commands.has_permissions(manage_messages=True)
    async def purge_invite(self, ctx, amount: int = 50):
        """Apaga mensagens com convite do Discord."""
        amount = _clamp(amount)

        def check(m):
            return bool(INVITE_REGEX.search(m.content or ""))

        count = await _safe_purge(ctx.channel, amount, check=check)
        await ctx.send(embed=embed_success(
            f"🗑️ {count} mensagens com convite apagadas."), delete_after=5)
        await self._send_purge_log(ctx, "invite", count)

    @commands.command(name="purgebot", aliases=["purgebots2"])
    @commands.has_permissions(manage_messages=True)
    async def purge_bot(self, ctx, bot: discord.Member = None,
                        amount: int = 50):
        """Apaga mensagens de BOT (ou de um bot específico)."""
        amount = _clamp(amount)

        # Se passou um bot específico.
        if bot and not bot.bot:
            return await ctx.send(embed=embed_error(
                f"❌ {bot.mention} não é um bot."), delete_after=6)

        if bot:
            def check(m):
                return m.author.id == bot.id
            method = f"bot:{bot.name}"
        else:
            def check(m):
                return m.author.bot
            method = "bots"

        count = await _safe_purge(ctx.channel, amount, check=check)
        await ctx.send(embed=embed_success(
            f"🗑️ {count} mensagens de bot apagadas."), delete_after=5)
        await self._send_purge_log(ctx, method, count)

    @commands.command(name="purgeuser")
    @commands.has_permissions(manage_messages=True)
    async def purge_user(self, ctx, member: discord.Member, amount: int = 50):
        """Apaga mensagens de UM usuário específico."""
        amount = _clamp(amount)

        def check(m):
            return m.author.id == member.id

        count = await _safe_purge(ctx.channel, amount, check=check)
        await ctx.send(embed=embed_success(
            f"🗑️ {count} mensagens de {member.mention} apagadas."),
            delete_after=5)
        await self._send_purge_log(ctx, f"user:{member.id}", count)

    @commands.command(name="purgepinguser")
    @commands.has_permissions(manage_messages=True)
    async def purge_ping_user(self, ctx, member: discord.Member,
                               amount: int = 50):
        """Apaga mensagens que MENCIONAM um usuário."""
        amount = _clamp(amount)

        def check(m):
            return member in m.mentions or f"<@{member.id}>" in (m.content or "")

        count = await _safe_purge(ctx.channel, amount, check=check)
        await ctx.send(embed=embed_success(
            f"🗑️ {count} mensagens mencionando {member.mention} apagadas."),
            delete_after=5)
        await self._send_purge_log(ctx, f"ping:{member.id}", count)

    @commands.command(name="purgenuke", aliases=["purgewebhook"])
    @commands.has_permissions(manage_messages=True)
    async def purge_nuke(self, ctx, amount: int = 100):
        """Apaga mensagens enviadas por WEBHOOK."""
        amount = _clamp(amount)

        def check(m):
            return m.webhook_id is not None

        count = await _safe_purge(ctx.channel, amount, check=check)
        await ctx.send(embed=embed_success(
            f"🗑️ {count} mensagens de webhook apagadas."), delete_after=5)
        await self._send_purge_log(ctx, "webhook", count)

    # ============================================================
    # PURGE MULTI — agora PARALELO
    # ============================================================
    # 📌 Purge em VÁRIOS canais ao mesmo tempo.

    @commands.command(name="purgemulti", aliases=["purgeall"])
    @commands.has_permissions(administrator=True)
    async def purge_multi(self, ctx, amount: int = 20,
                          *channels: discord.TextChannel):
        """Apaga N mensagens em VÁRIOS canais (paralelo)."""
        amount = _clamp(amount)
        if not channels:
            return await ctx.send(embed=embed_error(
                "❌ Uso: `>purgemulti <n> #canal1 #canal2 ...`"),
                delete_after=8)

        if len(channels) > 10:
            return await ctx.send(embed=embed_error(
                "❌ Máximo: 10 canais."), delete_after=8)

        # ─── Execução PARALELA. ───
        results_map: Dict[int, int] = {}

        async def _worker(ch: discord.TextChannel):
            """Purge em 1 canal."""
            deleted = await ch.purge(limit=amount, bulk=True)
            results_map[ch.id] = len(deleted)

        # 4 canais ao mesmo tempo, com retry.
        stats = await run_parallel(
            list(channels), _worker,
            concurrency=4,
            retry=2,
        )

        total = sum(results_map.values())

        # Monta resumo.
        results = []
        for ch in channels:
            n = results_map.get(ch.id)
            if n is None:
                results.append(f"• {ch.mention}: ❌ falha")
            else:
                results.append(f"• {ch.mention}: `{n}`")

        embed = discord.Embed(
            title="🗑️ PURGE EM MASSA (paralelo)",
            description=(
                f"**Total:** `{total}` mensagens\n"
                f"**Canais:** `{len(channels)}` "
                f"(concurrency=4)\n\n" + "\n".join(results)
            ),
            color=discord.Color.green(),
            timestamp=_now())
        await ctx.send(embed=embed, delete_after=15)
        await self._send_purge_log(
            ctx, f"multi:{len(channels)}canais", total)

    @commands.command(name="purgeage", aliases=["purgeold"])
    @commands.has_permissions(manage_messages=True)
    async def purge_age(self, ctx, duration: str, amount: int = 100):
        """Apaga mensagens mais VELHAS que X tempo."""
        # Parseia "10m", "2h", "1d".
        match = re.match(r"(\d+)([mhd])", duration.lower())
        if not match:
            return await ctx.send(embed=embed_error(
                "❌ Use: `10m`, `2h`, `1d`"), delete_after=6)

        value = int(match.group(1))
        unit = match.group(2)
        seconds = {
            "m": value * 60,
            "h": value * 3600,
            "d": value * 86400,
        }[unit]

        cutoff = _now() - timedelta(seconds=seconds)
        amount = _clamp(amount)

        def check(m):
            return m.created_at < cutoff

        count = await _safe_purge(ctx.channel, amount, check=check)
        await ctx.send(embed=embed_success(
            f"🗑️ {count} mensagens com mais de `{duration}` apagadas."),
            delete_after=5)
        await self._send_purge_log(ctx, f"age:{duration}", count)

    # ============================================================
    # PURGE STATS
    # ============================================================

    @commands.command(name="purgestats", aliases=["purgehistory"])
    @commands.has_permissions(kick_members=True)
    async def purge_stats(self, ctx, limit: int = 10):
        """Mostra estatísticas de purges."""
        limit = max(1, min(limit, 25))
        # Usa Motor para consultar o histórico sem bloquear o event loop.
        db = await get_connection_async()
        # Materializa apenas a quantidade solicitada, mantendo a ordenação
        # mais recente primeiro e o limite original do comando.
        logs = await db["purge_logs"].find(
            {"guild_id": ctx.guild.id}
        ).sort("timestamp", -1).limit(limit).to_list(length=limit)

        if not logs:
            return await ctx.send(embed=embed_info(
                "📋 Nenhum purge registrado."))

        # Agrupa por método.
        by_method = {}
        total = 0
        for log in logs:
            m = log.get("method", "?")
            by_method[m] = by_method.get(m, 0) + log.get("count", 0)
            total += log.get("count", 0)

        embed = discord.Embed(
            title="📊 ESTATÍSTICAS DE PURGE",
            description=f"Últimos **{len(logs)}** purges | "
                        f"**{total}** mensagens apagadas",
            color=discord.Color.blurple(),
            timestamp=_now())

        if by_method:
            top = sorted(by_method.items(), key=lambda x: x[1], reverse=True)[:5]
            embed.add_field(
                name="📈 Top métodos",
                value="\n".join(f"• `{m}`: {c}" for m, c in top),
                inline=False)

        # Últimos 5.
        lines = []
        for log in logs[:5]:
            ts = log.get("timestamp", _now())
            ts_str = ts.strftime("%d/%m %H:%M") if hasattr(ts, "strftime") else str(ts)
            ch_id = log.get("channel_id")
            mod_id = log.get("moderator_id")
            lines.append(
                f"• <#{ch_id}> — `{log.get('method', '?')}` "
                f"({log.get('count', 0)}) por <@{mod_id}> — {ts_str}")
        if lines:
            embed.add_field(
                name="📋 Últimos",
                value="\n".join(lines),
                inline=False)

        await ctx.send(embed=embed)

    # ============================================================
    # NUKE (deletar + recriar canal)
    # ============================================================

    @commands.command(name="nuke", aliases=["cleanall"])
    @commands.has_permissions(administrator=True)
    async def nuke(self, ctx):
        """Deleta e recria o canal vazio (com confirmação)."""
        channel = ctx.channel

        await ctx.send(embed=embed_warning(
            "⚠️ **NUKE DO CANAL**\n"
            "O canal será **deletado e recriado** vazio.\n"
            "Digite `CONFIRMAR` em 15 segundos."))

        def check(m):
            return (m.author.id == ctx.author.id and
                    m.channel.id == channel.id and
                    m.content.upper() == "CONFIRMAR")

        try:
            await self.bot.wait_for("message", check=check, timeout=15)
        except asyncio.TimeoutError:
            return await ctx.send(embed=embed_info("Cancelado."),
                                   delete_after=5)

        # Salva os dados do canal original.
        position = channel.position
        category = channel.category
        topic = getattr(channel, "topic", None)
        nsfw = getattr(channel, "nsfw", False)
        slowmode = getattr(channel, "slowmode_delay", 0)
        name = channel.name

        try:
            # Clona o canal (mesmas permissões).
            new_channel = await channel.clone(
                reason=f"Nuke por {ctx.author}")
            # Deleta o original.
            await channel.delete(reason=f"Nuke por {ctx.author}")
            # Restaura posição, categoria, tópico, etc.
            await new_channel.edit(
                position=position,
                category=category,
                topic=topic,
                nsfw=nsfw,
                slowmode_delay=slowmode)

            # Avisa no novo canal.
            embed = discord.Embed(
                title="✅ Canal recriado",
                description=(
                    f"**Nome:** `{name}`\n"
                    f"**Por:** {ctx.author.mention}\n"
                    f"**ID novo:** `{new_channel.id}`"
                ),
                color=discord.Color.green(),
                timestamp=_now())
            await new_channel.send(embed=embed)

            # Loga.
            await _log_purge(ctx.guild.id, new_channel.id, ctx.author.id,
                             "nuke", 0, f"canal {name}")
            if BACKUP_LOG_WEBHOOK:
                await _WebhookLogger.send(
                    BACKUP_LOG_WEBHOOK,
                    discord.Embed(
                        title="💣 Canal nukado",
                        description=(
                            f"**Servidor:** {ctx.guild.name}\n"
                            f"**Canal:** `{name}`\n"
                            f"**Por:** {ctx.author.mention}"
                        ),
                        color=discord.Color.red(),
                        timestamp=_now()))
        except Exception as e:
            await ctx.send(embed=embed_error(f"❌ Erro: {e}"))

    # ============================================================
    # TEMPLOCK (trava temporária)
    # ============================================================

    @commands.command(name="templock")
    @commands.has_permissions(manage_channels=True)
    async def templock(self, ctx, duration: str,
                       channel: discord.TextChannel = None):
        """
        Tranca um canal temporariamente.
        Uso: >templock 10m #canal
        """
        # Parseia duração.
        match = re.match(r"(\d+)([smhd])", duration.lower())
        if not match:
            return await ctx.send(embed=embed_error(
                "❌ Use: `10s`, `5m`, `1h`, `1d`"))

        value = int(match.group(1))
        unit = match.group(2)
        seconds = {"s": value, "m": value * 60,
                   "h": value * 3600, "d": value * 86400}[unit]

        # Máximo 7 dias.
        if seconds > 86400 * 7:
            return await ctx.send(embed=embed_error("❌ Máximo: 7 dias."))

        # Canal atual (ou passado).
        channel = channel or ctx.channel
        # Pega as permissões atuais do @everyone.
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.send_messages = False

        # Aplica.
        try:
            await channel.set_permissions(
                ctx.guild.default_role, overwrite=ow,
                reason=f"Templock por {ctx.author}")
        except Exception as e:
            return await ctx.send(embed=embed_error(f"❌ {e}"))

        unlock_at = _now() + timedelta(seconds=seconds)
        embed = discord.Embed(
            title="🔒 CANAL TRANCADO",
            description=(
                f"**Canal:** {channel.mention}\n"
                f"**Duração:** `{duration}`\n"
                f"**Destranca:** <t:{int(unlock_at.timestamp())}:R>\n"
                f"**Por:** {ctx.author.mention}"
            ),
            color=discord.Color.orange(),
            timestamp=_now())
        await ctx.send(embed=embed)

        # ─── Agenda destrancamento automático. ───
        async def _auto_unlock():
            await asyncio.sleep(seconds)
            try:
                # Repega o overwrite atual.
                fresh_ow = channel.overwrites_for(ctx.guild.default_role)
                fresh_ow.send_messages = None  # None = herda padrão
                await channel.set_permissions(
                    ctx.guild.default_role, overwrite=fresh_ow,
                    reason="Templock expirado")
                # Avisa no canal.
                unlock_embed = discord.Embed(
                    title="🔓 CANAL DESTRANCADO",
                    description=f"{channel.mention} — templock expirado.",
                    color=discord.Color.green(),
                    timestamp=_now())
                try:
                    await channel.send(embed=unlock_embed)
                except Exception:
                    pass
            except Exception:
                pass

        asyncio.create_task(_auto_unlock())

        # Salva no banco (pra referência).
        try:
            # A configuração do templock é persistida de forma assíncrona.
            db = await get_connection_async()
            await db["templocks"].insert_one({
                "guild_id": ctx.guild.id,
                "channel_id": channel.id,
                "moderator_id": ctx.author.id,
                "duration_seconds": seconds,
                "unlock_at": unlock_at,
                "timestamp": _now(),
            })
        except Exception:
            pass

    # ============================================================
    # ROLEPERSIST (mantém cargos)
    # ============================================================
    # 📌 Faz um cargo persistir. Se o membro sair e voltar,
    # o cargo é reatribuído automaticamente.

    @commands.command(name="rolepersist")
    @commands.has_permissions(manage_roles=True)
    async def rolepersist(self, ctx, member: discord.Member = None,
                          role: discord.Role = None, action: str = "add"):
        """
        Mantém cargo persistente (reatribui se o membro sair/voltar).
        Uso: >rolepersist @user @cargo add/remove
             >rolepersist list
        """
        # Todas as operações de persistência de cargos usam a conexão Motor.
        db = await get_connection_async()

        # Sem member OU member == "list" → lista.
        if (member is None or (isinstance(member, str) and member.lower() == "list")):
            # Mantém o limite original de 30 cargos por consulta.
            docs = await db["role_persist"].find(
                {"guild_id": ctx.guild.id}).limit(30).to_list(length=30)
            embed = discord.Embed(
                title="📋 ROLE PERSIST",
                color=discord.Color.blurple(),
                timestamp=_now())
            if not docs:
                embed.description = "*Nenhum cargo persistido.*"
            else:
                for d in docs:
                    embed.add_field(
                        name=f"<@{d['user_id']}>",
                        value=f"<@&{d['role_id']}>",
                        inline=True)
            return await ctx.send(embed=embed)

        if member is None or role is None:
            return await ctx.send(embed=embed_error(
                "❌ Use: `>rolepersist @user @cargo add/remove` ou `>rolepersist list`"))

        # ─── Adicionar. ───
        if action.lower() in ("add", "addrole"):
            # Aguarda o upsert antes de confirmar ao usuário.
            await db["role_persist"].update_one(
                {"guild_id": ctx.guild.id,
                 "user_id": member.id,
                 "role_id": role.id},
                {"$set": {
                    "added_at": _now(),
                    "added_by": ctx.author.id,
                }},
                upsert=True)
            # Aplica o cargo se ainda não tem.
            try:
                if role not in member.roles:
                    await member.add_roles(role, reason="RolePersist")
            except Exception:
                pass
            await ctx.send(embed=embed_success(
                f"✅ {role.mention} será persistido em {member.mention}."))

        # ─── Remover. ───
        elif action.lower() in ("remove", "del"):
            # Aguarda a remoção para que deleted_count reflita o banco.
            result = await db["role_persist"].delete_one(
                {"guild_id": ctx.guild.id,
                 "user_id": member.id,
                 "role_id": role.id})
            if result.deleted_count > 0:
                await ctx.send(embed=embed_success(
                    f"✅ Persistência removida."))
            else:
                await ctx.send(embed=embed_warning(
                    f"⚠️ Cargo não estava persistido."))
        else:
            await ctx.send(embed=embed_error(
                "❌ Use: `add`, `remove` ou `list`"))

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        """Reatribui cargos persistidos quando alguém volta."""
        if member.bot:
            return
        try:
            # O listener também consulta o Mongo de forma assíncrona.
            db = await get_connection_async()
            # Busca todos os cargos persistidos desse user; sem limite novo,
            # preservando o comportamento anterior de reatribuir todos.
            docs = await db["role_persist"].find({
                "guild_id": member.guild.id,
                "user_id": member.id,
            }).to_list(length=None)
            for d in docs:
                role = member.guild.get_role(d["role_id"])
                if role and role not in member.roles:
                    try:
                        await member.add_roles(role,
                                                reason="RolePersist rejoin")
                    except Exception:
                        pass
        except Exception:
            pass


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("ModerationExtra") is None:
        await bot.add_cog(ModerationExtra(bot))