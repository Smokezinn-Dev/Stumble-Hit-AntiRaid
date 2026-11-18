# ============================================================
# COMMANDS_EMBED_BUILDER.PY - v2.0
# CORE DO SISTEMA DE PAINÉIS INTERATIVOS
# ============================================================
# O QUE ESSE ARQUIVO FAZ:
# - Define e edita painéis (embed + botões + selects)
# - Salva no MongoDB
# - Editor visual com modais
# - Rascunho persistente (auto-save a cada edição)
# - Canal privado (pra editar) + canal público (pra publicar)
#
# A RUNTIME (commands_embed_actions.py) executa as ações.
# ============================================================

import asyncio
import copy
import io
import json
import random
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord.ext import commands
from discord import ui, ButtonStyle, SelectOption

from database import get_connection
from embeds import embed_success, embed_error, embed_warning, embed_info
from utils import TTLCache


def _now() -> datetime:
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# LIMITES DO DISCORD
# ============================================================
LIMITS = {
    "title": 256, "description": 4096, "author_name": 256,
    "footer_text": 2048, "field_name": 256, "field_value": 1024,
    "fields_count": 25, "embed_total": 6000, "url": 2048,
    "button_label": 80, "select_placeholder": 150,
    "select_option_label": 100, "select_option_desc": 100,
    "select_options_max": 25, "max_components": 25,
    "modal_input_value": 4000,  # limite fixo do Discord
    "content": 2000, "modal_title": 45,
}

# Cores por nome
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

# Hosts de imagem conhecidos
_IMAGE_HOSTS = (
    "cdn.discordapp.com", "media.discordapp.net", "i.imgur.com",
    "imgur.com", "i.postimg.cc", "postimg.cc", "cdn.7tv.app",
    "files.catbox.moe", "litter.catbox.moe", "i.ibb.co", "ibb.co",
    "tenor.com", "media.tenor.com", "c.tenor.com", "giphy.com",
    "media.giphy.com", "i.giphy.com",
)
_IMAGE_EXT_RX = re.compile(
    r"\.(png|jpg|jpeg|gif|webp|bmp|tiff|svg|avif)(\?.*)?$",
    re.IGNORECASE,
)


# ============================================================
# TIPOS DE AÇÃO
# ============================================================
ACTION_TYPES = {
    "none":              ("⛔ Nada (só visual)",     []),
    "reply_text":        ("💬 Resposta ephemeral",  ["text"]),
    "reply_embed":       ("📦 Resposta com embed",  ["message_name"]),
    "send_to_channel":   ("📤 Enviar em outro canal", ["channel_id"]),
    "send_message":      ("✉️ Enviar mensagem",     ["message_name"]),
    "send_dm":           ("📨 Enviar DM",            ["message_name"]),
    "redirect_channel":  ("🔗 Redirecionar canal",  ["channel_id", "text"]),
    "give_role":         ("🛡️ Dar cargo",           ["role_id", "text"]),
    "remove_role":       ("🚫 Remover cargo",       ["role_id", "text"]),
    "toggle_role":       ("🔄 Dar/remover cargo",   ["role_id", "text"]),
    "temp_role":         ("⏳ Cargo temporário",    ["role_id", "seconds", "text"]),
    "multi_give_roles":  ("🛡️ Dar vários cargos",   ["role_ids", "text"]),
    "multi_remove_roles":("🚫 Remover vários",      ["role_ids", "text"]),
    "multi_toggle_roles":("🔄 Dar/remover vários",  ["role_ids", "text"]),
    "select_cascade":    ("🔀 Select → abre painel", ["target_panel"]),
    "conditional":       ("❓ Se... então...",       ["condition", "true_action", "false_action"]),
    "open_template":     ("📋 Abrir template",      ["template_name"]),
    "open_modal_custom": ("📝 Abrir modal custom",   ["modal_config"]),
    "reaction_role":     ("😀 Reação → cargo",      ["emoji", "role_id"]),
    "open_panel":        ("🎨 Abrir outro painel",  ["panel_name"]),
    "add_to_list":       ("📋 Adicionar a lista",   ["list_name", "text"]),
    "kick":              ("👢 Kick",                 ["reason"]),
    "ban":               ("🔨 Ban",                  ["reason"]),
    "timeout":           ("⏰ Timeout",              ["seconds", "reason"]),
    "warn":              ("⚠️ Warn",                 ["reason"]),
}

BUTTON_STYLES = {
    "primary":   ButtonStyle.primary,
    "secondary": ButtonStyle.secondary,
    "success":   ButtonStyle.success,
    "danger":    ButtonStyle.danger,
    "link":      ButtonStyle.link,
}


# ============================================================
# SISTEMA DE VARIÁVEIS
# ============================================================
_VAR_RX = re.compile(r"\{([a-zA-Z0-9_\.\-]+)\}")


def _resolve_variables(text: str, *, guild=None, user=None, channel=None,
                        custom_vars: Dict[str, str] = None,
                        extra: Dict[str, str] = None) -> str:
    """Substitui {user.mention}, {guild.name}, etc."""
    if not text:
        return text
    custom_vars = custom_vars or {}
    extra = extra or {}

    def _replace(m):
        key = m.group(1)
        k = key.lower()

        if k in ("user", "user.mention"):
            return user.mention if user else "{user}"
        if k == "user.name":
            return user.display_name if user else "{user.name}"
        if k == "user.id":
            return str(user.id) if user else "{user.id}"
        if k == "user.avatar":
            return (user.display_avatar.url
                    if user and user.display_avatar else "{user.avatar}")
        if k == "user.joined_at" and user and getattr(user, "joined_at", None):
            return f"<t:{int(user.joined_at.timestamp())}:R>"
        if k == "user.created_at" and user and getattr(user, "created_at", None):
            return f"<t:{int(user.created_at.timestamp())}:R>"
        if k == "user.tag":
            return str(user) if user else "{user.tag}"

        if k in ("guild", "guild.name"):
            return guild.name if guild else "{guild}"
        if k == "guild.id":
            return str(guild.id) if guild else "{guild.id}"
        if k == "guild.icon":
            return (guild.icon.url if guild and guild.icon else "{guild.icon}")
        if k == "guild.member_count":
            return str(guild.member_count) if guild else "{guild.member_count}"

        if k in ("channel", "channel.mention"):
            return channel.mention if channel else "{channel}"
        if k == "channel.name":
            return channel.name if channel else "{channel.name}"
        if k == "channel.id":
            return str(channel.id) if channel else "{channel.id}"

        if k == "date":
            return _now().strftime("%d/%m/%Y")
        if k == "time":
            return _now().strftime("%H:%M")
        if k == "timestamp":
            return str(int(_now().timestamp()))
        if k == "datetime":
            return _now().strftime("%d/%m/%Y %H:%M")

        if k == "random":
            return str(random.randint(1, 100))
        if k.startswith("random."):
            nums = re.findall(r"\d+", key)
            if len(nums) >= 2:
                lo, hi = int(nums[0]), int(nums[1])
                if lo > hi:
                    lo, hi = hi, lo
                return str(random.randint(lo, hi))

        if k.startswith("var."):
            return custom_vars.get(key[4:], m.group(0))

        if key in extra:
            return str(extra[key])
        if k in extra:
            return str(extra[k])
        return m.group(0)

    return _VAR_RX.sub(_replace, text)


# ============================================================
# HELPERS DE COR / URL / TEXTO
# ============================================================
def parse_color(raw: str) -> int:
    """Converte qualquer formato de cor em int."""
    if raw is None:
        raise ValueError("Cor vazia.")
    s = str(raw).strip().lower()
    if s in NAMED_COLORS:
        return NAMED_COLORS[s]
    m = re.match(r"rgb\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)", s)
    if m:
        r, g, b = (int(x) for x in m.groups())
        if any(c > 255 for c in (r, g, b)):
            raise ValueError("RGB fora de 0-255.")
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
    raise ValueError(f"Cor inválida: `{raw}`")


def color_to_hex(color: int) -> str:
    return f"#{color & 0xFFFFFF:06x}"


def is_valid_url(url: str) -> bool:
    if not url:
        return False
    return bool(re.match(r"^https?://\S+$", url.strip(), re.IGNORECASE))


def is_image_url(url: str) -> bool:
    if not is_valid_url(url):
        return False
    low = url.lower()
    if _IMAGE_EXT_RX.search(low):
        return True
    return any(h in low for h in _IMAGE_HOSTS)


def truncate(text: str, max_len: int) -> str:
    if not text or len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def validate_embed_limits(embed_data: dict) -> Optional[str]:
    """Valida todos os limites do Discord. None se OK."""
    t = embed_data.get("title") or ""
    d = embed_data.get("description") or ""
    a = (embed_data.get("author") or {}).get("name") or ""
    f = (embed_data.get("footer") or {}).get("text") or ""
    fields = embed_data.get("fields") or []
    if len(t) > LIMITS["title"]:
        return f"Título muito longo."
    if len(d) > LIMITS["description"]:
        return f"Descrição muito longa."
    if len(a) > LIMITS["author_name"]:
        return "Autor muito longo."
    if len(f) > LIMITS["footer_text"]:
        return "Footer muito longo."
    if len(fields) > LIMITS["fields_count"]:
        return f"Máx {LIMITS['fields_count']} campos."
    for i, fl in enumerate(fields, 1):
        n = fl.get("name") or ""
        v = fl.get("value") or ""
        if len(n) > LIMITS["field_name"]:
            return f"Campo #{i} — nome longo."
        if len(v) > LIMITS["field_value"]:
            return f"Campo #{i} — valor longo."
        if not n or not v:
            return f"Campo #{i} vazio."
    total = len(t) + len(d) + len(a) + len(f)
    for fl in fields:
        total += len(fl.get("name") or "") + len(fl.get("value") or "")
    if total > LIMITS["embed_total"]:
        return f"Total do embed muito grande."
    return None


def dict_to_embed(data: dict, *, guild=None, user=None, channel=None,
                    custom_vars: Dict[str, str] = None) -> discord.Embed:
    """Converte dict em discord.Embed."""
    embed = discord.Embed()
    if data.get("title"):
        embed.title = truncate(
            _resolve_variables(data["title"], guild=guild, user=user,
                                channel=channel, custom_vars=custom_vars),
            LIMITS["title"])
    if data.get("description"):
        embed.description = truncate(
            _resolve_variables(data["description"], guild=guild, user=user,
                                channel=channel, custom_vars=custom_vars),
            LIMITS["description"])
    embed.color = discord.Color(int(data.get("color", 0x5865F2)))
    if data.get("url") and is_valid_url(data["url"]):
        embed.url = data["url"]
    author = data.get("author") or {}
    if author.get("name"):
        try:
            embed.set_author(
                name=truncate(_resolve_variables(author["name"], guild=guild,
                                                  user=user, channel=channel,
                                                  custom_vars=custom_vars),
                              LIMITS["author_name"]),
                icon_url=author.get("icon_url") or None,
                url=author.get("url") or None)
        except Exception:
            pass
    if data.get("thumbnail"):
        try:
            embed.set_thumbnail(url=data["thumbnail"])
        except Exception:
            pass
    if data.get("image"):
        try:
            embed.set_image(url=data["image"])
        except Exception:
            pass
    footer = data.get("footer") or {}
    if footer.get("text"):
        try:
            embed.set_footer(
                text=truncate(_resolve_variables(footer["text"], guild=guild,
                                                  user=user, channel=channel,
                                                  custom_vars=custom_vars),
                              LIMITS["footer_text"]),
                icon_url=footer.get("icon_url") or None)
        except Exception:
            pass
    if data.get("timestamp"):
        try:
            embed.timestamp = datetime.fromisoformat(data["timestamp"])
        except Exception:
            pass
    for f in (data.get("fields") or [])[: LIMITS["fields_count"]]:
        n = f.get("name") or ""
        v = f.get("value") or ""
        if not n or not v:
            continue
        try:
            embed.add_field(
                name=truncate(_resolve_variables(n, guild=guild, user=user,
                                                  channel=channel,
                                                  custom_vars=custom_vars),
                              LIMITS["field_name"]),
                value=truncate(_resolve_variables(v, guild=guild, user=user,
                                                    channel=channel,
                                                    custom_vars=custom_vars),
                                LIMITS["field_value"]),
                inline=bool(f.get("inline", True)))
        except Exception:
            continue
    return embed


