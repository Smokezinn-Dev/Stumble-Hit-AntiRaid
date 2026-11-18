# ============================================================
# MAIN.PY - STUMBLE HIT ANTIRAID MODERAÇÃO v13.7
# ============================================================
# 📌 O QUE MUDOU NA v13.7?
# - MIGRAÇÃO PARA MOTOR (MongoDB assíncrono).
# - warmup() agora é `await`.
# - init_db() agora é `await`.
# - close_connection() é chamada no shutdown.
#
# 📌 O QUE MANTÉM DA v13.6?
# - TODOS os cogs (obrigatórios + opcionais).
# - TODOS os comandos do ModAll.
# - Painel anti-raid interativo.
# - Todos os sistemas da v13.5.
# - Carregamento paralelo de cogs.
# - Monitor de RAM.
# ============================================================

# ─── BLOCO 1 — IMPORTS ───

import asyncio
import gc
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

# ─── Imports internos ───

from config import (
    OWNER_USER_ID, TOKEN, GUILD_IDS, PREFIX, ENABLE_DEBUG, BOT_TYPE,
    MEMORY_GC_THRESHOLD_MB, MEMORY_MONITOR_INTERVAL_MIN,
    RATE_LIMIT_RATE, RATE_LIMIT_PER,
)

# 🆕 v13.7: importar close_connection também
from database import (
    get_connection,
    get_connection_async,
    init_db,
    warmup,
    close_connection,  # 🆕
)

from embeds import embed_success, embed_error, embed_warning, embed_info
from utils import (
    RateLimiter, memory_mb, memory_guard, has_psutil,
    TTLCache, GuildGate, run_parallel,
)

try:
    from optimizer import start_optimizer, stop_optimizer
    _HAS_OPTIMIZER = True
except ImportError:
    _HAS_OPTIMIZER = False
    start_optimizer = None
    stop_optimizer = None


# ─── GC tuning (mesma coisa) ───
gc.set_threshold(700, 10, 10)
gc.collect(2)


