# ============================================================
# COMMANDS_LOGS.PY - v9.0
# SISTEMA DE LOGS CUSTOMIZÁVEIS + CONTEXTUAIS + PROFISSIONAIS
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Sistema de logs unificado. Centraliza onde cada tipo de log
# vai. Categorias contextuais:
#   - mod, voice, channel, role, member, message,
#     automod, antinuke, automod_extra
#
# Cada categoria pode ter seu próprio canal. E cada tipo de log
# tem texto (título, descrição, cor) customizável.
#
# v9.0:
# - Design profissional (avatar à direita, texto natural)
# - Executor sempre visível
# - Footer consistente
# - Campos organizados
# - Mesmos comandos de antes (compatível)
# ============================================================

import discord
from discord.ext import commands
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from database import get_connection_async
from embeds import embed_success, embed_error, embed_warning, embed_info
from embeds_professional import ProfessionalLog, fmt_reason
from utils import TTLCache


def _now() -> datetime:
    """Hora atual em UTC."""
    return datetime.now(timezone.utc)


# ============================================================
# CONFIGURAÇÃO PADRÃO
# ============================================================
# Inclui os textos padrão de cada tipo de log.

DEFAULT_LOG_CONFIG = {
    "guild_id": 0,
    "enabled": True,
    "channel_id": 0,               # canal padrão (fallback)
    "context_channels": {},         # {categoria: canal_id}
    "color": 0xDC143C,             # cor padrão dos embeds
    "thumbnail_url": "",
    "footer_text": "Stumble Hit AntiRaid • Proteção Ativa",
    "footer_icon": "",
    "author_name": "STUMBLE HIT ANTIRAID",
    "author_icon": "",
    # Textos por tipo de log.
    "texts": {
        "raid_detected": {
            "title": "Raid Detectada",
            "description": ("Uma tentativa de raid foi interceptada "
                             "com sucesso."),
            "color": 0x8B0000,
        },
        "raid_blocked": {
            "title": "Raid Bloqueada",
            "description": ("O sistema interceptou e bloqueou uma "
                             "tentativa de raid."),
            "color": 0xDC143C,
        },
        "bot_malicious": {
            "title": "Bot Malicioso Banido",
            "description": ("Um bot não autorizado executou ações "
                             "destrutivas e foi banido."),
            "color": 0x8B0000,
        },
        "bot_blocked": {
            "title": "Bot Bloqueado",
            "description": ("Uma tentativa de adicionar um bot não "
                             "autorizado foi bloqueada."),
            "color": 0xFF4500,
        },
        "nuke_attempt": {
            "title": "Tentativa de Nuke",
            "description": ("Uma tentativa de nuke foi neutralizada "
                             "pelo sistema de proteção."),
            "color": 0x8B0000,
        },
        "privilege_escalation": {
            "title": "Escalada de Privilégios",
            "description": ("Uma tentativa de escalar privilégios foi "
                             "bloqueada pelo sistema."),
            "color": 0xFF8C00,
        },
        "canary_triggered": {
            "title": "Canary Nuke Disparado",
            "description": ("Um gatilho manual foi acionado durante "
                             "uma raid."),
            "color": 0x8B0000,
        },
        "user_punished": {
            "title": "Usuário Punido",
            "description": ("Um usuário foi punido pelo sistema de "
                             "proteção."),
            "color": 0xFFA500,
        },
        "automod_action": {
            "title": "AutoMod",
            "description": ("Uma ação do automod foi executada."),
            "color": 0x00BFFF,
        },
        "default": {
            "title": "🛡️ Log do Sistema",
            "description": "{details}",
            "color": 0xDC143C,
        },
    },
}


# Lista de categorias contextuais possíveis.
LOG_CATEGORIES = [
    "mod", "voice", "channel", "role", "member",
    "message", "automod", "antinuke", "automod_extra",
]


# ============================================================
# MANAGER
# ============================================================

