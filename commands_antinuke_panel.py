# ============================================================
# COMMANDS_ANTINUKE_PANEL.PY - v1.7
# PAINEL INTERATIVO ANTI-RAID (3 páginas + Embed Builder)
# ============================================================
# 📌 O QUE MUDOU NA v1.7?
# - PARIDADE TOTAL com os comandos de texto: tudo que dá pra
#   configurar por `>antinuke X Y` agora dá pra configurar no painel.
# - ADICIONADO: botão "⚙️ Avançado" em cada proteção (separa
#   config simples das avançadas).
# - ADICIONADO: `limite_mencoes` e `limite_links` configuráveis
#   (mass_mention e url_spam).
# - ADICIONADO: `global_window_seconds` no Global.
# - ADICIONADO: `admin_bypass` no Global (evita bug do comando
#   que checa essa chave mas ela não existia no DEFAULT_CONFIG).
# - ADICIONADO: Anti-Escalation completo (11 regras + thresholds).
# - ADICIONADO: Rollback completo (max_items + window).
# - ADICIONADO: Canary completo (triggers + ban_author + ban_executor).
# - ADICIONADO: Bot Approval completo (auto_kick).
# - ADICIONADO: Whitelist completa (add/remove individual, check, modo).
# - ADICIONADO: Permissões com require_manager toggle.
#
# 📌 O QUE MANTÉM DA v1.6?
# - Sub-painel 🎨 Embed Builder
# - 3 páginas de paginação
# - Fix do bug do botão Histórico (naive timestamp)
# - Lockdown/Whitelist/Permissões/Histórico como botões dedicados
# - Thresholds duplos (normal + whitelisted)
# - Auto-refresh 30s
# - get_config_fresh (sempre lê do banco)
# ============================================================

# ─── BLOCO 1 — IMPORTS ───
# Cada import traz uma ferramenta específica pra dentro do arquivo.

# "asyncio" = rodar tarefas em paralelo (assíncrono).
import asyncio
# "datetime/timezone/timedelta" = trabalhar com datas e fusos.
from datetime import datetime, timezone, timedelta
# "typing" = documentação de tipos (não muda comportamento).
from typing import Dict, List, Optional

# "discord" = biblioteca principal do Discord.
import discord
# "commands" = criar comandos e cogs.
# "tasks" = tarefas em loop (background).
from discord.ext import commands, tasks
# "ui" = componentes interativos (botões, selects, modais).
# "ButtonStyle" = estilos de botão (cores).
# "SelectOption" = opções de um menu select.
from discord import ui, ButtonStyle, SelectOption

# "get_connection" = conexão com MongoDB.
from database import get_connection
# "embeds" = caixas coloridas prontas.
from embeds import embed_success, embed_error, embed_warning, embed_info


def _now() -> datetime:
    """Hora atual em UTC. Usado em todos os timestamps."""
    return datetime.now(timezone.utc)


def _normalize_timestamp(ts) -> Optional[datetime]:
    """
    Converte qualquer timestamp em datetime UTC "aware".

    📌 POR QUE ISSO EXISTE?
    O bug do botão Histórico era: quando o log tinha um timestamp
    "naive" (sem timezone), a comparação discord.utils.utcnow() - ts
    quebrava com TypeError. Essa função resolve isso normalizando
    tudo pra UTC com timezone.
    """
    # Se não tem timestamp, retorna None.
    if ts is None:
        return None
    # Se já é datetime:
    if isinstance(ts, datetime):
        # Se já tem timezone, converte pra UTC.
        if ts.tzinfo is not None:
            return ts.astimezone(timezone.utc)
        # Se não tem, assume que é UTC e adiciona.
        return ts.replace(tzinfo=timezone.utc)
    # Se é string ISO:
    if isinstance(ts, str):
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None
    # Se é número (timestamp unix):
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        except Exception:
            return None
    # Qualquer outra coisa retorna None.
    return None


# ============================================================
# BLOCO 2 — IMPORT DO ANTI-NUKE
# ============================================================
# Importamos as classes/constantes do commands_antinuke.py.
# Isso evita duplicar código e mantém tudo sincronizado.

try:
    # AntiNukeManager = gerencia config do anti-nuke no banco.
    # DEFAULT_CONFIG = config padrão do anti-nuke.
    # PROTECTION_MAP = mapa de proteções (label, desc, keys).
    # parse_color = converte string em cor.
    # color_to_hex = converte cor em hex.
    from commands_antinuke import (
        AntiNukeManager,
        DEFAULT_CONFIG,
        PROTECTION_MAP,
        parse_color,
        color_to_hex,
    )
except Exception:
    # Se falhar, define None/vazio pra não quebrar.
    AntiNukeManager = None
    DEFAULT_CONFIG = {}
    PROTECTION_MAP = {}

    def parse_color(raw):
        return None

    def color_to_hex(c):
        return f"#{c & 0xFFFFFF:06x}"


# Cor padrão do painel (dark_red).
DEFAULT_PANEL_COLOR = 0x26364F


# ============================================================
# BLOCO 3 — MAPA DE PROTEÇÕES DO PAINEL
# ============================================================
# 📌 O QUE É ISSO?
# Cada proteção do anti-nuke tem:
# - emoji + label + desc (visual)
# - keys: chaves booleanas no cfg (on/off)
# - threshold_key: chave do threshold normal
# - window_key: chave da janela em segundos
# - whitelist_threshold_key: chave do threshold whitelisted
# - hard_key: chave do limite "hard" (ex: limite de menções)
# - extra_keys: lista de (key, label, default) extras
#
# 📌 O QUE MUDOU NA v1.7?
# Adicionei "limite_mencoes" em mass_mention e "limite_links" em
# url_spam como hard_key. Antes esses dois não tinham config no
# painel (só nos comandos de texto).

PANEL_PROTECTIONS = {
    # ═══════════════════════════════════════════════════════════
    # CANAIS
    # ═══════════════════════════════════════════════════════════
    "channel_delete": {
        "emoji": "🗑️", "label": "Destruição de canais",
        "desc": "Impede deleção em massa de canais.",
        "keys": ["anti_channel_delete"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "channel_delete_threshold_whitelisted",
    },
    "channel_create": {
        "emoji": "📁", "label": "Criação de canais",
        "desc": "Impede criação em massa de canais.",
        "keys": ["anti_channel_create"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "channel_create_threshold_whitelisted",
    },
    "channel_rename": {
        "emoji": "✏️", "label": "Rename de canais",
        "desc": "Impede renomear canais em massa.",
        "keys": ["anti_channel_rename"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "channel_rename_threshold_whitelisted",
    },

    # ═══════════════════════════════════════════════════════════
    # CARGOS
    # ═══════════════════════════════════════════════════════════
    "role_delete": {
        "emoji": "👑", "label": "Nuke de cargos",
        "desc": "Impede deleção em massa de cargos.",
        "keys": ["anti_role_delete"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "role_delete_threshold_whitelisted",
    },
    "role_create": {
        "emoji": "➕", "label": "Criação de cargos",
        "desc": "Impede criação em massa de cargos.",
        "keys": ["anti_role_create"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "role_create_threshold_whitelisted",
    },
    "role_rename": {
        "emoji": "🏷️", "label": "Rename de cargos",
        "desc": "Impede renomear cargos em massa.",
        "keys": ["anti_role_rename"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "role_rename_threshold_whitelisted",
    },

    # ═══════════════════════════════════════════════════════════
    # MEMBROS
    # ═══════════════════════════════════════════════════════════
    "ban": {
        "emoji": "🔨", "label": "Ban em massa",
        "desc": "Impede banir membros em massa.",
        "keys": ["anti_ban"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "ban_threshold_whitelisted",
    },
    "kick": {
        "emoji": "👢", "label": "Kick em massa",
        "desc": "Impede expulsar membros em massa.",
        "keys": ["anti_kick"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "kick_threshold_whitelisted",
    },
    "member_update": {
        "emoji": "👤", "label": "Anti member update",
        "desc": "Impede alterar membros em massa.",
        "keys": ["anti_member_update"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "member_update_threshold_whitelisted",
    },
    "mass_disconnect": {
        "emoji": "🔇", "label": "Anti mass disconnect",
        "desc": "Impede desconectar membros em massa.",
        "keys": ["anti_mass_disconnect"],
        "threshold_key": "threshold",
        "window_key": "window_seconds",
        "whitelist_threshold_key": "mass_disconnect_threshold_whitelisted",
    },

    # ═══════════════════════════════════════════════════════════
    # BOTS / WEBHOOKS
    # ═══════════════════════════════════════════════════════════
    "bot_add": {
        "emoji": "🤖", "label": "Bloqueio de bots",
        "desc": "Kicka bots não autorizados ao entrarem.",
        "keys": ["anti_bot_add"],
    },
    "webhook": {
        "emoji": "🪝", "label": "Anti-webhook",
        "desc": "Impede criar/deletar webhooks em massa.",
        "keys": ["anti_webhook"],
    },

    # ═══════════════════════════════════════════════════════════
    # OUTROS RECURSOS
    # ═══════════════════════════════════════════════════════════
    "emoji_delete": {
        "emoji": "😀", "label": "Anti emoji delete",
        "desc": "Impede deletar emojis em massa.",
        "keys": ["anti_emoji_delete"],
    },
    "sticker_delete": {
        "emoji": "🖼️", "label": "Anti sticker delete",
        "desc": "Impede deletar stickers em massa.",
        "keys": ["anti_sticker_delete"],
    },
    "thread_delete": {
        "emoji": "🧵", "label": "Anti thread delete",
        "desc": "Impede deletar threads em massa.",
        "keys": ["anti_thread_delete"],
    },
    "integration": {
        "emoji": "🔗", "label": "Anti integration",
        "desc": "Impede adicionar/remover integrações.",
        "keys": ["anti_integration"],
    },
    "guild_update": {
        "emoji": "🖥️", "label": "Anti edit server",
        "desc": "Impede alterações suspeitas no servidor.",
        "keys": ["anti_guild_update"],
    },
    "message_nuke": {
        "emoji": "💬", "label": "Anti message nuke",
        "desc": "Impede deletar mensagens em massa.",
        "keys": ["anti_message_nuke"],
    },

    # ═══════════════════════════════════════════════════════════
    # ANTI-SPAM
    # ═══════════════════════════════════════════════════════════
    "invite_spam": {
        "emoji": "📨", "label": "Anti invite",
        "desc": "Bloqueia convites do Discord.",
        "keys": ["anti_invite_spam"],
    },
    # 🆕 v1.7: url_spam agora tem limite_links configurável no painel.
    "url_spam": {
        "emoji": "🌐", "label": "Anti link spam",
        "desc": "Bloqueia spam de links.",
        "keys": ["anti_url_spam"],
        # hard_key = chave no cfg que define o limite.
        "hard_key": "limite_links",
        # hard_default = valor padrão se não configurado.
        "hard_default": 3,
        # hard_label = label exibido no modal.
        "hard_label": "Máximo de links por mensagem",
    },
    # 🆕 v1.7: mass_mention agora tem limite_mencoes configurável.
    "mass_mention": {
        "emoji": "📢", "label": "Flood de menções",
        "desc": "Bloqueia muitas menções numa mensagem.",
        "keys": ["anti_mass_mention"],
        "hard_key": "limite_mencoes",
        "hard_default": 5,
        "hard_label": "Máximo de menções por mensagem",
    },
    "everyone_mention": {
        "emoji": "🚨", "label": "Anti @everyone",
        "desc": "Bloqueia menção a @everyone.",
        "keys": ["anti_everyone_mention"],
    },
    "msg_flood": {
        "emoji": "🌊", "label": "Anti flood",
        "desc": "Bloqueia spam de mensagens.",
        "keys": ["anti_msg_flood"],
        "hard_key": "hard_msg_flood_limit",
        "hard_default": 10,
        "hard_label": "Máximo de mensagens por 5s",
    },
    "emoji_spam": {
        "emoji": "😀", "label": "Anti emoji spam",
        "desc": "Bloqueia spam de emojis.",
        "keys": ["anti_emoji_spam"],
        "hard_key": "hard_emoji_limit",
        "hard_default": 10,
        "hard_label": "Máximo de emojis por mensagem",
    },
    "custom_emoji_spam": {
        "emoji": "🎨", "label": "Anti custom emoji",
        "desc": "Bloqueia spam de emojis customizados.",
        "keys": ["anti_custom_emoji_spam"],
        "hard_key": "hard_custom_emoji_limit",
        "hard_default": 8,
        "hard_label": "Máximo de custom emojis",
    },
    "special_chars": {
        "emoji": "✳️", "label": "Anti special chars",
        "desc": "Bloqueia muitos caracteres especiais.",
        "keys": ["anti_special_chars"],
        "hard_key": "hard_special_chars_percent",
        "hard_default": 50,
        "hard_label": "Porcentagem máxima (%)",
    },
    "caps_lock": {
        "emoji": "🔠", "label": "Anti caps lock",
        "desc": "Bloqueia CAPS LOCK excessivo.",
        "keys": ["anti_caps_lock"],
        "hard_key": "hard_caps_percent",
        "hard_default": 80,
        "hard_label": "Porcentagem de CAPS (%)",
    },
    "zalgo": {
        "emoji": "👹", "label": "Anti zalgo",
        "desc": "Bloqueia caracteres zalgo.",
        "keys": ["anti_zalgo"],
        "hard_key": "hard_zalgo_limit",
        "hard_default": 5,
        "hard_label": "Máximo de diacríticos",
    },
    "invisible_chars": {
        "emoji": "👻", "label": "Anti invisíveis",
        "desc": "Bloqueia caracteres invisíveis.",
        "keys": ["anti_invisible_chars"],
    },
    "repeated_chars": {
        "emoji": "🔁", "label": "Anti repetidos",
        "desc": "Bloqueia caracteres repetidos.",
        "keys": ["anti_repeated_chars"],
        "hard_key": "hard_repeated_chars",
        "hard_default": 15,
        "hard_label": "Máximo de repetições seguidas",
    },

    # ═══════════════════════════════════════════════════════════
    # ANTI-RAID
    # ═══════════════════════════════════════════════════════════
    "anti_raid": {
        "emoji": "🚪", "label": "Join flood",
        "desc": "Detecta entradas em massa (raid).",
        "keys": ["anti_raid"],
        "threshold_key": "raid_join_threshold",
        "window_key": "raid_join_window",
        "whitelist_threshold_key": "raid_join_threshold_whitelisted",
        # extra_keys = campos adicionais específicos dessa proteção.
        # Cada item é (chave_no_cfg, label_exibido, valor_padrão).
        "extra_keys": [
            ("min_account_age_days", "Idade mínima de conta (dias)", 7),
        ],
    },
    "require_avatar": {
        "emoji": "🖼️", "label": "Exigir avatar",
        "desc": "Kicka membros sem avatar ao entrar.",
        "keys": ["require_avatar"],
    },
    "raid_lockdown": {
        "emoji": "🔒", "label": "Lockdown automático",
        "desc": "Ativa lockdown ao detectar raid.",
        "keys": ["raid_lockdown"],
    },
    "user_protection": {
        "emoji": "👤", "label": "Proteção contra users",
        "desc": "Monitora ações de usuários (não só bots).",
        "keys": ["user_protection_enabled"],
    },
}


# ============================================================
# BLOCO 4 — PAGINAÇÃO
# ============================================================
# ALL_ITEMS = lista de tudo que aparece no select da página 1.
# Cada item é OU um dict (sub-painel) OU uma string (proteção).

ALL_ITEMS = [
    # ─── Sub-painéis especiais (dicts) ───
    {"type": "subpanel", "key": "__global__",
     "emoji": "⚙️", "label": "Configurações Globais",
     "desc": "Punição, thresholds, cooldown, etc."},
    {"type": "subpanel", "key": "__escalation__",
     "emoji": "🚨", "label": "Anti-Escalation",
     "desc": "Configurar sistema anti-escalation"},
    {"type": "subpanel", "key": "__rollback__",
     "emoji": "♻️", "label": "Rollback",
     "desc": "Config completa de rollback"},
    {"type": "subpanel", "key": "__canary__",
     "emoji": "🐤", "label": "Canary Nuke",
     "desc": "Config do canary nuke"},
    {"type": "subpanel", "key": "__botapproval__",
     "emoji": "🤖", "label": "Bot Approval",
     "desc": "Config de aprovação de bots"},

    # ─── v13.5: sub-painéis das features novas ───
    {"type": "subpanel", "key": "__antiiplogger__",
     "emoji": "🚫", "label": "Anti IP Logger",
     "desc": "Bloqueia links de IP logger (Grabify, etc)"},
    {"type": "subpanel", "key": "__antievade__",
     "emoji": "🔄", "label": "Anti Evadir Mute",
     "desc": "Persiste timeouts e reaplica ao voltar"},
    {"type": "subpanel", "key": "__joinrisk__",
     "emoji": "📊", "label": "Join Risk Score",
     "desc": "Score de risco (0-100) em novos membros"},
    {"type": "subpanel", "key": "__statsgraph__",
     "emoji": "📈", "label": "Stats Graph",
     "desc": "Gráficos de estatísticas com Pillow"},

    # ─── v1.6: Embed Builder ───
    {"type": "subpanel", "key": "__embedbuilder__",
     "emoji": "🎨", "label": "Embed Builder",
     "desc": "Painéis interativos (botões, selects, templates)"},

    # ─── Proteções (string = chave em PANEL_PROTECTIONS) ───
    "channel_delete", "channel_create", "channel_rename",
    "role_delete", "role_create", "role_rename",
    "ban", "kick", "member_update", "mass_disconnect",
    "bot_add",
    "webhook", "emoji_delete", "sticker_delete", "thread_delete",
    "integration", "guild_update", "message_nuke",
    "invite_spam", "url_spam", "mass_mention", "everyone_mention",
    "msg_flood", "emoji_spam", "custom_emoji_spam",
    "special_chars", "caps_lock", "zalgo",
    "invisible_chars", "repeated_chars",
    "anti_raid", "require_avatar", "raid_lockdown",
    "user_protection",
]

# Quantos itens por página.
PAGE_SIZE = 25


def _get_total_pages() -> int:
    """Calcula quantas páginas tem no total."""
    # Fórmula: (total + tamanho - 1) // tamanho = divisão arredondada pra cima.
    total = len(ALL_ITEMS)
    return (total + PAGE_SIZE - 1) // PAGE_SIZE


def _get_page_items(page: int) -> list:
    """Retorna os itens da página N (0-indexed)."""
    start = page * PAGE_SIZE
    end = start + PAGE_SIZE
    return ALL_ITEMS[start:end]


# ============================================================
# BLOCO 5 — HELPERS DE UI E CONFIG
# ============================================================

def _status_text(enabled: bool) -> str:
    """Devolve '🟢 ATIVADO' ou '🔴 DESATIVADO'."""
    return "🟢 ATIVADO" if enabled else "🔴 DESATIVADO"


def _get_panel_cfg(guild_id: int) -> dict:
    """
    Pega a config do anti-nuke (SEMPRE fresca, direto do banco).
    Usado no painel pra mostrar valores atualizados.
    """
    if AntiNukeManager:
        return AntiNukeManager.get_config_fresh(guild_id)
    # Fallback se o import falhou.
    db = get_connection()
    return {**DEFAULT_CONFIG, **(db["antinuke_config"].find_one(
        {"guild_id": guild_id}) or {})}


def _update_panel_cfg(guild_id: int, key: str, value):
    """
    Persiste uma chave de config no Mongo e invalida o cache da guild.
    Mesmo caminho que os comandos usavam (AntiNukeManager.update).
    """
    if AntiNukeManager:
        AntiNukeManager.update(guild_id, key, value)


async def _panel_interaction_allowed(interaction: discord.Interaction) -> bool:
    """Aplica a mesma política de managers em todos os controles do painel."""
    if interaction.guild is None or interaction.user is None:
        return False
    member = interaction.guild.get_member(interaction.user.id) or interaction.user
    if interaction.guild.owner_id == interaction.user.id:
        return True
    try:
        cfg = _get_panel_cfg(interaction.guild.id)
    except Exception:
        cfg = None
    allowed = False
    if cfg is not None:
        try:
            managers = {int(x) for x in cfg.get("antinuke_managers_users", []) if x is not None}
            role_ids = {int(x) for x in cfg.get("antinuke_managers_roles", []) if x is not None}
            allowed = interaction.user.id in managers or any(
                r.id in role_ids for r in getattr(member, "roles", []))
            if (not allowed and not cfg.get("antinuke_require_manager", True)
                    and getattr(getattr(member, "guild_permissions", None), "administrator", False)):
                allowed = True
        except Exception:
            allowed = False
    if allowed:
        return True
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message(
                embed=embed_error(
                    "❌ Sem permissão para o painel Anti-Nuke.\n"
                    "É necessário ser owner, manager configurado ou administrador "
                    "quando a exigência de manager estiver desativada."),
                ephemeral=True)
    except Exception:
        pass
    return False


def _is_protection_on(cfg: dict, prot_key: str) -> bool:
    """Diz se uma proteção está ativada (checando todas as keys)."""
    prot = PANEL_PROTECTIONS.get(prot_key)
    if not prot:
        return False
    keys = prot["keys"]
    # Se tem 1 chave só, retorna o valor dela.
    if len(keys) == 1:
        return bool(cfg.get(keys[0], True))
    # Se tem múltiplas, retorna True só se TODAS estão True.
    return all(cfg.get(k, True) for k in keys)


# ─── Cor do painel ───

def _get_panel_color(guild_id: int) -> discord.Color:
    """Lê a cor custom do painel do banco."""
    try:
        db = get_connection()
        doc = db["antinuke_panel_config"].find_one(
            {"guild_id": guild_id}) or {}
        color_int = doc.get("color")
        if color_int is not None:
            return discord.Color(int(color_int))
    except Exception:
        pass
    return discord.Color(DEFAULT_PANEL_COLOR)


def _set_panel_color(guild_id: int, color: Optional[int]):
    """Define (ou reseta) a cor do painel."""
    db = get_connection()
    if color is None:
        # $unset = remove o campo (volta pro padrão).
        db["antinuke_panel_config"].update_one(
            {"guild_id": guild_id}, {"$unset": {"color": ""}}, upsert=True)
    else:
        db["antinuke_panel_config"].update_one(
            {"guild_id": guild_id}, {"$set": {"color": color}}, upsert=True)


# ─── Canal restrito ───

def _panel_channel_check(guild_id: int, channel_id: int) -> bool:
    """Diz se o painel pode ser aberto nesse canal."""
    db = get_connection()
    doc = db["antinuke_panel_config"].find_one({"guild_id": guild_id}) or {}
    restricted = doc.get("restricted_channel")
    # Se não tem restrição, permite em qualquer canal.
    if not restricted:
        return True
    return int(restricted) == channel_id


def _get_restricted_channel(guild_id: int) -> Optional[int]:
    """Lê o canal restrito do banco."""
    db = get_connection()
    doc = db["antinuke_panel_config"].find_one({"guild_id": guild_id}) or {}
    return doc.get("restricted_channel")


def _set_restricted_channel(guild_id: int, channel_id: Optional[int]):
    """Define (ou remove) o canal restrito."""
    db = get_connection()
    if channel_id is None:
        db["antinuke_panel_config"].update_one(
            {"guild_id": guild_id}, {"$unset": {"restricted_channel": ""}},
            upsert=True)
    else:
        db["antinuke_panel_config"].update_one(
            {"guild_id": guild_id},
            {"$set": {"restricted_channel": channel_id}}, upsert=True)


# ─── Auto-refresh ───

# Registro de painéis ativos (pra task de auto-refresh saber quais editar).
# Estrutura: {(channel_id, message_id): guild_id}
_ACTIVE_PANELS: Dict[tuple, int] = {}

# Cache da página atual de cada painel.
# Estrutura: {message_id: page_number}
_PANEL_PAGES: Dict[int, int] = {}


def _register_panel(guild_id: int, channel_id: int, message_id: int):
    """Registra um painel pra auto-refresh e persiste no Mongo
    (sobrevive a restart do bot).
    """
    _ACTIVE_PANELS[(channel_id, message_id)] = guild_id
    try:
        db = get_connection()
        db["antinuke_active_panels"].update_one(
            {"channel_id": channel_id, "message_id": message_id},
            {"$set": {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "message_id": message_id,
                "updated_at": _now(),
            }},
            upsert=True)
    except Exception:
        pass


def _unregister_panel(channel_id: int, message_id: int):
    """Remove um painel do auto-refresh e do Mongo."""
    _ACTIVE_PANELS.pop((channel_id, message_id), None)
    try:
        db = get_connection()
        db["antinuke_active_panels"].delete_one(
            {"channel_id": channel_id, "message_id": message_id})
    except Exception:
        pass


# ============================================================
# BLOCO 6 — EMBED DO PAINEL PRINCIPAL
# ============================================================

def _build_main_embed(cfg: dict, guild: discord.Guild,
                       page: int = 0) -> discord.Embed:
    """Monta o embed do painel principal."""
    total_pages = _get_total_pages()

    # Descrição com instruções.
    desc = (
        "Escolha uma proteção no menu para consultar ou alterar suas opções.\n"
        "Acesso rápido: **Lockdown** · **Whitelist** · **Permissões** · **Histórico**"
    )
    if total_pages > 1:
        desc += f"\nPágina **{page + 1} de {total_pages}** · navegue pelos botões abaixo"

    # Cria o embed base.
    embed = discord.Embed(
        title="Anti-Raid | Painel de proteção",
        description=desc,
        color=_get_panel_color(guild.id))

    # Thumbnail = ícone do servidor.
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    # Monta linhas de texto (uma por item da página).
    lines = []
    page_items = _get_page_items(page)

    for item in page_items:
        # Sub-painel (dict).
        if isinstance(item, dict):
            emoji = item.get("emoji", "•")
            label = item.get("label", "?")
            lines.append(f"{emoji} **{label}** · Ativo")
        else:
            # Proteção normal (string).
            prot = PANEL_PROTECTIONS.get(item)
            if not prot:
                continue
            enabled = _is_protection_on(cfg, item)
            status = _status_text(enabled)
            emoji = prot.get("emoji", "•")
            label = prot["label"]
            lines.append(f"{emoji} **{label}** · {status}")

    # Agrupa em blocos (Discord limita a 1024 chars por field).
    blocks: List[str] = []
    current = ""
    for line in lines:
        # Se adicionar essa linha passar de 950 chars, começa novo bloco.
        if len(current) + len(line) + 2 > 950:
            blocks.append(current.strip())
            current = line + "\n\n"
        else:
            current += line + "\n\n"
    if current.strip():
        blocks.append(current.strip())

    # Adiciona até 6 blocos (limite do Discord é 25 fields).
    for i, block in enumerate(blocks[:6]):
        embed.add_field(
            name="\u200b" if i > 0 else "Proteções",
            value=block, inline=True)

    # Footer.
    embed.set_footer(
        text=f"Stumble Hit AntiRaid  •  Anti-Raid  •  {page + 1}/{total_pages}")
    return embed


# ============================================================
# BLOCO 7 — SELECT DO PAINEL PRINCIPAL
# ============================================================

class MainProtectionSelect(ui.Select):
    """
    Select do painel principal (página N).

    custom_id OBRIGATÓRIO: sem ele o menu morre após restart do bot
    (View timeout=None sozinha não basta — cada componente precisa de id).
    Formato: antinuke_main_sel_{guild_id}_{page} (único por guild/página).
    """

    def __init__(self, guild_id: int, page: int = 0):
        self.guild_id = guild_id
        self.page = page
        cfg = _get_panel_cfg(guild_id)
        page_items = _get_page_items(page)

        options: List[SelectOption] = []

        # Monta uma opção por item da página.
        for item in page_items:
            # Sub-painel.
            if isinstance(item, dict):
                options.append(SelectOption(
                    label=f"{item.get('emoji', '•')} {item.get('label', '?')}",
                    value=item["key"],
                    description=item.get("desc", "")[:80],
                ))
            else:
                # Proteção.
                prot = PANEL_PROTECTIONS.get(item)
                if not prot:
                    continue
                enabled = _is_protection_on(cfg, item)
                status_emoji = "🟢" if enabled else "🔴"
                options.append(SelectOption(
                    label=f"{prot['emoji']} {prot['label']}",
                    value=item,
                    description=f"{prot['desc'][:80]} [{status_emoji}]",
                ))

        # custom_id: torna o select persistente após restart.
        super().__init__(
            placeholder=f"🛡️ Página {page + 1}: selecione um item...",
            options=options[:25], min_values=1, max_values=1,
            custom_id=f"antinuke_main_sel_{guild_id}_{page}")

    async def callback(self, interaction: discord.Interaction):
        """Chamado quando o user seleciona uma opção."""
        choice = self.values[0]

        # ─── Sub-painéis (cada um tem sua View própria) ───
        if choice == "__global__":
            view = GlobalConfigView(self.guild_id)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)
        if choice == "__escalation__":
            view = EscalationView(self.guild_id)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)
        if choice == "__rollback__":
            view = RollbackView(self.guild_id)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)
        if choice == "__canary__":
            view = CanaryView(self.guild_id)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)
        if choice == "__botapproval__":
            view = BotApprovalView(self.guild_id)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)
        if choice == "__antiiplogger__":
            view = AntiIPLoggerPanelView(self.guild_id)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)
        if choice == "__antievade__":
            # Carrega a configuração antes de montar a interface síncrona.
            from commands_anti_evade import AntiEvadeManager
            await AntiEvadeManager.get_config(self.guild_id)
            view = AntiEvadePanelView(self.guild_id)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)
        if choice == "__joinrisk__":
            view = JoinRiskPanelView(self.guild_id)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)
        if choice == "__statsgraph__":
            view = StatsGraphPanelView(self.guild_id)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)
        if choice == "__embedbuilder__":
            view = EmbedBuilderPanelView(self.guild_id)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)

        # ─── Proteção normal → sub-view com botão Avançado ───
        if choice in PANEL_PROTECTIONS:
            view = ProtectionSubView(self.guild_id, choice)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)