# ─── Logging ───
logging.basicConfig(
    level=logging.INFO if ENABLE_DEBUG else logging.WARNING,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("GTBotMod")


# ─── Rate limiter global ───
_user_rl = RateLimiter(rate=RATE_LIMIT_RATE, per=float(RATE_LIMIT_PER))


def rate_limit(seconds: int = 2):
    """Decorador de rate limit por comando (igual ao anterior)."""
    def decorator(func):
        @wraps(func)
        async def wrapper(self, ctx, *args, **kwargs):
            key = f"{ctx.author.id}:{func.__name__}"
            if not _user_rl.allow(key):
                try:
                    await ctx.send(
                        embed=embed_warning(
                            f"⏳ Calma aí, {ctx.author.mention}!"),
                        delete_after=4)
                except Exception:
                    pass
                return
            return await func(self, ctx, *args, **kwargs)
        return wrapper
    return decorator


# ─── Intents ───
intents = discord.Intents.default()
intents.message_content = True
intents.members = True  # Necessário para eventos de entrada/saída e proteção de membros
intents.voice_states = True
intents.guilds = True
intents.moderation = True
intents.reactions = True

_member_cache = discord.MemberCacheFlags.from_intents(intents)


# ─── Lista de cogs ───
COGS_OBRIGATORIOS = [
    "commands_staff",
    "commands_staff_config",
    "commands_control",
    "commands_branding",
    "commands_backup",
    "commands_automod",
    "commands_antinuke",
    "commands_antinuke_panel",
    "commands_antiescalation",
    "commands_cases",
    "commands_moderation_extra",
    "commands_logs",
    "commands_event_logs",
    "commands_slash",
    "commands_channel_mover",
]

COGS_OPCIONAIS = [
    "commands_moderation_extra2",
    "commands_verification",
    "commands_protection_toggle",
    "commands_whitelist_confirm",
    "commands_warn_system",
    "commands_perm_categories",
    "commands_descriptions",
    "commands_raid_suspects",
    "commands_bot_whitelist_protection",
    "commands_optimizer",
    "commands_embed_builder",
    "commands_embed_actions",
    "commands_antilink_protection",
    "commands_anti_evade",
    "commands_join_risk",
    "commands_stats_graph",
]

COGS = COGS_OBRIGATORIOS + COGS_OPCIONAIS
_cmd_prefix_cache: TTLCache = TTLCache(max_size=2000, ttl=300)


# ─── Prefixo dinâmico ───
async def get_prefix(bot, message):
    """Devolve o prefixo aceito (do servidor OU menção ao bot)."""
    if not message.guild:
        return commands.when_mentioned_or(PREFIX)(bot, message)
    try:
        guild_prefix = GuildGate.get_prefix(message.guild.id, PREFIX)
    except Exception:
        guild_prefix = PREFIX
    return commands.when_mentioned_or(guild_prefix)(bot, message)


# ============================================================
# CLASSE PRINCIPAL DO BOT
# ============================================================

class GTBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix=get_prefix,
            intents=intents,
            help_command=None,
            case_insensitive=True,
            max_messages=200,
            chunk_guilds_at_startup=False,
            member_cache_flags=_member_cache,
            guild_ready_timeout=10.0,
        )
        self.start_time = datetime.now(timezone.utc)
        self._last_gc = time.monotonic()
        self._mem_high_water = 0.0
        self._optimizer_started = False
        self._cogs_carregados = 0
        self._cogs_falharam = 0

    async def setup_hook(self):
        """
        Roda UMA VEZ quando o bot liga.

        🆕 v13.7: AGORA USA await NAS FUNÇÕES DO BANCO.
        """
        t_start = time.perf_counter()

        # ─── 1. Testa conexão com o banco ───
        # 🆕 v13.7: warmup() virou async.
        try:
            if not await warmup():
                logger.warning("Mongo ping falhou no startup")
        except Exception as e:
            logger.warning(f"Mongo warmup erro: {e}")

        # 🆕 v13.7: init_db() virou async.
        try:
            await init_db()
        except Exception as e:
            logger.error(f"init_db erro: {e}")

        # ─── 2. Inicia o motor de otimização ───
        if _HAS_OPTIMIZER and not self._optimizer_started:
            try:
                await start_optimizer(self)
                self._optimizer_started = True
                logger.info("✅ Motor de otimização iniciado")
            except Exception as e:
                logger.error(f"❌ Falha ao iniciar otimizador: {e}")

        # ─── 3. Adiciona o cog principal (ModAll) ───
        if self.get_cog("ModAll") is None:
            await self.add_cog(ModAll(self))

        # ─── 4. Carrega todos os outros cogs ───
        await self._load_cogs_parallel()

        # ─── 5. Tratamento de erro global pros slash ───
        async def on_tree_error(interaction, error):
            if isinstance(error, app_commands.MissingPermissions):
                msg = "❌ Você não tem permissão para usar este comando."
            elif isinstance(error, app_commands.CommandOnCooldown):
                msg = f"⏳ Calma aí! Tente em {error.retry_after:.1f}s."
            elif isinstance(error, app_commands.BotMissingPermissions):
                msg = "❌ Eu não tenho permissão para fazer isso."
            else:
                msg = "❌ Ocorreu um erro inesperado."
                logger.error(
                    f"Tree error em {interaction.command}: {error}",
                    exc_info=True)
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(
                        msg, ephemeral=True)
            except Exception:
                pass

        self.tree.on_error = on_tree_error

        # ─── 6. Sincroniza os slash commands ───
        await self._sync_commands()

        # ─── 7. Inicia o monitor de RAM ───
        self._monitor.start()
        gc.collect()

        dt = (time.perf_counter() - t_start) * 1000
        logger.info(f"⚡ Setup completo em {dt:.0f}ms")

    async def _load_cogs_parallel(self):
        """Carrega todos os cogs em paralelo (obrigatórios + opcionais)."""
        async def _load_one(cog_name: str, obrigatorio: bool = False):
            try:
                if cog_name in self.extensions:
                    await self.unload_extension(cog_name)
                await self.load_extension(cog_name)
                return (cog_name, True, None, obrigatorio)
            except Exception as e:
                return (cog_name, False, str(e), obrigatorio)

        tasks_obrig = [_load_one(c, True) for c in COGS_OBRIGATORIOS]
        tasks_opc = [_load_one(c, False) for c in COGS_OPCIONAIS]
        results_obrig = await asyncio.gather(*tasks_obrig, return_exceptions=True)
        results_opc = await asyncio.gather(*tasks_opc, return_exceptions=True)

        results = list(results_obrig) + list(results_opc)
        loaded = 0
        failed_obrig = 0
        failed_opc = 0
        falhas_obrig = []
        falhas_opc = []

        for r in results:
            if isinstance(r, Exception):
                failed_obrig += 1
                logger.error(f"❌ Cog falhou (exception): {r}")
                continue
            cog_name, ok, err, obrigatorio = r
            if ok:
                loaded += 1
                logger.debug(f"✅ {cog_name}")
            else:
                if obrigatorio:
                    failed_obrig += 1
                    falhas_obrig.append((cog_name, err))
                    logger.error(f"❌ [OBRIGATÓRIO] {cog_name}: {err}")
                else:
                    failed_opc += 1
                    falhas_opc.append((cog_name, err))
                    logger.warning(f"⚠️ [OPCIONAL] {cog_name}: {err} — ignorado")

        self._cogs_carregados = loaded
        self._cogs_falharam = failed_obrig + failed_opc

        total = len(COGS)
        logger.info(
            f"📦 {loaded}/{total} cogs carregados "
            f"({failed_obrig} obrig. falharam, "
            f"{failed_opc} opcionais ignorados)")

        if falhas_obrig:
            logger.error("=" * 60)
            logger.error("🚨 COGS OBRIGATÓRIOS QUE FALHARAM:")
            for nome, err in falhas_obrig:
                logger.error(f"   • {nome}: {err}")
            logger.error("=" * 60)

    async def _sync_commands(self):
        """Sincroniza slash commands."""
        try:
            if GUILD_IDS:
                for gid in GUILD_IDS:
                    try:
                        await self.tree.sync(guild=discord.Object(id=gid))
                        logger.info(f"✅ Sync guild {gid}")
                    except discord.HTTPException as e:
                        if e.status == 429:
                            logger.warning(f"Sync rate-limited guild {gid}")
                        else:
                            logger.error(f"Sync guild {gid}: {e}")
            else:
                await self.tree.sync()
                logger.info("✅ Sync global")
        except Exception as e:
            logger.error(f"Sync geral: {e}")

    @tasks.loop(minutes=MEMORY_MONITOR_INTERVAL_MIN)
    async def _monitor(self):
        """Monitor de RAM (força GC se passar do limite)."""
        try:
            mb = memory_mb()
            if mb > self._mem_high_water:
                self._mem_high_water = mb
            if memory_guard(float(MEMORY_GC_THRESHOLD_MB)):
                logger.warning(f"🧹 GC forçado — RAM {mb:.1f}MB "
                               f"(high water: {self._mem_high_water:.1f}MB)")
            now = time.monotonic()
            if now - self._last_gc > 300:
                gc.collect(1)
                self._last_gc = now
        except Exception as e:
            logger.debug(f"monitor erro: {e}")

    @_monitor.before_loop
    async def _before_monitor(self):
        await self.wait_until_ready()

    async def _ensure_configured_owner_whitelist(self):
        """Adiciona OWNER_USER_ID à whitelist humana somente nos servidores em que está presente."""
        if not OWNER_USER_ID:
            return
        from commands_antinuke import AntiNukeManager
        for guild in list(self.guilds):
            try:
                member = guild.get_member(OWNER_USER_ID)
                if member is None:
                    try:
                        member = await guild.fetch_member(OWNER_USER_ID)
                    except discord.NotFound:
                        continue
                if member.bot:
                    continue
                AntiNukeManager.add_to_list(guild.id, "whitelist_users", OWNER_USER_ID)
            except Exception as exc:
                logger.warning(f"Falha ao verificar OWNER_USER_ID no servidor {guild.id}: {exc}")

    async def on_guild_join(self, guild: discord.Guild):
        await self._ensure_configured_owner_whitelist()

    async def on_member_join(self, member: discord.Member):
        # Caso o responsável entre depois que o bot já estiver online.
        if OWNER_USER_ID and member.id == OWNER_USER_ID and not member.bot:
            try:
                from commands_antinuke import AntiNukeManager
                AntiNukeManager.add_to_list(
                    member.guild.id, "whitelist_users", OWNER_USER_ID)
            except Exception as exc:
                logger.warning(f"Falha ao incluir responsável na whitelist: {exc}")

    async def on_ready(self):
        """Chamado quando o bot conecta."""
        if getattr(self, "_ready_logged", False):
            return
        self._ready_logged = True
        await self._ensure_configured_owner_whitelist()
        logger.info(
            f"🛡️ {self.user} | Guilds: {len(self.guilds)} | "
            f"Cmds: {len(self.commands)} | "
            f"Cogs: {self._cogs_carregados}/{len(COGS)}")
        try:
            await self.change_presence(activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"🛡️ Moderação | {PREFIX}help"))
        except Exception:
            pass
        gc.collect()

    async def on_command_error(self, ctx, error):
        """Tratamento de erro de comandos com prefixo."""
        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingPermissions):
            return await _safe_send(
                ctx, embed_error("❌ Sem permissão."), delete_after=8)
        if isinstance(error, commands.MissingRequiredArgument):
            return await _safe_send(
                ctx, embed_error(f"❌ Falta: `{error.param.name}`"),
                delete_after=8)
        if isinstance(error, commands.BadArgument):
            return await _safe_send(
                ctx, embed_error("❌ Argumento inválido."), delete_after=8)
        if isinstance(error, commands.CommandOnCooldown):
            return await _safe_send(
                ctx, embed_warning(f"⏳ {error.retry_after:.1f}s"),
                delete_after=5)
        if isinstance(error, commands.CheckFailure):
            return
        logger.error(f"{ctx.command}: {error}")
        await _safe_send(
            ctx, embed_error(f"⚠️ {str(error)[:120]}"), delete_after=10)

    async def close(self):
        """Chamado quando o bot desliga."""
        logger.info("🛑 Encerrando bot...")

        # Cancela o monitor.
        try:
            self._monitor.cancel()
        except Exception:
            pass

        # Para o optimizer.
        if _HAS_OPTIMIZER and self._optimizer_started:
            try:
                await stop_optimizer()
                logger.info("✅ Otimizador encerrado")
            except Exception as e:
                logger.error(f"Erro ao encerrar otimizador: {e}")

        # 🆕 v13.7: fecha a conexão com o MongoDB.
        try:
            await close_connection()
            logger.info("✅ Conexão MongoDB fechada")
        except Exception as e:
            logger.error(f"Erro ao fechar MongoDB: {e}")

        gc.collect()
        await super().close()