class LogsManager:
    _cache = TTLCache(max_size=200, ttl=60)

    @classmethod
    async def get_config(cls, guild_id: int) -> dict:
        """Pega config (cache OU banco). Faz merge dos textos."""
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached
        # Motor executa a leitura sem bloquear o event loop do bot.
        db = await get_connection_async()
        doc = await db["logs_config"].find_one({"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_LOG_CONFIG, **doc}

        # Mescla textos padrão com os salvos (o salvo sobrescreve).
        texts = dict(DEFAULT_LOG_CONFIG["texts"])
        user_texts = doc.get("texts", {})
        for k, v in user_texts.items():
            if k in texts:
                texts[k] = {**texts[k], **v}
            else:
                texts[k] = v
        cfg["texts"] = texts

        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def update(cls, guild_id: int, key: str, value):
        """Salva uma config."""
        db = await get_connection_async()
        # Aguarda a gravação antes de invalidar o cache: assim, uma próxima
        # leitura não recarrega do banco um estado anterior à atualização.
        await db["logs_config"].update_one(
            {"guild_id": guild_id}, {"$set": {key: value}}, upsert=True)
        cls._cache.invalidate(guild_id)

    @classmethod
    async def update_text(cls, guild_id: int, text_key: str,
                     field: str, value: str):
        """Atualiza um campo específico de um texto (ex: title, desc, color)."""
        db = await get_connection_async()
        # A notação com ponto atualiza somente o campo solicitado,
        # preservando os demais textos personalizados do servidor.
        await db["logs_config"].update_one(
            {"guild_id": guild_id},
            {"$set": {f"texts.{text_key}.{field}": value}},
            upsert=True)
        cls._cache.invalidate(guild_id)

    @classmethod
    def invalidate(cls, guild_id: int):
        cls._cache.invalidate(guild_id)


# ============================================================
# LOGGER CUSTOMIZADO
# ============================================================
# Essa classe é usada por outros cogs pra enviar logs.

class CustomLogger:
    """Logger profissional com design consistente."""

    def __init__(self, bot):
        self.bot = bot

    async def log(self, guild: discord.Guild, log_type: str,
                   details: Dict[str, Any] = None,
                   target: Optional[discord.Member] = None,
                   executor: Optional[discord.Member] = None,
                   severity: str = "medium",
                   action_type: str = "",
                   category: str = None) -> bool:
        """
        Envia um log.
        - Se `category` for passada, usa o canal dessa categoria.
        - Senão, usa o canal padrão (ou do anti-nuke).
        """
        cfg = await LogsManager.get_config(guild.id)
        if not cfg.get("enabled"):
            return False

        # Decide o canal.
        channel_id = None
        if category:
            ctx_channels = cfg.get("context_channels", {})
            channel_id = ctx_channels.get(category)
        if not channel_id:
            channel_id = cfg.get("channel_id")
        # Fallback: canal do anti-nuke.
        if not channel_id:
            try:
                from commands_antinuke import AntiNukeManager
                an_cfg = AntiNukeManager.get_config(guild.id)
                channel_id = an_cfg.get("log_channel")
            except Exception:
                pass
        if not channel_id:
            return False

        channel = guild.get_channel(int(channel_id))
        if not channel:
            return False

        # Monta o embed.
        embed = self._build_embed(cfg, log_type, details, target,
                                    executor, severity)
        if embed is None:
            return False

        try:
            await channel.send(embed=embed)
            return True
        except Exception:
            return False

    def _build_embed(self, cfg: dict, log_type: str,
                     details: Dict[str, Any],
                     target: Optional[discord.Member],
                     executor: Optional[discord.Member],
                     severity: str) -> Optional[discord.Embed]:
        """Monta o embed com base no tipo de log."""
        # Busca o texto configurado (ou o padrão).
        texts = cfg.get("texts", {})
        text_cfg = texts.get(log_type) or texts.get("default")
        if not text_cfg:
            text_cfg = {
                "title": "🛡️ Log do Sistema",
                "description": "{details}",
                "color": cfg.get("color", 0xDC143C),
            }

        # Pega título, descrição e detalhes.
        title = text_cfg.get("title", "🛡️ Log do Sistema")
        description_tpl = text_cfg.get("description", "")
        details_dict = details or {}

        # Substitui variáveis na descrição.
        description = description_tpl
        details_block = self._format_details(details_dict)
        description = description.replace("{details}", details_block)
        description = description.replace(
            "{target}", str(target) if target else "—")
        for key, value in details_dict.items():
            description = description.replace(f"{{{key}}}", str(value))

        # Cria o log profissional.
        log = ProfessionalLog(
            title=title,
            description=description[:4000],
            severity=severity,
            footer=cfg.get("footer_text", "🛡️ Stumble Hit AntiRaid"))

        if target:
            log.set_target(target)
        if executor:
            log.set_executor(executor)

        # Monta o embed base.
        embed = log.build()

        # Aplica cor customizada se tiver.
        custom_color = text_cfg.get("color")
        if custom_color is not None:
            try:
                embed.color = discord.Color(int(custom_color))
            except Exception:
                pass

        # Thumbnail customizada.
        thumb = cfg.get("thumbnail_url")
        if thumb:
            try:
                embed.set_thumbnail(url=thumb)
            except Exception:
                pass

        # Autor customizado.
        author_name = cfg.get("author_name")
        author_icon = cfg.get("author_icon")
        if author_name:
            try:
                if author_icon:
                    embed.set_author(name=author_name, icon_url=author_icon)
                else:
                    embed.set_author(name=author_name)
            except Exception:
                embed.set_author(name=author_name)

        # Fields estruturados.
        fields = details_dict.get("fields", [])
        if isinstance(fields, list):
            for f in fields[:20]:
                if isinstance(f, dict) and "name" in f and "value" in f:
                    try:
                        embed.add_field(
                            name=str(f["name"])[:256],
                            value=str(f["value"])[:1024],
                            inline=bool(f.get("inline", True)))
                    except Exception:
                        pass

        # Footer custom.
        footer_text = cfg.get("footer_text", "🛡️ Stumble Hit AntiRaid")
        footer_icon = cfg.get("footer_icon")
        try:
            if footer_icon:
                embed.set_footer(text=footer_text, icon_url=footer_icon)
            else:
                embed.set_footer(text=footer_text)
        except Exception:
            embed.set_footer(text=footer_text)

        return embed

    def _format_details(self, details: dict) -> str:
        """Formata os detalhes em texto legível."""
        if not details:
            return ""

        lines = []
        # Ordem de prioridade pra exibição.
        priority_order = [
            "author", "target", "executor", "bot", "action",
            "reason", "count", "severity", "duration",
        ]
        displayed = set()

        # Primeiro, os prioritários.
        for key in priority_order:
            if key in details and key != "fields":
                value = details[key]
                # Converte objetos Discord em menção.
                if isinstance(value, discord.Member):
                    value = f"{value.mention} (`{value.id}`)"
                elif isinstance(value, discord.User):
                    value = f"{value.mention} (`{value.id}`)"
                elif isinstance(value, discord.Role):
                    value = value.mention
                elif isinstance(value, discord.abc.GuildChannel):
                    value = value.mention
                lines.append(f"**{key.title()}:** {value}")
                displayed.add(key)

        # Depois, os demais.
        for key, value in details.items():
            if key in displayed or key == "fields":
                continue
            if isinstance(value, discord.Member):
                value = f"{value.mention} (`{value.id}`)"
            elif isinstance(value, discord.User):
                value = f"{value.mention} (`{value.id}`)"
            elif isinstance(value, discord.Role):
                value = value.mention
            elif isinstance(value, discord.abc.GuildChannel):
                value = value.mention
            lines.append(f"**{key.title()}:** {value}")

        return "\n".join(lines)[:3500]


# ============================================================
# COG
# ============================================================

class LogsCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.logger = CustomLogger(bot)

    # ============================================================
    # COMANDO PRINCIPAL: >logs
    # ============================================================

    @commands.command(name="logs", aliases=["logsystem", "logcfg"])
    @commands.has_permissions(administrator=True)
    async def logs(self, ctx, feature: str = None, state: str = None, *,
                    extra: str = None):
        """Comando principal. Sem arg = painel."""
        cfg = await LogsManager.get_config(ctx.guild.id)

        # Sem arg → painel.
        if not feature:
            return await self._show_panel(ctx, cfg)
        # on/off.
        if feature == "on":
            await LogsManager.update(ctx.guild.id, "enabled", True)
            return await ctx.send(embed=embed_success(
                "✅ Sistema de logs **ativado**!"))
        if feature == "off":
            await LogsManager.update(ctx.guild.id, "enabled", False)
            return await ctx.send(embed=embed_success(
                "✅ Sistema de logs **desativado**!"))

        # canal: define o canal padrão.
        if feature in ("canal", "channel"):
            if not state or not state.startswith("<#"):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `>logs canal #canal`"))
            ch_id = int(state[2:-1])
            await LogsManager.update(ctx.guild.id, "channel_id", ch_id)
            return await ctx.send(embed=embed_success(
                f"✅ Logs serão enviados em <#{ch_id}>"))

        # cor: define a cor padrão.
        if feature in ("cor", "color"):
            if not state:
                return await ctx.send(embed=embed_error(
                    "❌ Use: `>logs cor #ff0000`"))
            color = state.lstrip("#")
            try:
                color_int = int(color, 16)
            except ValueError:
                return await ctx.send(embed=embed_error("❌ Cor inválida."))
            await LogsManager.update(ctx.guild.id, "color", color_int)
            return await ctx.send(embed=embed_success(
                f"✅ Cor definida: `#{color_int:06x}`"))

        # thumbnail: URL da imagem.
        if feature in ("thumbnail", "thumb", "icone"):
            if not state or not state.startswith("http"):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `>logs thumbnail <URL>`"))
            await LogsManager.update(ctx.guild.id, "thumbnail_url", state)
            return await ctx.send(embed=embed_success(
                "✅ Thumbnail definida!"))

        # thumbnailremove: remove thumbnail.
        if feature in ("thumbnailremove", "thumbremove"):
            await LogsManager.update(ctx.guild.id, "thumbnail_url", "")
            return await ctx.send(embed=embed_success(
                "✅ Thumbnail removida."))

        # footer: texto do footer.
        if feature == "footer":
            if not state:
                return await ctx.send(embed=embed_error(
                    "❌ Use: `>logs footer <texto>`"))
            await LogsManager.update(ctx.guild.id, "footer_text", state)
            return await ctx.send(embed=embed_success(
                f"✅ Footer: **{state}**"))

        # footericon: ícone do footer.
        if feature == "footericon":
            if not state or not state.startswith("http"):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `>logs footericon <URL>`"))
            await LogsManager.update(ctx.guild.id, "footer_icon", state)
            return await ctx.send(embed=embed_success(
                "✅ Ícone do footer definido!"))

        # author: nome do autor.
        if feature == "author":
            if not state:
                return await ctx.send(embed=embed_error(
                    "❌ Use: `>logs author <texto>`"))
            await LogsManager.update(ctx.guild.id, "author_name", state)
            return await ctx.send(embed=embed_success(
                f"✅ Autor: **{state}**"))

        # authoricon: ícone do autor.
        if feature == "authoricon":
            if not state or not state.startswith("http"):
                return await ctx.send(embed=embed_error(
                    "❌ Use: `>logs authoricon <URL>`"))
            await LogsManager.update(ctx.guild.id, "author_icon", state)
            return await ctx.send(embed=embed_success(
                "✅ Ícone do autor definido!"))

        # texto: edita um texto específico.
        if feature in ("texto", "text"):
            return await self._handle_text_edit(ctx, cfg, state, extra)

        # textos: lista todos os textos.
        if feature in ("textos", "texts"):
            return await self._show_texts(ctx, cfg)

        # reset: reseta tudo.
        if feature == "reset":
            db = await get_connection_async()
            # O reset só confirma ao usuário depois que a exclusão terminar.
            await db["logs_config"].delete_one(
                {"guild_id": ctx.guild.id})
            LogsManager.invalidate(ctx.guild.id)
            return await ctx.send(embed=embed_warning(
                "⚠️ Configuração de logs resetada!"))

        # test: envia um log de teste.
        if feature in ("teste", "test"):
            return await self._send_test_log(ctx)

        # context: gerencia logs contextuais.
        if feature in ("context", "contexto", "logctx"):
            return await self._handle_context(ctx, cfg, state, extra)

        # Nada bateu.
        return await ctx.send(embed=embed_error(
            "❌ Feature inválida. Use `>logs` para ver o painel."))

    # ============================================================
    # PAINEL
    # ============================================================

    async def _show_panel(self, ctx, cfg: dict):
        """Mostra o painel principal do >logs."""
        # Função pra ✅/❌.
        def onoff(v):
            return "✅" if v else "❌"

        channel_id = cfg.get("channel_id")
        channel_mention = (f"<#{channel_id}>" if channel_id
                            else "❌ Não configurado")
        ctx_channels = cfg.get("context_channels", {})

        embed = discord.Embed(
            title="📢 SISTEMA DE LOGS v9.0",
            description=(
                f"**Status:** {onoff(cfg['enabled'])}\n"
                f"**Canal padrão:** {channel_mention}\n"
                f"**Cor:** `#{cfg.get('color', 0xDC143C):06x}`\n"
                f"**Thumbnail:** "
                f"{'✅' if cfg.get('thumbnail_url') else '❌'}\n"
                f"**Autor:** `{cfg.get('author_name', '—')}`\n"
                f"**Footer:** `{cfg.get('footer_text', '—')}`\n"
                f"**Canais contextuais:** `{len(ctx_channels)}`"
            ),
            color=discord.Color(int(cfg.get('color', 0xDC143C))),
            timestamp=_now())
        if ctx.guild.icon:
            embed.set_thumbnail(url=ctx.guild.icon.url)

        embed.add_field(
            name="📌 Comandos",
            value=(
                "`>logs canal #canal` — canal padrão\n"
                "`>logs cor #ff0000` — cor\n"
                "`>logs thumbnail <URL>` — imagem\n"
                "`>logs footer <texto>` — rodapé\n"
                "`>logs author <texto>` — autor\n"
                "`>logs texts` — ver textos\n"
                "`>logs text <tipo> title <novo>`\n"
                "`>logs context` — logs contextuais\n"
                "`>logs context <cat> #canal`\n"
                "`>logs test` — teste\n"
                "`>logs reset`"
            ),
            inline=False)
        embed.add_field(
            name="🎯 Tipos de log",
            value=", ".join(f"`{k}`" for k in cfg["texts"].keys()),
            inline=False)
        embed.add_field(
            name="🏷️ Categorias contextuais",
            value=", ".join(f"`{c}`" for c in LOG_CATEGORIES),
            inline=False)

        await ctx.send(embed=embed)

    # ============================================================
    # EDICAO DE TEXTOS
    # ============================================================

    async def _handle_text_edit(self, ctx, cfg: dict, state: str,
                                  extra: str):
        """Edita um texto específico (título/descrição/cor)."""
        if not state:
            return await ctx.send(embed=embed_error(
                "❌ Use: `>logs text <tipo> title/desc/color <valor>`"))

        text_key = state.lower()
        if text_key not in cfg["texts"]:
            return await ctx.send(embed=embed_error(
                f"❌ Tipo `{text_key}` não existe. Use `>logs texts`"))
        if not extra:
            return await ctx.send(embed=embed_error(
                "❌ Use: `>logs text <tipo> title/desc/color <valor>`"))

        parts = extra.split(maxsplit=1)
        if len(parts) < 2:
            return await ctx.send(embed=embed_error(
                "❌ Use: `>logs text <tipo> title/desc/color <valor>`"))

        field, value = parts[0].lower(), parts[1]

        # Título.
        if field in ("title", "titulo"):
            await LogsManager.update_text(ctx.guild.id, text_key, "title", value)
            return await ctx.send(embed=embed_success(
                f"✅ Título de `{text_key}` atualizado!"))

        # Descrição.
        if field in ("desc", "description", "descricao"):
            await LogsManager.update_text(ctx.guild.id, text_key,
                                     "description", value)
            return await ctx.send(embed=embed_success(
                f"✅ Descrição de `{text_key}` atualizada!"))

        # Cor.
        if field in ("color", "cor"):
            color = value.lstrip("#")
            try:
                color_int = int(color, 16)
            except ValueError:
                return await ctx.send(embed=embed_error("❌ Cor inválida."))
            await LogsManager.update_text(ctx.guild.id, text_key, "color",
                                     color_int)
            return await ctx.send(embed=embed_success(
                f"✅ Cor de `{text_key}` atualizada!"))

        return await ctx.send(embed=embed_error(
            "❌ Campo inválido. Use: `title`, `desc` ou `color`"))

    async def _show_texts(self, ctx, cfg: dict):
        """Lista todos os textos configurados."""
        embed = discord.Embed(
            title="📝 TEXTOS DE LOGS",
            description=(
                "Edite com:\n"
                "`>logs text <tipo> title <novo>`\n"
                "`>logs text <tipo> desc <novo>`\n"
                "`>logs text <tipo> color <hex>`"
            ),
            color=discord.Color(int(cfg.get('color', 0xDC143C))),
            timestamp=_now())

        for key, text in list(cfg["texts"].items())[:15]:
            title = text.get("title", "—")
            desc = text.get("description", "—")
            # Corta descrição se for muito longa.
            if len(desc) > 100:
                desc = desc[:100] + "..."
            embed.add_field(
                name=f"`{key}` — {title}",
                value=f"*{desc}*",
                inline=False)

        await ctx.send(embed=embed)

    # ============================================================
    # LOGS CONTEXTUAIS
    # ============================================================

    async def _handle_context(self, ctx, cfg: dict, category: str,
                                extra: str):
        """Gerencia logs contextuais (por categoria)."""
        ctx_channels = cfg.get("context_channels", {})

        # Sem categoria → mostra painel.
        if not category:
            embed = discord.Embed(
                title="📢 LOGS CONTEXTUAIS",
                description=(
                    "Cada categoria pode ter seu próprio canal.\n\n"
                    "**Como usar:**\n"
                    "`>logs context <categoria> #canal`\n"
                    "`>logs context <categoria> remove`\n\n"
                    "**Categorias disponíveis:**"
                ),
                color=discord.Color.blurple(),
                timestamp=_now())
            for cat in LOG_CATEGORIES:
                ch_id = ctx_channels.get(cat)
                status = f"<#{ch_id}>" if ch_id else "*não configurado*"
                embed.add_field(name=f"`{cat}`", value=status, inline=True)
            return await ctx.send(embed=embed)

        # Categoria inválida.
        if category not in LOG_CATEGORIES:
            return await ctx.send(embed=embed_error(
                f"❌ Categoria inválida.\n"
                f"Use: {', '.join(f'`{c}`' for c in LOG_CATEGORIES)}"))

        # Sem extra OU "remove" → remove a categoria.
        if not extra or extra.lower() == "remove":
            db = await get_connection_async()
            # Remove apenas a categoria escolhida, sem apagar outros canais.
            await db["logs_config"].update_one(
                {"guild_id": ctx.guild.id},
                {"$unset": {f"context_channels.{category}": ""}},
                upsert=True)
            LogsManager.invalidate(ctx.guild.id)
            return await ctx.send(embed=embed_success(
                f"✅ Categoria `{category}` removida."))

        # Deve ser um canal.
        if not extra.startswith("<#") or not extra.endswith(">"):
            return await ctx.send(embed=embed_error(
                "❌ Use: `>logs context <categoria> #canal`"))

        ch_id = int(extra[2:-1])
        db = await get_connection_async()
        # Persiste o canal contextual antes de limpar o cache desta guilda.
        await db["logs_config"].update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {f"context_channels.{category}": ch_id}},
            upsert=True)
        LogsManager.invalidate(ctx.guild.id)

        return await ctx.send(embed=embed_success(
            f"✅ Categoria `{category}` → <#{ch_id}>"))

    # ============================================================
    # TESTE
    # ============================================================

    async def _send_test_log(self, ctx):
        """Envia um log de teste pra ver se tá funcionando."""
        await self.logger.log(
            ctx.guild,
            "raid_blocked",
            details={
                "target": ctx.author,
                "action": "teste_manual",
                "reason": "Este é um log de teste do sistema.",
                "count": 1,
                "fields": [
                    {"name": "📊 Teste",
                     "value": "O sistema está funcionando!",
                     "inline": True},
                    {"name": "🎯 Alvo",
                     "value": ctx.author.mention,
                     "inline": True},
                ],
            },
            target=ctx.author,
            executor=ctx.author,
            severity="low",
            action_type="test")
        await ctx.send(embed=embed_success("✅ Log de teste enviado!"))


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("LogsCommands") is None:
        await bot.add_cog(LogsCommands(bot))