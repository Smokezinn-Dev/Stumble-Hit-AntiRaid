# ============================================================
# DATABASE.PY - v13.0
# MIGRAÇÃO PARA MOTOR (DRIVER ASSÍNCRONO)
# ============================================================
# 📌 O QUE MUDOU NA v13.0?
# - MIGRAÇÃO CRÍTICA: pymongo (síncrono) → motor (assíncrono).
#   Isso resolve o problema do MongoDB bloqueando o event loop.
#
# 📌 POR QUE ISSO IMPORTA?
# O pymongo é SÍNCRONO. Quando você faz:
#   db["collection"].find_one(...)
# o Python fica PARADO esperando o Mongo responder. Isso trava
# o event loop do bot e afeta TODAS as outras operações.
#
# O motor é ASSÍNCRONO. Quando você faz:
#   await db["collection"].find_one(...)
# o Python pode processar outras coisas enquanto espera. Isso
# mantém o loop livre, o bot responde mais rápido e o lag cai.
#
# 📌 O QUE MUDOU NA PRÁTICA?
# - MongoClient → AsyncIOMotorClient
# - get_connection() agora é `async def` → precisa de `await`
# - init_db(), warmup(), ensure_indexes() viraram async
# - log_privilege_change() e create_privilege_alert() viraram async
# - get_pool_stats() virou async
# - Adicionado get_db() (atalho pra pegar o banco)
#
# 📌 O QUE MANTÉM DA v12.4?
# - TODOS os índices (nenhum foi removido)
# - TTL das coleções
# - Pool tuning (adaptado pro motor)
# - TODOS os comentários originais
# ============================================================

# ─── BLOCO 1 — IMPORTS ───

import asyncio

# "motor" = driver ASSÍNCRONO oficial do MongoDB.
# A API é IDÊNTICA ao pymongo, mas todas as operações são `await`.
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

# "pymongo" = ainda usamos pra pegar as constantes (ASCENDING, etc)
# e pra capturar exceções específicas.
from pymongo import ASCENDING, DESCENDING, TEXT
from pymongo.errors import (
    ConnectionFailure,
    ServerSelectionTimeoutError,
    OperationFailure,
)

# Configurações lidas do ambiente.
from config import MONGODB_URL, DB_NAME


# ============================================================
# BLOCO 2 — CONEXÃO (agora assíncrona)
# ============================================================
# 📌 DIFERENÇA DO PYMONGO:
# Antes, o MongoClient era criado no import do módulo (síncrono).
# Agora, criamos ele dentro de uma função async, porque o motor
# precisa estar "dentro" de um event loop pra funcionar.
#
# A conexão em si é LAZY: o motor só conecta de verdade na
# primeira operação. Então criar o client é instantâneo.

# Guarda o client globalmente (só existe um por processo).
_client: AsyncIOMotorClient = None
# Client síncrono para os módulos legados que usam a API PyMongo diretamente.
# A base do projeto contém chamadas síncronas em vários Cogs; manter apenas
# Motor (assíncrono) faria get_connection() devolver uma coroutine nesses Cogs.
_sync_client: MongoClient = None

# Número máximo de tentativas de conexão.
_MAX_ATTEMPTS = 4

# Delay entre tentativas (segundos).
_RETRY_DELAY = 4