def embed_has_content(embed: discord.Embed) -> bool:
    """Diz se o embed tem algo pra mostrar."""
    if not embed:
        return False
    if (embed.title or "").strip(): return True
    if (embed.description or "").strip(): return True
    if embed.fields: return True
    if embed.image and embed.image.url: return True
    if embed.thumbnail and embed.thumbnail.url: return True
    if embed.footer and (embed.footer.text or "").strip(): return True
    if embed.author and (embed.author.name or "").strip(): return True
    return False


# ============================================================
# BANCO DE DADOS (CRUD)
# ============================================================
def _load_panel(guild_id: int, name: str) -> Optional[dict]:
    db = get_connection()
    return db["embed_panels"].find_one(
        {"guild_id": guild_id, "name": name.lower()})


def _save_panel(guild_id: int, name: str, data: dict) -> bool:
    try:
        db = get_connection()
        data["updated_at"] = _now()
        db["embed_panels"].update_one(
            {"guild_id": guild_id, "name": name.lower()},
            {"$set": data, "$setOnInsert": {"created_at": _now()}},
            upsert=True)
        return True
    except Exception:
        return False


def _delete_panel(guild_id: int, name: str) -> bool:
    try:
        db = get_connection()
        r = db["embed_panels"].delete_one(
            {"guild_id": guild_id, "name": name.lower()})
        return r.deleted_count > 0
    except Exception:
        return False


def _list_panels(guild_id: int, limit: int = 30) -> List[dict]:
    db = get_connection()
    return list(db["embed_panels"].find({"guild_id": guild_id})
                .sort("updated_at", -1).limit(limit))


def _load_message(guild_id: int, name: str) -> Optional[dict]:
    db = get_connection()
    return db["embed_messages"].find_one(
        {"guild_id": guild_id, "name": name.lower()})


def _save_message(guild_id: int, name: str, data: dict) -> bool:
    try:
        db = get_connection()
        data["updated_at"] = _now()
        db["embed_messages"].update_one(
            {"guild_id": guild_id, "name": name.lower()},
            {"$set": data, "$setOnInsert": {"created_at": _now()}},
            upsert=True)
        return True
    except Exception:
        return False


def _delete_message(guild_id: int, name: str) -> bool:
    try:
        db = get_connection()
        r = db["embed_messages"].delete_one(
            {"guild_id": guild_id, "name": name.lower()})
        return r.deleted_count > 0
    except Exception:
        return False


def _list_messages(guild_id: int, limit: int = 30) -> List[dict]:
    db = get_connection()
    return list(db["embed_messages"].find({"guild_id": guild_id})
                .sort("updated_at", -1).limit(limit))


def _get_custom_vars(guild_id: int) -> Dict[str, str]:
    db = get_connection()
    docs = db["embed_variables"].find({"guild_id": guild_id})
    return {d["key"]: d["value"] for d in docs}


def _log_panel_action(guild_id, panel_name, component_id, user_id,
                       action_type, detail=""):
    try:
        db = get_connection()
        db["embed_panel_logs"].insert_one({
            "guild_id": guild_id, "panel_name": panel_name,
            "component_id": component_id, "user_id": user_id,
            "action_type": action_type, "detail": detail[:300],
            "timestamp": _now()})
    except Exception:
        pass


# ============================================================
# CONFIG DO EMBED BUILDER (canal privado + público)
# ============================================================
def _get_eb_config(guild_id: int) -> dict:
    """Pega config do embed builder por guild."""
    db = get_connection()
    doc = db["embed_builder_config"].find_one({"guild_id": guild_id}) or {}
    return {
        "private_channel": doc.get("private_channel", 0),
        "public_channel": doc.get("public_channel", 0),
    }


def _set_eb_config(guild_id: int, key: str, value):
    """Salva uma config."""
    db = get_connection()
    db["embed_builder_config"].update_one(
        {"guild_id": guild_id}, {"$set": {key: value}}, upsert=True)


