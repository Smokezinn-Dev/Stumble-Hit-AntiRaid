# ============================================================
# OPTIMIZER.PY — Stumble Hit AntiRaid v1.0
# ⚡ MOTOR PESADO DE OTIMIZAÇÃO (inspirado no bot economia)
# ============================================================
# Esse arquivo é o "cérebro de performance" do bot. Ele roda
# SEPARADO dos cogs (não é um comando), em paralelo, cuidando de:
#   • Limpar caches automaticamente
#   • Rodar GC (faxineiro de memória) periodicamente
#   • Monitorar lag do event loop
#   • Aplicar "patches" (melhorias em outras classes)
#   • Criar índices extras no MongoDB
#   • Aquecer caches (deixa tudo rápido)
#   • Salvar estatísticas
# ============================================================

from __future__ import annotations

import asyncio
import gc
import json
import logging
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# psutil = mede RAM/CPU (opcional)
try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

# discord = principal (opcional aqui, só pra checar HTTPException)
try:
    import discord
    _HAS_DISCORD = True
except ImportError:
    _HAS_DISCORD = False


_logger = logging.getLogger("Optimizer")


# ============================================================
# CONFIGURAÇÃO (calibrada pra Railway free: 2 vCPU, 1GB RAM)
# ============================================================
# Cada valor é "de quanto em quanto tempo" ou "limite" pra algo.
# Tudo em segundos, exceto onde marcado.

_DEFAULT_CONFIG = {
    # Intervalos
    "cycle_delay": 8.0,             # delay entre ciclos do loop
    "gc_interval": 300,             # GC a cada 5min
    "cache_clean_interval": 300,    # limpa caches a cada 5min
    "db_cleanup_interval": 21600,   # cleanup DB a cada 6h
    "db_health_interval": 900,      # ping DB a cada 15min
    "memory_check_interval": 60,    # check RAM a cada 1min
    "warmup_interval": 1800,        # warmup a cada 30min
    "lag_check_interval": 30,       # check lag a cada 30s
    "stats_persist_interval": 1800, # salva stats a cada 30min

    # Limites de memória (MB)
    "memory_gc_threshold_mb": 500,
    "memory_warn_threshold_mb": 700,
    "memory_alert_threshold_mb": 850,

    # Cache (multiplicadores dos patches)
    "cache_multiplier": 3,          # 3x o tamanho padrão
    "cache_ttl_multiplier": 2,      # 2x o TTL padrão

    # GC
    "gc_threshold": (700, 10, 10),  # config do coletor de lixo

    # Loop lag (Railway aguenta até ~1.5s sem problema)
    "loop_lag_warn_ms": 1500,
    "loop_lag_critical_ms": 3000,

    # Rate limit do Discord (proteção contra 429)
    "global_rate_limit_per_sec": 40,
    "route_rate_limit_per_sec": 5,

    # Onde salvar stats (Railway tem /tmp gravável)
    "stats_persist_path": "/tmp/optimizer_stats.json",

    # Auto-tuning (desligado)
    "auto_tune_enabled": False,
}


# ============================================================
# STATS (contadores do optimizer)
# ============================================================

class OptimizerStats:
    """
    Guarda contadores de tudo que o optimizer fez.
    Ex: quantos GCs rodou, quantos caches limpou, etc.
    """
    __slots__ = ("_data",)

    def __init__(self):
        # Dicionário com todos os contadores começando em 0.
        self._data = {
            "gc_forced": 0,           # quantas vezes forçou GC
            "cache_cleaned": 0,       # entradas removidas dos caches
            "docs_cleaned": 0,        # documentos removidos do DB
            "memory_alerts": 0,       # alertas de RAM alta
            "db_health_fails": 0,     # falhas de ping no DB
            "patches_applied": 0,     # patches aplicados
            "indexes_created": 0,     # índices extras criados
            "warmups_done": 0,        # warmups executados
            "loop_lag_warns": 0,      # avisos de lag
            "loop_lag_criticals": 0,  # lag crítico
            "tunings_applied": 0,
            "started_at": time.time(),  # quando começou
            "last_cycle_at": 0.0,       # último ciclo
        }

    def inc(self, key: str, amount: int = 1) -> None:
        """Incrementa um contador."""
        self._data[key] = self._data.get(key, 0) + amount

    def set(self, key: str, value: Any) -> None:
        """Define um valor direto (não incrementa)."""
        self._data[key] = value

    def get(self, key: str, default: Any = 0) -> Any:
        """Lê um valor."""
        return self._data.get(key, default)

    def to_dict(self) -> dict:
        """Devolve tudo como dicionário (com uptime calculado)."""
        data = dict(self._data)
        if data.get("started_at"):
            data["uptime_seconds"] = int(time.time() - data["started_at"])
        return data