async def get_mongo_client() -> AsyncIOMotorClient:
    """
    Abre conexão com o MongoDB, com retry automático.
    Retorna um AsyncIOMotorClient pronto pra uso.

    📌 POR QUE ASYNC?
    Porque o motor precisa rodar dentro de um event loop.
    Chamar essa função de dentro do setup_hook do bot é o ideal.

    📌 POR QUE RETRY?
    Redes caem, servidores ficam lentos. Se falhar, tenta de
    novo até N vezes antes de desistir.
    """
    global _client

    # Se já tem um client aberto, devolve ele (singleton).
    if _client is not None:
        return _client

    attempt = 0
    last_exc = None

    while attempt < _MAX_ATTEMPTS:
        try:
            # Cria o client com as configurações de pool.
            # As opções são as MESMAS do pymongo, porque o motor
            # usa o pymongo por baixo dos panos.
            client = AsyncIOMotorClient(
                MONGODB_URL,
                # Timeout pra achar um servidor disponível.
                serverSelectionTimeoutMS=10000,
                # Timeout pra abrir conexão TCP.
                connectTimeoutMS=10000,
                # Timeout pra operações de socket.
                socketTimeoutMS=20000,
                # Tamanho máximo do pool de conexões.
                maxPoolSize=20,
                # Tamanho mínimo (conexões sempre abertas).
                minPoolSize=5,
                # Tempo máximo que uma conexão pode ficar ociosa.
                maxIdleTimeMS=300000,
                # Timeout pra pegar conexão do pool.
                waitQueueTimeoutMS=5000,
                # Reenvia writes se der erro de rede.
                retryWrites=True,
                # Nível de confirmação: 1 = confirma no primário só.
                w=1,
                # Não espera gravar no journal (mais rápido).
                journal=False,
                # Prefere ler do primário.
                readPreference="primaryPreferred",
                # De quanto em quanto tempo checa se o servidor tá vivo.
                heartbeatFrequencyMS=30000,
                # Nome do app (aparece no MongoDB Atlas).
                appname="GTBotMod-v13.0",
            )

            # Testa se conseguiu conectar de verdade (ping).
            await client.admin.command("ping")

            # Guarda globalmente e devolve.
            _client = client
            return client

        except (ConnectionFailure, ServerSelectionTimeoutError) as e:
            attempt += 1
            last_exc = e
            if attempt < _MAX_ATTEMPTS:
                # Backoff: 4s, 8s, 12s...
                await asyncio.sleep(_RETRY_DELAY * attempt)

    # Chegou aqui = todas as tentativas falharam.
    raise ConnectionError(f"Falha ao conectar MongoDB: {last_exc}")


def get_connection():
    """Retorna o banco PyMongo síncrono usado pelos Cogs existentes.

    Os módulos do bot foram escritos com operações PyMongo síncronas
    (find_one/update_one/insert_one). Esta função mantém essa API compatível.
    Para rotinas realmente assíncronas de manutenção, use
    ``await get_connection_async()``.
    """
    global _sync_client
    if _sync_client is None:
        client = MongoClient(
            MONGODB_URL,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=10000,
            maxPoolSize=20,
            minPoolSize=2,
            maxIdleTimeMS=300000,
            waitQueueTimeoutMS=5000,
            retryWrites=True,
            appname="ModerationBot-SyncCompat",
        )
        # PyMongo conecta de forma lazy. Não faça ping síncrono aqui:
        # isso bloquearia o event loop no primeiro acesso ao banco.
        _sync_client = client
    return _sync_client[DB_NAME]


async def get_connection_async():
    """Retorna o banco Motor assíncrono para rotinas que usam await."""
    client = await get_mongo_client()
    return client[DB_NAME]


async def get_db():
    """
    Atalho pra pegar o banco direto.

    📌 DIFERENÇA DE get_connection():
    get_connection() é mais "geral" (poderia ser usado pra
    pegar outros bancos). get_db() é direto ao ponto: devolve
    o banco principal já pronto.

    Uso:
        db = await get_db()
        await db["collection"].insert_one({...})
    """
    return await get_connection_async()


# ============================================================
# BLOCO 3 — HELPERS DE ÍNDICE (agora async)
# ============================================================
# 📌 DIFERENÇA:
# Antes, os índices eram criados de forma síncrona (o que
# bloqueava o event loop por alguns segundos no startup).
# Agora, são criados de forma assíncrona — não travam o bot.

async def _safe_idx(col: str, keys: list, **kwargs) -> bool:
    """
    Cria um índice, mas não quebra se der erro.

    📌 POR QUE "SAFE"?
    Se um índice já existe com nome diferente, o MongoDB reclama.
    Como não queremos que o bot morra por causa disso, a gente
    captura o erro e segue a vida.

    Args:
        col: nome da coleção.
        keys: lista de (campo, direção) — ex: [("guild_id", 1)].
        **kwargs: opções (unique, background, name, etc).

    Returns:
        True se criou, False se falhou.
    """
    try:
        db = await get_connection_async()
        await db[col].create_index(keys, **kwargs)
        return True
    except Exception as e:
        print(f"⚠️ idx {col} {keys}: {e}")
        return False


