# ============================================================
# UTILS.PY - v9.0
# CACHE + GUILD GATE + RETRY + PARALLEL + HELPERS MOTOR
# ============================================================
# 📌 O QUE MUDOU NA v9.0?
# - ADICIONADO: helpers assíncronos pra facilitar a migração
#   do pymongo (síncrono) pro motor (assíncrono).
# - ADICIONADO: run_db() — executa sync fora do event loop
#   (fallback pra código legado que ainda não migrou).
# - ADICIONADO: await_db() — atalho pra pegar o banco.
# - ADICIONADO: to_async() — decorador sync→async.
#
# 📌 O QUE MANTÉM DA v8.0?
# - TTLCache (com lock)
# - CacheRegistry (centraliza caches)
# - GuildGate (controle por guild)
# - RateLimiter (token bucket)
# - retry_mongo (retry decorator)
# - run_parallel / run_parallel_chunked
# - safe_object_id
# - memory_mb / memory_guard / has_psutil
#
# 📌 TODOS OS COMENTÁRIOS ORIGINAIS FORAM MANTIDOS.
# ============================================================

# ────────────────────────────────────────────────────────────
# BLOCO 1 — IMPORTS (o que cada biblioteca faz)
# ────────────────────────────────────────────────────────────

# "from __future__ import annotations" = instrução especial do Python
# que faz ele "não se preocupar" com tipos de dados na hora de rodar.
# Isso permite usar tipos como "dict[str, int]" em versões antigas
# do Python (3.7, 3.8) sem dar erro. Não muda o comportamento do
# código, só permite escrever tipos mais modernos.
from __future__ import annotations

# "asyncio" = biblioteca padrão do Python pra rodar várias coisas
# "ao mesmo tempo" (assíncrono). É o coração de todo bot do Discord.
# Sem ela, o bot ficaria travado esperando uma resposta antes de
# fazer qualquer outra coisa.
import asyncio

# "gc" = "garbage collector" = coletor de lixo. É o "faxineiro"
# automático do Python. Ele libera memória de objetos que não
# são mais usados. Chamamos manualmente às vezes pra forçar limpeza.
import gc

# "threading" = biblioteca pra trabalhar com "threads" (linhas de
# execução paralelas). Usamos só pra ter um "Lock" (trava) que
# impede duas partes do código de mexer no mesmo cache ao mesmo tempo.
import threading

# "time" = biblioteca padrão pra lidar com tempo.
# Usamos "time.monotonic()" (relógio que só avança, nunca volta)
# pra medir quanto tempo passou. E "time.sleep()" pra pausar.
import time

# "OrderedDict" = dicionário que "lembra a ordem" em que as coisas
# foram inseridas. Usamos no cache pra saber qual item é o "mais
# antigo" e remover ele quando o cache enche.
from collections import OrderedDict

# "wraps" = decorador que preserva o nome/docstring da função
# original quando a gente "embrulha" ela em outro decorador.
# Sem isso, o "nome" da função vira o nome do decorador.
from functools import wraps

# "typing" = só pra "dizer" pro Python (e pra você) que tipo de
# dado uma função espera. Não muda o comportamento do código,
# só ajuda a entender e previne erros bobos.
from typing import Any, Awaitable, Callable, Dict, List, Optional

# ─── Imports opcionais (podem falhar sem quebrar o bot) ───

# "bson" = biblioteca do MongoDB. "ObjectId" é o tipo de ID que
# o MongoDB gera (uma string hexadecimal de 24 caracteres).
# "InvalidId" = erro que acontece quando você tenta converter
# uma string que não é um ObjectId válido.
try:
    from bson import ObjectId
    from bson.errors import InvalidId
    _HAS_BSON = True  # flag: "consegui importar bson"
except Exception:
    # Se falhar (banco não instalado), define como None
    # e a flag fica False. Assim o resto do código pode
    # checar antes de usar.
    _HAS_BSON = False
    ObjectId = None
    InvalidId = Exception

# "psutil" = biblioteca que dá informações do sistema (RAM, CPU).
# Usamos pra medir quanta memória o bot tá usando.
try:
    import psutil
    _HAS_PSUTIL = True
except Exception:
    _HAS_PSUTIL = False

# "discord" = biblioteca principal do Discord. Tudo que envolve
# mensagens, cargos, membros, servidores vem daqui.
try:
    import discord
    _HAS_DISCORD = True
except Exception:
    _HAS_DISCORD = False


# ════════════════════════════════════════════════════════════
# 🆕 BLOCO 1.5 — HELPERS MOTOR (NOVO NA v9.0)
# ════════════════════════════════════════════════════════════
# 📌 O QUE É ISSO?
# Helpers pra facilitar a vida quando você tem código que
# ainda usa pymongo (síncrono) e quer migrar pro motor (async).
#
# 📌 POR QUE ISSO EXISTE?
# Migrar TUDO de uma vez é arriscado. Os helpers permitem
# migrar por partes:
#   - Código novo → usa motor direto (await db[...])
#   - Código legado → usa run_db() pra rodar sync em thread

async def await_db():
    """
    Atalho assíncrono pra pegar o banco de dados.

    📌 USO:
        db = await await_db()
        doc = await db["collection"].find_one({"guild_id": 123})

    📌 DIFERENÇA DE get_connection():
    É só um atalho. Internamente chama get_connection().

    📌 POR QUE NÃO USAR DIRETO?
    Pra manter a API consistente. Se um dia mudarmos o
    database.py, você só precisa mudar aqui.
    """
    from database import get_connection_async
    return await get_connection_async()


