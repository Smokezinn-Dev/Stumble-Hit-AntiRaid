# ============================================================
# COMMANDS_AUTOMOD.PY
# FILTROS AUTOMÁTICOS
# ============================================================
# 📌 O QUE ESSE ARQUIVO FAZ?
# Filtra mensagens automaticamente ANTES dos moderadores verem.
# Bloqueia spam, links, convites, CAPS LOCK, flood, etc.
# É um "pré-moderador" que trabalha 24/7.
#
# Cada filtro pode ser ligado/desligado individualmente, e a
# punição (warn/timeout/kick/ban) é configurável.
# ============================================================

# discord = biblioteca principal
import discord
# commands = criar comandos com prefixo e cogs
from discord.ext import commands
# app_commands = criar slash commands (/comando)
from discord import app_commands

# re = regex (buscar padrões em texto)
import re
# time = tempo
import time
# datetime = datas
from datetime import datetime, timedelta, timezone
# typing = tipos (só documentação)
from typing import Optional, Dict
# defaultdict/deque = dicionário com padrão + fila rápida
from collections import defaultdict, deque

# database = conexão MongoDB
from config import AUTOMOD_ENABLED
from database import get_connection_async
# embeds = embeds coloridos prontos
from embeds import embed_success, embed_error, embed_warning, embed_info
# TTLCache = cache temporário
from utils import TTLCache
from protection_safety import automatic_action_allowed

# ─── Regex (padrões de busca) ───
# Detecta links de convite do Discord.
INVITE_REGEX = re.compile(r"(discord\.(gg|io|me|li)|discordapp\.com/invite)/[a-zA-Z0-9]+", re.IGNORECASE)
# Detecta qualquer URL http/https.
URL_REGEX = re.compile(r"https?://\S+", re.IGNORECASE)
# Detecta caracteres "zalgo" (diacríticos empilhados).
ZALGO_REGEX = re.compile(r"[\u0300-\u036f\u0489]")


# ============================================================
# CONFIGURAÇÃO PADRÃO
# ============================================================

DEFAULT_CONFIG = {
    "enabled": False,                    # sistema desligado por padrão
    "antispam": False,                   # anti-flood
    "antispam_count": 5,                 # N mensagens em X segundos = spam
    "antispam_seconds": 5,
    "antilink": False,                   # bloquear links
    "antiinvite": False,                 # bloquear convites
    "anticaps": False,                   # bloquear CAPS LOCK
    "anticaps_percent": 70,              # % de maiúsculas
    "anticaps_min_length": 10,           # mínimo de letras
    "antimention": False,                # bloquear spam de menções
    "antimention_limit": 5,              # N menções = spam
    "antizalgo": False,                  # bloquear zalgo
    "antiduplicate": False,              # bloquear msg repetidas
    "antiduplicate_count": 3,            # N repetições = spam
    "punishment": "timeout",             # warn/timeout/kick/ban
    "timeout_minutes": 10,               # duração do timeout
    "whitelist_channels": [],            # canais isentos
    "whitelist_roles": [],               # cargos isentos
    "log_channel": 0,                    # canal de log
}


# ============================================================
# MANAGER
# ============================================================

