# ============================================================
# COMMANDS_EMBED_ACTIONS.PY - v2.0
# RUNTIME DO SISTEMA DE PAINÉIS INTERATIVOS
# ============================================================
# O QUE ESSE ARQUIVO FAZ:
# - Executa ações quando alguém clica num botão/select
# - Reaction roles (emoji → cargo, estilo Carl-bot)
# - Selects encadeados (select que abre outro painel)
# - Multi-select (escolher vários no mesmo select)
# - Condicionais (se cargo X → ação Y)
# - Webhook de envio (opcional, mais rápido)
# - Listener global de interações
#
# CONVERSA COM O CORE (commands_embed_builder.py):
# - O CORE DEFINE painéis e salva no MongoDB
# - ESTE arquivo LÊ os painéis do MongoDB e executa as ações
# - Ambos compartilham o formato JSON das coleções
# - Importamos constantes e helpers do CORE
# ============================================================

import asyncio
import io
import json
import random
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import discord
from discord.ext import commands
from discord import ui, ButtonStyle, SelectOption

from database import get_connection
from embeds import embed_success, embed_error, embed_warning, embed_info

# ─── Imports do CORE ───
# Importamos as constantes e helpers do commands_embed_builder.py.
# Isso evita duplicar código e mantém as definições sincronizadas.
from commands_embed_builder import (
    ACTION_TYPES,
    BUTTON_STYLES,
    LIMITS,
    _get_custom_vars,
    _load_message,
    _load_panel,
    _log_panel_action,
    _resolve_variables,
    dict_to_embed,
    embed_has_content,
)


def _now() -> datetime:
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# CACHE DE COOLDOWN (evita spam de cliques)
# ============================================================
# 📌 POR QUE ISSO?
# Se alguém clicar 20x no mesmo botão, não queremos processar 20x.
# Isso guarda o último timestamp de cada (user, component) e bloqueia
# cliques repetidos em menos de X segundos.
#
# Estrutura: {(guild_id, user_id, component_id): timestamp}

_cooldowns: Dict[Tuple[int, int, str], float] = {}
_COOLDOWN_SECONDS = 1.5


def _check_cooldown(guild_id: int, user_id: int, component_id: str) -> bool:
    """
    Diz se o user pode executar a ação (não tá em cooldown).

    Returns:
        True se pode, False se tá em cooldown.
    """
    key = (guild_id, user_id, component_id)
    now = time.time()
    last = _cooldowns.get(key, 0)
    if (now - last) < _COOLDOWN_SECONDS:
        return False
    _cooldowns[key] = now
    # Limpa entradas antigas (evita crescer infinito).
    if len(_cooldowns) > 5000:
        cutoff = now - 60
        for k in [k for k, v in _cooldowns.items() if v < cutoff]:
            del _cooldowns[k]
    return True


# ============================================================
# HELPERS DE SEGURANÇA
# ============================================================

async def _safe_reply(interaction: discord.Interaction, text: str,
                       ephemeral: bool = True):
    """
    Envia uma resposta segura (tenta response, senão followup).
    Evita erro 400 se já tiver respondido.
    """
    try:
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(text, ephemeral=ephemeral)
    except Exception:
        pass


def _parse_role_ids(raw) -> List[int]:
    """
    Converte uma string com IDs em lista de ints.

    Aceita: "123,456,789" ou "123 456 789" ou "123, 456".
    """
    if not raw:
        return []
    if isinstance(raw, list):
        return [int(x) for x in raw if str(x).isdigit()]
    text = str(raw)
    nums = re.findall(r"\d+", text)
    return [int(n) for n in nums]


def _has_role(member: discord.Member, role_id: int) -> bool:
    """Diz se o member tem um cargo específico."""
    if not member:
        return False
    return member.get_role(int(role_id)) is not None


# ============================================================
# EXECUTOR DE AÇÕES (o cérebro)
# ============================================================