# ============================================================
# RATE LIMITER GLOBAL DO DISCORD
# ============================================================
# Evita o bot tomar 429 (rate limit) do Discord.
# É como um "policial" que libera N requisições por segundo.

class DiscordRateLimiter:
    __slots__ = ("_global_max", "_route_max", "_global_tokens",
                 "_global_reset", "_route_tokens", "_route_reset",
                 "_lock", "_stats")

    def __init__(self, global_per_sec: int = 40, per_route_per_sec: int = 5):
        self._global_max = float(global_per_sec)      # limite global
        self._route_max = float(per_route_per_sec)    # limite por rota
        self._global_tokens = self._global_max        # fichas disponíveis
        self._global_reset = time.monotonic() + 1.0   # quando repõe
        self._route_tokens: Dict[str, float] = {}
        self._route_reset: Dict[str, float] = {}
        self._lock = asyncio.Lock()
        self._stats = {"allowed": 0, "blocked": 0}

    async def acquire(self, route_key: str = "default") -> bool:
        """
        Tenta pegar 1 ficha. Retorna True se conseguiu.
        False = tá bloqueado (espera um pouco).
        """
        async with self._lock:
            now = time.monotonic()

            # Se passou 1 segundo, repõe TODAS as fichas globais.
            if now > self._global_reset:
                self._global_tokens = self._global_max
                self._global_reset = now + 1.0

            # Sem ficha global? Bloqueia.
            if self._global_tokens < 1:
                self._stats["blocked"] += 1
                return False
            self._global_tokens -= 1

            # Mesma coisa pra rota específica.
            if now > self._route_reset.get(route_key, 0):
                self._route_tokens[route_key] = self._route_max
                self._route_reset[route_key] = now + 1.0

            if self._route_tokens.get(route_key, 0) < 1:
                self._stats["blocked"] += 1
                return False
            self._route_tokens[route_key] -= 1

            self._stats["allowed"] += 1
            return True

    async def wait(self, route_key: str = "default",
                    timeout: float = 10.0) -> bool:
        """Fica tentando pegar ficha até conseguir ou dar timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self.acquire(route_key):
                return True
            await asyncio.sleep(0.1)
        return False

    @property
    def stats(self) -> dict:
        return dict(self._stats)


# Instância global (só existe UMA).
_rate_limiter: Optional[DiscordRateLimiter] = None


def get_rate_limiter() -> Optional[DiscordRateLimiter]:
    """Devolve o rate limiter global (ou None se ainda não foi criado)."""
    return _rate_limiter


# ============================================================
# TASK TRACKER (evita "tasks órfãs" que consomem RAM)
# ============================================================

class TaskTracker:
    """
    Guarda todas as tasks paralelas criadas. Assim dá pra
    cancelar todas de uma vez quando o bot desliga.
    """
    __slots__ = ("_tasks",)

    def __init__(self):
        self._tasks: set = set()  # conjunto (sem duplicatas)

    def spawn(self, coro) -> Optional[asyncio.Task]:
        """Cria e registra uma task."""
        try:
            task = asyncio.create_task(coro)
            self._tasks.add(task)
            task.add_done_callback(self._on_done)
            return task
        except Exception:
            return None

    def _on_done(self, task: asyncio.Task):
        """Chamado quando a task termina (remove do registro)."""
        self._tasks.discard(task)
        try:
            exc = task.exception()
            if exc and not isinstance(exc, asyncio.CancelledError):
                _logger.debug(f"Task falhou: {type(exc).__name__}: {exc}")
        except Exception:
            pass

    async def cancel_all(self, timeout: float = 5.0) -> None:
        """Cancela TODAS as tasks e espera terminarem."""
        if not self._tasks:
            return
        for task in list(self._tasks):
            task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*self._tasks, return_exceptions=True),
                timeout=timeout,
            )
        except Exception:
            pass
        self._tasks.clear()

    @property
    def count(self) -> int:
        """Quantas tasks estão ativas."""
        return len(self._tasks)


_task_tracker: Optional[TaskTracker] = None


def get_task_tracker() -> Optional[TaskTracker]:
    return _task_tracker


# ============================================================
# USER NAME CACHE (evita buscar nome toda hora)
# ============================================================

class UserNameCache:
    """
    Guarda nomes de usuários em cache por 1 hora.
    Evita fazer fetch_user (que é lento) toda hora.
    """
    __slots__ = ("_cache", "_ttl", "_max_size")

    def __init__(self, ttl: float = 3600.0, max_size: int = 5000):
        self._cache: Dict[int, tuple] = {}  # {id: (nome, timestamp)}
        self._ttl = ttl
        self._max_size = max_size

    async def get(self, bot, user_id: int) -> str:
        """Pega o nome do user (do cache OU busca)."""
        now_ts = time.time()
        cached = self._cache.get(user_id)

        # Cache válido? Devolve.
        if cached and (now_ts - cached[1]) < self._ttl:
            return cached[0]

        # Não tá no cache → busca.
        user = bot.get_user(user_id)
        if user is None:
            try:
                user = await bot.fetch_user(user_id)
            except Exception:
                user = None

        # Monta o nome (ou fallback se não achou).
        name = user.display_name if user else f"User {user_id}"
        self._cache[user_id] = (name, now_ts)

        # Limpa entradas velhas se o cache encher.
        if len(self._cache) > self._max_size:
            cutoff = now_ts - self._ttl
            for k in [k for k, v in self._cache.items() if v[1] < cutoff]:
                del self._cache[k]

        return name

    def invalidate(self, user_id: int) -> None:
        self._cache.pop(user_id, None)

    def clear(self) -> None:
        self._cache.clear()


_user_name_cache: Optional[UserNameCache] = None


def get_user_name_cache() -> Optional[UserNameCache]:
    return _user_name_cache


# ============================================================
# LOOP LAG MONITOR (mede "engasgo" do event loop)
# ============================================================
# Se o loop principal do bot travar, tudo atrasa. Isso mede.

class LoopLagMonitor:
    """
    Mede quanto o event loop "atrasou" no último ciclo.
    Só alerta em travamentos REAIS (configurado pra 1.5s+).
    """
    __slots__ = ("_last_check", "_warn_ms", "_critical_ms", "_samples")

    def __init__(self, warn_ms: int = 1500, critical_ms: int = 3000):
        self._last_check = time.monotonic()
        self._warn_ms = warn_ms
        self._critical_ms = critical_ms
        self._samples = deque(maxlen=60)  # últimas 60 medições

    def check(self, expected_interval: float = 8.0) -> dict:
        """
        Checa o lag agora.
        expected_interval = quanto tempo DEVERIA ter passado.
        Se passou muito mais, é lag.
        """
        now = time.monotonic()
        # Quanto passou ALÉM do esperado (em ms).
        lag_ms = (now - self._last_check - expected_interval) * 1000
        self._last_check = now

        if lag_ms < 0:
            lag_ms = 0

        self._samples.append(lag_ms)

        result = {"lag_ms": lag_ms, "status": "ok"}
        if lag_ms >= self._critical_ms:
            result["status"] = "critical"
        elif lag_ms >= self._warn_ms:
            result["status"] = "warn"
        return result

    @property
    def avg_lag(self) -> float:
        """Média das últimas medições."""
        if not self._samples:
            return 0.0
        return sum(self._samples) / len(self._samples)

    @property
    def max_lag(self) -> float:
        """Pior lag registrado."""
        return max(self._samples) if self._samples else 0.0


_loop_lag_monitor: Optional[LoopLagMonitor] = None


def get_loop_lag() -> Optional[LoopLagMonitor]:
    return _loop_lag_monitor


# ============================================================
# GLOBAL OPTIMIZER (o motor de verdade)
# ============================================================

class GlobalOptimizer:
    """
    O motor principal. Roda um loop infinito que:
    - Checa lag
    - Roda GC a cada X tempo
    - Limpa caches
    - Checa memória
    - Pinga o DB
    - Limpa coleções antigas
    - Faz warmup
    - Persiste stats
    """
    __slots__ = (
        "_running", "_task", "_config", "_stats", "_bot",
        "_last_gc", "_last_cache_clean", "_last_memory_check",
        "_last_db_check", "_last_db_cleanup", "_last_warmup",
        "_last_stats_persist", "_last_tuning",
        "_patch_applied",
    )

    def __init__(self, config: Optional[dict] = None):
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # Junta a config padrão com a customizada (se passou).
        self._config = {**_DEFAULT_CONFIG, **(config or {})}
        self._stats = OptimizerStats()
        self._patch_applied = False
        self._bot = None

        # Timestamps das últimas execuções de cada coisa.
        self._last_gc = 0.0
        self._last_cache_clean = 0.0
        self._last_memory_check = 0.0
        self._last_db_check = 0.0
        self._last_db_cleanup = 0.0
        self._last_warmup = 0.0
        self._last_stats_persist = 0.0
        self._last_tuning = 0.0

    async def start(self) -> None:
        """Liga o optimizer (chamado uma vez no main.py)."""
        global _rate_limiter, _task_tracker, _user_name_cache, _loop_lag_monitor

        if self._running:
            _logger.info("⏩ Optimizer já estava rodando")
            return

        _logger.info("🚀 Iniciando Optimizer v1.0...")

        # 1. Cria os componentes globais.
        try:
            _rate_limiter = DiscordRateLimiter(
                global_per_sec=self._config["global_rate_limit_per_sec"],
                per_route_per_sec=self._config["route_rate_limit_per_sec"],
            )
            _task_tracker = TaskTracker()
            _user_name_cache = UserNameCache()
            _loop_lag_monitor = LoopLagMonitor(
                warn_ms=self._config["loop_lag_warn_ms"],
                critical_ms=self._config["loop_lag_critical_ms"],
            )
            _logger.info("   ✅ Componentes globais inicializados")
        except Exception as e:
            _logger.warning(f"   ⚠️ Falha componentes globais: {e}")

        # 2. Tuning do GC (faxineiro).
        try:
            gc.set_threshold(*self._config["gc_threshold"])
            gc.collect()
            try:
                gc.freeze()  # "congela" objetos permanentes
                _logger.info("   ✅ GC tuning + freeze aplicado")
            except Exception:
                _logger.info("   ✅ GC tuning aplicado")
        except Exception as e:
            _logger.warning(f"   ⚠️ GC tuning falhou: {e}")

        # 3. Aplica patches (melhorias em outras classes).
        try:
            self._apply_monkey_patches()
            _logger.info(
                f"   ✅ {self._stats.get('patches_applied')} patches aplicados")
        except Exception as e:
            _logger.warning(f"   ⚠️ Monkey patches falharam: {e}")

        # 4. Cria índices extras em BACKGROUND.
        try:
            if _task_tracker:
                _task_tracker.spawn(self._create_extra_indexes_async())
        except Exception as e:
            _logger.warning(f"   ⚠️ Índices extras: {e}")

        # 5. Aquece o pool do Mongo.
        try:
            await self._warmup_mongo_pool()
            _logger.info("   ✅ Warmup do pool Mongo")
        except Exception as e:
            _logger.warning(f"   ⚠️ Warmup falhou: {e}")

        # Marca como rodando e cria a task do loop principal.
        self._running = True
        self._task = asyncio.create_task(self._run())
        _logger.info("✅ Optimizer v1.0 rodando")

    async def stop(self) -> None:
        """Desliga o optimizer (chamado no close do bot)."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        # Cancela tasks filhas.
        if _task_tracker:
            await _task_tracker.cancel_all()

        self._persist_stats()
        _logger.info("⏹️ Optimizer parado")

    async def _run(self) -> None:
        """Loop principal — roda pra sempre."""
        while self._running:
            try:
                # Espera o "delay" entre ciclos (evita consumir 100% CPU).
                await asyncio.sleep(self._config["cycle_delay"])

                now = time.monotonic()
                self._stats.set("last_cycle_at", time.time())

                # ─── Checa loop lag ───
                if _loop_lag_monitor:
                    result = _loop_lag_monitor.check(
                        expected_interval=self._config["cycle_delay"])
                    if result["status"] == "critical":
                        self._stats.inc("loop_lag_criticals")
                        lag = result["lag_ms"]
                        _logger.warning(
                            f"⚠️ Event loop lag MUITO alto: {lag:.0f}ms")
                        # Se travou de vez, lista as tasks ativas pra debug.
                        if lag > 5000:
                            try:
                                all_tasks = asyncio.all_tasks()
                                _logger.warning(
                                    f"   Tasks ativas: {len(all_tasks)}")
                                for t in list(all_tasks)[:5]:
                                    name = getattr(t, "get_name",
                                                    lambda: "?")()
                                    _logger.warning(f"   • {name}")
                            except Exception:
                                pass
                    elif result["status"] == "warn":
                        self._stats.inc("loop_lag_warns")

                # ─── GC a cada X tempo ───
                if now - self._last_gc > self._config["gc_interval"]:
                    await self._force_gc_async()
                    self._last_gc = now
                    await asyncio.sleep(0.1)  # deixa o loop respirar

                # ─── Limpa caches ───
                if now - self._last_cache_clean > self._config["cache_clean_interval"]:
                    await self._clean_caches()
                    self._last_cache_clean = now
                    await asyncio.sleep(0.1)

                # ─── Checa memória ───
                if now - self._last_memory_check > self._config["memory_check_interval"]:
                    await self._check_memory()
                    self._last_memory_check = now
                    await asyncio.sleep(0.1)

                # ─── Pinga o DB ───
                if now - self._last_db_check > self._config["db_health_interval"]:
                    await self._check_db_health()
                    self._last_db_check = now
                    await asyncio.sleep(0.1)

                # ─── Limpa coleções antigas ───
                if now - self._last_db_cleanup > self._config["db_cleanup_interval"]:
                    await self._cleanup_collections()
                    self._last_db_cleanup = now
                    await asyncio.sleep(0.1)

                # ─── Warmup ───
                if now - self._last_warmup > self._config["warmup_interval"]:
                    await self._warmup_hot_caches()
                    self._last_warmup = now
                    await asyncio.sleep(0.1)

                # ─── Salva stats ───
                if now - self._last_stats_persist > self._config["stats_persist_interval"]:
                    self._persist_stats()
                    self._last_stats_persist = now

                # "respira" (cede o controle pro event loop).
                await asyncio.sleep(0)

            except asyncio.CancelledError:
                break  # pediram pra parar
            except Exception as e:
                _logger.error(f"⚠️ Optimizer erro: {e}", exc_info=False)
                await asyncio.sleep(15)  # espera antes de tentar de novo

    # ============================================================
    # MONKEY PATCHES (melhorias em outras classes)
    # ============================================================
    # "Monkey patch" = substituir um método de uma classe existente
    # por um melhorado. Aqui, aumentamos os caches.

    def _apply_monkey_patches(self) -> None:
        """Aplica todos os patches."""
        if self._patch_applied:
            return

        patches = 0
        if self._patch_ttl_cache():
            patches += 1
        if self._patch_guild_gate():
            patches += 1
        if self._patch_cache_registry():
            patches += 1
        if self._patch_embeds_brand():
            patches += 1

        self._patch_applied = True
        self._stats.set("patches_applied", patches)

    def _patch_ttl_cache(self) -> bool:
        """Aumenta o tamanho padrão do TTLCache (3x)."""
        try:
            from utils import TTLCache
            _orig = TTLCache.__init__
            mult = self._config["cache_multiplier"]
            ttl_mult = self._config["cache_ttl_multiplier"]

            def _patched(self, max_size=500, ttl=30):
                # Chama o __init__ original com valores MAIORES.
                _orig(
                    self,
                    max_size=int(max_size * mult),
                    ttl=int(ttl * ttl_mult),
                )

            TTLCache.__init__ = _patched
            _logger.debug(f"   Patch TTLCache: x{mult} size, x{ttl_mult} ttl")
            return True
        except Exception as e:
            _logger.warning(f"   Patch TTLCache falhou: {e}")
            return False

    def _patch_guild_gate(self) -> bool:
        """Recria o cache do GuildGate com tamanho maior."""
        try:
            from utils import GuildGate, TTLCache
            GuildGate._cache = TTLCache(max_size=500, ttl=300)
            return True
        except Exception as e:
            _logger.warning(f"   Patch GuildGate falhou: {e}")
            return False

    def _patch_cache_registry(self) -> bool:
        """Aplica patch no CacheRegistry (se existir)."""
        try:
            from utils import CacheRegistry
            mult = self._config["cache_multiplier"]
            ttl_mult = self._config["cache_ttl_multiplier"]

            _orig_get = CacheRegistry.get.__func__

            @classmethod
            def _patched_get(cls, name, max_size=300, ttl=60):
                # Chama o original multiplicando os valores.
                return _orig_get(
                    cls, name,
                    max_size=max_size * mult,
                    ttl=ttl * ttl_mult,
                )

            CacheRegistry.get = _patched_get

            # Aumenta caches que JÁ existem.
            for cache in CacheRegistry._caches.values():
                try:
                    cache.max_size = max(cache.max_size * mult, 500)
                    cache.ttl = max(cache.ttl * ttl_mult, 120)
                except Exception:
                    pass

            _logger.debug("   Patch CacheRegistry aplicado")
            return True
        except ImportError:
            return False
        except Exception as e:
            _logger.warning(f"   Patch CacheRegistry falhou: {e}")
            return False

    def _patch_embeds_brand(self) -> bool:
        """Aumenta o cache de branding."""
        try:
            from embeds import _brand_cache, TTLCache
            import embeds
            embeds._brand_cache = TTLCache(max_size=500, ttl=180)
            return True
        except Exception:
            return False

    # ============================================================
    # ÍNDICES EXTRAS (background)
    # ============================================================

    async def _create_extra_indexes_async(self) -> None:
        """Cria os índices extras com Motor, sem bloquear o event loop."""
        try:
            created = await self._create_extra_indexes_motor()
            self._stats.inc("indexes_created", created)
            if created:
                _logger.info(f"   ✅ {created} índices extras criados")
        except Exception as e:
            _logger.warning(f"   ⚠️ Índices extras: {e}")

    async def _create_extra_indexes_motor(self) -> int:
        """Cria no MongoDB os mesmos índices definidos pelo otimizador."""
        # A conexão Motor é assíncrona; cada criação é aguardada individualmente.
        from database import get_connection_async
        db = await get_connection_async()
        created = 0

        # Lista de (coleção, chaves, nome do índice).
        indexes = [
            ("antinuke_logs",
             [("guild_id", 1), ("timestamp", -1)],
             "opt_anlog_ts"),
            ("privilege_alerts",
             [("guild_id", 1), ("resolved", 1), ("timestamp", -1)],
             "opt_priv_resolved"),
            ("event_logs_config",
             [("guild_id", 1)],
             "opt_evlog_cfg"),
            ("protection_toggle_logs",
             [("guild_id", 1), ("timestamp", -1)],
             "opt_ptoggle_ts"),
            ("whitelist_confirm_config",
             [("guild_id", 1)],
             "opt_wlconf_cfg"),
            ("deleted_channels_cache",
             [("guild_id", 1), ("timestamp", -1)],
             "opt_delch_ts"),
            ("deleted_roles_cache",
             [("guild_id", 1), ("timestamp", -1)],
             "opt_delro_ts"),
            ("punishments",
             [("guild_id", 1), ("timestamp", -1)],
             "opt_pun_ts"),
            ("warns",
             [("guild_id", 1), ("user_id", 1), ("timestamp", -1)],
             "opt_warns_usr_ts"),
            ("staff_points",
             [("guild_id", 1), ("points", -1)],
             "opt_stpts_rank"),
            ("staff_actions",
             [("guild_id", 1), ("timestamp", -1)],
             "opt_stact_ts"),
            ("mod_cases",
             [("guild_id", 1), ("active", 1), ("timestamp", -1)],
             "opt_case_active"),
            ("appeals",
             [("guild_id", 1), ("status", 1), ("created_at", -1)],
             "opt_appeals_pend"),
        ]

        for col, keys, name in indexes:
            try:
                await db[col].create_index(keys, name=name)
                created += 1
            except Exception:
                pass

        return created

    # ============================================================
    # WARMUP (deixar tudo rápido)
    # ============================================================

    async def _warmup_mongo_pool(self) -> None:
        """Aquece o pool Motor com quatro pings paralelos."""
        try:
            from database import get_connection_async
            db = await get_connection_async()
            # Mantém os quatro pings paralelos do warmup original.
            await asyncio.gather(
                *(db.command("ping") for _ in range(4)),
                return_exceptions=True)
        except Exception:
            pass

    async def _warmup_hot_caches(self) -> None:
        """Aquece os caches mais usados."""
        try:
            from database import get_connection_async
            from utils import GuildGate
            db = await get_connection_async()

            # Pega os mesmos IDs de servidores, agora via cursor Motor.
            guild_ids = await db["guild_config"].distinct("guild_id")
            if not guild_ids:
                return

            # Carrega cada config (popula o cache do GuildGate).
            def _sync_warm():
                for gid in guild_ids[:50]:  # limita a 50
                    try:
                        GuildGate._fetch(gid)
                    except Exception:
                        pass

            await loop.run_in_executor(None, _sync_warm)
            self._stats.inc("warmups_done")
        except Exception as e:
            _logger.debug(f"Warmup falhou: {e}")

    # ============================================================
    # GC (garbage collector)
    # ============================================================

    async def _force_gc_async(self) -> None:
        """Força GC sem travar o loop (roda em thread)."""
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._force_gc_blocking)
            self._stats.inc("gc_forced")
        except Exception:
            pass

    @staticmethod
    def _force_gc_blocking() -> None:
        """O GC de verdade (blocking)."""
        gc.collect(0)  # geração jovem
        gc.collect(2)  # geração completa

    # ============================================================
    # LIMPEZA DE CACHES
    # ============================================================

    async def _clean_caches(self) -> None:
        """Remove entradas expiradas de todos os caches."""
        now = time.monotonic()
        total = 0

        # GuildGate
        try:
            from utils import GuildGate
            total += self._clean_one_cache(GuildGate._cache, now)
        except Exception:
            pass

        # CacheRegistry
        try:
            from utils import CacheRegistry
            for cache in list(CacheRegistry._caches.values()):
                total += self._clean_one_cache(cache, now)
        except Exception:
            pass

        # Brand cache
        try:
            import embeds
            total += self._clean_one_cache(embeds._brand_cache, now)
        except Exception:
            pass

        if total > 0:
            self._stats.inc("cache_cleaned", total)

    @staticmethod
    def _clean_one_cache(cache, now: float) -> int:
        """Limpa UM cache. Retorna quantos removeu."""
        removed = 0
        try:
            with cache._lock:
                # Lista chaves expiradas.
                expired = [
                    k for k, (v, exp) in cache._data.items()
                    if now > exp
                ]
                for k in expired:
                    del cache._data[k]
                    removed += 1
        except Exception:
            pass
        return removed

    # ============================================================
    # CHECAGEM DE MEMÓRIA
    # ============================================================

    async def _check_memory(self) -> None:
        """Verifica uso de RAM e força GC se necessário."""
        if not _HAS_PSUTIL:
            return

        try:
            process = psutil.Process()
            mem_mb = process.memory_info().rss / 1024 / 1024

            warn = self._config["memory_warn_threshold_mb"]
            alert = self._config["memory_alert_threshold_mb"]
            gc_thresh = self._config["memory_gc_threshold_mb"]

            # RAM crítica → log CRITICAL.
            if mem_mb >= alert:
                _logger.critical(f"🚨 RAM CRÍTICA: {mem_mb:.1f}MB")
                self._stats.inc("memory_alerts")
            # RAM alta → log WARNING.
            elif mem_mb >= warn:
                _logger.warning(f"🧹 RAM alta: {mem_mb:.1f}MB")
                self._stats.inc("memory_alerts")

            # Passou do threshold de GC → força.
            if mem_mb >= gc_thresh:
                await self._force_gc_async()
        except Exception:
            pass

    # ============================================================
    # HEALTH CHECK DO DB
    # ============================================================

    async def _check_db_health(self) -> None:
        """Verifica a saúde do MongoDB usando uma operação Motor."""
        try:
            from database import get_connection_async
            db = await get_connection_async()
            await db.command("ping")
        except Exception:
            self._stats.inc("db_health_fails")
            _logger.warning("⚠️ DB health check falhou")

    # ============================================================
    # CLEANUP DE COLEÇÕES (remover docs muito velhos)
    # ============================================================

    async def _cleanup_collections(self) -> None:
        """Remove documentos antigos usando operações assíncronas do Motor."""
        try:
            total = await self._do_cleanup_motor()
            if total > 0:
                self._stats.inc("docs_cleaned", total)
                _logger.info(f"🧹 Cleanup: {total} docs removidos")
        except Exception as e:
            _logger.warning(f"⚠️ Cleanup: {e}")

    async def _do_cleanup_motor(self) -> int:
        """Aplica os mesmos prazos de retenção do cleanup original."""
        from database import get_connection_async
        db = await get_connection_async()
        total = 0
        now = datetime.utcnow()

        # (coleção, campo de data, quantos dias guardar)
        cleanups = [
            ("antinuke_logs", "timestamp", 30),
            ("privilege_changes", "timestamp", 60),
            ("privilege_alerts", "timestamp", 120),
            ("punishments", "timestamp", 180),
            ("protection_toggle_logs", "timestamp", 120),
        ]

        for col, field, days in cleanups:
            try:
                cutoff = now - timedelta(days=days)
                # Aguarda a exclusão antes de somar o total confirmado.
                r = await db[col].delete_many({field: {"$lt": cutoff}})
                total += r.deleted_count
            except Exception:
                pass

        return total

    # ============================================================
    # PERSISTÊNCIA DAS STATS (salvar em arquivo)
    # ============================================================

    def _persist_stats(self) -> None:
        """Salva as stats em JSON no /tmp."""
        try:
            path = Path(self._config["stats_persist_path"])
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w") as f:
                json.dump(self._stats.to_dict(), f, indent=2, default=str)
        except Exception:
            pass

    def get_stats(self) -> dict:
        """Devolve TODAS as stats consolidadas (pra >opt)."""
        data = self._stats.to_dict()
        # Anexa stats dos componentes globais.
        if _rate_limiter:
            data["rate_limiter"] = _rate_limiter.stats
        if _task_tracker:
            data["background_tasks"] = _task_tracker.count
        if _loop_lag_monitor:
            data["loop_lag_avg_ms"] = round(_loop_lag_monitor.avg_lag, 1)
            data["loop_lag_max_ms"] = round(_loop_lag_monitor.max_lag, 1)
        return data

    def print_stats(self) -> None:
        """Imprime as stats no log."""
        stats = self.get_stats()
        _logger.info("=" * 50)
        _logger.info("🚀 OPTIMIZER STATS v1.0")
        _logger.info("=" * 50)
        for k, v in stats.items():
            _logger.info(f"  {k}: {v}")
        _logger.info("=" * 50)


