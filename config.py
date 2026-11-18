# ============================================================
# CONFIG.PY - v7.0
# BOT DE MODERAÇÃO + ANTI-RAID + BOT APPROVAL
# ============================================================
# Este arquivo LÊ as variáveis de ambiente (config do Railway/discloud)
# e as expõe como constantes Python. É o "painel de configuração"
# do bot. Nada de lógica aqui, só leitura e validação.

import os    # pra ler variáveis de ambiente (os.getenv)
import sys   # pra encerrar o programa em caso de erro (sys.exit)


def _require(name: str) -> str:
    """Lê uma variável OBRIGATÓRIA. Se não existir, mata o bot."""
    value = os.getenv(name)
    if not value:
        print(f"❌ Variável obrigatória ausente: {name}", file=sys.stderr)
        sys.exit(1)  # 1 = código de erro (sai do programa)
    return value


def _get_int(name: str, default: int) -> int:
    """Lê variável e converte pra int. Se falhar, usa o padrão."""
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (ValueError, TypeError):
        print(f"⚠️ {name} inválido ('{raw}'), usando padrão {default}",
              file=sys.stderr)
        return default


def _get_bool(name: str, default: bool) -> bool:
    """Lê variável e converte pra True/False. Aceita: true,1,yes,sim,on."""
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("true", "1", "yes", "sim", "on")


# ============================================================
# OBRIGATÓRIAS (se faltar, bot não liga)
# ============================================================

TOKEN = _require("DISCORD_TOKEN")       # token do bot no Discord
MONGODB_URL = _require("MONGODB_URL")   # string de conexão do MongoDB

# ============================================================
# BÁSICO
# ============================================================

DB_NAME = os.getenv("DB_NAME", "gt_bot_moderacao")  # nome do banco
PREFIX = os.getenv("PREFIX", ">")                    # prefixo padrão
ADMIN_ROLE_ID = _get_int("ADMIN_ROLE_ID", 0)         # cargo admin (opcional)
OWNER_USER_ID = _get_int("OWNER_USER_ID", 0)           # ID do responsável; whitelist humana automática apenas se estiver no servidor

# GUILD_IDS aceita múltiplos IDs separados por vírgula: "123,456,789"
_guild_ids_raw = os.getenv("GUILD_IDS") or os.getenv("GUILD_ID") or ""
GUILD_IDS: list[int] = [
    int(g.strip()) for g in _guild_ids_raw.split(",") if g.strip().isdigit()
]
GUILD_ID = GUILD_IDS[0] if GUILD_IDS else 0  # primeiro da lista

ENABLE_DEBUG = _get_bool("ENABLE_DEBUG", False)         # logs verbosos
RAILWAY_ENV = os.getenv("RAILWAY_ENVIRONMENT", "")       # ambiente Railway

BOT_TYPE = "moderacao"  # identifica qual bot é esse

# ============================================================
# BACKUP
# ============================================================

BACKUP_ENABLED = _get_bool("BACKUP_ENABLED", True)
BACKUP_AUTO_HOUR = _get_int("BACKUP_AUTO_HOUR", 4)         # hora UTC
BACKUP_MAX_PER_GUILD = _get_int("BACKUP_MAX_PER_GUILD", 10)

# Webhook opcional (evita usar o bot pra mandar log de backup)
BACKUP_LOG_WEBHOOK = os.getenv("BACKUP_LOG_WEBHOOK", "")

# ============================================================
# ANTI-NUKE / ANTI-RAID
# ============================================================

ANTINUKE_ENABLED = _get_bool("ANTINUKE_ENABLED", True)
ANTIRAID_ENABLED = _get_bool("ANTIRAID_ENABLED", True)

# Usar webhook pros logs de anti-nuke (evita rate limit do bot)
ANTINUKE_USE_WEBHOOK = _get_bool("ANTINUKE_USE_WEBHOOK", True)

# Rollback (restaurar canais/cargos deletados)
ANTINUKE_ROLLBACK = _get_bool("ANTINUKE_ROLLBACK", True)
ANTINUKE_ROLLBACK_MAX_ITEMS = _get_int("ANTINUKE_ROLLBACK_MAX_ITEMS", 10)

# Mandar DM pro dono quando disparar
ANTINUKE_NOTIFY_OWNER = _get_bool("ANTINUKE_NOTIFY_OWNER", True)

# Tempo padrão da confirmação de bots whitelisted (minutos).
# A decisão de expulsar continua sempre sujeita à aprovação configurada por servidor.
BOT_APPROVAL_TIMEOUT = max(60, _get_int("BOT_APPROVAL_TIMEOUT", 300))

# ============================================================
# AUTOMOD
# ============================================================

AUTOMOD_ENABLED = _get_bool("AUTOMOD_ENABLED", True)
# Nota: anti-DM não existe (Discord não tem API confiável pra isso)

# ============================================================
# CONTROLE / CACHE / PERFORMANCE
# ============================================================

CACHE_TTL_DEFAULT = _get_int("CACHE_TTL_DEFAULT", 60)          # segundos
CACHE_MAX_SIZE_DEFAULT = _get_int("CACHE_MAX_SIZE_DEFAULT", 500)

# Se RAM passar disso (MB), força GC automático
MEMORY_GC_THRESHOLD_MB = _get_int("MEMORY_GC_THRESHOLD_MB", 380)

# De quanto em quanto tempo o monitor de RAM roda (minutos)
MEMORY_MONITOR_INTERVAL_MIN = _get_int("MEMORY_MONITOR_INTERVAL_MIN", 3)

# Rate limit por usuário (token bucket): N ações a cada X segundos
RATE_LIMIT_RATE = _get_int("RATE_LIMIT_RATE", 3)
RATE_LIMIT_PER = _get_int("RATE_LIMIT_PER", 5)

# ============================================================
# LOG / DEBUG
# ============================================================

# Se DEBUG ligado → INFO, senão → WARNING (só avisos e erros)
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO" if ENABLE_DEBUG else "WARNING")

# ============================================================
# STARTUP LOG (só aparece se DEBUG=on)
# ============================================================

if ENABLE_DEBUG:
    print("=" * 60)
    print(f"🛡️  BOT: MODERAÇÃO v7.0")
    print(f"📡  Guilds: {GUILD_IDS or 'global'}")
    print(f"🍃  DB: {DB_NAME} | Prefix: {PREFIX}")
    print(f"💾  Backup auto: {BACKUP_ENABLED} @ {BACKUP_AUTO_HOUR}h UTC")
    print(f"    └─ Máx por guild: {BACKUP_MAX_PER_GUILD}")
    print(f"🚨  Anti-nuke: {ANTINUKE_ENABLED} | Anti-raid: {ANTIRAID_ENABLED}")
    print(f"    └─ Webhook logs: {ANTINUKE_USE_WEBHOOK}")
    print(f"    └─ Rollback: {ANTINUKE_ROLLBACK} (máx {ANTINUKE_ROLLBACK_MAX_ITEMS})")
    print(f"    └─ Notificar dono: {ANTINUKE_NOTIFY_OWNER}")
    print(f"🤖  Automod: {AUTOMOD_ENABLED}")
    print(f"🧠  Mem threshold: {MEMORY_GC_THRESHOLD_MB}MB | "
          f"Monitor: {MEMORY_MONITOR_INTERVAL_MIN}min")
    print(f"⏱️   Rate limit: {RATE_LIMIT_RATE} por {RATE_LIMIT_PER}s")
    print(f"📝  Log level: {LOG_LEVEL}")
    if RAILWAY_ENV:
        print(f"🚂  Railway env: {RAILWAY_ENV}")
    print("=" * 60)