# ============================================================
# COMMANDS_ANTINUKE.PY - v13.5
# PROTEÇÃO COMPLETA + PERMISSÕES + COR CUSTOM
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# É o coração da proteção anti-nuke. Fica de olho em TODA ação
# destrutiva no servidor e pune automaticamente se alguém tentar
# "nukar" o servidor.
#
# 📌 O QUE MUDOU NA v13.5?
# - ADICIONADO: sistema de permissões do anti-nuke.
#   Chaves:
#     - antinuke_managers_roles (cargos que podem mexer)
#     - antinuke_managers_users (users que podem mexer)
#     - antinuke_require_manager (bool: se False, admin do Discord também)
#   Aplica em TODOS os comandos + whitelist.
# - UNIFICADO: whitelist_managers_roles/users → agora usa antinuke_managers_*
#   (whitelist_managers_* continua funcionando pra retrocompat).
# - ADICIONADO: método `_can_manage_antinuke()`.
# - ALTERADO: `_can_manage_whitelist()` usa a mesma lista.
# - ALTERADO: TODOS os comandos `>antinuke ...` checam permissão.
# - ADICIONADO: novo comando `>antinuke perm ...` (addrole/removerole/adduser/removeuser/list/require).
# - ADICIONADO: novo comando `>antinuke cor ...` (definir/reset/ver cor do painel).
#
# 📌 O QUE MANTÉM DA v13.4?
# - Chaves `{key}_whitelisted` em cada proteção
# - `whitelist_mode` global
# - `_apply_lockdown()` / `_restore_lockdown()`
# - `_get_threshold_for()`
# - `get_config_fresh()`
# ============================================================

# ─── BLOCO 1 — IMPORTS ───

import asyncio
import re
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

import discord
from discord.ext import commands

from config import ANTINUKE_ENABLED, ANTIRAID_ENABLED, PREFIX
from database import get_connection
from embeds import embed_success, embed_error, embed_warning, embed_info
from embeds_professional import ProfessionalLog, fmt_reason, fmt_user
from utils import TTLCache, safe_object_id, run_parallel


def _now() -> datetime:
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# BLOCO 2 — REGEX
# ============================================================

INVITE_RX = re.compile(
    r"(discord\.(gg|io|me|li)|discordapp\.com/invite)/[a-zA-Z0-9]+",
    re.IGNORECASE)
URL_RX = re.compile(r"https?://\S+", re.IGNORECASE)
CUSTOM_EMOJI_RX = re.compile(r"<a?:\w+:\d+>")
UNICODE_EMOJI_RX = re.compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002700-\U000027BF"
    "\U0001F900-\U0001F9FF"
    "\U00002600-\U000026FF"
    "]+",
    flags=re.UNICODE)
INVISIBLE_RX = re.compile(r"[\u200B\u200E\u200F\u202A-\u202E\u2060-\u206F\uFEFF]")
ZALGO_RX = re.compile(r"[\u0300-\u036f\u0489\u1AB0-\u1AFF\u1DC0-\u1DFF]")
SUSPICIOUS_NAME_RX = re.compile(
    r"[\u200B-\u200F\u202A-\u202E\u2060-\u206F\uFEFF\u0300-\u036f]")


def count_emojis(text: str) -> int:
    if not text:
        return 0
    return (len(CUSTOM_EMOJI_RX.findall(text))
            + len(UNICODE_EMOJI_RX.findall(text)))


def count_custom_emojis(text: str) -> int:
    return len(CUSTOM_EMOJI_RX.findall(text)) if text else 0


def special_chars_percent(text: str) -> float:
    if not text:
        return 0.0
    total = len(text)
    special = sum(1 for c in text if not c.isalnum() and not c.isspace())
    return (special / total) * 100 if total else 0.0


def caps_percent(text: str) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return (sum(1 for c in letters if c.isupper()) / len(letters)) * 100


def max_repeated_char_run(text: str) -> int:
    if not text:
        return 0
    max_run = current_run = 1
    for i in range(1, len(text)):
        if text[i] == text[i - 1]:
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 1
    return max_run


def count_zalgo(text: str) -> int:
    return len(ZALGO_RX.findall(text)) if text else 0


def has_invisible_chars(text: str) -> bool:
    return bool(INVISIBLE_RX.search(text)) if text else False


def has_suspicious_name(text: str) -> bool:
    return bool(SUSPICIOUS_NAME_RX.search(text)) if text else False


def is_caps_spam(text: str, percent: int = 80) -> bool:
    letters = [c for c in text if c.isalpha()]
    if len(letters) < 10:
        return False
    return caps_percent(text) >= percent


# ============================================================
# BLOCO 3 — COR: parse helpers
# ============================================================
# Aceita hex (#ff0000), nome (red), rgb(255,0,0), 0x..., decimal.

NAMED_COLORS = {
    "red": 0xED4245, "green": 0x57F287, "blue": 0x5865F2,
    "yellow": 0xFEE75C, "orange": 0xE67E22, "purple": 0x9B59B6,
    "pink": 0xEB459E, "cyan": 0x00FFFF, "teal": 0x1ABC9C,
    "lime": 0x2ECC71, "gold": 0xF1C40F, "silver": 0xBDC3C7,
    "bronze": 0xCD7F32, "black": 0x000000, "white": 0xFFFFFF,
    "gray": 0x95A5A6, "grey": 0x95A5A6,
    "dark_red": 0x8B0000, "dark_green": 0x006400,
    "dark_blue": 0x00008B, "dark_purple": 0x4B0082,
    "discord": 0x5865F2, "blurple": 0x5865F2,
}


