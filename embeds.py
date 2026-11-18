# ============================================================
# EMBEDS.PY - v6.0 (BRAND POR GUILD)
# ============================================================
# Este arquivo cria os "embeds" (aquelas caixinhas coloridas
# que o bot manda no Discord). Também aplica a "marca"
# customizada de cada servidor (cor/footer configurados
# com >brandingcolor e >brandingfooter).

import discord
from datetime import datetime
from typing import Optional

from utils import TTLCache

# ─── Cores padrão de cada tipo de embed ───
COLOR_SUCCESS = 0x2E8B70    # verde discreto
COLOR_ERROR = 0xB94A48      # vermelho fechado
COLOR_WARNING = 0xC58B3C    # âmbar
COLOR_INFO = 0x526F9D       # azul aço
COLOR_GOLD = 0xB89A55       # dourado fosco
COLOR_BLUE = 0x526F9D       # azul aço
COLOR_NEUTRAL = 0x465268    # ardósia

# Cache de branding por servidor (evita ir no banco toda hora).
# max_size=200 = cabe 200 servidores. ttl=60 = expira em 60s.
_brand_cache = TTLCache(max_size=200, ttl=60)


def _get_brand(guild_id: Optional[int]) -> dict:
    """
    Busca a config de branding do servidor (cor e footer).
    Retorna {} se não tiver ou se guild_id for None.
    """
    if not guild_id:
        return {}
    
    # Tenta pegar do cache.
    cached = _brand_cache.get(guild_id)
    if cached is not None:
        return cached
    
    # Não tá no cache → busca do banco (só os campos color e footer).
    try:
        from database import get_connection
        doc = get_connection()["branding_config"].find_one(
            {"guild_id": guild_id}, {"color": 1, "footer": 1}) or {}
    except Exception:
        doc = {}
    
    # Guarda no cache.
    _brand_cache.set(guild_id, doc)
    return doc


def invalidate_brand(guild_id: int):
    """Limpa o cache de branding desse servidor (força recarregar)."""
    _brand_cache.invalidate(guild_id)


def _now() -> datetime:
    """Retorna a data/hora atual em UTC (padrão do Discord)."""
    return datetime.utcnow()


def _apply_brand(embed: discord.Embed, guild_id: Optional[int]) -> discord.Embed:
    """
    Aplica cor/footer customizados do servidor no embed.
    Se o servidor não configurou nada, deixa do jeito que tá.
    """
    if not guild_id:
        return embed
    
    brand = _get_brand(guild_id)
    color = brand.get("color")
    footer = brand.get("footer")
    
    # Se o servidor tem cor customizada, aplica.
    if color is not None:
        try:
            embed.color = discord.Color(int(color))
        except Exception:
            pass
    
    # Se o servidor tem footer customizado, aplica (junta com o existente).
    if footer:
        existing = embed.footer.text if embed.footer else None
        embed.set_footer(text=f"{footer} • {existing}" if existing else footer)
    
    return embed


# ─── Funções que criam embeds padronizados ───
# Cada uma cria um embed com título, cor e timestamp certos.
# São usadas em TODO o bot pra manter consistência visual.

def _message_embed(title: str, desc: str, color: int) -> discord.Embed:
    """Resposta curta, sem excesso de ícones ou carimbos de horário."""
    return discord.Embed(title=title, description=desc, color=color)


def embed_success(desc: str) -> discord.Embed:
    """Resposta de sucesso em verde discreto."""
    return _message_embed("Concluído", desc, COLOR_SUCCESS)


def embed_error(desc: str) -> discord.Embed:
    """Resposta de erro em vermelho fechado."""
    return _message_embed("Não foi possível concluir", desc, COLOR_ERROR)


def embed_warning(desc: str) -> discord.Embed:
    """Aviso em âmbar, reservado para situações que exigem atenção."""
    return _message_embed("Atenção", desc, COLOR_WARNING)


def embed_info(desc: str) -> discord.Embed:
    """Informação neutra em azul aço."""
    return _message_embed("Informação", desc, COLOR_INFO)


def embed_guild(guild_id: int, title: str, desc: str, color: int = COLOR_INFO) -> discord.Embed:
    """
    Embed genérico que JÁ APLICA a marca do servidor.
    Use quando quiser que a cor/footer customizados apareçam.
    """
    e = discord.Embed(title=title, description=desc, color=color)
    return _apply_brand(e, guild_id)