# ============================================================
# BLOCO 8 — BOTÕES DE NAVEGAÇÃO
# ============================================================

class PrevPageButton(ui.Button):
    """Botão de voltar página."""

    def __init__(self, guild_id: int, current_page: int):
        super().__init__(
            label="◀️ Anterior", style=ButtonStyle.primary,
            custom_id=f"antinuke_prev_{guild_id}", row=1)
        self.guild_id = guild_id
        self.current_page = current_page

    async def callback(self, interaction: discord.Interaction):
        # max(0, atual - 1) garante que não vai pra negativo.
        new_page = max(0, self.current_page - 1)
        _PANEL_PAGES[interaction.message.id] = new_page
        cfg = _get_panel_cfg(self.guild_id)
        view = AntiRaidPanelView(self.guild_id, page=new_page)
        embed = _build_main_embed(cfg, interaction.guild, page=new_page)
        await interaction.response.edit_message(embed=embed, view=view)


class NextPageButton(ui.Button):
    """Botão de avançar página."""

    def __init__(self, guild_id: int, current_page: int):
        super().__init__(
            label="▶️ Próximo", style=ButtonStyle.primary,
            custom_id=f"antinuke_next_{guild_id}", row=1)
        self.guild_id = guild_id
        self.current_page = current_page

    async def callback(self, interaction: discord.Interaction):
        total = _get_total_pages()
        # min(total - 1, atual + 1) garante que não passa do limite.
        new_page = min(total - 1, self.current_page + 1)
        _PANEL_PAGES[interaction.message.id] = new_page
        cfg = _get_panel_cfg(self.guild_id)
        view = AntiRaidPanelView(self.guild_id, page=new_page)
        embed = _build_main_embed(cfg, interaction.guild, page=new_page)
        await interaction.response.edit_message(embed=embed, view=view)


# ============================================================
# BLOCO 9 — SUB-MENU DE PROTEÇÃO (com botão Avançado)
# ============================================================
# 📌 O QUE MUDOU NA v1.7?
# Agora cada proteção tem botões separados:
# - Configurações básicas (threshold, janela, limite)
# - Configurações avançadas (whitelist threshold, extras)
# Isso deixa mais profissional e organizado.

class ProtectionActionSelect(ui.Select):
    """Select de ações básicas da proteção."""

    def __init__(self, guild_id: int, prot_key: str, advanced: bool = False):
        self.guild_id = guild_id
        self.prot_key = prot_key
        self.advanced = advanced
        cfg = _get_panel_cfg(guild_id)
        enabled = _is_protection_on(cfg, prot_key)
        prot = PANEL_PROTECTIONS[prot_key]

        options = []

        # ─── Se NÃO é avançado: mostra toggle e configs básicas ───
        if not advanced:
            # Botão de ligar/desligar.
            if enabled:
                options.append(SelectOption(
                    label="❌ Desativar proteção", value="toggle_off",
                    description="Desliga essa proteção"))
            else:
                options.append(SelectOption(
                    label="✅ Ativar proteção", value="toggle_on",
                    description="Liga essa proteção"))

            # Threshold normal.
            if "threshold_key" in prot:
                tk = prot["threshold_key"]
                valor = cfg.get(tk, 3)
                options.append(SelectOption(
                    label="🎚️ Configurar threshold normal",
                    value="config_threshold",
                    description=f"Atual: {valor} ações"))

            # Janela de tempo.
            if "window_key" in prot:
                wnk = prot["window_key"]
                valor = cfg.get(wnk, 15)
                options.append(SelectOption(
                    label="⏱️ Configurar janela",
                    value="config_window",
                    description=f"Atual: {valor}s"))

            # Hard limit (ex: limite de menções).
            if "hard_key" in prot:
                hk = prot["hard_key"]
                valor = cfg.get(hk, prot.get("hard_default", 0))
                options.append(SelectOption(
                    label=f"⚙️ {prot.get('hard_label', 'Limite')}",
                    value="config_hard",
                    description=f"Atual: {valor}"))

        # ─── Se É avançado: mostra configs mais técnicas ───
        else:
            # Threshold whitelisted.
            if "whitelist_threshold_key" in prot:
                wk = prot["whitelist_threshold_key"]
                normal = cfg.get(prot.get("threshold_key", "threshold"), 3)
                valor = cfg.get(wk, normal + 1)
                options.append(SelectOption(
                    label="🎚️ Threshold whitelisted",
                    value="config_threshold_wl",
                    description=f"Atual: {valor} (mín: {normal + 1})"))

            # Extras (ex: min_account_age_days).
            if "extra_keys" in prot:
                for idx, (key, label, default) in enumerate(prot["extra_keys"]):
                    valor = cfg.get(key, default)
                    options.append(SelectOption(
                        label=f"⚙️ {label[:50]}",
                        value=f"config_extra_{idx}",
                        description=f"Atual: {valor}"))

            # Reset sempre disponível.
            options.append(SelectOption(
                label="🔄 Resetar para padrão", value="reset",
                description="Volta ao valor padrão"))

        # Se não tem opção nenhuma, adiciona uma dummy.
        if not options:
            options.append(SelectOption(
                label="Nada pra configurar aqui", value="none"))

        # custom_id único: guild + proteção + básico/avançado.
        # Sem isso o sub-painel quebra após restart.
        adv = "adv" if advanced else "bas"
        super().__init__(
            placeholder=("⚙️ Avançado..." if advanced
                         else "⚙️ Selecione uma ação..."),
            options=options[:25], min_values=1, max_values=1,
            custom_id=f"antinuke_prot_{guild_id}_{prot_key}_{adv}"[:100])

    async def callback(self, interaction: discord.Interaction):
        """Processa a ação selecionada."""
        action = self.values[0]
        prot = PANEL_PROTECTIONS[self.prot_key]

        # ─── Toggle OFF ───
        if action == "toggle_off":
            for k in prot["keys"]:
                _update_panel_cfg(self.guild_id, k, False)
            view = ProtectionSubView(self.guild_id, self.prot_key)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)

        # ─── Toggle ON ───
        if action == "toggle_on":
            for k in prot["keys"]:
                _update_panel_cfg(self.guild_id, k, True)
            view = ProtectionSubView(self.guild_id, self.prot_key)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)

        # ─── Abrir modais específicos ───
        if action == "config_threshold":
            return await interaction.response.send_modal(
                ThresholdModal(self.guild_id, self.prot_key, "normal"))
        if action == "config_threshold_wl":
            return await interaction.response.send_modal(
                ThresholdModal(self.guild_id, self.prot_key, "whitelisted"))
        if action == "config_window":
            return await interaction.response.send_modal(
                WindowModal(self.guild_id, self.prot_key))
        if action == "config_hard":
            return await interaction.response.send_modal(
                HardLimitModal(self.guild_id, self.prot_key))

        # Extras (min_account_age_days, etc).
        if action.startswith("config_extra_"):
            idx = int(action.replace("config_extra_", ""))
            extras = prot.get("extra_keys", [])
            if idx < len(extras):
                key, label, default = extras[idx]
                return await interaction.response.send_modal(
                    ExtraKeyModal(self.guild_id, self.prot_key,
                                   key, label, default))

        # ─── Reset ───
        if action == "reset":
            # Volta todas as keys pro padrão.
            for k in prot["keys"]:
                _update_panel_cfg(self.guild_id, k,
                                    DEFAULT_CONFIG.get(k, True))
            # Threshold normal.
            if "threshold_key" in prot:
                tk = prot["threshold_key"]
                _update_panel_cfg(self.guild_id, tk,
                                    DEFAULT_CONFIG.get(tk, 3))
            # Threshold whitelisted.
            if "whitelist_threshold_key" in prot:
                wk = prot["whitelist_threshold_key"]
                _update_panel_cfg(self.guild_id, wk,
                                    DEFAULT_CONFIG.get(wk, 4))
            # Janela.
            if "window_key" in prot:
                wnk = prot["window_key"]
                _update_panel_cfg(self.guild_id, wnk,
                                    DEFAULT_CONFIG.get(wnk, 15))
            # Hard limit.
            if "hard_key" in prot:
                _update_panel_cfg(self.guild_id, prot["hard_key"],
                                    DEFAULT_CONFIG.get(
                                        prot["hard_key"],
                                        prot["hard_default"]))
            # Extras.
            if "extra_keys" in prot:
                for key, label, default in prot["extra_keys"]:
                    _update_panel_cfg(self.guild_id, key,
                                        DEFAULT_CONFIG.get(key, default))

            view = ProtectionSubView(self.guild_id, self.prot_key)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)

        # Fallback: não faz nada.
        try:
            await interaction.response.defer()
        except Exception:
            pass