class AutomodManager:
    _cache = TTLCache(max_size=200, ttl=60)

    @classmethod
    async def get_config(cls, guild_id: int) -> dict:
        """Pega config (cache OU banco)."""
        cached = cls._cache.get(guild_id)
        if cached is not None:
            return cached
        db = await get_connection_async()
        doc = await db["automod_config"].find_one({"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def get_config_fresh(cls, guild_id: int) -> dict:
        """Lê a configuração diretamente do Mongo antes de qualquer punição."""
        db = await get_connection_async()
        doc = await db["automod_config"].find_one({"guild_id": guild_id}) or {}
        cfg = {**DEFAULT_CONFIG, **doc}
        cls._cache.set(guild_id, cfg)
        return cfg

    @classmethod
    async def update(cls, guild_id: int, key: str, value):
        """Salva uma config e invalida o cache associado à guild."""
        db = await get_connection_async()
        await db["automod_config"].update_one(
            {"guild_id": guild_id}, {"$set": {key: value}}, upsert=True)
        cls._cache.invalidate(guild_id)

    @classmethod
    def is_whitelisted(cls, cfg: dict, channel_id: int, role_ids: list) -> bool:
        """Diz se o canal ou algum cargo está isento do automod."""
        if channel_id in cfg.get("whitelist_channels", []):
            return True
        wl_roles = set(cfg.get("whitelist_roles", []))
        return any(r in wl_roles for r in role_ids)


# ============================================================
# COG
# ============================================================

class AutomodCommands(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Guarda timestamps de mensagens (pra detectar spam).
        self._msg_times: Dict[int, Dict[int, deque]] = defaultdict(lambda: defaultdict(lambda: deque(maxlen=10)))
        # Guarda conteúdo de mensagens (pra detectar repetidas).
        self._msg_content: Dict[int, Dict[int, deque]] = defaultdict(lambda: defaultdict(lambda: deque(maxlen=5)))

    def _punish_emoji(self, p: str) -> str:
        """Devolve um emoji pra cada tipo de punição."""
        return {"warn": "⚠️", "timeout": "⏰", "kick": "👢", "ban": "🔨"}.get(p, "⚠️")

    async def _apply_punishment(self, message: discord.Message, reason: str,
                                cfg: dict, feature_key: str = None):
        """Revalida a proteção e só então registra/aplica a ação configurada.

        A leitura fresca evita que uma mensagem detectada com cache antigo
        seja punida depois que a staff desligar a proteção no painel.
        Falha de banco é tratada como fail-closed: nenhuma ação automática.
        """
        member = message.author
        if not message.guild or getattr(member, "bot", False):
            return
        try:
            cfg = await AutomodManager.get_config_fresh(message.guild.id)
        except Exception:
            return

        if not cfg.get("enabled", False):
            return
        if not cfg.get("user_protection_enabled", True):
            return
        if not await automatic_action_allowed(message.guild.id, member):
            return
        if feature_key and not cfg.get(feature_key, False):
            return
        # Revalida imunidades e whitelist com a configuração atual.
        try:
            if member.guild_permissions.manage_messages:
                return
            if AutomodManager.is_whitelisted(
                    cfg, message.channel.id, [r.id for r in member.roles]):
                return
        except Exception:
            return

        punishment = cfg.get("punishment", "timeout")
        timeout_min = int(cfg.get("timeout_minutes", 10))

        # 1. Deleta a mensagem infratora.
        try:
            await message.delete()
        except Exception:
            pass

        # 2. Salva no banco (histórico).
        db = await get_connection_async()
        await db["automod_infractions"].insert_one({
            "guild_id": message.guild.id,
            "user_id": member.id,
            "reason": reason,
            "channel_id": message.channel.id,
            "message_content": message.content[:500],
            "punishment": punishment,
            "timestamp": datetime.now(timezone.utc)
        })

        # 3. Última validação imediatamente antes da punição. Se a staff
        # desligou a proteção durante a exclusão/registro, não continua.
        if not await automatic_action_allowed(message.guild.id, member):
            return
        try:
            current_cfg = await AutomodManager.get_config_fresh(message.guild.id)
        except Exception:
            return
        if (not current_cfg.get("enabled", False)
                or not current_cfg.get("user_protection_enabled", True)
                or (feature_key and not current_cfg.get(feature_key, False))
                or AutomodManager.is_whitelisted(
                    current_cfg, message.channel.id,
                    [r.id for r in member.roles])):
            return
        cfg = current_cfg
        punishment = cfg.get("punishment", punishment)
        timeout_min = int(cfg.get("timeout_minutes", timeout_min))
        try:
            if punishment == "warn":
                # Só avisa no canal (some em 6s).
                await message.channel.send(
                    f"⚠️ {member.mention} — **{reason}**", delete_after=6)
            elif punishment == "timeout":
                # Timeout (silencia).
                until = discord.utils.utcnow() + timedelta(minutes=timeout_min)
                await member.timeout(until, reason=f"AutoMod: {reason}")
                await message.channel.send(
                    f"⏰ {member.mention} em timeout ({timeout_min}min) — **{reason}**",
                    delete_after=6)
            elif punishment == "kick":
                # Expulsa.
                await member.kick(reason=f"AutoMod: {reason}")
                await message.channel.send(
                    f"👢 {member.mention} expulso — **{reason}**", delete_after=6)
            elif punishment == "ban":
                # Bane.
                await member.ban(reason=f"AutoMod: {reason}", delete_message_days=1)
                await message.channel.send(
                    f"🔨 {member.mention} banido — **{reason}**", delete_after=6)
        except Exception:
            pass

        # 4. Envia pro canal de log (se configurado).
        log_ch = cfg.get("log_channel")
        if log_ch:
            channel = message.guild.get_channel(int(log_ch))
            if channel:
                embed = discord.Embed(
                    title="🤖 AUTOMOD",
                    description=f"**Usuário:** {member.mention} (`{member.id}`)\n**Motivo:** {reason}",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc)
                )
                embed.add_field(name="Canal", value=message.channel.mention, inline=True)
                embed.add_field(name="Punição", value=f"{self._punish_emoji(punishment)} {punishment}", inline=True)
                if message.content:
                    embed.add_field(name="Mensagem", value=message.content[:500], inline=False)
                try:
                    await channel.send(embed=embed)
                except Exception:
                    pass

    # ============================================================
    # LISTENER: ANALISA TODA MENSAGEM
    # ============================================================

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Chamado pelo Discord pra cada mensagem enviada."""
        # Ignora DMs e bots.
        if not message.guild or message.author.bot or not AUTOMOD_ENABLED:
            return
        
        cfg = await AutomodManager.get_config(message.guild.id)
        # Sistema desligado? Sai.
        if not cfg.get("enabled"):
            return
        # Quem tem "gerenciar mensagens" (staff) não é afetado.
        if message.author.guild_permissions.manage_messages:
            return
        # Canal/cargo isento? Sai.
        if AutomodManager.is_whitelisted(cfg, message.channel.id,
                                          [r.id for r in message.author.roles]):
            return

        content = message.content or ""

        # ─── Anti-invite (convites do Discord) ───
        if cfg.get("antiinvite") and INVITE_REGEX.search(content):
            return await self._apply_punishment(message, "Convite do Discord", cfg, feature_key="antiinvite")

        # ─── Anti-link (qualquer URL) ───
        if cfg.get("antilink") and URL_REGEX.search(content):
            return await self._apply_punishment(message, "Link não permitido", cfg, feature_key="antilink")

        # ─── Anti-caps (CAPS LOCK excessivo) ───
        if cfg.get("anticaps"):
            letters = [c for c in content if c.isalpha()]
            if len(letters) >= int(cfg.get("anticaps_min_length", 10)):
                upper = sum(1 for c in letters if c.isupper())
                pct = (upper / len(letters)) * 100
                if pct >= float(cfg.get("anticaps_percent", 70)):
                    return await self._apply_punishment(message, "CAPS LOCK excessivo", cfg, feature_key="anticaps")

        # ─── Anti-mention (spam de menções) ───
        if cfg.get("antimention"):
            total_mentions = len(message.mentions) + len(message.role_mentions)
            if total_mentions >= int(cfg.get("antimention_limit", 5)):
                return await self._apply_punishment(message, f"Muitas menções ({total_mentions})", cfg, feature_key="antimention")

        # ─── Anti-zalgo (texto estranho) ───
        if cfg.get("antizalgo") and ZALGO_REGEX.search(content):
            return await self._apply_punishment(message, "Zalgo / caracteres estranhos", cfg, feature_key="antizalgo")

        # ─── Anti-spam (flood) ───
        if cfg.get("antispam"):
            gid, uid = message.guild.id, message.author.id
            now = time.time()
            window = float(cfg.get("antispam_seconds", 5))
            limit = int(cfg.get("antispam_count", 5))
            dq = self._msg_times[gid][uid]
            # Remove antigas (fora da janela).
            while dq and now - dq[0] > window:
                dq.popleft()
            dq.append(now)
            # Se passou do limite, pune.
            if len(dq) >= limit:
                dq.clear()
                return await self._apply_punishment(message, "Spam / Flood", cfg, feature_key="antispam")

        # ─── Anti-duplicate (mensagens repetidas) ───
        if cfg.get("antiduplicate") and content:
            gid, uid = message.guild.id, message.author.id
            dq = self._msg_content[gid][uid]
            dq.append(content.lower().strip())
            limit = int(cfg.get("antiduplicate_count", 3))
            # Se as últimas N mensagens são iguais, é duplicata.
            if len(dq) >= limit and len(set(list(dq)[-limit:])) == 1:
                dq.clear()
                return await self._apply_punishment(message, "Mensagem repetida", cfg, feature_key="antiduplicate")

    # ============================================================
    # COMANDOS (>automod)
    # ============================================================

    @commands.command(name="automod")
    @commands.has_permissions(administrator=True)
    async def automod(self, ctx, feature: str = None, state: str = None, *, extra: str = None):
        """Comando principal. Sem arg = painel."""
        cfg = await AutomodManager.get_config(ctx.guild.id)

        # Sem arg → painel.
        if not feature:
            embed = discord.Embed(
                title="🤖 AUTOMOD",
                description=(
                    f"**Status:** {'✅ ATIVO' if cfg['enabled'] else '❌ DESATIVADO'}\n\n"
                    "**Filtros:**\n"
                    f"{'✅' if cfg['antispam'] else '❌'} `antispam`\n"
                    f"{'✅' if cfg['antilink'] else '❌'} `antilink`\n"
                    f"{'✅' if cfg['antiinvite'] else '❌'} `antiinvite`\n"
                    f"{'✅' if cfg['anticaps'] else '❌'} `anticaps`\n"
                    f"{'✅' if cfg['antimention'] else '❌'} `antimention`\n"
                    f"{'✅' if cfg['antizalgo'] else '❌'} `antizalgo`\n"
                    f"{'✅' if cfg['antiduplicate'] else '❌'} `antiduplicate`\n\n"
                    "**Punição:** " + f"`{cfg['punishment']}`\n\n"
                    "**Como usar:**\n"
                    "`>automod <feature> on/off`\n"
                    "`>automod punishment timeout`\n"
                    "`>automod log #canal`\n"
                    "`>automod whitelist #canal`\n"
                    "`>automod whitelist @cargo`\n"
                ),
                color=discord.Color.blurple(),
                timestamp=datetime.utcnow()
            )
            return await ctx.send(embed=embed)

        # on/off.
        if feature == "on":
            await AutomodManager.update(ctx.guild.id, "enabled", True)
            return await ctx.send(embed=embed_success("✅ Automod **ativado**!"))
        if feature == "off":
            await AutomodManager.update(ctx.guild.id, "enabled", False)
            return await ctx.send(embed=embed_success("✅ Automod **desativado**!"))

        # punishment.
        if feature in ("punishment", "punicao"):
            if not state or state.lower() not in ("warn", "timeout", "kick", "ban"):
                return await ctx.send(embed=embed_error("❌ Use: `warn`, `timeout`, `kick` ou `ban`"))
            await AutomodManager.update(ctx.guild.id, "punishment", state.lower())
            return await ctx.send(embed=embed_success(f"✅ Punição definida: `{state.lower()}`"))

        # log: canal.
        if feature in ("log", "logchannel"):
            if not state or not state.startswith("<#"):
                return await ctx.send(embed=embed_error("❌ Use: `>automod log #canal`"))
            ch_id = int(state[2:-1])
            await AutomodManager.update(ctx.guild.id, "log_channel", ch_id)
            return await ctx.send(embed=embed_success(f"✅ Logs em <#{ch_id}>"))

        # whitelist (canal ou cargo).
        if feature in ("whitelist", "wl"):
            if not state:
                return await ctx.send(embed=embed_error("❌ Use: `>automod whitelist #canal` ou `@cargo`"))
            # Se é canal.
            if state.startswith("<#"):
                ch_id = int(state[2:-1])
                wl = list(cfg.get("whitelist_channels", []))
                if ch_id not in wl:
                    wl.append(ch_id)
                    await AutomodManager.update(ctx.guild.id, "whitelist_channels", wl)
                return await ctx.send(embed=embed_success(f"✅ <#{ch_id}> na whitelist."))
            # Se é cargo.
            if state.startswith("<@&"):
                r_id = int(state[3:-1])
                wl = list(cfg.get("whitelist_roles", []))
                if r_id not in wl:
                    wl.append(r_id)
                    await AutomodManager.update(ctx.guild.id, "whitelist_roles", wl)
                return await ctx.send(embed=embed_success(f"✅ <@&{r_id}> na whitelist."))

        # ─── Features booleanas (on/off) ───
        valid_features = ["antispam", "antilink", "antiinvite", "anticaps",
                          "antimention", "antizalgo", "antiduplicate"]
        if feature in valid_features:
            if not state:
                return await ctx.send(embed=embed_error("❌ Use: `on` ou `off`"))
            enabled = state.lower() in ("on", "true", "1", "sim", "ativar")
            await AutomodManager.update(ctx.guild.id, feature, enabled)
            return await ctx.send(embed=embed_success(
                f"✅ `{feature}` {'ativado' if enabled else 'desativado'}"))

        # ─── Limites numéricos ───
        limits = {
            "antispam_count": int, "antispam_seconds": int,
            "anticaps_percent": int, "anticaps_min_length": int,
            "antimention_limit": int, "antiduplicate_count": int,
            "timeout_minutes": int,
        }
        if feature in limits and state:
            try:
                await AutomodManager.update(ctx.guild.id, feature, int(state))
                await ctx.send(embed=embed_success(f"✅ `{feature}` = `{state}`"))
            except Exception:
                await ctx.send(embed=embed_error("❌ Valor inválido."))
            return

        # Nada bateu.
        await ctx.send(embed=embed_error("❌ Feature inválida. Use `>automod` para ver."))

    # ============================================================
    # SLASH COMMAND (/automod)
    # ============================================================
    # Versão slash do mesmo comando. Adaptamos chamando o prefixo.

    @app_commands.command(name="automod", description="🤖 Configura o automod")
    @app_commands.default_permissions(administrator=True)
    async def automod_slash(self, interaction: discord.Interaction, feature: str, state: str):
        """Versão slash: cria um FakeCtx pra reaproveitar o comando."""
        class FakeCtx:
            def __init__(self, i):
                self.guild = i.guild
                self.author = i.user
                # Reaproveita o send do interaction.
                self.send = i.response.send_message
        await self.automod(FakeCtx(interaction), feature, state)


async def setup(bot):
    """Chamado pelo main.py."""
    if bot.get_cog("AutomodCommands") is None:
        await bot.add_cog(AutomodCommands(bot))