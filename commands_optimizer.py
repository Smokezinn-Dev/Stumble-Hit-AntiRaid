# ============================================================
# COMMANDS_OPTIMIZER.PY - v9.0
# ⚡ COG DE COMANDOS DO OTIMIZADOR
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Esse cog NÃO é o motor (o motor tá em optimizer.py).
# Ele só expõe COMANDOS pra você ver/controlar o otimizador.
#
# Comandos:
#   >opt                 — Painel
#   >opt stats           — Estatísticas do motor
#   >opt cache           — Stats dos caches
#   >opt cache clear     — Limpa todos os caches
#   >opt snapshot        — Snapshot de config em RAM
#   >opt warm            — Força prefetch
#   >opt gc              — Força GC + limpa caches
#   >opt lag             — Loop lag atual
#   >opt tasks           — Background tasks
#   >opt routes          — Rate limit do Discord
#   >opt modules         — Status dos patches aplicados
#   >opt report          — Relatório completo
# ============================================================

from __future__ import annotations

import asyncio
import gc
import time
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ext import commands, tasks

from database import get_connection
from embeds import embed_success, embed_error, embed_warning, embed_info
from utils import CacheRegistry, GuildGate, memory_mb, has_psutil


def _now() -> datetime:
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


def _fmt_mb(mb: float) -> str:
    """Formata MB em texto. Ex: 75.3 MB."""
    return f"{mb:.1f} MB"


# ============================================================
# COG
# ============================================================