class ProtectionSubView(ui.View):
    """
    View de uma proteção específica.

    📌 v1.7: tem 2 selects (básico + avançado) e botão Voltar.
    """
    def __init__(self, guild_id: int, prot_key: str):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.prot_key = prot_key

        # Select de configs básicas (row 0) — com custom_id.
        self.add_item(ProtectionActionSelect(guild_id, prot_key,
                                                advanced=False))

        # Select de configs avançadas (row 1, SÓ se tiver algo).
        prot = PANEL_PROTECTIONS.get(prot_key, {})
        has_advanced = (
            "whitelist_threshold_key" in prot
            or "extra_keys" in prot
        )
        if has_advanced:
            self.add_item(ProtectionActionSelect(guild_id, prot_key,
                                                    advanced=True))

        # Botão de voltar (row 2).
        btn_back = ui.Button(
            label="⬅️ Voltar", style=ButtonStyle.secondary,
            custom_id=f"antinuke_back_{guild_id}", row=2)
        btn_back.callback = self._back
        self.add_item(btn_back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _panel_interaction_allowed(interaction)

    def build_embed(self) -> discord.Embed:
        """Monta o embed com o estado atual da proteção."""
        cfg = _get_panel_cfg(self.guild_id)
        prot = PANEL_PROTECTIONS.get(self.prot_key)
        if not prot:
            return embed_error("Proteção não encontrada.")

        enabled = _is_protection_on(cfg, self.prot_key)
        # Verde se ativo, vermelho se desativado.
        color = (discord.Color.green() if enabled
                 else discord.Color.red())

        embed = discord.Embed(
            title=f"{prot['emoji']} {prot['label']}",
            description=prot["desc"],
            color=color)

        # Estado atual.
        embed.add_field(name="Status",
                         value=_status_text(enabled), inline=True)

        # Threshold normal.
        if "threshold_key" in prot:
            tk = prot["threshold_key"]
            valor = cfg.get(tk, 3)
            embed.add_field(name="Limite normal",
                             value=f"`{valor}` ações", inline=True)

        # Threshold whitelisted.
        if "whitelist_threshold_key" in prot:
            wk = prot["whitelist_threshold_key"]
            normal = cfg.get(prot.get("threshold_key", "threshold"), 3)
            valor = cfg.get(wk, normal + 1)
            embed.add_field(name="Limite da whitelist",
                             value=f"`{valor}` ações (mín: {normal + 1})",
                             inline=True)

        # Janela.
        if "window_key" in prot:
            wnk = prot["window_key"]
            valor = cfg.get(wnk, 15)
            embed.add_field(name="Janela",
                             value=f"`{valor}s`", inline=True)

        # Hard limit.
        if "hard_key" in prot:
            hk = prot["hard_key"]
            valor = cfg.get(hk, prot.get("hard_default", 0))
            embed.add_field(name=f"⚙️ {prot.get('hard_label', 'Limite')}",
                             value=f"`{valor}`", inline=False)

        # Extras.
        if "extra_keys" in prot:
            for key, label, default in prot["extra_keys"]:
                valor = cfg.get(key, default)
                embed.add_field(name=f"⚙️ {label[:50]}",
                                 value=f"`{valor}`", inline=True)

        embed.set_footer(text="Stumble Hit AntiRaid  •  Anti-Raid")
        return embed

    async def _back(self, interaction: discord.Interaction):
        """Volta pro painel principal (página 0)."""
        _PANEL_PAGES[interaction.message.id] = 0
        view = AntiRaidPanelView(self.guild_id, page=0)
        cfg = _get_panel_cfg(self.guild_id)
        embed = _build_main_embed(cfg, interaction.guild, page=0)
        await interaction.response.edit_message(embed=embed, view=view)


# ============================================================
# BLOCO 10 — MODAIS DE PROTEÇÃO
# ============================================================

class ThresholdModal(ui.Modal):
    """Modal pra configurar threshold normal ou whitelisted."""

    def __init__(self, guild_id: int, prot_key: str, mode: str):
        prot = PANEL_PROTECTIONS[prot_key]
        self.guild_id = guild_id
        self.prot_key = prot_key
        self.mode = mode

        cfg = _get_panel_cfg(guild_id)
        normal_key = prot.get("threshold_key", "threshold")
        wl_key = prot.get("whitelist_threshold_key")
        self.normal_val = int(cfg.get(normal_key, 3))

        # Título e label conforme o modo.
        if mode == "normal":
            titulo = f"🎚️ Threshold Normal — {prot['label']}"[:45]
            valor_atual = self.normal_val
            label = "Threshold normal (ações)"
        else:
            titulo = f"🎚️ Threshold Whitelisted — {prot['label']}"[:45]
            valor_atual = (cfg.get(wl_key, self.normal_val + 1)
                           if wl_key else self.normal_val + 1)
            label = f"Threshold whitelisted (mín: {self.normal_val + 1})"

        super().__init__(title=titulo)

        # Campo de texto.
        self.input = ui.TextInput(
            label=label[:45],
            placeholder=f"Atual: {valor_atual}",
            default=str(valor_atual),
            required=True, max_length=6)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        """Chamado quando o user submete o modal."""
        prot = PANEL_PROTECTIONS[self.prot_key]
        raw = (self.input.value or "").strip()

        # Tenta converter pra inteiro.
        try:
            valor = int(raw)
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ Valor inválido."), ephemeral=True)

        # Valida faixa.
        if valor < 1 or valor > 100000:
            return await interaction.response.send_message(
                embed=embed_error("❌ Use entre 1 e 100000."),
                ephemeral=True)

        # Validação extra pro whitelisted (deve ser ≥ normal + 1).
        if self.mode == "whitelisted" and valor < self.normal_val + 1:
            return await interaction.response.send_message(
                embed=embed_error(
                    f"❌ Valor inválido. O threshold whitelisted deve ser "
                    f"**≥ {self.normal_val + 1}**."), ephemeral=True)

        # Decide qual chave salvar.
        if self.mode == "normal":
            key = prot.get("threshold_key", "threshold")
        else:
            key = prot.get("whitelist_threshold_key") or "threshold_whitelisted"

        _update_panel_cfg(self.guild_id, key, valor)
        view = ProtectionSubView(self.guild_id, self.prot_key)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class WindowModal(ui.Modal):
    """Modal pra configurar a janela de tempo (em segundos)."""

    def __init__(self, guild_id: int, prot_key: str):
        prot = PANEL_PROTECTIONS[prot_key]
        super().__init__(title=f"⏱️ Janela — {prot['label']}"[:45])
        self.guild_id = guild_id
        self.prot_key = prot_key

        cfg = _get_panel_cfg(guild_id)
        wnk = prot.get("window_key", "window_seconds")
        valor_atual = cfg.get(wnk, 15)

        self.input = ui.TextInput(
            label="Janela em segundos",
            placeholder=f"Atual: {valor_atual}s",
            default=str(valor_atual),
            required=True, max_length=5)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        prot = PANEL_PROTECTIONS[self.prot_key]
        raw = (self.input.value or "").strip()

        try:
            valor = int(raw)
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ Valor inválido."), ephemeral=True)

        # Janela entre 1 e 3600 segundos.
        if valor < 1 or valor > 3600:
            return await interaction.response.send_message(
                embed=embed_error("❌ Use entre 1 e 3600 segundos."),
                ephemeral=True)

        wnk = prot.get("window_key", "window_seconds")
        _update_panel_cfg(self.guild_id, wnk, valor)
        view = ProtectionSubView(self.guild_id, self.prot_key)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class HardLimitModal(ui.Modal):
    """Modal pra configurar hard limit (ex: limite de menções)."""

    def __init__(self, guild_id: int, prot_key: str):
        prot = PANEL_PROTECTIONS[prot_key]
        super().__init__(title=f"⚙️ {prot['label']}"[:45])
        self.guild_id = guild_id
        self.prot_key = prot_key

        cfg = _get_panel_cfg(guild_id)
        valor_atual = cfg.get(prot["hard_key"],
                                prot.get("hard_default", 0))

        self.input = ui.TextInput(
            label=prot.get("hard_label", "Valor")[:45],
            placeholder=f"Padrão: {prot.get('hard_default', 0)}",
            default=str(valor_atual),
            required=True, max_length=6)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        prot = PANEL_PROTECTIONS[self.prot_key]
        raw = (self.input.value or "").strip()

        try:
            valor = int(raw)
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ Valor inválido."), ephemeral=True)

        # Valida faixa.
        if valor < 1 or valor > 100000:
            return await interaction.response.send_message(
                embed=embed_error("❌ Use entre 1 e 100000."),
                ephemeral=True)

        _update_panel_cfg(self.guild_id, prot["hard_key"], valor)
        view = ProtectionSubView(self.guild_id, self.prot_key)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class ExtraKeyModal(ui.Modal):
    """Modal pra configs extras (ex: min_account_age_days)."""

    def __init__(self, guild_id: int, prot_key: str,
                 key: str, label: str, default):
        super().__init__(title=f"⚙️ {label[:40]}")
        self.guild_id = guild_id
        self.prot_key = prot_key
        self.key = key
        self.default = default

        cfg = _get_panel_cfg(guild_id)
        valor_atual = cfg.get(key, default)

        self.input = ui.TextInput(
            label=label[:45],
            placeholder=f"Padrão: {default}",
            default=str(valor_atual),
            required=True, max_length=6)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = (self.input.value or "").strip()

        try:
            valor = int(raw)
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ Valor inválido."), ephemeral=True)

        # Dias entre 0 e 365.
        if valor < 0 or valor > 365:
            return await interaction.response.send_message(
                embed=embed_error("❌ Use entre 0 e 365."), ephemeral=True)

        _update_panel_cfg(self.guild_id, self.key, valor)
        view = ProtectionSubView(self.guild_id, self.prot_key)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


# ============================================================
# BLOCO 11 — MODAL DE COR DO PAINEL
# ============================================================

class PanelColorModal(ui.Modal):
    """Modal pra configurar a cor do painel."""

    def __init__(self, guild_id: int):
        super().__init__(title="🎨 Cor do Painel")
        self.guild_id = guild_id

        db = get_connection()
        doc = db["antinuke_panel_config"].find_one(
            {"guild_id": guild_id}) or {}
        cor_atual = doc.get("color")
        default_str = color_to_hex(int(cor_atual)) if cor_atual else "#8B0000"

        self.input = ui.TextInput(
            label="Cor (hex, nome ou rgb)",
            placeholder="#ff0000 | red | rgb(255,0,0) | 16711680",
            default=default_str, required=True, max_length=50,
            style=discord.TextStyle.short)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = (self.input.value or "").strip()

        # Se digitou "reset", volta pro padrão.
        if raw.lower() in ("reset", "padrao", "default"):
            _set_panel_color(self.guild_id, None)
            view = GlobalConfigView(self.guild_id)
            await interaction.response.edit_message(
                embed=view.build_embed(), view=view)
            await interaction.followup.send(
                "✅ Cor resetada para o padrão (dark_red).", ephemeral=True)
            return

        # Tenta converter a cor.
        cor = parse_color(raw)
        if cor is None:
            return await interaction.response.send_message(
                embed=embed_error(
                    "❌ Cor inválida. Use: `#ff0000`, `red`, "
                    "`rgb(255,0,0)`, `0xff0000` ou decimal."),
                ephemeral=True)

        _set_panel_color(self.guild_id, cor)
        view = GlobalConfigView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)
        await interaction.followup.send(
            f"✅ Cor do painel definida: `{color_to_hex(cor)}`",
            ephemeral=True)


# ============================================================
# BLOCO 12 — SUB-PAINEL: CONFIGURAÇÕES GLOBAIS
# ============================================================
# 📌 v1.7: adicionado global_window_seconds e admin_bypass.

class GlobalActionSelect(ui.Select):
    """Select com todas as configs globais do anti-nuke (custom_id persistente)."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        cfg = _get_panel_cfg(guild_id)

        # Monta as opções (com valor atual em cada).
        options = [
            SelectOption(label="🎯 Punição", value="punishment",
                          description=f"Atual: {cfg.get('punishment', 'strip')}"),
            SelectOption(label="🎚️ Threshold (global)", value="threshold",
                          description=f"Atual: {cfg.get('threshold', 3)} ações"),
            SelectOption(label="🎚️ Threshold whitelisted (global)",
                          value="threshold_wl",
                          description=f"Atual: {cfg.get('threshold_whitelisted', 4)}"),
            SelectOption(label="⏱️ Janela (global)", value="window",
                          description=f"Atual: {cfg.get('window_seconds', 15)}s"),
            SelectOption(label="🌐 Global threshold", value="global_threshold",
                          description=f"Atual: {cfg.get('global_threshold', 6)}"),
            # 🆕 v1.7: janela global.
            SelectOption(label="🌐 Global window (segundos)",
                          value="globalwindow",
                          description=f"Atual: {cfg.get('global_window_seconds', 30)}s"),
            SelectOption(label="🕒 Cooldown punição", value="cooldown",
                          description=f"Atual: {cfg.get('punish_cooldown', 60)}s"),
            SelectOption(label="🔔 Notificar owner", value="notify",
                          description=f"Atual: {'ON' if cfg.get('notify_owner') else 'OFF'}"),
            SelectOption(label="📅 Idade mínima de conta", value="minage",
                          description=f"Atual: {cfg.get('min_account_age_days', 7)} dias"),
            SelectOption(label="🚪 Ação em raid", value="raid_action",
                          description=f"Atual: {cfg.get('raid_action', 'kick')}"),
            SelectOption(label="👤 Proteção contra users", value="user_prot",
                          description=f"Atual: {'ON' if cfg.get('user_protection_enabled') else 'OFF'}"),
            SelectOption(label="🎯 Modo Whitelist", value="whitelist_mode",
                          description=f"Atual: {cfg.get('whitelist_mode', 'confirm_kick')}"),
            # 🆕 v1.7: admin_bypass.
            SelectOption(label="👑 Admin bypass", value="admin_bypass",
                          description=f"Atual: {'ON' if cfg.get('admin_bypass', True) else 'OFF'}"),
            SelectOption(label="🎨 Cor do painel", value="panel_color",
                          description="Mudar a cor do painel (hex, nome, rgb)"),
            SelectOption(label="♻️ Rollback: máx itens", value="rollbackmax",
                          description=f"Atual: {cfg.get('rollback_max_items', 50)}"),
            SelectOption(label="♻️ Rollback: janela", value="rollbackwindow",
                          description=f"Atual: {cfg.get('rollback_window_minutes', 5)}min"),
        ]

        super().__init__(
            placeholder="⚙️ Selecione uma configuração...",
            options=options[:25], min_values=1, max_values=1,
            custom_id=f"antinuke_global_sel_{guild_id}")

    async def callback(self, interaction: discord.Interaction):
        """Processa a config selecionada."""
        action = self.values[0]

        # ─── Modais de valores numéricos ───
        if action in ("threshold", "window", "global_threshold",
                      "cooldown", "minage", "threshold_wl",
                      "rollbackmax", "rollbackwindow", "globalwindow"):
            return await interaction.response.send_modal(
                GlobalConfigModal(self.guild_id, action))

        # ─── Selects especiais ───
        if action == "punishment":
            return await interaction.response.edit_message(
                embed=_build_punishment_embed(self.guild_id),
                view=PunishmentSelectView(self.guild_id))
        if action == "raid_action":
            return await interaction.response.edit_message(
                embed=_build_raid_action_embed(self.guild_id),
                view=RaidActionSelectView(self.guild_id))
        if action == "whitelist_mode":
            return await interaction.response.edit_message(
                embed=_build_wl_mode_embed(self.guild_id),
                view=WhitelistModeSelectView(self.guild_id))

        # ─── Modal de cor ───
        if action == "panel_color":
            return await interaction.response.send_modal(
                PanelColorModal(self.guild_id))

        # ─── Toggle simples (notify, user_prot, admin_bypass) ───
        if action == "notify":
            cfg = _get_panel_cfg(self.guild_id)
            new_val = not bool(cfg.get("notify_owner", True))
            _update_panel_cfg(self.guild_id, "notify_owner", new_val)
            view = GlobalConfigView(self.guild_id)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)

        if action == "user_prot":
            cfg = _get_panel_cfg(self.guild_id)
            new_val = not bool(cfg.get("user_protection_enabled", True))
            _update_panel_cfg(self.guild_id, "user_protection_enabled", new_val)
            if AntiNukeManager:
                AntiNukeManager.invalidate(self.guild_id)
            view = GlobalConfigView(self.guild_id)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)

        # 🆕 v1.7: admin_bypass (default True = staff não é punido).
        if action == "admin_bypass":
            cfg = _get_panel_cfg(self.guild_id)
            new_val = not bool(cfg.get("admin_bypass", True))
            _update_panel_cfg(self.guild_id, "admin_bypass", new_val)
            if AntiNukeManager:
                AntiNukeManager.invalidate(self.guild_id)
            view = GlobalConfigView(self.guild_id)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)


class GlobalConfigView(ui.View):
    """View do sub-painel de configs globais (user_protection, admin_bypass, etc)."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        # Select de ações (row 0).
        self.add_item(GlobalActionSelect(guild_id))

        # Botão voltar (row 1).
        btn_back = ui.Button(
            label="⬅️ Voltar", style=ButtonStyle.secondary,
            custom_id=f"antinuke_gback_{guild_id}", row=1)
        btn_back.callback = self._back
        self.add_item(btn_back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _panel_interaction_allowed(interaction)

    def build_embed(self) -> discord.Embed:
        """Monta o embed com as configs atuais."""
        cfg = _get_panel_cfg(self.guild_id)
        db = get_connection()
        doc = db["antinuke_panel_config"].find_one(
            {"guild_id": self.guild_id}) or {}
        cor_atual = doc.get("color")
        cor_str = color_to_hex(int(cor_atual)) if cor_atual else "dark_red (padrão)"

        embed = discord.Embed(
            title="⚙️ Configurações Globais",
            description="Parâmetros gerais do anti-raid.",
            color=_get_panel_color(self.guild_id),
            timestamp=_now())

        embed.add_field(name="🎯 Punição",
                         value=f"`{cfg.get('punishment', 'strip')}`", inline=True)
        embed.add_field(name="🎚️ Threshold",
                         value=f"`{cfg.get('threshold', 3)}`", inline=True)
        embed.add_field(name="🎚️ Threshold WL",
                         value=f"`{cfg.get('threshold_whitelisted', 4)}`", inline=True)
        embed.add_field(name="Janela",
                         value=f"`{cfg.get('window_seconds', 15)}s`", inline=True)
        embed.add_field(name="🌐 Global",
                         value=f"`{cfg.get('global_threshold', 6)}`", inline=True)
        # 🆕 v1.7.
        embed.add_field(name="🌐 Global window",
                         value=f"`{cfg.get('global_window_seconds', 30)}s`", inline=True)
        embed.add_field(name="🕒 Cooldown",
                         value=f"`{cfg.get('punish_cooldown', 60)}s`", inline=True)
        embed.add_field(name="🔔 Notificar",
                         value=_status_text(bool(cfg.get("notify_owner", True))),
                         inline=True)
        embed.add_field(name="📅 Min Age",
                         value=f"`{cfg.get('min_account_age_days', 7)}` dias",
                         inline=True)
        embed.add_field(name="🚪 Raid Action",
                         value=f"`{cfg.get('raid_action', 'kick')}`", inline=True)
        embed.add_field(name="👤 User Protect",
                         value=_status_text(bool(cfg.get("user_protection_enabled", True))),
                         inline=True)
        embed.add_field(name="🎯 Modo WL",
                         value=f"`{cfg.get('whitelist_mode', 'confirm_kick')}`",
                         inline=True)
        # 🆕 v1.7.
        embed.add_field(name="👑 Admin bypass",
                         value=_status_text(bool(cfg.get("admin_bypass", True))),
                         inline=True)
        embed.add_field(name="♻️ Rollback máx",
                         value=f"`{cfg.get('rollback_max_items', 50)}`",
                         inline=True)
        embed.add_field(name="♻️ Rollback janela",
                         value=f"`{cfg.get('rollback_window_minutes', 5)}min`",
                         inline=True)
        embed.add_field(name="🎨 Cor do painel",
                         value=f"`{cor_str}`", inline=True)

        embed.set_footer(text="Stumble Hit AntiRaid  •  Anti-Raid")
        return embed

    async def _back(self, interaction: discord.Interaction):
        """Volta pro painel principal."""
        _PANEL_PAGES[interaction.message.id] = 0
        view = AntiRaidPanelView(self.guild_id, page=0)
        cfg = _get_panel_cfg(self.guild_id)
        embed = _build_main_embed(cfg, interaction.guild, page=0)
        await interaction.response.edit_message(embed=embed, view=view)


# ─── Helpers de embeds ───

def _build_punishment_embed(guild_id: int) -> discord.Embed:
    """Embed de escolha de punição."""
    cfg = _get_panel_cfg(guild_id)
    atual = cfg.get("punishment", "strip")
    return discord.Embed(
        title="🎯 Escolha a Punição",
        description=f"**Atual:** `{atual}`\n\nEscolha abaixo:",
        color=_get_panel_color(guild_id),
        timestamp=_now())


def _build_raid_action_embed(guild_id: int) -> discord.Embed:
    """Embed de escolha de ação em raid."""
    cfg = _get_panel_cfg(guild_id)
    atual = cfg.get("raid_action", "kick")
    return discord.Embed(
        title="🚪 Ação em Raid",
        description=f"**Atual:** `{atual}`\n\nEscolha abaixo:",
        color=_get_panel_color(guild_id),
        timestamp=_now())


def _build_wl_mode_embed(guild_id: int) -> discord.Embed:
    """Embed de escolha do modo whitelist."""
    cfg = _get_panel_cfg(guild_id)
    atual = cfg.get("whitelist_mode", "confirm_kick")
    return discord.Embed(
        title="🎯 Modo da Whitelist",
        description=(f"**Atual:** `{atual}`\n\n"
                     "**Pode tudo** — whitelisted ignora TUDO.\n"
                     "**Confirma expulsão** — só expulsa com confirmação DM."),
        color=_get_panel_color(guild_id),
        timestamp=_now())


class PunishmentSelectView(ui.View):
    """View de escolha de punição."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        atual = _get_panel_cfg(guild_id).get("punishment", "strip")

        # Select com as 4 opções (custom_id para persistência).
        select = ui.Select(
            placeholder="🎯 Escolha a punição...",
            options=[
                SelectOption(label="Strip (remover cargos)",
                             value="strip", default=(atual == "strip")),
                SelectOption(label="Ban (banir)",
                             value="ban", default=(atual == "ban")),
                SelectOption(label="Kick (expulsar)",
                             value="kick", default=(atual == "kick")),
                SelectOption(label="Lockdown",
                             value="lockdown", default=(atual == "lockdown")),
            ],
            min_values=1, max_values=1,
            custom_id=f"antinuke_pun_sel_{guild_id}")
        select.callback = self._on_select
        self.add_item(select)

        # Botão voltar.
        btn = ui.Button(label="⬅️ Voltar", style=ButtonStyle.secondary,
                        custom_id=f"antinuke_punback_{guild_id}", row=1)
        btn.callback = self._back
        self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Impede que membros sem permissão alterem configurações do painel."""
        return await _panel_interaction_allowed(interaction)

    async def _on_select(self, interaction: discord.Interaction):
        """Salva a punição escolhida."""
        value = interaction.data["values"][0]
        _update_panel_cfg(self.guild_id, "punishment", value)
        view = GlobalConfigView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)

    async def _back(self, interaction: discord.Interaction):
        view = GlobalConfigView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class RaidActionSelectView(ui.View):
    """View de escolha de ação em raid."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        atual = _get_panel_cfg(guild_id).get("raid_action", "kick")

        select = ui.Select(
            placeholder="🚪 Escolha a ação...",
            options=[
                SelectOption(label="Kick", value="kick", default=(atual == "kick")),
                SelectOption(label="Ban", value="ban", default=(atual == "ban")),
                SelectOption(label="Verify", value="verify", default=(atual == "verify")),
            ],
            min_values=1, max_values=1,
            custom_id=f"antinuke_raid_sel_{guild_id}")
        select.callback = self._on_select
        self.add_item(select)

        btn = ui.Button(label="⬅️ Voltar", style=ButtonStyle.secondary,
                        custom_id=f"antinuke_raidback_{guild_id}", row=1)
        btn.callback = self._back
        self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Impede que membros sem permissão alterem configurações do painel."""
        return await _panel_interaction_allowed(interaction)

    async def _on_select(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]
        _update_panel_cfg(self.guild_id, "raid_action", value)
        view = GlobalConfigView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)

    async def _back(self, interaction: discord.Interaction):
        view = GlobalConfigView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class WhitelistModeSelectView(ui.View):
    """View de escolha do modo whitelist."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        atual = _get_panel_cfg(guild_id).get("whitelist_mode", "confirm_kick")

        select = ui.Select(
            placeholder="🎯 Escolha o modo...",
            options=[
                SelectOption(label="Pode tudo", value="can_do_everything",
                             description="Whitelisted ignora TODAS as proteções",
                             default=(atual == "can_do_everything")),
                SelectOption(label="Confirma expulsão", value="confirm_kick",
                             description="Whitelisted só expulsa com confirmação DM",
                             default=(atual == "confirm_kick")),
            ],
            min_values=1, max_values=1,
            custom_id=f"antinuke_wlm_sel_{guild_id}")
        select.callback = self._on_select
        self.add_item(select)

        btn = ui.Button(label="⬅️ Voltar", style=ButtonStyle.secondary,
                        custom_id=f"antinuke_wlmback_{guild_id}", row=1)
        btn.callback = self._back
        self.add_item(btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Impede que membros sem permissão alterem configurações do painel."""
        return await _panel_interaction_allowed(interaction)

    async def _on_select(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]
        _update_panel_cfg(self.guild_id, "whitelist_mode", value)
        view = GlobalConfigView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)

    async def _back(self, interaction: discord.Interaction):
        view = GlobalConfigView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class GlobalConfigModal(ui.Modal):
    """
    Modal pra editar valores numéricos das configs globais.

    📌 v1.7: adicionado globalwindow.
    """
    # Mapeamento: action → (chave_no_cfg, título, label, min, max).
    FIELDS = {
        "threshold": ("threshold", "🎚️ Threshold",
                       "Ações necessárias", 1, 50),
        "threshold_wl": ("threshold_whitelisted",
                          "🎚️ Threshold WL",
                          "Threshold whitelisted", 2, 100),
        "window": ("window_seconds", "⏱️ Janela",
                    "Segundos", 5, 300),
        "global_threshold": ("global_threshold", "🌐 Global",
                              "Ações globais", 1, 100),
        # 🆕 v1.7.
        "globalwindow": ("global_window_seconds", "🌐 Global Window",
                          "Segundos", 5, 600),
        "cooldown": ("punish_cooldown", "🕒 Cooldown",
                      "Segundos", 10, 3600),
        "minage": ("min_account_age_days", "📅 Idade Mínima",
                    "Dias", 0, 365),
        "rollbackmax": ("rollback_max_items", "♻️ Rollback Máx",
                         "Itens", 1, 500),
        "rollbackwindow": ("rollback_window_minutes", "♻️ Rollback Janela",
                            "Minutos", 1, 1440),
    }

    def __init__(self, guild_id: int, action: str):
        key, titulo, label, self.min_val, self.max_val = self.FIELDS[action]
        super().__init__(title=titulo)
        self.guild_id = guild_id
        self.action = action
        self.key = key

        cfg = _get_panel_cfg(guild_id)
        valor_atual = cfg.get(key, 0)

        self.input = ui.TextInput(
            label=label[:45],
            default=str(valor_atual),
            required=True, max_length=10)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        raw = (self.input.value or "").strip()

        try:
            valor = int(raw)
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ Valor inválido."), ephemeral=True)

        # Validação especial pro threshold_wl.
        if self.action == "threshold_wl":
            cfg = _get_panel_cfg(self.guild_id)
            normal = int(cfg.get("threshold", 3))
            if valor < normal + 1:
                return await interaction.response.send_message(
                    embed=embed_error(f"❌ Deve ser ≥ {normal + 1} (normal + 1)."),
                    ephemeral=True)

        # Valida faixa.
        if valor < self.min_val or valor > self.max_val:
            return await interaction.response.send_message(
                embed=embed_error(f"❌ Use entre {self.min_val} e {self.max_val}."),
                ephemeral=True)

        _update_panel_cfg(self.guild_id, self.key, valor)
        view = GlobalConfigView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