async def _safe_ttl(col: str, key: str, seconds: int, name: str) -> bool:
    """
    Cria índice TTL (auto-delete após X segundos).

    📌 O QUE É TTL?
    "Time To Live" = "tempo de vida". O MongoDB apaga
    automaticamente documentos que ficarem mais velhos que X
    segundos. Útil pra logs, caches e dados temporários.

    Args:
        col: nome da coleção.
        key: campo de data (precisa ser Date).
        seconds: quantos segundos guardar (0 = desabilita).
        name: nome do índice (pra não duplicar).

    Returns:
        True se criou, False se falhou.
    """
    try:
        db = await get_connection_async()
        await db[col].create_index(
            [(key, ASCENDING)],
            expireAfterSeconds=seconds,
            background=True,
            name=name,
        )
        return True
    except Exception:
        return False


# ============================================================
# BLOCO 4 — CRIA TODOS OS ÍNDICES (agora async)
# ============================================================
# 📌 O QUE MUDOU?
# A estrutura é a MESMA. Só virou `async def` e cada `_safe_idx`
# virou `await _safe_idx(...)`.

async def ensure_indexes() -> None:
    """
    Cria todos os índices que o bot precisa.

    📌 POR QUE ISSO EXISTE?
    Sem índices, o MongoDB faz "full scan" em cada query (lento).
    Com índices, ele vai direto no documento certo (rápido).

    📌 QUANDO RODA?
    Uma vez no startup (dentro do setup_hook do bot).
    """
    try:
        # ═══════════════════════════════════════════════════════
        # CONTROLE / BRANDING
        # ═══════════════════════════════════════════════════════
        await _safe_idx("control_config", [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_ctrl")
        await _safe_idx("branding_config", [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_brand")

        # ═══════════════════════════════════════════════════════
        # GUILD CONFIG
        # ═══════════════════════════════════════════════════════
        await _safe_idx("guild_config", [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_gcfg")
        await _safe_idx("role_permissions",
                        [("guild_id", ASCENDING), ("role_id", ASCENDING),
                         ("command_name", ASCENDING)],
                        background=True, name="idx_rperm")
        await _safe_idx("user_permissions",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING),
                         ("command_name", ASCENDING)],
                        background=True, name="idx_uperm")

        # ═══════════════════════════════════════════════════════
        # STAFF
        # ═══════════════════════════════════════════════════════
        await _safe_idx("staff_config", [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_stfg")
        await _safe_idx("staff_points",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING)],
                        unique=True, background=True, name="idx_stpts")
        await _safe_idx("staff_points",
                        [("guild_id", ASCENDING), ("points", DESCENDING)],
                        background=True, name="idx_stpts_rank")
        await _safe_idx("staff_actions",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_stact")
        await _safe_idx("staff_auto_roles",
                        [("guild_id", ASCENDING),
                         ("points_required", ASCENDING)],
                        background=True, name="idx_star")

        # ═══════════════════════════════════════════════════════
        # MODERAÇÃO
        # ═══════════════════════════════════════════════════════
        await _safe_idx("warns",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING)],
                        background=True, name="idx_warns")
        await _safe_idx("punishments",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_pun")
        await _safe_idx("blocked_words",
                        [("guild_id", ASCENDING), ("word", ASCENDING)],
                        unique=True, background=True, name="idx_bwords")
        await _safe_idx("whitelist",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING)],
                        unique=True, background=True, name="idx_wl")

        # ═══════════════════════════════════════════════════════
        # AUTOMOD
        # ═══════════════════════════════════════════════════════
        await _safe_idx("automod_config", [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_amcfg")
        await _safe_idx("automod_infractions",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_aminf")
        await _safe_ttl("automod_infractions", "timestamp",
                        60 * 60 * 24 * 7, "ttl_aminf")

        # ═══════════════════════════════════════════════════════
        # ANTI-NUKE / ANTI-RAID
        # ═══════════════════════════════════════════════════════
        await _safe_idx("antinuke_config", [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_ancfg")
        await _safe_idx("antinuke_logs",
                        [("guild_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_anlog")
        await _safe_idx("antinuke_logs",
                        [("guild_id", ASCENDING),
                         ("action_type", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_anlog_action")
        await _safe_idx("antinuke_logs",
                        [("guild_id", ASCENDING), ("severity", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_anlog_sev")
        await _safe_ttl("antinuke_logs", "timestamp",
                        60 * 60 * 24 * 14, "ttl_anlog")
        await _safe_idx("antinuke_panel_config", [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_anpanel_cfg")

        # ═══════════════════════════════════════════════════════
        # ANTI-ESCALATION
        # ═══════════════════════════════════════════════════════
        await _safe_idx("antiescalation_config", [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_aecfg")
        await _safe_idx("privilege_changes",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_priv_change")
        await _safe_idx("privilege_changes",
                        [("guild_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_priv_recent")
        await _safe_ttl("privilege_changes", "timestamp",
                        60 * 60 * 24 * 30, "ttl_priv")
        await _safe_idx("privilege_alerts",
                        [("guild_id", ASCENDING), ("resolved", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_priv_alert")
        await _safe_ttl("privilege_alerts", "timestamp",
                        60 * 60 * 24 * 90, "ttl_priv_alert")

        # ═══════════════════════════════════════════════════════
        # BOT APPROVAL
        # ═══════════════════════════════════════════════════════
        await _safe_idx("pending_bots",
                        [("guild_id", ASCENDING), ("bot_id", ASCENDING)],
                        unique=True, background=True, name="idx_pbot")
        await _safe_idx("pending_bots",
                        [("guild_id", ASCENDING),
                         ("added_at", DESCENDING)],
                        background=True, name="idx_pbot_time")
        await _safe_ttl("pending_bots", "added_at",
                        60 * 60 * 24 * 7, "ttl_pbot")
        await _safe_idx("bot_approvals",
                        [("guild_id", ASCENDING), ("bot_id", ASCENDING)],
                        unique=True, background=True, name="idx_bapp")
        await _safe_idx("bot_approvals",
                        [("guild_id", ASCENDING),
                         ("approved_at", DESCENDING)],
                        background=True, name="idx_bapp_time")

        # ═══════════════════════════════════════════════════════
        # CASES / NOTES / APPEALS
        # ═══════════════════════════════════════════════════════
        await _safe_idx("mod_cases",
                        [("guild_id", ASCENDING), ("case_id", ASCENDING)],
                        unique=True, background=True, name="idx_case")
        await _safe_idx("mod_cases",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_case_usr")
        await _safe_idx("user_notes",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING)],
                        background=True, name="idx_notes")
        await _safe_idx("appeals",
                        [("guild_id", ASCENDING), ("status", ASCENDING)],
                        background=True, name="idx_appeals")

        # ═══════════════════════════════════════════════════════
        # BACKUP
        # ═══════════════════════════════════════════════════════
        await _safe_idx("backups",
                        [("guild_id", ASCENDING),
                         ("created_at", DESCENDING)],
                        background=True, name="idx_bkup")
        await _safe_idx("backup_texts",
                        [("backup_id", ASCENDING)],
                        background=True, name="idx_bkup_txt")
        await _safe_idx("backup_config",
                        [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_bkup_cfg")

        # ═══════════════════════════════════════════════════════
        # ROLLBACK CACHE
        # ═══════════════════════════════════════════════════════
        await _safe_idx("deleted_channels_cache",
                        [("guild_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_delch")
        await _safe_ttl("deleted_channels_cache", "timestamp",
                        60 * 60 * 24, "ttl_delch")
        await _safe_idx("deleted_roles_cache",
                        [("guild_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_delro")
        await _safe_ttl("deleted_roles_cache", "timestamp",
                        60 * 60 * 24, "ttl_delro")

        # ═══════════════════════════════════════════════════════
        # LOGS
        # ═══════════════════════════════════════════════════════
        await _safe_idx("logs_config",
                        [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_logcfg")

        # ═══════════════════════════════════════════════════════
        # VERIFICATION
        # ═══════════════════════════════════════════════════════
        await _safe_idx("verification_config",
                        [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_vcfg")
        await _safe_idx("verification_logs",
                        [("guild_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_vlog")
        await _safe_ttl("verification_logs", "timestamp",
                        60 * 60 * 24 * 30, "ttl_vlog")

        # ═══════════════════════════════════════════════════════
        # PURGE
        # ═══════════════════════════════════════════════════════
        await _safe_idx("purge_logs",
                        [("guild_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_purge")
        await _safe_ttl("purge_logs", "timestamp",
                        60 * 60 * 24 * 30, "ttl_purge")

        # ═══════════════════════════════════════════════════════
        # PROTEÇÃO TOGGLE
        # ═══════════════════════════════════════════════════════
        await _safe_idx("protection_toggle_config",
                        [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_ptoggle")
        await _safe_idx("protection_toggle_logs",
                        [("guild_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_ptoggle_log")
        await _safe_ttl("protection_toggle_logs", "timestamp",
                        60 * 60 * 24 * 90, "ttl_ptoggle")

        # ═══════════════════════════════════════════════════════
        # WHITELIST CONFIRM
        # ═══════════════════════════════════════════════════════
        await _safe_idx("whitelist_confirm_config",
                        [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_wlconf")

        # ═══════════════════════════════════════════════════════
        # JAIL / STRIP / QUICK NOTES / REPORTS
        # ═══════════════════════════════════════════════════════
        await _safe_idx("jail_saved",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING)],
                        unique=True, background=True, name="idx_jail")
        await _safe_idx("strip_saved",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING)],
                        unique=True, background=True, name="idx_strip")
        await _safe_idx("quick_notes",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_qnote")
        await _safe_idx("reports",
                        [("guild_id", ASCENDING), ("resolved", ASCENDING)],
                        background=True, name="idx_report")

        # ═══════════════════════════════════════════════════════
        # EVENT LOGS
        # ═══════════════════════════════════════════════════════
        await _safe_idx("event_logs_config",
                        [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_evlogcfg")

        # ═══════════════════════════════════════════════════════
        # WARN SYSTEM
        # ═══════════════════════════════════════════════════════
        await _safe_idx("warn_system_config", [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_wsys_cfg")
        await _safe_idx("warn_system_warns",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING),
                         ("ativo", ASCENDING), ("timestamp", DESCENDING)],
                        background=True, name="idx_wsys_warns")
        await _safe_idx("warn_system_warns",
                        [("guild_id", ASCENDING), ("ativo", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_wsys_warns_all")
        await _safe_idx("warn_system_punicoes",
                        [("guild_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_wsys_pun")
        await _safe_ttl("warn_system_warns", "timestamp",
                        60 * 60 * 24 * 180, "ttl_wsys")
        await _safe_ttl("warn_system_punicoes", "timestamp",
                        60 * 60 * 24 * 180, "ttl_wsys_pun")

        # ═══════════════════════════════════════════════════════
        # PERM CATEGORIES
        # ═══════════════════════════════════════════════════════
        await _safe_idx("perm_categories_config",
                        [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_perm_cat")

        # ═══════════════════════════════════════════════════════
        # RAID SUSPECTS
        # ═══════════════════════════════════════════════════════
        await _safe_idx("raid_suspects_config",
                        [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_rs_cfg")
        await _safe_idx("raid_suspects_log",
                        [("guild_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_rs_log")
        await _safe_idx("raid_suspects_log",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_rs_log_user")
        await _safe_ttl("raid_suspects_log", "timestamp",
                        60 * 60 * 24 * 30, "ttl_rs_log")
        await _safe_idx("raid_detections",
                        [("guild_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_rs_detect")
        await _safe_ttl("raid_detections", "timestamp",
                        60 * 60 * 24 * 90, "ttl_rs_detect")

        # ═══════════════════════════════════════════════════════
        # BOT WHITELIST PROTECTION
        # ═══════════════════════════════════════════════════════
        await _safe_idx("bot_whitelist_protection_config",
                        [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_bwl_cfg")

        # ═══════════════════════════════════════════════════════
        # EMBED BUILDER v1.5
        # ═══════════════════════════════════════════════════════
        await _safe_idx("embed_builder_sessions",
                        [("user_id", ASCENDING), ("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_eb_sess")
        await _safe_ttl("embed_builder_sessions", "updated_at",
                        60 * 60 * 24 * 7, "ttl_eb_sess")
        await _safe_idx("embed_templates",
                        [("guild_id", ASCENDING), ("name", ASCENDING)],
                        unique=True, background=True, name="idx_eb_tpl")
        await _safe_idx("embed_templates",
                        [("guild_id", ASCENDING),
                         ("updated_at", DESCENDING)],
                        background=True, name="idx_eb_tpl_upd")
        await _safe_idx("embed_templates",
                        [("guild_id", ASCENDING), ("author_id", ASCENDING)],
                        background=True, name="idx_eb_tpl_author")

        # ═══════════════════════════════════════════════════════
        # EMBED BUILDER v3.0 (painéis interativos)
        # ═══════════════════════════════════════════════════════
        await _safe_idx("embed_panels",
                        [("guild_id", ASCENDING), ("name", ASCENDING)],
                        unique=True, background=True, name="idx_ebpanel_name")
        await _safe_idx("embed_panels",
                        [("guild_id", ASCENDING),
                         ("updated_at", DESCENDING)],
                        background=True, name="idx_ebpanel_upd")
        await _safe_idx("embed_panels",
                        [("message_id", ASCENDING)],
                        background=True, name="idx_ebpanel_msg")
        await _safe_idx("embed_messages",
                        [("guild_id", ASCENDING), ("name", ASCENDING)],
                        unique=True, background=True, name="idx_ebmsg_name")
        await _safe_idx("embed_messages",
                        [("guild_id", ASCENDING),
                         ("updated_at", DESCENDING)],
                        background=True, name="idx_ebmsg_upd")
        await _safe_idx("embed_variables",
                        [("guild_id", ASCENDING), ("key", ASCENDING)],
                        unique=True, background=True, name="idx_ebvar_key")
        await _safe_idx("embed_panel_logs",
                        [("guild_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_eblog_ts")
        await _safe_idx("embed_panel_logs",
                        [("guild_id", ASCENDING),
                         ("component_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_eblog_comp")
        await _safe_idx("embed_panel_logs",
                        [("guild_id", ASCENDING),
                         ("panel_name", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_eblog_panel")
        await _safe_ttl("embed_panel_logs", "timestamp",
                        60 * 60 * 24 * 30, "ttl_eblog")
        await _safe_idx("embed_lists",
                        [("guild_id", ASCENDING),
                         ("list_name", ASCENDING)],
                        unique=True, background=True, name="idx_eblist_name")

        # ═══════════════════════════════════════════════════════
        # v13.5 — NOVAS FEATURES
        # ═══════════════════════════════════════════════════════

        # ─── ANTI IP LOGGER ───
        await _safe_idx("antilink_config",
                        [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_al_cfg")

        # ─── ANTI EVADIR MUTE ───
        await _safe_idx("anti_evade_config",
                        [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_ae_cfg")
        await _safe_idx("active_timeouts",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING)],
                        unique=True, background=True, name="idx_at_user")
        await _safe_idx("active_timeouts",
                        [("expires_at", ASCENDING)],
                        background=True, name="idx_at_expires")
        await _safe_ttl("active_timeouts", "expires_at", 0, "ttl_at")

        # ─── SCORE DE RISCO NO JOIN ───
        await _safe_idx("join_risk_config",
                        [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_jr_cfg")
        await _safe_idx("join_risk_logs",
                        [("guild_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_jr_log")
        await _safe_idx("join_risk_logs",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_jr_log_user")
        await _safe_idx("join_risk_logs",
                        [("guild_id", ASCENDING), ("score", DESCENDING),
                         ("timestamp", DESCENDING)],
                        background=True, name="idx_jr_log_score")
        await _safe_ttl("join_risk_logs", "timestamp",
                        60 * 60 * 24 * 30, "ttl_jr_log")

        # ─── STATS GRAPH ───
        await _safe_idx("stats_graph_config",
                        [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_sg_cfg")

        # ─── VERIFICATION v9.0 ───
        await _safe_idx("verification_v2_tokens",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING)],
                        unique=True, background=True, name="idx_v2_tok")
        await _safe_ttl("verification_v2_tokens", "created_at",
                        60 * 10, "ttl_v2_tok")
        await _safe_idx("verification_v2_attempts",
                        [("guild_id", ASCENDING), ("user_id", ASCENDING)],
                        unique=True, background=True, name="idx_v2_att")
        await _safe_ttl("verification_v2_attempts", "updated_at",
                        60 * 60 * 24, "ttl_v2_att")

        # ═══════════════════════════════════════════════════════
        # v12.4 — EMBED BUILDER v2.0
        # ═══════════════════════════════════════════════════════

        # ─── RASCUNHOS DO EDITOR (auto-save) ───
        await _safe_idx("embed_panel_drafts",
                        [("user_id", ASCENDING), ("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_ebdraft_user")
        await _safe_idx("embed_panel_drafts",
                        [("guild_id", ASCENDING),
                         ("updated_at", DESCENDING)],
                        background=True, name="idx_ebdraft_upd")
        await _safe_ttl("embed_panel_drafts", "updated_at",
                        60 * 60 * 24 * 7, "ttl_ebdraft")

        # ─── CONFIG DO EMBED BUILDER (canais) ───
        await _safe_idx("embed_builder_config",
                        [("guild_id", ASCENDING)],
                        unique=True, background=True, name="idx_ebcfg")

        # ─── REACTION ROLES (emoji → cargo) ───
        await _safe_idx("embed_reaction_roles",
                        [("guild_id", ASCENDING), ("message_id", ASCENDING)],
                        unique=True, background=True, name="idx_rr_msg")
        await _safe_idx("embed_reaction_roles",
                        [("guild_id", ASCENDING),
                         ("updated_at", DESCENDING)],
                        background=True, name="idx_rr_upd")

        # ═══════════════════════════════════════════════════════
        # FIM
        # ═══════════════════════════════════════════════════════
        print("✅ Índices garantidos (v13.0 — motor).")

    except Exception as e:
        print(f"⚠️ Erro geral índices: {e}")


# ============================================================
# BLOCO 5 — INICIALIZAÇÃO / WARMUP (agora async)
# ============================================================

async def init_db():
    """
    Chamado no startup: cria todos os índices.

    📌 IMPORTANTE:
    Agora é `async def`. No main.py, precisa ser:
        await init_db()
    """
    await ensure_indexes()
    db = await get_connection_async()
    return db


async def warmup() -> bool:
    """
    Pinga o banco pra testar conexão.

    📌 IMPORTANTE:
    Agora é `async def`. No main.py, precisa ser:
        if not await warmup():
            ...
    """
    try:
        db = await get_connection_async()
        await db.command("ping")
        return True
    except Exception:
        return False


# ============================================================
# BLOCO 6 — POOL STATS (agora async)
# ============================================================

async def get_pool_stats() -> dict:
    """
    Retorna info do pool/versão do Mongo.

    📌 Agora é async porque precisa chamar `server_info()`.
    """
    try:
        client = await get_mongo_client()
        server_info = await client.server_info()
        return {
            "server_version": server_info.get("version", "?"),
            "pool_max": 20,
            "pool_min": 5,
            "read_pref": "primaryPreferred",
            "write_concern": "w=1",
        }
    except Exception:
        return {}


# ============================================================
# BLOCO 7 — HELPERS DE ANTI-ESCALATION (agora async)
# ============================================================

async def log_privilege_change(guild_id: int, user_id: int,
                                moderator_id: int, action: str,
                                target_id: int, target_type: str,
                                details: dict = None) -> None:
    """
    Registra uma mudança de privilégio.

    📌 IMPORTANTE:
    Agora é `async def`. No commands_antiescalation.py, precisa ser:
        await log_privilege_change(...)

    📌 POR QUE ISSO EXISTE?
    Pra ter um histórico de quem mudou o que. Se alguém tentar
    escalar privilégios (dar admin pra si mesmo), fica registrado.
    """
    try:
        from datetime import datetime, timezone
        db = await get_connection_async()
        await db["privilege_changes"].insert_one({
            "guild_id": guild_id,
            "user_id": user_id,
            "moderator_id": moderator_id,
            "action": action,
            "target_id": target_id,
            "target_type": target_type,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc),
        })
    except Exception:
        pass


async def create_privilege_alert(guild_id: int, user_id: int,
                                  severity: str, reason: str,
                                  details: dict = None) -> None:
    """
    Cria um alerta de escalada.

    📌 IMPORTANTE:
    Agora é `async def`. No commands_antiescalation.py, precisa ser:
        await create_privilege_alert(...)
    """
    try:
        from datetime import datetime, timezone
        db = await get_connection_async()
        await db["privilege_alerts"].insert_one({
            "guild_id": guild_id,
            "user_id": user_id,
            "severity": severity,
            "reason": reason,
            "details": details or {},
            "resolved": False,
            "timestamp": datetime.now(timezone.utc),
        })
    except Exception:
        pass


# ============================================================
# BLOCO 8 — FECHAR CONEXÃO (novo, pro shutdown)
# ============================================================

async def close_connection():
    """
    Fecha a conexão com o MongoDB.

    📌 POR QUE ISSO EXISTE?
    Quando o bot desliga, precisa fechar as conexões abertas
    pra não deixar "leaks" no servidor do MongoDB.

    📌 ONDE USAR?
    No `close()` do GTBot, no main.py:
        await close_connection()
    """
    global _client, _sync_client
    if _client is not None:
        try:
            _client.close()
        except Exception:
            pass
        finally:
            _client = None
    if _sync_client is not None:
        try:
            _sync_client.close()
        except Exception:
            pass
        finally:
            _sync_client = None