class OptimizerCommands(commands.Cog):
    """
    ⚡ Cog de controle do motor de otimização.
    O motor real está em optimizer.py (roda fora dos cogs).
    """

    def __init__(self, bot):
        self.bot = bot
        # Task de warmup inicial.
        self._warm_task: Optional[asyncio.Task] = None
        # Flag: já rodou warmup?
        self._warmed = False

    async def cog_load(self):
        """Agenda warmup inicial quando o cog carrega."""
        self._initial_warm.start()

    async def cog_unload(self):
        """Cancela tasks ao descarregar."""
        try:
            self._initial_warm.cancel()
        except Exception:
            pass

    # ============================================================
    # WARMUP INICIAL
    # ============================================================

    @tasks.loop(count=1)
    async def _initial_warm(self):
        """Aquece caches 5s após o bot carregar."""
        await self.bot.wait_until_ready()
        await asyncio.sleep(5)
        try:
            await self._warm_all()
        except Exception:
            pass

    async def _warm_all(self) -> float:
        """Aquece caches (guild members, roles, configs)."""
        t0 = time.perf_counter()  # marca o tempo

        # Pega todas as guilds.
        guild_ids = [g.id for g in self.bot.guilds]
        if guild_ids:
            # Roda o warmup "blocking" numa thread separada.
            await asyncio.to_thread(self._warm_blocking, guild_ids)

        dt = (time.perf_counter() - t0) * 1000
        self._warmed = True
        return dt

    @staticmethod
    def _warm_blocking(guild_ids: list) -> None:
        """Blocking warmup — roda em thread separada."""
        # Aquece até 50 guilds.
        for gid in guild_ids[:50]:
            try:
                # Carrega config de cada guild (popula o cache).
                GuildGate._fetch(gid)

                # Registra caches comuns (eles criam vazios).
                for name in ("antinuke_cfg", "automod_cfg",
                             "logs_cfg", "event_logs_cfg"):
                    CacheRegistry.get(name, max_size=200, ttl=120)

            except Exception:
                pass

    # ============================================================
    # HELPERS DO MOTOR
    # ============================================================
    # Esses helpers pegam referências ao optimizer.py
    # (com fallback se ele não carregou).

    def _get_optimizer(self):
        """Importa o optimizer global."""
        try:
            import optimizer
            return optimizer._optimizer
        except Exception:
            return None

    def _get_stats(self) -> dict:
        """Pega as stats do motor."""
        try:
            import optimizer
            return optimizer.get_optimizer_stats()
        except Exception:
            return {}

    def _get_loop_lag(self):
        """Pega o monitor de loop lag."""
        try:
            import optimizer
            return optimizer.get_loop_lag()
        except Exception:
            return None

    def _get_rate_limiter(self):
        """Pega o rate limiter global."""
        try:
            import optimizer
            return optimizer.get_rate_limiter()
        except Exception:
            return None

    def _get_task_tracker(self):
        """Pega o task tracker."""
        try:
            import optimizer
            return optimizer.get_task_tracker()
        except Exception:
            return None

    # ============================================================
    # COMANDO PRINCIPAL: >opt
    # ============================================================

    @commands.group(name="opt", aliases=["optimize", "perf"],
                    invoke_without_command=True)
    @commands.is_owner()
    async def opt(self, ctx):
        """Painel de otimização (só dono)."""
        stats = self._get_stats()
        cache_registry = CacheRegistry.stats()

        embed = discord.Embed(
            title="⚡ Otimizador — Painel",
            description="Motor de performance do bot",
            color=discord.Color.gold(),
            timestamp=_now())

        # ─── Sistema (RAM + uptime). ───
        ram = memory_mb()
        uptime = stats.get("uptime_seconds", 0)
        uptime_str = self._fmt_uptime(uptime)

        embed.add_field(
            name="🖥️ Sistema",
            value=(
                f"**RAM:** `{_fmt_mb(ram)}`\n"
                f"**Uptime:** `{uptime_str}`\n"
                f"**psutil:** {'✅' if has_psutil() else '❌'}"
            ),
            inline=True)

        # ─── Loop lag. ───
        lag_mon = self._get_loop_lag()
        if lag_mon:
            avg = lag_mon.avg_lag
            mx = lag_mon.max_lag
            lag_str = f"avg `{avg:.0f}ms` / max `{mx:.0f}ms`"
        else:
            lag_str = "*indisponível*"
        embed.add_field(
            name="⏱️ Loop Lag",
            value=lag_str,
            inline=True)

        # ─── Task tracker. ───
        tracker = self._get_task_tracker()
        tasks_str = f"`{tracker.count}` ativas" if tracker else "*—*"
        embed.add_field(
            name="🔄 Tasks",
            value=tasks_str,
            inline=True)

        # ─── Cache Registry. ───
        if cache_registry:
            total_size = sum(c.get("size", 0)
                             for c in cache_registry.values())
            embed.add_field(
                name="📦 Caches registrados",
                value=(
                    f"**{len(cache_registry)}** caches "
                    f"(`{total_size}` entradas)"
                ),
                inline=True)
        else:
            embed.add_field(
                name="📦 Caches",
                value="*nenhum registrado*",
                inline=True)

        # ─── Stats do motor. ───
        embed.add_field(
            name="🚀 Motor",
            value=(
                f"**GC forçado:** `{stats.get('gc_forced', 0)}`\n"
                f"**Cache limpado:** `{stats.get('cache_cleaned', 0)}`\n"
                f"**Warmups:** `{stats.get('warmups_done', 0)}`\n"
                f"**Patches:** `{stats.get('patches_applied', 0)}`\n"
                f"**Índices extras:** `{stats.get('indexes_created', 0)}`"
            ),
            inline=False)

        # ─── Loop lag warnings. ───
        warns = stats.get("loop_lag_warns", 0)
        crits = stats.get("loop_lag_criticals", 0)
        embed.add_field(
            name="⚠️ Alertas de lag",
            value=(
                f"**Warns:** `{warns}`\n"
                f"**Criticals:** `{crits}`"
            ),
            inline=True)

        # ─── Lista de subcomandos. ───
        embed.add_field(
            name="📌 Subcomandos",
            value=(
                "`>opt stats` — stats do motor\n"
                "`>opt cache` — ver caches\n"
                "`>opt cache clear` — limpar caches\n"
                "`>opt snapshot` — config em RAM\n"
                "`>opt warm` — forçar prefetch\n"
                "`>opt gc` — limpar RAM\n"
                "`>opt lag` — loop lag atual\n"
                "`>opt tasks` — background tasks\n"
                "`>opt routes` — rate limit Discord\n"
                "`>opt modules` — patches aplicados\n"
                "`>opt report` — relatório completo"
            ),
            inline=False)

        embed.set_footer(text="⚡ Stumble Hit AntiRaid — Motor de Otimização v9.0")
        await ctx.send(embed=embed)

    # ============================================================
    # >opt stats
    # ============================================================

    @opt.command(name="stats")
    @commands.is_owner()
    async def opt_stats(self, ctx):
        """Mostra as estatísticas do motor."""
        stats = self._get_stats()
        if not stats:
            return await ctx.send(embed=embed_error(
                "❌ Motor não inicializado."))

        embed = discord.Embed(
            title="📊 Stats do Motor",
            color=discord.Color.blue(),
            timestamp=_now())

        # Uptime.
        uptime = stats.get("uptime_seconds", 0)
        embed.add_field(
            name="⏱️ Uptime",
            value=f"`{self._fmt_uptime(uptime)}`",
            inline=True)

        # Última atividade do motor.
        last = stats.get("last_cycle_at", 0)
        if last:
            elapsed = int(time.time() - last)
            embed.add_field(
                name="🔄 Último ciclo",
                value=f"há `{elapsed}s`",
                inline=True)

        # Limpezas.
        embed.add_field(
            name="🧹 Limpeza",
            value=(
                f"GC: `{stats.get('gc_forced', 0)}`\n"
                f"Caches: `{stats.get('cache_cleaned', 0)}`\n"
                f"Docs DB: `{stats.get('docs_cleaned', 0)}`"
            ),
            inline=True)

        # Performance.
        embed.add_field(
            name="⚡ Performance",
            value=(
                f"Warmups: `{stats.get('warmups_done', 0)}`\n"
                f"Patches: `{stats.get('patches_applied', 0)}`\n"
                f"Índices: `{stats.get('indexes_created', 0)}`"
            ),
            inline=True)

        # Alertas.
        embed.add_field(
            name="⚠️ Alertas",
            value=(
                f"RAM alta: `{stats.get('memory_alerts', 0)}`\n"
                f"DB fails: `{stats.get('db_health_fails', 0)}`\n"
                f"Lag warns: `{stats.get('loop_lag_warns', 0)}`\n"
                f"Lag crítico: `{stats.get('loop_lag_criticals', 0)}`"
            ),
            inline=True)

        # Rate limiter.
        rl = stats.get("rate_limiter")
        if rl:
            embed.add_field(
                name="🚦 Rate Limit Discord",
                value=(
                    f"Permitidos: `{rl.get('allowed', 0)}`\n"
                    f"Bloqueados: `{rl.get('blocked', 0)}`"
                ),
                inline=True)

        # Loop lag.
        if "loop_lag_avg_ms" in stats:
            embed.add_field(
                name="⏱️ Loop Lag",
                value=(
                    f"avg: `{stats.get('loop_lag_avg_ms')}ms`\n"
                    f"max: `{stats.get('loop_lag_max_ms')}ms`"
                ),
                inline=True)

        # Tasks.
        if "background_tasks" in stats:
            embed.add_field(
                name="🔄 Tasks",
                value=f"`{stats.get('background_tasks')}` ativas",
                inline=True)

        await ctx.send(embed=embed)

    # ============================================================
    # >opt cache
    # ============================================================

    @opt.group(name="cache", invoke_without_command=True)
    @commands.is_owner()
    async def opt_cache(self, ctx):
        """Mostra os caches registrados."""
        caches = CacheRegistry.stats()
        if not caches:
            return await ctx.send(embed=embed_info(
                "📦 Nenhum cache registrado ainda.\n"
                "Os caches são criados conforme os cogs usam."))

        embed = discord.Embed(
            title="📦 Caches registrados",
            description=f"Total: **{len(caches)}** caches",
            color=discord.Color.blurple(),
            timestamp=_now())

        # Mostra os 15 primeiros.
        for name, stats in list(caches.items())[:15]:
            hit_rate = stats.get("hit_rate", 0) * 100
            size = stats.get("size", 0)
            mx = stats.get("max", 0)

            # Barra visual de hit rate.
            bar_filled = int(hit_rate / 10)
            bar = "█" * bar_filled + "░" * (10 - bar_filled)

            embed.add_field(
                name=f"`{name}`",
                value=(
                    f"Size: `{size}/{mx}`\n"
                    f"Hit rate: `{bar}` `{hit_rate:.1f}%`\n"
                    f"Hits: `{stats.get('hits', 0)}` | "
                    f"Miss: `{stats.get('misses', 0)}`"
                ),
                inline=False)

        await ctx.send(embed=embed)

    @opt_cache.command(name="clear")
    @commands.is_owner()
    async def opt_cache_clear(self, ctx):
        """Limpa TODOS os caches."""
        CacheRegistry.invalidate_all()

        # Também limpa caches soltos (GuildGate, branding).
        try:
            GuildGate._cache.clear()
        except Exception:
            pass
        try:
            import embeds
            embeds._brand_cache.clear()
        except Exception:
            pass

        await ctx.send(embed=embed_success("✅ Todos os caches limpos!"))

    @opt_cache.command(name="clean")
    @commands.is_owner()
    async def opt_cache_clean(self, ctx):
        """Remove entradas expiradas dos caches."""
        removed = CacheRegistry.clean_expired()
        await ctx.send(embed=embed_success(
            f"✅ `{removed}` entradas expiradas removidas."))

    # ============================================================
    # >opt warm
    # ============================================================

    @opt.command(name="warm")
    @commands.is_owner()
    async def opt_warm(self, ctx):
        """Força um warmup (aquece caches)."""
        msg = await ctx.send(embed=embed_info("⚡ Aquecendo caches..."))
        dt = await self._warm_all()
        await msg.edit(embed=embed_success(
            f"✅ Warmup completo em `{dt:.1f}ms`"))

    # ============================================================
    # >opt gc
    # ============================================================

    @opt.command(name="gc")
    @commands.is_owner()
    async def opt_gc(self, ctx):
        """Força GC + limpa caches. Mostra quanto liberou."""
        before = memory_mb()

        # Limpa caches.
        CacheRegistry.invalidate_all()
        try:
            GuildGate._cache.clear()
        except Exception:
            pass

        # Roda GC completo.
        collected = gc.collect(2)

        after = memory_mb()

        embed = discord.Embed(
            title="🧹 GC Executado",
            color=discord.Color.green(),
            timestamp=_now())
        embed.add_field(name="📉 Antes",
                        value=f"`{before:.1f} MB`", inline=True)
        embed.add_field(name="📈 Depois",
                        value=f"`{after:.1f} MB`", inline=True)
        embed.add_field(name="💚 Liberado",
                        value=f"`{before - after:.1f} MB`", inline=True)
        embed.add_field(name="🗑️ Objetos coletados",
                        value=f"`{collected}`", inline=False)
        embed.add_field(name="📦 Caches",
                        value="✅ Limpos", inline=True)

        await ctx.send(embed=embed)

    # ============================================================
    # >opt lag
    # ============================================================

    @opt.command(name="lag")
    @commands.is_owner()
    async def opt_lag(self, ctx):
        """Mostra o loop lag atual."""
        lag_mon = self._get_loop_lag()
        if not lag_mon:
            return await ctx.send(embed=embed_error(
                "❌ Loop lag monitor não disponível."))

        avg = lag_mon.avg_lag
        mx = lag_mon.max_lag
        samples = len(lag_mon._samples)

        # Cor conforme o lag.
        if avg < 100:
            color = discord.Color.green()
            status = "🟢 Excelente"
        elif avg < 500:
            color = discord.Color.yellow()
            status = "🟡 Normal"
        elif avg < 1500:
            color = discord.Color.orange()
            status = "🟠 Alto"
        else:
            color = discord.Color.red()
            status = "🔴 Crítico"

        embed = discord.Embed(
            title="⏱️ Loop Lag",
            description=f"**Status:** {status}",
            color=color,
            timestamp=_now())
        embed.add_field(name="Média", value=f"`{avg:.1f} ms`", inline=True)
        embed.add_field(name="Máximo", value=f"`{mx:.1f} ms`", inline=True)
        embed.add_field(name="Amostras", value=f"`{samples}`", inline=True)

        await ctx.send(embed=embed)

    # ============================================================
    # >opt tasks
    # ============================================================

    @opt.command(name="tasks")
    @commands.is_owner()
    async def opt_tasks(self, ctx):
        """Lista as background tasks ativas."""
        tracker = self._get_task_tracker()
        if not tracker:
            return await ctx.send(embed=embed_error(
                "❌ Task tracker não disponível."))

        embed = discord.Embed(
            title="🔄 Background Tasks",
            description=f"Total: **{tracker.count}** tasks ativas",
            color=discord.Color.blue(),
            timestamp=_now())

        # Lista até 10 tasks.
        if tracker._tasks:
            lines = []
            for task in list(tracker._tasks)[:10]:
                name = getattr(task, "get_name", lambda: "unnamed")()
                done = "✅" if task.done() else "⏳"
                lines.append(f"• {done} `{name}`")

            embed.add_field(
                name="Tasks",
                value="\n".join(lines),
                inline=False)

        await ctx.send(embed=embed)

    # ============================================================
    # >opt routes
    # ============================================================

    @opt.command(name="routes")
    @commands.is_owner()
    async def opt_routes(self, ctx):
        """Mostra o rate limit do Discord."""
        rl = self._get_rate_limiter()
        if not rl:
            return await ctx.send(embed=embed_error(
                "❌ Rate limiter não disponível."))

        embed = discord.Embed(
            title="🚦 Rate Limit Discord",
            color=discord.Color.orange(),
            timestamp=_now())
        embed.add_field(
            name="📊 Stats",
            value=(
                f"Permitidos: `{rl.stats.get('allowed', 0)}`\n"
                f"Bloqueados: `{rl.stats.get('blocked', 0)}`"
            ),
            inline=True)
        embed.add_field(
            name="⚙️ Config",
            value=(
                f"Global: `{rl._global_max}/s`\n"
                f"Por rota: `{rl._route_max}/s`"
            ),
            inline=True)

        await ctx.send(embed=embed)

    # ============================================================
    # >opt modules
    # ============================================================

    @opt.command(name="modules")
    @commands.is_owner()
    async def opt_modules(self, ctx):
        """Mostra os patches que foram aplicados."""
        embed = discord.Embed(
            title="🔧 Patches Aplicados",
            color=discord.Color.purple(),
            timestamp=_now())

        patches = []

        # Verifica cada patch.

        # TTLCache.
        try:
            from utils import TTLCache
            if hasattr(TTLCache, "__init__"):
                patches.append("✅ TTLCache (max_size ×3, ttl ×2)")
        except Exception:
            patches.append("❌ TTLCache")

        # GuildGate.
        try:
            from utils import GuildGate
            if GuildGate._cache.max_size >= 500:
                patches.append(
                    f"✅ GuildGate (max={GuildGate._cache.max_size})")
            else:
                patches.append(
                    f"⚠️ GuildGate (max={GuildGate._cache.max_size})")
        except Exception:
            patches.append("❌ GuildGate")

        # CacheRegistry.
        try:
            from utils import CacheRegistry
            names = CacheRegistry.names()
            patches.append(
                f"✅ CacheRegistry ({len(names)} caches)")
        except Exception:
            patches.append("❌ CacheRegistry")

        # Brand cache.
        try:
            import embeds
            patches.append(
                f"✅ Brand cache (max={embeds._brand_cache.max_size})")
        except Exception:
            patches.append("❌ Brand cache")

        embed.add_field(
            name="Estado",
            value="\n".join(patches) or "*Nenhum*",
            inline=False)

        await ctx.send(embed=embed)

    # ============================================================
    # >opt snapshot
    # ============================================================

    @opt.command(name="snapshot")
    @commands.is_owner()
    async def opt_snapshot(self, ctx):
        """Mostra um snapshot dos caches em RAM."""
        caches = CacheRegistry.stats()
        if not caches:
            return await ctx.send(embed=embed_info(
                "📸 Nenhum cache registrado ainda."))

        embed = discord.Embed(
            title="📸 Snapshot em RAM",
            color=discord.Color.blurple(),
            timestamp=_now())

        # Soma totais.
        total_entries = 0
        total_hits = 0
        total_misses = 0

        for name, stats in caches.items():
            size = stats.get("size", 0)
            total_entries += size
            total_hits += stats.get("hits", 0)
            total_misses += stats.get("misses", 0)

        embed.description = (
            f"**Caches:** `{len(caches)}`\n"
            f"**Entradas totais:** `{total_entries}`\n"
            f"**Hits:** `{total_hits}`\n"
            f"**Misses:** `{total_misses}`"
        )

        # Top 10 caches.
        for name, stats in list(caches.items())[:10]:
            size = stats.get("size", 0)
            mx = stats.get("max", 0)
            embed.add_field(
                name=f"`{name}`",
                value=f"{size}/{mx}",
                inline=True)

        await ctx.send(embed=embed)

    # ============================================================
    # >opt report
    # ============================================================

    @opt.command(name="report")
    @commands.is_owner()
    async def opt_report(self, ctx):
        """Relatório completo do estado do motor."""
        stats = self._get_stats()
        caches = CacheRegistry.stats()

        embed = discord.Embed(
            title="📋 Relatório Completo",
            description="Estado atual do motor de otimização",
            color=discord.Color.gold(),
            timestamp=_now())

        # Sistema.
        embed.add_field(
            name="🖥️ Sistema",
            value=(
                f"RAM: `{memory_mb():.1f}MB`\n"
                f"Uptime: `{self._fmt_uptime(stats.get('uptime_seconds', 0))}`"
            ),
            inline=False)

        # Motor.
        embed.add_field(
            name="⚙️ Motor",
            value=(
                f"GC: `{stats.get('gc_forced', 0)}` | "
                f"Caches: `{stats.get('cache_cleaned', 0)}` | "
                f"Warmups: `{stats.get('warmups_done', 0)}`"
            ),
            inline=False)

        # Performance.
        lag = self._get_loop_lag()
        tracker = self._get_task_tracker()
        embed.add_field(
            name="⚡ Performance",
            value=(
                f"Loop lag: `{lag.avg_lag:.0f}ms` (média)\n"
                f"Tasks: `{tracker.count if tracker else 0}`"
            ),
            inline=False)

        # Caches.
        total_size = sum(c.get("size", 0) for c in caches.values())
        total_hits = sum(c.get("hits", 0) for c in caches.values())
        total_misses = sum(c.get("misses", 0) for c in caches.values())
        total = total_hits + total_misses
        overall_hit_rate = (total_hits / total * 100) if total else 0

        embed.add_field(
            name="📦 Caches",
            value=(
                f"Registrados: `{len(caches)}`\n"
                f"Entradas: `{total_size}`\n"
                f"Hit rate geral: `{overall_hit_rate:.1f}%`"
            ),
            inline=False)

        await ctx.send(embed=embed)

    # ============================================================
    # HELPERS
    # ============================================================

    @staticmethod
    def _fmt_uptime(seconds: int) -> str:
        """Formata segundos em texto. Ex: 7200 → '2h 0min'."""
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}min"
        if seconds < 86400:
            h = seconds // 3600
            m = (seconds % 3600) // 60
            return f"{h}h {m}min"
        d = seconds // 86400
        h = (seconds % 86400) // 3600
        return f"{d}d {h}h"


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("OptimizerCommands") is None:
        await bot.add_cog(OptimizerCommands(bot))