async def run_db(func: Callable, *args, **kwargs):
    """
    Executa uma função SÍNCRONA do pymongo FORA do event loop.

    📌 O QUE É ISSO?
    Um "runner" que pega uma função síncrona (que bloqueia o
    event loop) e executa ela numa thread separada. Assim o
    bot não trava enquanto o Mongo responde.

    📌 QUANDO USAR?
    Quando você tem código que ainda usa pymongo direto e
    não pode migrar pra motor agora. Ex:

        # Código antigo (bloqueia):
        from database import get_connection
        db = get_connection()  # <- síncrono, errado!
        doc = db["collection"].find_one(...)

        # Código novo (não bloqueia):
        from utils import run_db
        doc = await run_db(
            lambda: get_connection()["collection"].find_one(...)
        )

    📌 IMPORTANTE:
    Isso é um PALIATIVO. O ideal é migrar pro motor de verdade.

    Args:
        func: função síncrona (callable) sem argumentos.
        *args, **kwargs: argumentos pra passar pra func.

    Returns:
        O resultado da função.
    """
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: func(*args, **kwargs))


def to_async(func: Callable) -> Callable:
    """
    Decorador que transforma uma função SÍNCRONA em ASSÍNCRONA.

    📌 O QUE É ISSO?
    Pega uma função que bloqueia (síncrona) e cria uma versão
    async dela que roda em thread separada.

    📌 QUANDO USAR?
    Quando você tem uma função utilitária que faz I/O síncrono
    (ex: ler arquivo, chamar API síncrona) e quer chamar ela de
    dentro de código async sem travar.

    Uso:
        @to_async
        def minha_funcao_sync():
            time.sleep(1)
            return "pronto"

        # Agora pode chamar com await:
        resultado = await minha_funcao_sync()

    📌 ATENÇÃO:
    Se a função já é async, o decorador não faz nada de útil
    (só repassa). Só use em funções síncronas.
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: func(*args, **kwargs)
        )
    return wrapper


# ────────────────────────────────────────────────────────────
# BLOCO 2 — FUNÇÃO AUXILIAR: safe_object_id
# ────────────────────────────────────────────────────────────
#
# O MongoDB usa um tipo especial de ID (ObjectId) que é uma
# string de 24 caracteres hexadecimais (ex: "507f1f77bcf86cd799439011").
#
# Se o usuário digitar um ID errado, o Python dá erro. Essa função
# tenta converter com segurança: se não conseguir, retorna None
# em vez de quebrar o bot.

def safe_object_id(value: Any) -> Optional[Any]:
    """
    Converte uma string em ObjectId (ID do MongoDB) com segurança.

    Uso:
        oid = safe_object_id("507f1f77bcf86cd799439011")
        if oid is None:
            print("ID inválido!")
        else:
            db["colecao"].find_one({"_id": oid})

    Args:
        value: pode ser string, ObjectId ou None

    Returns:
        Um ObjectId válido OU None se não conseguir converter.
    """
    # Se não tem bson (banco não instalado) ou value é None,
    # retorna None (não dá pra converter).
    if not _HAS_BSON or value is None:
        return None

    # Se JÁ é um ObjectId, retorna ele mesmo (nada a fazer).
    if isinstance(value, ObjectId):
        return value

    # Se é uma string de 24 caracteres, tenta converter.
    if isinstance(value, str) and len(value) == 24:
        try:
            return ObjectId(value)
        except (InvalidId, ValueError, TypeError):
            # Se não for hexadecimal válido, retorna None.
            return None

    # Qualquer outra coisa retorna None.
    return None


# ────────────────────────────────────────────────────────────
# BLOCO 3 — TTLCache (cache com tempo de vida)
# ────────────────────────────────────────────────────────────
#
# 📌 O QUE É UM CACHE?
#
# Imagina que você vai no banco de dados buscar a config do
# servidor TODA VEZ que alguém manda uma mensagem. Isso é LENTO.
#
# Um cache guarda o resultado em memória. Se pedir de novo em
# menos de X segundos, devolve da memória (instantâneo).
#
# 📌 O QUE É "TTL"?
#
# "Time To Live" = "tempo de vida". Cada item do cache tem um
# tempo de validade. Depois desse tempo, ele é considerado
# "expirado" e a próxima consulta vai no banco de novo.
#
# 📌 POR QUE ISSO IMPORTA?
#
# Sem cache, o bot ficaria lento e estouraria o limite do MongoDB.
# Com cache, fica 100x mais rápido.

class TTLCache:
    """
    Cache com tamanho máximo e tempo de vida (TTL).

    Uso:
        cache = TTLCache(max_size=100, ttl=60)
        cache.set("chave", "valor")
        valor = cache.get("chave")  # devolve "valor"
        # depois de 60s, cache.get() devolve None
    """

    # "__slots__" = otimização do Python. Diz quais atributos a
    # classe pode ter. Isso economiza memória (em vez de um
    # dicionário interno, usa slots fixos).
    __slots__ = ("max_size", "ttl", "_data", "_lock", "_hits", "_misses")

    def __init__(self, max_size: Optional[int] = None, ttl: Optional[int] = None):
        """
        Cria um cache novo.

        Args:
            max_size: número máximo de itens (se passar, remove o mais antigo)
            ttl: tempo de vida em segundos (depois disso, o item expira)
        """
        # Usa os padrões configuráveis quando o chamador não informar valores.
        if max_size is None or ttl is None:
            try:
                from config import CACHE_MAX_SIZE_DEFAULT, CACHE_TTL_DEFAULT
            except Exception:
                CACHE_MAX_SIZE_DEFAULT, CACHE_TTL_DEFAULT = 500, 60
            if max_size is None:
                max_size = CACHE_MAX_SIZE_DEFAULT
            if ttl is None:
                ttl = CACHE_TTL_DEFAULT
        self.max_size = max(1, int(max_size))
        self.ttl = max(1, int(ttl))

        # "_data" é onde os itens ficam guardados de verdade.
        # É um OrderedDict (dicionário que lembra a ordem).
        # Formato: {chave: (valor, tempo_de_expiracao)}
        self._data: OrderedDict = OrderedDict()

        # "_lock" é uma "trava" (Lock). Serve pra impedir que duas
        # partes do código mexam no cache ao mesmo tempo (evita bugs).
        # RLock = "reentrant lock" = a mesma thread pode pegar várias vezes.
        self._lock = threading.RLock()

        # Contadores de estatística:
        # "_hits" = quantas vezes achou no cache (bom!)
        # "_misses" = quantas vezes NÃO achou (foi no banco)
        self._hits = 0
        self._misses = 0

    def get(self, key):
        """
        Busca um valor no cache.

        Retorna:
            O valor guardado OU None se não existe / expirou.
        """
        # Pega o "tempo atual" (monotonic = relógio que só avança).
        now = time.monotonic()

        # Pega a trava (lock) pra ninguém mexer no cache agora.
        # "with" garante que a trava é liberada no final, mesmo
        # se der erro no meio.
        with self._lock:
            # Tenta pegar o item do dicionário.
            item = self._data.get(key)

            # Se não existe, conta como "miss" (não achou) e retorna None.
            if item is None:
                self._misses += 1
                return None

            # Desempacota: "value" é o valor, "expires" é quando expira.
            value, expires = item

            # Se o tempo atual JÁ PASSOU do tempo de expiração,
            # o item está vencido. Remove e conta como "miss".
            if now > expires:
                del self._data[key]
                self._misses += 1
                return None

            # Item válido! Move pro "final" da fila (isso marca ele
            # como "usado recentemente", então ele demora mais pra
            # ser removido se o cache encher).
            self._data.move_to_end(key)

            # Conta como "hit" (achou!).
            self._hits += 1

            # Devolve o valor.
            return value

    def set(self, key, value):
        """
        Guarda um valor no cache.

        Args:
            key: chave (ex: ID do servidor)
            value: valor a guardar (ex: config do servidor)
        """
        with self._lock:
            # Se a chave já existe, move pro final (marca como recente).
            if key in self._data:
                self._data.move_to_end(key)

            # Guarda o valor JUNTO com o tempo de expiração.
            # "time.monotonic() + self.ttl" = "agora + X segundos".
            self._data[key] = (value, time.monotonic() + self.ttl)

            # Se passou do tamanho máximo, remove o MAIS ANTIGO.
            # "popitem(last=False)" remove o primeiro (mais antigo).
            if len(self._data) > self.max_size:
                self._data.popitem(last=False)

    def invalidate(self, key):
        """
        Remove UM item específico do cache.

        Usado quando algo mudou e o cache ficou "desatualizado".
        Ex: admin mudou a config → invalida o cache daquele servidor.
        """
        with self._lock:
            self._data.pop(key, None)  # None = não dá erro se não existir

    def invalidate_prefix(self, prefix: str):
        """
        Remove TODOS os itens cuja chave começa com "prefix".

        Ex: invalidate_prefix("guild_") remove "guild_123", "guild_456".
        """
        p = str(prefix)
        with self._lock:
            # Cria uma lista de chaves que começam com o prefixo.
            # (precisa ser lista separada, porque não pode modificar
            # o dicionário durante o loop)
            for k in [k for k in self._data if str(k).startswith(p)]:
                del self._data[k]

    def clear(self):
        """
        Limpa TUDO do cache e zera os contadores.

        Usado em emergências (ex: admin quer forçar recarga total).
        """
        with self._lock:
            self._data.clear()
            self._hits = 0
            self._misses = 0

    @property
    def stats(self) -> dict:
        """
        Devolve estatísticas do cache (quantos itens, hits, misses...).

        "@property" = permite chamar "cache.stats" (sem parênteses)
        em vez de "cache.stats()".
        """
        with self._lock:
            # Total de consultas = hits + misses.
            total = self._hits + self._misses
            return {
                "size": len(self._data),        # quantos itens tem agora
                "max": self.max_size,           # tamanho máximo
                "hits": self._hits,             # acertos
                "misses": self._misses,         # erros
                # Hit rate = % de acertos. round(...,3) = 3 casas decimais.
                "hit_rate": round(self._hits / total, 3) if total else 0.0,
            }


# ────────────────────────────────────────────────────────────
# BLOCO 4 — CACHE REGISTRY (registro central de caches)
# ────────────────────────────────────────────────────────────
#
# 📌 POR QUE ISSO EXISTE?
#
# Se cada arquivo cria seu próprio cache, fica uma bagunça.
# Ninguém sabe quantos caches existem, nem consegue limpá-los
# todos de uma vez.
#
# O CacheRegistry é um "registro central": cada arquivo pede
# seu cache pra ele. Assim, o otimizador (optimizer.py) consegue
# ver TODOS os caches e limpá-los de uma vez.

class CacheRegistry:
    """
    Registro central de caches por nome.

    Uso:
        cache = CacheRegistry.get("meu_cache", max_size=500, ttl=60)
        cache.set("chave", valor)
        valor = cache.get("chave")

    O optimizer aplica patches globais em todos os caches aqui.
    """

    # Dicionário: {nome_do_cache: objeto_TTLCache}
    _caches: Dict[str, TTLCache] = {}

    # Trava pra ninguém mexer no registro ao mesmo tempo.
    _lock = threading.RLock()

    @classmethod
    def get(cls, name: str, max_size: int = 300,
            ttl: int = 60) -> TTLCache:
        """
        Retorna (ou cria, se não existir) um cache nomeado.

        Se o cache com esse nome JÁ EXISTE, devolve ele.
        Se NÃO EXISTE, cria um novo com max_size e ttl fornecidos.

        Args:
            name: nome único do cache (ex: "antinuke_cfg")
            max_size: tamanho máximo (só usado se criar novo)
            ttl: tempo de vida (só usado se criar novo)
        """
        with cls._lock:
            # Tenta pegar o cache existente.
            cache = cls._caches.get(name)

            # Se não existe, cria um novo e registra.
            if cache is None:
                cache = TTLCache(max_size=max_size, ttl=ttl)
                cls._caches[name] = cache

            return cache

    @classmethod
    def invalidate_all(cls):
        """
        Invalida (limpa) TODOS os caches registrados.

        Usado pelo comando >opt cache clear.
        """
        with cls._lock:
            for cache in cls._caches.values():
                cache.clear()

    @classmethod
    def clean_expired(cls) -> int:
        """
        Remove entradas expiradas de TODOS os caches.

        Retorna:
            Número total de entradas removidas.
        """
        # Pega o tempo atual uma vez só (economia).
        now = time.monotonic()
        removed = 0

        with cls._lock:
            for cache in cls._caches.values():
                try:
                    # Pega a trava interna do cache.
                    with cache._lock:
                        # Lista das chaves expiradas.
                        expired = [
                            k for k, (v, exp) in cache._data.items()
                            if now > exp
                        ]
                        # Remove uma a uma.
                        for k in expired:
                            del cache._data[k]
                            removed += 1
                except Exception:
                    # Se der erro em um cache, continua com os outros.
                    pass

        return removed

    @classmethod
    def stats(cls) -> dict:
        """
        Retorna estatísticas de TODOS os caches.

        Retorna:
            {"nome_do_cache": {stats}, ...}
        """
        with cls._lock:
            out = {}
            for name, cache in cls._caches.items():
                out[name] = cache.stats
            return out

    @classmethod
    def names(cls) -> list:
        """
        Retorna a lista de nomes de todos os caches registrados.
        """
        with cls._lock:
            return list(cls._caches.keys())


# ────────────────────────────────────────────────────────────
# BLOCO 5 — RETRY MONGO (tentativa automática em erro)
# ────────────────────────────────────────────────────────────
#
# 📌 O QUE É RETRY?
#
# Se uma operação no MongoDB falhar (rede caiu, servidor lento),
# em vez de dar erro direto, tenta de novo. Isso se chama "retry".
#
# O decorador @retry_mongo(attempts=3) faz a função tentar
# até 3 vezes com uma pequena pausa entre elas.
#
# 📌 IMPORTANTE NA v9.0:
# Esse decorador funciona tanto pra funções SÍNCRONAS quanto
# pra funções ASSÍNCRONAS. Ele detecta automaticamente.

def retry_mongo(attempts: int = 3, base_delay: float = 0.08):
    """
    Decorador que tenta novamente se uma operação MongoDB falhar.

    Uso (sync):
        @retry_mongo(attempts=3)
        def buscar_dados():
            return db["colecao"].find_one(...)

    Uso (async):
        @retry_mongo(attempts=3)
        async def buscar_dados():
            return await db["colecao"].find_one(...)

    Args:
        attempts: número total de tentativas (padrão 3)
        base_delay: pausa base entre tentativas em segundos

    Como funciona:
        - Tenta executar a função
        - Se der erro, espera "base_delay * 2^i" segundos
          (backoff exponencial: 0.08s, 0.16s, 0.32s...)
        - Tenta de novo
        - Se esgotar as tentativas, lança o último erro
    """
    def decorator(func: Callable):
        # Verifica se a função é async (coroutine function).
        is_async = asyncio.iscoroutinefunction(func)

        if is_async:
            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                last_exc = None
                for i in range(attempts):
                    try:
                        return await func(*args, **kwargs)
                    except Exception as e:
                        last_exc = e
                        if i < attempts - 1:
                            await asyncio.sleep(base_delay * (2 ** i))
                raise last_exc
            return async_wrapper
        else:
            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                last_exc = None
                for i in range(attempts):
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        last_exc = e
                        if i < attempts - 1:
                            time.sleep(base_delay * (2 ** i))
                raise last_exc
            return sync_wrapper

    return decorator


# ────────────────────────────────────────────────────────────
# BLOCO 6 — RATE LIMITER (limitador de velocidade)
# ────────────────────────────────────────────────────────────
#
# 📌 O QUE É ISSO?
#
# Impede que alguém use um comando MUITAS VEZES em pouco tempo.
# Ex: "no máximo 3 comandos a cada 5 segundos".
#
# 📌 COMO FUNCIONA (token bucket)?
#
# Imagina um balde com fichas. Cada vez que você usa um comando,
# gasta uma ficha. As fichas vão sendo repostas aos poucos com
# o tempo. Se acabar as fichas, você tem que esperar.

class RateLimiter:
    """
    Limitador de taxa (rate limit) por chave.

    Uso:
        rl = RateLimiter(rate=3, per=5.0)  # 3 comandos a cada 5s
        if rl.allow("user_123"):
            # executa o comando
        else:
            # nega: "calma aí, tá rápido demais"
    """

    __slots__ = ("rate", "per", "_buckets", "_lock")

    def __init__(self, rate: int = 3, per: float = 5.0):
        """
        Args:
            rate: quantas ações são permitidas em "per" segundos
            per: janela de tempo em segundos
        """
        self.rate = rate
        self.per = per

        # Dicionário de "baldes" (um pra cada chave).
        # Formato: {chave: [ultimo_timestamp, fichas_restantes]}
        self._buckets: dict = {}

        # Trava pra evitar conflito entre threads.
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        """
        Verifica se a chave pode fazer uma ação agora.

        Args:
            key: identificador (ex: "user_123:kick")

        Returns:
            True = permitido, False = bloqueado (tá rápido demais)
        """
        # Pega o tempo atual.
        now = time.monotonic()

        with self._lock:
            # Tenta pegar o balde da chave.
            entry = self._buckets.get(key)

            # Se não existe (primeira vez), cria com o rate cheio.
            if entry is None:
                self._buckets[key] = [now, self.rate - 1]

                # Limpeza: se tem MUITAS chaves, remove as antigas.
                # Isso evita o dicionário crescer infinitamente.
                if len(self._buckets) > 5000:
                    cutoff = now - self.per * 4  # itens mais velhos que isso
                    for k in [k for k, v in self._buckets.items()
                              if v[0] < cutoff]:
                        del self._buckets[k]

                return True

            # Desempacota: "last" = última vez, "tokens" = fichas restantes.
            last, tokens = entry

            # Repõe fichas com base no tempo passado.
            # Se passou X segundos, adiciona "X * taxa_por_segundo" fichas.
            tokens = min(self.rate,
                         tokens + (now - last) * (self.rate / self.per))

            # Se não tem nem 1 ficha, bloqueia.
            if tokens < 1:
                entry[0] = now
                entry[1] = tokens
                return False

            # Tem ficha! Gasta uma e permite.
            entry[0] = now
            entry[1] = tokens - 1
            return True


# ────────────────────────────────────────────────────────────
# BLOCO 7 — HELPERS DE MEMÓRIA
# ────────────────────────────────────────────────────────────
#
# Funções pra medir quanto de RAM o bot tá usando e forçar
# limpeza se passar do limite.

# Pega o "processo" atual do Python (pra poder medir memória).
# Se psutil não tá instalado, fica None.
_PROCESS = psutil.Process() if _HAS_PSUTIL else None


def memory_mb() -> float:
    """
    Retorna quanta RAM o bot está usando, em MB.

    Se psutil não está instalado, retorna 0.0.
    """
    if _PROCESS is None:
        return 0.0
    try:
        # memory_info().rss = "Resident Set Size" = RAM "real" usada.
        # Divide por (1024*1024) pra converter de bytes pra MB.
        return _PROCESS.memory_info().rss / (1024 * 1024)
    except Exception:
        return 0.0


def memory_guard(threshold_mb: float = 380.0) -> bool:
    """
    Verifica se a memória passou do limite e força GC.

    Args:
        threshold_mb: limite em MB (padrão 380)

    Returns:
        True se precisou fazer GC, False se tá tudo ok.
    """
    if _PROCESS is None:
        return False

    # Se passou do limite, roda o coletor de lixo (gc.collect).
    if memory_mb() >= threshold_mb:
        gc.collect(2)  # 2 = coleta completa (mais pesada)
        return True

    return False


def has_psutil() -> bool:
    """
    Diz se a biblioteca psutil está disponível.

    Uso: em comandos como >health, pra mostrar "psutil: ✅" ou "❌".
    """
    return _HAS_PSUTIL


# ────────────────────────────────────────────────────────────
# BLOCO 8 — RUN PARALLEL (execução paralela com retry)
# ────────────────────────────────────────────────────────────
#
# 📌 O QUE É ISSO?
#
# Antigamente, comandos como ">hideall" (esconder todos os canais)
# faziam um canal por vez:
#   canal1.set_permissions() → espera
#   canal2.set_permissions() → espera
#   canal3.set_permissions() → espera
# Resultado: 30 canais = 15 segundos.
#
# Com run_parallel(), faz VÁRIOS ao mesmo tempo:
#   canal1, canal2, canal3, canal4, canal5 → tudo junto
# Resultado: 30 canais = 3 segundos.
#
# 📌 POR QUE "SEMÁFORO"?
#
# Se a gente fizer 30 ao mesmo tempo, o Discord vai bloquear
# (rate limit). Então limitamos a "5 ao mesmo tempo", o que é
# rápido mas seguro.

# Lista de códigos HTTP que valem retry (rate limit / servidor caiu).
# - 429 = rate limit (muito rápido)
# - 500, 502, 503, 504 = erros internos do Discord
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)


async def run_parallel(
    items: List[Any],
    worker: Callable,
    *,
    concurrency: int = 5,
    retry: int = 2,
    retry_base_delay: float = 0.4,
    cancel_event: Optional[asyncio.Event] = None,
    on_progress: Optional[Callable] = None,
    on_error: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Executa worker(item) para cada item, EM PARALELO, com retry.

    Args:
        items: lista de itens (ex: lista de canais)
        worker: função ASYNC que recebe 1 item e faz o trabalho
        concurrency: máximo de workers rodando ao mesmo tempo
        retry: quantas tentativas extras em caso de erro "retryable"
        retry_base_delay: tempo base de espera entre retries
        cancel_event: asyncio.Event pra poder cancelar no meio
        on_progress: função chamada a cada item concluído
        on_error: função chamada quando um item falha de vez

    Returns:
        {"ok": N, "fail": N, "cancelled": bool, "errors": [...]}
    """
    # Se não tem itens, devolve zerado.
    if not items:
        return {"ok": 0, "fail": 0, "cancelled": False, "errors": []}

    # "Semaphore" = trava que permite no máximo N ao mesmo tempo.
    # max(1, concurrency) garante que é pelo menos 1.
    sem = asyncio.Semaphore(max(1, concurrency))

    # Dicionário pra guardar os resultados.
    stats = {"ok": 0, "fail": 0, "cancelled": False, "errors": []}

    async def _one(item):
        """
        Processa UM item (com retry). Roda em paralelo com outros.
        """
        # Se o cancelamento foi pedido, para tudo.
        if cancel_event and cancel_event.is_set():
            stats["cancelled"] = True
            return

        # Pega o semáforo (bloqueia se já tem N rodando).
        async with sem:
            # Checa de novo se cancelou enquanto esperava.
            if cancel_event and cancel_event.is_set():
                stats["cancelled"] = True
                return

            # Loop de tentativas: até (retry + 1) tentativas no total.
            for attempt in range(retry + 1):
                try:
                    # Tenta executar o worker.
                    await worker(item)

                    # Sucesso! Incrementa e sai do loop.
                    stats["ok"] += 1
                    break

                except Exception as e:
                    # Erro! Vamos ver se vale tentar de novo.
                    is_retryable = False

                    # Se é um erro do Discord (HTTPException)...
                    if _HAS_DISCORD and isinstance(e, discord.HTTPException):
                        # E o status está na lista de "retryable"...
                        if e.status in _RETRYABLE_STATUS:
                            is_retryable = True

                    # Timeout ou erro de conexão também valem retry.
                    elif isinstance(e, (asyncio.TimeoutError, ConnectionError)):
                        is_retryable = True

                    # Se vale retry E ainda tem tentativas, espera e tenta de novo.
                    if is_retryable and attempt < retry:
                        # Backoff exponencial: 0.4 → 0.8 → 1.6 segundos.
                        await asyncio.sleep(retry_base_delay * (2 ** attempt))
                        continue

                    # Falha definitiva.
                    stats["fail"] += 1

                    # Guarda o erro (só os 20 primeiros, pra não lotar).
                    if len(stats["errors"]) < 20:
                        stats["errors"].append((item, str(e)))

                    # Chama o callback de erro se fornecido.
                    if on_error:
                        try:
                            on_error(item, e)
                        except Exception:
                            pass

                    break

            # Depois de processar, chama o callback de progresso.
            if on_progress:
                try:
                    on_progress()
                except Exception:
                    pass

    # Cria uma "task" (tarefa paralela) pra cada item.
    tasks = [asyncio.create_task(_one(it)) for it in items]

    # Espera todas terminarem.
    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except asyncio.CancelledError:
        # Se cancelado, cancela todas as tasks.
        for t in tasks:
            t.cancel()
        raise

    return stats