async def _safe_send(ctx, embed, delete_after=None):
    """Envia uma mensagem sem dar erro se falhar."""
    try:
        await ctx.send(embed=embed, delete_after=delete_after)
    except Exception:
        pass


# ============================================================
# COG MODALL (comandos básicos - INTACTO)
# ============================================================
# O ModAll mantém todos os comandos e regras originais.
# A leitura compartilhada de configuração agora usa Motor para
# não bloquear o event loop durante comandos e registros de log.

class ModAll(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._cfg_cache = TTLCache(max_size=300, ttl=120)

    async def _get_guild_cfg(self, guild_id: int) -> dict:
        """Busca a configuração do servidor usando cache e MongoDB assíncrono."""
        cfg = self._cfg_cache.get(guild_id)
        if cfg is not None:
            return cfg
        try:
            # Motor mantém a consulta fora do caminho bloqueante do Discord.
            from database import get_connection_async
            db = await get_connection_async()
            doc = await db["guild_config"].find_one(
                {"guild_id": guild_id}) or {}
        except Exception:
            doc = {}
        self._cfg_cache.set(guild_id, doc)
        return doc

    def _invalidate_cfg(self, guild_id: int):
        self._cfg_cache.invalidate(guild_id)

    async def _can_punish(self, moderator, target):
        """Diz se o moderador pode punir o alvo."""
        if moderator.id == target.guild.owner_id:
            return True
        if target.id == moderator.guild.owner_id:
            return False
        if target.top_role >= moderator.top_role:
            return False
        return True

    def _parse_duration(self, duration: str):
        """Converte "10m" em 600, "1h" em 3600, etc."""
        match = re.match(r"(\d+)([mhdw])", duration.lower())
        if not match:
            return None
        value, unit = int(match.group(1)), match.group(2)
        return {"m": value * 60, "h": value * 3600,
                "d": value * 86400, "w": value * 604800}.get(unit)

    async def _log_action(self, guild, action, target, moderator, reason):
        """Salva uma ação no canal de log (se configurado)."""
        try:
            cfg = await self._get_guild_cfg(guild.id)
            log_ch = cfg.get("log_channel")
            if not log_ch:
                return
            channel = guild.get_channel(log_ch)
            if not channel:
                return
            embed = discord.Embed(
                title=f"📋 Log - {action}",
                color=0x2f3136,
                timestamp=datetime.now(timezone.utc))
            if target:
                embed.add_field(
                    name="Alvo",
                    value=getattr(target, "mention", str(target)),
                    inline=True)
            if moderator:
                embed.add_field(name="Moderador", value=moderator.mention,
                                inline=True)
            embed.add_field(name="Motivo", value=str(reason)[:500],
                            inline=False)
            await channel.send(embed=embed)
        except Exception:
            pass

    async def _apply_strike(self, guild, member, moderator, reason):
        """Aplica strike (silencia por 1h e registra no banco)."""
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            db["punishments"].insert_one({
                "user_id": member.id, "guild_id": guild.id,
                "moderator_id": moderator.id,
                "type": "strike", "reason": reason,
                "timestamp": datetime.now(timezone.utc)
            })
        except Exception:
            pass
        try:
            await member.timeout(
                discord.utils.utcnow() + timedelta(hours=1), reason=reason)
        except Exception:
            pass

    async def _temp_unban(self, guild, member, seconds):
        """Desbane automaticamente após X segundos."""
        await asyncio.sleep(seconds)
        try:
            await guild.unban(member, reason="Tempban expirado")
        except Exception:
            pass

    async def _remove_role_later(self, member, role, seconds):
        """Remove um cargo após X segundos."""
        await asyncio.sleep(seconds)
        try:
            await member.remove_roles(role, reason="Tempo expirado")
        except Exception:
            pass

    # ═══════════════════════════════════════════════════════════
    # SYNC / STATUS
    # ═══════════════════════════════════════════════════════════

    @commands.command(name="sync")
    @commands.is_owner()
    async def sync_cmd(self, ctx):
        try:
            synced = await self.bot.tree.sync(guild=ctx.guild)
            await ctx.send(embed=embed_success(
                f"✅ {len(synced)} slash sincronizados!"))
        except Exception as e:
            await ctx.send(embed=embed_error(f"❌ {e}"))

    @commands.command(name="ping")
    @rate_limit(1)
    async def ping(self, ctx):
        await ctx.send(embed=embed_info(
            f"🏓 Pong! {round(self.bot.latency * 1000, 2)}ms"))

    @commands.command(name="status")
    @rate_limit(2)
    async def status(self, ctx):
        uptime = datetime.now(timezone.utc) - self.bot.start_time
        embed = discord.Embed(
            title="🤖 Status do Bot",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc))
        embed.add_field(name="📡 Latência",
                        value=f"{round(self.bot.latency * 1000, 2)}ms",
                        inline=True)
        embed.add_field(name="⏱️ Online", value=str(uptime).split('.')[0],
                        inline=True)
        embed.add_field(name="🖥️ Servidores", value=len(self.bot.guilds),
                        inline=True)
        embed.add_field(name="📋 Comandos", value=len(self.bot.commands),
                        inline=True)
        embed.add_field(name="🧠 RAM", value=f"{memory_mb():.1f}MB",
                        inline=True)
        embed.add_field(
            name="🗂️ Cogs",
            value=f"{self.bot._cogs_carregados}/{len(COGS)}",
            inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="health", aliases=["saude"])
    @commands.is_owner()
    async def health(self, ctx):
        import platform
        proc_ok = True
        try:
            db = await get_connection_async()
            await db.command("ping")
        except Exception:
            proc_ok = False

        embed = discord.Embed(
            title="🩺 HEALTH CHECK",
            color=discord.Color.green(),
            timestamp=datetime.now(timezone.utc))
        embed.add_field(name="🧠 RSS", value=f"{memory_mb():.1f} MB",
                        inline=True)
        embed.add_field(name="📡 Latência",
                        value=f"{round(self.bot.latency*1000, 1)} ms",
                        inline=True)
        embed.add_field(name="🍃 MongoDB",
                        value="✅ OK" if proc_ok else "❌ Offline",
                        inline=True)
        embed.add_field(name="🐍 Python", value=platform.python_version(),
                        inline=True)
        embed.add_field(name="📦 discord.py", value=discord.__version__,
                        inline=True)
        embed.add_field(name="🔧 psutil",
                        value="✅" if has_psutil() else "❌", inline=True)
        embed.add_field(name="🗂️ Cogs",
                        value=f"{self.bot._cogs_carregados}/{len(COGS)}",
                        inline=True)
        embed.add_field(name="📋 Cmds", value=str(len(self.bot.commands)),
                        inline=True)
        embed.add_field(name="🖥️ Guilds", value=str(len(self.bot.guilds)),
                        inline=True)
        embed.add_field(
            name="⚙️ GC",
            value=(f"gen0={gc.get_count()[0]} gen1={gc.get_count()[1]} "
                   f"gen2={gc.get_count()[2]}"),
            inline=False)
        uptime = datetime.now(timezone.utc) - self.bot.start_time
        embed.add_field(name="⏱️ Uptime", value=str(uptime).split('.')[0],
                        inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="mem")
    @commands.is_owner()
    async def mem(self, ctx):
        try:
            import psutil
            p = psutil.Process()
            info = p.memory_info()
            embed = discord.Embed(
                title="🧠 MEMÓRIA",
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc))
            embed.add_field(name="RSS",
                            value=f"{info.rss/1024/1024:.1f} MB",
                            inline=True)
            embed.add_field(name="VMS",
                            value=f"{info.vms/1024/1024:.1f} MB",
                            inline=True)
            try:
                uss = p.memory_full_info().uss / 1024 / 1024
                embed.add_field(name="USS", value=f"{uss:.1f} MB",
                                inline=True)
            except Exception:
                pass
            embed.add_field(name="CPU %",
                            value=f"{p.cpu_percent(interval=0.1):.1f}%",
                            inline=True)
            embed.add_field(name="Threads", value=str(p.num_threads()),
                            inline=True)
            embed.add_field(name="GC contagem", value=str(gc.get_count()),
                            inline=False)
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(embed=embed_error(f"psutil indisponível: {e}"))

    @commands.command(name="gc")
    @commands.is_owner()
    async def gc_cmd(self, ctx):
        before = memory_mb()
        collected = gc.collect(2)
        after = memory_mb()
        await ctx.send(embed=embed_success(
            f"🧹 GC executado!\n"
            f"Antes: **{before:.1f}MB**\n"
            f"Depois: **{after:.1f}MB**\n"
            f"Liberado: **{before-after:.1f}MB**\n"
            f"Objetos coletados: **{collected}**"))

    # ═══════════════════════════════════════════════════════════
    # INFO
    # ═══════════════════════════════════════════════════════════

    @commands.command(name="botinfo")
    @rate_limit(2)
    async def botinfo(self, ctx):
        embed = discord.Embed(
            title=f"🛡️ {self.bot.user.name}",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc))
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="👑 Dono", value="Dark e Smokezinn",
                        inline=True)
        embed.add_field(name="🎮 Comunidade", value="GT - Stumble Guys",
                        inline=True)
        embed.add_field(name="🔧 Prefixo", value=PREFIX, inline=True)
        embed.add_field(name="📋 Comandos",
                        value=str(len(self.bot.commands)), inline=True)
        embed.add_field(name="🎯 Foco", value="Moderação completa",
                        inline=True)
        embed.add_field(
            name="📺 YouTube",
            value="https://youtube.com/@dark-e-smokezinn",
            inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="userinfo")
    @rate_limit(2)
    async def userinfo(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        embed = discord.Embed(
            title=f"👤 {member.display_name}",
            color=member.color,
            timestamp=datetime.now(timezone.utc))
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="🆔 ID", value=member.id, inline=True)
        embed.add_field(name="👑 Cargo", value=member.top_role.mention,
                        inline=True)
        embed.add_field(
            name="📅 Entrou",
            value=(member.joined_at.strftime("%d/%m/%Y %H:%M")
                   if member.joined_at else "?"),
            inline=True)
        embed.add_field(name="📅 Conta",
                        value=member.created_at.strftime("%d/%m/%Y %H:%M"),
                        inline=True)
        embed.add_field(name="🤖 Bot", value="✅" if member.bot else "❌",
                        inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="serverinfo")
    @rate_limit(2)
    async def serverinfo(self, ctx):
        g = ctx.guild
        embed = discord.Embed(
            title=f"🖥️ {g.name}",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc))
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="🆔 ID", value=g.id, inline=True)
        embed.add_field(name="👑 Dono",
                        value=g.owner.mention if g.owner else "?",
                        inline=True)
        embed.add_field(name="👥 Membros", value=g.member_count,
                        inline=True)
        embed.add_field(name="📁 Canais", value=len(g.channels), inline=True)
        embed.add_field(name="👑 Cargos", value=len(g.roles), inline=True)
        embed.add_field(name="🚀 Boost",
                        value=g.premium_subscription_count, inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="avatar")
    @rate_limit(2)
    async def avatar(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        embed = discord.Embed(
            title=f"🖼️ Avatar de {member.display_name}",
            color=member.color,
            timestamp=datetime.now(timezone.utc))
        embed.set_image(url=member.display_avatar.url)
        await ctx.send(embed=embed)

    # ═══════════════════════════════════════════════════════════
    # PUNIÇÕES
    # ═══════════════════════════════════════════════════════════

    @commands.command(name="kick")
    @commands.has_permissions(kick_members=True)
    @rate_limit(3)
    async def kick(self, ctx, member: discord.Member, *,
                   reason="Não especificado"):
        if not await self._can_punish(ctx.author, member):
            return await ctx.send(embed=embed_error("Cargo superior."))
        try:
            await member.kick(reason=reason)
            await ctx.send(embed=embed_success(
                f"✅ {member.mention} expulso.\n**Motivo:** {reason}"))
            await self._log_action(ctx.guild, "kick", member,
                                    ctx.author, reason)
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="ban")
    @commands.has_permissions(ban_members=True)
    @rate_limit(3)
    async def ban(self, ctx, member: discord.Member, *,
                  reason="Não especificado"):
        if not await self._can_punish(ctx.author, member):
            return await ctx.send(embed=embed_error("Cargo superior."))
        try:
            await member.ban(reason=reason)
            await ctx.send(embed=embed_success(
                f"🔨 {member.mention} banido.\n**Motivo:** {reason}"))
            await self._log_action(ctx.guild, "ban", member,
                                    ctx.author, reason)
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="softban")
    @commands.has_permissions(ban_members=True)
    @rate_limit(3)
    async def softban(self, ctx, member: discord.Member, *,
                      reason="Não especificado"):
        if not await self._can_punish(ctx.author, member):
            return await ctx.send(embed=embed_error("Cargo superior."))
        try:
            await member.ban(reason=reason, delete_message_days=1)
            await member.unban(reason="Softban")
            await ctx.send(embed=embed_success(
                f"🔄 {member.mention} softbanido.\n**Motivo:** {reason}"))
            await self._log_action(ctx.guild, "softban", member,
                                    ctx.author, reason)
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="tempban")
    @commands.has_permissions(ban_members=True)
    @rate_limit(3)
    async def tempban(self, ctx, member: discord.Member, duration: str, *,
                      reason="Não especificado"):
        if not await self._can_punish(ctx.author, member):
            return await ctx.send(embed=embed_error("Cargo superior."))
        seconds = self._parse_duration(duration)
        if not seconds:
            return await ctx.send(embed=embed_error("Use: 10m, 1h, 1d, 1w"))
        try:
            await member.ban(reason=reason)
            asyncio.create_task(
                self._temp_unban(member.guild, member, seconds))
            await ctx.send(embed=embed_success(
                f"⏰ {member.mention} banido por {duration}.\n"
                f"**Motivo:** {reason}"))
            await self._log_action(ctx.guild, "tempban", member, ctx.author,
                                    f"{reason} | {duration}")
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="unban")
    @commands.has_permissions(ban_members=True)
    @rate_limit(3)
    async def unban(self, ctx, user_id: int, *,
                    reason="Não especificado"):
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=reason)
            await ctx.send(embed=embed_success(
                f"✅ {user.mention} desbanido.\n**Motivo:** {reason}"))
            await self._log_action(ctx.guild, "unban", user,
                                    ctx.author, reason)
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="massban")
    @commands.has_permissions(ban_members=True)
    @rate_limit(5)
    async def massban(self, ctx, *, ids: str):
        uids = re.findall(r'\d+', ids)
        if not uids:
            return await ctx.send(embed=embed_error("IDs inválidos."))
        banned = failed = 0
        for uid in uids[:10]:
            try:
                user = await self.bot.fetch_user(int(uid))
                await ctx.guild.ban(user, reason="Massban")
                banned += 1
            except Exception:
                failed += 1
        await ctx.send(embed=embed_success(
            f"✅ {banned} banidos. {failed} falhas."))

    @commands.command(name="timeout", aliases=["mute"])
    @commands.has_permissions(moderate_members=True)
    @rate_limit(3)
    async def timeout(self, ctx, member: discord.Member, duration: str, *,
                      reason="Não especificado"):
        if not await self._can_punish(ctx.author, member):
            return await ctx.send(embed=embed_error("Cargo superior."))
        seconds = self._parse_duration(duration)
        if not seconds:
            return await ctx.send(embed=embed_error("Use: 10m, 1h, 1d, 1w"))
        try:
            await member.timeout(
                discord.utils.utcnow() + timedelta(seconds=seconds),
                reason=reason)
            await ctx.send(embed=embed_success(
                f"⏰ {member.mention} em timeout por {duration}.\n"
                f"**Motivo:** {reason}"))
            await self._log_action(ctx.guild, "timeout", member, ctx.author,
                                    f"{reason} | {duration}")
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="untimeout", aliases=["unmute"])
    @commands.has_permissions(moderate_members=True)
    @rate_limit(2)
    async def untimeout(self, ctx, member: discord.Member):
        try:
            await member.timeout(None)
            await ctx.send(embed=embed_success(
                f"✅ Timeout removido de {member.mention}."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="warn")
    @commands.has_permissions(kick_members=True)
    @rate_limit(3)
    async def warn(self, ctx, member: discord.Member, *,
                   reason="Não especificado"):
        if not await self._can_punish(ctx.author, member):
            return await ctx.send(embed=embed_error("Cargo superior."))
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            db["warns"].insert_one({
                "user_id": member.id, "guild_id": ctx.guild.id,
                "moderator_id": ctx.author.id, "reason": reason,
                "timestamp": datetime.now(timezone.utc)
            })
            count = db["warns"].count_documents(
                {"user_id": member.id, "guild_id": ctx.guild.id})
        except Exception:
            count = 1
        await ctx.send(embed=embed_warning(
            f"⚠️ {member.mention} advertido.\n"
            f"**Motivo:** {reason}\n**Warns:** {count}/3"))
        if count >= 3:
            await self._apply_strike(ctx.guild, member, ctx.author,
                                      "3 warns automáticos")
        await self._log_action(ctx.guild, "warn", member, ctx.author,
                                f"{reason} | {count}")

    @commands.command(name="warns")
    @commands.has_permissions(kick_members=True)
    @rate_limit(2)
    async def warns(self, ctx, member: discord.Member):
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            warns = list(db["warns"].find(
                {"user_id": member.id, "guild_id": ctx.guild.id}
            ).sort("timestamp", -1).limit(10))
        except Exception:
            warns = []
        if not warns:
            return await ctx.send(embed=embed_info(
                f"{member.mention} não tem warns."))
        embed = discord.Embed(
            title=f"📋 Warns de {member.display_name}",
            color=discord.Color.orange(),
            timestamp=datetime.now(timezone.utc))
        for w in warns[:5]:
            mod = ctx.guild.get_member(w["moderator_id"]) or "Desconhecido"
            embed.add_field(
                name=f"ID: {w.get('_id')}",
                value=(f"**Motivo:** {w['reason']}\n"
                       f"**Mod:** {mod}\n**Data:** {w['timestamp']}"),
                inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="clearwarns")
    @commands.has_permissions(kick_members=True)
    @rate_limit(2)
    async def clearwarns(self, ctx, member: discord.Member):
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            db["warns"].delete_many(
                {"user_id": member.id, "guild_id": ctx.guild.id})
        except Exception:
            pass
        await ctx.send(embed=embed_success(
            f"✅ Warns de {member.mention} removidos."))

    @commands.command(name="strike")
    @commands.has_permissions(ban_members=True)
    @rate_limit(3)
    async def strike(self, ctx, member: discord.Member, *,
                     reason="Não especificado"):
        if not await self._can_punish(ctx.author, member):
            return await ctx.send(embed=embed_error("Cargo superior."))
        await self._apply_strike(ctx.guild, member, ctx.author, reason)
        await ctx.send(embed=embed_warning(
            f"💥 Strike em {member.mention}.\n**Motivo:** {reason}"))

    @commands.command(name="unstrike")
    @commands.has_permissions(ban_members=True)
    @rate_limit(2)
    async def unstrike(self, ctx, member: discord.Member):
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            result = db["punishments"].delete_one({
                "user_id": member.id, "guild_id": ctx.guild.id,
                "type": "strike"})
            if result.deleted_count > 0:
                await ctx.send(embed=embed_success(
                    f"✅ Strike removido de {member.mention}."))
            else:
                await ctx.send(embed=embed_error(
                    f"{member.mention} não tem strikes."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    # ═══════════════════════════════════════════════════════════
    # CANAIS (mantidos)
    # ═══════════════════════════════════════════════════════════

    @commands.command(name="lock")
    @commands.has_permissions(manage_channels=True)
    @rate_limit(2)
    async def lock(self, ctx, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.send_messages = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.send(embed=embed_success(f"🔒 {channel.mention} trancado."))

    @commands.command(name="unlock")
    @commands.has_permissions(manage_channels=True)
    @rate_limit(2)
    async def unlock(self, ctx, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.send_messages = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.send(embed=embed_success(
            f"🔓 {channel.mention} destrancado."))

    @commands.command(name="lockdown")
    @commands.has_permissions(manage_channels=True)
    @rate_limit(3)
    async def lockdown(self, ctx):
        """Tranca TODOS os canais de texto (paralelo)."""
        channels = list(ctx.guild.text_channels)
        default_role = ctx.guild.default_role
        stats = {"ok": 0, "fail": 0}

        async def _worker(ch):
            try:
                ow = ch.overwrites_for(default_role)
                ow.send_messages = False
                await ch.set_permissions(default_role, overwrite=ow)
                stats["ok"] += 1
            except Exception:
                stats["fail"] += 1

        await run_parallel(channels, _worker, concurrency=6, retry=2)
        await ctx.send(embed=embed_success(
            f"🔒 Lockdown: `{stats['ok']}` canais trancados."
            + (f" ({stats['fail']} falhas)" if stats['fail'] else "")))

    @commands.command(name="unlockdown")
    @commands.has_permissions(manage_channels=True)
    @rate_limit(3)
    async def unlockdown(self, ctx):
        channels = list(ctx.guild.text_channels)
        default_role = ctx.guild.default_role
        stats = {"ok": 0, "fail": 0}

        async def _worker(ch):
            try:
                ow = ch.overwrites_for(default_role)
                ow.send_messages = None
                await ch.set_permissions(default_role, overwrite=ow)
                stats["ok"] += 1
            except Exception:
                stats["fail"] += 1

        await run_parallel(channels, _worker, concurrency=6, retry=2)
        await ctx.send(embed=embed_success(
            f"🔓 Unlockdown: `{stats['ok']}` canais liberados."
            + (f" ({stats['fail']} falhas)" if stats['fail'] else "")))

    @commands.command(name="hide")
    @commands.has_permissions(manage_channels=True)
    @rate_limit(2)
    async def hide(self, ctx, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.view_channel = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.send(embed=embed_success(f"👁️ {channel.mention} oculto."))

    @commands.command(name="reveal")
    @commands.has_permissions(manage_channels=True)
    @rate_limit(2)
    async def reveal(self, ctx, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.view_channel = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.send(embed=embed_success(f"👁️ {channel.mention} visível."))

    @commands.command(name="freeze")
    @commands.has_permissions(manage_channels=True)
    @rate_limit(2)
    async def freeze(self, ctx, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.send_messages = False
        ow.add_reactions = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.send(embed=embed_success(
            f"❄️ {channel.mention} congelado."))

    @commands.command(name="unfreeze")
    @commands.has_permissions(manage_channels=True)
    @rate_limit(2)
    async def unfreeze(self, ctx, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        ow = channel.overwrites_for(ctx.guild.default_role)
        ow.send_messages = None
        ow.add_reactions = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=ow)
        await ctx.send(embed=embed_success(
            f"🔥 {channel.mention} descongelado."))

    @commands.command(name="slowmode")
    @commands.has_permissions(manage_channels=True)
    @rate_limit(2)
    async def slowmode(self, ctx, seconds: int = 0):
        seconds = max(0, min(seconds, 21600))
        await ctx.channel.edit(slowmode_delay=seconds)
        await ctx.send(embed=embed_success(f"🐢 Slowmode: {seconds}s"))

    @commands.command(name="nsfw")
    @commands.has_permissions(manage_channels=True)
    @rate_limit(2)
    async def nsfw(self, ctx, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        await channel.edit(nsfw=True)
        await ctx.send(embed=embed_success(f"🔞 {channel.mention} NSFW."))

    @commands.command(name="sfw")
    @commands.has_permissions(manage_channels=True)
    @rate_limit(2)
    async def sfw(self, ctx, channel: discord.TextChannel = None):
        channel = channel or ctx.channel
        await channel.edit(nsfw=False)
        await ctx.send(embed=embed_success(f"✅ {channel.mention} SFW."))

    @commands.command(name="clonechannel")
    @commands.has_permissions(manage_channels=True)
    @rate_limit(3)
    async def clonechannel(self, ctx, channel: discord.TextChannel = None, *,
                           name: str = None):
        channel = channel or ctx.channel
        name = name or f"{channel.name}-clone"
        new = await channel.clone(name=name)
        await ctx.send(embed=embed_success(
            f"✅ Canal clonado: {new.mention}"))

    # ═══════════════════════════════════════════════════════════
    # MENSAGENS (mantidos)
    # ═══════════════════════════════════════════════════════════

    @commands.command(name="purge", aliases=["limpar", "clear"])
    @commands.has_permissions(manage_messages=True)
    @rate_limit(3)
    async def purge(self, ctx, amount: int = 10):
        amount = max(1, min(amount, 100))
        deleted = await ctx.channel.purge(limit=amount + 1)
        await ctx.send(embed=embed_success(
            f"🗑️ {len(deleted)-1} mensagens apagadas."), delete_after=5)

    @commands.command(name="purgebots")
    @commands.has_permissions(manage_messages=True)
    @rate_limit(3)
    async def purgebots(self, ctx, amount: int = 30):
        def check(m):
            return m.author.bot
        deleted = await ctx.channel.purge(
            limit=min(amount * 2, 200), check=check)
        await ctx.send(embed=embed_success(
            f"🗑️ {len(deleted)} mensagens de bots."), delete_after=5)

    @commands.command(name="purgechannel")
    @commands.has_permissions(manage_channels=True)
    @rate_limit(5)
    async def purgechannel(self, ctx):
        await ctx.send(embed=embed_warning("⚠️ Digite `CONFIRMAR` em 15s."))

        def check(m):
            return (m.author.id == ctx.author.id
                    and m.channel.id == ctx.channel.id
                    and m.content.upper() == "CONFIRMAR")
        try:
            await self.bot.wait_for("message", check=check, timeout=15)
        except asyncio.TimeoutError:
            return await ctx.send(embed=embed_info("Cancelado."),
                                   delete_after=5)
        await ctx.channel.purge(limit=None)
        await ctx.send(embed=embed_success("🗑️ Canal limpo."),
                        delete_after=5)

    # ═══════════════════════════════════════════════════════════
    # PROTEÇÃO / WHITELIST
    # ═══════════════════════════════════════════════════════════

    @commands.command(name="block")
    @commands.has_permissions(manage_messages=True)
    @rate_limit(2)
    async def block(self, ctx, *, word: str):
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            db["blocked_words"].update_one(
                {"guild_id": ctx.guild.id, "word": word.lower()},
                {"$set": {"word": word.lower(), "added_by": ctx.author.id}},
                upsert=True)
        except Exception:
            pass
        await ctx.send(embed=embed_success(f"🚫 Palavra `{word}` bloqueada."))

    @commands.command(name="unblock")
    @commands.has_permissions(manage_messages=True)
    @rate_limit(2)
    async def unblock(self, ctx, *, word: str):
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            db["blocked_words"].delete_one(
                {"guild_id": ctx.guild.id, "word": word.lower()})
        except Exception:
            pass
        await ctx.send(embed=embed_success(
            f"✅ Palavra `{word}` desbloqueada."))

    @commands.command(name="blocklist")
    @commands.has_permissions(manage_messages=True)
    @rate_limit(2)
    async def blocklist(self, ctx):
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            words = list(db["blocked_words"].find({"guild_id": ctx.guild.id}))
        except Exception:
            words = []
        if not words:
            return await ctx.send(embed=embed_info("Lista vazia."))
        text = ", ".join(f"`{w['word']}`" for w in words[:30])
        await ctx.send(embed=embed_info(f"🚫 Bloqueadas:\n{text}"))

    @commands.command(name="whitelist")
    @commands.has_permissions(administrator=True)
    @rate_limit(2)
    async def whitelist(self, ctx, member: discord.Member):
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            db["whitelist"].update_one(
                {"guild_id": ctx.guild.id, "user_id": member.id},
                {"$set": {"user_id": member.id}}, upsert=True)
        except Exception:
            pass
        await ctx.send(embed=embed_success(
            f"✅ {member.mention} na whitelist."))

    @commands.command(name="unwhitelist")
    @commands.has_permissions(administrator=True)
    @rate_limit(2)
    async def unwhitelist(self, ctx, member: discord.Member):
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            db["whitelist"].delete_one(
                {"guild_id": ctx.guild.id, "user_id": member.id})
        except Exception:
            pass
        await ctx.send(embed=embed_success(f"✅ Removido da whitelist."))

    # ═══════════════════════════════════════════════════════════
    # CARGOS (mantidos)
    # ═══════════════════════════════════════════════════════════

    @commands.command(name="addrole")
    @commands.has_permissions(manage_roles=True)
    @rate_limit(2)
    async def addrole(self, ctx, member: discord.Member, role: discord.Role,
                      *, reason="Adicionado"):
        if role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=embed_error("Cargo superior."))
        try:
            await member.add_roles(role, reason=reason)
            await ctx.send(embed=embed_success(
                f"✅ {role.mention} → {member.mention}"))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="removerole")
    @commands.has_permissions(manage_roles=True)
    @rate_limit(2)
    async def removerole(self, ctx, member: discord.Member,
                         role: discord.Role, *, reason="Removido"):
        if role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=embed_error("Cargo superior."))
        try:
            await member.remove_roles(role, reason=reason)
            await ctx.send(embed=embed_success(
                f"✅ {role.mention} removido de {member.mention}"))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="temprole")
    @commands.has_permissions(manage_roles=True)
    @rate_limit(3)
    async def temprole(self, ctx, member: discord.Member, role: discord.Role,
                       duration: str, *, reason="Cargo temporário"):
        if role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=embed_error("Cargo superior."))
        seconds = self._parse_duration(duration)
        if not seconds:
            return await ctx.send(embed=embed_error("Use: 10m, 1h, 1d, 1w"))
        try:
            await member.add_roles(role, reason=reason)
            asyncio.create_task(self._remove_role_later(member, role,
                                                          seconds))
            await ctx.send(embed=embed_success(
                f"✅ {role.mention} por {duration}."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="createrole")
    @commands.has_permissions(manage_roles=True)
    @rate_limit(3)
    async def createrole(self, ctx, name: str, color: str = None,
                         *, reason="Criado"):
        try:
            color_int = (
                int(color[1:], 16) if color and color.startswith("#")
                else (int(color, 16) if color else None))
            role = await ctx.guild.create_role(
                name=name,
                color=discord.Color(color_int) if color_int
                else discord.Color.default(),
                reason=reason)
            await ctx.send(embed=embed_success(
                f"✅ Cargo `{role.name}` criado. ID: {role.id}"))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="deleterole")
    @commands.has_permissions(manage_roles=True)
    @rate_limit(3)
    async def deleterole(self, ctx, role: discord.Role, *,
                         reason="Deletado"):
        if role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=embed_error("Cargo superior."))
        try:
            await role.delete(reason=reason)
            await ctx.send(embed=embed_success("✅ Cargo deletado."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="rolecolor")
    @commands.has_permissions(manage_roles=True)
    @rate_limit(2)
    async def rolecolor(self, ctx, role: discord.Role, color: str):
        try:
            color_int = (int(color[1:], 16) if color.startswith("#")
                         else int(color, 16))
            await role.edit(color=discord.Color(color_int))
            await ctx.send(embed=embed_success("✅ Cor alterada."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="roleinfo")
    @rate_limit(2)
    async def roleinfo(self, ctx, role: discord.Role):
        embed = discord.Embed(
            title=f"📋 {role.name}",
            color=role.color,
            timestamp=datetime.now(timezone.utc))
        embed.add_field(name="🆔 ID", value=role.id, inline=True)
        embed.add_field(name="👥 Membros", value=len(role.members),
                        inline=True)
        embed.add_field(name="🎨 Cor", value=str(role.color), inline=True)
        embed.add_field(name="📊 Posição", value=role.position, inline=True)
        embed.add_field(name="🔗 Menção", value=role.mention, inline=True)
        await ctx.send(embed=embed)

    # ═══════════════════════════════════════════════════════════
    # LOGS / AUDIT
    # ═══════════════════════════════════════════════════════════

    @commands.command(name="modlogs")
    @commands.has_permissions(kick_members=True)
    @rate_limit(3)
    async def modlogs(self, ctx, limit: int = 10):
        limit = max(1, min(limit, 50))
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            logs = list(db["punishments"].find(
                {"guild_id": ctx.guild.id}
            ).sort("timestamp", -1).limit(limit))
        except Exception:
            logs = []
        if not logs:
            return await ctx.send(embed=embed_info("Nenhum log."))
        embed = discord.Embed(
            title="📋 Logs de Moderação",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc))
        for log in logs:
            user = ctx.guild.get_member(log["user_id"]) or "?"
            mod = ctx.guild.get_member(log["moderator_id"]) or "?"
            embed.add_field(
                name=f"#{log.get('_id')} - {log.get('type', '?').upper()}",
                value=(f"**User:** {user}\n**Mod:** {mod}\n"
                       f"**Motivo:** {log.get('reason')}"),
                inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="reason")
    @commands.has_permissions(kick_members=True)
    @rate_limit(2)
    async def reason(self, ctx, case_id: int, *, new_reason: str):
        from utils import safe_object_id
        oid = safe_object_id(str(case_id))
        if not oid:
            return await ctx.send(embed=embed_error("ID inválido."))
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            result = db["punishments"].update_one(
                {"_id": oid, "guild_id": ctx.guild.id},
                {"$set": {"reason": new_reason}})
            if result.modified_count > 0:
                await ctx.send(embed=embed_success(
                    f"✅ Caso #{case_id} atualizado."))
            else:
                await ctx.send(embed=embed_error(
                    f"❌ Caso #{case_id} não encontrado."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="audit")
    @commands.has_permissions(administrator=True)
    @rate_limit(3)
    async def audit(self, ctx, limit: int = 10):
        limit = max(1, min(limit, 50))
        embed = discord.Embed(
            title="🔍 Audit Logs",
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc))
        try:
            async for entry in ctx.guild.audit_logs(limit=limit):
                embed.add_field(
                    name=f"{entry.action.name} - {entry.user}",
                    value=(f"**Alvo:** {entry.target}\n"
                           f"**Motivo:** {entry.reason or 'N/A'}"),
                    inline=False)
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    # ═══════════════════════════════════════════════════════════
    # CONFIG (mantidos, mas usando o delegate do motor)
    # ═══════════════════════════════════════════════════════════

    @commands.command(name="setconfig")
    @commands.has_permissions(administrator=True)
    @rate_limit(2)
    async def setconfig(self, ctx, key: str, *, value: str):
        if value.isdigit():
            value = int(value)
        elif value.lower() in ("true", "false"):
            value = value.lower() == "true"
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            db["guild_config"].update_one(
                {"guild_id": ctx.guild.id}, {"$set": {key: value}}, upsert=True)
            self._invalidate_cfg(ctx.guild.id)
        except Exception:
            pass
        await ctx.send(embed=embed_success(f"✅ `{key}` = `{value}`"))

    @commands.command(name="getconfig")
    @commands.has_permissions(administrator=True)
    @rate_limit(2)
    async def getconfig(self, ctx):
        doc = await self._get_guild_cfg(ctx.guild.id)
        if not doc:
            return await ctx.send(embed=embed_info("Nenhuma config."))
        embed = discord.Embed(
            title="⚙️ Config",
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc))
        for key, value in doc.items():
            if key not in ("_id", "guild_id") and value:
                if key.endswith("_channel") and value:
                    value = f"<#{value}>"
                embed.add_field(name=key.replace("_", " ").title(),
                                value=str(value), inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="resetconfig")
    @commands.has_permissions(administrator=True)
    @rate_limit(2)
    async def resetconfig(self, ctx, key: str = None):
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            if key:
                db["guild_config"].update_one(
                    {"guild_id": ctx.guild.id}, {"$unset": {key: ""}})
                self._invalidate_cfg(ctx.guild.id)
                await ctx.send(embed=embed_success(f"✅ `{key}` resetado."))
            else:
                db["guild_config"].delete_one({"guild_id": ctx.guild.id})
                self._invalidate_cfg(ctx.guild.id)
                await ctx.send(embed=embed_warning("⚠️ Config resetada."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="setperm")
    @commands.has_permissions(administrator=True)
    @rate_limit(2)
    async def setperm(self, ctx, target: str, command: str,
                      enabled: str = "true"):
        enabled_bool = enabled.lower() in ("true", "1", "sim", "on")
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            if target.startswith("<@&") and target.endswith(">"):
                role_id = int(target[3:-1])
                db["role_permissions"].update_one(
                    {"guild_id": ctx.guild.id, "role_id": role_id,
                     "command_name": command},
                    {"$set": {"enabled": 1 if enabled_bool else 0}}, upsert=True)
                await ctx.send(embed=embed_success(
                    f"✅ `{command}` "
                    f"{'liberado' if enabled_bool else 'bloqueado'} para cargo."))
            elif target.startswith("<@") and target.endswith(">"):
                user_id = int(re.search(r'\d+', target).group())
                db["user_permissions"].update_one(
                    {"guild_id": ctx.guild.id, "user_id": user_id,
                     "command_name": command},
                    {"$set": {"enabled": 1 if enabled_bool else 0}}, upsert=True)
                await ctx.send(embed=embed_success(
                    f"✅ `{command}` "
                    f"{'liberado' if enabled_bool else 'bloqueado'} "
                    f"para usuário."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="removeperm")
    @commands.has_permissions(administrator=True)
    @rate_limit(2)
    async def removeperm(self, ctx, target: str, command: str):
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            if target.startswith("<@&") and target.endswith(">"):
                role_id = int(target[3:-1])
                db["role_permissions"].delete_one(
                    {"guild_id": ctx.guild.id, "role_id": role_id,
                     "command_name": command})
                await ctx.send(embed=embed_success("✅ Permissão removida."))
            elif target.startswith("<@") and target.endswith(">"):
                user_id = int(re.search(r'\d+', target).group())
                db["user_permissions"].delete_one(
                    {"guild_id": ctx.guild.id, "user_id": user_id,
                     "command_name": command})
                await ctx.send(embed=embed_success("✅ Permissão removida."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="listperms")
    @commands.has_permissions(administrator=True)
    @rate_limit(2)
    async def listperms(self, ctx, target: str):
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            embed = discord.Embed(
                title="📋 Permissões",
                color=discord.Color.blue(),
                timestamp=datetime.now(timezone.utc))
            if target.startswith("<@&") and target.endswith(">"):
                role_id = int(target[3:-1])
                role = ctx.guild.get_role(role_id)
                if not role:
                    return await ctx.send(embed=embed_error(
                        "Cargo não encontrado."))
                perms = list(db["role_permissions"].find(
                    {"guild_id": ctx.guild.id, "role_id": role_id}))
                embed.title = f"📋 {role.name}"
                for p in perms:
                    embed.add_field(name=p["command_name"],
                                    value="✅" if p["enabled"] else "❌",
                                    inline=True)
            elif target.startswith("<@") and target.endswith(">"):
                user_id = int(re.search(r'\d+', target).group())
                perms = list(db["user_permissions"].find(
                    {"guild_id": ctx.guild.id, "user_id": user_id}))
                embed.title = f"📋 <@{user_id}>"
                for p in perms:
                    embed.add_field(name=p["command_name"],
                                    value="✅" if p["enabled"] else "❌",
                                    inline=True)
            await ctx.send(embed=embed)
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    # ═══════════════════════════════════════════════════════════
    # VOZ (mantidos)
    # ═══════════════════════════════════════════════════════════

    @commands.command(name="moveall")
    @commands.has_permissions(move_members=True)
    @rate_limit(3)
    async def moveall(self, ctx, from_channel: discord.VoiceChannel,
                      to_channel: discord.VoiceChannel):
        moved = 0
        for member in list(from_channel.members)[:15]:
            try:
                await member.move_to(to_channel)
                moved += 1
            except Exception:
                pass
        await ctx.send(embed=embed_success(f"✅ {moved} membros movidos."))

    @commands.command(name="disconnect")
    @commands.has_permissions(move_members=True)
    @rate_limit(2)
    async def disconnect(self, ctx, member: discord.Member):
        if not member.voice:
            return await ctx.send(embed=embed_error("Não está em voz."))
        try:
            await member.move_to(None)
            await ctx.send(embed=embed_success(
                f"✅ {member.mention} desconectado."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="voicekick")
    @commands.has_permissions(move_members=True)
    @rate_limit(2)
    async def voicekick(self, ctx, member: discord.Member):
        if not member.voice:
            return await ctx.send(embed=embed_error("Não está em voz."))
        try:
            await member.move_to(None)
            await ctx.send(embed=embed_success(
                f"✅ {member.mention} kickado do voz."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="voiceban")
    @commands.has_permissions(administrator=True)
    @rate_limit(2)
    async def voiceban(self, ctx, member: discord.Member,
                       channel: discord.VoiceChannel):
        try:
            await channel.set_permissions(member, connect=False)
            if member.voice and member.voice.channel == channel:
                await member.move_to(None)
            await ctx.send(embed=embed_success(
                f"✅ {member.mention} banido de {channel.name}."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="voiceunban")
    @commands.has_permissions(administrator=True)
    @rate_limit(2)
    async def voiceunban(self, ctx, member: discord.Member,
                         channel: discord.VoiceChannel):
        try:
            await channel.set_permissions(member, connect=None)
            await ctx.send(embed=embed_success(
                f"✅ {member.mention} liberado em {channel.name}."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @commands.command(name="cloneroom")
    @commands.has_permissions(manage_channels=True)
    @rate_limit(3)
    async def cloneroom(self, ctx, channel: discord.VoiceChannel = None,
                        name: str = None):
        channel = channel or (ctx.author.voice and ctx.author.voice.channel)
        if not channel:
            return await ctx.send(embed=embed_error("Especifique um canal."))
        name = name or f"{channel.name}-clone"
        try:
            new = await channel.clone(name=name)
            await ctx.send(embed=embed_success(
                f"✅ Canal clonado: {new.name}"))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    # ═══════════════════════════════════════════════════════════
    # UTILITÁRIOS
    # ═══════════════════════════════════════════════════════════

    @commands.command(name="embed")
    @commands.has_permissions(manage_messages=True)
    @rate_limit(2)
    async def embed(self, ctx, title: str, *, description: str):
        e = discord.Embed(
            title=title, description=description,
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc))
        e.set_footer(text=f"Por {ctx.author.display_name}")
        await ctx.send(embed=e)

    @commands.command(name="announce")
    @commands.has_permissions(manage_messages=True)
    @rate_limit(2)
    async def announce(self, ctx, *, text: str):
        e = discord.Embed(
            title="📢 ANÚNCIO", description=text,
            color=discord.Color.gold(),
            timestamp=datetime.now(timezone.utc))
        e.set_footer(text=f"Por {ctx.author.display_name}",
                     icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=e)
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @commands.command(name="poll")
    @commands.has_permissions(manage_messages=True)
    @rate_limit(3)
    async def poll(self, ctx, *, question: str):
        e = discord.Embed(
            title="📊 ENQUETE", description=question,
            color=discord.Color.blue(),
            timestamp=datetime.now(timezone.utc))
        e.set_footer(text=f"Por {ctx.author.display_name}")
        msg = await ctx.send(embed=e)
        await msg.add_reaction("✅")
        await msg.add_reaction("❌")
        try:
            await ctx.message.delete()
        except Exception:
            pass

    @commands.command(name="say")
    @commands.has_permissions(manage_messages=True)
    @rate_limit(2)
    async def say(self, ctx, *, text: str):
        await ctx.send(text)
        try:
            await ctx.message.delete()
        except Exception:
            pass


# ============================================================
# INICIALIZAÇÃO
# ============================================================

if __name__ == "__main__":
    if BOT_TYPE != "moderacao":
        print(f"❌ BOT_TYPE incorreto: {BOT_TYPE}")
        sys.exit(1)

    bot = GTBot()
    print("🚀 Stumble Hit AntiRaid v13.7 iniciando...")
    try:
        bot.run(TOKEN, log_handler=None, reconnect=True)
    except KeyboardInterrupt:
        print("🛑 Interrompido.")
        sys.exit(0)
    except Exception as e:
        print(f"❌ Erro fatal: {e}")
        sys.exit(1)