# ============================================================
# BLOCO 13 — SUB-PAINEL: ANTI-ESCALATION (COMPLETO v1.7)
# ============================================================
# 📌 v1.7: adicionado select com TODAS as regras + thresholds +
#          protectrole + whitelist + modo painel.

# Mapa das 11 regras (chave → label legível).
_ESCALATION_RULES = {
    "block_self_promotion": "Auto-promoção",
    "block_privileged_role_grant": "Dar cargo perigoso",
    "block_role_creation_dangerous": "Criar cargo perigoso",
    "block_role_edit_escalation": "Editar cargo elevando",
    "block_everyone_edit": "Editar @everyone",
    "block_position_abuse": "Mover cargo acima",
    "block_manage_roles_delegation": "Delegar manage_roles",
    "block_bot_bypass": "Bot burlando",
    "block_cascade": "Cascata",
    "block_bot_admin_role": "Bot dando admin",
    "block_admin_grant_to_bot": "Admin pra bot",
}


class EscalationActionSelect(ui.Select):
    """Select de ações do anti-escalation."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        # Lê a config do anti-escalation.
        db = get_connection()
        doc = db["antiescalation_config"].find_one(
            {"guild_id": guild_id}) or {}

        options = [
            # Toggles gerais.
            SelectOption(label="🔄 Ativar/Desativar sistema",
                          value="toggle_system",
                          description=f"Atual: {'ON' if doc.get('enabled', True) else 'OFF'}"),
            SelectOption(label="🎯 Ciclar punição",
                          value="cycle_punishment",
                          description=f"Atual: {doc.get('punishment', 'strip')}"),
            SelectOption(label="↩️ Toggle auto-revert",
                          value="toggle_autorevert",
                          description=f"Atual: {'ON' if doc.get('auto_revert', True) else 'OFF'}"),
            # Thresholds.
            SelectOption(label="⚙️ Configurar cascade threshold",
                          value="config_cascade",
                          description=f"Atual: {doc.get('cascade_threshold', 3)}"),
            SelectOption(label="⚙️ Configurar mass grant threshold",
                          value="config_massgrant",
                          description=f"Atual: {doc.get('mass_grant_threshold', 5)}"),
            # Cargos protegidos.
            SelectOption(label="🛡️ Adicionar cargo protegido",
                          value="add_protected_role",
                          description="Modal com ID do cargo"),
            SelectOption(label="🗑️ Remover cargo protegido",
                          value="remove_protected_role",
                          description="Remove cargo da lista de protegidos"),
            # Whitelist.
            SelectOption(label="📋 Ver whitelist",
                          value="show_whitelist",
                          description="Lista users/cargos ignorados"),
        ]

        # Adiciona toggle pra cada uma das 11 regras.
        for key, label in _ESCALATION_RULES.items():
            status = "🟢" if doc.get(key, True) else "🔴"
            options.append(SelectOption(
                label=f"{status} {label[:70]}",
                value=f"toggle_rule_{key}",
                description=f"Clique para alternar"
            ))

        super().__init__(
            placeholder="🚨 Selecione uma ação...",
            options=options[:25], min_values=1, max_values=1,
            custom_id=f"antinuke_esc_sel_{guild_id}")

    async def callback(self, interaction: discord.Interaction):
        action = self.values[0]
        db = get_connection()

        # ─── Toggle do sistema ───
        if action == "toggle_system":
            doc = db["antiescalation_config"].find_one(
                {"guild_id": self.guild_id}) or {}
            new_val = not doc.get("enabled", True)
            db["antiescalation_config"].update_one(
                {"guild_id": self.guild_id},
                {"$set": {"enabled": new_val}}, upsert=True)
            _invalidate_escalation_cache(self.guild_id)
            return await interaction.response.edit_message(
                embed=self.view.build_embed(), view=self.view)

        # ─── Ciclar punição ───
        if action == "cycle_punishment":
            doc = db["antiescalation_config"].find_one(
                {"guild_id": self.guild_id}) or {}
            atual = doc.get("punishment", "strip")
            ordem = ["revert", "strip", "kick", "ban"]
            idx = ordem.index(atual) if atual in ordem else 0
            novo = ordem[(idx + 1) % len(ordem)]
            db["antiescalation_config"].update_one(
                {"guild_id": self.guild_id},
                {"$set": {"punishment": novo}}, upsert=True)
            _invalidate_escalation_cache(self.guild_id)
            return await interaction.response.edit_message(
                embed=self.view.build_embed(), view=self.view)

        # ─── Toggle auto-revert ───
        if action == "toggle_autorevert":
            doc = db["antiescalation_config"].find_one(
                {"guild_id": self.guild_id}) or {}
            new_val = not doc.get("auto_revert", True)
            db["antiescalation_config"].update_one(
                {"guild_id": self.guild_id},
                {"$set": {"auto_revert": new_val}}, upsert=True)
            _invalidate_escalation_cache(self.guild_id)
            return await interaction.response.edit_message(
                embed=self.view.build_embed(), view=self.view)

        # ─── Toggle de regra individual ───
        if action.startswith("toggle_rule_"):
            rule_key = action.replace("toggle_rule_", "")
            doc = db["antiescalation_config"].find_one(
                {"guild_id": self.guild_id}) or {}
            new_val = not doc.get(rule_key, True)
            db["antiescalation_config"].update_one(
                {"guild_id": self.guild_id},
                {"$set": {rule_key: new_val}}, upsert=True)
            _invalidate_escalation_cache(self.guild_id)
            return await interaction.response.edit_message(
                embed=self.view.build_embed(), view=self.view)

        # ─── Configs numéricas ───
        if action == "config_cascade":
            return await interaction.response.send_modal(
                EscalationNumberModal(self.guild_id, "cascade_threshold",
                                       "⚙️ Cascade Threshold",
                                       "Mínimo de elos", 1, 20))
        if action == "config_massgrant":
            return await interaction.response.send_modal(
                EscalationNumberModal(self.guild_id, "mass_grant_threshold",
                                       "⚙️ Mass Grant Threshold",
                                       "Máximo de grants", 1, 50))

        # ─── Adicionar cargo protegido ───
        if action == "add_protected_role":
            return await interaction.response.send_modal(
                EscalationRoleModal(self.guild_id, "add"))

        # ─── Remover cargo protegido ───
        if action == "remove_protected_role":
            return await interaction.response.send_modal(
                EscalationRoleModal(self.guild_id, "remove"))

        # ─── Ver whitelist ───
        if action == "show_whitelist":
            doc = db["antiescalation_config"].find_one(
                {"guild_id": self.guild_id}) or {}
            users = doc.get("whitelist_users", [])
            roles = doc.get("whitelist_roles", [])
            prot_roles = doc.get("protected_roles", [])

            text = "**📋 Whitelist:**\n"
            text += "\n**👤 Users:**\n" + (
                "\n".join(f"• <@{u}>" for u in users[:15])
                or "*nenhum*")
            text += "\n\n**👑 Cargos:**\n" + (
                "\n".join(f"• <@&{r}>" for r in roles[:15])
                or "*nenhum*")
            text += "\n\n**🛡️ Cargos Protegidos:**\n" + (
                "\n".join(f"• <@&{r}>" for r in prot_roles[:15])
                or "*nenhum*")

            return await interaction.response.send_message(
                embed=embed_info(text[:4000]), ephemeral=True)


def _invalidate_escalation_cache(guild_id: int):
    """Limpa o cache do anti-escalation."""
    try:
        from commands_antiescalation import AntiEscalationManager
        AntiEscalationManager.invalidate(guild_id)
    except Exception:
        pass


class EscalationNumberModal(ui.Modal):
    """Modal pra configurar números do anti-escalation."""

    def __init__(self, guild_id: int, key: str, title: str,
                 label: str, min_val: int, max_val: int):
        super().__init__(title=title[:45])
        self.guild_id = guild_id
        self.key = key
        self.min_val = min_val
        self.max_val = max_val

        db = get_connection()
        doc = db["antiescalation_config"].find_one(
            {"guild_id": guild_id}) or {}
        valor_atual = doc.get(key, 0)

        self.input = ui.TextInput(
            label=label[:45], default=str(valor_atual),
            required=True, max_length=5)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            valor = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ Valor inválido."), ephemeral=True)

        if valor < self.min_val or valor > self.max_val:
            return await interaction.response.send_message(
                embed=embed_error(
                    f"❌ Use entre {self.min_val} e {self.max_val}."),
                ephemeral=True)

        db = get_connection()
        db["antiescalation_config"].update_one(
            {"guild_id": self.guild_id},
            {"$set": {self.key: valor}}, upsert=True)
        _invalidate_escalation_cache(self.guild_id)

        view = EscalationView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class EscalationRoleModal(ui.Modal):
    """Modal pra add/remove cargo protegido."""

    def __init__(self, guild_id: int, mode: str):
        title = ("🛡️ Adicionar Cargo Protegido" if mode == "add"
                 else "🗑️ Remover Cargo Protegido")
        super().__init__(title=title[:45])
        self.guild_id = guild_id
        self.mode = mode

        self.input = ui.TextInput(
            label="ID do cargo",
            placeholder="Ex: 123456789012345678",
            required=True, max_length=25)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rid = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ ID inválido."), ephemeral=True)

        db = get_connection()
        doc = db["antiescalation_config"].find_one(
            {"guild_id": self.guild_id}) or {}
        protected = list(doc.get("protected_roles", []))

        if self.mode == "add":
            if rid not in protected:
                protected.append(rid)
            msg = f"✅ <@&{rid}> adicionado aos protegidos."
        else:
            protected = [r for r in protected if r != rid]
            msg = f"✅ <@&{rid}> removido dos protegidos."

        db["antiescalation_config"].update_one(
            {"guild_id": self.guild_id},
            {"$set": {"protected_roles": protected}}, upsert=True)
        _invalidate_escalation_cache(self.guild_id)

        view = EscalationView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)
        await interaction.followup.send(msg, ephemeral=True)


class EscalationView(ui.View):
    """View do sub-painel de anti-escalation (v1.7 completo)."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

        # Select principal (row 0).
        self.add_item(EscalationActionSelect(guild_id))

        # Botão voltar (row 1).
        btn_back = ui.Button(
            label="⬅️ Voltar", style=ButtonStyle.secondary,
            custom_id=f"antinuke_esc_back_{guild_id}", row=1)
        btn_back.callback = self._back
        self.add_item(btn_back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _panel_interaction_allowed(interaction)

    def build_embed(self) -> discord.Embed:
        db = get_connection()
        doc = db["antiescalation_config"].find_one(
            {"guild_id": self.guild_id}) or {}
        enabled = doc.get("enabled", True)
        punishment = doc.get("punishment", "strip")
        auto_revert = doc.get("auto_revert", True)

        color = (discord.Color.green() if enabled
                 else discord.Color.red())

        embed = discord.Embed(
            title="🚨 Anti-Escalation",
            description=("Proteção contra escalada de privilégios.\n"
                         "Detecta dar cargos perigosos, editar cargos "
                         "elevando permissões, cascatas, etc."),
            color=color)

        embed.add_field(name="📊 Status",
                         value=_status_text(enabled), inline=True)
        embed.add_field(name="🎯 Punição",
                         value=f"`{punishment}`", inline=True)
        embed.add_field(name="↩️ Auto-Revert",
                         value=_status_text(auto_revert), inline=True)

        # Lista das 11 regras.
        linhas = []
        for key, label in _ESCALATION_RULES.items():
            on = doc.get(key, True)
            linhas.append(f"{'🟢' if on else '🔴'} {label}")

        embed.add_field(name=f"📋 Regras ({len(_ESCALATION_RULES)})",
                         value="\n".join(linhas), inline=False)

        # Thresholds.
        cascade = doc.get("cascade_threshold", 3)
        massgrant = doc.get("mass_grant_threshold", 5)
        embed.add_field(name="⚙️ Thresholds",
                         value=(f"Cascade: `{cascade}`\n"
                                f"Mass grant: `{massgrant}`"),
                         inline=True)

        # Cargos protegidos.
        prot_roles = doc.get("protected_roles", [])
        embed.add_field(name="🛡️ Cargos protegidos",
                         value=f"`{len(prot_roles)}`",
                         inline=True)

        embed.set_footer(text="Stumble Hit AntiRaid  •  Anti-Raid")
        return embed

    async def _back(self, interaction: discord.Interaction):
        _PANEL_PAGES[interaction.message.id] = 0
        view = AntiRaidPanelView(self.guild_id, page=0)
        cfg = _get_panel_cfg(self.guild_id)
        embed = _build_main_embed(cfg, interaction.guild, page=0)
        await interaction.response.edit_message(embed=embed, view=view)


# ============================================================
# BLOCO 14 — SUB-PAINEL: ROLLBACK (COMPLETO v1.7)
# ============================================================

class RollbackActionSelect(ui.Select):
    """Select de ações do rollback."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        cfg = _get_panel_cfg(guild_id)

        options = [
            SelectOption(label="🔄 Ativar/Desativar",
                          value="toggle",
                          description=f"Atual: {'ON' if cfg.get('rollback_enabled', True) else 'OFF'}"),
            SelectOption(label="♻️ Máximo de itens",
                          value="config_max",
                          description=f"Atual: {cfg.get('rollback_max_items', 50)}"),
            SelectOption(label="⏱️ Janela (minutos)",
                          value="config_window",
                          description=f"Atual: {cfg.get('rollback_window_minutes', 5)}min"),
            SelectOption(label="⚡ Forçar rollback agora",
                          value="force",
                          description="Restaura deletados agora"),
            SelectOption(label="🗑️ Limpar cache de deletados",
                          value="clear_cache",
                          description="Remove cache sem restaurar"),
        ]

        super().__init__(
            placeholder="♻️ Selecione uma ação...",
            options=options[:25], min_values=1, max_values=1,
            custom_id=f"antinuke_rb_sel_{guild_id}")

    async def callback(self, interaction: discord.Interaction):
        action = self.values[0]

        # ─── Toggle ───
        if action == "toggle":
            cfg = _get_panel_cfg(self.guild_id)
            new_val = not bool(cfg.get("rollback_enabled", True))
            _update_panel_cfg(self.guild_id, "rollback_enabled", new_val)
            return await interaction.response.edit_message(
                embed=self.view.build_embed(), view=self.view)

        # ─── Configs ───
        if action == "config_max":
            return await interaction.response.send_modal(
                RollbackNumberModal(self.guild_id, "rollback_max_items",
                                     "♻️ Máx de itens", "Itens", 1, 500))
        if action == "config_window":
            return await interaction.response.send_modal(
                RollbackNumberModal(self.guild_id, "rollback_window_minutes",
                                     "⏱️ Janela", "Minutos", 1, 1440))

        # ─── Forçar rollback ───
        if action == "force":
            await interaction.response.defer(ephemeral=True)
            cog = interaction.client.get_cog("AntiNukeCommands")
            if not cog:
                return await interaction.followup.send(
                    embed=embed_error("❌ Cog AntiNuke não encontrado."),
                    ephemeral=True)
            cfg = _get_panel_cfg(self.guild_id)
            reverted = await cog._rollback_recent(
                interaction.guild, cfg,
                window_minutes=int(cfg.get("rollback_window_minutes", 5)))
            if not reverted:
                return await interaction.followup.send(
                    embed=embed_info("📋 Nada para restaurar."),
                    ephemeral=True)
            await interaction.followup.send(
                embed=embed_success(f"✅ {len(reverted)} itens restaurados."),
                ephemeral=True)
            return

        # ─── Limpar cache ───
        if action == "clear_cache":
            db = get_connection()
            db["deleted_channels_cache"].delete_many(
                {"guild_id": self.guild_id})
            db["deleted_roles_cache"].delete_many(
                {"guild_id": self.guild_id})
            return await interaction.response.edit_message(
                embed=self.view.build_embed(), view=self.view)


class RollbackNumberModal(ui.Modal):
    """Modal pra configurar valores numéricos do rollback."""

    def __init__(self, guild_id: int, key: str, title: str,
                 label: str, min_val: int, max_val: int):
        super().__init__(title=title[:45])
        self.guild_id = guild_id
        self.key = key
        self.min_val = min_val
        self.max_val = max_val

        cfg = _get_panel_cfg(guild_id)
        valor_atual = cfg.get(key, min_val)

        self.input = ui.TextInput(
            label=label[:45], default=str(valor_atual),
            required=True, max_length=5)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            valor = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ Valor inválido."), ephemeral=True)

        if valor < self.min_val or valor > self.max_val:
            return await interaction.response.send_message(
                embed=embed_error(
                    f"❌ Use entre {self.min_val} e {self.max_val}."),
                ephemeral=True)

        _update_panel_cfg(self.guild_id, self.key, valor)
        view = RollbackView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class RollbackView(ui.View):
    """View do sub-painel de rollback (v1.7)."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.add_item(RollbackActionSelect(guild_id))

        btn_back = ui.Button(
            label="⬅️ Voltar", style=ButtonStyle.secondary,
            custom_id=f"antinuke_rb_back_{guild_id}", row=1)
        btn_back.callback = self._back
        self.add_item(btn_back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _panel_interaction_allowed(interaction)

    def build_embed(self) -> discord.Embed:
        cfg = _get_panel_cfg(self.guild_id)
        enabled = cfg.get("rollback_enabled", True)
        db = get_connection()
        cache_ch = db["deleted_channels_cache"].count_documents(
            {"guild_id": self.guild_id})
        cache_ro = db["deleted_roles_cache"].count_documents(
            {"guild_id": self.guild_id})

        color = (discord.Color.green() if enabled
                 else discord.Color.red())

        embed = discord.Embed(
            title="♻️ Rollback",
            description="Restaura canais/cargos deletados.",
            color=color)

        embed.add_field(name="📊 Status",
                         value=_status_text(enabled), inline=True)
        embed.add_field(name="📦 Máx itens",
                         value=f"`{cfg.get('rollback_max_items', 50)}`",
                         inline=True)
        embed.add_field(name="Janela",
                         value=f"`{cfg.get('rollback_window_minutes', 5)}min`",
                         inline=True)
        embed.add_field(name="🗄️ Cache atual",
                         value=f"Canais: `{cache_ch}`\nCargos: `{cache_ro}`",
                         inline=False)

        embed.set_footer(text="Stumble Hit AntiRaid  •  Anti-Raid")
        return embed

    async def _back(self, interaction: discord.Interaction):
        _PANEL_PAGES[interaction.message.id] = 0
        view = AntiRaidPanelView(self.guild_id, page=0)
        cfg = _get_panel_cfg(self.guild_id)
        embed = _build_main_embed(cfg, interaction.guild, page=0)
        await interaction.response.edit_message(embed=embed, view=view)


# ============================================================
# BLOCO 15 — SUB-PAINEL: CANARY NUKE (COMPLETO v1.7)
# ============================================================

class CanaryActionSelect(ui.Select):
    """Select de ações do canary nuke."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        cfg = _get_panel_cfg(guild_id)
        triggers = cfg.get("canary_nuke_triggers", [])

        options = [
            SelectOption(label="🔄 Ativar/Desativar",
                          value="toggle",
                          description=f"Atual: {'ON' if cfg.get('canary_nuke_enabled', True) else 'OFF'}"),
            SelectOption(label="⏱️ Configurar duração",
                          value="config_duration",
                          description=f"Atual: {cfg.get('canary_nuke_duration', 300)}s"),
            SelectOption(label="🎯 Adicionar trigger",
                          value="add_trigger",
                          description=f"Atuais: {len(triggers)}"),
            SelectOption(label="🗑️ Remover trigger",
                          value="remove_trigger",
                          description="Remove um trigger existente"),
            SelectOption(label="👤 Banir autor do gatilho",
                          value="toggle_ban_author",
                          description=f"Atual: {'ON' if cfg.get('canary_nuke_ban_author', True) else 'OFF'}"),
            SelectOption(label="🔨 Banir quem executou",
                          value="toggle_ban_executor",
                          description=f"Atual: {'ON' if cfg.get('canary_nuke_ban_executor', True) else 'OFF'}"),
        ]

        super().__init__(
            placeholder="🐤 Selecione uma ação...",
            options=options[:25], min_values=1, max_values=1,
            custom_id=f"antinuke_can_sel_{guild_id}")

    async def callback(self, interaction: discord.Interaction):
        action = self.values[0]

        if action == "toggle":
            cfg = _get_panel_cfg(self.guild_id)
            new_val = not bool(cfg.get("canary_nuke_enabled", True))
            _update_panel_cfg(self.guild_id, "canary_nuke_enabled", new_val)
            return await interaction.response.edit_message(
                embed=self.view.build_embed(), view=self.view)

        if action == "config_duration":
            return await interaction.response.send_modal(
                CanaryDurationModal(self.guild_id))

        if action == "add_trigger":
            return await interaction.response.send_modal(
                CanaryTriggerModal(self.guild_id, "add"))

        if action == "remove_trigger":
            return await interaction.response.send_modal(
                CanaryTriggerModal(self.guild_id, "remove"))

        if action == "toggle_ban_author":
            cfg = _get_panel_cfg(self.guild_id)
            new_val = not bool(cfg.get("canary_nuke_ban_author", True))
            _update_panel_cfg(self.guild_id,
                                "canary_nuke_ban_author", new_val)
            return await interaction.response.edit_message(
                embed=self.view.build_embed(), view=self.view)

        if action == "toggle_ban_executor":
            cfg = _get_panel_cfg(self.guild_id)
            new_val = not bool(cfg.get("canary_nuke_ban_executor", True))
            _update_panel_cfg(self.guild_id,
                                "canary_nuke_ban_executor", new_val)
            return await interaction.response.edit_message(
                embed=self.view.build_embed(), view=self.view)


class CanaryDurationModal(ui.Modal):
    """Modal pra configurar duração do canary."""

    def __init__(self, guild_id: int):
        super().__init__(title="⏱️ Duração do Canary")
        self.guild_id = guild_id
        cfg = _get_panel_cfg(guild_id)
        valor = cfg.get("canary_nuke_duration", 300)

        self.input = ui.TextInput(
            label="Duração em segundos",
            default=str(valor), required=True, max_length=5)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            valor = int(self.input.value)
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ Valor inválido."), ephemeral=True)
        if valor < 10 or valor > 3600:
            return await interaction.response.send_message(
                embed=embed_error("❌ Use entre 10 e 3600 segundos."),
                ephemeral=True)
        _update_panel_cfg(self.guild_id, "canary_nuke_duration", valor)
        view = CanaryView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class CanaryTriggerModal(ui.Modal):
    """Modal pra add/remove trigger do canary."""

    def __init__(self, guild_id: int, mode: str):
        title = ("🎯 Adicionar Trigger" if mode == "add"
                 else "🗑️ Remover Trigger")
        super().__init__(title=title[:45])
        self.guild_id = guild_id
        self.mode = mode

        self.input = ui.TextInput(
            label="Palavra do gatilho",
            placeholder="Ex: nuke, raid, atacar",
            required=True, max_length=30)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        palavra = (self.input.value or "").strip().lower()
        if not palavra:
            return await interaction.response.send_message(
                embed=embed_error("❌ Palavra vazia."), ephemeral=True)

        cfg = _get_panel_cfg(self.guild_id)
        triggers = list(cfg.get("canary_nuke_triggers", []))

        if self.mode == "add":
            if palavra in triggers:
                return await interaction.response.send_message(
                    embed=embed_warning("⚠️ Já existe."), ephemeral=True)
            triggers.append(palavra)
            msg = f"✅ Trigger `{palavra}` adicionado."
        else:
            if palavra not in triggers:
                return await interaction.response.send_message(
                    embed=embed_warning("⚠️ Não existe."), ephemeral=True)
            triggers.remove(palavra)
            msg = f"✅ Trigger `{palavra}` removido."

        _update_panel_cfg(self.guild_id, "canary_nuke_triggers", triggers)
        view = CanaryView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)
        await interaction.followup.send(msg, ephemeral=True)


class CanaryView(ui.View):
    """View do sub-painel do canary nuke (v1.7)."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.add_item(CanaryActionSelect(guild_id))

        btn_back = ui.Button(
            label="⬅️ Voltar", style=ButtonStyle.secondary,
            custom_id=f"antinuke_can_back_{guild_id}", row=1)
        btn_back.callback = self._back
        self.add_item(btn_back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _panel_interaction_allowed(interaction)

    def build_embed(self) -> discord.Embed:
        cfg = _get_panel_cfg(self.guild_id)
        enabled = cfg.get("canary_nuke_enabled", True)
        triggers = cfg.get("canary_nuke_triggers", [])
        duration = cfg.get("canary_nuke_duration", 300)
        ban_author = cfg.get("canary_nuke_ban_author", True)
        ban_executor = cfg.get("canary_nuke_ban_executor", True)

        color = (discord.Color.green() if enabled
                 else discord.Color.red())

        embed = discord.Embed(
            title="🐤 Canary Nuke",
            description=("Vigilância de emergência. Se alguém digita "
                         "um gatilho, o bot entra em alerta máximo."),
            color=color)

        embed.add_field(name="📊 Status",
                         value=_status_text(enabled), inline=True)
        embed.add_field(name="⏱️ Duração",
                         value=f"`{duration}s`", inline=True)
        embed.add_field(name="👤 Banir autor",
                         value=_status_text(ban_author), inline=True)
        embed.add_field(name="🔨 Banir executor",
                         value=_status_text(ban_executor), inline=True)

        if triggers:
            embed.add_field(name="🎯 Gatilhos",
                             value=" ".join(f"`{t}`" for t in triggers),
                             inline=False)

        embed.set_footer(text="Stumble Hit AntiRaid  •  Anti-Raid")
        return embed

    async def _back(self, interaction: discord.Interaction):
        _PANEL_PAGES[interaction.message.id] = 0
        view = AntiRaidPanelView(self.guild_id, page=0)
        cfg = _get_panel_cfg(self.guild_id)
        embed = _build_main_embed(cfg, interaction.guild, page=0)
        await interaction.response.edit_message(embed=embed, view=view)


# ============================================================
# BLOCO 16 — SUB-PAINEL: BOT APPROVAL (COMPLETO v1.7)
# ============================================================

class BotApprovalActionSelect(ui.Select):
    """Select de ações do bot approval."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        cfg = _get_panel_cfg(guild_id)

        options = [
            SelectOption(label="🔄 Ativar/Desativar sistema",
                          value="toggle",
                          description=f"Atual: {'ON' if cfg.get('bot_approval_enabled', True) else 'OFF'}"),
            SelectOption(label="👢 Auto-kick não aprovados",
                          value="toggle_autokick",
                          description=f"Atual: {'ON' if cfg.get('auto_kick_unapproved_bots', True) else 'OFF'}"),
            SelectOption(label="➕ Adicionar User aprovador",
                          value="add_user",
                          description="User que pode aprovar bots"),
            SelectOption(label="➕ Adicionar Cargo aprovador",
                          value="add_role",
                          description="Cargo que pode aprovar bots"),
            SelectOption(label="👤 Remover User aprovador",
                          value="remove_user",
                          description="Remove aprovador pelo ID"),
            SelectOption(label="👑 Remover Cargo aprovador",
                          value="remove_role",
                          description="Remove cargo aprovador pelo ID"),
        ]

        super().__init__(
            placeholder="🤖 Selecione uma ação...",
            options=options[:25], min_values=1, max_values=1,
            custom_id=f"antinuke_ba_sel_{guild_id}")

    async def callback(self, interaction: discord.Interaction):
        action = self.values[0]

        if action == "toggle":
            cfg = _get_panel_cfg(self.guild_id)
            new_val = not bool(cfg.get("bot_approval_enabled", True))
            _update_panel_cfg(self.guild_id, "bot_approval_enabled", new_val)
            return await interaction.response.edit_message(
                embed=self.view.build_embed(), view=self.view)

        if action == "toggle_autokick":
            cfg = _get_panel_cfg(self.guild_id)
            new_val = not bool(cfg.get("auto_kick_unapproved_bots", True))
            _update_panel_cfg(self.guild_id,
                                "auto_kick_unapproved_bots", new_val)
            return await interaction.response.edit_message(
                embed=self.view.build_embed(), view=self.view)

        if action == "add_user":
            return await interaction.response.send_modal(
                BotApprovalAddUserModal(self.guild_id))
        if action == "add_role":
            return await interaction.response.send_modal(
                BotApprovalAddRoleModal(self.guild_id))

        if action == "remove_user":
            return await interaction.response.send_modal(
                BotApprovalRemoveModal(self.guild_id, "user"))
        if action == "remove_role":
            return await interaction.response.send_modal(
                BotApprovalRemoveModal(self.guild_id, "role"))


class BotApprovalAddUserModal(ui.Modal):
    """Modal pra adicionar user aprovador."""

    def __init__(self, guild_id: int):
        super().__init__(title="➕ Adicionar User Aprovador")
        self.guild_id = guild_id
        self.input = ui.TextInput(
            label="ID do usuário",
            placeholder="Ex: 123456789012345678",
            required=True, max_length=25)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            uid = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ ID inválido."), ephemeral=True)
        cfg = _get_panel_cfg(self.guild_id)
        wl = list(cfg.get("bot_approvers", []))
        if uid not in wl:
            wl.append(uid)
            _update_panel_cfg(self.guild_id, "bot_approvers", wl)
        view = BotApprovalView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class BotApprovalAddRoleModal(ui.Modal):
    """Modal pra adicionar cargo aprovador."""

    def __init__(self, guild_id: int):
        super().__init__(title="➕ Adicionar Cargo Aprovador")
        self.guild_id = guild_id
        self.input = ui.TextInput(
            label="ID do cargo",
            placeholder="Ex: 123456789012345678",
            required=True, max_length=25)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            rid = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ ID inválido."), ephemeral=True)
        cfg = _get_panel_cfg(self.guild_id)
        wl = list(cfg.get("bot_approvers_roles", []))
        if rid not in wl:
            wl.append(rid)
            _update_panel_cfg(self.guild_id, "bot_approvers_roles", wl)
        view = BotApprovalView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class BotApprovalRemoveModal(ui.Modal):
    """Modal pra remover user/cargo aprovador."""

    def __init__(self, guild_id: int, kind: str):
        title = ("👤 Remover User Aprovador" if kind == "user"
                 else "👑 Remover Cargo Aprovador")
        super().__init__(title=title[:45])
        self.guild_id = guild_id
        self.kind = kind

        self.input = ui.TextInput(
            label="ID pra remover",
            placeholder="Ex: 123456789012345678",
            required=True, max_length=25)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ ID inválido."), ephemeral=True)

        cfg = _get_panel_cfg(self.guild_id)
        if self.kind == "user":
            lista = [u for u in cfg.get("bot_approvers", []) if u != target]
            _update_panel_cfg(self.guild_id, "bot_approvers", lista)
        else:
            lista = [r for r in cfg.get("bot_approvers_roles", [])
                     if r != target]
            _update_panel_cfg(self.guild_id, "bot_approvers_roles", lista)

        view = BotApprovalView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)
        await interaction.followup.send(
            "✅ Removido.", ephemeral=True)


class BotApprovalView(ui.View):
    """View do sub-painel do bot approval (v1.7)."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.add_item(BotApprovalActionSelect(guild_id))

        btn_back = ui.Button(
            label="⬅️ Voltar", style=ButtonStyle.secondary,
            custom_id=f"antinuke_ba_back_{guild_id}", row=1)
        btn_back.callback = self._back
        self.add_item(btn_back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _panel_interaction_allowed(interaction)

    def build_embed(self) -> discord.Embed:
        cfg = _get_panel_cfg(self.guild_id)
        enabled = cfg.get("bot_approval_enabled", True)
        auto_kick = cfg.get("auto_kick_unapproved_bots", True)
        approvers_users = cfg.get("bot_approvers", [])
        approvers_roles = cfg.get("bot_approvers_roles", [])

        color = (discord.Color.green() if enabled
                 else discord.Color.red())

        embed = discord.Embed(
            title="🤖 Bot Approval",
            description="Sistema de aprovação de bots.",
            color=color)

        embed.add_field(name="📊 Status",
                         value=_status_text(enabled), inline=True)
        embed.add_field(name="👢 Auto-Kick",
                         value=_status_text(auto_kick), inline=True)
        embed.add_field(name="👥 Aprovadores",
                         value=f"`{len(approvers_users)}` users, "
                               f"`{len(approvers_roles)}` cargos",
                         inline=True)
        if approvers_users:
            embed.add_field(name="👤 Users",
                             value="\n".join(f"• <@{a}>"
                                             for a in approvers_users[:8]),
                             inline=False)
        if approvers_roles:
            embed.add_field(name="👑 Cargos",
                             value="\n".join(f"• <@&{r}>"
                                             for r in approvers_roles[:8]),
                             inline=False)

        embed.set_footer(text="Stumble Hit AntiRaid  •  Anti-Raid")
        return embed

    async def _back(self, interaction: discord.Interaction):
        _PANEL_PAGES[interaction.message.id] = 0
        view = AntiRaidPanelView(self.guild_id, page=0)
        cfg = _get_panel_cfg(self.guild_id)
        embed = _build_main_embed(cfg, interaction.guild, page=0)
        await interaction.response.edit_message(embed=embed, view=view)


# ============================================================
# BLOCO 17 — SUB-PAINEL: ANTI IP LOGGER
# ============================================================

class AntiIPLoggerPanelView(ui.View):
    """Sub-painel do Anti IP Logger."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

        # Botão toggle.
        btn_toggle = ui.Button(
            label="🔄 Ativar/Desativar", style=ButtonStyle.primary,
            custom_id=f"antinuke_aipl_toggle_{guild_id}", row=0)
        btn_toggle.callback = self._toggle
        self.add_item(btn_toggle)

        # Botão ciclo de punição.
        btn_punish = ui.Button(
            label="🎯 Punição", style=ButtonStyle.secondary,
            custom_id=f"antinuke_aipl_punish_{guild_id}", row=0)
        btn_punish.callback = self._cycle_punish
        self.add_item(btn_punish)

        # Botão timeout.
        btn_timeout = ui.Button(
            label="⏱️ Timeout", style=ButtonStyle.secondary,
            custom_id=f"antinuke_aipl_timeout_{guild_id}", row=0)
        btn_timeout.callback = self._set_timeout
        self.add_item(btn_timeout)

        # Botão log.
        btn_log = ui.Button(
            label="📝 Log", style=ButtonStyle.secondary,
            custom_id=f"antinuke_aipl_log_{guild_id}", row=1)
        btn_log.callback = self._set_log
        self.add_item(btn_log)

        # Botão voltar.
        btn_back = ui.Button(
            label="⬅️ Voltar", style=ButtonStyle.secondary,
            custom_id=f"antinuke_aipl_back_{guild_id}", row=1)
        btn_back.callback = self._back
        self.add_item(btn_back)

    def build_embed(self) -> discord.Embed:
        try:
            from commands_antilink_protection import AntiIPLoggerManager
            cfg = AntiIPLoggerManager.get_config(self.guild_id)
            total = len(AntiIPLoggerManager.get_all_domains(cfg))
        except Exception:
            cfg = {}
            total = 0

        def onoff(v):
            return "✅" if v else "❌"

        log_ch = cfg.get("log_channel")
        log_str = f"<#{log_ch}>" if log_ch else "❌"

        embed = discord.Embed(
            title="🚫 Anti IP Logger",
            description=(
                "Bloqueia links de serviços de IP Logger "
                "(Grabify, IPLogger, etc). Esses links são usados "
                "para capturar o IP dos membros e podem ser usados "
                "para doxxing ou ataques DDoS."
            ),
            color=discord.Color.red(),
            timestamp=_now())

        embed.add_field(name="📊 Status",
                         value=onoff(cfg.get("enabled", True)),
                         inline=True)
        embed.add_field(name="🎯 Punição",
                         value=f"`{cfg.get('punishment', 'warn')}`",
                         inline=True)
        embed.add_field(name="⏱️ Timeout",
                         value=f"`{cfg.get('timeout_minutes', 30)}min`",
                         inline=True)
        embed.add_field(name="🌐 Domínios bloqueados",
                         value=f"`{total}`",
                         inline=True)
        embed.add_field(name="📝 Log",
                         value=log_str,
                         inline=True)
        embed.add_field(name="👮 Ignorar staff",
                         value=onoff(cfg.get("ignore_staff", True)),
                         inline=True)

        embed.set_footer(text="Stumble Hit AntiRaid • Anti IP Logger")
        return embed

    async def _toggle(self, interaction: discord.Interaction):
        try:
            from commands_antilink_protection import AntiIPLoggerManager
            cfg = AntiIPLoggerManager.get_config(self.guild_id)
            new_val = not bool(cfg.get("enabled", True))
            AntiIPLoggerManager.update(self.guild_id, "enabled", new_val)
        except Exception:
            pass
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self)

    async def _cycle_punish(self, interaction: discord.Interaction):
        try:
            from commands_antilink_protection import AntiIPLoggerManager
            cfg = AntiIPLoggerManager.get_config(self.guild_id)
            atual = cfg.get("punishment", "warn")
            ordem = ["warn", "timeout", "kick", "ban", "delete_only"]
            idx = ordem.index(atual) if atual in ordem else 0
            novo = ordem[(idx + 1) % len(ordem)]
            AntiIPLoggerManager.update(self.guild_id, "punishment", novo)
        except Exception:
            pass
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self)

    async def _set_timeout(self, interaction: discord.Interaction):
        return await interaction.response.send_modal(
            AntiIPLoggerTimeoutModal(self.guild_id))

    async def _set_log(self, interaction: discord.Interaction):
        return await interaction.response.send_modal(
            AntiIPLoggerLogModal(self.guild_id))

    async def _back(self, interaction: discord.Interaction):
        _PANEL_PAGES[interaction.message.id] = 0
        view = AntiRaidPanelView(self.guild_id, page=0)
        cfg = _get_panel_cfg(self.guild_id)
        embed = _build_main_embed(cfg, interaction.guild, page=0)
        await interaction.response.edit_message(embed=embed, view=view)


class AntiIPLoggerTimeoutModal(ui.Modal):
    """Modal pra timeout do anti-ip-logger."""

    def __init__(self, guild_id: int):
        super().__init__(title="⏱️ Timeout do Anti IP Logger")
        self.guild_id = guild_id
        try:
            from commands_antilink_protection import AntiIPLoggerManager
            cfg = AntiIPLoggerManager.get_config(guild_id)
            valor = cfg.get("timeout_minutes", 30)
        except Exception:
            valor = 30

        self.input = ui.TextInput(
            label="Duração (minutos)",
            default=str(valor), required=True, max_length=6)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            valor = int(self.input.value)
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ Valor inválido."), ephemeral=True)
        if valor < 1 or valor > 40320:
            return await interaction.response.send_message(
                embed=embed_error("❌ Use entre 1 e 40320."),
                ephemeral=True)
        try:
            from commands_antilink_protection import AntiIPLoggerManager
            AntiIPLoggerManager.update(self.guild_id,
                                         "timeout_minutes", valor)
        except Exception:
            pass
        view = AntiIPLoggerPanelView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class AntiIPLoggerLogModal(ui.Modal):
    """Modal pra canal de log do anti-ip-logger."""

    def __init__(self, guild_id: int):
        super().__init__(title="📝 Canal de Log")
        self.guild_id = guild_id
        self.input = ui.TextInput(
            label="ID do canal de log",
            placeholder="Ex: 123456789012345678",
            required=True, max_length=25)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cid = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ ID inválido."), ephemeral=True)
        try:
            from commands_antilink_protection import AntiIPLoggerManager
            AntiIPLoggerManager.update(self.guild_id, "log_channel", cid)
        except Exception:
            pass
        view = AntiIPLoggerPanelView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


# ============================================================
# BLOCO 18 — SUB-PAINEL: ANTI EVADIR MUTE
# ============================================================

class AntiEvadePanelView(ui.View):
    """Sub-painel do Anti Evadir Mute."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

        btn_toggle = ui.Button(
            label="🔄 Ativar/Desativar", style=ButtonStyle.primary,
            custom_id=f"antinuke_ae_toggle_{guild_id}", row=0)
        btn_toggle.callback = self._toggle
        self.add_item(btn_toggle)

        btn_action = ui.Button(
            label="🎯 Ação", style=ButtonStyle.secondary,
            custom_id=f"antinuke_ae_action_{guild_id}", row=0)
        btn_action.callback = self._cycle_action
        self.add_item(btn_action)

        btn_multiplier = ui.Button(
            label="✖️ Multiplier", style=ButtonStyle.secondary,
            custom_id=f"antinuke_ae_mult_{guild_id}", row=0)
        btn_multiplier.callback = self._set_multiplier
        self.add_item(btn_multiplier)

        btn_log = ui.Button(
            label="📝 Log", style=ButtonStyle.secondary,
            custom_id=f"antinuke_ae_log_{guild_id}", row=1)
        btn_log.callback = self._set_log
        self.add_item(btn_log)

        btn_back = ui.Button(
            label="⬅️ Voltar", style=ButtonStyle.secondary,
            custom_id=f"antinuke_ae_back_{guild_id}", row=1)
        btn_back.callback = self._back
        self.add_item(btn_back)

    def build_embed(self) -> discord.Embed:
        try:
            from commands_anti_evade import AntiEvadeManager
            cfg = AntiEvadeManager.get_cached_config(self.guild_id)
        except Exception:
            cfg = {}

        def onoff(v):
            return "✅" if v else "❌"

        log_ch = cfg.get("log_channel")
        log_str = f"<#{log_ch}>" if log_ch else "❌"

        embed = discord.Embed(
            title="🔄 Anti Evadir Mute",
            description=(
                "Detecta quando um usuário sai do servidor para "
                "escapar de um timeout. Ao voltar, reaplica o timeout "
                "com o tempo restante. Configurável pra escalar "
                "(kick/ban) após N evasões."
            ),
            color=discord.Color.orange(),
            timestamp=_now())

        embed.add_field(name="📊 Status",
                         value=onoff(cfg.get("enabled", True)),
                         inline=True)
        embed.add_field(name="🎯 Ação",
                         value=f"`{cfg.get('action', 'timeout')}`",
                         inline=True)
        embed.add_field(name="✖️ Multiplier",
                         value=f"`{cfg.get('multiplier', 1.0)}x`",
                         inline=True)
        embed.add_field(name="🔁 Máx evasões",
                         value=f"`{cfg.get('max_evasions', 3)}`",
                         inline=True)
        embed.add_field(name="🚨 Escalar pra",
                         value=f"`{cfg.get('escalate_action', 'ban')}`",
                         inline=True)
        embed.add_field(name="📝 Log",
                         value=log_str,
                         inline=True)

        embed.set_footer(text="Stumble Hit AntiRaid • Anti Evadir Mute")
        return embed

    async def _toggle(self, interaction: discord.Interaction):
        try:
            from commands_anti_evade import AntiEvadeManager
            cfg = await AntiEvadeManager.get_config(self.guild_id)
            new_val = not bool(cfg.get("enabled", True))
            await AntiEvadeManager.update(self.guild_id, "enabled", new_val)
            await AntiEvadeManager.get_config(self.guild_id)
        except Exception:
            pass
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self)

    async def _cycle_action(self, interaction: discord.Interaction):
        try:
            from commands_anti_evade import AntiEvadeManager
            cfg = await AntiEvadeManager.get_config(self.guild_id)
            atual = cfg.get("action", "timeout")
            ordem = ["timeout", "kick", "ban"]
            idx = ordem.index(atual) if atual in ordem else 0
            novo = ordem[(idx + 1) % len(ordem)]
            await AntiEvadeManager.update(self.guild_id, "action", novo)
            await AntiEvadeManager.get_config(self.guild_id)
        except Exception:
            pass
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self)

    async def _set_multiplier(self, interaction: discord.Interaction):
        return await interaction.response.send_modal(
            AntiEvadeMultiplierModal(self.guild_id))

    async def _set_log(self, interaction: discord.Interaction):
        return await interaction.response.send_modal(
            AntiEvadeLogModal(self.guild_id))

    async def _back(self, interaction: discord.Interaction):
        _PANEL_PAGES[interaction.message.id] = 0
        view = AntiRaidPanelView(self.guild_id, page=0)
        cfg = _get_panel_cfg(self.guild_id)
        embed = _build_main_embed(cfg, interaction.guild, page=0)
        await interaction.response.edit_message(embed=embed, view=view)


class AntiEvadeMultiplierModal(ui.Modal):
    """Modal pra multiplier do anti-evade."""

    def __init__(self, guild_id: int):
        super().__init__(title="✖️ Multiplier do Anti Evade")
        self.guild_id = guild_id
        try:
            from commands_anti_evade import AntiEvadeManager
            cfg = AntiEvadeManager.get_cached_config(guild_id)
            valor = cfg.get("multiplier", 1.0)
        except Exception:
            valor = 1.0

        self.input = ui.TextInput(
            label="Multiplicador (1.0 a 10.0)",
            default=str(valor), required=True, max_length=5)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            valor = float(self.input.value.replace(",", "."))
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ Valor inválido."), ephemeral=True)
        if valor < 1.0 or valor > 10.0:
            return await interaction.response.send_message(
                embed=embed_error("❌ Use entre 1.0 e 10.0."),
                ephemeral=True)
        try:
            from commands_anti_evade import AntiEvadeManager
            await AntiEvadeManager.update(self.guild_id, "multiplier", valor)
            await AntiEvadeManager.get_config(self.guild_id)
        except Exception:
            pass
        view = AntiEvadePanelView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class AntiEvadeLogModal(ui.Modal):
    """Modal pra canal de log do anti-evade."""

    def __init__(self, guild_id: int):
        super().__init__(title="📝 Canal de Log")
        self.guild_id = guild_id
        self.input = ui.TextInput(
            label="ID do canal de log",
            placeholder="Ex: 123456789012345678",
            required=True, max_length=25)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cid = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ ID inválido."), ephemeral=True)
        try:
            from commands_anti_evade import AntiEvadeManager
            await AntiEvadeManager.update(self.guild_id, "log_channel", cid)
            await AntiEvadeManager.get_config(self.guild_id)
        except Exception:
            pass
        view = AntiEvadePanelView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


# ============================================================
# BLOCO 19 — SUB-PAINEL: JOIN RISK
# ============================================================

class JoinRiskPanelView(ui.View):
    """Sub-painel do Join Risk Score."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

        btn_toggle = ui.Button(
            label="🔄 Ativar/Desativar", style=ButtonStyle.primary,
            custom_id=f"antinuke_jr_toggle_{guild_id}", row=0)
        btn_toggle.callback = self._toggle
        self.add_item(btn_toggle)

        btn_dry = ui.Button(
            label="🧪 Dry Run", style=ButtonStyle.secondary,
            custom_id=f"antinuke_jr_dry_{guild_id}", row=0)
        btn_dry.callback = self._toggle_dry
        self.add_item(btn_dry)

        btn_log = ui.Button(
            label="📝 Log", style=ButtonStyle.secondary,
            custom_id=f"antinuke_jr_log_{guild_id}", row=0)
        btn_log.callback = self._set_log
        self.add_item(btn_log)

        btn_f1 = ui.Button(
            label="🎯 Faixa 1", style=ButtonStyle.secondary,
            custom_id=f"antinuke_jr_f1_{guild_id}", row=1)
        btn_f1.callback = lambda i: self._cycle_action(i, 1)
        self.add_item(btn_f1)

        btn_f2 = ui.Button(
            label="🎯 Faixa 2", style=ButtonStyle.secondary,
            custom_id=f"antinuke_jr_f2_{guild_id}", row=1)
        btn_f2.callback = lambda i: self._cycle_action(i, 2)
        self.add_item(btn_f2)

        btn_f3 = ui.Button(
            label="🎯 Faixa 3", style=ButtonStyle.secondary,
            custom_id=f"antinuke_jr_f3_{guild_id}", row=1)
        btn_f3.callback = lambda i: self._cycle_action(i, 3)
        self.add_item(btn_f3)

        btn_back = ui.Button(
            label="⬅️ Voltar", style=ButtonStyle.secondary,
            custom_id=f"antinuke_jr_back_{guild_id}", row=2)
        btn_back.callback = self._back
        self.add_item(btn_back)

    def build_embed(self) -> discord.Embed:
        try:
            from commands_join_risk import JoinRiskManager
            cfg = JoinRiskManager.get_config(self.guild_id)
        except Exception:
            cfg = {}

        def onoff(v):
            return "✅" if v else "❌"

        log_ch = cfg.get("log_channel")
        log_str = f"<#{log_ch}>" if log_ch else "❌"

        t1 = cfg.get("threshold1", 70)
        t2 = cfg.get("threshold2", 85)
        t3 = cfg.get("threshold3", 95)

        embed = discord.Embed(
            title="📊 Join Risk Score",
            description=(
                "Calcula um score de risco (0-100) em cada novo "
                "membro. **Aditivo** ao sistema de raid existente."
            ),
            color=discord.Color.purple(),
            timestamp=_now())

        embed.add_field(name="📊 Status",
                         value=onoff(cfg.get("enabled", True)),
                         inline=True)
        embed.add_field(name="🧪 Dry run",
                         value=onoff(cfg.get("dry_run", False)),
                         inline=True)
        embed.add_field(name="📝 Log",
                         value=log_str, inline=True)

        embed.add_field(
            name="🎯 Faixas",
            value=(
                f"**1** ({t1}-{t2-1}): `{cfg.get('action1', 'log')}`\n"
                f"**2** ({t2}-{t3-1}): `{cfg.get('action2', 'kick')}`\n"
                f"**3** ({t3}+): `{cfg.get('action3', 'ban')}`"
            ),
            inline=False)

        embed.set_footer(text="Stumble Hit AntiRaid • Join Risk Score")
        return embed

    async def _toggle(self, interaction: discord.Interaction):
        try:
            from commands_join_risk import JoinRiskManager
            cfg = JoinRiskManager.get_config(self.guild_id)
            new_val = not bool(cfg.get("enabled", True))
            JoinRiskManager.update(self.guild_id, "enabled", new_val)
        except Exception:
            pass
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self)

    async def _toggle_dry(self, interaction: discord.Interaction):
        try:
            from commands_join_risk import JoinRiskManager
            cfg = JoinRiskManager.get_config(self.guild_id)
            new_val = not bool(cfg.get("dry_run", False))
            JoinRiskManager.update(self.guild_id, "dry_run", new_val)
        except Exception:
            pass
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self)

    async def _set_log(self, interaction: discord.Interaction):
        return await interaction.response.send_modal(
            JoinRiskLogModal(self.guild_id))

    async def _cycle_action(self, interaction: discord.Interaction,
                              faixa: int):
        try:
            from commands_join_risk import JoinRiskManager
            cfg = JoinRiskManager.get_config(self.guild_id)
            key = f"action{faixa}"
            atual = cfg.get(key, "log" if faixa == 1 else
                             ("kick" if faixa == 2 else "ban"))
            ordem = ["log", "kick", "ban", "timeout", "none"]
            idx = ordem.index(atual) if atual in ordem else 0
            novo = ordem[(idx + 1) % len(ordem)]
            JoinRiskManager.update(self.guild_id, key, novo)
        except Exception:
            pass
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self)

    async def _back(self, interaction: discord.Interaction):
        _PANEL_PAGES[interaction.message.id] = 0
        view = AntiRaidPanelView(self.guild_id, page=0)
        cfg = _get_panel_cfg(self.guild_id)
        embed = _build_main_embed(cfg, interaction.guild, page=0)
        await interaction.response.edit_message(embed=embed, view=view)


class JoinRiskLogModal(ui.Modal):
    """Modal pra canal de log do join risk."""

    def __init__(self, guild_id: int):
        super().__init__(title="📝 Canal de Log")
        self.guild_id = guild_id
        self.input = ui.TextInput(
            label="ID do canal de log",
            placeholder="Ex: 123456789012345678",
            required=True, max_length=25)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cid = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ ID inválido."), ephemeral=True)
        try:
            from commands_join_risk import JoinRiskManager
            JoinRiskManager.update(self.guild_id, "log_channel", cid)
        except Exception:
            pass
        view = JoinRiskPanelView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


# ============================================================
# BLOCO 20 — SUB-PAINEL: STATS GRAPH
# ============================================================

class StatsGraphPanelView(ui.View):
    """Sub-painel do Stats Graph."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

        btn_toggle = ui.Button(
            label="🔄 Auto on/off", style=ButtonStyle.primary,
            custom_id=f"antinuke_sg_toggle_{guild_id}", row=0)
        btn_toggle.callback = self._toggle
        self.add_item(btn_toggle)

        btn_days = ui.Button(
            label="📅 Dias", style=ButtonStyle.secondary,
            custom_id=f"antinuke_sg_days_{guild_id}", row=0)
        btn_days.callback = self._set_days
        self.add_item(btn_days)

        btn_hour = ui.Button(
            label="🕒 Hora", style=ButtonStyle.secondary,
            custom_id=f"antinuke_sg_hour_{guild_id}", row=0)
        btn_hour.callback = self._set_hour
        self.add_item(btn_hour)

        btn_ch = ui.Button(
            label="📝 Canal", style=ButtonStyle.secondary,
            custom_id=f"antinuke_sg_ch_{guild_id}", row=1)
        btn_ch.callback = self._set_channel
        self.add_item(btn_ch)

        btn_back = ui.Button(
            label="⬅️ Voltar", style=ButtonStyle.secondary,
            custom_id=f"antinuke_sg_back_{guild_id}", row=1)
        btn_back.callback = self._back
        self.add_item(btn_back)

    def build_embed(self) -> discord.Embed:
        try:
            from commands_stats_graph import StatsGraphManager, _HAS_PIL
            cfg = StatsGraphManager.get_config(self.guild_id)
            has_pil = _HAS_PIL
        except Exception:
            cfg = {}
            has_pil = False

        def onoff(v):
            return "✅" if v else "❌"

        ch = cfg.get("auto_channel")
        ch_str = f"<#{ch}>" if ch else "❌"

        dias = ["Segunda", "Terça", "Quarta", "Quinta",
                 "Sexta", "Sábado", "Domingo"]
        wd = int(cfg.get("auto_weekday", 0))
        dia_nome = dias[wd] if 0 <= wd < 7 else "?"

        embed = discord.Embed(
            title="📈 Stats Graph",
            description=(
                "Gera gráficos PNG com estatísticas de moderação. "
                "Pode ser enviado sob demanda ou automaticamente."
            ),
            color=discord.Color.blurple(),
            timestamp=_now())

        embed.add_field(name="🎨 Pillow",
                         value=onoff(has_pil), inline=True)
        embed.add_field(name="🤖 Auto",
                         value=onoff(cfg.get("auto_enabled", False)),
                         inline=True)
        embed.add_field(name="📅 Dia",
                         value=f"`{dia_nome}`", inline=True)
        embed.add_field(name="🕒 Hora (UTC)",
                         value=f"`{cfg.get('auto_hour', 9)}h`",
                         inline=True)
        embed.add_field(name="📝 Canal",
                         value=ch_str, inline=True)
        embed.add_field(name="📊 Dias padrão",
                         value=f"`{cfg.get('default_days', 30)}`",
                         inline=True)

        embed.set_footer(text="Stumble Hit AntiRaid • Stats Graph")
        return embed

    async def _toggle(self, interaction: discord.Interaction):
        try:
            from commands_stats_graph import StatsGraphManager
            cfg = StatsGraphManager.get_config(self.guild_id)
            new_val = not bool(cfg.get("auto_enabled", False))
            StatsGraphManager.update(self.guild_id, "auto_enabled", new_val)
        except Exception:
            pass
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self)

    async def _set_days(self, interaction: discord.Interaction):
        return await interaction.response.send_modal(
            StatsGraphDaysModal(self.guild_id))

    async def _set_hour(self, interaction: discord.Interaction):
        return await interaction.response.send_modal(
            StatsGraphHourModal(self.guild_id))

    async def _set_channel(self, interaction: discord.Interaction):
        return await interaction.response.send_modal(
            StatsGraphChannelModal(self.guild_id))

    async def _back(self, interaction: discord.Interaction):
        _PANEL_PAGES[interaction.message.id] = 0
        view = AntiRaidPanelView(self.guild_id, page=0)
        cfg = _get_panel_cfg(self.guild_id)
        embed = _build_main_embed(cfg, interaction.guild, page=0)
        await interaction.response.edit_message(embed=embed, view=view)


class StatsGraphDaysModal(ui.Modal):
    """Modal pra dias padrão do stats graph."""

    def __init__(self, guild_id: int):
        super().__init__(title="📅 Dias padrão")
        self.guild_id = guild_id
        try:
            from commands_stats_graph import StatsGraphManager
            cfg = StatsGraphManager.get_config(guild_id)
            valor = cfg.get("default_days", 30)
        except Exception:
            valor = 30
        self.input = ui.TextInput(
            label="Dias (1-365)",
            default=str(valor), required=True, max_length=3)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            valor = int(self.input.value)
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ Valor inválido."), ephemeral=True)
        if valor < 1 or valor > 365:
            return await interaction.response.send_message(
                embed=embed_error("❌ Use entre 1 e 365."),
                ephemeral=True)
        try:
            from commands_stats_graph import StatsGraphManager
            StatsGraphManager.update(self.guild_id, "default_days", valor)
        except Exception:
            pass
        view = StatsGraphPanelView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class StatsGraphHourModal(ui.Modal):
    """Modal pra hora do stats graph."""

    def __init__(self, guild_id: int):
        super().__init__(title="🕒 Hora do envio (UTC)")
        self.guild_id = guild_id
        try:
            from commands_stats_graph import StatsGraphManager
            cfg = StatsGraphManager.get_config(guild_id)
            valor = cfg.get("auto_hour", 9)
        except Exception:
            valor = 9
        self.input = ui.TextInput(
            label="Hora (0-23)",
            default=str(valor), required=True, max_length=2)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            valor = int(self.input.value)
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ Valor inválido."), ephemeral=True)
        if valor < 0 or valor > 23:
            return await interaction.response.send_message(
                embed=embed_error("❌ Use entre 0 e 23."),
                ephemeral=True)
        try:
            from commands_stats_graph import StatsGraphManager
            StatsGraphManager.update(self.guild_id, "auto_hour", valor)
        except Exception:
            pass
        view = StatsGraphPanelView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class StatsGraphChannelModal(ui.Modal):
    """Modal pra canal do stats graph."""

    def __init__(self, guild_id: int):
        super().__init__(title="📝 Canal do envio automático")
        self.guild_id = guild_id
        self.input = ui.TextInput(
            label="ID do canal",
            placeholder="Ex: 123456789012345678",
            required=True, max_length=25)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            cid = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ ID inválido."), ephemeral=True)
        try:
            from commands_stats_graph import StatsGraphManager
            StatsGraphManager.update(self.guild_id, "auto_channel", cid)
        except Exception:
            pass
        view = StatsGraphPanelView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


# ============================================================
# BLOCO 21 — SUB-PAINEL: EMBED BUILDER
# ============================================================

class EmbedBuilderPanelView(ui.View):
    """Sub-painel do Embed Builder (só atalhos)."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

        btn_editor = ui.Button(
            label="✏️ Editor (Canal Privado)", style=ButtonStyle.primary,
            custom_id=f"antinuke_eb_editor_{guild_id}", row=0)
        btn_editor.callback = self._open_editor
        self.add_item(btn_editor)

        btn_list = ui.Button(
            label="📋 Ver Painéis", style=ButtonStyle.secondary,
            custom_id=f"antinuke_eb_list_{guild_id}", row=0)
        btn_list.callback = self._list_panels
        self.add_item(btn_list)

        btn_channels = ui.Button(
            label="📁 Configurar Canais", style=ButtonStyle.secondary,
            custom_id=f"antinuke_eb_channels_{guild_id}", row=0)
        btn_channels.callback = self._config_channels
        self.add_item(btn_channels)

        btn_reaction = ui.Button(
            label="😀 Reaction Roles", style=ButtonStyle.secondary,
            custom_id=f"antinuke_eb_rr_{guild_id}", row=1)
        btn_reaction.callback = self._reaction_roles
        self.add_item(btn_reaction)

        btn_back = ui.Button(
            label="⬅️ Voltar", style=ButtonStyle.secondary,
            custom_id=f"antinuke_eb_back_{guild_id}", row=1)
        btn_back.callback = self._back
        self.add_item(btn_back)

    def build_embed(self) -> discord.Embed:
        try:
            from commands_embed_builder import _get_eb_config
            eb_cfg = _get_eb_config(self.guild_id)
        except Exception:
            eb_cfg = {}

        try:
            db = get_connection()
            panels = db["embed_panels"].count_documents(
                {"guild_id": self.guild_id})
            messages = db["embed_messages"].count_documents(
                {"guild_id": self.guild_id})
            variables = db["embed_variables"].count_documents(
                {"guild_id": self.guild_id})
            drafts = db["embed_panel_drafts"].count_documents(
                {"guild_id": self.guild_id})
            reaction_roles = db["embed_reaction_roles"].count_documents(
                {"guild_id": self.guild_id})
        except Exception:
            panels = messages = variables = drafts = reaction_roles = 0

        priv = eb_cfg.get("private_channel")
        pub = eb_cfg.get("public_channel")
        priv_str = f"<#{priv}>" if priv else "❌ Não configurado"
        pub_str = f"<#{pub}>" if pub else "❌ Não configurado"

        embed = discord.Embed(
            title="🎨 Embed Builder",
            description=(
                "Crie **painéis interativos** persistentes com botões, "
                "selects, modais e ações configuráveis.\n\n"
                "**Fluxo recomendado:**\n"
                "1. Configure um **canal privado** (só staff vê)\n"
                "2. Configure um **canal público** (todos veem)\n"
                "3. Crie/edite painéis no canal privado\n"
                "4. Publique no canal público quando estiver pronto"
            ),
            color=discord.Color.from_rgb(88, 101, 242),
            timestamp=_now())

        embed.add_field(
            name="📁 Canais",
            value=f"**🔒 Privado:** {priv_str}\n**🌐 Público:** {pub_str}",
            inline=False)

        embed.add_field(
            name="📊 Estatísticas",
            value=(
                f"**🎨 Painéis:** `{panels}`\n"
                f"**📋 Templates:** `{messages}`\n"
                f"**🔧 Variáveis:** `{variables}`\n"
                f"**📝 Rascunhos:** `{drafts}`\n"
                f"**😀 Reaction Roles:** `{reaction_roles}`"
            ),
            inline=True)

        embed.add_field(
            name="📌 Comandos úteis",
            value=(
                "`>eb criar <nome>`\n"
                "`>eb editar <nome>`\n"
                "`>eb enviar <nome>`\n"
                "`>eb listar`\n"
                "`>rr criar <msg_id> <emoji> @cargo`"
            ),
            inline=True)

        embed.set_footer(text="Stumble Hit AntiRaid • Embed Builder v2.0")
        return embed

    async def _open_editor(self, interaction: discord.Interaction):
        try:
            from commands_embed_builder import _get_eb_config
            cfg = _get_eb_config(self.guild_id)
            priv = cfg.get("private_channel")
        except Exception:
            priv = None

        if priv:
            ch = interaction.guild.get_channel(int(priv))
            texto = (f"✏️ Use `>eb editar <nome>` — o editor abrirá "
                     f"automaticamente em {ch.mention}.")
        else:
            texto = ("✏️ Configure o canal privado primeiro com "
                     "`>eb canal-privado #canal`, depois use "
                     "`>eb editar <nome>`.")

        await interaction.response.send_message(
            embed=embed_info(texto), ephemeral=True)

    async def _list_panels(self, interaction: discord.Interaction):
        try:
            from commands_embed_builder import _list_panels
            panels = _list_panels(self.guild_id, 20)
        except Exception:
            panels = []

        if not panels:
            return await interaction.response.send_message(
                embed=embed_info(
                    "📋 Nenhum painel ainda. Use `>eb criar <nome>`."),
                ephemeral=True)

        linhas = []
        for p in panels[:15]:
            icone = "📤" if p.get("message_id") else "💾"
            comps = len(p.get("components", []))
            linhas.append(f"{icone} `{p['name']}` — {comps} componentes")

        await interaction.response.send_message(
            embed=embed_info(
                f"🎨 **Painéis ({len(panels)}):**\n" + "\n".join(linhas)),
            ephemeral=True)

    async def _config_channels(self, interaction: discord.Interaction):
        texto = (
            "📁 **Configurar canais:**\n\n"
            "**Canal privado** (onde você edita):\n"
            "`>eb canal-privado #canal`\n\n"
            "**Canal público** (onde publica):\n"
            "`>eb canal-publico #canal`\n\n"
            "Ver config atual: `>eb canais`"
        )
        await interaction.response.send_message(
            embed=embed_info(texto), ephemeral=True)

    async def _reaction_roles(self, interaction: discord.Interaction):
        texto = (
            "😀 **Reaction Roles** (estilo Carl-bot):\n\n"
            "`>rr criar <msg_id> <emoji> @cargo`\n"
            "`>rr listar`\n"
            "`>rr remover <msg_id> <emoji>`\n"
            "`>rr limpar <msg_id>`"
        )
        await interaction.response.send_message(
            embed=embed_info(texto), ephemeral=True)

    async def _back(self, interaction: discord.Interaction):
        _PANEL_PAGES[interaction.message.id] = 0
        view = AntiRaidPanelView(self.guild_id, page=0)
        cfg = _get_panel_cfg(self.guild_id)
        embed = _build_main_embed(cfg, interaction.guild, page=0)
        await interaction.response.edit_message(embed=embed, view=view)


# ============================================================
# BLOCO 22 — SUB-PAINEL: LOCKDOWN
# ============================================================

class LockdownView(ui.View):
    """Sub-painel de lockdown emergencial."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        cfg = _get_panel_cfg(guild_id)
        ativo = cfg.get("manual_lockdown_active", False)

        # Botão muda conforme o estado.
        if ativo:
            btn = ui.Button(
                label="🔓 Desativar Lockdown", style=ButtonStyle.success,
                custom_id=f"antinuke_ld_off_{guild_id}", row=0)
            btn.callback = self._deactivate
        else:
            btn = ui.Button(
                label="🔒 Ativar Lockdown", style=ButtonStyle.danger,
                custom_id=f"antinuke_ld_on_{guild_id}", row=0)
            btn.callback = self._activate
        self.add_item(btn)

        btn_back = ui.Button(
            label="⬅️ Voltar", style=ButtonStyle.secondary,
            custom_id=f"antinuke_ld_back_{guild_id}", row=1)
        btn_back.callback = self._back
        self.add_item(btn_back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _panel_interaction_allowed(interaction)

    def build_embed(self) -> discord.Embed:
        cfg = _get_panel_cfg(self.guild_id)
        ativo = cfg.get("manual_lockdown_active", False)
        color = (discord.Color.red() if ativo
                 else discord.Color.blurple())

        embed = discord.Embed(
            title="🔒 Lockdown Emergencial",
            description=("**Modo de emergência** — trava TODOS os canais.\n\n"
                         "• Remove permissões do @everyone\n"
                         "• Remove exceções de cargos\n"
                         "• Salva estado pra restaurar depois\n\n"
                         "⚠️ Use só em emergência real."),
            color=color)
        embed.add_field(name="📊 Estado",
                         value="🔴 **ATIVO**" if ativo else "🟢 **INATIVO**",
                         inline=False)
        embed.set_footer(text="Stumble Hit AntiRaid  •  Anti-Raid")
        return embed

    async def _activate(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        cog = interaction.client.get_cog("AntiNukeCommands")
        if not cog:
            return await interaction.followup.send(
                embed=embed_error("❌ Cog AntiNuke não encontrado."),
                ephemeral=True)
        result = await cog._apply_lockdown(interaction.guild)
        AntiNukeManager.update(interaction.guild.id,
                                "manual_lockdown_active", True)
        AntiNukeManager.update(interaction.guild.id,
                                "lockdown_saved_perms", result["saved"])
        view = LockdownView(self.guild_id)
        await interaction.message.edit(embed=view.build_embed(), view=view)
        await interaction.followup.send(
            embed=embed_success(
                f"🔒 Lockdown ativado! "
                f"**{result['stats']['ok']}** canais trancados."),
            ephemeral=True)

    async def _deactivate(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        cog = interaction.client.get_cog("AntiNukeCommands")
        if not cog:
            return await interaction.followup.send(
                embed=embed_error("❌ Cog AntiNuke não encontrado."),
                ephemeral=True)
        cfg = _get_panel_cfg(self.guild_id)
        saved = cfg.get("lockdown_saved_perms", {})
        stats = await cog._restore_lockdown(interaction.guild, saved)
        AntiNukeManager.update(interaction.guild.id,
                                "manual_lockdown_active", False)
        AntiNukeManager.update(interaction.guild.id,
                                "lockdown_saved_perms", {})
        view = LockdownView(self.guild_id)
        await interaction.message.edit(embed=view.build_embed(), view=view)
        await interaction.followup.send(
            embed=embed_success(f"🔓 Lockdown desativado! "
                                 f"**{stats['ok']}** canais restaurados."),
            ephemeral=True)

    async def _back(self, interaction: discord.Interaction):
        _PANEL_PAGES[interaction.message.id] = 0
        view = AntiRaidPanelView(self.guild_id, page=0)
        cfg = _get_panel_cfg(self.guild_id)
        embed = _build_main_embed(cfg, interaction.guild, page=0)
        await interaction.response.edit_message(embed=embed, view=view)


# ============================================================
# BLOCO 23 — SUB-PAINEL: PERMISSÕES
# ============================================================

class AntinukePermissionsView(ui.View):
    """Sub-painel de permissões do anti-nuke."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

        btn_cargo = ui.Button(
            label="➕ Cargo", style=ButtonStyle.success,
            custom_id=f"antinuke_perm_role_{guild_id}", row=0)
        btn_cargo.callback = self._add_role
        self.add_item(btn_cargo)

        btn_user = ui.Button(
            label="➕ User", style=ButtonStyle.success,
            custom_id=f"antinuke_perm_user_{guild_id}", row=0)
        btn_user.callback = self._add_user
        self.add_item(btn_user)

        # 🆕 v1.7: botão de remover.
        btn_remove = ui.Button(
            label="🗑️ Remover", style=ButtonStyle.danger,
            custom_id=f"antinuke_perm_remove_{guild_id}", row=0)
        btn_remove.callback = self._remove
        self.add_item(btn_remove)

        # 🆕 v1.7: toggle require_manager.
        btn_toggle = ui.Button(
            label="🔄 Require Manager", style=ButtonStyle.primary,
            custom_id=f"antinuke_perm_toggle_{guild_id}", row=1)
        btn_toggle.callback = self._toggle_require
        self.add_item(btn_toggle)

        btn_clear = ui.Button(
            label="🗑️ Limpar tudo", style=ButtonStyle.danger,
            custom_id=f"antinuke_perm_clear_{guild_id}", row=1)
        btn_clear.callback = self._clear
        self.add_item(btn_clear)

        btn_back = ui.Button(
            label="⬅️ Voltar", style=ButtonStyle.secondary,
            custom_id=f"antinuke_perm_back_{guild_id}", row=1)
        btn_back.callback = self._back
        self.add_item(btn_back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Impede que membros sem permissão alterem configurações do painel."""
        return await _panel_interaction_allowed(interaction)

    def build_embed(self) -> discord.Embed:
        cfg = _get_panel_cfg(self.guild_id)
        roles = cfg.get("antinuke_managers_roles", [])
        users = cfg.get("antinuke_managers_users", [])
        require = cfg.get("antinuke_require_manager", True)

        embed = discord.Embed(
            title="🔐 Permissões do Anti-Nuke",
            description=("Configure quem pode mexer no anti-nuke "
                         "(painel + comandos).\n\n"
                         "**Owner sempre pode.**"),
            color=_get_panel_color(self.guild_id),
            timestamp=_now())

        embed.add_field(
            name=f"👑 Cargos ({len(roles)})",
            value="\n".join(f"• <@&{r}>" for r in roles[:10]) or "*vazio*",
            inline=False)
        embed.add_field(
            name=f"👤 Users ({len(users)})",
            value="\n".join(f"• <@{u}>" for u in users[:10]) or "*vazio*",
            inline=False)
        embed.add_field(
            name="🛡️ Exigir manager",
            value=(
                "🟢 **SIM** — SÓ quem tá na lista pode mexer.\n"
                if require else
                "🔴 **NÃO** — Admin do Discord também pode.\n"),
            inline=False)

        embed.set_footer(text="Stumble Hit AntiRaid  •  Anti-Raid")
        return embed

    async def _add_role(self, interaction: discord.Interaction):
        return await interaction.response.send_modal(
            AntinukePermAddRoleModal(self.guild_id))

    async def _add_user(self, interaction: discord.Interaction):
        return await interaction.response.send_modal(
            AntinukePermAddUserModal(self.guild_id))

    async def _remove(self, interaction: discord.Interaction):
        return await interaction.response.send_modal(
            AntinukePermRemoveModal(self.guild_id))

    async def _toggle_require(self, interaction: discord.Interaction):
        cfg = _get_panel_cfg(self.guild_id)
        new_val = not bool(cfg.get("antinuke_require_manager", True))
        _update_panel_cfg(self.guild_id,
                            "antinuke_require_manager", new_val)
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self)

    async def _clear(self, interaction: discord.Interaction):
        _update_panel_cfg(self.guild_id, "antinuke_managers_roles", [])
        _update_panel_cfg(self.guild_id, "antinuke_managers_users", [])
        await interaction.response.edit_message(
            embed=self.build_embed(), view=self)

    async def _back(self, interaction: discord.Interaction):
        _PANEL_PAGES[interaction.message.id] = 0
        view = AntiRaidPanelView(self.guild_id, page=0)
        cfg = _get_panel_cfg(self.guild_id)
        embed = _build_main_embed(cfg, interaction.guild, page=0)
        await interaction.response.edit_message(embed=embed, view=view)


class AntinukePermAddRoleModal(ui.Modal):
    """Modal pra adicionar cargo manager."""

    def __init__(self, guild_id: int):
        super().__init__(title="🔐 Adicionar Cargo Manager")
        self.guild_id = guild_id
        self.input = ui.TextInput(
            label="ID do cargo",
            placeholder="Ex: 123456789012345678",
            required=True, max_length=25)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        if not await _panel_interaction_allowed(interaction):
            return
        try:
            rid = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ ID inválido."), ephemeral=True)
        cfg = _get_panel_cfg(self.guild_id)
        wl = list(cfg.get("antinuke_managers_roles", []))
        if rid not in wl:
            wl.append(rid)
            _update_panel_cfg(self.guild_id, "antinuke_managers_roles", wl)
        view = AntinukePermissionsView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class AntinukePermAddUserModal(ui.Modal):
    """Modal pra adicionar user manager."""

    def __init__(self, guild_id: int):
        super().__init__(title="🔐 Adicionar User Manager")
        self.guild_id = guild_id
        self.input = ui.TextInput(
            label="ID do usuário",
            placeholder="Ex: 123456789012345678",
            required=True, max_length=25)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        if not await _panel_interaction_allowed(interaction):
            return
        try:
            uid = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ ID inválido."), ephemeral=True)
        cfg = _get_panel_cfg(self.guild_id)
        wl = list(cfg.get("antinuke_managers_users", []))
        if uid not in wl:
            wl.append(uid)
            _update_panel_cfg(self.guild_id, "antinuke_managers_users", wl)
        view = AntinukePermissionsView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class AntinukePermRemoveModal(ui.Modal):
    """Modal pra remover user/cargo manager."""

    def __init__(self, guild_id: int):
        super().__init__(title="🗑️ Remover Manager")
        self.guild_id = guild_id
        self.input = ui.TextInput(
            label="ID do user OU cargo",
            placeholder="Ex: 123456789012345678",
            required=True, max_length=25)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        if not await _panel_interaction_allowed(interaction):
            return
        try:
            target = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ ID inválido."), ephemeral=True)
        cfg = _get_panel_cfg(self.guild_id)

        # Tenta remover de ambas as listas.
        roles = [r for r in cfg.get("antinuke_managers_roles", [])
                 if r != target]
        users = [u for u in cfg.get("antinuke_managers_users", [])
                 if u != target]
        _update_panel_cfg(self.guild_id, "antinuke_managers_roles", roles)
        _update_panel_cfg(self.guild_id, "antinuke_managers_users", users)

        view = AntinukePermissionsView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


# ============================================================
# BLOCO 24 — SUB-PAINEL: WHITELIST (v1.7 — completa)
# ============================================================

class WhitelistActionSelect(ui.Select):
    """Select de ações da whitelist."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        cfg = _get_panel_cfg(guild_id)

        # Modo atual da whitelist.
        modo = cfg.get("whitelist_mode", "confirm_kick")

        options = [
            SelectOption(label="➕ Adicionar User",
                          value="add_user",
                          description="User que ignora proteções"),
            SelectOption(label="➕ Adicionar Bot",
                          value="add_bot",
                          description="Whitelist exclusiva de bots; adicionador também precisa estar na whitelist humana"),
            SelectOption(label="➕ Adicionar Cargo",
                          value="add_role",
                          description="Cargo que ignora proteções"),
            SelectOption(label="🚫 Exceção de Cargo",
                          value="add_role_exception",
                          description="Este usuário não herda a whitelist dos cargos"),
            SelectOption(label="🗑️ Remover",
                          value="remove",
                          description="Remove user/cargo da whitelist"),
            SelectOption(label="🔍 Verificar alvo",
                          value="check",
                          description="Mostra se um alvo está na whitelist"),
            SelectOption(label="🎯 Alternar modo",
                          value="toggle_mode",
                          description=f"Atual: {modo}"),
            SelectOption(label="🗑️ Limpar tudo",
                          value="clear_all",
                          description="Remove TODOS da whitelist"),
        ]

        super().__init__(
            placeholder="📋 Selecione uma ação...",
            options=options, min_values=1, max_values=1,
            custom_id=f"antinuke_wl_sel_{guild_id}")

    async def callback(self, interaction: discord.Interaction):
        action = self.values[0]

        if action == "add_user":
            return await interaction.response.send_modal(
                WhitelistAddModal(self.guild_id, "user"))
        if action == "add_bot":
            return await interaction.response.send_modal(
                WhitelistAddModal(self.guild_id, "bot"))
        if action == "add_role":
            return await interaction.response.send_modal(
                WhitelistAddModal(self.guild_id, "role"))
        if action == "add_role_exception":
            return await interaction.response.send_modal(
                WhitelistAddModal(self.guild_id, "exclude_user"))
        if action == "remove":
            return await interaction.response.send_modal(
                WhitelistRemoveModal(self.guild_id))
        if action == "check":
            return await interaction.response.send_modal(
                WhitelistCheckModal(self.guild_id))

        # Alternar modo.
        if action == "toggle_mode":
            cfg = _get_panel_cfg(self.guild_id)
            atual = cfg.get("whitelist_mode", "confirm_kick")
            novo = ("can_do_everything" if atual == "confirm_kick"
                    else "confirm_kick")
            _update_panel_cfg(self.guild_id, "whitelist_mode", novo)
            view = WhitelistView(self.guild_id)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)

        # Limpar tudo.
        if action == "clear_all":
            _update_panel_cfg(self.guild_id, "whitelist_users", [])
            _update_panel_cfg(self.guild_id, "whitelist_bots", [])
            _update_panel_cfg(self.guild_id, "whitelist_roles", [])
            _update_panel_cfg(self.guild_id, "whitelist_role_exclusions", [])
            view = WhitelistView(self.guild_id)
            return await interaction.response.edit_message(
                embed=view.build_embed(), view=view)


class WhitelistAddModal(ui.Modal):
    """Modal pra adicionar user/bot/cargo na whitelist."""

    def __init__(self, guild_id: int, kind: str):
        titles = {
            "user": "➕ Adicionar User",
            "bot": "➕ Adicionar Bot",
            "role": "➕ Adicionar Cargo",
            "exclude_user": "🚫 Exceção de Cargo",
        }
        super().__init__(title=titles.get(kind, "➕ Adicionar"))
        self.guild_id = guild_id
        self.kind = kind

        labels = {
            "user": "ID do usuário",
            "bot": "ID do bot",
            "role": "ID do cargo",
            "exclude_user": "ID do usuário",
        }
        self.input = ui.TextInput(
            label=labels.get(kind, "ID"),
            placeholder="Ex: 123456789012345678",
            required=True, max_length=25)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        if not await _panel_interaction_allowed(interaction):
            return
        try:
            target = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ ID inválido."), ephemeral=True)

        # Atômico via $addToSet (evita race e perda de entradas).
        if AntiNukeManager:
            if self.kind == "user":
                AntiNukeManager.add_to_list(self.guild_id, "whitelist_users", target)
            elif self.kind == "bot":
                AntiNukeManager.add_to_list(self.guild_id, "whitelist_bots", target)
            elif self.kind == "role":
                AntiNukeManager.add_to_list(self.guild_id, "whitelist_roles", target)
            elif self.kind == "exclude_user":
                AntiNukeManager.add_to_list(
                    self.guild_id, "whitelist_role_exclusions", target)
        else:
            # Fallback (não deveria acontecer).
            cfg = _get_panel_cfg(self.guild_id)
            if self.kind == "user":
                wl = list(cfg.get("whitelist_users", []))
                if target not in wl:
                    wl.append(target)
                _update_panel_cfg(self.guild_id, "whitelist_users", wl)
            elif self.kind == "bot":
                wl = list(cfg.get("whitelist_bots", []))
                if target not in wl:
                    wl.append(target)
                _update_panel_cfg(self.guild_id, "whitelist_bots", wl)
            elif self.kind == "role":
                wl = list(cfg.get("whitelist_roles", []))
                if target not in wl:
                    wl.append(target)
                _update_panel_cfg(self.guild_id, "whitelist_roles", wl)
            elif self.kind == "exclude_user":
                wl = list(cfg.get("whitelist_role_exclusions", []))
                if target not in wl:
                    wl.append(target)
                _update_panel_cfg(self.guild_id, "whitelist_role_exclusions", wl)

        view = WhitelistView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)


class WhitelistRemoveModal(ui.Modal):
    """Modal pra remover da whitelist."""

    def __init__(self, guild_id: int):
        super().__init__(title="🗑️ Remover da Whitelist")
        self.guild_id = guild_id
        self.input = ui.TextInput(
            label="ID pra remover",
            placeholder="Ex: 123456789012345678",
            required=True, max_length=25)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        if not await _panel_interaction_allowed(interaction):
            return
        try:
            target = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ ID inválido."), ephemeral=True)

        # Remove de TODAS as listas de forma atômica ($pull).
        if AntiNukeManager:
            AntiNukeManager.remove_from_list(self.guild_id, "whitelist_users", target)
            AntiNukeManager.remove_from_list(self.guild_id, "whitelist_bots", target)
            AntiNukeManager.remove_from_list(self.guild_id, "whitelist_roles", target)
            AntiNukeManager.remove_from_list(
                self.guild_id, "whitelist_role_exclusions", target)
        else:
            cfg = _get_panel_cfg(self.guild_id)
            for key in ("whitelist_users", "whitelist_bots", "whitelist_roles", "whitelist_role_exclusions"):
                wl = [int(x) for x in cfg.get(key, []) if int(x) != target]
                _update_panel_cfg(self.guild_id, key, wl)

        view = WhitelistView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)
        await interaction.followup.send(
            f"✅ `{target}` removido de todas as listas.", ephemeral=True)


class WhitelistCheckModal(ui.Modal):
    """Modal pra verificar se alvo está na whitelist."""

    def __init__(self, guild_id: int):
        super().__init__(title="🔍 Verificar Alvo")
        self.guild_id = guild_id
        self.input = ui.TextInput(
            label="ID do alvo",
            placeholder="Ex: 123456789012345678",
            required=True, max_length=25)
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target = int(self.input.value.strip())
        except ValueError:
            return await interaction.response.send_message(
                embed=embed_error("❌ ID inválido."), ephemeral=True)

        cfg = _get_panel_cfg(self.guild_id)
        def _ids(values):
            normalized = set()
            for value in values or []:
                try:
                    normalized.add(int(value))
                except (TypeError, ValueError):
                    continue
            return normalized
        in_bots = target in _ids(cfg.get("whitelist_bots", []))
        in_users = target in _ids(cfg.get("whitelist_users", []))
        in_roles = target in _ids(cfg.get("whitelist_roles", []))
        role_exception = target in _ids(cfg.get("whitelist_role_exclusions", []))

        texto = (
            f"**Alvo:** `{target}`\n\n"
            f"🤖 Whitelist Bots: {'✅ SIM' if in_bots else '❌ NÃO'}\n"
            f"👤 Whitelist Users: {'✅ SIM' if in_users else '❌ NÃO'}\n"
            f"👑 Whitelist Cargos: {'✅ SIM' if in_roles else '❌ NÃO'}\n"
            f"🚫 Exceção de Cargo: {'✅ SIM' if role_exception else '❌ NÃO'}"
        )
        await interaction.response.send_message(
            embed=embed_info(texto), ephemeral=True)


class WhitelistView(ui.View):
    """Sub-painel whitelist (v1.7 completa). Só admin/managers."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id

        # Select principal.
        self.add_item(WhitelistActionSelect(guild_id))

        # Botão de permissões (row 1).
        btn_perm = ui.Button(
            label="🔐 Permissões", style=ButtonStyle.primary,
            custom_id=f"antinuke_wl_perm_{guild_id}", row=1)
        btn_perm.callback = self._open_permissions
        self.add_item(btn_perm)

        # Botão voltar (row 1).
        btn_back = ui.Button(
            label="⬅️ Voltar", style=ButtonStyle.secondary,
            custom_id=f"antinuke_wl_back_{guild_id}", row=1)
        btn_back.callback = self._back
        self.add_item(btn_back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _panel_interaction_allowed(interaction)

    def build_embed(self) -> discord.Embed:
        cfg = _get_panel_cfg(self.guild_id)
        users = cfg.get("whitelist_users", [])
        bots = cfg.get("whitelist_bots", [])
        roles = cfg.get("whitelist_roles", [])
        exclusions = cfg.get("whitelist_role_exclusions", [])
        modo = cfg.get("whitelist_mode", "confirm_kick")

        embed = discord.Embed(
            title="📋 Whitelist",
            description=("Adicione humanos, bots e cargos separadamente.\n"
                         "Quem adiciona um bot precisa estar na whitelist humana; o bot também precisa ser incluído na whitelist de bots.\n"
                         "Use Exceção de Cargo para retirar a imunidade herdada de uma pessoa específica."),
            color=_get_panel_color(self.guild_id),
            timestamp=_now())

        embed.add_field(name=f"👤 Users ({len(users)})",
                         value="\n".join(f"• <@{u}>" for u in users[:10]) or "*vazio*",
                         inline=False)
        embed.add_field(name=f"🤖 Bots ({len(bots)})",
                         value="\n".join(f"• <@{b}>" for b in bots[:10]) or "*vazio*",
                         inline=False)
        embed.add_field(name=f"👑 Cargos ({len(roles)})",
                         value="\n".join(f"• <@&{r}>" for r in roles[:10]) or "*vazio*",
                         inline=False)
        embed.add_field(name=f"🚫 Exceções de cargos ({len(exclusions)})",
                         value="\n".join(f"• <@{u}>" for u in exclusions[:10]) or "*vazio*",
                         inline=False)

        if modo == "can_do_everything":
            embed.add_field(name="🎯 Modo Atual: **Pode tudo**",
                             value="Whitelisted ignora TODAS as proteções.",
                             inline=False)
        else:
            embed.add_field(name="🎯 Modo Atual: **Confirma expulsão**",
                             value=("Whitelisted nunca é banido. Só expulso "
                                    "com confirmação via DM."),
                             inline=False)

        embed.set_footer(text="Stumble Hit AntiRaid  •  Anti-Raid")
        return embed

    async def _open_permissions(self, interaction: discord.Interaction):
        view = AntinukePermissionsView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)

    async def _back(self, interaction: discord.Interaction):
        _PANEL_PAGES[interaction.message.id] = 0
        view = AntiRaidPanelView(self.guild_id, page=0)
        cfg = _get_panel_cfg(self.guild_id)
        embed = _build_main_embed(cfg, interaction.guild, page=0)
        await interaction.response.edit_message(embed=embed, view=view)


# ============================================================
# BLOCO 25 — SUB-PAINEL: HISTÓRICO (com fix)
# ============================================================

class HistoryView(ui.View):
    """Sub-painel de histórico de ações do anti-nuke."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        btn_back = ui.Button(
            label="⬅️ Voltar", style=ButtonStyle.secondary,
            custom_id=f"antinuke_hist_back_{guild_id}", row=0)
        btn_back.callback = self._back
        self.add_item(btn_back)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return await _panel_interaction_allowed(interaction)

    def build_embed(self) -> discord.Embed:
        db = get_connection()
        logs = list(db["antinuke_logs"].find(
            {"guild_id": self.guild_id}
        ).sort("timestamp", -1).limit(10))

        embed = discord.Embed(
            title="📜 Histórico",
            description=(f"Últimas **{len(logs)}** ações do anti-nuke."
                         if logs else "*Nenhum registro.*"),
            color=_get_panel_color(self.guild_id),
            timestamp=_now())
        if not logs:
            return embed

        # Pega o tempo atual (aware).
        now_utc = discord.utils.utcnow()

        for i, log in enumerate(logs, 1):
            # FIX v1.5: normaliza timestamp (naive → aware).
            ts = _normalize_timestamp(log.get("timestamp"))

            if ts is not None:
                try:
                    delta = (now_utc - ts).total_seconds()
                    if delta < 60:
                        ts_str = f"há {int(delta)}s"
                    elif delta < 3600:
                        ts_str = f"há {int(delta // 60)}min"
                    elif delta < 86400:
                        ts_str = f"há {int(delta // 3600)}h"
                    else:
                        ts_str = f"há {int(delta // 86400)}d"
                except Exception:
                    ts_str = "?"
            else:
                ts_str = "?"

            action = log.get("action_type", "?")
            user_id = log.get("user_id", 0)
            reason = (log.get("reason", "—") or "—")[:150]
            sev = str(log.get("severity", "?")).upper()
            reverted = len(log.get("reverted", []))

            value = (f"**Alvo:** <@{user_id}>\n"
                     f"**Motivo:** {reason}\n"
                     f"**Severidade:** `{sev}`")
            if reverted:
                value += f"\n**Restaurado:** `{reverted}` itens"

            embed.add_field(name=f"`{i}.` {action} — {ts_str}",
                             value=value, inline=False)

        embed.set_footer(text="Stumble Hit AntiRaid  •  Anti-Raid")
        return embed

    async def _back(self, interaction: discord.Interaction):
        _PANEL_PAGES[interaction.message.id] = 0
        view = AntiRaidPanelView(self.guild_id, page=0)
        cfg = _get_panel_cfg(self.guild_id)
        embed = _build_main_embed(cfg, interaction.guild, page=0)
        await interaction.response.edit_message(embed=embed, view=view)


# ============================================================
# BLOCO 26 — VIEW DO PAINEL PRINCIPAL
# ============================================================

class AntiRaidPanelView(ui.View):
    """
    View do painel principal (persistente).

    Row 0: select de proteções (custom_id).
    Row 1: ◀️ ▶️ navegação.
    Row 2: 🔒 Lockdown | 📋 Whitelist | 🔐 Permissões | 📜 Histórico | 🗑️ Fechar.

    interaction_check: só admin/managers — evita abuso por membro comum.
    """

    def __init__(self, guild_id: int, page: int = 0):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.page = page
        total_pages = _get_total_pages()

        # Row 0: select de proteções (com custom_id persistente).
        self.add_item(MainProtectionSelect(guild_id, page=page))

        # Row 1: navegação (só se tiver mais de 1 página).
        if page > 0:
            self.add_item(PrevPageButton(guild_id, page))
        if page < total_pages - 1:
            self.add_item(NextPageButton(guild_id, page))

        # Row 2: 5 botões rápidos (todos com custom_id).
        btn_lock = ui.Button(
            label="🔒 Lockdown", style=ButtonStyle.danger,
            custom_id=f"antinuke_btn_lock_{guild_id}", row=2)
        btn_lock.callback = self._open_lockdown
        self.add_item(btn_lock)

        btn_wl = ui.Button(
            label="📋 Whitelist", style=ButtonStyle.primary,
            custom_id=f"antinuke_btn_wl_{guild_id}", row=2)
        btn_wl.callback = self._open_whitelist
        self.add_item(btn_wl)

        btn_perm = ui.Button(
            label="🔐 Permissões", style=ButtonStyle.primary,
            custom_id=f"antinuke_btn_perm_{guild_id}", row=2)
        btn_perm.callback = self._open_permissions
        self.add_item(btn_perm)

        btn_hist = ui.Button(
            label="📜 Histórico", style=ButtonStyle.secondary,
            custom_id=f"antinuke_btn_hist_{guild_id}", row=2)
        btn_hist.callback = self._open_history
        self.add_item(btn_hist)

        btn_close = ui.Button(
            label="🗑️ Fechar", style=ButtonStyle.secondary,
            custom_id=f"antinuke_btn_close_{guild_id}", row=2)
        btn_close.callback = self._close
        self.add_item(btn_close)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Bloqueia quem não pode gerenciar o anti-nuke."""
        return await _panel_interaction_allowed(interaction)

    async def _open_lockdown(self, interaction: discord.Interaction):
        view = LockdownView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)

    async def _open_whitelist(self, interaction: discord.Interaction):
        view = WhitelistView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)

    async def _open_permissions(self, interaction: discord.Interaction):
        view = AntinukePermissionsView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)

    async def _open_history(self, interaction: discord.Interaction):
        view = HistoryView(self.guild_id)
        await interaction.response.edit_message(
            embed=view.build_embed(), view=view)

    async def _close(self, interaction: discord.Interaction):
        _unregister_panel(interaction.channel.id, interaction.message.id)
        try:
            await interaction.message.delete()
        except Exception:
            await interaction.response.send_message(
                embed=embed_info("Painel fechado."), ephemeral=True)