def parse_color(raw: str) -> Optional[int]:
    """
    Converte string de cor em inteiro.
    Retorna None se inválido.
    """
    if not raw:
        return None
    s = str(raw).strip().lower()

    if s in NAMED_COLORS:
        return NAMED_COLORS[s]

    # rgb(r,g,b)
    m = re.match(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", s)
    if m:
        r, g, b = (int(x) for x in m.groups())
        if any(c > 255 for c in (r, g, b)):
            return None
        return (r << 16) | (g << 8) | b

    if s.startswith("0x"):
        s = s[2:]
    if s.startswith("#"):
        s = s[1:]

    if re.fullmatch(r"[0-9a-f]{6}", s):
        return int(s, 16)
    if re.fullmatch(r"[0-9a-f]{3}", s):
        return int("".join(c * 2 for c in s), 16)
    if re.fullmatch(r"\d+", s):
        val = int(s)
        if 0 <= val <= 0xFFFFFF:
            return val
    return None


def color_to_hex(color: int) -> str:
    return f"#{color & 0xFFFFFF:06x}"


# ============================================================
# BLOCO 4 — CONFIGURAÇÃO PADRÃO
# ============================================================

DEFAULT_CONFIG = {
    "enabled": True,
    "user_protection_enabled": True,
    # Incrementado em toda mudança ON/OFF para invalidar tarefas pendentes
    # do Anti-Evade mesmo se a limpeza de documentos falhar.
    "user_protection_epoch": 0,
    "punishment": "strip",
    "log_channel": 0,
    "log_webhook": "",
    "notify_owner": True,

    "threshold": 3,
    "window_seconds": 15,
    "global_threshold": 6,
    "global_window_seconds": 30,
    "punish_cooldown": 60,

    "whitelist_mode": "confirm_kick",

    # 🆕 v13.5 — Permissões do anti-nuke (unificadas)
    "antinuke_managers_roles": [],
    "antinuke_managers_users": [],
    "antinuke_require_manager": True,

    # Retrocompat (v13.4)
    "whitelist_managers_roles": [],
    "whitelist_managers_users": [],
    "whitelist_require_manager": True,

    # Threshold whitelisted (por proteção)
    "threshold_whitelisted": 4,
    "channel_delete_threshold_whitelisted": 4,
    "channel_create_threshold_whitelisted": 4,
    "channel_rename_threshold_whitelisted": 4,
    "role_delete_threshold_whitelisted": 4,
    "role_create_threshold_whitelisted": 4,
    "role_rename_threshold_whitelisted": 4,
    "ban_threshold_whitelisted": 4,
    "kick_threshold_whitelisted": 4,
    "member_update_threshold_whitelisted": 4,
    "mass_disconnect_threshold_whitelisted": 4,
    "raid_join_threshold_whitelisted": 6,

    # Anti-nuke
    "anti_channel_delete": True,
    "anti_channel_create": True,
    "anti_channel_rename": True,
    "anti_role_delete": True,
    "anti_role_create": True,
    "anti_role_rename": True,
    "anti_ban": True,
    "anti_kick": True,
    "anti_member_update": True,
    "anti_mass_disconnect": True,
    "anti_webhook": True,
    "anti_emoji_delete": True,
    "anti_sticker_delete": True,
    "anti_thread_delete": True,
    "anti_integration": True,
    "anti_guild_update": True,
    "anti_bot_add": True,
    "anti_message_nuke": True,

    # Anti-spam
    "anti_invite_spam": True,
    "anti_url_spam": True,
    "anti_mass_mention": True,
    "anti_everyone_mention": True,
    "anti_msg_flood": True,
    "anti_emoji_spam": True,
    "anti_custom_emoji_spam": True,
    "anti_special_chars": True,
    "anti_caps_lock": True,
    "anti_zalgo": True,
    "anti_invisible_chars": True,
    "anti_repeated_chars": True,

    "limite_links": 3,
    "limite_mencoes": 5,

    "hard_channel_limit": 5,
    "hard_msg_flood_limit": 10,
    "hard_emoji_limit": 10,
    "hard_custom_emoji_limit": 8,
    "hard_special_chars_percent": 50,
    "hard_caps_percent": 80,
    "hard_repeated_chars": 15,
    "hard_zalgo_limit": 5,

    # Anti-raid
    "anti_raid": True,
    "raid_join_threshold": 5,
    "raid_join_window": 10,
    "min_account_age_days": 7,
    "raid_action": "kick",
    "require_avatar": False,
    "raid_lockdown": False,

    "manual_lockdown_active": False,
    "lockdown_saved_perms": {},

    "rollback_enabled": True,
    "rollback_max_items": 50,
    "rollback_window_minutes": 5,

    "bot_approval_enabled": True,
    "bot_approvers": [],
    "bot_approvers_roles": [],
    "auto_kick_unapproved_bots": True,

    "canary_nuke_enabled": True,
    "canary_nuke_triggers": ["nuke", "raid", "atacar", "destruir"],
    "canary_nuke_duration": 300,
    "canary_nuke_ban_author": True,
    "canary_nuke_ban_executor": True,

    "whitelist_users": [],
    "whitelist_roles": [],
    "whitelist_bots": [],
    # Exceção de pessoa: ignora a whitelist por cargo, mas não uma entrada
    # direta em whitelist_users.
    "whitelist_role_exclusions": [],
}


# ============================================================
# BLOCO 5 — MAPA DE PROTEÇÕES
# ============================================================

PROTECTION_MAP = {
    "channel_delete": {
        "label": "🗑️ Destruição de canais",
        "desc": "Impede deleção em massa de canais",
        "keys": ["anti_channel_delete"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "channel_delete_threshold_whitelisted",
    },
    "channel_create": {
        "label": "📁 Criação de canais",
        "desc": "Impede criação em massa de canais",
        "keys": ["anti_channel_create"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "channel_create_threshold_whitelisted",
    },
    "channel_rename": {
        "label": "✏️ Rename de canais",
        "desc": "Impede renomear canais em massa",
        "keys": ["anti_channel_rename"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "channel_rename_threshold_whitelisted",
    },
    "role_delete": {
        "label": "👑 Nuke de cargos",
        "desc": "Impede deleção em massa de cargos",
        "keys": ["anti_role_delete"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "role_delete_threshold_whitelisted",
    },
    "role_create": {
        "label": "➕ Criação de cargos",
        "desc": "Impede criação em massa de cargos",
        "keys": ["anti_role_create"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "role_create_threshold_whitelisted",
    },
    "role_rename": {
        "label": "🏷️ Rename de cargos",
        "desc": "Impede renomear cargos em massa",
        "keys": ["anti_role_rename"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "role_rename_threshold_whitelisted",
    },
    "ban": {
        "label": "🔨 Ban em massa",
        "desc": "Impede banir membros em massa",
        "keys": ["anti_ban"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "ban_threshold_whitelisted",
    },
    "kick": {
        "label": "👢 Kick em massa",
        "desc": "Impede expulsar membros em massa",
        "keys": ["anti_kick"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "kick_threshold_whitelisted",
    },
    "member_update": {
        "label": "👤 Anti member update",
        "desc": "Impede alterar membros em massa",
        "keys": ["anti_member_update"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "member_update_threshold_whitelisted",
    },
    "mass_disconnect": {
        "label": "🔇 Anti mass disconnect",
        "desc": "Impede desconectar membros em massa",
        "keys": ["anti_mass_disconnect"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "mass_disconnect_threshold_whitelisted",
    },
    "bot_add": {
        "label": "🤖 Bloqueio de bots",
        "desc": "Kicka bots não autorizados",
        "keys": ["anti_bot_add"],
    },
    "bot_approval": {
        "label": "🛡️ Aprovação de bots",
        "desc": "Sistema de aprovação de bots",
        "keys": ["bot_approval_enabled"],
    },
    "webhook": {
        "label": "🪝 Anti-webhook",
        "desc": "Impede criar/deletar webhooks em massa",
        "keys": ["anti_webhook"],
    },
    "emoji_delete": {
        "label": "😀 Anti emoji delete",
        "desc": "Impede deletar emojis em massa",
        "keys": ["anti_emoji_delete"],
    },
    "sticker_delete": {
        "label": "🏷️ Anti sticker delete",
        "desc": "Impede deletar stickers em massa",
        "keys": ["anti_sticker_delete"],
    },
    "thread_delete": {
        "label": "🧵 Anti thread delete",
        "desc": "Impede deletar threads em massa",
        "keys": ["anti_thread_delete"],
    },
    "integration": {
        "label": "🔗 Anti integration",
        "desc": "Impede adicionar/remover integrações",
        "keys": ["anti_integration"],
    },
    "guild_update": {
        "label": "🖥️ Anti edit server",
        "desc": "Impede alterações suspeitas no servidor",
        "keys": ["anti_guild_update"],
    },
    "message_nuke": {
        "label": "💬 Anti message nuke",
        "desc": "Impede deletar mensagens em massa",
        "keys": ["anti_message_nuke"],
    },
    "invite_spam": {
        "label": "📨 Anti invite",
        "desc": "Bloqueia convites do Discord",
        "keys": ["anti_invite_spam"],
    },
    "url_spam": {
        "label": "🔗 Anti link spam",
        "desc": "Bloqueia spam de links",
        "keys": ["anti_url_spam"],
    },
    "mass_mention": {
        "label": "📢 Flood de menções",
        "desc": "Bloqueia muitas menções numa mensagem",
        "keys": ["anti_mass_mention"],
    },
    "everyone_mention": {
        "label": "🚨 Anti everyone",
        "desc": "Bloqueia menção a @everyone",
        "keys": ["anti_everyone_mention"],
    },
    "msg_flood": {
        "label": "🌊 Anti flood",
        "desc": "Bloqueia spam de mensagens",
        "keys": ["anti_msg_flood"],
    },
    "emoji_spam": {
        "label": "😀 Anti emoji spam",
        "desc": "Bloqueia spam de emojis",
        "keys": ["anti_emoji_spam"],
    },
    "custom_emoji_spam": {
        "label": "🎨 Anti custom emoji",
        "desc": "Bloqueia spam de emojis customizados",
        "keys": ["anti_custom_emoji_spam"],
    },
    "special_chars": {
        "label": "✳️ Anti special chars",
        "desc": "Bloqueia muitos caracteres especiais",
        "keys": ["anti_special_chars"],
    },
    "caps_lock": {
        "label": "🔠 Anti caps lock",
        "desc": "Bloqueia CAPS LOCK excessivo",
        "keys": ["anti_caps_lock"],
    },
    "zalgo": {
        "label": "👹 Anti zalgo",
        "desc": "Bloqueia caracteres zalgo",
        "keys": ["anti_zalgo"],
    },
    "invisible_chars": {
        "label": "👻 Anti invisíveis",
        "desc": "Bloqueia caracteres invisíveis",
        "keys": ["anti_invisible_chars"],
    },
    "repeated_chars": {
        "label": "🔁 Anti repetidos",
        "desc": "Bloqueia caracteres repetidos",
        "keys": ["anti_repeated_chars"],
    },
    "anti_raid": {
        "label": "🚪 Join flood",
        "desc": "Detecta entradas em massa (raid)",
        "keys": ["anti_raid"],
        "threshold_key": "raid_join_threshold",
        "window_key": "raid_join_window",
        "whitelist_threshold_key": "raid_join_threshold_whitelisted",
    },
    "require_avatar": {
        "label": "🖼️ Exigir avatar",
        "desc": "Kicka membros sem avatar",
        "keys": ["require_avatar"],
    },
    "raid_lockdown": {
        "label": "🔒 Lockdown automático",
        "desc": "Ativa lockdown ao detectar raid",
        "keys": ["raid_lockdown"],
    },
    "canary_nuke": {
        "label": "🐤 Canary nuke",
        "desc": "Vigilância de emergência por comando",
        "keys": ["canary_nuke_enabled"],
    },
    "rollback": {
        "label": "♻️ Rollback automático",
        "desc": "Restaura canais/cargos deletados",
        "keys": ["rollback_enabled"],
    },
    "user_protection": {
        "label": "👤 Proteção contra users",
        "desc": "Monitora ações de usuários (não só bots)",
        "keys": ["user_protection_enabled"],
    },
}


# ============================================================
# BLOCO 6 — MANAGER
# ============================================================

class AntiNukeManager:
    _cache = TTLCache(max_size=200, ttl=60)

    @classmethod
    def get_config(cls, guild_id: int) -> dict:
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached
        db = get_connection()
        doc = db["antinuke_config"].find_one({"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    def get_config_fresh(cls, guild_id: int) -> dict:
        db = get_connection()
        doc = db["antinuke_config"].find_one({"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    def update(cls, guild_id: int, key: str, value):
        db = get_connection()
        update_doc = {"$set": {key: value}}
        if key == "user_protection_enabled":
            # Toda transição ON/OFF invalida os timeouts criados no ciclo
            # anterior. O epoch protege até se delete_many falhar.
            update_doc["$inc"] = {"user_protection_epoch": 1}
        db["antinuke_config"].update_one(
            {"guild_id": guild_id}, update_doc, upsert=True)
        cls._cache.invalidate(guild_id)
        if key == "user_protection_enabled":
            try:
                db["active_timeouts"].delete_many(
                    {"guild_id": int(guild_id)})
            except Exception:
                pass

    @classmethod
    def add_to_list(cls, guild_id: int, key: str, value) -> bool:
        """Adiciona IDs à whitelist sem sobrescrever atualizações concorrentes."""
        try:
            value = int(value)
            if key not in {"whitelist_users", "whitelist_bots", "whitelist_roles", "whitelist_role_exclusions"}:
                return False
            db = get_connection()
            # Remove a representação textual legada antes de inserir o inteiro.
            db["antinuke_config"].update_one(
                {"guild_id": guild_id}, {"$pull": {key: str(value)}})
            result = db["antinuke_config"].update_one(
                {"guild_id": guild_id}, {"$addToSet": {key: value}}, upsert=True)
            cls._cache.invalidate(guild_id)
            return bool(result.modified_count or result.upserted_id)
        except Exception:
            return False

    @classmethod
    def remove_from_list(cls, guild_id: int, key: str, value) -> bool:
        """Remove ID inteiro e legado textual da whitelist de forma segura."""
        try:
            value = int(value)
            if key not in {"whitelist_users", "whitelist_bots", "whitelist_roles", "whitelist_role_exclusions"}:
                return False
            db = get_connection()
            a = db["antinuke_config"].update_one(
                {"guild_id": guild_id}, {"$pull": {key: value}})
            b = db["antinuke_config"].update_one(
                {"guild_id": guild_id}, {"$pull": {key: str(value)}})
            cls._cache.invalidate(guild_id)
            return bool(a.modified_count or b.modified_count)
        except Exception:
            return False

    @classmethod
    def invalidate(cls, guild_id: int):
        cls._cache.invalidate(guild_id)


# ============================================================
# BLOCO 7 — TRACKER
# ============================================================

class ActionTracker:
    def __init__(self):
        self._per_action = defaultdict(
            lambda: defaultdict(lambda: defaultdict(lambda: deque(maxlen=50))))
        self._global = defaultdict(
            lambda: defaultdict(lambda: deque(maxlen=100)))
        self._cooldown = defaultdict(dict)

    def register(self, guild_id: int, user_id: int, action: str,
                 window: float, global_window: float) -> tuple:
        now = time.time()
        dq = self._per_action[guild_id][user_id][action]
        while dq and now - dq[0] > window:
            dq.popleft()
        dq.append(now)

        gq = self._global[guild_id][user_id]
        while gq and now - gq[0] > global_window:
            gq.popleft()
        gq.append(now)

        return len(dq), len(gq)

    def is_on_cooldown(self, guild_id: int, user_id: int,
                        cooldown: float) -> bool:
        last = self._cooldown[guild_id].get(user_id, 0)
        return (time.time() - last) < cooldown

    def set_cooldown(self, guild_id: int, user_id: int):
        self._cooldown[guild_id][user_id] = time.time()

    def clear(self, guild_id: int, user_id: int):
        self._per_action[guild_id][user_id].clear()
        self._global[guild_id].pop(user_id, None)


# ============================================================
# BLOCO 8 — WEBHOOK LOGGER
# ============================================================

class WebhookLogger:
    _cache: Dict[str, discord.Webhook] = {}

    @classmethod
    async def send(cls, url: str, embed: discord.Embed,
                   username: str = "🛡️ Stumble Hit AntiRaid",
                   avatar_url: Optional[str] = None) -> bool:
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
# BLOCO 9 — ROLLBACK
# ============================================================

class RollbackManager:
    @staticmethod
    async def restore_channel(guild, data, reason="AntiNuke rollback"):
        try:
            category = None
            if data.get("category_id"):
                category = guild.get_channel(data["category_id"])
            channel_type = str(data.get("type", "text")).lower()

            if "voice" in channel_type:
                new_ch = await guild.create_voice_channel(
                    data["name"], category=category, reason=reason)
            elif "category" in channel_type:
                new_ch = await guild.create_category(
                    data["name"], reason=reason)
            else:
                new_ch = await guild.create_text_channel(
                    data["name"], category=category,
                    topic=data.get("topic"),
                    nsfw=data.get("nsfw", False),
                    slowmode_delay=data.get("slowmode", 0),
                    reason=reason)

            overwrites = data.get("overwrites", [])
            if overwrites:
                async def _apply_ow(ow):
                    try:
                        if ow["target_type"] == "role":
                            target = guild.get_role(ow["target_id"])
                        else:
                            target = guild.get_member(ow["target_id"])
                        if not target:
                            return
                        allow = discord.Permissions(ow["allow"])
                        deny = discord.Permissions(ow["deny"])
                        await new_ch.set_permissions(
                            target,
                            overwrite=discord.PermissionOverwrite.from_pair(
                                allow, deny))
                    except Exception:
                        pass
                await run_parallel(overwrites[:30], _apply_ow,
                                    concurrency=3, retry=1)
            try:
                await new_ch.edit(position=data.get("position", 0))
            except Exception:
                pass
            return new_ch
        except Exception:
            return None

    @staticmethod
    async def restore_role(guild, data, reason="AntiNuke rollback"):
        try:
            perms = discord.Permissions(data.get("permissions", 0))
            role = await guild.create_role(
                name=data["name"],
                color=discord.Color(data.get("color", 0)),
                permissions=perms,
                hoist=data.get("hoist", False),
                mentionable=data.get("mentionable", False),
                reason=reason)
            try:
                await role.edit(position=data.get("position", role.position))
            except Exception:
                pass
            return role
        except Exception:
            return None


# ============================================================
# BLOCO 10 — COG PRINCIPAL
# ============================================================

class AntiNukeCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._tracker = ActionTracker()
        self._pending_bots: Dict[int, Dict[int, dict]] = defaultdict(dict)
        self._canary: Dict[int, dict] = {}

    # ═══════════════════════════════════════════════════════════
    # HELPERS DE PERMISSÃO (v13.5)
    # ═══════════════════════════════════════════════════════════

    def _can_manage_antinuke(self, cfg: dict,
                              member: Optional[discord.Member]) -> bool:
        """
        🆕 v13.5 — Diz se user pode mexer no anti-nuke.
        Regras:
        - Owner sempre pode
        - Users em antinuke_managers_users podem
        - Users com cargo em antinuke_managers_roles podem
        - Se require_manager = False → admin do Discord também pode
        """
        if member is None:
            return False
        if member.id == member.guild.owner_id:
            return True
        if member.id in cfg.get("antinuke_managers_users", []):
            return True
        roles = set(cfg.get("antinuke_managers_roles", []))
        if any(r.id in roles for r in member.roles):
            return True
        if not cfg.get("antinuke_require_manager", True):
            return member.guild_permissions.administrator
        return False

    def _can_manage_whitelist(self, cfg: dict,
                              member: Optional[discord.Member]) -> bool:
        """
        v13.5 — Mesma coisa que antinuke (unificado).
        Mantém retrocompat com whitelist_managers_* se quiser.
        """
        # Unificado: usa antinuke_managers_*.
        if self._can_manage_antinuke(cfg, member):
            return True
        # Retrocompat: checa whitelist_managers_* também.
        if member is None:
            return False
        if member.id == member.guild.owner_id:
            return True
        if member.id in cfg.get("whitelist_managers_users", []):
            return True
        roles = set(cfg.get("whitelist_managers_roles", []))
        if any(r.id in roles for r in member.roles):
            return True
        if not cfg.get("whitelist_require_manager", True):
            return member.guild_permissions.administrator
        return False

    def _can_approve_bots(self, cfg: dict,
                           member: Optional[discord.Member]) -> bool:
        """Diz se user pode aprovar bots."""
        if member is None:
            return False
        if member.id == member.guild.owner_id:
            return True
        if member.id in cfg.get("bot_approvers", []):
            return True
        roles = set(cfg.get("bot_approvers_roles", []))
        if any(r.id in roles for r in member.roles):
            return True
        # Se é manager do antinuke, também pode aprovar bots.
        if self._can_manage_antinuke(cfg, member):
            return True
        return False

    # ═══════════════════════════════════════════════════════════
    # HELPERS
    # ═══════════════════════════════════════════════════════════

    @staticmethod
    def _normalized_ids(values) -> set:
        """Normaliza IDs do Mongo (int/string) sem deixar dado inválido quebrar o evento."""
        normalized = set()
        for value in values or []:
            try:
                normalized.add(int(value))
            except (TypeError, ValueError):
                continue
        return normalized

    def _is_whitelisted(self, cfg: dict, user_id: int,
                        member: Optional[discord.Member] = None) -> bool:
        try:
            uid = int(user_id)
        except (TypeError, ValueError):
            return False
        if uid in self._normalized_ids(cfg.get("whitelist_users", [])):
            return True
        # Exceção individual vence apenas a whitelist por cargo.
        if uid in self._normalized_ids(cfg.get("whitelist_role_exclusions", [])):
            return False
        if member:
            wl_roles = self._normalized_ids(cfg.get("whitelist_roles", []))
            if any(r.id in wl_roles for r in member.roles):
                return True
        return False

    def _is_immune_to_punish(self, cfg: dict, guild: discord.Guild,
                             user_id: int,
                             member: Optional[discord.Member] = None) -> bool:
        """Imunidade absoluta do dono e do modo whitelist sem restrições.

        No modo `confirm_kick`, membros whitelisted seguem para o fluxo
        explícito de confirmação em `_punish_user`; no modo
        `can_do_everything`, não podem ser punidos automaticamente.
        """
        if int(user_id) in (int(guild.owner_id), int(self.bot.user.id)):
            return True
        return (self._is_can_do_everything_mode(cfg)
                and self._is_whitelisted(cfg, user_id, member))

    def _is_bot_whitelisted(self, cfg: dict, bot_id: int,
                            member: Optional[discord.Member] = None) -> bool:
        try:
            bid = int(bot_id)
        except (TypeError, ValueError):
            return False
        # A whitelist de bots é independente: nem a whitelist humana,
        # nem cargos humanos, nem a lista de aprovadores substituem a
        # inclusão explícita do ID do bot em whitelist_bots.
        return bid in self._normalized_ids(cfg.get("whitelist_bots", []))

    @staticmethod
    async def _bot_whitelist_mode(guild_id: int) -> str:
        """Retorna 'immune' ou 'threshold' usando sempre a config atual.

        Se o banco não responder, retorna 'immune' para não punir um bot
        explicitamente whitelistado com base em uma configuração incerta.
        """
        try:
            from commands_bot_whitelist_protection import BotWhitelistManager
            cfg = await BotWhitelistManager.get_config_fresh(guild_id)
            if not cfg.get("enabled", True):
                return "threshold"
            mode = str(cfg.get("modo", "threshold")).lower()
            return "immune" if mode in ("immune", "never_kick") else "threshold"
        except Exception:
            return "immune"

    @staticmethod
    def _trusted_added_bot(guild_id: int, bot_id: int) -> bool:
        """Bot adicionado por humano whitelisted, mas ainda não whitelistado."""
        try:
            doc = get_connection()["antinuke_trusted_added_bots"].find_one(
                {"guild_id": int(guild_id), "bot_id": int(bot_id)})
            return bool(doc)
        except Exception:
            return False

    @staticmethod
    def _remember_trusted_added_bot(guild_id: int, bot_id: int,
                                    adder_id: int) -> None:
        get_connection()["antinuke_trusted_added_bots"].update_one(
            {"guild_id": int(guild_id), "bot_id": int(bot_id)},
            {"$set": {"guild_id": int(guild_id),
                      "bot_id": int(bot_id),
                      "added_by": int(adder_id),
                      "created_at": _now()}}, upsert=True)

    def _is_bot_authorized(self, cfg: dict, guild: discord.Guild,
                           user_id: Optional[int]) -> bool:
        if user_id is None:
            return False
        if user_id == guild.owner_id:
            return True
        if user_id in self._normalized_ids(cfg.get("bot_approvers", [])):
            return True
        if user_id in self._normalized_ids(cfg.get("whitelist_users", [])):
            return True
        member = guild.get_member(user_id)
        if member:
            approver_roles = self._normalized_ids(cfg.get("bot_approvers_roles", []))
            if any(r.id in approver_roles for r in member.roles):
                return True
            wl_roles = self._normalized_ids(cfg.get("whitelist_roles", []))
            if any(r.id in wl_roles for r in member.roles):
                return True
        return False

    def _get_threshold_for(self, cfg: dict, prot_key: str,
                            is_whitelisted: bool) -> int:
        prot = PROTECTION_MAP.get(prot_key)
        if not prot:
            return int(cfg.get("threshold", 3))
        normal_key = prot.get("threshold_key", "threshold")
        normal = int(cfg.get(normal_key, 3))
        if not is_whitelisted:
            return normal
        wl_key = prot.get("whitelist_threshold_key")
        if wl_key:
            wl_val = int(cfg.get(wl_key,
                                  cfg.get("threshold_whitelisted", normal + 1)))
            return max(wl_val, normal + 1)
        return max(int(cfg.get("threshold_whitelisted", normal + 1)),
                    normal + 1)

    def _is_confirma_kick_mode(self, cfg: dict) -> bool:
        return cfg.get("whitelist_mode", "confirm_kick") == "confirm_kick"

    def _is_can_do_everything_mode(self, cfg: dict) -> bool:
        return cfg.get("whitelist_mode", "confirm_kick") == "can_do_everything"

    async def _send_log(self, guild: discord.Guild, cfg: dict,
                        embed: discord.Embed):
        wh_url = cfg.get("log_webhook", "") or ""
        if wh_url:
            sent = await WebhookLogger.send(
                wh_url, embed,
                avatar_url=guild.me.display_avatar.url if guild.me else None)
            if sent:
                return
        log_ch = cfg.get("log_channel")
        if log_ch:
            channel = guild.get_channel(int(log_ch))
            if channel:
                try:
                    await channel.send(embed=embed)
                    return
                except Exception:
                    pass
        try:
            from commands_logs import LogsManager
            logs_cfg = await LogsManager.get_config(guild.id)
            ctx_channels = logs_cfg.get("context_channels", {})
            fallback_id = (ctx_channels.get("antinuke")
                           or logs_cfg.get("channel_id"))
            if fallback_id:
                ch = guild.get_channel(int(fallback_id))
                if ch:
                    try:
                        await ch.send(embed=embed)
                    except Exception:
                        pass
        except Exception:
            pass

    async def _notify_owner(self, guild, cfg, title, description):
        if not cfg.get("notify_owner"):
            return
        try:
            owner = guild.owner or await guild.fetch_member(guild.owner_id)
            embed = discord.Embed(
                title=title, description=description,
                color=discord.Color.dark_red(),
                timestamp=_now())
            embed.set_footer(text=f"Servidor: {guild.name}")
            await owner.send(embed=embed)
        except Exception:
            pass

    def _severity_from_counts(self, count_action, count_global,
                              threshold, global_threshold) -> str:
        ratio_a = count_action / max(threshold, 1)
        ratio_g = count_global / max(global_threshold, 1)
        worst = max(ratio_a, ratio_g)
        if worst >= 3:
            return "critical"
        if worst >= 2:
            return "high"
        if worst >= 1.5:
            return "medium"
        return "low"

    # ═══════════════════════════════════════════════════════════
    # LOCKDOWN
    # ═══════════════════════════════════════════════════════════

    async def _apply_lockdown(self, guild: discord.Guild) -> dict:
        bot_top_pos = guild.me.top_role.position
        saved = {}
        stats = {"ok": 0, "fail": 0, "skipped": 0}

        async def _worker(ch: discord.TextChannel):
            try:
                everyone_ow = ch.overwrites_for(guild.default_role)
                allow_e, deny_e = everyone_ow.pair()
                channel_save = {
                    "everyone": {"allow": allow_e.value, "deny": deny_e.value},
                    "roles": {},
                }
                roles_to_edit = []
                for target, ow in ch.overwrites.items():
                    if not isinstance(target, discord.Role):
                        continue
                    if target == guild.default_role:
                        continue
                    if target.position >= bot_top_pos:
                        continue
                    allow_r, deny_r = ow.pair()
                    if allow_r.value or deny_r.value:
                        channel_save["roles"][str(target.id)] = {
                            "allow": allow_r.value, "deny": deny_r.value}
                        roles_to_edit.append(target)
                saved[str(ch.id)] = channel_save

                everyone_ow.send_messages = False
                everyone_ow.add_reactions = False
                everyone_ow.connect = False
                everyone_ow.speak = False
                everyone_ow.create_public_threads = False
                everyone_ow.create_private_threads = False
                everyone_ow.send_messages_in_threads = False
                await ch.set_permissions(
                    guild.default_role, overwrite=everyone_ow,
                    reason="Lockdown anti-raid")

                for role in roles_to_edit:
                    try:
                        empty_ow = discord.PermissionOverwrite()
                        await ch.set_permissions(
                            role, overwrite=empty_ow,
                            reason="Lockdown (exceção removida)")
                    except Exception:
                        pass
                stats["ok"] += 1
            except Exception:
                stats["fail"] += 1

        await run_parallel(list(guild.text_channels), _worker,
                            concurrency=6, retry=2)
        return {"saved": saved, "stats": stats}

    async def _restore_lockdown(self, guild, saved: dict) -> dict:
        stats = {"ok": 0, "fail": 0}

        async def _worker(item):
            ch_id_str, data = item
            try:
                ch = guild.get_channel(int(ch_id_str))
                if not ch:
                    stats["fail"] += 1
                    return
                if "everyone" in data:
                    allow = discord.Permissions(data["everyone"].get("allow", 0))
                    deny = discord.Permissions(data["everyone"].get("deny", 0))
                    ow = discord.PermissionOverwrite.from_pair(allow, deny)
                    await ch.set_permissions(
                        guild.default_role, overwrite=ow,
                        reason="Lockdown restaurado")
                for role_id_str, role_data in data.get("roles", {}).items():
                    try:
                        role = guild.get_role(int(role_id_str))
                        if not role:
                            continue
                        allow = discord.Permissions(role_data.get("allow", 0))
                        deny = discord.Permissions(role_data.get("deny", 0))
                        ow = discord.PermissionOverwrite.from_pair(allow, deny)
                        await ch.set_permissions(
                            role, overwrite=ow,
                            reason="Lockdown restaurado (exceção)")
                    except Exception:
                        pass
                stats["ok"] += 1
            except Exception:
                stats["fail"] += 1

        await run_parallel(list(saved.items()), _worker,
                            concurrency=6, retry=2)
        return stats

    # ═══════════════════════════════════════════════════════════
    # ROLLBACK
    # ═══════════════════════════════════════════════════════════

    async def _rollback_recent(self, guild, cfg,
                                window_minutes: int = 5) -> List[str]:
        reverted: List[str] = []
        db = get_connection()
        cutoff = _now() - timedelta(minutes=window_minutes)
        max_items = int(cfg.get("rollback_max_items", 50))

        deleted_channels = list(db["deleted_channels_cache"].find({
            "guild_id": guild.id,
            "timestamp": {"$gte": cutoff},
        }).sort("timestamp", 1).limit(max_items))

        deleted_roles = list(db["deleted_roles_cache"].find({
            "guild_id": guild.id,
            "timestamp": {"$gte": cutoff},
        }).sort("timestamp", 1).limit(max_items))

        channels_to_restore = []
        for ch_data in deleted_channels:
            if guild.get_channel(ch_data["channel_id"]):
                try:
                    db["deleted_channels_cache"].delete_one({"_id": ch_data["_id"]})
                except Exception:
                    pass
                continue
            channels_to_restore.append(ch_data)

        roles_to_restore = []
        for role_data in deleted_roles:
            if guild.get_role(role_data["role_id"]):
                try:
                    db["deleted_roles_cache"].delete_one({"_id": role_data["_id"]})
                except Exception:
                    pass
                continue
            roles_to_restore.append(role_data)

        role_names_map: Dict[int, str] = {}
        if roles_to_restore:
            async def _restore_one_role(role_data):
                try:
                    new_role = await RollbackManager.restore_role(
                        guild,
                        {
                            "id": role_data["role_id"],
                            "name": role_data["name"],
                            "color": role_data["color"],
                            "permissions": role_data["permissions"],
                            "hoist": role_data.get("hoist", False),
                            "mentionable": role_data.get("mentionable", False),
                            "position": role_data.get("position", 0),
                        },
                        reason="AntiNuke rollback")
                    if new_role:
                        role_names_map[role_data["_id"]] = f"@{new_role.name}"
                except Exception:
                    pass
            await run_parallel(roles_to_restore, _restore_one_role,
                                concurrency=4, retry=2)
            for role_data in roles_to_restore:
                if role_data["_id"] in role_names_map:
                    reverted.append(role_names_map[role_data["_id"]])
                    try:
                        db["deleted_roles_cache"].delete_one({"_id": role_data["_id"]})
                    except Exception:
                        pass

        channel_names_map: Dict[int, str] = {}
        if channels_to_restore:
            async def _restore_one_channel(ch_data):
                try:
                    new_ch = await RollbackManager.restore_channel(
                        guild,
                        {
                            "id": ch_data["channel_id"],
                            "name": ch_data["name"],
                            "type": ch_data["type"],
                            "position": ch_data.get("position", 0),
                            "category_id": ch_data.get("category_id"),
                            "topic": ch_data.get("topic"),
                            "nsfw": ch_data.get("nsfw", False),
                            "slowmode": ch_data.get("slowmode", 0),
                            "overwrites": ch_data.get("overwrites", []),
                        },
                        reason="AntiNuke rollback")
                    if new_ch:
                        channel_names_map[ch_data["_id"]] = f"#{new_ch.name}"
                except Exception:
                    pass
            await run_parallel(channels_to_restore, _restore_one_channel,
                                concurrency=4, retry=2)
            for ch_data in channels_to_restore:
                if ch_data["_id"] in channel_names_map:
                    reverted.append(channel_names_map[ch_data["_id"]])
                    try:
                        db["deleted_channels_cache"].delete_one({"_id": ch_data["_id"]})
                    except Exception:
                        pass

        return reverted

    # ═══════════════════════════════════════════════════════════
    # PUNIÇÕES
    # ═══════════════════════════════════════════════════════════

    async def _punish_user(self, guild, user_id, reason, cfg,
                            action_type="unknown",
                            count_action=0, count_global=0,
                            feature_key=None):
        """Pune humano somente após revalidar o estado atual no banco.

        O cache continua sendo usado no caminho quente de detecção. Esta
        leitura final protege contra toggle de proteção feito no painel entre
        a detecção e a execução da punição. Em caso de falha no banco, falha
        fechada: não aplica punição.
        """
        try:
            cfg = AntiNukeManager.get_config_fresh(guild.id)
        except Exception:
            return

        if not cfg.get("enabled", False):
            return
        if feature_key and not cfg.get(feature_key, False):
            return

        member = guild.get_member(user_id)
        # Não classificar um executor desconhecido como humano. Sem membro
        # confirmado, não há punição automática.
        if member is None:
            return
        # O toggle de proteção de usuários não pode desativar a proteção
        # independente contra bots maliciosos.
        if member.bot:
            await self._punish_bot_malicious(
                guild, user_id, reason, cfg, action_type=action_type,
                count_action=count_action, count_global=count_global)
            return
        if not cfg.get("user_protection_enabled", True):
            return
        if self._is_immune_to_punish(cfg, guild, user_id, member):
            return

        whitelist_confirmation_required = self._is_whitelisted(
            cfg, user_id, member)
        punishment = cfg.get("punishment", "strip")
        # Regra específica do Anti-Nuke: bots recebem ban; humanos, no máximo,
        # kick (ou strip). Não altera o tratamento de bots.
        if punishment == "ban":
            punishment = "kick"
        threshold = int(cfg.get("threshold", 3))
        global_threshold = int(cfg.get("global_threshold", 6))
        severity = self._severity_from_counts(
            count_action, count_global, threshold, global_threshold)

        async def _execute():
            # A confirmação pode demorar; nunca executar com a configuração
            # capturada antes da staff desligar a proteção ou mudar whitelist.
            try:
                current_cfg = AntiNukeManager.get_config_fresh(guild.id)
            except Exception:
                return
            current_member = guild.get_member(user_id)
            if (not current_cfg.get("enabled", False)
                    or not current_cfg.get("user_protection_enabled", True)
                    or (feature_key and not current_cfg.get(feature_key, False))
                    or current_member is None
                    or self._is_immune_to_punish(
                        current_cfg, guild, user_id, current_member)):
                return
            if (self._is_whitelisted(current_cfg, user_id, current_member)
                    and not whitelist_confirmation_required):
                return
            cfg = current_cfg
            member = current_member
            effective_punishment = current_cfg.get("punishment", "strip")
            # Humanos nunca recebem ban automático pelo Anti-Nuke.
            if effective_punishment == "ban":
                effective_punishment = "kick"
            try:
                if member:
                    if effective_punishment == "strip":
                        dangerous = []
                        for r in member.roles:
                            if r.is_default():
                                continue
                            perms = r.permissions
                            if (perms.administrator or perms.ban_members or
                                perms.kick_members or perms.manage_guild or
                                perms.manage_roles or perms.manage_channels or
                                perms.manage_webhooks or
                                perms.manage_emojis_and_stickers):
                                dangerous.append(r)
                        if dangerous:
                            await member.remove_roles(
                                *dangerous, reason=f"AntiNuke: {reason}")
                    elif effective_punishment == "ban":
                        await guild.ban(
                            member, reason=f"AntiNuke: {reason}",
                            delete_message_days=1)
                    elif effective_punishment == "kick":
                        await member.kick(reason=f"AntiNuke: {reason}")
            except Exception:
                pass

            self._tracker.set_cooldown(guild.id, user_id)
            reverted = []
            if cfg.get("rollback_enabled"):
                reverted = await self._rollback_recent(
                    guild, cfg,
                    window_minutes=int(cfg.get("rollback_window_minutes", 5)))

            db = get_connection()
            db["antinuke_logs"].insert_one({
                "guild_id": guild.id, "user_id": user_id,
                "reason": reason, "action_type": action_type,
                "punishment": effective_punishment, "severity": severity,
                "count_action": count_action, "count_global": count_global,
                "reverted": reverted, "timestamp": _now(),
            })

            log = ProfessionalLog(
                title="Anti-Nuke Acionado",
                description="Um usuário executou ações destrutivas e as medidas de proteção foram aplicadas automaticamente.",
                severity=severity, footer="Stumble Hit AntiRaid • Anti-Nuke Ativo")
            log.set_target(member)
            log.add_field("Ação Detectada", f"`{action_type}`")
            log.add_field("Punição", f"`{effective_punishment}`")
            log.add_field("Contadores",
                           f"Ação: `{count_action}` | Global: `{count_global}`",
                           inline=False)
            if reverted:
                log.add_field(f"Restaurado ({len(reverted)})",
                               "\n".join(f"• {x}" for x in reverted[:10]),
                               inline=False)
            log.add_field("Motivo", fmt_reason(reason), inline=False)
            await self._send_log(guild, cfg, log.build())

            await self._notify_owner(
                guild, cfg, "🚨 Anti-Nuke acionado",
                f"**Usuário:** <@{user_id}>\n"
                f"**Punição:** `{effective_punishment}`\n"
                f"**Restaurado:** {len(reverted)} item(ns)")

        if self._is_whitelisted(cfg, user_id, member):
            cog = self.bot.get_cog("WhitelistConfirmCommands")
            if cog is None:
                # Sem o sistema de aprovação, membro confiável nunca é punido.
                return
            await cog.request_confirmation(
                guild, action_type, member, self.bot.user, reason, _execute)
            return
        await _execute()

    async def _punish_bot_malicious(self, guild, bot_id, reason, cfg,
                                     action_type="unknown",
                                     count_action=0, count_global=0):
        if bot_id == self.bot.user.id:
            return
        if bot_id == guild.owner_id:
            return
        try:
            cfg = AntiNukeManager.get_config_fresh(guild.id)
        except Exception:
            return
        if not cfg.get("enabled", False):
            return
        bot_member = guild.get_member(bot_id)
        is_whitelisted_bot = self._is_bot_whitelisted(
            cfg, bot_id, bot_member)

        if is_whitelisted_bot and await self._bot_whitelist_mode(guild.id) == "immune":
            return
        if is_whitelisted_bot:
            protection_cog = self.bot.get_cog("BotWhitelistProtectionCommands")
            if protection_cog and bot_member:
                try:
                    await protection_cog.proteger_bot_whitelisted(
                        guild=guild, bot_member=bot_member, motivo=reason,
                        action_type=action_type,
                        count_action=count_action, count_global=count_global)
                except Exception:
                    pass
                return
            await self._send_log(guild, cfg, discord.Embed(
                title="⚠️ Bot whitelisted bloqueado",
                description=(
                    f"**Bot:** {bot_member.mention if bot_member else bot_id}\n"
                    f"**Motivo:** {reason}\n"
                    f"**Ação:** Bloqueada (bot na whitelist)"
                ),
                color=discord.Color.orange(), timestamp=_now()))
            return

        # Sempre tenta o banimento primeiro. A hierarquia/permissão real é
        # confirmada pela API do Discord; só então usa mitigação como fallback.
        try:
            target = bot_member or discord.Object(id=int(bot_id))
            await guild.ban(target,
                             reason=f"AntiNuke: bot malicioso ({reason})",
                             delete_message_days=7)
        except Exception as e:
            if bot_member:
                await self._mitigate_bot(
                    guild, cfg, bot_member, reason,
                    action_type, count_action, count_global)
            await self._send_log(guild, cfg, discord.Embed(
                title="⚠️ Falha ao banir bot",
                description=f"Banimento falhou: `{type(e).__name__}`. "
                            "Mitigação aplicada quando possível.",
                color=discord.Color.red()))

        wl = list(cfg.get("whitelist_bots", []))
        if bot_id in wl:
            wl.remove(bot_id)
            AntiNukeManager.update(guild.id, "whitelist_bots", wl)

        self._tracker.set_cooldown(guild.id, bot_id)
        reverted = []
        if cfg.get("rollback_enabled"):
            await asyncio.sleep(0.5)
            reverted = await self._rollback_recent(
                guild, cfg,
                window_minutes=int(cfg.get("rollback_window_minutes", 5)))

        db = get_connection()
        db["antinuke_logs"].insert_one({
            "guild_id": guild.id, "user_id": bot_id,
            "reason": f"BOT MALICIOSO: {reason}",
            "action_type": action_type, "punishment": "ban_bot",
            "severity": "critical",
            "count_action": count_action, "count_global": count_global,
            "reverted": reverted, "timestamp": _now(),
        })

        log = ProfessionalLog(
            title="Bot Malicioso Banido",
            description="Um bot não autorizado executou ações destrutivas e foi banido imediatamente. Todos os danos foram revertidos.",
            severity="critical",
            footer="Stumble Hit AntiRaid • Bot Banido Automaticamente")
        log.set_target(bot_member)
        log.add_field("Ação Detectada", f"`{action_type}`")
        log.add_field("Modo", "⚡ Instantâneo")
        log.add_field("Contadores",
                       f"Ação: `{count_action}` | Global: `{count_global}`",
                       inline=False)
        if reverted:
            log.add_field(f"Restaurado ({len(reverted)})",
                           "\n".join(f"• {x}" for x in reverted[:10]),
                           inline=False)
        else:
            log.add_field("Restauração", "Nada precisou ser restaurado",
                           inline=False)
        log.add_field("Motivo", fmt_reason(reason), inline=False)
        await self._send_log(guild, cfg, log.build())

        await self._notify_owner(
            guild, cfg, "🚨 Bot Malicioso Banido",
            f"**Bot:** {bot_member or bot_id}\n"
            f"**Ação:** `{action_type}`\n"
            f"**Restaurado:** {len(reverted)} item(ns)")

    async def _mitigate_bot(self, guild, cfg, bot_member, reason,
                              action_type, count_action, count_global):
        removed = []
        try:
            dangerous = [
                r for r in bot_member.roles
                if not r.is_default() and (
                    r.permissions.administrator or
                    r.permissions.ban_members or
                    r.permissions.kick_members or
                    r.permissions.manage_roles or
                    r.permissions.manage_channels or
                    r.permissions.manage_guild or
                    r.permissions.manage_webhooks)
                and r < guild.me.top_role
            ]
            if dangerous:
                await bot_member.remove_roles(
                    *dangerous, reason=f"AntiNuke (fallback): {reason}")
                removed = [r.name for r in dangerous]
        except Exception:
            pass

        log = ProfessionalLog(
            title="Bot Malicioso Mitigado (Fallback)",
            description="Não foi possível banir o bot (hierarquia superior), mas as permissões perigosas foram removidas.",
            severity="critical",
            footer="Stumble Hit AntiRaid • Mitigação de Emergência")
        log.set_target(bot_member)
        log.add_field("Ação Detectada", f"`{action_type}`")
        log.add_field("Cargos Removidos",
                       "\n".join(f"• `{r}`" for r in removed) or "*nenhum*",
                       inline=False)
        log.add_field("Motivo", fmt_reason(reason), inline=False)
        log.add_field("⚠️ Ação Manual Necessária",
                       "O cargo do bot está acima do meu. **Mova meu cargo para cima dele** e remova-o manualmente.",
                       inline=False)
        await self._send_log(guild, cfg, log.build())

    # ═══════════════════════════════════════════════════════════
    # CANARY NUKE
    # ═══════════════════════════════════════════════════════════

    async def _check_canary_trigger(self, message, cfg):
        content = (message.content or "").strip().lower()
        if not content.startswith(PREFIX.lower()):
            return
        command_part = content[len(PREFIX):].strip()
        first_word = command_part.split()[0] if command_part else ""
        triggers = [t.lower() for t in cfg.get("canary_nuke_triggers", ["nuke"])]
        if first_word not in triggers:
            return
        # Só owner/admin/managers podem armar o Canary. Um membro comum não
        # pode ativar a janela de punição por escrever uma palavra no chat.
        if not self._can_manage_antinuke(cfg, message.author):
            return
        guild = message.guild
        duration = int(cfg.get("canary_nuke_duration", 300))
        now = time.time()
        self._canary[guild.id] = {
            "author_id": message.author.id,
            "author_name": str(message.author),
            "triggered_at": now, "expires_at": now + duration,
            "message_id": message.id, "channel_id": message.channel.id,
        }
        await self._notify_owner(
            guild, cfg, "🚨 Canary Nuke Ativado",
            f"**Autor:** {message.author.mention}\n"
            f"**Canal:** {message.channel.mention}\n"
            f"**Duração:** {duration // 60} min\n"
            f"Qualquer ação destrutiva será punida com BAN + rollback.")
        log = ProfessionalLog(
            title="Canary Nuke Ativado",
            description="Um gatilho manual foi acionado. Qualquer ação destrutiva nos próximos minutos resultará em banimento automático.",
            severity="high", footer="Stumble Hit AntiRaid • Canary Nuke")
        log.set_target(message.author)
        log.add_field("Canal", message.channel.mention)
        log.add_field("Duração", f"{duration // 60} min")
        await self._send_log(guild, cfg, log.build())

    def _is_canary_active(self, guild_id: int) -> Optional[dict]:
        canary = self._canary.get(guild_id)
        if not canary:
            return None
        if time.time() > canary["expires_at"]:
            self._canary.pop(guild_id, None)
            return None
        return canary

    async def _trigger_canary_ban(self, guild, executor_id, action, reason, cfg):
        canary = self._canary.get(guild.id)
        if not canary:
            return
        author_id = canary["author_id"]
        banned = []
        try:
            fresh_cfg = AntiNukeManager.get_config_fresh(guild.id)
        except Exception:
            return
        if not fresh_cfg.get("enabled", False):
            return
        if fresh_cfg.get("canary_nuke_ban_author", True):
            author = guild.get_member(author_id)
            if author and author.id != guild.owner_id:
                if author.bot:
                    if self._is_bot_whitelisted(fresh_cfg, author.id, author):
                        author = None
                if author and author.bot:
                    try:
                        await guild.ban(author,
                            reason="Canary Nuke: gatilho + raid",
                            delete_message_days=7)
                        banned.append(author_id)
                    except Exception:
                        pass
                elif (fresh_cfg.get("user_protection_enabled", True)
                      and not self._is_whitelisted(fresh_cfg, author_id, author)
                      and not self._is_immune_to_punish(
                          fresh_cfg, guild, author_id, author)):
                    try:
                        await author.kick(reason="Canary Nuke: gatilho + raid")
                        banned.append(author_id)
                    except Exception:
                        pass
        if (fresh_cfg.get("canary_nuke_ban_executor", True)
                and executor_id != author_id):
            executor = guild.get_member(executor_id)
            if executor and executor.id != guild.owner_id:
                if executor.bot:
                    if self._is_bot_whitelisted(fresh_cfg, executor.id, executor):
                        executor = None
                if executor and executor.bot:
                    try:
                        await guild.ban(executor,
                            reason="Canary Nuke: executou raid",
                            delete_message_days=7)
                        banned.append(executor_id)
                    except Exception:
                        pass
                elif (fresh_cfg.get("user_protection_enabled", True)
                      and not self._is_whitelisted(fresh_cfg, executor_id, executor)
                      and not self._is_immune_to_punish(
                          fresh_cfg, guild, executor_id, executor)):
                    try:
                        await executor.kick(reason="Canary Nuke: executou raid")
                        banned.append(executor_id)
                    except Exception:
                        pass
        reverted = []
        if fresh_cfg.get("rollback_enabled"):
            await asyncio.sleep(0.5)
            reverted = await self._rollback_recent(guild, fresh_cfg)
        self._canary.pop(guild.id, None)
        db = get_connection()
        db["antinuke_logs"].insert_one({
            "guild_id": guild.id, "user_id": executor_id,
            "reason": f"CANARY NUKE: {reason}",
            "action_type": f"canary:{action}",
            "punishment": "ban", "severity": "critical",
            "reverted": reverted, "banned_ids": banned,
            "timestamp": _now(),
        })
        log = ProfessionalLog(
            title="Canary Nuke Disparado",
            description="Uma ação destrutiva foi executada durante a vigilância do Canary Nuke. Todos os envolvidos foram banidos.",
            severity="critical", footer="Stumble Hit AntiRaid • Canary Nuke")
        exec_member = guild.get_member(executor_id)
        log.set_target(exec_member)
        log.add_field("Ação", f"`{action}`")
        log.add_field("Banidos", f"`{len(banned)}`")
        if reverted:
            log.add_field(f"Restaurado ({len(reverted)})",
                           "\n".join(f"• {x}" for x in reverted[:10]),
                           inline=False)
        log.add_field("Motivo", fmt_reason(reason), inline=False)
        await self._send_log(guild, cfg, log.build())
        await self._notify_owner(
            guild, cfg, "🚨 CANARY NUKE — RAID",
            f"**Ação:** `{action}`\n"
            f"**Bans:** {len(banned)}\n"
            f"**Restaurado:** {len(reverted)} item(ns)")

    # ═══════════════════════════════════════════════════════════
    # CORE: CHECK USER
    # ═══════════════════════════════════════════════════════════

    async def _check_user(self, guild, user_id, action, feature_key, reason,
                          prot_key=None, snapshot=None):
        if not ANTINUKE_ENABLED:
            return
        cfg = AntiNukeManager.get_config(guild.id)
        if not cfg.get("enabled"):
            return
        if not cfg.get(feature_key, True):
            return
        member = guild.get_member(user_id)
        is_bot = bool(member and member.bot)
        if is_bot and action == "bot_add":
            return  # tratado pela verificação dupla em _handle_bot_join
        if not is_bot and not cfg.get("user_protection_enabled", True):
            return

        is_whitelisted_user = (not is_bot) and self._is_whitelisted(
            cfg, user_id, member)
        is_whitelisted_bot = is_bot and self._is_bot_whitelisted(cfg, user_id, member)
        is_whitelisted = is_whitelisted_user or is_whitelisted_bot

        # O modo global de whitelist é humano; bots seguem os dois modos
        # específicos da whitelist_bots.
        if is_whitelisted_user and self._is_can_do_everything_mode(cfg):
            return
        if (is_bot and is_whitelisted_bot
                and await self._bot_whitelist_mode(guild.id) == "immune"):
            return

        canary = self._is_canary_active(guild.id)
        if is_bot and is_whitelisted_bot:
            canary = False  # bots whitelistados seguem seus limites próprios
        if canary and user_id not in (guild.owner_id, self.bot.user.id):
            await self._trigger_canary_ban(guild, user_id, action, reason, cfg)
            return

        if is_bot:
            if user_id == self.bot.user.id:
                return
            # Modo imune significa nenhuma proteção automática para esse bot.
            if (is_whitelisted_bot
                    and await self._bot_whitelist_mode(guild.id) == "immune"):
                return
            trusted_added = (not is_whitelisted_bot
                             and self._trusted_added_bot(guild.id, user_id))
            cooldown = float(cfg.get("punish_cooldown", 60))
            if self._tracker.is_on_cooldown(guild.id, user_id, cooldown):
                return
            # Bot explicitamente whitelistado: limites individuais e globais
            # são sempre pelo menos o limite normal + 1. Bot apenas adicionado
            # por humano confiável continua nos limites normais.
            if is_whitelisted_bot or trusted_added:
                # Hard limit também recebe +1 no modo threshold da whitelist.
                if is_whitelisted_bot and action in ("chan_create", "chan_del"):
                    hard_limit = int(cfg.get("hard_channel_limit", 5)) + 1
                    hard_count = self._tracker.register(
                        guild.id, user_id, f"hard_{action}", 10.0, 30.0)[0]
                    if hard_count >= hard_limit:
                        self._tracker.clear(guild.id, user_id)
                        await self._punish_bot_malicious(
                            guild, user_id,
                            f"HARD LIMIT whitelisted: {hard_count} canais em 10s",
                            cfg, action_type=action,
                            count_action=hard_count, count_global=hard_count)
                        return
                prot = prot_key or "channel_delete"
                threshold = self._get_threshold_for(
                    cfg, prot, is_whitelisted=is_whitelisted_bot)
                window = float(cfg.get("window_seconds", 15))
                global_threshold = int(cfg.get("global_threshold", 6))
                if is_whitelisted_bot:
                    global_threshold += 1
                global_window = float(cfg.get("global_window_seconds", 30))
                ca, cg = self._tracker.register(
                    guild.id, user_id, action, window, global_window)
                if ca >= threshold or cg >= global_threshold:
                    self._tracker.clear(guild.id, user_id)
                    label = "Bot whitelisted" if is_whitelisted_bot else "Bot adicionado por humano whitelisted"
                    await self._punish_bot_malicious(
                        guild, user_id, f"{label} excedeu os limites: {reason}",
                        cfg, action_type=action,
                        count_action=ca, count_global=cg)
                return

            # Bot adicionado por pessoa não autorizada é banido no próprio
            # evento de entrada; esta rota é uma segunda defesa para bots
            # sem registro de autorização.
            if action in ("chan_create", "chan_del"):
                hard_limit = int(cfg.get("hard_channel_limit", 5))
                count_chan = self._tracker.register(
                    guild.id, user_id, f"hard_{action}", 10.0, 30.0)[0]
                if count_chan >= hard_limit:
                    self._tracker.clear(guild.id, user_id)
                    await self._punish_bot_malicious(
                        guild, user_id,
                        f"HARD LIMIT: {count_chan} canais em 10s",
                        cfg, action_type=action,
                        count_action=count_chan, count_global=count_chan)
                    return
            self._tracker.clear(guild.id, user_id)
            await self._punish_bot_malicious(
                guild, user_id,
                f"Bot não autorizado executou ação: {reason}",
                cfg, action_type=action, count_action=1, count_global=1)
            return

        if is_whitelisted_user:
            pass

        if user_id == guild.owner_id:
            return
        if user_id == self.bot.user.id:
            return
        cooldown = float(cfg.get("punish_cooldown", 60))
        if self._tracker.is_on_cooldown(guild.id, user_id, cooldown):
            return
        prot = prot_key or "channel_delete"
        threshold = self._get_threshold_for(cfg, prot, is_whitelisted=is_whitelisted)
        window = float(cfg.get("window_seconds", 15))
        global_threshold = int(cfg.get("global_threshold", 6))
        global_window = float(cfg.get("global_window_seconds", 30))
        ca, cg = self._tracker.register(
            guild.id, user_id, action, window, global_window)
        if ca >= threshold or cg >= global_threshold:
            self._tracker.clear(guild.id, user_id)
            await self._punish_user(
                guild, user_id, reason, cfg,
                action_type=action, count_action=ca, count_global=cg,
                feature_key=feature_key)

    # ═══════════════════════════════════════════════════════════
    # LISTENERS
    # ═══════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        try:
            db = get_connection()
            data = {
                "guild_id": channel.guild.id,
                "channel_id": channel.id,
                "name": channel.name,
                "type": str(channel.type),
                "position": channel.position,
                "category_id": channel.category.id if channel.category else None,
                "topic": getattr(channel, "topic", None),
                "nsfw": getattr(channel, "nsfw", False),
                "slowmode": getattr(channel, "slowmode_delay", 0),
                "overwrites": [], "timestamp": _now(),
            }
            try:
                for target, ow in channel.overwrites.items():
                    allow, deny = ow.pair()
                    data["overwrites"].append({
                        "target_id": target.id,
                        "target_type": ("role"
                                        if isinstance(target, discord.Role)
                                        else "member"),
                        "allow": allow.value, "deny": deny.value})
            except Exception:
                pass
            db["deleted_channels_cache"].insert_one(data)
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_guild_role_delete(self, role):
        try:
            db = get_connection()
            db["deleted_roles_cache"].insert_one({
                "guild_id": role.guild.id, "role_id": role.id,
                "name": role.name, "color": role.color.value,
                "permissions": role.permissions.value,
                "hoist": role.hoist, "mentionable": role.mentionable,
                "position": role.position, "timestamp": _now(),
            })
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_audit_log_entry_create(self, entry):
        if not entry.guild:
            return
        if entry.user_id is None:
            return
        guild = entry.guild
        user_id = entry.user_id
        mapping = self._map_audit_action(entry)
        if not mapping:
            return
        feature_key, reason, track_action, prot_key = mapping

        if entry.action == discord.AuditLogAction.channel_update:
            target = entry.target
            if (target and hasattr(target, "name")
                    and has_suspicious_name(target.name)):
                await self._check_user(
                    guild, user_id, "chan_rename_suspicious",
                    "anti_channel_rename",
                    f"Canal com nome suspeito: {target.name}",
                    prot_key="channel_rename")
        elif entry.action == discord.AuditLogAction.role_update:
            target = entry.target
            if (target and hasattr(target, "name")
                    and has_suspicious_name(target.name)):
                await self._check_user(
                    guild, user_id, "role_rename_suspicious",
                    "anti_role_rename",
                    f"Cargo com nome suspeito: {target.name}",
                    prot_key="role_rename")

        try:
            await self._check_user(guild, user_id, track_action,
                                     feature_key, reason, prot_key=prot_key)
        except Exception:
            pass

    def _map_audit_action(self, entry):
        A = discord.AuditLogAction
        m = {
            A.channel_delete: ("anti_channel_delete", "Deletou canais em massa",
                                "chan_del", "channel_delete"),
            A.channel_create: ("anti_channel_create", "Criou canais em massa",
                                "chan_create", "channel_create"),
            A.channel_update: ("anti_channel_rename", "Renomeou canais",
                                "chan_rename", "channel_rename"),
            A.role_delete: ("anti_role_delete", "Deletou cargos em massa",
                             "role_del", "role_delete"),
            A.role_create: ("anti_role_create", "Criou cargos em massa",
                             "role_create", "role_create"),
            A.role_update: ("anti_role_rename", "Renomeou cargos",
                             "role_rename", "role_rename"),
            A.ban: ("anti_ban", "Baniu membros em massa", "ban", "ban"),
            A.kick: ("anti_kick", "Expulsou membros em massa", "kick", "kick"),
            A.webhook_create: ("anti_webhook", "Criou webhooks em massa",
                                "webhook_create", "webhook"),
            A.webhook_delete: ("anti_webhook", "Deletou webhooks em massa",
                                "webhook_del", "webhook"),
            A.emoji_delete: ("anti_emoji_delete", "Deletou emojis em massa",
                              "emoji_del", "emoji_delete"),
            A.emoji_create: ("anti_emoji_delete", "Criou emojis em massa",
                              "emoji_create", "emoji_delete"),
            A.sticker_delete: ("anti_sticker_delete", "Deletou stickers em massa",
                                "sticker_del", "sticker_delete"),
            A.thread_delete: ("anti_thread_delete", "Deletou threads em massa",
                               "thread_del", "thread_delete"),
            A.integration_create: ("anti_integration", "Adicionou integrações",
                                    "integration_create", "integration"),
            A.integration_delete: ("anti_integration", "Removeu integrações",
                                    "integration_del", "integration"),
            A.guild_update: ("anti_guild_update", "Alterou o servidor",
                              "guild_update", "guild_update"),
            A.bot_add: ("anti_bot_add", "Adicionou um bot",
                         "bot_add", "bot_add"),
            A.message_delete: ("anti_message_nuke", "Deletou mensagens em massa",
                                "msg_del", "message_nuke"),
            A.message_bulk_delete: ("anti_message_nuke", "Bulk delete de mensagens",
                                     "msg_bulk_del", "message_nuke"),
            A.member_update: ("anti_member_update", "Alterou membros em massa",
                               "member_update", "member_update"),
            A.member_disconnect: ("anti_mass_disconnect", "Desconectou membros em massa",
                                   "mass_disconnect", "mass_disconnect"),
        }
        return m.get(entry.action)

    @commands.Cog.listener()
    async def on_message(self, message):
        if not message.guild:
            return
        if not ANTINUKE_ENABLED:
            return
        guild = message.guild
        cfg = AntiNukeManager.get_config(guild.id)
        if not cfg.get("enabled"):
            return
        if cfg.get("canary_nuke_enabled"):
            await self._check_canary_trigger(message, cfg)

        author = message.author
        if author.id == self.bot.user.id:
            return
        if author.id == guild.owner_id:
            return

        is_bot = author.bot
        is_whitelisted_bot = is_bot and self._is_bot_whitelisted(cfg, author.id, author)
        bot_limit_offset = (1 if is_whitelisted_bot
                            and await self._bot_whitelist_mode(guild.id) == "threshold"
                            else 0)
        is_whitelisted_user = (not is_bot) and self._is_whitelisted(
            cfg, author.id, author)

        if is_whitelisted_user and self._is_can_do_everything_mode(cfg):
            return
        if (is_bot and is_whitelisted_bot
                and await self._bot_whitelist_mode(guild.id) == "immune"):
            return
        if is_whitelisted_user and self._is_confirma_kick_mode(cfg):
            return
        if not is_bot and not cfg.get("user_protection_enabled", True):
            return

        content = message.content or ""
        violations = []

        if cfg.get("anti_invite_spam") and INVITE_RX.search(content):
            violations.append(("invite_spam", "Convite em mensagem"))
        if cfg.get("anti_url_spam"):
            urls = URL_RX.findall(content)
            limite = int(cfg.get("limite_links", 3)) + bot_limit_offset
            if len(urls) >= limite:
                violations.append(("url_spam", f"{len(urls)} links"))
        if cfg.get("anti_mass_mention"):
            total = len(message.mentions) + len(message.role_mentions)
            limite = int(cfg.get("limite_mencoes", 5)) + bot_limit_offset
            if total >= limite:
                violations.append(("mass_mention", f"{total} menções"))
        if (cfg.get("anti_everyone_mention")
                and message.mention_everyone and not is_bot):
            violations.append(("everyone_mention", "@everyone"))
        if cfg.get("anti_emoji_spam"):
            ec = count_emojis(content)
            if ec >= int(cfg.get("hard_emoji_limit", 10)) + bot_limit_offset:
                violations.append(("emoji_spam", f"{ec} emojis"))
        if cfg.get("anti_custom_emoji_spam"):
            cc = count_custom_emojis(content)
            if cc >= int(cfg.get("hard_custom_emoji_limit", 8)) + bot_limit_offset:
                violations.append(("custom_emoji_spam", f"{cc} custom"))
        if cfg.get("anti_special_chars"):
            pct = special_chars_percent(content)
            if (pct >= int(cfg.get("hard_special_chars_percent", 50)) + bot_limit_offset
                    and len(content) >= 20):
                violations.append(("special_chars", f"{pct:.0f}% especiais"))
        if cfg.get("anti_caps_lock"):
            if is_caps_spam(content, int(cfg.get("hard_caps_percent", 80)) + bot_limit_offset):
                violations.append(("caps_lock",
                                    f"{caps_percent(content):.0f}% CAPS"))
        if cfg.get("anti_zalgo"):
            zc = count_zalgo(content)
            if zc >= int(cfg.get("hard_zalgo_limit", 5)) + bot_limit_offset:
                violations.append(("zalgo", f"{zc} diacríticos"))
        if cfg.get("anti_invisible_chars") and has_invisible_chars(content):
            violations.append(("invisible_chars", "Invisíveis/RTL"))
        if cfg.get("anti_repeated_chars"):
            run = max_repeated_char_run(content)
            if run >= int(cfg.get("hard_repeated_chars", 15)) + bot_limit_offset:
                violations.append(("repeated_chars", f"{run} repetidos"))
        if (is_bot and cfg.get("anti_msg_flood")):
            ca = self._tracker.register(
                guild.id, author.id, "msg_flood", 5.0, 10.0)[0]
            if ca >= int(cfg.get("hard_msg_flood_limit", 10)) + bot_limit_offset:
                violations.append(("msg_flood", f"{ca} msgs em 5s"))
        if len(content) >= 1000 + bot_limit_offset:
            violations.append(("massive_message",
                                f"{len(content)} caracteres"))

        for action, reason_txt in violations:
            await self._check_message_violation(
                message, author, action, reason_txt, cfg,
                is_bot=is_bot, is_whitelisted_bot=is_whitelisted_bot)
            break

    async def _check_message_violation(self, message, author, action,
                                         reason, cfg, is_bot,
                                         is_whitelisted_bot):
        guild = message.guild
        message_feature_keys = {
            "invite_spam": "anti_invite_spam",
            "url_spam": "anti_url_spam",
            "mass_mention": "anti_mass_mention",
            "everyone_mention": "anti_everyone_mention",
            "emoji_spam": "anti_emoji_spam",
            "custom_emoji_spam": "anti_custom_emoji_spam",
            "special_chars": "anti_special_chars",
            "caps_lock": "anti_caps_lock",
            "zalgo": "anti_zalgo",
            "invisible_chars": "anti_invisible_chars",
            "repeated_chars": "anti_repeated_chars",
            "msg_flood": "anti_msg_flood",
            "massive_message": None,
        }
        feature_key = message_feature_keys.get(action)

        # Primeira validação fresca: nenhuma mensagem é apagada e nenhum
        # caminho Canary/punição começa usando estado antigo do cache.
        try:
            cfg = AntiNukeManager.get_config_fresh(guild.id)
        except Exception:
            return
        if not cfg.get("enabled", False):
            return
        if feature_key and not cfg.get(feature_key, False):
            return
        if not is_bot and not cfg.get("user_protection_enabled", True):
            return

        if is_bot:
            is_whitelisted_bot = self._is_bot_whitelisted(
                cfg, author.id, author)
            if (is_whitelisted_bot
                    and await self._bot_whitelist_mode(guild.id) == "immune"):
                return
            trusted_added = (not is_whitelisted_bot
                             and self._trusted_added_bot(guild.id, author.id))
        else:
            trusted_added = False
            if self._is_immune_to_punish(cfg, guild, author.id, author):
                return
            if (self._is_whitelisted(cfg, author.id, author)
                    and self._is_can_do_everything_mode(cfg)):
                return

        canary = self._is_canary_active(guild.id)
        # Bots whitelistados e bots cuja adição foi autorizada seguem seus
        # limites; Canary não pode furar os dois regimes de threshold.
        if is_bot and (is_whitelisted_bot or trusted_added):
            canary = False
        if canary and not is_bot and author.id not in (
                guild.owner_id, self.bot.user.id):
            try:
                await message.delete()
            except Exception:
                pass
            await self._trigger_canary_ban(
                guild, author.id, action, reason, cfg)
            return

        # Bot sem autorização humana na entrada: defesa secundária. O handler
        # de entrada também tenta bani-lo imediatamente.
        if is_bot and not is_whitelisted_bot and not trusted_added:
            try:
                await message.delete()
            except Exception:
                pass
            if not self._tracker.is_on_cooldown(guild.id, author.id, 60):
                await self._punish_bot_malicious(
                    guild, author.id, f"Bot não autorizado: {reason}",
                    cfg, action_type=action,
                    count_action=1, count_global=1)
            return

        ca, cg = self._tracker.register(
            guild.id, author.id, action,
            float(cfg.get("window_seconds", 15)),
            float(cfg.get("global_window_seconds", 30)))
        action_threshold = int(cfg.get("threshold", 3))
        global_threshold = int(cfg.get("global_threshold", 6))
        if is_bot and is_whitelisted_bot:
            normal_message_threshold = int(cfg.get("threshold", 3))
            action_threshold = max(
                int(cfg.get("threshold_whitelisted", normal_message_threshold + 1)),
                normal_message_threshold + 1)
            global_threshold += 1

        if ca < action_threshold and cg < global_threshold:
            return

        self._tracker.clear(guild.id, author.id)
        # Última validação após o contador atingir o limite e imediatamente
        # antes de apagar a mensagem ou iniciar a punição.
        try:
            fresh_cfg = AntiNukeManager.get_config_fresh(guild.id)
        except Exception:
            return
        if (not fresh_cfg.get("enabled", False)
                or (feature_key and not fresh_cfg.get(feature_key, False))
                or (not is_bot and not fresh_cfg.get(
                    "user_protection_enabled", True))):
            return
        if is_bot:
            final_whitelisted_bot = self._is_bot_whitelisted(
                fresh_cfg, author.id, author)
            if (final_whitelisted_bot
                    and await self._bot_whitelist_mode(guild.id) == "immune"):
                return
            final_trusted_added = (not final_whitelisted_bot
                                   and self._trusted_added_bot(
                                       guild.id, author.id))
            if not final_whitelisted_bot and not final_trusted_added:
                # A autorização foi revogada durante o evento.
                await self._punish_bot_malicious(
                    guild, author.id, reason, fresh_cfg,
                    action_type=action, count_action=ca,
                    count_global=cg)
                return
            if final_whitelisted_bot:
                final_normal = int(fresh_cfg.get("threshold", 3))
                final_action_threshold = max(
                    int(fresh_cfg.get("threshold_whitelisted", final_normal + 1)),
                    final_normal + 1)
                final_global_threshold = int(
                    fresh_cfg.get("global_threshold", 6)) + 1
            else:
                final_action_threshold = int(fresh_cfg.get("threshold", 3))
                final_global_threshold = int(fresh_cfg.get("global_threshold", 6))
            if (ca < final_action_threshold
                    and cg < final_global_threshold):
                return
        else:
            if self._is_immune_to_punish(
                    fresh_cfg, guild, author.id, author):
                return
            if (self._is_whitelisted(fresh_cfg, author.id, author)
                    and self._is_can_do_everything_mode(fresh_cfg)):
                return

        try:
            await message.delete()
        except Exception:
            pass
        if is_bot:
            await self._punish_bot_malicious(
                guild, author.id, reason, fresh_cfg,
                action_type=action, count_action=ca, count_global=cg)
            return
        await self._punish_user(
            guild, author.id, reason, fresh_cfg,
            action_type=action, count_action=ca, count_global=cg,
            feature_key=feature_key)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        if member.bot:
            await self._handle_bot_join(member)
            return
        guild = member.guild
        if not ANTINUKE_ENABLED or not ANTIRAID_ENABLED:
            return
        cfg = AntiNukeManager.get_config(guild.id)
        if not cfg.get("enabled") or not cfg.get("anti_raid"):
            return
        # Toggle mestre do embed: se OFF, nenhum humano pode ser expulso
        # pelo Anti-Raid/lockdown. A checagem é repetida antes da ação.
        if not cfg.get("user_protection_enabled", True):
            return
        if self._is_whitelisted(cfg, member.id, member):
            return
        if cfg.get("raid_lockdown"):
            try:
                fresh = AntiNukeManager.get_config_fresh(guild.id)
            except Exception:
                return
            if (not fresh.get("enabled", False)
                    or not fresh.get("anti_raid", False)
                    or not fresh.get("user_protection_enabled", True)
                    or self._is_whitelisted(fresh, member.id, member)
                    or self._is_immune_to_punish(
                        fresh, guild, member.id, member)):
                return
            try:
                await member.kick(reason="AntiRaid: lockdown")
                return
            except Exception:
                pass

        min_days = int(cfg.get("min_account_age_days", 7))
        age_days = (discord.utils.utcnow() - member.created_at).days
        suspicious = False
        reasons = []
        if min_days > 0 and age_days < min_days:
            suspicious = True
            reasons.append(f"conta nova ({age_days}d)")
        if cfg.get("require_avatar") and member.avatar is None:
            suspicious = True
            reasons.append("sem avatar")
        if (has_suspicious_name(member.name)
                or (member.nick and has_suspicious_name(member.nick))):
            suspicious = True
            reasons.append("nome suspeito")

        rw = float(cfg.get("raid_join_window", 10))
        rt = int(cfg.get("raid_join_threshold", 5))
        count = self._tracker.register(guild.id, 0, "member_join", rw, rw)[0]
        mass_join = count >= rt
        if mass_join:
            reasons.append(f"mass join ({count})")
        if not suspicious and not mass_join:
            return

        reason_str = "AntiRaid: " + ", ".join(reasons)
        try:
            fresh = AntiNukeManager.get_config_fresh(guild.id)
        except Exception:
            return
        if (not fresh.get("enabled", False)
                or not fresh.get("anti_raid", False)
                or not fresh.get("user_protection_enabled", True)
                or self._is_whitelisted(fresh, member.id, member)
                or self._is_immune_to_punish(
                    fresh, guild, member.id, member)):
            return
        # Mantém a regra de segurança existente: humanos recebem kick;
        # banimento automático permanece reservado ao fluxo de bots.
        raid_action = fresh.get("raid_action", "kick")
        if raid_action == "ban":
            raid_action = "kick"
        try:
            if raid_action == "kick":
                await member.kick(reason=reason_str)
        except Exception:
            pass

        log = ProfessionalLog(
            title="Raid Detectada",
            description="Um novo membro apresentou características de raid e foi removido automaticamente pelo sistema.",
            severity="high", footer="Stumble Hit AntiRaid • Anti-Raid Ativo")
        log.set_target(member)
        log.add_field("Conta Criada",
                       f"<t:{int(member.created_at.timestamp())}:R>")
        log.add_field("Ação Aplicada", f"`{raid_action}`")
        log.add_field("Motivos", ", ".join(reasons), inline=False)
        await self._send_log(guild, cfg, log.build())

        if mass_join and not cfg.get("raid_lockdown"):
            AntiNukeManager.update(guild.id, "raid_lockdown", True)
            await self._send_log(guild, cfg, discord.Embed(
                title="🔒 Lockdown Ativado",
                description=f"**{count} joins** em **{rw}s** — lockdown automático.",
                color=discord.Color.dark_red(), timestamp=_now()))
            await self._notify_owner(
                guild, cfg, "🔒 Raid detectada",
                f"**{count} joins** em **{rw}s**.\nLockdown ativado.")

        if mass_join:
            try:
                raid_cog = self.bot.get_cog("RaidSuspectsCommands")
                if raid_cog:
                    await raid_cog._processar_raid(
                        guild,
                        motivo=f"Mass join ({count} membros em {int(rw)}s)",
                        tipo="mass_join")
            except Exception:
                pass

    async def _handle_bot_join(self, bot_member):
        """Aplica as duas verificações obrigatórias ao adicionar um bot.

        1) Quem adicionou precisa estar na whitelist humana (ID ou cargo).
           Caso contrário, o bot recém-adicionado é banido automaticamente.
        2) Se o humano estiver autorizado, o bot também precisa estar na
           whitelist de bots para receber os limites elevados/imunidade.
           Sem whitelist própria, ele permanece sob os limites normais e será
           banido quando os exceder.
        """
        guild = bot_member.guild
        if not ANTINUKE_ENABLED:
            return
        try:
            cfg = AntiNukeManager.get_config_fresh(guild.id)
        except Exception:
            return
        if not cfg.get("enabled", False) or not cfg.get("anti_bot_add", True):
            return

        # Aguarda a auditoria do Discord registrar o evento de adição.
        added_by = None
        try:
            await asyncio.sleep(1)
            async for entry in guild.audit_logs(
                    limit=10, action=discord.AuditLogAction.bot_add):
                target_id = getattr(getattr(entry, "target", None), "id", None)
                created_at = getattr(entry, "created_at", None)
                # Não associar a entrada atual a um registro antigo de outro
                # momento. Se não houver data confiável, a autorização falha.
                recent = bool(
                    created_at and
                    (discord.utils.utcnow() - created_at).total_seconds() <= 120
                    and (discord.utils.utcnow() - created_at).total_seconds() >= -5
                )
                if target_id == bot_member.id and recent:
                    added_by = entry.user_id
                    break
        except Exception:
            added_by = None

        if added_by == self.bot.user.id:
            return

        adder = None
        if added_by:
            try:
                adder = await guild.fetch_member(added_by)
            except Exception:
                adder = None
        # Falha ao identificar o responsável não concede autorização.
        # Banir o bot evita deixar passar uma adição que não pôde ser validada.
        adder_is_human_whitelisted = bool(
            adder and not adder.bot and self._is_whitelisted(
                cfg, adder.id, adder))
        if not adder_is_human_whitelisted:
            try:
                final_cfg = AntiNukeManager.get_config_fresh(guild.id)
            except Exception:
                return
            if (not final_cfg.get("enabled", False)
                    or not final_cfg.get("anti_bot_add", True)):
                return
            current_adder = None
            if added_by:
                try:
                    current_adder = await guild.fetch_member(added_by)
                except Exception:
                    current_adder = None
            if not (current_adder and not current_adder.bot
                    and self._is_whitelisted(final_cfg, current_adder.id,
                                             current_adder)):
                try:
                    await guild.ban(
                        bot_member,
                        reason=("Anti-Nuke: bot adicionado por pessoa fora "
                                "da whitelist humana"),
                        delete_message_days=0)
                except Exception:
                    await self._mitigate_bot(
                        guild, final_cfg, bot_member,
                        "Adicionado por pessoa fora da whitelist humana",
                        "bot_add", 1, 1)
                log = discord.Embed(
                    title="🚫 Bot banido — adicionador não autorizado",
                    description=(
                        f"O bot {bot_member.mention} foi banido porque o "
                        "responsável pela adição não estava na whitelist "
                        "humana ou não pôde ser verificado."),
                    color=discord.Color.red(), timestamp=_now())
                await self._send_log(guild, final_cfg, log)
                return
            cfg = final_cfg
            adder = current_adder

        # A pessoa está autorizada. Ainda assim, a whitelist de bots é
        # independente e exige o ID explícito do bot.
        try:
            final_cfg = AntiNukeManager.get_config_fresh(guild.id)
        except Exception:
            return
        current_adder = None
        if added_by:
            try:
                current_adder = await guild.fetch_member(added_by)
            except Exception:
                current_adder = None
        if (not final_cfg.get("enabled", False)
                or not final_cfg.get("anti_bot_add", True)):
            return
        if (current_adder is None or current_adder.bot
                or not self._is_whitelisted(
                    final_cfg, current_adder.id, current_adder)):
            try:
                await guild.ban(
                    bot_member,
                    reason="Anti-Nuke: whitelist humana do adicionador ausente/revogada",
                    delete_message_days=0)
            except Exception:
                await self._mitigate_bot(
                    guild, final_cfg, bot_member,
                    "Whitelist humana do adicionador ausente/revogada",
                    "bot_add", 1, 1)
            await self._send_log(guild, final_cfg, discord.Embed(
                title="🚫 Bot banido — autorização humana inválida",
                description=(f"O responsável por adicionar {bot_member.mention} "
                             "não está mais na whitelist humana."),
                color=discord.Color.red(), timestamp=_now()))
            return

        bot_is_explicitly_whitelisted = self._is_bot_whitelisted(
            final_cfg, bot_member.id, bot_member)
        try:
            self._remember_trusted_added_bot(
                guild.id, bot_member.id, current_adder.id)
        except Exception:
            if not bot_is_explicitly_whitelisted:
                try:
                    await guild.ban(
                        bot_member,
                        reason="Anti-Nuke: não foi possível registrar autorização do bot",
                        delete_message_days=0)
                except Exception:
                    await self._mitigate_bot(
                        guild, final_cfg, bot_member,
                        "Falha ao registrar autorização do bot",
                        "bot_add", 1, 1)
                return
        if bot_is_explicitly_whitelisted:
            mode = await self._bot_whitelist_mode(guild.id)
            status = ("imunidade total" if mode == "immune"
                      else "limites elevados (normal + 1)")
            description = (f"Bot {bot_member.mention} adicionado por "
                           f"{current_adder.mention}; whitelist humana e de bots confirmadas: {status}.")
        else:
            status = "limites normais; banimento ao excedê-los"
            description = (f"Bot {bot_member.mention} adicionado por "
                           f"{current_adder.mention}, que está na whitelist humana, "
                           "mas o bot não foi incluído na whitelist de bots.")

        await self._send_log(guild, final_cfg, discord.Embed(
            title="🤖 Verificação de adição de bot",
            description=description + f"\n**Tratamento:** {status}",
            color=discord.Color.orange(), timestamp=_now()))

    # ═══════════════════════════════════════════════════════════
    # MÉTODO PÚBLICO: processar_feature (chamado pelo painel)
    # ═══════════════════════════════════════════════════════════

    async def processar_feature(self, ctx, feature: str = None,
                                 state: str = None, extra: str = None,
                                 skip_perm_check: bool = False):
        """
        Processa feature do comando >antinuke.
        🆕 v13.5: verifica permissão (a não ser que skip_perm_check=True).
        """
        """Compatibilidade legada: configurações são exclusivas do painel."""
        if not feature:
            return
        try:
            await ctx.send(embed=embed_warning(
                "⚠️ A configuração do Anti-Nuke é feita exclusivamente pelo painel embed. "
                "Use `>antinuke` sem argumentos."))
        except Exception:
            pass
        return

    # ═══════════════════════════════════════════════════════════
    # 🆕 v13.5: PERMISSÕES
    # ═══════════════════════════════════════════════════════════

    async def _handle_permissions(self, ctx, cfg, state, extra):
        """
        Gerencia permissões do anti-nuke.
        Subcomandos: addrole, removerole, adduser, removeuser, list, require, reset
        """
        if not state:
            return await ctx.send(embed=embed_info(
                "Uso:\n"
                "`>antinuke perm addrole @cargo`\n"
                "`>antinuke perm removerole @cargo`\n"
                "`>antinuke perm adduser @user`\n"
                "`>antinuke perm removeuser @user`\n"
                "`>antinuke perm require on/off`\n"
                "`>antinuke perm list`"))

        sub = state.lower()

        # Listar.
        if sub in ("list", "ls"):
            return await self._show_permissions(ctx, cfg)

        # require on/off.
        if sub in ("require", "exigir"):
            if not extra or extra.lower() not in ("on", "off"):
                return await ctx.send(embed=embed_error("❌ Use: `>antinuke perm require on/off`"))
            enabled = extra.lower() == "on"
            AntiNukeManager.update(ctx.guild.id, "antinuke_require_manager", enabled)
            return await ctx.send(embed=embed_success(
                f"✅ Exigir manager: {'ON' if enabled else 'OFF'}"))

        # addrole / removerole / adduser / removeuser.
        if not extra or not extra.startswith("<@"):
            return await ctx.send(embed=embed_error(
                "❌ Use: `>antinuke perm addrole @cargo` (ou adduser @user)"))

        # Cargo (<@&123>).
        if extra.startswith("<@&") and extra.endswith(">"):
            rid = int(extra[3:-1])
            roles = list(cfg.get("antinuke_managers_roles", []))
            if sub in ("addrole", "addcargo", "add"):
                if rid not in roles:
                    roles.append(rid)
                    AntiNukeManager.update(ctx.guild.id, "antinuke_managers_roles", roles)
                return await ctx.send(embed=embed_success(
                    f"✅ <@&{rid}> pode mexer no anti-nuke."))
            elif sub in ("removerole", "removecargo", "remove"):
                roles = [x for x in roles if x != rid]
                AntiNukeManager.update(ctx.guild.id, "antinuke_managers_roles", roles)
                return await ctx.send(embed=embed_success(f"✅ <@&{rid}> removido."))
            else:
                return await ctx.send(embed=embed_error(
                    "❌ Use: `addrole`, `removerole`, `adduser`, `removeuser`"))

        # User (<@123>).
        if extra.startswith("<@") and extra.endswith(">"):
            uid = int(extra[2:-1].split("!")[-1])
            users = list(cfg.get("antinuke_managers_users", []))
            if sub in ("adduser", "add"):
                if uid not in users:
                    users.append(uid)
                    AntiNukeManager.update(ctx.guild.id, "antinuke_managers_users", users)
                return await ctx.send(embed=embed_success(
                    f"✅ <@{uid}> pode mexer no anti-nuke."))
            elif sub in ("removeuser", "remove"):
                users = [x for x in users if x != uid]
                AntiNukeManager.update(ctx.guild.id, "antinuke_managers_users", users)
                return await ctx.send(embed=embed_success(f"✅ <@{uid}> removido."))
            else:
                return await ctx.send(embed=embed_error(
                    "❌ Use: `adduser`, `removeuser`"))

        return await ctx.send(embed=embed_error(
            "❌ Use: `addrole`, `removerole`, `adduser`, `removeuser`"))

    async def _show_permissions(self, ctx, cfg):
        """Mostra as permissões do anti-nuke."""
        roles = cfg.get("antinuke_managers_roles", [])
        users = cfg.get("antinuke_managers_users", [])
        require = cfg.get("antinuke_require_manager", True)
        embed = discord.Embed(
            title="🔐 Permissões do Anti-Nuke",
            description=(
                "**Owner sempre pode.**\n"
                f"**Exigir manager:** {'🟢 SIM' if require else '🔴 NÃO'}\n"
                + ("*(se OFF, admin do Discord também pode)*" if not require else "")
            ),
            color=discord.Color.purple(),
            timestamp=_now())
        embed.add_field(
            name=f"👑 Cargos ({len(roles)})",
            value="\n".join(f"• <@&{r}>" for r in roles[:15]) or "*vazio*",
            inline=False)
        embed.add_field(
            name=f"👤 Users ({len(users)})",
            value="\n".join(f"• <@{u}>" for u in users[:15]) or "*vazio*",
            inline=False)
        await ctx.send(embed=embed)

    # ═══════════════════════════════════════════════════════════
    # 🆕 v13.5: COR
    # ═══════════════════════════════════════════════════════════

    async def _handle_color(self, ctx, cfg, state):
        """Gerencia a cor do painel."""
        # Sem args → mostra atual.
        if not state:
            db = get_connection()
            doc = db["antinuke_panel_config"].find_one(
                {"guild_id": ctx.guild.id}) or {}
            cor = doc.get("color")
            if cor:
                return await ctx.send(embed=embed_info(
                    f"🎨 Cor atual do painel: `{color_to_hex(int(cor))}`"))
            return await ctx.send(embed=embed_info(
                "🎨 Cor atual: **padrão** (dark_red).\n"
                "Use `>antinuke cor #ff0000` pra mudar."))

        # Reset.
        if state.lower() in ("reset", "padrao", "default"):
            db = get_connection()
            db["antinuke_panel_config"].update_one(
                {"guild_id": ctx.guild.id},
                {"$unset": {"color": ""}}, upsert=True)
            return await ctx.send(embed=embed_success(
                "✅ Cor resetada para o padrão (dark_red)."))

        # Parse.
        cor = parse_color(state)
        if cor is None:
            return await ctx.send(embed=embed_error(
                "❌ Cor inválida. Use: `#ff0000`, `red`, `rgb(255,0,0)`, "
                "`0xff0000` ou decimal."))

        db = get_connection()
        db["antinuke_panel_config"].update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"color": cor}}, upsert=True)
        return await ctx.send(embed=embed_success(
            f"✅ Cor do painel definida: `{color_to_hex(cor)}`"))

    # ═══════════════════════════════════════════════════════════
    # MÉTODOS AUXILIARES (whitelist, approver, stats)
    # ═══════════════════════════════════════════════════════════

    async def _handle_whitelist(self, ctx, cfg, target, extra):
        """Gerencia whitelist."""
        if not self._can_manage_whitelist(cfg, ctx.author):
            return await ctx.send(embed=embed_error(
                "❌ Você não tem permissão para gerenciar a whitelist.\n"
                "Peça pra um admin configurar em `>antinuke` → 🔐 Permissões."))
        if not target:
            return await ctx.send(embed=embed_error(
                "❌ Use: `>antinuke whitelist user/role/bot/exclude @x`"))
        if target.lower() in ("list", "ls"):
            return await self._show_whitelist(ctx, cfg)
        if target.lower() in ("remove", "rm"):
            return await self._handle_whitelist_remove(ctx, cfg, extra)
        if not extra:
            return await ctx.send(embed=embed_error("❌ Use: `user/role/bot/exclude @x`"))

        kind = target.lower()
        value = extra.strip()

        if kind in ("user", "u"):
            if not (value.startswith("<@") and value.endswith(">")):
                return await ctx.send(embed=embed_error("❌ Use: `user @user`"))
            uid = int(value[2:-1].split("!")[-1])
            wl = list(cfg.get("whitelist_users", []))
            if uid not in wl:
                wl.append(uid)
                AntiNukeManager.update(ctx.guild.id, "whitelist_users", wl)
            return await ctx.send(embed=embed_success(f"✅ <@{uid}> na whitelist."))

        if kind in ("exclude", "exception", "excecao", "exceção"):
            if not (value.startswith("<@") and value.endswith(">")):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `exclude @user` para remover a imunidade herdada de cargos."))
            uid = int(value[2:-1].split("!")[-1])
            if uid not in self._normalized_ids(cfg.get("whitelist_role_exclusions", [])):
                AntiNukeManager.add_to_list(
                    ctx.guild.id, "whitelist_role_exclusions", uid)
            return await ctx.send(embed=embed_success(
                f"✅ <@{uid}> não herdará a whitelist dos cargos. "
                "Uma entrada direta em whitelist_users ainda prevalece."))

        if kind in ("role", "r"):
            if not (value.startswith("<@&") and value.endswith(">")):
                return await ctx.send(embed=embed_error("❌ Use: `role @cargo`"))
            rid = int(value[3:-1])
            wl = list(cfg.get("whitelist_roles", []))
            if rid not in wl:
                wl.append(rid)
                AntiNukeManager.update(ctx.guild.id, "whitelist_roles", wl)
            return await ctx.send(embed=embed_success(f"✅ <@&{rid}> na whitelist."))

        if kind in ("bot", "b"):
            if not (value.startswith("<@") and value.endswith(">")):
                return await ctx.send(embed=embed_error("❌ Use: `bot @bot`"))
            raw = value[2:-1]
            if raw.startswith("&"):
                return await ctx.send(embed=embed_error(
                    "❌ Você mencionou um cargo. Use `@bot`."))
            bid = int(raw.split("!")[-1])
            wl_bots = list(cfg.get("whitelist_bots", []))
            if bid not in wl_bots:
                wl_bots.append(bid)
            AntiNukeManager.update(ctx.guild.id, "whitelist_bots", wl_bots)
            self._pending_bots[ctx.guild.id].pop(bid, None)
            return await ctx.send(embed=embed_success(
                f"✅ Bot <@{bid}> adicionado somente à whitelist de bots. "
                "O responsável pela adição também precisa estar na whitelist humana."))

        if kind in ("check", "verificar", "ver"):
            if not (value.startswith("<@") and value.endswith(">")):
                return await ctx.send(embed=embed_error("❌ Use: `check @user`"))
            raw = value[2:-1]
            if raw.startswith("&"):
                target_id = int(raw[1:])
            else:
                target_id = int(raw.split("!")[-1])
            in_bots = target_id in cfg.get("whitelist_bots", [])
            in_users = target_id in cfg.get("whitelist_users", [])
            in_approvers = target_id in cfg.get("bot_approvers", [])
            member = ctx.guild.get_member(target_id)
            is_bot_flag = bool(member and member.bot)
            embed = discord.Embed(
                title="🔍 WHITELIST CHECK",
                description=f"**Alvo:** {value} (`{target_id}`)",
                color=discord.Color.blurple(), timestamp=_now())
            embed.add_field(name="🤖 Whitelist Bots",
                             value="✅ SIM" if in_bots else "❌ NÃO", inline=True)
            embed.add_field(name="👤 Whitelist Users",
                             value="✅ SIM" if in_users else "❌ NÃO", inline=True)
            embed.add_field(name="✅ Approvers",
                             value="✅ SIM" if in_approvers else "❌ NÃO", inline=True)
            return await ctx.send(embed=embed)

        return await ctx.send(embed=embed_error("❌ Use: `user/role/bot/check`"))

    async def _handle_whitelist_remove(self, ctx, cfg, value):
        if not value:
            return await ctx.send(embed=embed_error("❌ Use: `remove @x`"))
        if value.startswith("<@&") and value.endswith(">"):
            rid = int(value[3:-1])
            wl = [x for x in cfg.get("whitelist_roles", []) if x != rid]
            AntiNukeManager.update(ctx.guild.id, "whitelist_roles", wl)
            return await ctx.send(embed=embed_success(f"✅ <@&{rid}> removido."))
        if value.startswith("<@") and value.endswith(">"):
            uid = int(value[2:-1].split("!")[-1])
            removed = False
            for key in ("whitelist_users", "whitelist_bots", "whitelist_role_exclusions"):
                wl = list(cfg.get(key, []))
                if uid in wl:
                    wl.remove(uid)
                    AntiNukeManager.update(ctx.guild.id, key, wl)
                    removed = True
            if removed:
                return await ctx.send(embed=embed_success(
                    f"✅ <@{uid}> removido de todas as listas."))
            return await ctx.send(embed=embed_warning("⚠️ Não estava na whitelist."))
        return await ctx.send(embed=embed_error("❌ Use: `remove @x`"))

    async def _show_whitelist(self, ctx, cfg):
        embed = discord.Embed(title="📋 WHITELIST",
                               color=discord.Color.blurple(),
                               timestamp=_now())
        users = cfg.get("whitelist_users", [])
        roles = cfg.get("whitelist_roles", [])
        bots = cfg.get("whitelist_bots", [])
        approvers = cfg.get("bot_approvers", [])
        approver_roles = cfg.get("bot_approvers_roles", [])
        managers_users = cfg.get("antinuke_managers_users", [])
        managers_roles = cfg.get("antinuke_managers_roles", [])
        embed.add_field(name=f"👤 Users ({len(users)})",
                         value="\n".join(f"• <@{u}>" for u in users[:10]) or "*vazio*",
                         inline=False)
        embed.add_field(name=f"👑 Cargos ({len(roles)})",
                         value="\n".join(f"• <@&{r}>" for r in roles[:10]) or "*vazio*",
                         inline=False)
        embed.add_field(name=f"🤖 Bots ({len(bots)})",
                         value="\n".join(f"• <@{b}>" for b in bots[:10]) or "*vazio*",
                         inline=False)
        exclusions = cfg.get("whitelist_role_exclusions", [])
        embed.add_field(name=f"🚫 Exceções de cargos ({len(exclusions)})",
                         value="\n".join(f"• <@{u}>" for u in exclusions[:10]) or "*vazio*",
                         inline=False)
        embed.add_field(name=f"✅ Aprovadores users ({len(approvers)})",
                         value="\n".join(f"• <@{a}>" for a in approvers[:5]) or "*vazio*",
                         inline=True)
        embed.add_field(name=f"✅ Aprovadores cargos ({len(approver_roles)})",
                         value="\n".join(f"• <@&{r}>" for r in approver_roles[:5]) or "*vazio*",
                         inline=True)
        embed.add_field(name=f"🔐 Managers users ({len(managers_users)})",
                         value="\n".join(f"• <@{u}>" for u in managers_users[:5]) or "*vazio*",
                         inline=True)
        embed.add_field(name=f"🔐 Managers cargos ({len(managers_roles)})",
                         value="\n".join(f"• <@&{r}>" for r in managers_roles[:5]) or "*vazio*",
                         inline=True)
        embed.add_field(name="🎯 Modo",
                         value=f"`{cfg.get('whitelist_mode', 'confirm_kick')}`",
                         inline=False)
        embed.add_field(name="🔒 Exigir manager",
                         value="🟢 SIM" if cfg.get("antinuke_require_manager", True) else "🔴 NÃO",
                         inline=False)
        await ctx.send(embed=embed)

    async def _handle_approver(self, ctx, cfg, value, op):
        if not self._can_approve_bots(cfg, ctx.author):
            return await ctx.send(embed=embed_error(
                "❌ Você não tem permissão para gerenciar aprovadores."))
        if not value or not value.startswith("<@"):
            return await ctx.send(embed=embed_error(
                "❌ Use: `>antinuke approver @user` ou `@cargo`"))
        if value.startswith("<@&") and value.endswith(">"):
            rid = int(value[3:-1])
            roles = list(cfg.get("bot_approvers_roles", []))
            if op == "add":
                if rid not in roles:
                    roles.append(rid)
                    AntiNukeManager.update(ctx.guild.id, "bot_approvers_roles", roles)
                return await ctx.send(embed=embed_success(
                    f"✅ <@&{rid}> agora pode aprovar bots."))
            roles = [x for x in roles if x != rid]
            AntiNukeManager.update(ctx.guild.id, "bot_approvers_roles", roles)
            return await ctx.send(embed=embed_success(f"✅ <@&{rid}> removido."))
        if value.startswith("<@") and value.endswith(">"):
            uid = int(value[2:-1].split("!")[-1])
            approvers = list(cfg.get("bot_approvers", []))
            if op == "add":
                if uid not in approvers:
                    approvers.append(uid)
                    AntiNukeManager.update(ctx.guild.id, "bot_approvers", approvers)
                return await ctx.send(embed=embed_success(
                    f"✅ <@{uid}> agora pode aprovar bots."))
            approvers = [x for x in approvers if x != uid]
            AntiNukeManager.update(ctx.guild.id, "bot_approvers", approvers)
            return await ctx.send(embed=embed_success(f"✅ <@{uid}> removido."))
        return await ctx.send(embed=embed_error(
            "❌ Use: `>antinuke approver @user` ou `@cargo`"))

    # Comando legado desregistrado: aprovação/whitelist é feita pelo painel.
    async def approve_bot(self, ctx, bot_id: str = None):
        cfg = AntiNukeManager.get_config(ctx.guild.id)
        if not self._can_approve_bots(cfg, ctx.author):
            return await ctx.send(embed=embed_error(
                "❌ Você não tem permissão para aprovar bots."))
        if not bot_id:
            pend = self._pending_bots.get(ctx.guild.id, {})
            if not pend:
                return await ctx.send(embed=embed_info("📋 Nenhum bot pendente."))
            embed = discord.Embed(title="🤖 Bots pendentes",
                                   color=discord.Color.orange(),
                                   timestamp=_now())
            for bid, info in list(pend.items())[:10]:
                embed.add_field(name=f"`{bid}` — {info['bot_name']}",
                                 value=f"Por <@{info['added_by']}>", inline=False)
            return await ctx.send(embed=embed)
        try:
            target_id = int(bot_id)
        except Exception:
            return await ctx.send(embed=embed_error("❌ ID inválido."))
        wl_bots = list(cfg.get("whitelist_bots", []))
        if target_id not in wl_bots:
            wl_bots.append(target_id)
            AntiNukeManager.update(ctx.guild.id, "whitelist_bots", wl_bots)
        self._pending_bots[ctx.guild.id].pop(target_id, None)
        await ctx.send(embed=embed_success(
            f"✅ Bot `{target_id}` adicionado à whitelist de bots. "
            "A pessoa que o adicionou também precisa estar na whitelist humana."))

    async def _manual_rollback(self, ctx, cfg):
        msg = await ctx.send(embed=embed_info("⏳ Restaurando..."))
        reverted = await self._rollback_recent(
            ctx.guild, cfg,
            window_minutes=int(cfg.get("rollback_window_minutes", 5)))
        if not reverted:
            return await msg.edit(embed=embed_info("📋 Nada para restaurar."))
        embed = discord.Embed(title=f"✅ RESTAURADO ({len(reverted)})",
                               description="\n".join(f"• {x}" for x in reverted[:30]),
                               color=discord.Color.green(),
                               timestamp=_now())
        await msg.edit(embed=embed)

    async def _show_canary_status(self, ctx, cfg):
        canary = self._is_canary_active(ctx.guild.id)
        triggers = cfg.get("canary_nuke_triggers", [])
        duration = int(cfg.get("canary_nuke_duration", 300))
        embed = discord.Embed(
            title="🎯 Canary Nuke — Status",
            color=(discord.Color.dark_red() if canary else discord.Color.blurple()),
            timestamp=_now())
        embed.add_field(name="Sistema",
                         value=("✅ ATIVO" if cfg.get("canary_nuke_enabled") else "❌ OFF"),
                         inline=True)
        embed.add_field(name="Duração", value=f"`{duration}s`", inline=True)
        embed.add_field(name="Triggers",
                         value=", ".join(f"`{t}`" for t in triggers) or "*Nenhum*",
                         inline=False)
        if canary:
            elapsed = int(time.time() - canary["triggered_at"])
            remaining = max(0, int(canary["expires_at"] - time.time()))
            embed.add_field(name="🚨 VIGILÂNCIA ATIVA",
                             value=f"**Autor:** <@{canary['author_id']}>\n"
                                   f"**Desde:** {elapsed}s\n"
                                   f"**Expira em:** {remaining}s",
                             inline=False)
        await ctx.send(embed=embed)

    async def _show_stats(self, ctx):
        db = get_connection()
        total = db["antinuke_logs"].count_documents({"guild_id": ctx.guild.id})
        last_24h = db["antinuke_logs"].count_documents({
            "guild_id": ctx.guild.id,
            "timestamp": {"$gte": _now() - timedelta(hours=24)}})
        cache_ch = db["deleted_channels_cache"].count_documents({"guild_id": ctx.guild.id})
        cache_ro = db["deleted_roles_cache"].count_documents({"guild_id": ctx.guild.id})
        embed = discord.Embed(title="📊 ESTATÍSTICAS",
                               color=discord.Color.blurple(),
                               timestamp=_now())
        embed.add_field(name="Total", value=str(total), inline=True)
        embed.add_field(name="Últimas 24h", value=str(last_24h), inline=True)
        embed.add_field(name="🗄️ Cache Rollback",
                         value=f"Canais: `{cache_ch}`\nCargos: `{cache_ro}`",
                         inline=True)
        await ctx.send(embed=embed)

    async def _show_logs(self, ctx, amount=None):
        try:
            n = int(amount) if amount else 10
        except Exception:
            n = 10
        n = max(1, min(n, 25))
        db = get_connection()
        logs = list(db["antinuke_logs"].find(
            {"guild_id": ctx.guild.id}).sort("timestamp", -1).limit(n))
        if not logs:
            return await ctx.send(embed=embed_info("📋 Nenhum log."))
        embed = discord.Embed(title=f"📋 ÚLTIMOS {len(logs)} LOGS",
                               color=discord.Color.blurple(),
                               timestamp=_now())
        for log in logs:
            ts = log.get("timestamp", _now())
            ts_str = (ts.strftime("%d/%m %H:%M")
                      if hasattr(ts, "strftime") else str(ts))
            value = (f"**User:** <@{log.get('user_id')}>\n"
                     f"**Motivo:** {log.get('reason', '?')}\n"
                     f"**Punição:** `{log.get('punishment', '?')}` | "
                     f"**Sev:** `{log.get('severity', '?')}`")
            embed.add_field(name=f"`{log.get('action_type', '?')}` — {ts_str}",
                             value=value, inline=False)
        await ctx.send(embed=embed)


async def setup(bot):
    if bot.get_cog("AntiNukeCommands") is None:
        await bot.add_cog(AntiNukeCommands(bot))