async def _execute_action(
    interaction: discord.Interaction,
    action: dict,
    *,
    panel: dict,
    component: dict,
    bot,
    selected_value: str = None,
):
    """
    Executa uma ação configurada em um componente.

    Args:
        interaction: interação do Discord
        action: dict com {"type": ..., ...parâmetros}
        panel: painel completo
        component: componente que foi acionado
        bot: instância do bot (pra acessar cogs)
        selected_value: valor selecionado (se for select)
    """
    # Sem ação → não faz nada.
    if not action:
        return

    atype = action.get("type", "none")
    guild = interaction.guild
    user = interaction.user
    channel = interaction.channel

    # Carrega variáveis customizadas.
    custom_vars = _get_custom_vars(guild.id) if guild else {}

    # Helper: substitui variáveis num texto.
    def _r(text: str) -> str:
        return _resolve_variables(
            text or "", guild=guild, user=user,
            channel=channel, custom_vars=custom_vars,
            extra={"selected": selected_value or ""})

    # ═══════════════════════════════════════════════════════════
    # AÇÕES BÁSICAS
    # ═══════════════════════════════════════════════════════════

    # ─── NONE (só visual) ───
    if atype == "none":
        try:
            await interaction.response.defer()
        except Exception:
            pass
        return

    # ─── REPLY_TEXT (resposta ephemeral) ───
    if atype == "reply_text":
        text = _r(action.get("text") or "✅ OK!")[:2000]
        await _safe_reply(interaction, text)
        return

    # ─── REPLY_EMBED (resposta ephemeral com embed) ───
    if atype == "reply_embed":
        msg_name = action.get("message_name")
        msg = _load_message(guild.id, msg_name) if msg_name else None
        embed_data = (msg or {}).get("embed") or action.get("embed") or {}
        embed = dict_to_embed(embed_data, guild=guild, user=user,
                                channel=channel, custom_vars=custom_vars)
        content = _r((msg or {}).get("content") or action.get("content") or "")[:2000]
        try:
            if embed_has_content(embed):
                await interaction.response.send_message(
                    content=content or None, embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(
                    content=content or "✅", ephemeral=True)
        except Exception:
            try:
                if embed_has_content(embed):
                    await interaction.followup.send(
                        content=content or None, embed=embed, ephemeral=True)
                else:
                    await interaction.followup.send(
                        content=content or "✅", ephemeral=True)
            except Exception:
                pass
        return

    # ═══════════════════════════════════════════════════════════
    # ENVIO DE MENSAGENS
    # ═══════════════════════════════════════════════════════════

    # ─── SEND_TO_CHANNEL (envia num canal específico) ───
    if atype == "send_to_channel":
        ch_id = action.get("channel_id")
        target = guild.get_channel(int(ch_id)) if ch_id else None
        if not target:
            return await _safe_reply(interaction, "❌ Canal não encontrado.")
        msg_name = action.get("message_name")
        msg = _load_message(guild.id, msg_name) if msg_name else None
        embed_data = (msg or {}).get("embed") or action.get("embed") or {}
        embed = dict_to_embed(embed_data, guild=guild, user=user,
                                channel=channel, custom_vars=custom_vars)
        content = _r((msg or {}).get("content") or action.get("content") or "")[:2000]
        try:
            if embed_has_content(embed):
                await target.send(content=content or None, embed=embed)
            else:
                await target.send(content=content or "📤")
            await _safe_reply(interaction, "✅ Mensagem enviada!", ephemeral=True)
        except Exception as e:
            await _safe_reply(interaction, f"❌ Erro: {e}", ephemeral=True)
        return

    # ─── SEND_MESSAGE (envia no canal atual) ───
    if atype == "send_message":
        msg_name = action.get("message_name")
        msg = _load_message(guild.id, msg_name) if msg_name else None
        embed_data = (msg or {}).get("embed") or action.get("embed") or {}
        embed = dict_to_embed(embed_data, guild=guild, user=user,
                                channel=channel, custom_vars=custom_vars)
        content = _r((msg or {}).get("content") or action.get("content") or "")[:2000]
        try:
            if embed_has_content(embed):
                await channel.send(content=content or None, embed=embed)
            else:
                await channel.send(content=content or "✉️")
            await _safe_reply(interaction, "✅ Mensagem enviada!", ephemeral=True)
        except Exception as e:
            await _safe_reply(interaction, f"❌ Erro: {e}", ephemeral=True)
        return

    # ─── SEND_DM (manda DM pro user) ───
    if atype == "send_dm":
        msg_name = action.get("message_name")
        msg = _load_message(guild.id, msg_name) if msg_name else None
        embed_data = (msg or {}).get("embed") or action.get("embed") or {}
        embed = dict_to_embed(embed_data, guild=guild, user=user,
                                channel=channel, custom_vars=custom_vars)
        content = _r((msg or {}).get("content") or action.get("content") or "")[:2000]
        try:
            if embed_has_content(embed):
                await user.send(content=content or None, embed=embed)
            else:
                await user.send(content=content or "📨")
            await _safe_reply(interaction, "✅ DM enviada!", ephemeral=True)
        except Exception:
            await _safe_reply(interaction,
                                "❌ Não consegui enviar DM (bloqueado?).",
                                ephemeral=True)
        return

    # ─── REDIRECT_CHANNEL (link do canal) ───
    if atype == "redirect_channel":
        ch_id = action.get("channel_id")
        target = guild.get_channel(int(ch_id)) if ch_id else None
        if not target:
            return await _safe_reply(interaction, "❌ Canal não encontrado.")
        text = _r(action.get("text") or f"📎 Vá para {target.mention}")
        await _safe_reply(interaction, text[:2000], ephemeral=True)
        return

    # ═══════════════════════════════════════════════════════════
    # CARGOS (simples)
    # ═══════════════════════════════════════════════════════════

    # ─── GIVE_ROLE ───
    if atype == "give_role":
        role_id = action.get("role_id")
        role = guild.get_role(int(role_id)) if role_id else None
        if not role:
            return await _safe_reply(interaction, "❌ Cargo não encontrado.")
        if role >= guild.me.top_role:
            return await _safe_reply(
                interaction, "❌ Não posso dar esse cargo (acima do meu).",
                ephemeral=True)
        try:
            member = guild.get_member(user.id)
            if not member:
                return await _safe_reply(interaction, "❌ Você não está no servidor.")
            if role in member.roles:
                await _safe_reply(
                    interaction,
                    _r(action.get("text") or f"ℹ️ Você já tem {role.mention}."),
                    ephemeral=True)
                return
            await member.add_roles(role, reason=f"EmbedBuilder: {panel.get('name')}")
            await _safe_reply(
                interaction,
                _r(action.get("text") or f"✅ Cargo {role.mention} recebido!"),
                ephemeral=True)
        except Exception as e:
            await _safe_reply(interaction, f"❌ Erro: {e}", ephemeral=True)
        return

    # ─── REMOVE_ROLE ───
    if atype == "remove_role":
        role_id = action.get("role_id")
        role = guild.get_role(int(role_id)) if role_id else None
        if not role:
            return await _safe_reply(interaction, "❌ Cargo não encontrado.")
        if role >= guild.me.top_role:
            return await _safe_reply(interaction, "❌ Cargo acima do meu.")
        try:
            member = guild.get_member(user.id)
            if not member:
                return await _safe_reply(interaction, "❌ Você não está no servidor.")
            if role not in member.roles:
                return await _safe_reply(
                    interaction,
                    _r(action.get("text") or "ℹ️ Você não tem esse cargo."),
                    ephemeral=True)
            await member.remove_roles(role, reason=f"EmbedBuilder: {panel.get('name')}")
            await _safe_reply(
                interaction,
                _r(action.get("text") or f"✅ Cargo {role.mention} removido!"),
                ephemeral=True)
        except Exception as e:
            await _safe_reply(interaction, f"❌ Erro: {e}", ephemeral=True)
        return

    # ─── TOGGLE_ROLE ───
    if atype == "toggle_role":
        role_id = action.get("role_id")
        role = guild.get_role(int(role_id)) if role_id else None
        if not role:
            return await _safe_reply(interaction, "❌ Cargo não encontrado.")
        if role >= guild.me.top_role:
            return await _safe_reply(interaction, "❌ Cargo acima do meu.")
        try:
            member = guild.get_member(user.id)
            if not member:
                return await _safe_reply(interaction, "❌ Você não está no servidor.")
            if role in member.roles:
                await member.remove_roles(role, reason="EmbedBuilder toggle")
                await _safe_reply(
                    interaction,
                    _r(action.get("text") or f"➖ Cargo {role.mention} removido."),
                    ephemeral=True)
            else:
                await member.add_roles(role, reason="EmbedBuilder toggle")
                await _safe_reply(
                    interaction,
                    _r(action.get("text") or f"➕ Cargo {role.mention} adicionado."),
                    ephemeral=True)
        except Exception as e:
            await _safe_reply(interaction, f"❌ Erro: {e}", ephemeral=True)
        return

    # ─── TEMP_ROLE ───
    if atype == "temp_role":
        role_id = action.get("role_id")
        seconds = int(action.get("seconds", 60))
        role = guild.get_role(int(role_id)) if role_id else None
        if not role:
            return await _safe_reply(interaction, "❌ Cargo não encontrado.")
        if role >= guild.me.top_role:
            return await _safe_reply(interaction, "❌ Cargo acima do meu.")
        try:
            member = guild.get_member(user.id)
            if not member:
                return await _safe_reply(interaction, "❌ Você não está no servidor.")
            await member.add_roles(role, reason="EmbedBuilder temp_role")
            await _safe_reply(
                interaction,
                _r(action.get("text") or f"⏳ Cargo {role.mention} por {seconds}s!"),
                ephemeral=True)
            async def _remove_later():
                await asyncio.sleep(seconds)
                try:
                    fresh = guild.get_member(user.id)
                    if fresh and role in fresh.roles:
                        await fresh.remove_roles(role, reason="temp_role expirou")
                except Exception:
                    pass
            asyncio.create_task(_remove_later())
        except Exception as e:
            await _safe_reply(interaction, f"❌ Erro: {e}", ephemeral=True)
        return

    # ═══════════════════════════════════════════════════════════
    # 🆕 v2.0 — MULTI-ROLES
    # ═══════════════════════════════════════════════════════════

    # ─── MULTI_GIVE_ROLES ───
    if atype == "multi_give_roles":
        role_ids = _parse_role_ids(action.get("role_ids"))
        if not role_ids:
            return await _safe_reply(interaction, "❌ Nenhum cargo configurado.")
        member = guild.get_member(user.id)
        if not member:
            return await _safe_reply(interaction, "❌ Você não está no servidor.")
        applied, failed = [], []
        for rid in role_ids:
            role = guild.get_role(rid)
            if not role:
                failed.append(rid)
                continue
            if role >= guild.me.top_role:
                failed.append(rid)
                continue
            if role in member.roles:
                continue
            try:
                await member.add_roles(role, reason="EmbedBuilder multi_give")
                applied.append(role.name)
            except Exception:
                failed.append(rid)
        msg = _r(action.get("text") or "")
        if not msg:
            if applied:
                msg = f"✅ {len(applied)} cargos adicionados!"
            elif failed:
                msg = f"❌ Falha ao aplicar cargos."
            else:
                msg = "ℹ️ Você já tinha todos esses cargos."
        await _safe_reply(interaction, msg, ephemeral=True)
        return

    # ─── MULTI_REMOVE_ROLES ───
    if atype == "multi_remove_roles":
        role_ids = _parse_role_ids(action.get("role_ids"))
        if not role_ids:
            return await _safe_reply(interaction, "❌ Nenhum cargo configurado.")
        member = guild.get_member(user.id)
        if not member:
            return await _safe_reply(interaction, "❌ Você não está no servidor.")
        removed = 0
        for rid in role_ids:
            role = guild.get_role(rid)
            if not role or role >= guild.me.top_role:
                continue
            if role not in member.roles:
                continue
            try:
                await member.remove_roles(role, reason="EmbedBuilder multi_remove")
                removed += 1
            except Exception:
                pass
        msg = _r(action.get("text") or "")
        if not msg:
            msg = f"➖ {removed} cargos removidos!"
        await _safe_reply(interaction, msg, ephemeral=True)
        return

    # ─── MULTI_TOGGLE_ROLES ───
    if atype == "multi_toggle_roles":
        role_ids = _parse_role_ids(action.get("role_ids"))
        if not role_ids:
            return await _safe_reply(interaction, "❌ Nenhum cargo configurado.")
        member = guild.get_member(user.id)
        if not member:
            return await _safe_reply(interaction, "❌ Você não está no servidor.")
        applied, removed = 0, 0
        for rid in role_ids:
            role = guild.get_role(rid)
            if not role or role >= guild.me.top_role:
                continue
            try:
                if role in member.roles:
                    await member.remove_roles(role, reason="EmbedBuilder multi_toggle")
                    removed += 1
                else:
                    await member.add_roles(role, reason="EmbedBuilder multi_toggle")
                    applied += 1
            except Exception:
                pass
        msg = _r(action.get("text") or "")
        if not msg:
            msg = f"🔄 {applied} adicionados, {removed} removidos!"
        await _safe_reply(interaction, msg, ephemeral=True)
        return

    # ═══════════════════════════════════════════════════════════
    # 🆕 v2.0 — SELECTS ENCADEADOS
    # ═══════════════════════════════════════════════════════════

    # ─── SELECT_CASCADE (abre outro painel baseado no valor) ───
    if atype == "select_cascade":
        target_name = action.get("target_panel")
        if not target_name:
            return await _safe_reply(interaction, "❌ Painel alvo não configurado.")
        # Tenta dois formatos:
        # 1. Painel único (target_panel é um nome fixo)
        # 2. Mapa (target_panel é um dict {valor_escolhido: painel})
        # Aqui usamos o formato 1.
        other_panel = _load_panel(guild.id, target_name)
        if not other_panel:
            return await _safe_reply(
                interaction, f"❌ Painel `{target_name}` não encontrado.",
                ephemeral=True)
        embed_data = other_panel.get("embed") or {}
        embed = dict_to_embed(embed_data, guild=guild, user=user,
                                channel=channel, custom_vars=custom_vars)
        comps = _build_components_from_panel_runtime(other_panel, bot)
        view = ui.View(timeout=None)
        for c in comps:
            view.add_item(c)
        try:
            await interaction.response.send_message(
                embed=embed if embed_has_content(embed) else None,
                view=view, ephemeral=True)
        except Exception as e:
            await _safe_reply(interaction, f"❌ Erro: {e}", ephemeral=True)
        return

    # ─── OPEN_PANEL (abre outro painel) ───
    if atype == "open_panel":
        pname = action.get("panel_name") or action.get("target_panel")
        other = _load_panel(guild.id, pname) if pname else None
        if not other:
            return await _safe_reply(interaction, "❌ Painel não encontrado.")
        embed_data = other.get("embed") or {}
        embed = dict_to_embed(embed_data, guild=guild, user=user,
                                channel=channel, custom_vars=custom_vars)
        comps = _build_components_from_panel_runtime(other, bot)
        view = ui.View(timeout=None)
        for c in comps:
            view.add_item(c)
        try:
            await interaction.response.send_message(
                embed=embed if embed_has_content(embed) else None,
                view=view, ephemeral=True)
        except Exception as e:
            await _safe_reply(interaction, f"❌ Erro: {e}", ephemeral=True)
        return

    # ═══════════════════════════════════════════════════════════
    # 🆕 v2.0 — CONDICIONAL (if/else por cargo)
    # ═══════════════════════════════════════════════════════════

    # ─── CONDITIONAL ───
    if atype == "conditional":
        cond = action.get("condition") or {}
        cond_type = cond.get("type") or "has_role"
        cond_role = cond.get("role_id")

        member = guild.get_member(user.id)
        if not member:
            return await _safe_reply(interaction, "❌ Você não está no servidor.")

        result = False
        if cond_type == "has_role" and cond_role:
            result = _has_role(member, int(cond_role))
        elif cond_type == "not_has_role" and cond_role:
            result = not _has_role(member, int(cond_role))
        elif cond_type == "is_admin":
            result = member.guild_permissions.administrator

        # Escolhe qual ação rodar.
        branch = action.get("true_action") if result else action.get("false_action")
        if not branch:
            return await _safe_reply(
                interaction,
                _r(action.get("text") or "❌ Condição não configurada."),
                ephemeral=True)
        # Executa a sub-ação (recursivo).
        await _execute_action(interaction, branch, panel=panel,
                                component=component, bot=bot,
                                selected_value=selected_value)
        return

    # ═══════════════════════════════════════════════════════════
    # 🆕 v2.0 — OPEN_TEMPLATE
    # ═══════════════════════════════════════════════════════════

    # ─── OPEN_TEMPLATE ───
    if atype == "open_template":
        tname = action.get("template_name")
        if not tname:
            return await _safe_reply(interaction, "❌ Nome do template faltando.")
        tpl = _load_message(guild.id, tname)
        if not tpl:
            return await _safe_reply(
                interaction, f"❌ Template `{tname}` não encontrado.",
                ephemeral=True)
        embed_data = tpl.get("embed") or {}
        embed = dict_to_embed(embed_data, guild=guild, user=user,
                                channel=channel, custom_vars=custom_vars)
        content = _r(tpl.get("content") or "")[:2000]
        try:
            if embed_has_content(embed):
                await interaction.response.send_message(
                    content=content or None, embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(
                    content=content or "📋", ephemeral=True)
        except Exception as e:
            await _safe_reply(interaction, f"❌ Erro: {e}", ephemeral=True)
        return

    # ═══════════════════════════════════════════════════════════
    # ADICIONAR A LISTA
    # ═══════════════════════════════════════════════════════════

    # ─── ADD_TO_LIST ───
    if atype == "add_to_list":
        list_name = action.get("list_name") or "default"
        try:
            db = get_connection()
            db["embed_lists"].update_one(
                {"guild_id": guild.id, "list_name": list_name},
                {"$addToSet": {"entries": {
                    "user_id": user.id,
                    "timestamp": _now()}}},
                upsert=True)
            await _safe_reply(
                interaction,
                _r(action.get("text") or f"✅ Você foi adicionado à lista **{list_name}**!"),
                ephemeral=True)
        except Exception as e:
            await _safe_reply(interaction, f"❌ Erro: {e}", ephemeral=True)
        return

    # ═══════════════════════════════════════════════════════════
    # MODERAÇÃO (perigosa)
    # ═══════════════════════════════════════════════════════════

    # ─── KICK ───
    if atype == "kick":
        try:
            member = guild.get_member(user.id)
            if not member:
                return await _safe_reply(interaction, "❌ Você não está no servidor.")
            if member.top_role >= guild.me.top_role:
                return await _safe_reply(interaction, "❌ Você tem cargo acima do meu.")
            reason = _r(action.get("reason") or "EmbedBuilder kick")
            await member.kick(reason=reason)
            await _safe_reply(interaction, "👢 Você foi expulso.", ephemeral=True)
        except Exception as e:
            await _safe_reply(interaction, f"❌ Erro: {e}", ephemeral=True)
        return

    # ─── BAN ───
    if atype == "ban":
        try:
            reason = _r(action.get("reason") or "EmbedBuilder ban")
            await guild.ban(user, reason=reason, delete_message_days=1)
            await _safe_reply(interaction, "🔨 Você foi banido.", ephemeral=True)
        except Exception as e:
            await _safe_reply(interaction, f"❌ Erro: {e}", ephemeral=True)
        return

    # ─── TIMEOUT ───
    if atype == "timeout":
        try:
            member = guild.get_member(user.id)
            if not member:
                return await _safe_reply(interaction, "❌ Você não está no servidor.")
            seconds = int(action.get("seconds", 60))
            until = discord.utils.utcnow() + timedelta(seconds=seconds)
            reason = _r(action.get("reason") or "EmbedBuilder timeout")
            await member.timeout(until, reason=reason)
            await _safe_reply(
                interaction, f"⏰ Timeout de {seconds}s aplicado.",
                ephemeral=True)
        except Exception as e:
            await _safe_reply(interaction, f"❌ Erro: {e}", ephemeral=True)
        return

    # ─── WARN ───
    if atype == "warn":
        try:
            db = get_connection()
            db["warns"].insert_one({
                "guild_id": guild.id,
                "user_id": user.id,
                "moderator_id": bot.user.id,
                "reason": _r(action.get("reason") or "EmbedBuilder warn"),
                "timestamp": _now()})
            await _safe_reply(interaction, "⚠️ Aviso registrado.", ephemeral=True)
        except Exception as e:
            await _safe_reply(interaction, f"❌ Erro: {e}", ephemeral=True)
        return

    # ─── OPEN_MODAL_CUSTOM ───
    if atype == "open_modal_custom":
        cfg = action.get("modal_config") or {}
        title = (cfg.get("title") or "Formulário")[:45]
        fields_cfg = cfg.get("fields") or []
        inputs = []
        for f in fields_cfg[:5]:
            inputs.append(ui.TextInput(
                label=(f.get("label") or "Campo")[:45],
                placeholder=(f.get("placeholder") or "")[:100] or None,
                required=bool(f.get("required", True)),
                max_length=min(int(f.get("max_length", 1000)), 4000),
                style=(discord.TextStyle.paragraph
                        if f.get("style") == "paragraph"
                        else discord.TextStyle.short),
                default=(f.get("default") or None)))
        if not inputs:
            return await _safe_reply(interaction, "❌ Modal sem campos.")
        modal = _DynamicModal(title, inputs, action, panel, bot)
        try:
            await interaction.response.send_modal(modal)
        except Exception as e:
            await _safe_reply(interaction, f"❌ Erro: {e}", ephemeral=True)
        return

    # Fallback: tipo desconhecido, só defer.
    try:
        await interaction.response.defer()
    except Exception:
        pass


# ============================================================
# MODAL DINÂMICO (construído a partir de config)
# ============================================================

class _DynamicModal(ui.Modal):
    """Modal dinâmico construído a partir de config."""
    def __init__(self, title, inputs, action, panel, bot):
        super().__init__(title=title)
        self._action = action
        self._panel = panel
        self._bot = bot
        self._field_names = []
        for inp in inputs[:5]:
            self.add_item(inp)
            self._field_names.append(inp.label)

    async def on_submit(self, interaction: discord.Interaction):
        """Chamado quando o user preenche o modal e envia."""
        values = {}
        for i, child in enumerate(self.children):
            if isinstance(child, ui.TextInput):
                name = self._field_names[i] if i < len(self._field_names) else f"field_{i}"
                values[name] = child.value or ""
        submit_action = self._action.get("on_submit") or {
            "type": "reply_text", "text": "✅ Enviado!"}
        await _execute_action(
            interaction, submit_action,
            panel=self._panel, component={"id": "modal"},
            bot=self._bot,
            selected_value=json.dumps(values, ensure_ascii=False)[:100])
        try:
            _log_panel_action(
                interaction.guild.id, self._panel.get("name", "?"),
                "modal_submit", interaction.user.id,
                submit_action.get("type", "?"),
                detail=str(values)[:200])
        except Exception:
            pass


# ============================================================
# BUILD DE COMPONENTES (runtime)
# ============================================================

def _build_components_from_panel_runtime(panel, cog_ref) -> List[ui.Item]:
    """
    Monta os componentes do painel (versão runtime).

    📌 DIFERENÇA DO CORE:
    O CORE usa custom_id fixo (`eb:{panel_id}:{component_id}`).
    Aqui a gente usa o MESMO formato, garantindo que o listener
    global (on_interaction) consiga identificar o painel e o
    componente corretos.
    """
    from commands_embed_builder import _build_components_from_panel
    return _build_components_from_panel(panel, cog_ref)


# ============================================================
# COG PRINCIPAL (runtime)
# ============================================================

class EmbedActionsCommands(commands.Cog):
    """
    Cog que processa TODAS as interações dos painéis.

    📌 POR QUE UM LISTENER GLOBAL?
    Como usamos custom_ids dinâmicos (`eb:<panel_id>:<comp_id>`),
    não dá pra pré-registrar uma View pra cada combinação. Então
    usamos on_interaction pra capturar TUDO que começa com "eb:".

    📌 REACTION ROLES:
    Também escutamos on_raw_reaction_add pra processar cargos
    via reação (estilo Carl-bot).
    """
    def __init__(self, bot):
        self.bot = bot

    # ═══════════════════════════════════════════════════════════
    # LISTENER: processa cliques em botões/selects
    # ═══════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction):
        """Processa TODOS os cliques em botões/selects dos painéis."""
        # Só interações de componentes.
        if interaction.type != discord.InteractionType.component:
            return
        if not interaction.guild:
            return

        data = interaction.data or {}
        custom_id = data.get("custom_id", "")
        if not custom_id.startswith("eb:"):
            return

        # Parseia: eb:<panel_id>:<comp_id>.
        parts = custom_id.split(":")
        if len(parts) < 3:
            return
        panel_id_str = parts[1]
        comp_id = parts[2]

        # Converte pra ObjectId.
        try:
            from bson import ObjectId
            panel_id = ObjectId(panel_id_str)
        except Exception:
            return

        db = get_connection()
        panel = db["embed_panels"].find_one({"_id": panel_id})
        if not panel:
            return await _safe_reply(
                interaction, "❌ Painel não encontrado.", ephemeral=True)

        # Acha o componente.
        comp = None
        for c in panel.get("components", []):
            if c.get("id") == comp_id:
                comp = c
                break
        if not comp:
            return await _safe_reply(
                interaction, "❌ Componente não encontrado.", ephemeral=True)

        # Checa cooldown (anti-spam).
        if not _check_cooldown(interaction.guild.id,
                                interaction.user.id, comp_id):
            try:
                await interaction.response.defer()
            except Exception:
                pass
            return

        # Se for select, pega o valor selecionado.
        selected_values = []
        if comp.get("type") == "select":
            values = data.get("values", [])
            selected_values = values if values else []

        # ─── Multi-select: aplica a ação a CADA valor escolhido ───
        if len(selected_values) > 1:
            # Se o select for multivalor, executa a ação pra cada opção.
            for sel_val in selected_values:
                # Acha a opção específica.
                opt = None
                for o in comp.get("options", []):
                    if o.get("value") == sel_val:
                        opt = o
                        break
                opt_action = (opt or {}).get("action")
                if opt_action and opt_action.get("type", "none") != "none":
                    # Executa a ação da opção.
                    await _execute_action(
                        interaction, opt_action,
                        panel=panel, component=comp, bot=self.bot,
                        selected_value=sel_val)
                    _log_panel_action(
                        interaction.guild.id, panel.get("name", "?"),
                        comp_id, interaction.user.id,
                        opt_action.get("type", "?"),
                        detail=f"opt={sel_val}")
                    return
            # Se nenhuma opção teve ação própria, usa a ação do componente.
            # (cai no código de baixo)

        # ─── Select normal (1 valor) ───
        if len(selected_values) == 1:
            sel_val = selected_values[0]
            for opt in comp.get("options", []):
                if opt.get("value") == sel_val:
                    opt_action = opt.get("action")
                    if opt_action and opt_action.get("type", "none") != "none":
                        await _execute_action(
                            interaction, opt_action,
                            panel=panel, component=comp, bot=self.bot,
                            selected_value=sel_val)
                        _log_panel_action(
                            interaction.guild.id, panel.get("name", "?"),
                            comp_id, interaction.user.id,
                            opt_action.get("type", "?"),
                            detail=f"opt={sel_val}")
                        return

        # ─── Ação do componente (botão ou select sem ação própria) ───
        action = comp.get("action") or {"type": "none"}
        await _execute_action(
            interaction, action,
            panel=panel, component=comp, bot=self.bot,
            selected_value=selected_values[0] if selected_values else None)

        # Log.
        _log_panel_action(
            interaction.guild.id, panel.get("name", "?"),
            comp_id, interaction.user.id,
            action.get("type", "?"),
            detail=f"val={selected_values or ''}")

    # ═══════════════════════════════════════════════════════════
    # LISTENER: reaction roles (estilo Carl-bot)
    # ═══════════════════════════════════════════════════════════

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """
        Chamado quando alguém adiciona uma reação.

        📌 COMO FUNCIONA:
        Se a mensagem tiver uma config de reaction role, e o emoji
        estiver mapeado, o bot dá/tira o cargo.
        """
        # Ignora DMs e bots.
        if not payload.guild_id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return
        # Ignora reação do próprio bot.
        if payload.user_id == self.bot.user.id:
            return

        # Procura config de reaction role na mensagem.
        try:
            db = get_connection()
            config = db["embed_reaction_roles"].find_one({
                "guild_id": guild.id,
                "message_id": payload.message_id,
            })
        except Exception:
            return

        if not config:
            return

        # Normaliza o emoji (unicode ou custom).
        emoji_str = str(payload.emoji)
        emoji_id = payload.emoji.id if payload.emoji.is_custom_emoji() else None

        # Procura no mapping.
        mapping = config.get("mappings", [])
        target_role_id = None
        for m in mapping:
            m_emoji = m.get("emoji")
            if not m_emoji:
                continue
            # Bate por id de emoji OU por nome/unicode.
            if emoji_id and str(m_emoji) == str(emoji_id):
                target_role_id = m.get("role_id")
                break
            if str(m_emoji) == emoji_str:
                target_role_id = m.get("role_id")
                break

        if not target_role_id:
            return

        role = guild.get_role(int(target_role_id))
        if not role:
            return
        member = guild.get_member(payload.user_id)
        if not member:
            return

        # Verifica hierarquia.
        if role >= guild.me.top_role:
            return

        # Aplica o cargo.
        try:
            if role in member.roles:
                await member.remove_roles(role, reason="Reaction role (toggle)")
            else:
                await member.add_roles(role, reason="Reaction role (toggle)")
        except Exception:
            pass


# ============================================================
# COMANDOS PARA REACTION ROLES
# ============================================================

class ReactionRoleCommands(commands.Cog):
    """Comandos pra configurar reaction roles em painéis."""

    def __init__(self, bot):
        self.bot = bot

    @commands.group(name="reactionrole", aliases=["rr"],
                     invoke_without_command=True)
    @commands.has_permissions(manage_roles=True)
    async def rr(self, ctx):
        """Painel de reaction roles."""
        await ctx.send(embed=discord.Embed(
            title="😀 Reaction Roles",
            description=(
                "Configure reações em mensagens pra dar cargos "
                "automaticamente (estilo Carl-bot).\n\n"
                "**Comandos:**\n"
                "`>rr criar <mensagem_id> <emoji> @cargo`\n"
                "`>rr listar`\n"
                "`>rr remover <mensagem_id> <emoji>`\n"
                "`>rr limpar <mensagem_id>`"
            ),
            color=discord.Color.blurple()))

    @rr.command(name="criar", aliases=["add"])
    @commands.has_permissions(manage_roles=True)
    async def rr_add(self, ctx, message_id: int, emoji: str,
                      role: discord.Role):
        """Adiciona uma reação → cargo."""
        if role >= ctx.guild.me.top_role:
            return await ctx.send(embed=embed_error(
                "❌ Cargo acima do meu."))
        try:
            db = get_connection()
            # Adiciona/atualiza mapping.
            db["embed_reaction_roles"].update_one(
                {"guild_id": ctx.guild.id, "message_id": message_id},
                {"$addToSet": {"mappings": {
                    "emoji": emoji,
                    "role_id": role.id}}},
                upsert=True)
            # Adiciona a reação na mensagem (se o bot puder).
            try:
                msg = await ctx.channel.fetch_message(message_id)
                await msg.add_reaction(emoji)
            except Exception:
                pass
            await ctx.send(embed=embed_success(
                f"✅ Reação {emoji} → {role.mention} configurada em "
                f"`{message_id}`."))
        except Exception as e:
            await ctx.send(embed=embed_error(f"❌ Erro: {e}"))

    @rr.command(name="remover", aliases=["del", "rm"])
    @commands.has_permissions(manage_roles=True)
    async def rr_remove(self, ctx, message_id: int, emoji: str):
        """Remove uma reação → cargo."""
        db = get_connection()
        config = db["embed_reaction_roles"].find_one({
            "guild_id": ctx.guild.id, "message_id": message_id})
        if not config:
            return await ctx.send(embed=embed_error("❌ Config não existe."))
        mappings = [m for m in config.get("mappings", [])
                     if str(m.get("emoji")) != emoji]
        db["embed_reaction_roles"].update_one(
            {"_id": config["_id"]},
            {"$set": {"mappings": mappings}})
        await ctx.send(embed=embed_success(f"🗑️ `{emoji}` removido."))

    @rr.command(name="listar", aliases=["list", "ls"])
    @commands.has_permissions(manage_roles=True)
    async def rr_list(self, ctx):
        """Lista reaction roles configurados."""
        db = get_connection()
        docs = list(db["embed_reaction_roles"].find(
            {"guild_id": ctx.guild.id}))
        if not docs:
            return await ctx.send(embed=embed_info("Nenhum configurado."))
        embed = discord.Embed(title="😀 Reaction Roles",
                                color=discord.Color.blurple())
        for d in docs[:10]:
            mappings = d.get("mappings", [])
            linhas = [f"• `{m.get('emoji')}` → <@&{m.get('role_id')}>"
                       for m in mappings[:10]]
            embed.add_field(name=f"📝 Msg `{d['message_id']}`",
                             value="\n".join(linhas) or "*vazio*",
                             inline=False)
        await ctx.send(embed=embed)

    @rr.command(name="limpar", aliases=["clear"])
    @commands.has_permissions(manage_roles=True)
    async def rr_clear(self, ctx, message_id: int):
        """Remove toda config de uma mensagem."""
        db = get_connection()
        r = db["embed_reaction_roles"].delete_one({
            "guild_id": ctx.guild.id, "message_id": message_id})
        if r.deleted_count:
            await ctx.send(embed=embed_success("🗑️ Config removida."))
        else:
            await ctx.send(embed=embed_error("❌ Não achei config."))


# ============================================================
# SETUP
# ============================================================

async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("EmbedActionsCommands") is None:
        await bot.add_cog(EmbedActionsCommands(bot))
    if bot.get_cog("ReactionRoleCommands") is None:
        await bot.add_cog(ReactionRoleCommands(bot))