# ============================================================
# SESSÃO DO EDITOR (RASCUNHO)
# ============================================================
@dataclass
class BuilderSession:
    """Rascunho do painel em edição."""
    user_id: int
    guild_id: int
    channel_id: int
    panel_name: str
    title: str = ""
    description: str = ""
    color: int = 0x5865F2
    url: str = ""
    author_name: str = ""
    author_icon: str = ""
    author_url: str = ""
    thumbnail: str = ""
    image: str = ""
    footer_text: str = ""
    footer_icon: str = ""
    fields: List[Dict[str, Any]] = field(default_factory=list)
    components: List[Dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    editing_component_id: Optional[str] = None
    preview_message_id: int = 0
    panel_message_id: int = 0
    saved: bool = False

    def touch(self):
        self.updated_at = time.time()

    def is_expired(self, minutes: int = 120) -> bool:
        return (time.time() - self.updated_at) > (minutes * 60)

    def to_embed_dict(self) -> dict:
        return {
            "title": self.title, "description": self.description,
            "color": self.color, "url": self.url,
            "author": {"name": self.author_name,
                       "icon_url": self.author_icon,
                       "url": self.author_url},
            "thumbnail": self.thumbnail, "image": self.image,
            "footer": {"text": self.footer_text, "icon_url": self.footer_icon},
            "fields": [dict(f) for f in self.fields]}

    def to_panel_dict(self) -> dict:
        return {
            "name": self.panel_name, "embed": self.to_embed_dict(),
            "components": [copy.deepcopy(c) for c in self.components],
            "persistent": True}

    def build_preview_embed(self) -> discord.Embed:
        embed = dict_to_embed(self.to_embed_dict())
        if not embed_has_content(embed):
            embed.description = "*O embed está vazio — use os botões abaixo.*"
            embed.color = discord.Color(0x5865F2)
        return embed

    @classmethod
    def from_panel(cls, user_id, guild_id, channel_id, panel):
        e = panel.get("embed") or {}
        a = e.get("author") or {}
        f = e.get("footer") or {}
        return cls(
            user_id=user_id, guild_id=guild_id, channel_id=channel_id,
            panel_name=panel.get("name", "novo"),
            title=e.get("title", ""), description=e.get("description", ""),
            color=int(e.get("color", 0x5865F2)), url=e.get("url", ""),
            author_name=a.get("name", ""), author_icon=a.get("icon_url", ""),
            author_url=a.get("url", ""), thumbnail=e.get("thumbnail", ""),
            image=e.get("image", ""), footer_text=f.get("text", ""),
            footer_icon=f.get("icon_url", ""),
            fields=[dict(x) for x in (e.get("fields") or [])],
            components=[copy.deepcopy(c) for c in (panel.get("components") or [])],
            saved=True)


class SessionManager:
    """Gerencia sessões do editor."""
    _cache: Dict[int, BuilderSession] = {}
    _TIMEOUT_MIN = 120

    @classmethod
    def get(cls, user_id: int) -> Optional[BuilderSession]:
        s = cls._cache.get(user_id)
        if s and s.is_expired(cls._TIMEOUT_MIN):
            cls._cache.pop(user_id, None)
            cls._delete_draft(user_id)
            return None
        return s

    @classmethod
    def create(cls, user_id, guild_id, channel_id, panel_name):
        s = BuilderSession(user_id=user_id, guild_id=guild_id,
                            channel_id=channel_id, panel_name=panel_name)
        cls._cache[user_id] = s
        cls._save_draft(s)
        return s

    @classmethod
    def remove(cls, user_id):
        cls._cache.pop(user_id, None)
        cls._delete_draft(user_id)

    @classmethod
    def _save_draft(cls, session):
        try:
            db = get_connection()
            db["embed_panel_drafts"].update_one(
                {"user_id": session.user_id, "guild_id": session.guild_id},
                {"$set": {
                    "user_id": session.user_id,
                    "guild_id": session.guild_id,
                    "channel_id": session.channel_id,
                    "panel_name": session.panel_name,
                    "data": asdict(session),
                    "updated_at": _now()}},
                upsert=True)
        except Exception:
            pass

    @classmethod
    def _load_draft(cls, user_id, guild_id):
        try:
            db = get_connection()
            doc = db["embed_panel_drafts"].find_one(
                {"user_id": user_id, "guild_id": guild_id})
            if not doc:
                return None
            data = doc.get("data") or {}
            s = BuilderSession(**{k: v for k, v in data.items()
                                   if k in BuilderSession.__dataclass_fields__})
            if s.is_expired(cls._TIMEOUT_MIN):
                cls._delete_draft(user_id)
                return None
            return s
        except Exception:
            return None

    @classmethod
    def _delete_draft(cls, user_id):
        try:
            db = get_connection()
            db["embed_panel_drafts"].delete_many({"user_id": user_id})
        except Exception:
            pass


# ============================================================
# MODAIS DO EDITOR
# ============================================================
class EBTitleModal(ui.Modal, title="📝 Título"):
    value = ui.TextInput(label="Título", max_length=LIMITS["title"],
                          required=False, style=discord.TextStyle.short)
    def __init__(self, session, view_ref):
        super().__init__()
        self.session, self.view_ref = session, view_ref
        self.value.default = session.title or None
    async def on_submit(self, interaction):
        self.session.title = self.value.value or ""
        self.session.touch()
        await self.view_ref.refresh_preview(interaction)


class EBDescriptionModal(ui.Modal, title="📄 Descrição"):
    value = ui.TextInput(label="Descrição",
                          max_length=4000,  # LIMITE FIXO do Discord
                          required=False,
                          style=discord.TextStyle.paragraph)
    def __init__(self, session, view_ref):
        super().__init__()
        self.session, self.view_ref = session, view_ref
        self.value.default = session.description or None
    async def on_submit(self, interaction):
        self.session.description = self.value.value or ""
        self.session.touch()
        await self.view_ref.refresh_preview(interaction)


class EBColorModal(ui.Modal, title="🎨 Cor"):
    value = ui.TextInput(label="Cor (hex, nome ou rgb)",
                          placeholder="#ff0000 | red | rgb(255,0,0)",
                          max_length=50, required=True,
                          style=discord.TextStyle.short)
    def __init__(self, session, view_ref):
        super().__init__()
        self.session, self.view_ref = session, view_ref
        self.value.default = color_to_hex(session.color)
    async def on_submit(self, interaction):
        try:
            self.session.color = parse_color(self.value.value)
            self.session.touch()
            await self.view_ref.refresh_preview(interaction)
        except ValueError as e:
            await interaction.response.send_message(
                embed=embed_error(str(e)), ephemeral=True)


class EBAuthorModal(ui.Modal, title="👤 Autor"):
    name = ui.TextInput(label="Nome", max_length=LIMITS["author_name"],
                         required=False, style=discord.TextStyle.short)
    icon = ui.TextInput(label="URL do ícone", max_length=500,
                         required=False, style=discord.TextStyle.short)
    url = ui.TextInput(label="URL do autor", max_length=500,
                        required=False, style=discord.TextStyle.short)
    def __init__(self, session, view_ref):
        super().__init__()
        self.session, self.view_ref = session, view_ref
        self.name.default = session.author_name or None
        self.icon.default = session.author_icon or None
        self.url.default = session.author_url or None
    async def on_submit(self, interaction):
        self.session.author_name = (self.name.value or "").strip()
        icon = (self.icon.value or "").strip()
        url = (self.url.value or "").strip()
        if icon and not is_valid_url(icon):
            return await interaction.response.send_message(
                embed=embed_error("URL do ícone inválida."), ephemeral=True)
        if url and not is_valid_url(url):
            return await interaction.response.send_message(
                embed=embed_error("URL do autor inválida."), ephemeral=True)
        self.session.author_icon = icon
        self.session.author_url = url
        self.session.touch()
        await self.view_ref.refresh_preview(interaction)


class EBImageModal(ui.Modal):
    value = ui.TextInput(label="URL", max_length=500, required=False,
                          style=discord.TextStyle.short)
    def __init__(self, session, view_ref, field_key, label):
        super().__init__(title=f"🖼️ {label}")
        self.session, self.view_ref = session, view_ref
        self.field_key = field_key
        self.value.label = label
        self.value.default = getattr(session, field_key, "") or None
    async def on_submit(self, interaction):
        url = (self.value.value or "").strip()
        if url and not is_image_url(url):
            return await interaction.response.send_message(
                embed=embed_error("URL de imagem inválida."), ephemeral=True)
        setattr(self.session, self.field_key, url)
        self.session.touch()
        await self.view_ref.refresh_preview(interaction)


class EBFooterModal(ui.Modal, title="📎 Footer"):
    text = ui.TextInput(label="Texto", max_length=LIMITS["footer_text"],
                         required=False, style=discord.TextStyle.short)
    icon = ui.TextInput(label="URL do ícone", max_length=500,
                         required=False, style=discord.TextStyle.short)
    def __init__(self, session, view_ref):
        super().__init__()
        self.session, self.view_ref = session, view_ref
        self.text.default = session.footer_text or None
        self.icon.default = session.footer_icon or None
    async def on_submit(self, interaction):
        self.session.footer_text = (self.text.value or "").strip()
        icon = (self.icon.value or "").strip()
        if icon and not is_valid_url(icon):
            return await interaction.response.send_message(
                embed=embed_error("URL inválida."), ephemeral=True)
        self.session.footer_icon = icon
        self.session.touch()
        await self.view_ref.refresh_preview(interaction)


class EBURLModal(ui.Modal, title="🔗 URL do título"):
    value = ui.TextInput(label="URL", max_length=LIMITS["url"],
                          required=False, style=discord.TextStyle.short)
    def __init__(self, session, view_ref):
        super().__init__()
        self.session, self.view_ref = session, view_ref
        self.value.default = session.url or None
    async def on_submit(self, interaction):
        url = (self.value.value or "").strip()
        if url and not is_valid_url(url):
            return await interaction.response.send_message(
                embed=embed_error("URL inválida."), ephemeral=True)
        self.session.url = url
        self.session.touch()
        await self.view_ref.refresh_preview(interaction)


class EBAddFieldModal(ui.Modal, title="➕ Adicionar campo"):
    name = ui.TextInput(label="Nome", max_length=LIMITS["field_name"],
                         required=True, style=discord.TextStyle.short)
    value = ui.TextInput(label="Valor", max_length=LIMITS["field_value"],
                          required=True, style=discord.TextStyle.paragraph)
    inline = ui.TextInput(label="Inline? (sim/não)", default="sim",
                           max_length=3, required=False,
                           style=discord.TextStyle.short)
    def __init__(self, session, view_ref):
        super().__init__()
        self.session, self.view_ref = session, view_ref
    async def on_submit(self, interaction):
        if len(self.session.fields) >= LIMITS["fields_count"]:
            return await interaction.response.send_message(
                embed=embed_error("Muitos campos."), ephemeral=True)
        inline = (self.inline.value or "sim").strip().lower() in (
            "sim", "s", "yes", "y", "true", "1")
        self.session.fields.append({
            "name": self.name.value, "value": self.value.value,
            "inline": inline})
        self.session.touch()
        await self.view_ref.refresh_preview(interaction)


class EBEditFieldModal(ui.Modal):
    name = ui.TextInput(label="Nome", max_length=LIMITS["field_name"],
                         required=True, style=discord.TextStyle.short)
    value = ui.TextInput(label="Valor", max_length=LIMITS["field_value"],
                          required=True, style=discord.TextStyle.paragraph)
    inline = ui.TextInput(label="Inline?", max_length=3, required=False,
                           style=discord.TextStyle.short)
    def __init__(self, session, view_ref, index):
        super().__init__(title=f"✏️ Editar campo #{index + 1}")
        self.session, self.view_ref, self.index = session, view_ref, index
        f = session.fields[index]
        self.name.default = f["name"]
        self.value.default = f["value"]
        self.inline.default = "sim" if f.get("inline") else "não"
    async def on_submit(self, interaction):
        if self.index >= len(self.session.fields):
            return await interaction.response.send_message(
                embed=embed_error("Campo não existe."), ephemeral=True)
        inline = (self.inline.value or "sim").strip().lower() in (
            "sim", "s", "yes", "y", "true", "1")
        self.session.fields[self.index] = {
            "name": self.name.value, "value": self.value.value,
            "inline": inline}
        self.session.touch()
        await self.view_ref.refresh_preview(interaction)


class EBAddButtonModal(ui.Modal, title="➕ Adicionar botão"):
    label = ui.TextInput(label="Texto do botão",
                          max_length=LIMITS["button_label"], required=True,
                          style=discord.TextStyle.short)
    emoji = ui.TextInput(label="Emoji (opcional)", max_length=50,
                          required=False, style=discord.TextStyle.short)
    style = ui.TextInput(
        label="Estilo (primary/secondary/success/danger/link)",
        default="primary", max_length=20, required=True,
        style=discord.TextStyle.short)
    row = ui.TextInput(label="Row (0-4)", default="0", max_length=1,
                        required=True, style=discord.TextStyle.short)
    def __init__(self, session, view_ref):
        super().__init__()
        self.session, self.view_ref = session, view_ref
    async def on_submit(self, interaction):
        cid = f"btn_{int(time.time() * 1000)}"
        style_key = (self.style.value or "primary").strip().lower()
        if style_key not in BUTTON_STYLES:
            return await interaction.response.send_message(
                embed=embed_error("Estilo inválido."), ephemeral=True)
        row_val = max(0, min(int((self.row.value or "0").strip() or "0"), 4))
        if len(self.session.components) >= LIMITS["max_components"]:
            return await interaction.response.send_message(
                embed=embed_error("Muitos componentes."), ephemeral=True)
        self.session.components.append({
            "id": cid, "type": "button", "row": row_val,
            "label": self.label.value, "emoji": self.emoji.value or None,
            "style": style_key, "disabled": False, "url": "",
            "action": {"type": "none"}})
        self.session.touch()
        await self.view_ref.refresh_preview(interaction)


class EBAddSelectModal(ui.Modal, title="➕ Adicionar select"):
    placeholder = ui.TextInput(
        label="Placeholder", max_length=LIMITS["select_placeholder"],
        required=True, style=discord.TextStyle.short)
    row = ui.TextInput(label="Row (0-4)", default="0", max_length=1,
                        required=True, style=discord.TextStyle.short)
    min_max = ui.TextInput(label="Min/Max (ex: 1,1)", default="1,1",
                            max_length=10, required=True,
                            style=discord.TextStyle.short)
    def __init__(self, session, view_ref):
        super().__init__()
        self.session, self.view_ref = session, view_ref
    async def on_submit(self, interaction):
        cid = f"sel_{int(time.time() * 1000)}"
        row_val = max(0, min(int((self.row.value or "0").strip() or "0"), 4))
        if len(self.session.components) >= LIMITS["max_components"]:
            return await interaction.response.send_message(
                embed=embed_error("Muitos componentes."), ephemeral=True)
        try:
            parts = [int(x) for x in self.min_max.value.split(",")]
            mn = max(0, min(parts[0], 25))
            mx = max(mn, min(parts[1] if len(parts) > 1 else mn, 25))
        except Exception:
            mn, mx = 1, 1
        self.session.components.append({
            "id": cid, "type": "select", "row": row_val,
            "placeholder": self.placeholder.value,
            "min_values": mn, "max_values": mx,
            "options": [], "action": {"type": "none"}})
        self.session.touch()
        await self.view_ref.refresh_preview(interaction)


class EBAddOptionModal(ui.Modal, title="➕ Adicionar opção"):
    label = ui.TextInput(label="Label", max_length=LIMITS["select_option_label"],
                          required=True, style=discord.TextStyle.short)
    value = ui.TextInput(label="Valor (opcional)", max_length=100,
                          required=False, style=discord.TextStyle.short)
    desc = ui.TextInput(label="Descrição (opcional)",
                         max_length=LIMITS["select_option_desc"],
                         required=False, style=discord.TextStyle.short)
    emoji = ui.TextInput(label="Emoji (opcional)", max_length=50,
                          required=False, style=discord.TextStyle.short)
    def __init__(self, session, comp, view_ref):
        super().__init__()
        self.session, self.comp, self.view_ref = session, comp, view_ref
    async def on_submit(self, interaction):
        options = self.comp.get("options") or []
        if len(options) >= LIMITS["select_options_max"]:
            return await interaction.response.send_message(
                embed=embed_error("Muitas opções."), ephemeral=True)
        options.append({
            "label": self.label.value,
            "value": (self.value.value or self.label.value)[:100],
            "description": self.desc.value or "",
            "emoji": self.emoji.value or None,
            "action": {"type": "none"}})
        self.comp["options"] = options
        self.session.touch()
        await interaction.response.send_message(
            embed=embed_success(f"✅ Opção `{self.label.value}` adicionada."),
            ephemeral=True)


class EBSendToChannelModal(ui.Modal, title="📤 Enviar painel"):
    channel_id = ui.TextInput(label="ID do canal", max_length=25,
                                required=True, style=discord.TextStyle.short)
    def __init__(self, session, view_ref, bot):
        super().__init__()
        self.session, self.view_ref, self.bot = session, view_ref, bot
    async def on_submit(self, interaction):
        try:
            ch_id = int(self.channel_id.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("ID inválido."), ephemeral=True)
        channel = interaction.guild.get_channel(ch_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                embed=embed_error("Canal inválido."), ephemeral=True)
        err = validate_embed_limits(self.session.to_embed_dict())
        if err:
            return await interaction.response.send_message(
                embed=embed_error(err), ephemeral=True)
        _save_panel(self.session.guild_id, self.session.panel_name,
                     self.session.to_panel_dict())
        self.session.saved = True
        panel = _load_panel(self.session.guild_id, self.session.panel_name)
        embed = dict_to_embed(panel.get("embed") or {})
        view = ui.View(timeout=None)
        for c in _build_components_from_panel(panel, self.bot):
            view.add_item(c)
        try:
            msg = await channel.send(
                embed=embed if embed_has_content(embed) else None,
                view=view if len(view.children) else None)
        except Exception as e:
            return await interaction.response.send_message(
                embed=embed_error(f"Erro: {e}"), ephemeral=True)
        try:
            db = get_connection()
            db["embed_panels"].update_one(
                {"_id": panel["_id"]},
                {"$set": {"message_id": msg.id, "channel_id": ch_id}})
        except Exception:
            pass
        await interaction.response.send_message(
            embed=embed_success(f"📤 Painel enviado em {channel.mention}!"),
            ephemeral=True)


# ============================================================
# BUILD DE COMPONENTES
# ============================================================
def _build_components_from_panel(panel, cog_ref) -> List[ui.Item]:
    """Monta os componentes do painel."""
    components = panel.get("components") or []
    items: List[ui.Item] = []
    sorted_comps = sorted(components,
                           key=lambda c: (c.get("row", 0), c.get("id", "")))
    for comp in sorted_comps:
        cid = comp.get("id") or "c"
        ctype = comp.get("type") or "button"
        row = max(0, min(int(comp.get("row", 0)), 4))
        if ctype == "button":
            style = BUTTON_STYLES.get(comp.get("style", "primary"),
                                        ButtonStyle.primary)
            try:
                btn = ui.Button(
                    label=(comp.get("label") or "Botão")[: LIMITS["button_label"]],
                    emoji=comp.get("emoji") or None, style=style,
                    disabled=bool(comp.get("disabled", False)),
                    url=comp.get("url") if style == ButtonStyle.link else None,
                    custom_id=f"eb:{panel['_id']}:{cid}", row=row)
                items.append(btn)
            except Exception:
                continue
        elif ctype == "select":
            options_raw = comp.get("options") or []
            options: List[SelectOption] = []
            for opt in options_raw[: LIMITS["select_options_max"]]:
                options.append(SelectOption(
                    label=(opt.get("label") or "Opção")[: LIMITS["select_option_label"]],
                    value=(opt.get("value") or opt.get("label") or "opt")[:100],
                    description=(opt.get("description") or "")[: LIMITS["select_option_desc"]] or None,
                    emoji=opt.get("emoji") or None, default=False))
            if not options:
                options.append(SelectOption(label="(vazio)", value="_empty"))
            try:
                sel = ui.Select(
                    placeholder=(comp.get("placeholder") or "Selecione...")[: LIMITS["select_placeholder"]],
                    options=options,
                    min_values=int(comp.get("min_values", 1)),
                    max_values=int(comp.get("max_values", 1)),
                    custom_id=f"eb:{panel['_id']}:{cid}", row=row)
                items.append(sel)
            except Exception:
                continue
    return items


# ============================================================
# VIEWS DO EDITOR
# ============================================================
class EBActionButton(ui.Button):
    def __init__(self, label, style, custom_id, callback, row=0, emoji=None):
        super().__init__(label=label, style=style, custom_id=custom_id,
                          row=row, emoji=emoji)
        self._cb = callback
    async def callback(self, interaction):
        await self._cb(interaction)


class EBMainSelect(ui.Select):
    def __init__(self, session, view_ref):
        self.session, self.view_ref = session, view_ref
        options = [
            SelectOption(label="Título", value="title", emoji="📝"),
            SelectOption(label="Descrição", value="desc", emoji="📄"),
            SelectOption(label="Cor", value="color", emoji="🎨"),
            SelectOption(label="Autor", value="author", emoji="👤"),
            SelectOption(label="Thumbnail", value="thumb", emoji="🖼️"),
            SelectOption(label="Imagem", value="image", emoji="🏞️"),
            SelectOption(label="Footer", value="footer", emoji="📎"),
            SelectOption(label="URL do título", value="url", emoji="🔗"),
            SelectOption(label="Adicionar campo", value="add_field", emoji="➕"),
            SelectOption(label="Editar campo", value="edit_field", emoji="✏️"),
            SelectOption(label="Remover campo", value="rm_field", emoji="❌"),
            SelectOption(label="Reordenar campos", value="reorder", emoji="🔀"),
        ]
        super().__init__(placeholder="🎨 Escolha o que editar...",
                          options=options, min_values=1, max_values=1)
    async def callback(self, interaction):
        if interaction.user.id != self.session.user_id:
            return await interaction.response.send_message(
                embed=embed_error("Essa sessão é de outro user."),
                ephemeral=True)
        choice = self.values[0]
        self.session.touch()
        if choice == "title":
            await interaction.response.send_modal(EBTitleModal(self.session, self.view_ref))
        elif choice == "desc":
            await interaction.response.send_modal(EBDescriptionModal(self.session, self.view_ref))
        elif choice == "color":
            await interaction.response.send_modal(EBColorModal(self.session, self.view_ref))
        elif choice == "author":
            await interaction.response.send_modal(EBAuthorModal(self.session, self.view_ref))
        elif choice == "thumb":
            await interaction.response.send_modal(EBImageModal(self.session, self.view_ref, "thumbnail", "Thumbnail"))
        elif choice == "image":
            await interaction.response.send_modal(EBImageModal(self.session, self.view_ref, "image", "Imagem"))
        elif choice == "footer":
            await interaction.response.send_modal(EBFooterModal(self.session, self.view_ref))
        elif choice == "url":
            await interaction.response.send_modal(EBURLModal(self.session, self.view_ref))
        elif choice == "add_field":
            await interaction.response.send_modal(EBAddFieldModal(self.session, self.view_ref))
        elif choice in ("edit_field", "rm_field"):
            if not self.session.fields:
                return await interaction.response.send_message(
                    embed=embed_info("Nenhum campo."), ephemeral=True)
            action = "edit" if choice == "edit_field" else "rm"
            view = _FieldSelectView(self.session, self.view_ref, action)
            await interaction.response.edit_message(
                embed=self.session.build_preview_embed(), view=view)
        elif choice == "reorder":
            if len(self.session.fields) < 2:
                return await interaction.response.send_message(
                    embed=embed_info("Precisa de 2+ campos."), ephemeral=True)
            view = _FieldReorderView(self.session, self.view_ref)
            await interaction.response.edit_message(
                embed=self.session.build_preview_embed(), view=view)


class EBBuilderView(ui.View):
    def __init__(self, session, bot):
        super().__init__(timeout=7200)  # 2 horas
        self.session, self.bot = session, bot
        self._build()
    def _build(self):
        self.clear_items()
        self.add_item(EBMainSelect(self.session, self))
        self.add_item(EBActionButton("➕ Botão", ButtonStyle.success,
                                       "eb_add_btn", self._add_button, row=1))
        self.add_item(EBActionButton("➕ Select", ButtonStyle.success,
                                       "eb_add_select", self._add_select, row=1))
        self.add_item(EBActionButton("🧩 Componentes", ButtonStyle.primary,
                                       "eb_view_comps", self._view_comps, row=1))
        self.add_item(EBActionButton("🎯 Ações", ButtonStyle.primary,
                                       "eb_actions", self._view_actions, row=1))
        self.add_item(EBActionButton("🎨 Variáveis", ButtonStyle.secondary,
                                       "eb_vars", self._show_vars, row=1))
        self.add_item(EBActionButton("💾 Salvar", ButtonStyle.success,
                                       "eb_save", self._save, row=2))
        self.add_item(EBActionButton("📤 Enviar", ButtonStyle.primary,
                                       "eb_send", self._send, row=2))
        self.add_item(EBActionButton("📋 Templates", ButtonStyle.secondary,
                                       "eb_templates", self._templates, row=2))
        self.add_item(EBActionButton("🗑️ Fechar", ButtonStyle.danger,
                                       "eb_close", self._close, row=2))

    async def refresh_preview(self, interaction):
        self.session.touch()
        SessionManager._save_draft(self.session)
        try:
            embed = self.session.build_preview_embed()
            if interaction.response.is_done():
                await interaction.message.edit(embed=embed, view=self)
            else:
                await interaction.response.edit_message(embed=embed, view=self)
        except Exception:
            pass

    async def _add_button(self, i):
        await i.response.send_modal(EBAddButtonModal(self.session, self))
    async def _add_select(self, i):
        await i.response.send_modal(EBAddSelectModal(self.session, self))

    async def _view_comps(self, i):
        view = EBComponentsView(self.session, self)
        await i.response.edit_message(embed=view.build_embed(), view=view)

    async def _view_actions(self, i):
        if not self.session.components:
            return await i.response.send_message(
                embed=embed_info("Adicione componentes primeiro."),
                ephemeral=True)
        view = EBActionsView(self.session, self)
        await i.response.edit_message(embed=view.build_embed(), view=view)

    async def _show_vars(self, i):
        text = (
            "**Variáveis:**\n\n"
            "**User:** `{user}`, `{user.mention}`, `{user.name}`, "
            "`{user.id}`, `{user.avatar}`, `{user.joined_at}`, `{user.tag}`\n"
            "**Guild:** `{guild}`, `{guild.name}`, `{guild.id}`, "
            "`{guild.icon}`, `{guild.member_count}`\n"
            "**Canal:** `{channel}`, `{channel.mention}`, "
            "`{channel.name}`, `{channel.id}`\n"
            "**Data:** `{date}`, `{time}`, `{datetime}`, `{timestamp}`\n"
            "**Aleatório:** `{random}`, `{random.1-100}`\n"
            "**Custom:** `{var.NOME}`")
        await i.response.send_message(embed=embed_info(text), ephemeral=True)

    async def _save(self, i):
        err = validate_embed_limits(self.session.to_embed_dict())
        if err:
            return await i.response.send_message(
                embed=embed_error(err), ephemeral=True)
        ok = _save_panel(self.session.guild_id, self.session.panel_name,
                          self.session.to_panel_dict())
        if not ok:
            return await i.response.send_message(
                embed=embed_error("Erro ao salvar."), ephemeral=True)
        self.session.saved = True
        SessionManager._save_draft(self.session)
        await i.response.send_message(embed=embed_success(
            f"💾 Painel **{self.session.panel_name}** salvo!"),
            ephemeral=True)

    async def _send(self, i):
        # Se tiver canal público configurado, envia direto.
        cfg = _get_eb_config(self.session.guild_id)
        public_ch = cfg.get("public_channel")
        if public_ch:
            await i.response.defer(ephemeral=True)
            return await self._send_to(i, int(public_ch))
        await i.response.send_modal(EBSendToChannelModal(self.session, self, self.bot))

    async def _send_to(self, i, ch_id):
        channel = i.guild.get_channel(ch_id)
        if not channel:
            return await i.followup.send(embed=embed_error(
                f"Canal {ch_id} não encontrado."), ephemeral=True)
        err = validate_embed_limits(self.session.to_embed_dict())
        if err:
            return await i.followup.send(embed=embed_error(err), ephemeral=True)
        _save_panel(self.session.guild_id, self.session.panel_name,
                     self.session.to_panel_dict())
        self.session.saved = True
        panel = _load_panel(self.session.guild_id, self.session.panel_name)
        embed = dict_to_embed(panel.get("embed") or {})
        view = ui.View(timeout=None)
        for c in _build_components_from_panel(panel, self.bot):
            view.add_item(c)
        try:
            msg = await channel.send(
                embed=embed if embed_has_content(embed) else None,
                view=view if len(view.children) else None)
        except Exception as e:
            return await i.followup.send(
                embed=embed_error(f"Erro: {e}"), ephemeral=True)
        try:
            db = get_connection()
            db["embed_panels"].update_one({"_id": panel["_id"]},
                {"$set": {"message_id": msg.id, "channel_id": ch_id}})
        except Exception:
            pass
        await i.followup.send(embed=embed_success(
            f"📤 Painel publicado em {channel.mention}!"), ephemeral=True)

    async def _templates(self, i):
        view = EBMessagesView(self.session, self, self.bot)
        await i.response.edit_message(embed=view.build_embed(), view=view)

    async def _close(self, i):
        SessionManager.remove(self.session.user_id)
        try:
            await i.message.edit(embed=embed_info("✅ Editor fechado."),
                                   view=None)
        except Exception:
            pass


class _FieldSelectView(ui.View):
    def __init__(self, session, parent, action):
        super().__init__(timeout=300)
        self.session, self.parent, self.action = session, parent, action
        opts = []
        for i, f in enumerate(session.fields[:25]):
            opts.append(SelectOption(label=f"#{i + 1}: {f['name'][:60]}",
                                       value=str(i),
                                       description=(f["value"] or "")[:80]))
        sel = ui.Select(
            placeholder="✏️ Escolha o campo..." if action == "edit"
                        else "❌ Escolha o campo pra remover...",
            options=opts)
        sel.callback = self._on_select
        self.add_item(sel)
        self.add_item(EBActionButton("⬅️ Voltar", ButtonStyle.secondary,
                                       "eb_back", self._back, row=1))
    async def _on_select(self, i):
        idx = int(i.data["values"][0])
        if idx >= len(self.session.fields):
            return await i.response.send_message(
                embed=embed_error("Campo inválido."), ephemeral=True)
        if self.action == "rm":
            removed = self.session.fields.pop(idx)
            self.session.touch()
            await i.response.edit_message(
                embed=self.session.build_preview_embed(), view=self.parent)
            await i.followup.send(embed=embed_success(
                f"🗑️ Campo `{removed['name']}` removido."), ephemeral=True)
        else:
            await i.response.send_modal(
                EBEditFieldModal(self.session, self.parent, idx))
    async def _back(self, i):
        await i.response.edit_message(
            embed=self.session.build_preview_embed(), view=self.parent)


class _FieldReorderView(ui.View):
    def __init__(self, session, parent):
        super().__init__(timeout=300)
        self.session, self.parent = session, parent
        opts = [SelectOption(label=f"#{i + 1}: {f['name'][:60]}", value=str(i))
                 for i, f in enumerate(session.fields[:25])]
        sel = ui.Select(placeholder="Escolha um campo...", options=opts)
        sel.callback = self._on_select
        self.add_item(sel)
        self.add_item(EBActionButton("⬆️ Subir", ButtonStyle.primary,
                                       "eb_up", self._up, row=1))
        self.add_item(EBActionButton("⬇️ Descer", ButtonStyle.primary,
                                       "eb_down", self._down, row=1))
        self.add_item(EBActionButton("⬅️ Voltar", ButtonStyle.secondary,
                                       "eb_back2", self._back, row=1))
        self._sel_idx = None
    async def _on_select(self, i):
        self._sel_idx = int(i.data["values"][0])
        await i.response.send_message(embed=embed_info(
            f"Campo #{self._sel_idx + 1} selecionado."), ephemeral=True)
    async def _up(self, i):
        if self._sel_idx is None or self._sel_idx <= 0:
            return await i.response.send_message(
                embed=embed_warning("Escolha um campo que não seja o 1º."),
                ephemeral=True)
        j = self._sel_idx
        self.session.fields[j - 1], self.session.fields[j] = (
            self.session.fields[j], self.session.fields[j - 1])
        self._sel_idx = j - 1
        self.session.touch()
        await i.response.send_message(embed=embed_success("⬆️ Subiu!"),
                                        ephemeral=True)
    async def _down(self, i):
        if self._sel_idx is None or self._sel_idx >= len(self.session.fields) - 1:
            return await i.response.send_message(
                embed=embed_warning("Escolha um campo que não seja o último."),
                ephemeral=True)
        j = self._sel_idx
        self.session.fields[j + 1], self.session.fields[j] = (
            self.session.fields[j], self.session.fields[j + 1])
        self._sel_idx = j + 1
        self.session.touch()
        await i.response.send_message(embed=embed_success("⬇️ Desceu!"),
                                        ephemeral=True)
    async def _back(self, i):
        await i.response.edit_message(
            embed=self.session.build_preview_embed(), view=self.parent)


class EBComponentsView(ui.View):
    def __init__(self, session, parent):
        super().__init__(timeout=600)
        self.session, self.parent = session, parent
        self._build()
    def _build(self):
        self.clear_items()
        if not self.session.components:
            self.add_item(EBActionButton("⬅️ Voltar", ButtonStyle.secondary,
                                           "eb_back", self._back, row=0))
            return
        opts = []
        for comp in self.session.components[:25]:
            label = comp.get("label") or comp.get("placeholder") or comp["id"]
            emoji = comp.get("emoji") or ("🔘" if comp["type"] == "button" else "▼")
            opts.append(SelectOption(label=label[:80], value=comp["id"],
                                       description=f"Tipo: {comp['type']} | Row: {comp.get('row', 0)}"[:100],
                                       emoji=emoji if isinstance(emoji, str) else None))
        sel = ui.Select(placeholder="🧩 Escolha um componente...", options=opts)
        sel.callback = self._on_select
        self.add_item(sel)
        self.add_item(EBActionButton("⬅️ Voltar", ButtonStyle.secondary,
                                       "eb_back", self._back, row=1))
    def build_embed(self):
        embed = discord.Embed(title="🧩 Componentes",
                                description=f"Total: **{len(self.session.components)}**",
                                color=discord.Color.purple(),
                                timestamp=_now())
        if not self.session.components:
            embed.description = "*Nenhum componente ainda.*"
            return embed
        for i, comp in enumerate(self.session.components[:10], 1):
            label = comp.get("label") or comp.get("placeholder") or "(sem label)"
            action = comp.get("action", {}).get("type", "none")
            embed.add_field(name=f"{i}. {label[:40]}",
                             value=f"Tipo: `{comp['type']}` | Row: `{comp.get('row', 0)}` | Ação: `{action}` | ID: `{comp['id']}`",
                             inline=False)
        return embed
    async def _on_select(self, i):
        cid = i.data["values"][0]
        comp = next((c for c in self.session.components if c["id"] == cid), None)
        if not comp:
            return await i.response.send_message(
                embed=embed_error("Componente não encontrado."), ephemeral=True)
        view = EBComponentEditView(self.session, self.parent, comp)
        await i.response.edit_message(embed=view.build_embed(), view=view)
    async def _back(self, i):
        await i.response.edit_message(
            embed=self.session.build_preview_embed(), view=self.parent)


class EBComponentEditView(ui.View):
    def __init__(self, session, parent, comp):
        super().__init__(timeout=600)
        self.session, self.parent, self.comp = session, parent, comp
        self._build()
    def _build(self):
        self.clear_items()
        self.add_item(EBActionButton("🏷️ Label", ButtonStyle.primary,
                                       "eb_c_label", self._edit_label, row=0))
        self.add_item(EBActionButton("😀 Emoji", ButtonStyle.primary,
                                       "eb_c_emoji", self._edit_emoji, row=0))
        self.add_item(EBActionButton("📏 Row", ButtonStyle.primary,
                                       "eb_c_row", self._edit_row, row=0))
        if self.comp["type"] == "button":
            self.add_item(EBActionButton("🎨 Estilo", ButtonStyle.primary,
                                           "eb_c_style", self._edit_style, row=0))
        if self.comp["type"] == "select":
            self.add_item(EBActionButton("💬 Placeholder", ButtonStyle.primary,
                                           "eb_c_ph", self._edit_ph, row=0))
        if self.comp["type"] == "select":
            self.add_item(EBActionButton("🔢 Min/Max", ButtonStyle.primary,
                                           "eb_c_minmax", self._edit_mm, row=1))
            self.add_item(EBActionButton("➕ Opções", ButtonStyle.success,
                                           "eb_c_addopt", self._add_opt, row=1))
        self.add_item(EBActionButton("🎯 Ação", ButtonStyle.success,
                                       "eb_c_action", self._action, row=2))
        self.add_item(EBActionButton("🗑️ Remover", ButtonStyle.danger,
                                       "eb_c_rm", self._remove, row=2))
        self.add_item(EBActionButton("⬅️ Voltar", ButtonStyle.secondary,
                                       "eb_c_back", self._back, row=2))
    def build_embed(self):
        c = self.comp
        embed = discord.Embed(
            title=f"🧩 Editando: {c.get('label') or c.get('placeholder') or c['id']}",
            color=discord.Color.blue(), timestamp=_now())
        embed.add_field(name="ID", value=f"`{c['id']}`", inline=True)
        embed.add_field(name="Tipo", value=f"`{c['type']}`", inline=True)
        embed.add_field(name="Row", value=f"`{c.get('row', 0)}`", inline=True)
        if c["type"] == "button":
            embed.add_field(name="Label", value=c.get("label") or "—", inline=True)
            embed.add_field(name="Emoji", value=c.get("emoji") or "—", inline=True)
            embed.add_field(name="Estilo", value=f"`{c.get('style', 'primary')}`", inline=True)
        else:
            embed.add_field(name="Placeholder", value=c.get("placeholder") or "—", inline=False)
            embed.add_field(name="Min/Max",
                             value=f"{c.get('min_values', 1)}/{c.get('max_values', 1)}",
                             inline=True)
            opts = c.get("options") or []
            embed.add_field(name=f"Opções ({len(opts)})",
                             value="\n".join(f"• `{o.get('label')}`" for o in opts[:10]) or "*nenhuma*",
                             inline=False)
        action = c.get("action") or {"type": "none"}
        embed.add_field(name="🎯 Ação", value=f"`{action.get('type', 'none')}`",
                         inline=False)
        return embed
    async def _edit_label(self, i):
        await i.response.send_modal(_SimpleTextModal(
            "🏷️ Label", "Label", self.comp.get("label") or "",
            LIMITS["button_label"], lambda v: self._set("label", v)))
    async def _edit_emoji(self, i):
        await i.response.send_modal(_SimpleTextModal(
            "😀 Emoji", "Emoji", self.comp.get("emoji") or "", 50,
            lambda v: self._set("emoji", v or None)))
    async def _edit_row(self, i):
        await i.response.send_modal(_SimpleTextModal(
            "📏 Row", "Row (0-4)", str(self.comp.get("row", 0)), 1,
            lambda v: self._set("row", max(0, min(int(v or "0"), 4)))))
    async def _edit_style(self, i):
        await i.response.send_modal(_SimpleTextModal(
            "🎨 Estilo", "primary/secondary/success/danger/link",
            self.comp.get("style", "primary"), 20,
            lambda v: self._set("style", (v or "primary").lower()
                                  if (v or "").lower() in BUTTON_STYLES
                                  else "primary")))
    async def _edit_ph(self, i):
        await i.response.send_modal(_SimpleTextModal(
            "💬 Placeholder", "Placeholder",
            self.comp.get("placeholder") or "",
            LIMITS["select_placeholder"], lambda v: self._set("placeholder", v)))
    async def _edit_mm(self, i):
        await i.response.send_modal(_SimpleTextModal(
            "🔢 Min/Max", "Ex: 1,1",
            f"{self.comp.get('min_values', 1)},{self.comp.get('max_values', 1)}",
            10, lambda v: self._set_mm(v)))
    async def _add_opt(self, i):
        await i.response.send_modal(EBAddOptionModal(self.session, self.comp, self))
    async def _action(self, i):
        view = EBActionConfigView(self.session, self.parent, self.comp)
        await i.response.edit_message(embed=view.build_embed(), view=view)
    async def _remove(self, i):
        self.session.components = [c for c in self.session.components
                                     if c["id"] != self.comp["id"]]
        self.session.touch()
        await i.response.edit_message(
            embed=self.session.build_preview_embed(), view=self.parent)
    async def _back(self, i):
        view = EBComponentsView(self.session, self.parent)
        await i.response.edit_message(embed=view.build_embed(), view=view)
    def _set(self, k, v):
        self.comp[k] = v
        self.session.touch()
    def _set_mm(self, raw):
        try:
            parts = [int(x) for x in (raw or "1,1").split(",")]
            mn = max(0, min(parts[0], 25))
            mx = max(mn, min(parts[1] if len(parts) > 1 else mn, 25))
            self.comp["min_values"] = mn
            self.comp["max_values"] = mx
            self.session.touch()
        except Exception:
            pass


class _SimpleTextModal(ui.Modal):
    value = ui.TextInput(label="Valor", required=False)
    def __init__(self, title, label, default, max_len, on_submit_cb):
        super().__init__(title=title[:45])
        self._cb = on_submit_cb
        self.value.label = label[:45]
        self.value.max_length = min(max_len, 4000)  # LIMITE FIXO
        self.value.default = default or None
        self.add_item(self.value)
    async def on_submit(self, i):
        try:
            self._cb(self.value.value)
        except Exception as e:
            return await i.response.send_message(
                embed=embed_error(f"Erro: {e}"), ephemeral=True)
        await i.response.send_message(
            embed=embed_success("✅ Atualizado! Reabra o editor."),
            ephemeral=True)


class EBActionsView(ui.View):
    def __init__(self, session, parent):
        super().__init__(timeout=600)
        self.session, self.parent = session, parent
        self._build()
    def _build(self):
        self.clear_items()
        opts = []
        for comp in self.session.components[:25]:
            label = comp.get("label") or comp.get("placeholder") or comp["id"]
            action_type = comp.get("action", {}).get("type", "none")
            opts.append(SelectOption(label=label[:80], value=comp["id"],
                                       description=f"Ação: {action_type}"[:100]))
        sel = ui.Select(placeholder="🎯 Escolha um componente...", options=opts)
        sel.callback = self._on_select
        self.add_item(sel)
        self.add_item(EBActionButton("⬅️ Voltar", ButtonStyle.secondary,
                                       "eb_a_back", self._back, row=1))
    def build_embed(self):
        embed = discord.Embed(title="🎯 Configurar Ações",
                                description="Escolha um componente pra configurar.",
                                color=discord.Color.green(), timestamp=_now())
        linhas = [f"• `{k}` — {v[0]}" for k, v in list(ACTION_TYPES.items())[:20]]
        embed.add_field(name="Ações disponíveis", value="\n".join(linhas),
                         inline=False)
        return embed
    async def _on_select(self, i):
        cid = i.data["values"][0]
        comp = next((c for c in self.session.components if c["id"] == cid), None)
        if not comp:
            return await i.response.send_message(
                embed=embed_error("Componente não encontrado."), ephemeral=True)
        view = EBActionConfigView(self.session, self.parent, comp)
        await i.response.edit_message(embed=view.build_embed(), view=view)
    async def _back(self, i):
        await i.response.edit_message(
            embed=self.session.build_preview_embed(), view=self.parent)


class EBActionConfigView(ui.View):
    def __init__(self, session, parent, comp, option_index=None):
        super().__init__(timeout=600)
        self.session, self.parent = session, parent
        self.comp, self.option_index = comp, option_index
        self._build()
    def _target(self):
        if self.option_index is not None:
            opts = self.comp.get("options") or []
            if 0 <= self.option_index < len(opts):
                return opts[self.option_index]
        return self.comp
    def _build(self):
        self.clear_items()
        target = self._target()
        current_type = target.get("action", {}).get("type", "none")
        opts = [SelectOption(label=v[0][:80], value=k,
                              default=(k == current_type))
                 for k, v in ACTION_TYPES.items()]
        sel = ui.Select(placeholder="🎯 Escolha o tipo de ação...", options=opts)
        sel.callback = self._on_type_change
        self.add_item(sel)
        self.add_item(EBActionButton("⚙️ Parâmetros", ButtonStyle.success,
                                       "eb_cfg_params", self._params, row=1))
        if self.comp["type"] == "select" and self.option_index is None:
            self.add_item(EBActionButton("🎯 Ação por opção", ButtonStyle.primary,
                                           "eb_opt_actions", self._opt_actions, row=1))
        self.add_item(EBActionButton("⬅️ Voltar", ButtonStyle.secondary,
                                       "eb_a_cback", self._back, row=1))
    def build_embed(self):
        target = self._target()
        action = target.get("action") or {"type": "none"}
        embed = discord.Embed(title="🎯 Configurar Ação",
                                color=discord.Color.green(), timestamp=_now())
        if self.option_index is not None:
            embed.description = f"Ação da **opção** `{target.get('label', '?')}`"
        else:
            embed.description = f"Ação do **componente** `{self.comp.get('label') or self.comp.get('placeholder') or self.comp['id']}`"
        embed.add_field(name="Tipo atual", value=f"`{action.get('type', 'none')}`",
                         inline=False)
        params = [f"• `{k}`: `{str(v)[:100]}`" for k, v in action.items()
                   if k != "type"]
        embed.add_field(name="Parâmetros",
                         value="\n".join(params[:10]) if params else "*nenhum*",
                         inline=False)
        return embed
    async def _on_type_change(self, i):
        new_type = i.data["values"][0]
        target = self._target()
        target["action"] = {"type": new_type}
        self.session.touch()
        view = EBActionConfigView(self.session, self.parent, self.comp,
                                    self.option_index)
        await i.response.edit_message(embed=view.build_embed(), view=view)
    async def _params(self, i):
        target = self._target()
        action = target.get("action") or {"type": "none"}
        atype = action.get("type", "none")
        await i.response.send_modal(_ActionParamsModal(
            self.session, self, target, atype))
    async def _opt_actions(self, i):
        opts = self.comp.get("options") or []
        if not opts:
            return await i.response.send_message(
                embed=embed_info("Adicione opções primeiro."), ephemeral=True)
        view = EBActionPerOptionView(self.session, self.parent, self.comp)
        await i.response.edit_message(embed=view.build_embed(), view=view)
    async def _back(self, i):
        if self.option_index is not None:
            view = EBActionPerOptionView(self.session, self.parent, self.comp)
            await i.response.edit_message(embed=view.build_embed(), view=view)
        else:
            await i.response.edit_message(
                embed=self.session.build_preview_embed(), view=self.parent)


class EBActionPerOptionView(ui.View):
    def __init__(self, session, parent, comp):
        super().__init__(timeout=600)
        self.session, self.parent, self.comp = session, parent, comp
        opts = []
        for i, o in enumerate((comp.get("options") or [])[:25]):
            at = (o.get("action") or {}).get("type", "none")
            opts.append(SelectOption(label=(o.get("label") or "?")[:80],
                                       value=str(i),
                                       description=f"Ação: {at}"[:100]))
        if opts:
            sel = ui.Select(placeholder="Escolha a opção...", options=opts)
            sel.callback = self._on_select
            self.add_item(sel)
        self.add_item(EBActionButton("⬅️ Voltar", ButtonStyle.secondary,
                                       "eb_aopo_back", self._back, row=1))
    def build_embed(self):
        embed = discord.Embed(title="🎯 Ação por opção",
                                description=f"Select: **{self.comp.get('placeholder', self.comp['id'])}**",
                                color=discord.Color.green(), timestamp=_now())
        for i, o in enumerate((self.comp.get("options") or [])[:10]):
            at = (o.get("action") or {}).get("type", "none")
            embed.add_field(name=f"{i + 1}. {o.get('label', '?')}",
                             value=f"Ação: `{at}`", inline=True)
        return embed
    async def _on_select(self, i):
        idx = int(i.data["values"][0])
        view = EBActionConfigView(self.session, self.parent, self.comp,
                                    option_index=idx)
        await i.response.edit_message(embed=view.build_embed(), view=view)
    async def _back(self, i):
        view = EBActionConfigView(self.session, self.parent, self.comp)
        await i.response.edit_message(embed=view.build_embed(), view=view)


class _ActionParamsModal(ui.Modal):
    def __init__(self, session, view_ref, target, atype):
        label = ACTION_TYPES.get(atype, ("?", []))[0]
        super().__init__(title=f"⚙️ {label[:40]}")
        self.session, self.view_ref = session, view_ref
        self.target, self.atype = target, atype
        action = target.get("action") or {}
        self._fields = []

        def add(key, lbl, default="", required=False, paragraph=False, max_len=1000):
            ti = ui.TextInput(
                label=lbl[:45],
                default=str(default or "")[:max_len] or None,
                required=required,
                max_length=min(max_len, 4000),  # LIMITE FIXO
                style=discord.TextStyle.paragraph if paragraph else discord.TextStyle.short)
            self.add_item(ti)
            self._fields.append((key, ti))

        if atype == "reply_text":
            add("text", "Texto", action.get("text", ""), paragraph=True, max_len=2000)
        elif atype in ("reply_embed", "send_to_channel", "send_message", "send_dm"):
            if atype == "send_to_channel":
                add("channel_id", "ID do canal", action.get("channel_id", ""),
                     required=True, max_len=25)
            add("message_name", "Template", action.get("message_name", ""))
            add("content", "Texto antes", action.get("content", ""),
                 paragraph=True, max_len=2000)
        elif atype == "redirect_channel":
            add("channel_id", "ID do canal", action.get("channel_id", ""),
                 required=True, max_len=25)
            add("text", "Texto", action.get("text", ""),
                 paragraph=True, max_len=500)
        elif atype in ("give_role", "remove_role", "toggle_role"):
            add("role_id", "ID do cargo", action.get("role_id", ""),
                 required=True, max_len=25)
            add("text", "Texto resposta", action.get("text", ""),
                 paragraph=True, max_len=500)
        elif atype.startswith("multi_"):
            add("role_ids", "IDs (vírgula)", action.get("role_ids", ""),
                 required=True, max_len=500)
            add("text", "Texto resposta", action.get("text", ""),
                 paragraph=True, max_len=500)
        elif atype == "temp_role":
            add("role_id", "ID do cargo", action.get("role_id", ""),
                 required=True, max_len=25)
            add("seconds", "Segundos", action.get("seconds", "60"),
                 required=True, max_len=10)
            add("text", "Texto", action.get("text", ""),
                 paragraph=True, max_len=500)
        elif atype == "open_panel" or atype == "select_cascade":
            add("target_panel", "Nome do painel",
                 action.get("target_panel") or action.get("panel_name", ""),
                 required=True, max_len=50)
        elif atype == "open_template":
            add("template_name", "Nome do template",
                 action.get("template_name", ""), required=True, max_len=50)
        elif atype == "add_to_list":
            add("list_name", "Nome da lista", action.get("list_name", ""),
                 required=True, max_len=50)
            add("text", "Texto resposta", action.get("text", ""),
                 paragraph=True, max_len=500)
        elif atype in ("kick", "ban", "warn"):
            add("reason", "Motivo", action.get("reason", ""),
                 paragraph=True, max_len=500)
        elif atype == "timeout":
            add("seconds", "Segundos", action.get("seconds", "60"),
                 required=True, max_len=10)
            add("reason", "Motivo", action.get("reason", ""),
                 paragraph=True, max_len=500)
        elif atype == "reaction_role":
            add("emoji", "Emoji", action.get("emoji", "✅"),
                 required=True, max_len=50)
            add("role_id", "ID do cargo", action.get("role_id", ""),
                 required=True, max_len=25)

    async def on_submit(self, i):
        new_action = {"type": self.atype}
        for key, ti in self._fields:
            val = (ti.value or "").strip()
            if val:
                new_action[key] = val
        if self.atype in ("timeout",):
            try:
                new_action["seconds"] = int(new_action.get("seconds", 60))
            except Exception:
                new_action["seconds"] = 60
        if self.atype == "send_to_channel":
            try:
                new_action["channel_id"] = int(new_action.get("channel_id", 0))
            except Exception:
                return await i.response.send_message(
                    embed=embed_error("ID inválido."), ephemeral=True)
        if self.atype in ("give_role", "remove_role", "toggle_role", "temp_role"):
            try:
                new_action["role_id"] = int(new_action.get("role_id", 0))
            except Exception:
                return await i.response.send_message(
                    embed=embed_error("ID de cargo inválido."), ephemeral=True)
        self.target["action"] = new_action
        self.session.touch()
        await i.response.send_message(embed=embed_success("✅ Ação configurada!"),
                                        ephemeral=True)


class EBMessagesView(ui.View):
    def __init__(self, session, parent, bot):
        super().__init__(timeout=600)
        self.session, self.parent, self.bot = session, parent, bot
        self.add_item(EBActionButton("➕ Criar", ButtonStyle.success,
                                       "eb_msg_new", self._create, row=0))
        self.add_item(EBActionButton("📋 Listar", ButtonStyle.primary,
                                       "eb_msg_list", self._list, row=0))
        self.add_item(EBActionButton("⬅️ Voltar", ButtonStyle.secondary,
                                       "eb_msg_back", self._back, row=0))
    def build_embed(self):
        return discord.Embed(
            title="📋 Templates de Mensagem",
            description="Templates são mensagens/embeds reutilizáveis.\nUse `>eb msg criar <nome>`.",
            color=discord.Color.blurple(), timestamp=_now())
    async def _create(self, i):
        await i.response.send_message(
            embed=embed_info("Use `>eb msg criar <nome>` no chat."),
            ephemeral=True)
    async def _list(self, i):
        msgs = _list_messages(i.guild.id)
        if not msgs:
            return await i.response.send_message(
                embed=embed_info("Nenhum template."), ephemeral=True)
        text = "\n".join(f"• `{m['name']}`" for m in msgs[:20])
        await i.response.send_message(embed=embed_info(f"**{len(msgs)} templates:**\n{text}"),
                                        ephemeral=True)
    async def _back(self, i):
        await i.response.edit_message(
            embed=self.session.build_preview_embed(), view=self.parent)


# ============================================================
# COG PRINCIPAL
# ============================================================
class EmbedBuilderCommands(commands.Cog):
    """Cog com todos os comandos do Embed Builder v2.0."""

    def __init__(self, bot):
        self.bot = bot
        self._cleanup_task = None

    async def cog_load(self):
        self._cleanup_task = self.bot.loop.create_task(self._cleanup_loop())

    async def cog_unload(self):
        if self._cleanup_task:
            self._cleanup_task.cancel()

    async def _cleanup_loop(self):
        """Limpa sessões expiradas a cada 5min."""
        while True:
            try:
                await asyncio.sleep(300)
                expired = [uid for uid, s in SessionManager._cache.items()
                            if s.is_expired()]
                for uid in expired:
                    SessionManager._cache.pop(uid, None)
            except asyncio.CancelledError:
                break
            except Exception:
                pass

    # ─── Comando principal ───
    @commands.group(name="eb", aliases=["embedbuilder", "ebuild"],
                     invoke_without_command=True)
    @commands.has_permissions(manage_messages=True)
    async def eb(self, ctx):
        """Painel principal do Embed Builder."""
        embed = discord.Embed(
            title="🎨 EMBED BUILDER v2.0",
            description=(
                "Crie painéis interativos persistentes.\n\n"
                "**📌 Comandos principais:**\n"
                "`>eb criar <nome>` — cria painel\n"
                "`>eb listar` — lista painéis\n"
                "`>eb editar <nome>` — editor visual\n"
                "`>eb enviar <nome>` — publica\n"
                "`>eb deletar <nome>` — apaga\n\n"
                "**📁 Canais (NOVO!):**\n"
                "`>eb canal-privado #canal` — onde você edita\n"
                "`>eb canal-publico #canal` — onde publica\n"
                "`>eb canais` — ver configurado\n\n"
                "**📋 Templates:**\n"
                "`>eb msg criar <nome>`\n"
                "`>eb msg listar`\n\n"
                "**🔧 Variáveis:**\n"
                "`>eb var add <key> <valor>`\n"
                "`>eb var listar`"
            ),
            color=discord.Color.blurple(),
            timestamp=_now())
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)
        await ctx.send(embed=embed)

    # ─── Configurar canais ───
    @eb.command(name="canal-privado", aliases=["privado", "private"])
    @commands.has_permissions(administrator=True)
    async def eb_private_channel(self, ctx, canal: discord.TextChannel = None):
        """Define o canal privado (onde você edita os painéis)."""
        if not canal:
            cfg = _get_eb_config(ctx.guild.id)
            priv = cfg.get("private_channel")
            return await ctx.send(embed=embed_info(
                f"🔒 Canal privado: {f'<#{priv}>' if priv else '*não configurado*'}\n"
                f"Use: `>eb canal-privado #canal`"))
        _set_eb_config(ctx.guild.id, "private_channel", canal.id)
        await ctx.send(embed=embed_success(
            f"🔒 Canal privado definido: {canal.mention}\n"
            f"Use `>eb editar <nome>` pra abrir o editor aqui."))

    @eb.command(name="canal-publico", aliases=["publico", "public"])
    @commands.has_permissions(administrator=True)
    async def eb_public_channel(self, ctx, canal: discord.TextChannel = None):
        """Define o canal público (onde os painéis são publicados)."""
        if not canal:
            cfg = _get_eb_config(ctx.guild.id)
            pub = cfg.get("public_channel")
            return await ctx.send(embed=embed_info(
                f"🌐 Canal público: {f'<#{pub}>' if pub else '*não configurado*'}\n"
                f"Use: `>eb canal-publico #canal`"))
        _set_eb_config(ctx.guild.id, "public_channel", canal.id)
        await ctx.send(embed=embed_success(
            f"🌐 Canal público definido: {canal.mention}\n"
            f"`>eb enviar <nome>` agora publica direto aqui."))

    @eb.command(name="canais", aliases=["channels"])
    @commands.has_permissions(manage_messages=True)
    async def eb_channels(self, ctx):
        """Mostra os canais configurados."""
        cfg = _get_eb_config(ctx.guild.id)
        priv = cfg.get("private_channel")
        pub = cfg.get("public_channel")
        embed = discord.Embed(title="📁 Canais do Embed Builder",
                                color=discord.Color.blurple(),
                                timestamp=_now())
        embed.add_field(name="🔒 Privado (edição)",
                         value=f"<#{priv}>" if priv else "❌ Não configurado",
                         inline=False)
        embed.add_field(name="🌐 Público (publicação)",
                         value=f"<#{pub}>" if pub else "❌ Não configurado",
                         inline=False)
        embed.set_footer(text="Use >eb canal-privado/privado pra configurar")
        await ctx.send(embed=embed)

    # ─── Criar ───
    @eb.command(name="criar", aliases=["create", "new"])
    @commands.has_permissions(manage_messages=True)
    async def eb_create(self, ctx, *, name: str):
        """Cria um novo painel."""
        name = name.lower().strip()
        if not re.fullmatch(r"[a-z0-9_\-]{2,50}", name):
            return await ctx.send(embed=embed_error(
                "Nome inválido (2-50 chars, minúsculas, `_` ou `-`)."))
        existing = _load_panel(ctx.guild.id, name)
        if existing:
            return await ctx.send(embed=embed_error(f"Já existe `{name}`."))
        panel = {
            "name": name,
            "embed": {"title": "", "description": "", "color": 0x5865F2,
                       "url": "", "author": {"name": "", "icon_url": "",
                                                "url": ""},
                       "thumbnail": "", "image": "",
                       "footer": {"text": "", "icon_url": ""},
                       "fields": []},
            "components": [],
            "author_id": ctx.author.id,
            "author_name": str(ctx.author),
            "persistent": True,
        }
        _save_panel(ctx.guild.id, name, panel)
        await ctx.send(embed=embed_success(
            f"🎨 Painel **`{name}`** criado!\nUse `>eb editar {name}`."))

    # ─── Listar ───
    @eb.command(name="listar", aliases=["list", "ls"])
    @commands.has_permissions(manage_messages=True)
    async def eb_list(self, ctx):
        """Lista todos os painéis."""
        panels = _list_panels(ctx.guild.id, 30)
        if not panels:
            return await ctx.send(embed=embed_info(
                "Nenhum painel. Use `>eb criar <nome>`."))
        embed = discord.Embed(title=f"🎨 Painéis ({len(panels)})",
                                color=discord.Color.blurple(),
                                timestamp=_now())
        for p in panels[:25]:
            emoji = "📤" if p.get("message_id") else "💾"
            ts = p.get("updated_at", _now())
            ts_str = ts.strftime("%d/%m %H:%M") if hasattr(ts, "strftime") else "?"
            embed.add_field(name=f"{emoji} `{p['name']}`",
                             value=f"Componentes: `{len(p.get('components', []))}`\n"
                                    f"Atualizado: `{ts_str}`",
                             inline=True)
        await ctx.send(embed=embed)

    # ─── Editar (editor visual) ───
    @eb.command(name="editar", aliases=["edit", "e"])
    @commands.has_permissions(manage_messages=True)
    async def eb_edit(self, ctx, *, name: str):
        """Abre o editor visual."""
        name = name.lower().strip()
        panel = _load_panel(ctx.guild.id, name)
        if not panel:
            return await ctx.send(embed=embed_error(f"Painel `{name}` não encontrado."))

        # Verifica se tem canal privado configurado.
        cfg = _get_eb_config(ctx.guild.id)
        private_ch = cfg.get("private_channel")
        target_channel = ctx.channel
        if private_ch and private_ch != ctx.channel.id:
            ch = ctx.guild.get_channel(int(private_ch))
            if ch:
                target_channel = ch
                await ctx.send(embed=embed_info(
                    f"📁 Abrindo editor em {ch.mention} (canal privado)..."))
            else:
                await ctx.send(embed=embed_warning(
                    "⚠️ Canal privado não encontrado. Abrindo aqui."))
        # Cria sessão.
        session = SessionManager.create(ctx.author.id, ctx.guild.id,
                                          target_channel.id, name)
        session_data = BuilderSession.from_panel(
            ctx.author.id, ctx.guild.id, target_channel.id, panel)
        for k, v in asdict(session_data).items():
            if hasattr(session, k) and k not in ("user_id", "guild_id",
                                                    "channel_id", "panel_name"):
                setattr(session, k, v)
        SessionManager._cache[ctx.author.id] = session
        view = EBBuilderView(session, self.bot)
        embed = session.build_preview_embed()
        try:
            msg = await target_channel.send(embed=embed, view=view)
            session.panel_message_id = msg.id
        except Exception as e:
            return await ctx.send(embed=embed_error(f"Erro: {e}"))
        try:
            await ctx.message.delete()
        except Exception:
            pass

    # ─── Ver (preview) ───
    @eb.command(name="ver", aliases=["view", "preview"])
    @commands.has_permissions(manage_messages=True)
    async def eb_view(self, ctx, *, name: str):
        """Preview do painel."""
        panel = _load_panel(ctx.guild.id, name.lower())
        if not panel:
            return await ctx.send(embed=embed_error("Painel não encontrado."))
        embed = dict_to_embed(panel.get("embed") or {})
        view = ui.View(timeout=None)
        for c in _build_components_from_panel(panel, self.bot):
            view.add_item(c)
        await ctx.send(content=f"👁️ Preview: **{name}**",
                        embed=embed if embed_has_content(embed) else None,
                        view=view if len(view.children) else None)

    # ─── Enviar ───
    @eb.command(name="enviar", aliases=["send"])
    @commands.has_permissions(manage_messages=True)
    async def eb_send(self, ctx, name: str, channel: discord.TextChannel = None):
        """Envia o painel. Se canal público configurado, usa ele."""
        panel = _load_panel(ctx.guild.id, name.lower())
        if not panel:
            return await ctx.send(embed=embed_error("Painel não encontrado."))
        # Se não passou canal, usa o público.
        if channel is None:
            cfg = _get_eb_config(ctx.guild.id)
            pub = cfg.get("public_channel")
            if pub:
                channel = ctx.guild.get_channel(int(pub))
        # Se ainda não tem, usa o canal atual.
        if channel is None:
            channel = ctx.channel
        embed = dict_to_embed(panel.get("embed") or {})
        view = ui.View(timeout=None)
        for c in _build_components_from_panel(panel, self.bot):
            view.add_item(c)
        try:
            msg = await channel.send(
                embed=embed if embed_has_content(embed) else None,
                view=view if len(view.children) else None)
        except Exception as e:
            return await ctx.send(embed=embed_error(f"Erro: {e}"))
        try:
            db = get_connection()
            db["embed_panels"].update_one(
                {"_id": panel["_id"]},
                {"$set": {"message_id": msg.id, "channel_id": channel.id}})
        except Exception:
            pass
        await ctx.send(embed=embed_success(
            f"📤 Painel enviado em {channel.mention}!"))

    # ─── Reenviar (atualiza) ───
    @eb.command(name="reenviar", aliases=["update", "refresh"])
    @commands.has_permissions(manage_messages=True)
    async def eb_resend(self, ctx, name: str):
        """Atualiza a mensagem já enviada."""
        panel = _load_panel(ctx.guild.id, name.lower())
        if not panel:
            return await ctx.send(embed=embed_error("Painel não encontrado."))
        if not panel.get("message_id") or not panel.get("channel_id"):
            return await ctx.send(embed=embed_error(
                "Esse painel ainda não foi enviado."))
        try:
            ch = ctx.guild.get_channel(int(panel["channel_id"]))
            if not ch:
                return await ctx.send(embed=embed_error("Canal não existe mais."))
            msg = await ch.fetch_message(int(panel["message_id"]))
            embed = dict_to_embed(panel.get("embed") or {})
            view = ui.View(timeout=None)
            for c in _build_components_from_panel(panel, self.bot):
                view.add_item(c)
            await msg.edit(
                embed=embed if embed_has_content(embed) else None,
                view=view if len(view.children) else None)
            await ctx.send(embed=embed_success("🔄 Painel atualizado!"))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    # ─── Deletar ───
    @eb.command(name="deletar", aliases=["delete", "del", "rm"])
    @commands.has_permissions(manage_messages=True)
    async def eb_delete(self, ctx, *, name: str):
        """Deleta um painel."""
        name = name.lower().strip()
        panel = _load_panel(ctx.guild.id, name)
        if not panel:
            return await ctx.send(embed=embed_error("Painel não encontrado."))
        if panel.get("message_id") and panel.get("channel_id"):
            try:
                ch = ctx.guild.get_channel(int(panel["channel_id"]))
                if ch:
                    msg = await ch.fetch_message(int(panel["message_id"]))
                    await msg.delete()
            except Exception:
                pass
        _delete_panel(ctx.guild.id, name)
        await ctx.send(embed=embed_success(f"🗑️ Painel `{name}` deletado."))

    # ─── Duplicar ───
    @eb.command(name="duplicar", aliases=["clone", "copy"])
    @commands.has_permissions(manage_messages=True)
    async def eb_clone(self, ctx, name: str, new_name: str):
        """Duplica um painel."""
        panel = _load_panel(ctx.guild.id, name.lower())
        if not panel:
            return await ctx.send(embed=embed_error("Original não encontrado."))
        new_name = new_name.lower().strip()
        if _load_panel(ctx.guild.id, new_name):
            return await ctx.send(embed=embed_error(f"Já existe `{new_name}`."))
        new_data = {
            "name": new_name,
            "embed": copy.deepcopy(panel.get("embed", {})),
            "components": copy.deepcopy(panel.get("components", [])),
            "author_id": ctx.author.id,
            "author_name": str(ctx.author),
            "persistent": True}
        _save_panel(ctx.guild.id, new_name, new_data)
        await ctx.send(embed=embed_success(f"📋 Duplicado: `{name}` → `{new_name}`"))

    # ─── Stats ───
    @eb.command(name="stats", aliases=["stat"])
    @commands.has_permissions(manage_messages=True)
    async def eb_stats(self, ctx):
        """Estatísticas do Embed Builder."""
        db = get_connection()
        panels = db["embed_panels"].count_documents({"guild_id": ctx.guild.id})
        msgs = db["embed_messages"].count_documents({"guild_id": ctx.guild.id})
        vars_c = db["embed_variables"].count_documents({"guild_id": ctx.guild.id})
        logs = db["embed_panel_logs"].count_documents({"guild_id": ctx.guild.id})
        embed = discord.Embed(title="📊 Estatísticas do Embed Builder",
                                color=discord.Color.blurple(), timestamp=_now())
        embed.add_field(name="🎨 Painéis", value=f"`{panels}`", inline=True)
        embed.add_field(name="📋 Templates", value=f"`{msgs}`", inline=True)
        embed.add_field(name="🔧 Variáveis", value=f"`{vars_c}`", inline=True)
        embed.add_field(name="🖱️ Interações", value=f"`{logs}`", inline=True)
        await ctx.send(embed=embed)

    # ─── Exportar ───
    @eb.command(name="exportar", aliases=["export"])
    @commands.has_permissions(manage_messages=True)
    async def eb_export(self, ctx, *, name: str):
        """Exporta um painel como JSON."""
        panel = _load_panel(ctx.guild.id, name.lower())
        if not panel:
            return await ctx.send(embed=embed_error("Painel não encontrado."))
        data = {"name": panel.get("name"), "embed": panel.get("embed"),
                 "components": panel.get("components")}
        text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
        if len(text) > 1900:
            file = discord.File(io.BytesIO(text.encode("utf-8")),
                                  filename=f"{name}.json")
            return await ctx.send(file=file)
        await ctx.send(f"```json\n{text[:1900]}\n```")

    # ─── Importar ───
    @eb.command(name="importar", aliases=["import"])
    @commands.has_permissions(manage_messages=True)
    async def eb_import(self, ctx, name: str, *, json_text: str):
        """Importa um painel de JSON."""
        name = name.lower().strip()
        if not re.fullmatch(r"[a-z0-9_\-]{2,50}", name):
            return await ctx.send(embed=embed_error("Nome inválido."))
        json_text = re.sub(r"^```(?:json)?\s*", "", json_text.strip())
        json_text = re.sub(r"\s*```$", "", json_text)
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as e:
            return await ctx.send(embed=embed_error(f"JSON inválido: {e}"))
        panel = {"name": name, "embed": data.get("embed") or {},
                  "components": data.get("components") or [],
                  "author_id": ctx.author.id, "author_name": str(ctx.author),
                  "persistent": True}
        _save_panel(ctx.guild.id, name, panel)
        await ctx.send(embed=embed_success(f"✅ Painel `{name}` importado."))

    # ═══════════════════════════════════════════════════════════
    # SUBCOMANDOS: msg (templates)
    # ═══════════════════════════════════════════════════════════
    @eb.group(name="msg", aliases=["message", "template"],
                invoke_without_command=True)
    @commands.has_permissions(manage_messages=True)
    async def eb_msg(self, ctx):
        """Gerencia templates."""
        await ctx.send(embed=embed_info(
            "Use: `>eb msg criar <nome>`, `>eb msg <nome> title <txt>`, "
            "`>eb msg listar`, `>eb msg deletar <nome>`, `>eb msg ver <nome>`"))

    @eb_msg.command(name="criar", aliases=["create", "new"])
    @commands.has_permissions(manage_messages=True)
    async def eb_msg_create(self, ctx, *, name: str):
        """Cria template vazio."""
        name = name.lower().strip()
        if not re.fullmatch(r"[a-z0-9_\-]{2,50}", name):
            return await ctx.send(embed=embed_error("Nome inválido."))
        if _load_message(ctx.guild.id, name):
            return await ctx.send(embed=embed_error("Já existe."))
        _save_message(ctx.guild.id, name, {
            "name": name, "content": "",
            "embed": {"title": "", "description": "",
                       "color": 0x5865F2, "fields": []},
            "author_id": ctx.author.id})
        await ctx.send(embed=embed_success(
            f"📋 Template `{name}` criado!\n"
            f"Use `>eb msg {name} title <txt>`."))

    @eb_msg.command(name="listar", aliases=["list", "ls"])
    @commands.has_permissions(manage_messages=True)
    async def eb_msg_list(self, ctx):
        """Lista templates."""
        msgs = _list_messages(ctx.guild.id)
        if not msgs:
            return await ctx.send(embed=embed_info("Nenhum template."))
        text = "\n".join(
            f"• `{m['name']}` — {len((m.get('embed') or {}).get('fields', []))} campos"
            for m in msgs[:25])
        await ctx.send(embed=embed_info(f"**{len(msgs)} templates:**\n{text}"))

    @eb_msg.command(name="deletar", aliases=["delete", "del", "rm"])
    @commands.has_permissions(manage_messages=True)
    async def eb_msg_delete(self, ctx, *, name: str):
        """Deleta template."""
        if _delete_message(ctx.guild.id, name.lower()):
            await ctx.send(embed=embed_success(f"🗑️ Template `{name}` deletado."))
        else:
            await ctx.send(embed=embed_error("Template não encontrado."))

    @eb_msg.command(name="ver", aliases=["show", "preview"])
    @commands.has_permissions(manage_messages=True)
    async def eb_msg_view(self, ctx, *, name: str):
        """Preview do template."""
        msg = _load_message(ctx.guild.id, name.lower())
        if not msg:
            return await ctx.send(embed=embed_error("Template não encontrado."))
        embed = dict_to_embed(msg.get("embed") or {}, guild=ctx.guild,
                                user=ctx.author, channel=ctx.channel)
        content = msg.get("content") or ""
        await ctx.send(content=content or None,
                        embed=embed if embed_has_content(embed) else None)

    @eb_msg.command(name="content", aliases=["conteudo", "texto"])
    @commands.has_permissions(manage_messages=True)
    async def eb_msg_content(self, ctx, name: str, *, text: str):
        """Define texto antes do embed."""
        msg = _load_message(ctx.guild.id, name.lower())
        if not msg:
            return await ctx.send(embed=embed_error("Template não encontrado."))
        msg["content"] = text[:2000]
        msg.pop("_id", None)
        _save_message(ctx.guild.id, name.lower(), msg)
        await ctx.send(embed=embed_success("✅ Texto atualizado."))

    @eb_msg.command(name="title", aliases=["titulo"])
    @commands.has_permissions(manage_messages=True)
    async def eb_msg_title(self, ctx, name: str, *, text: str):
        """Define título."""
        msg = _load_message(ctx.guild.id, name.lower())
        if not msg:
            return await ctx.send(embed=embed_error("Template não encontrado."))
        embed = msg.get("embed") or {}
        embed["title"] = text[:LIMITS["title"]]
        msg["embed"] = embed
        msg.pop("_id", None)
        _save_message(ctx.guild.id, name.lower(), msg)
        await ctx.send(embed=embed_success("✅ Título atualizado."))

    @eb_msg.command(name="desc", aliases=["description", "descricao"])
    @commands.has_permissions(manage_messages=True)
    async def eb_msg_desc(self, ctx, name: str, *, text: str):
        """Define descrição."""
        msg = _load_message(ctx.guild.id, name.lower())
        if not msg:
            return await ctx.send(embed=embed_error("Template não encontrado."))
        embed = msg.get("embed") or {}
        embed["description"] = text[:LIMITS["description"]]
        msg["embed"] = embed
        msg.pop("_id", None)
        _save_message(ctx.guild.id, name.lower(), msg)
        await ctx.send(embed=embed_success("✅ Descrição atualizada."))

    @eb_msg.command(name="color", aliases=["cor"])
    @commands.has_permissions(manage_messages=True)
    async def eb_msg_color(self, ctx, name: str, *, color: str):
        """Define cor."""
        msg = _load_message(ctx.guild.id, name.lower())
        if not msg:
            return await ctx.send(embed=embed_error("Template não encontrado."))
        try:
            c = parse_color(color)
        except ValueError as e:
            return await ctx.send(embed=embed_error(str(e)))
        embed = msg.get("embed") or {}
        embed["color"] = c
        msg["embed"] = embed
        msg.pop("_id", None)
        _save_message(ctx.guild.id, name.lower(), msg)
        await ctx.send(embed=embed_success("✅ Cor atualizada."))

    @eb_msg.command(name="footer")
    @commands.has_permissions(manage_messages=True)
    async def eb_msg_footer(self, ctx, name: str, *, text: str):
        """Define footer."""
        msg = _load_message(ctx.guild.id, name.lower())
        if not msg:
            return await ctx.send(embed=embed_error("Template não encontrado."))
        embed = msg.get("embed") or {}
        footer = embed.get("footer") or {}
        footer["text"] = text[:LIMITS["footer_text"]]
        embed["footer"] = footer
        msg["embed"] = embed
        msg.pop("_id", None)
        _save_message(ctx.guild.id, name.lower(), msg)
        await ctx.send(embed=embed_success("✅ Footer atualizado."))

    @eb_msg.command(name="field", aliases=["campo"])
    @commands.has_permissions(manage_messages=True)
    async def eb_msg_field(self, ctx, name: str, action: str,
                            field_name: str = None, *, value: str = None):
        """Adiciona/remove campo."""
        msg = _load_message(ctx.guild.id, name.lower())
        if not msg:
            return await ctx.send(embed=embed_error("Template não encontrado."))
        embed = msg.get("embed") or {}
        fields = embed.get("fields") or []
        if action.lower() == "add":
            if not field_name or not value:
                return await ctx.send(embed=embed_error("Use: `field add <nome> <valor>`"))
            inline = True
            parts = value.rsplit(" ", 1)
            if len(parts) == 2 and parts[1].lower() in ("false", "0", "não", "nao"):
                inline = False
                value = parts[0]
            fields.append({
                "name": field_name[:LIMITS["field_name"]],
                "value": value[:LIMITS["field_value"]],
                "inline": inline})
        elif action.lower() in ("remove", "rm", "del"):
            try:
                idx = int(field_name)
                fields.pop(idx)
            except Exception:
                return await ctx.send(embed=embed_error("Índice inválido."))
        else:
            return await ctx.send(embed=embed_error("Ações: `add`, `remove`"))
        embed["fields"] = fields
        msg["embed"] = embed
        msg.pop("_id", None)
        _save_message(ctx.guild.id, name.lower(), msg)
        await ctx.send(embed=embed_success(f"✅ Campo {action}."))

    @eb_msg.command(name="enviar", aliases=["send"])
    @commands.has_permissions(manage_messages=True)
    async def eb_msg_send(self, ctx, name: str, channel: discord.TextChannel = None):
        """Envia template num canal."""
        msg = _load_message(ctx.guild.id, name.lower())
        if not msg:
            return await ctx.send(embed=embed_error("Template não encontrado."))
        channel = channel or ctx.channel
        embed = dict_to_embed(msg.get("embed") or {}, guild=ctx.guild,
                                user=ctx.author, channel=channel)
        content = _resolve_variables(
            msg.get("content") or "", guild=ctx.guild, user=ctx.author,
            channel=channel, custom_vars=_get_custom_vars(ctx.guild.id))
        try:
            await channel.send(
                content=content[:2000] or None,
                embed=embed if embed_has_content(embed) else None)
            await ctx.send(embed=embed_success(f"📤 Enviado em {channel.mention}."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    # ═══════════════════════════════════════════════════════════
    # SUBCOMANDOS: var (variáveis customizadas)
    # ═══════════════════════════════════════════════════════════
    @eb.group(name="var", aliases=["variable", "variavel"],
                invoke_without_command=True)
    @commands.has_permissions(manage_messages=True)
    async def eb_var(self, ctx):
        """Gerencia variáveis customizadas."""
        await ctx.send(embed=embed_info(
            "Use: `>eb var add <key> <valor>`, `>eb var remove <key>`, "
            "`>eb var listar`"))

    @eb_var.command(name="add", aliases=["adicionar", "set"])
    @commands.has_permissions(manage_messages=True)
    async def eb_var_add(self, ctx, key: str, *, value: str):
        """Adiciona variável."""
        key = key.lower().strip()
        if not re.fullmatch(r"[a-z0-9_\-]{1,30}", key):
            return await ctx.send(embed=embed_error("Key inválida."))
        try:
            db = get_connection()
            db["embed_variables"].update_one(
                {"guild_id": ctx.guild.id, "key": key},
                {"$set": {"value": value[:2000], "updated_at": _now()},
                 "$setOnInsert": {"created_at": _now()}},
                upsert=True)
            await ctx.send(embed=embed_success(
                f"✅ `{{var.{key}}}` = `{value[:100]}`"))
        except Exception as e:
            await ctx.send(embed=embed_error(f"Erro: {e}"))

    @eb_var.command(name="remove", aliases=["del", "rm"])
    @commands.has_permissions(manage_messages=True)
    async def eb_var_remove(self, ctx, key: str):
        """Remove variável."""
        db = get_connection()
        r = db["embed_variables"].delete_one(
            {"guild_id": ctx.guild.id, "key": key.lower()})
        if r.deleted_count:
            await ctx.send(embed=embed_success(f"🗑️ `{key}` removida."))
        else:
            await ctx.send(embed=embed_error("Variável não encontrada."))

    @eb_var.command(name="listar", aliases=["list", "ls"])
    @commands.has_permissions(manage_messages=True)
    async def eb_var_list(self, ctx):
        """Lista variáveis."""
        vars_map = _get_custom_vars(ctx.guild.id)
        if not vars_map:
            return await ctx.send(embed=embed_info("Nenhuma variável."))
        text = "\n".join(f"• `{{var.{k}}}` = `{v[:80]}`"
                          for k, v in list(vars_map.items())[:25])
        await ctx.send(embed=embed_info(f"**{len(vars_map)} variáveis:**\n{text}"))


# ============================================================
# SETUP
# ============================================================
async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("EmbedBuilderCommands") is None:
        await bot.add_cog(EmbedBuilderCommands(bot))