async def run_parallel_chunked(
    items: List[Any],
    worker: Callable,
    *,
    chunk_size: int = 50,
    concurrency: int = 5,
    retry: int = 2,
    cancel_event: Optional[asyncio.Event] = None,
    on_progress: Optional[Callable] = None,
) -> Dict[str, Any]:
    """
    Como run_parallel, mas processa em CHUNKS (pedaços) pra não
    criar milhares de tasks de uma vez.

    Ex: 1000 itens com chunk_size=50 → processa 50 por vez,
        20 chunks no total.

    Útil pra listas GIGANTES (ex: membros de um servidor com 50k).
    """
    total_ok = 0
    total_fail = 0
    cancelled = False
    errors: List = []

    # Loop pelos chunks: 0-50, 50-100, 100-150...
    for i in range(0, len(items), chunk_size):
        # Se cancelou, para tudo.
        if cancel_event and cancel_event.is_set():
            cancelled = True
            break

        # Pega o pedaço atual.
        chunk = items[i:i + chunk_size]

        # Processa o pedaço em paralelo.
        res = await run_parallel(
            chunk, worker,
            concurrency=concurrency,
            retry=retry,
            cancel_event=cancel_event,
            on_progress=on_progress,
        )

        # Acumula os resultados.
        total_ok += res["ok"]
        total_fail += res["fail"]

        if res["cancelled"]:
            cancelled = True

        # Adiciona os erros (limita a 20 no total).
        errors.extend(res["errors"][:max(0, 20 - len(errors))])

    return {
        "ok": total_ok,
        "fail": total_fail,
        "cancelled": cancelled,
        "errors": errors,
    }