# ============================================================
# INSTÂNCIA GLOBAL (só existe UMA)
# ============================================================

_optimizer = GlobalOptimizer()


# ============================================================
# API PÚBLICA (o que o main.py e outros arquivos usam)
# ============================================================

async def start_optimizer(bot=None) -> None:
    """Chamado no setup_hook do main.py."""
    if bot is not None:
        _optimizer._bot = bot
    await _optimizer.start()


async def stop_optimizer() -> None:
    """Chamado no close do bot."""
    await _optimizer.stop()


def force_gc() -> None:
    """Força GC + limpa caches (usado em >gc)."""
    gc.collect(0)
    gc.collect(2)
    now = time.monotonic()
    try:
        from utils import GuildGate
        GlobalOptimizer._clean_one_cache(GuildGate._cache, now)
    except Exception:
        pass
    try:
        import embeds
        GlobalOptimizer._clean_one_cache(embeds._brand_cache, now)
    except Exception:
        pass


def get_optimizer_stats() -> dict:
    """Devolve stats pro comando >opt."""
    return _optimizer.get_stats()


def print_optimizer_stats() -> None:
    """Imprime as stats."""
    _optimizer.print_stats()


# Lista do que "exporta" (o que outros arquivos podem importar).
__all__ = [
    "GlobalOptimizer",
    "start_optimizer",
    "stop_optimizer",
    "force_gc",
    "get_optimizer_stats",
    "print_optimizer_stats",
    "DiscordRateLimiter",
    "TaskTracker",
    "UserNameCache",
    "LoopLagMonitor",
    "get_rate_limiter",
    "get_task_tracker",
    "get_user_name_cache",
    "get_loop_lag",
]