# ============================================================
# BLOCO 27 — COG PRINCIPAL
# ============================================================

class AntiNukePanelCommands(commands.Cog):
    """Cog do painel interativo do anti-nuke."""

    def __init__(self, bot):
        self.bot = bot
        # Inicia a task de auto-refresh.
        self._auto_refresh.start()

    def cog_unload(self):
        """Cancela a task ao descarregar."""
        try:
            self._auto_refresh.cancel()
        except Exception:
            pass

    @tasks.loop(seconds=30)
    async def _auto_refresh(self):
        """Roda a cada 30s e atualiza todos os painéis ativos."""
        if not _ACTIVE_PANELS:
            return
        # Pega uma "foto" dos painéis ativos (evita mudar durante o loop).
        to_refresh = list(_ACTIVE_PANELS.items())
        # Roda todos em paralelo.
        tasks = [
            self._refresh_one(ch, msg, gid)
            for (ch, msg), gid in to_refresh
        ]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @_auto_refresh.before_loop
    async def _before_auto_refresh(self):
        """Espera o bot ficar pronto e restaura painéis + Views persistentes."""
        await self.bot.wait_until_ready()
        await self._restore_persistent_panels()

    async def _restore_persistent_panels(self):
        """Após restart: carrega painéis do Mongo, re-registra Views
        e repõe no _ACTIVE_PANELS para o auto-refresh funcionar.
        """
        try:
            db = get_connection()
            docs = list(db["antinuke_active_panels"].find({}))
        except Exception:
            return

        for doc in docs:
            try:
                guild_id = int(doc["guild_id"])
                channel_id = int(doc["channel_id"])
                message_id = int(doc["message_id"])
                channel = self.bot.get_channel(channel_id)
                if not channel:
                    _unregister_panel(channel_id, message_id)
                    continue
                try:
                    message = await channel.fetch_message(message_id)
                except Exception:
                    _unregister_panel(channel_id, message_id)
                    continue
                if message.author.id != self.bot.user.id:
                    _unregister_panel(channel_id, message_id)
                    continue

                # Re-anexa a View persistente (timeout=None + custom_ids).
                page = _PANEL_PAGES.get(message_id, 0)
                view = AntiRaidPanelView(guild_id, page=page)
                try:
                    self.bot.add_view(view, message_id=message_id)
                except Exception:
                    pass

                _ACTIVE_PANELS[(channel_id, message_id)] = guild_id
            except Exception:
                continue

    async def _refresh_one(self, channel_id: int, message_id: int,
                            guild_id: int):
        """Atualiza UM painel."""
        try:
            # Pega o canal.
            channel = self.bot.get_channel(channel_id)
            if not channel:
                _unregister_panel(channel_id, message_id)
                return

            # Pega a mensagem.
            try:
                message = await channel.fetch_message(message_id)
            except discord.NotFound:
                # Mensagem foi deletada → remove do registro.
                _unregister_panel(channel_id, message_id)
                return
            except Exception:
                return

            # Confere se é do próprio bot.
            if message.author.id != self.bot.user.id:
                _unregister_panel(channel_id, message_id)
                return

            guild = message.guild
            if not guild:
                _unregister_panel(channel_id, message_id)
                return

            # Pega a página atual (default 0).
            page = _PANEL_PAGES.get(message_id, 0)

            # Reconstrói o embed principal.
            cfg = _get_panel_cfg(guild_id)
            new_embed = _build_main_embed(cfg, guild, page=page)

            # Checa se mudou algo (pra não editar à toa).
            if message.embeds:
                old = message.embeds[0]
                if (old.description == new_embed.description
                        and len(old.fields) == len(new_embed.fields)):
                    same = all(
                        of.name == nf.name and of.value == nf.value
                        for of, nf in zip(old.fields, new_embed.fields))
                    if same:
                        return  # nada mudou, pula

            # Edita a mensagem (reanexa View para manter botões vivos).
            try:
                page = _PANEL_PAGES.get(message_id, 0)
                view = AntiRaidPanelView(guild_id, page=page)
                await message.edit(embed=new_embed, view=view)
            except discord.NotFound:
                _unregister_panel(channel_id, message_id)
            except Exception:
                pass
        except Exception:
            pass

    @commands.group(name="antinuke", aliases=["an"],
                     invoke_without_command=True)
    async def antinuke(self, ctx, feature: str = None, state: str = None,
                        *, extra: str = None):
        """Sem args: abre painel. Configuração APENAS pelo embed."""
        # Config por texto DESATIVADA — tudo só pelo painel interativo.
        cfg = _get_panel_cfg(ctx.guild.id)
        author = ctx.author
        is_owner = ctx.guild.owner_id == author.id
        managers = {int(x) for x in cfg.get("antinuke_managers_users", []) if x is not None}
        manager_roles = {int(x) for x in cfg.get("antinuke_managers_roles", []) if x is not None}
        is_manager = author.id in managers or any(
            role.id in manager_roles for role in getattr(author, "roles", []))
        is_admin_allowed = (not cfg.get("antinuke_require_manager", True)
                            and getattr(getattr(author, "guild_permissions", None), "administrator", False))
        if not (is_owner or is_manager or is_admin_allowed):
            return await ctx.send(embed=embed_error(
                "❌ Você não tem permissão para abrir o painel Anti-Nuke."))
        if feature:
            return await ctx.send(embed=embed_warning(
                "⚠️ **Configuração do Anti-Nuke só pelo painel embed.**\n\n"
                "Use `>antinuke` (sem argumentos) para abrir o painel.\n"
                "Botões e menus: proteções, whitelist, thresholds, punição, etc.\n\n"
                "Comandos de texto foram desativados para evitar inconsistência "
                "e punições indevidas por config errada."))

        # Verifica se o painel pode abrir aqui.
        if not _panel_channel_check(ctx.guild.id, ctx.channel.id):
            restricted = _get_restricted_channel(ctx.guild.id)
            return await ctx.send(embed=embed_error(
                f"❌ O painel só pode ser aberto em <#{restricted}>."))

        # Monta o painel.
        cfg = _get_panel_cfg(ctx.guild.id)
        view = AntiRaidPanelView(ctx.guild.id, page=0)
        embed = _build_main_embed(cfg, ctx.guild, page=0)

        # Deleta o comando do user.
        try:
            await ctx.message.delete()
        except Exception:
            pass

        # Envia o painel.
        msg = await ctx.send(embed=embed, view=view)

        # Registra View persistente (sobrevive a restart) + auto-refresh.
        try:
            self.bot.add_view(view, message_id=msg.id)
        except Exception:
            pass
        _register_panel(ctx.guild.id, ctx.channel.id, msg.id)
        _PANEL_PAGES[msg.id] = 0

    # Subcomando legado desregistrado: restrições/configuração do Anti-Nuke
    # não são mais alteradas por comandos de texto.
    async def antinuke_canal(self, ctx, *, target: str = None):
        """Restringe o painel a um canal específico."""
        if not target:
            restricted = _get_restricted_channel(ctx.guild.id)
            if restricted:
                return await ctx.send(embed=embed_info(
                    f"🔒 Painel restrito a <#{restricted}>."))
            return await ctx.send(embed=embed_info(
                "🔓 Painel pode ser aberto em qualquer canal."))

        # Remove restrição.
        if target.lower() in ("remove", "remover", "del", "delete"):
            _set_restricted_channel(ctx.guild.id, None)
            return await ctx.send(embed=embed_success(
                "🔓 Restrição removida!"))

        # Precisa ser menção de canal.
        if not (target.startswith("<#") and target.endswith(">")):
            return await ctx.send(embed=embed_error(
                "❌ Use: `>antinuke canal #canal` ou `remove`"))

        ch_id = int(target[2:-1])
        channel = ctx.guild.get_channel(ch_id)
        if not channel:
            return await ctx.send(embed=embed_error(
                "❌ Canal não encontrado."))

        _set_restricted_channel(ctx.guild.id, ch_id)
        await ctx.send(embed=embed_success(
            f"🔒 Painel restrito a {channel.mention}."))


# ============================================================
# BLOCO 28 — SETUP
# ============================================================

async def setup(bot):
    """Chamado pelo main.py pra carregar esse cog."""
    if bot.get_cog("AntiNukePanelCommands") is None:
        await bot.add_cog(AntiNukePanelCommands(bot))