# ────────────────────────────────────────────────────────────
# BLOCO 9 — GUILD GATE (controle por servidor)
# ────────────────────────────────────────────────────────────
#
# 📌 O QUE É ISSO?
#
# Guarda todas as configurações "especiais" de cada servidor:
# - Prefixo customizado (ex: em vez de ">", usar "!")
# - Módulos ativos/desativados (economia, moderação, etc)
# - Comandos desativados
# - Manutenção ligada/desligada
# - Cargos/usuários com "bypass" (ignoram tudo)
#
# 📌 POR QUE CACHE?
#
# Porque o bot checa isso TODA VEZ que um comando é usado.
# Sem cache, ficaria lento. Com cache, é instantâneo.

class GuildGate:
    """
    Controla as configurações por servidor (guild).
    """

    # Cache de configs (nome do servidor → config).
    # TTL de 60s: se mudar algo, demora até 60s pra atualizar.
    # (mas chamamos invalidate() quando mudamos, então é imediato)
    _cache = TTLCache(max_size=200, ttl=60)

    # Trava pra evitar conflitos.
    _lock = threading.RLock()

    # Módulos PADRÃO (ligados por padrão).
    # Cada chave é um módulo, o valor é True/False.
    DEFAULT_MODULES = {
        "economy": True, "earn": True, "shop": True, "games": True,
        "gambling": True, "events": True, "achievements": True,
        "staff": True, "automod": False, "antinuke": False,
        "cases": True, "social": True, "market": True,
        "missions": True, "sinks": True, "backup": True, "branding": True,
    }

    @classmethod
    def _fetch(cls, guild_id: int) -> dict:
        """
        Busca a config do servidor (do cache OU do banco).

        📌 IMPORTANTE NA v9.0:
        Essa função é SÍNCRONA. Usa run_db() pra rodar a query
        do pymongo em thread separada se precisar.

        ⚠️ ISSO É UM PALIATIVO.
        O ideal é migrar o GuildGate pra motor também. Mas como
        ele é chamado em MUITOS lugares (e alguns deles são
        síncronos), mantivemos sincronamente por enquanto.

        📌 TODO v13.1: Migrar GuildGate pra motor.
        """
        # Tenta pegar do cache.
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached

        # Não tá no cache → busca do banco.
        # (import dentro da função pra evitar import circular)
        try:
            from database import get_connection
            # Usa o cliente PyMongo compatível com os módulos síncronos.
            db_sync = get_connection()
        except Exception:
            return cls._get_default_state()

        doc = db_sync["control_config"].find_one({"guild_id": guild_id}) or {}

        # Monta o "estado" (mistura padrões com o que tá salvo).
        state = {
            # Módulos: começa com os padrões, sobrescreve com o que tá salvo.
            "modules": {**cls.DEFAULT_MODULES, **(doc.get("modules") or {})},

            # Comandos desativados: converte lista pra set (busca rápida).
            "disabled_commands": set(doc.get("disabled_commands") or []),

            # Canais onde comandos estão desativados.
            "disabled_channels": {
                k: set(v) for k, v in
                (doc.get("disabled_channels") or {}).items()
            },

            # Prefixo customizado (None = usar o padrão).
            "prefix": doc.get("prefix") or None,

            # Modo manutenção (True = só admin usa).
            "maintenance": bool(doc.get("maintenance", False)),

            # Cargos/usuários que ignoram restrições.
            "bypass_roles": set(doc.get("bypass_roles") or []),
            "bypass_users": set(doc.get("bypass_users") or []),
        }

        # Guarda no cache pra próxima vez.
        cls._cache.set(guild_id, state)
        return state

    @classmethod
    def _get_default_state(cls) -> dict:
        """Estado padrão (usado quando não conseguimos buscar do banco)."""
        return {
            "modules": dict(cls.DEFAULT_MODULES),
            "disabled_commands": set(),
            "disabled_channels": {},
            "prefix": None,
            "maintenance": False,
            "bypass_roles": set(),
            "bypass_users": set(),
        }

    @classmethod
    def invalidate(cls, guild_id: int):
        """
        Força recarregar a config do servidor na próxima consulta.

        Usado quando algo muda (ex: admin mudou prefixo).
        """
        cls._cache.invalidate(guild_id)

    @classmethod
    def get_prefix(cls, guild_id: int, default: str = ">") -> str:
        """
        Retorna o prefixo do servidor (ex: ">", "!", "?").
        Se não tem customizado, usa o padrão.
        """
        return cls._fetch(guild_id)["prefix"] or default

    @classmethod
    def is_module_on(cls, guild_id: int, module: str) -> bool:
        """
        Diz se um módulo está ligado no servidor.
        Ex: is_module_on(123, "staff") → True
        """
        return bool(cls._fetch(guild_id)["modules"].get(module, True))

    @classmethod
    def is_command_off(cls, guild_id: int, command: str) -> bool:
        """
        Diz se um comando está desativado no servidor.
        Ex: is_command_off(123, "kick") → True se kick está off
        """
        return command in cls._fetch(guild_id)["disabled_commands"]

    @classmethod
    def is_command_off_channel(cls, guild_id: int, command: str,
                                channel_id: int) -> bool:
        """
        Diz se um comando está desativado em UM CANAL específico.
        Ex: kick desativado só no #geral
        """
        chans = cls._fetch(guild_id)["disabled_channels"].get(command)
        return bool(chans and channel_id in chans)

    @classmethod
    def is_maintenance(cls, guild_id: int) -> bool:
        """
        Diz se o servidor tá em modo manutenção.
        Em manutenção, só admin usa.
        """
        return cls._fetch(guild_id)["maintenance"]

    @classmethod
    def is_bypassed(cls, guild_id: int, user_id: int,
                     role_ids: list) -> bool:
        """
        Diz se um usuário (ou um dos cargos dele) tem "bypass".

        Bypass = ignora TODAS as restrições (manutenção, comandos off).
        """
        state = cls._fetch(guild_id)

        # Se o ID do user tá na lista, tem bypass.
        if user_id in state["bypass_users"]:
            return True

        # Se algum cargo dele tá na lista, tem bypass.
        return any(rid in state["bypass_roles"] for rid in role_ids)

    @classmethod
    def get_module_state(cls, guild_id: int) -> dict:
        """
        Retorna o estado de TODOS os módulos.
        Ex: {"staff": True, "automod": False, ...}
        """
        return dict(cls._fetch(guild_id)["modules"])

    @classmethod
    def get_disabled_commands(cls, guild_id: int) -> set:
        """
        Retorna o conjunto de comandos desativados.
        """
        return set(cls._fetch(guild_id)["disabled_commands"])

    @classmethod
    def get_bypass_roles(cls, guild_id: int) -> set:
        """
        Retorna o conjunto de IDs de cargos com bypass.
        """
        return set(cls._fetch(guild_id)["bypass_roles"])

    @classmethod
    def get_bypass_users(cls, guild_id: int) -> set:
        """
        Retorna o conjunto de IDs de usuários com bypass.
        """
        return set(cls._fetch(guild_id)["bypass_users"])

    @classmethod
    def set_module(cls, guild_id: int, module: str, enabled: bool):
        """
        Liga/desliga um módulo.
        Salva no banco e invalida o cache.

        📌 IMPORTANTE NA v9.0:
        Essa função é SÍNCRONA. Usa o client síncrono do motor
        (delegate) pra salvar. É um paliativo — o ideal é
        migrar tudo pra async eventualmente.
        """
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            db["control_config"].update_one(
                {"guild_id": guild_id},
                {"$set": {f"modules.{module}": enabled}},  # caminho aninhado
                upsert=True  # cria se não existir
            )
            cls.invalidate(guild_id)  # recarrega na próxima consulta
        except Exception:
            pass

    @classmethod
    def set_command(cls, guild_id: int, command: str, disabled: bool):
        """
        Ativa/desativa um comando no servidor.
        """
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]

            if disabled:
                # Adiciona à lista de desativados (addToSet = só se não existir).
                db["control_config"].update_one(
                    {"guild_id": guild_id},
                    {"$addToSet": {"disabled_commands": command}},
                    upsert=True
                )
            else:
                # Remove da lista (pull = remove todos os que batem).
                db["control_config"].update_one(
                    {"guild_id": guild_id},
                    {"$pull": {"disabled_commands": command}},
                    upsert=True
                )

            cls.invalidate(guild_id)
        except Exception:
            pass

    @classmethod
    def set_channel_lock(cls, guild_id: int, command: str,
                          channel_id: int, disabled: bool):
        """
        Ativa/desativa um comando em um canal específico.
        """
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]

            # Caminho aninhado: "disabled_channels.kick" = [123, 456]
            key = f"disabled_channels.{command}"

            if disabled:
                db["control_config"].update_one(
                    {"guild_id": guild_id},
                    {"$addToSet": {key: channel_id}},
                    upsert=True
                )
            else:
                db["control_config"].update_one(
                    {"guild_id": guild_id},
                    {"$pull": {key: channel_id}},
                    upsert=True
                )

            cls.invalidate(guild_id)
        except Exception:
            pass

    @classmethod
    def set_prefix(cls, guild_id: int, prefix: Optional[str]):
        """
        Define o prefixo do servidor (ex: "!", "?").
        Se prefix=None, remove (volta pro padrão).
        """
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]

            if prefix:
                db["control_config"].update_one(
                    {"guild_id": guild_id},
                    {"$set": {"prefix": prefix}},
                    upsert=True
                )
            else:
                # $unset = remove o campo.
                db["control_config"].update_one(
                    {"guild_id": guild_id},
                    {"$unset": {"prefix": ""}},
                    upsert=True
                )

            cls.invalidate(guild_id)
        except Exception:
            pass

    @classmethod
    def set_maintenance(cls, guild_id: int, enabled: bool):
        """
        Liga/desliga o modo manutenção.
        Em manutenção, só admin usa comandos.
        """
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            db["control_config"].update_one(
                {"guild_id": guild_id},
                {"$set": {"maintenance": enabled}},
                upsert=True
            )
            cls.invalidate(guild_id)
        except Exception:
            pass

    @classmethod
    def add_bypass(cls, guild_id: int, kind: str, target_id: int):
        """
        Adiciona um cargo (kind="role") ou user (kind="user") à lista
        de bypass (ignoram todas as restrições).
        """
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            # Escolhe a chave correta.
            key = "bypass_roles" if kind == "role" else "bypass_users"
            db["control_config"].update_one(
                {"guild_id": guild_id},
                {"$addToSet": {key: target_id}},
                upsert=True
            )
            cls.invalidate(guild_id)
        except Exception:
            pass

    @classmethod
    def remove_bypass(cls, guild_id: int, kind: str, target_id: int):
        """
        Remove um cargo/user da lista de bypass.
        """
        try:
            from database import _client as _motor_client
            if _motor_client is None:
                return
            db = _motor_client.delegate["gt_bot_moderacao"]
            key = "bypass_roles" if kind == "role" else "bypass_users"
            db["control_config"].update_one(
                {"guild_id": guild_id},
                {"$pull": {key: target_id}},
                upsert=True
            )
            cls.invalidate(guild_id)
        except Exception:
            pass